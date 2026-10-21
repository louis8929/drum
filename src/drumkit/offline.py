"""
Rendu hors-ligne : joue une sequence d'evenements MIDI simules a travers le
routeur + moteur audio et ecrit le resultat dans un WAV.

Sert a deux choses :
- tester toute la chaine (synthese -> routage -> polyphonie -> choke) sans
  materiel ni carte son,
- generer une demo audible.

Un evenement est un tuple :
    (temps_sec, "note", note, velocity)
    (temps_sec, "cc",   controller, value)
"""
from __future__ import annotations

import numpy as np

from .engine import AudioEngine
from .router import DrumRouter


def render(router: DrumRouter, engine: AudioEngine, events: list[tuple],
           duration_sec: float, blocksize: int = 128) -> np.ndarray:
    """Rend 'events' sur 'duration_sec' et retourne un tableau (N, channels) float32."""
    sr = engine.samplerate
    total = int(duration_sec * sr)
    out = np.zeros((total, engine.channels), dtype=np.float32)
    block = np.zeros((blocksize, engine.channels), dtype=np.float32)

    # evenements tries, convertis en position d'echantillon
    evs = sorted(((int(t * sr), kind, a, b) for (t, kind, a, b) in events),
                 key=lambda e: e[0])
    ei = 0
    pos = 0
    while pos < total:
        n = min(blocksize, total - pos)
        # injecte les evenements qui tombent dans ce bloc (avant le process)
        while ei < len(evs) and evs[ei][0] < pos + n:
            _, kind, a, b = evs[ei]
            if kind == "note":
                router.note_on(a, b)
            elif kind == "cc":
                router.control_change(a, b)
            ei += 1
        view = block[:n]
        engine.process(view)
        out[pos:pos + n] = view
        pos += n
    return out


def write_wav(path: str, data: np.ndarray, samplerate: int) -> None:
    import soundfile as sf
    sf.write(path, data, samplerate, subtype="PCM_16")


def demo_groove(bpm: float = 100.0, bars: int = 2) -> tuple[list[tuple], float]:
    """Un petit groove rock pour la demo/test. Retourne (events, duree_sec)."""
    beat = 60.0 / bpm
    step = beat / 4.0  # doubles croches
    events: list[tuple] = []
    KICK, SNARE, HHC, HHO, CRASH = 36, 38, 42, 46, 49
    for bar in range(bars):
        t0 = bar * 4 * beat
        # charley en doubles croches
        for s in range(16):
            t = t0 + s * step
            vel = 95 if s % 2 == 0 else 70
            # ouverte sur le "et" du 4e temps du dernier bar
            if bar == bars - 1 and s == 14:
                events.append((t, "note", HHO, 110))
            else:
                events.append((t, "note", HHC, vel))
        # grosse caisse 1 et 3 (+ syncope)
        events += [(t0 + 0 * beat, "note", KICK, 120),
                   (t0 + 2 * beat, "note", KICK, 118),
                   (t0 + 2.5 * beat, "note", KICK, 100)]
        # caisse claire 2 et 4
        events += [(t0 + 1 * beat, "note", SNARE, 115),
                   (t0 + 3 * beat, "note", SNARE, 118)]
        if bar == 0:
            events.append((t0, "note", CRASH, 120))
    duration = bars * 4 * beat + 1.5
    return events, duration
