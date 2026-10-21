"""
Construction de la banque de sons : nom d'instrument / fichier WAV -> buffers audio.

Tous les sons sont prechargse en RAM (float32 stereo) au demarrage.
Chaque pad possede plusieurs couches de timbre selectionnees par la velocity.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import synth
from .config import iter_pads


@dataclass
class PadSound:
    note: int
    label: str
    choke_group: str | None
    layers: list[np.ndarray] = field(default_factory=list)  # chaque: (N, 2) float32

    def pick_layer(self, velocity: int) -> np.ndarray:
        """Choisit la couche de timbre selon la velocity (1..127)."""
        if len(self.layers) == 1:
            return self.layers[0]
        v = min(max(velocity, 1), 127) / 127.0
        idx = min(len(self.layers) - 1, int(v * len(self.layers)))
        return self.layers[idx]


class SampleBank:
    def __init__(self) -> None:
        self._by_note: dict[int, PadSound] = {}

    def get(self, note: int) -> PadSound | None:
        return self._by_note.get(note)

    def add(self, pad: PadSound) -> None:
        self._by_note[pad.note] = pad

    def __len__(self) -> int:
        return len(self._by_note)

    def notes(self) -> list[int]:
        return sorted(self._by_note)


def _load_wav(path: Path, samplerate: int) -> list[np.ndarray]:
    """Charge un WAV, le convertit en stereo float32 et le reechantillonne si besoin."""
    import soundfile as sf  # import tardif : seulement si des fichiers sont utilises

    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    elif data.shape[1] > 2:
        data = data[:, :2]
    if sr != samplerate:
        # reechantillonnage lineaire simple (suffisant pour des samples courts)
        n_out = int(round(data.shape[0] * samplerate / sr))
        x_old = np.linspace(0.0, 1.0, data.shape[0], endpoint=False)
        x_new = np.linspace(0.0, 1.0, n_out, endpoint=False)
        data = np.column_stack([np.interp(x_new, x_old, data[:, c]) for c in range(2)])
    return [np.ascontiguousarray(data, dtype=np.float32)]


def build_bank(cfg: dict, n_layers: int = 3, seed: int | None = None,
               verbose: bool = False) -> SampleBank:
    """Construit la banque depuis la config. Synthetise ou charge chaque son une seule fois."""
    samplerate = cfg["audio"]["samplerate"]
    bank = SampleBank()
    cache_instr: dict[str, list[np.ndarray]] = {}
    cache_file: dict[str, list[np.ndarray]] = {}
    base_dir = Path(cfg.get("_source_path", ".")).resolve().parent

    for note, pad in iter_pads(cfg):
        label = pad.get("label", pad.get("instrument", f"note {note}"))
        choke = pad.get("choke_group")

        if "file" in pad:
            fpath = (base_dir / pad["file"]).resolve()
            key = str(fpath)
            if key not in cache_file:
                if verbose:
                    print(f"  [WAV]   {label:<24} <- {pad['file']}")
                cache_file[key] = _load_wav(fpath, samplerate)
            layers = cache_file[key]
        else:
            instr = pad["instrument"]
            if instr not in cache_instr:
                if verbose:
                    print(f"  [synth] {label:<24} ({instr})")
                cache_instr[instr] = synth.synth_layers(instr, samplerate, n_layers, seed)
            layers = cache_instr[instr]

        bank.add(PadSound(note=note, label=label, choke_group=choke, layers=layers))

    return bank
