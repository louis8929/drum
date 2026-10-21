#!/usr/bin/env python3
"""
Batterie electronique virtuelle — point d'entree.

Exemples :
  python main.py devices
  python main.py play                 # choix interactif du port MIDI
  python main.py play --simulate      # demo en boucle, sans batterie
  python main.py play --midi-port 1 --master 0.8
  python main.py learn                # mode apprentissage MIDI
  python main.py render demo.wav      # rend une demo (test sans materiel)
"""
import argparse
import sys
from pathlib import Path

# rend le paquet 'drumkit' importable quel que soit le repertoire courant
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from drumkit import app  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="drumkit",
        description="Batterie electronique virtuelle basse latence (USB-MIDI -> sons).",
    )
    p.add_argument("--config", default=None,
                   help="Chemin du fichier de mapping JSON (defaut : config/default_mapping.json).")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("devices", help="Liste les ports MIDI et sorties audio.")
    sp.set_defaults(func=app.cmd_devices)

    sp = sub.add_parser("play", help="Joue les sons en direct (MIDI ou simulation).")
    sp.add_argument("--midi-port", type=int, default=None, help="Index du port MIDI d'entree.")
    sp.add_argument("--audio-device", type=int, default=None, help="Index de la sortie audio.")
    sp.add_argument("--master", type=float, default=None, help="Volume general 0.0..1.0.")
    sp.add_argument("--simulate", action="store_true", help="Joue un groove de demo en boucle.")
    sp.add_argument("--bpm", type=float, default=100.0, help="Tempo de la simulation.")
    sp.set_defaults(func=app.cmd_play)

    sp = sub.add_parser("learn", help="Mode apprentissage : assigne un son a chaque pad.")
    sp.add_argument("--midi-port", type=int, default=None, help="Index du port MIDI d'entree.")
    sp.add_argument("--audio-device", type=int, default=None, help="Index de la sortie audio.")
    sp.add_argument("--output", default=None, help="Fichier de sortie (defaut : la config chargee).")
    sp.set_defaults(func=app.cmd_learn)

    sp = sub.add_parser("render", help="Rend une demo/sequence en WAV (test hors-ligne).")
    sp.add_argument("output", nargs="?", default="demo.wav", help="Fichier WAV de sortie.")
    sp.add_argument("--bpm", type=float, default=100.0, help="Tempo.")
    sp.add_argument("--bars", type=int, default=2, help="Nombre de mesures.")
    sp.add_argument("--verbose", action="store_true", help="Detaille le chargement des sons.")
    sp.set_defaults(func=app.cmd_render)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
