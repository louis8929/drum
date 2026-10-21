#!/usr/bin/env python3
"""
Exporte les sons synthetises en fichiers WAV (un par instrument / couche de velocity).

Utile pour : ecouter les sons isolement, les retoucher dans un editeur audio, ou
les remplacer par de vrais samples ensuite.

Usage :
  python tools/generate_samples.py                 # -> samples/synth/
  python tools/generate_samples.py --out mes_sons  # dossier de sortie
  python tools/generate_samples.py --samplerate 44100 --layers 1
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import soundfile as sf  # noqa: E402

from drumkit import synth  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Exporte les sons synthetises en WAV.")
    ap.add_argument("--out", default="samples/synth", help="Dossier de sortie.")
    ap.add_argument("--samplerate", type=int, default=48000)
    ap.add_argument("--layers", type=int, default=3, help="Nombre de couches de velocity.")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    for instr in synth.available_instruments():
        layers = synth.synth_layers(instr, args.samplerate, args.layers, seed=1)
        for i, buf in enumerate(layers):
            suffix = f"_v{i + 1}" if args.layers > 1 else ""
            path = out / f"{instr}{suffix}.wav"
            sf.write(str(path), buf, args.samplerate, subtype="PCM_16")
            print(f"  ecrit {path}")
    print(f"\nTermine : {len(synth.available_instruments())} instruments dans {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
