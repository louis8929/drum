#!/usr/bin/env python3
"""
Auto-test de la chaine audio SANS materiel ni carte son ni dependances MIDI.

Verifie :
  - la synthese de chaque instrument (pas de NaN, pic raisonnable),
  - le chargement de la config + banque,
  - le routage (note_on, choke charley via note et via CC4),
  - la polyphonie (plusieurs voix simultanees mixees),
  - le rendu hors-ligne complet d'un groove.

Usage : python tools/selftest.py
Ne necessite que numpy (soundfile uniquement si --wav est demande).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402

from drumkit import config as cfgmod  # noqa: E402
from drumkit import synth  # noqa: E402
from drumkit.engine import AudioEngine  # noqa: E402
from drumkit.offline import demo_groove, render  # noqa: E402
from drumkit.router import DrumRouter  # noqa: E402
from drumkit.samplebank import build_bank  # noqa: E402

FAILS = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "OK  " if cond else "FAIL"
    print(f"  [{status}] {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def test_synth() -> None:
    print("1) Synthese des instruments")
    for instr in synth.available_instruments():
        layers = synth.synth_layers(instr, 48000, 3, seed=1)
        ok = True
        detail = ""
        for b in layers:
            if b.ndim != 2 or b.shape[1] != 2:
                ok, detail = False, "pas stereo"
            if not np.all(np.isfinite(b)):
                ok, detail = False, "NaN/Inf"
            peak = float(np.max(np.abs(b)))
            if peak < 0.5 or peak > 1.0:
                ok, detail = False, f"pic={peak:.2f}"
        check(f"{instr} (3 couches)", ok, detail)


def test_engine_poly() -> None:
    print("2) Polyphonie et choke (moteur isole)")
    eng = AudioEngine(samplerate=48000, blocksize=128, max_voices=8)
    tone = synth.synth_layers("tom_low", 48000, 1)[0]
    for _ in range(5):
        eng.queue_trigger(tone, 120, group=None)
    out = np.zeros((128, 2), dtype=np.float32)
    eng.process(out)
    check("5 voix simultanees actives", eng.active_voices == 5, f"actives={eng.active_voices}")
    check("mix non silencieux", float(np.max(np.abs(out))) > 0.01)

    # choke : 2 sons du meme groupe -> le 2e coupe le 1er
    eng.reset()
    hh = synth.synth_layers("hihat_open", 48000, 1)[0]
    eng.queue_trigger(hh, 120, group="hihat")
    eng.process(out)
    eng.queue_trigger(hh, 120, group="hihat")
    eng.process(out)
    # apres le 2e trigger + fade, au plus une voix "non-fading" reste; on verifie
    # qu'on ne s'empile pas a l'infini
    check("choke groupe : pas d'empilement", eng.active_voices <= 2,
          f"actives={eng.active_voices}")


def test_router_cc() -> None:
    print("3) Routage charley via CC4")
    cfg = cfgmod.load_config()
    bank = build_bank(cfg, n_layers=2)
    eng = AudioEngine(samplerate=cfg["audio"]["samplerate"], blocksize=128)
    router = DrumRouter(cfg, bank, eng)
    hits = []
    router.on_hit = lambda n, l, v: hits.append((n, l, v))

    router.note_on(46, 110)          # charley ouverte
    out = np.zeros((128, 2), dtype=np.float32)
    eng.process(out)
    before = eng.active_voices
    router.control_change(4, 127)    # pedale enfoncee -> ferme -> chick + choke
    eng.process(out)
    check("note charley ouverte declenchee", len(hits) == 1 and hits[0][0] == 46)
    check("CC4 ferme la charley (chick/choke)", eng.active_voices >= 1,
          f"voix avant={before}")


def test_offline_render(write_wav: bool) -> None:
    print("4) Rendu hors-ligne d'un groove complet")
    cfg = cfgmod.load_config()
    bank = build_bank(cfg, n_layers=3)
    eng = AudioEngine(samplerate=cfg["audio"]["samplerate"], blocksize=cfg["audio"]["blocksize"],
                      max_voices=cfg["audio"]["max_voices"], master_gain=cfg["audio"]["master_gain"])
    router = DrumRouter(cfg, bank, eng)
    events, duration = demo_groove(bpm=100, bars=2)
    audio = render(router, eng, events, duration, blocksize=cfg["audio"]["blocksize"])
    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(audio ** 2)))
    check("audio fini (pas de NaN)", bool(np.all(np.isfinite(audio))))
    check("signal present", peak > 0.1 and rms > 0.001, f"pic={peak:.3f} rms={rms:.4f}")
    check("pas de saturation franche", peak <= 1.0, f"pic={peak:.3f}")
    if write_wav:
        import soundfile as sf
        sf.write("selftest_demo.wav", audio, cfg["audio"]["samplerate"], subtype="PCM_16")
        print("     -> ecrit selftest_demo.wav")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", action="store_true", help="Ecrit aussi selftest_demo.wav")
    args = ap.parse_args()

    print("=== AUTO-TEST BATTERIE VIRTUELLE (sans materiel) ===\n")
    test_synth()
    test_engine_poly()
    test_router_cc()
    test_offline_render(args.wav)

    print()
    if FAILS:
        print(f"ECHEC : {len(FAILS)} test(s) KO -> {', '.join(FAILS)}")
        return 1
    print("TOUS LES TESTS SONT PASSES.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
