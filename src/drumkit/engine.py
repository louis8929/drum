"""
Moteur audio temps reel : mixage polyphonique de samples prechargse.

Concu pour etre SANS ALLOCATION dans le chemin critique (callback audio) :
- pool de voix prealloue,
- buffer de mixage 'scratch' prealloue,
- les declenchements arrivent via une deque (append/popleft atomiques en CPython),
  donc aucun verrou n'est pris dans le callback audio.

Le moteur est independant de sounddevice : il expose process(out) qui remplit un
tableau (frames, channels). Le flux live (audio_output.py) et le rendu hors-ligne
(offline.py) l'appellent tous les deux.
"""
from __future__ import annotations

from collections import deque

import numpy as np


class _Voice:
    __slots__ = ("active", "buf", "pos", "gain", "group", "fading", "fade_len", "fade_pos")

    def __init__(self) -> None:
        self.active = False
        self.buf: np.ndarray | None = None
        self.pos = 0
        self.gain = 0.0
        self.group: str | None = None
        self.fading = False
        self.fade_len = 0
        self.fade_pos = 0


class AudioEngine:
    def __init__(self, samplerate: int, blocksize: int, channels: int = 2,
                 max_voices: int = 64, master_gain: float = 0.9,
                 velocity_curve: float = 0.8, choke_fade_ms: float = 5.0) -> None:
        self.samplerate = samplerate
        self.channels = channels
        self.max_voices = max_voices
        self.master_gain = float(master_gain)
        self.velocity_curve = float(velocity_curve)
        self.choke_fade = max(1, int(choke_fade_ms * 1e-3 * samplerate))

        self._voices = [_Voice() for _ in range(max_voices)]
        self._pending: deque = deque()          # (buf, gain, group) et ("choke", group)
        self._scratch = np.zeros((max(blocksize, 1), channels), dtype=np.float32)

        # statistiques (lecture seule depuis d'autres threads, best-effort)
        self.active_voices = 0
        self.peak = 0.0

    # ----- API appelee depuis le thread MIDI ------------------------------- #
    def gain_from_velocity(self, velocity: int) -> float:
        v = min(max(velocity, 1), 127) / 127.0
        return v ** self.velocity_curve

    def queue_trigger(self, buf: np.ndarray, velocity: int, group: str | None) -> None:
        """Declenche un son (appele depuis le callback MIDI, non bloquant)."""
        self._pending.append((buf, self.gain_from_velocity(velocity), group))

    def queue_choke(self, group: str) -> None:
        """Coupe (avec fondu) tous les sons d'un groupe (ex: fermeture charley)."""
        self._pending.append(("__choke__", group))

    # ----- chemin critique : appele depuis le thread audio ----------------- #
    def _start_choke(self, group: str) -> None:
        for v in self._voices:
            if v.active and not v.fading and v.group == group:
                v.fading = True
                v.fade_len = self.choke_fade
                v.fade_pos = 0

    def _alloc_voice(self) -> _Voice:
        # 1) une voix libre
        for v in self._voices:
            if not v.active:
                return v
        # 2) sinon vol de voix : la plus avancee dans sa lecture (la plus "vieille")
        victim = max(self._voices, key=lambda v: v.pos / (v.buf.shape[0] if v.buf is not None else 1))
        return victim

    def _drain_pending(self) -> None:
        while self._pending:
            item = self._pending.popleft()
            if isinstance(item[0], str):   # ("__choke__", group)
                self._start_choke(item[1])
                continue
            buf, gain, group = item
            if group is not None:
                self._start_choke(group)   # un nouveau son coupe les precedents du groupe
            v = self._alloc_voice()
            v.active = True
            v.buf = buf
            v.pos = 0
            v.gain = gain
            v.group = group
            v.fading = False
            v.fade_pos = 0

    def process(self, out: np.ndarray) -> None:
        """Remplit 'out' (frames, channels) float32 avec le mixage des voix actives."""
        frames = out.shape[0]
        out.fill(0.0)

        self._drain_pending()

        if self._scratch.shape[0] < frames:
            self._scratch = np.zeros((frames, self.channels), dtype=np.float32)
        scratch = self._scratch

        active = 0
        for v in self._voices:
            if not v.active or v.buf is None:
                continue
            active += 1
            n = v.buf.shape[0]
            k = min(frames, n - v.pos)
            if k <= 0:
                v.active = False
                continue
            seg = v.buf[v.pos:v.pos + k]

            if v.fading:
                start = 1.0 - v.fade_pos / v.fade_len
                end = 1.0 - min(v.fade_len, v.fade_pos + k) / v.fade_len
                ramp = np.linspace(start, end, k, endpoint=False, dtype=np.float32)[:, None]
                np.multiply(seg, ramp, out=scratch[:k])
                scratch[:k] *= v.gain
                v.fade_pos += k
            else:
                np.multiply(seg, v.gain, out=scratch[:k])

            out[:k] += scratch[:k]
            v.pos += k

            if v.pos >= n or (v.fading and v.fade_pos >= v.fade_len):
                v.active = False

        out *= self.master_gain
        np.clip(out, -1.0, 1.0, out=out)

        self.active_voices = active
        # pic du bloc (pour un eventuel vu-metre), sans allocation lourde
        if frames:
            self.peak = float(np.max(np.abs(out)))

    def reset(self) -> None:
        self._pending.clear()
        for v in self._voices:
            v.active = False
