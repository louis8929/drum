"""Chargement / sauvegarde / validation du fichier de configuration (JSON)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "default_mapping.json"

_FALLBACK_AUDIO = {
    "samplerate": 48000,
    "blocksize": 128,
    "channels": 2,
    "host_api_preference": ["ASIO", "Windows WASAPI"],
    "wasapi_exclusive": True,
    "max_voices": 64,
    "master_gain": 0.9,
}


def load_config(path: str | Path | None = None) -> dict:
    """Charge la config JSON. Si 'path' est None, charge le mapping par defaut."""
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    _validate(cfg)
    # complete les champs audio manquants
    audio = dict(_FALLBACK_AUDIO)
    audio.update(cfg.get("audio", {}))
    cfg["audio"] = audio
    cfg.setdefault("hihat", {"pedal_cc": 4, "closed_threshold": 60, "choke_group": "hihat"})
    cfg["_source_path"] = str(path)
    return cfg


def save_config(cfg: dict, path: str | Path) -> None:
    """Sauvegarde la config (sans les cles internes commencant par '_')."""
    clean = {k: v for k, v in cfg.items() if not k.startswith("_")}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(clean, f, indent=2, ensure_ascii=False)


def _validate(cfg: dict) -> None:
    if "pads" not in cfg or not isinstance(cfg["pads"], dict):
        raise ValueError("Config invalide : cle 'pads' manquante ou incorrecte.")
    for note, pad in cfg["pads"].items():
        if note.startswith("_"):
            continue
        try:
            int(note)
        except ValueError as e:
            raise ValueError(f"Note MIDI invalide dans 'pads': {note!r}") from e
        if "instrument" not in pad and "file" not in pad:
            raise ValueError(f"Pad {note} doit avoir 'instrument' ou 'file'.")


def iter_pads(cfg: dict):
    """Itere (note:int, pad:dict) en ignorant les cles de commentaire."""
    for note, pad in cfg["pads"].items():
        if note.startswith("_"):
            continue
        yield int(note), pad


def set_pad(cfg: dict, note: int, instrument: str | None = None,
            label: str | None = None, choke_group: str | None = None,
            file: str | None = None) -> None:
    """Cree/modifie l'assignation d'une note (utilise par le mode apprentissage)."""
    pad = copy.deepcopy(cfg["pads"].get(str(note), {}))
    if instrument is not None:
        pad["instrument"] = instrument
        pad.pop("file", None)
    if file is not None:
        pad["file"] = file
        pad.pop("instrument", None)
    if label is not None:
        pad["label"] = label
    if choke_group is not None:
        pad["choke_group"] = choke_group
    cfg["pads"][str(note)] = pad
