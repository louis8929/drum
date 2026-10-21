"""
Interface en ligne de commande.

Sous-commandes :
  devices            liste les ports MIDI et les sorties audio
  play               joue les sons en direct depuis la batterie (ou en simulation)
  learn              mode apprentissage : frappe un pad -> assigne un son
  render             rend une demo/sequence en WAV (test sans materiel)
"""
from __future__ import annotations

import sys
import threading
import time
from collections import deque
from pathlib import Path

from . import config as cfgmod
from . import synth
from .engine import AudioEngine
from .router import DrumRouter
from .samplebank import build_bank


# --------------------------------------------------------------------------- #
# Utilitaires d'affichage
# --------------------------------------------------------------------------- #
def _vel_bar(vel: int, width: int = 20) -> str:
    n = int(round(vel / 127 * width))
    return "█" * n + "·" * (width - n)


def _print_midi_ports() -> list[str]:
    from . import midi_input
    print(f"  (backend MIDI : {midi_input.backend_name() or 'aucun'})")
    try:
        ports = midi_input.list_input_ports()
    except Exception as e:  # noqa: BLE001
        print(f"  (impossible de lister les ports MIDI : {e})")
        return []
    if not ports:
        print("  (aucun port MIDI d'entree detecte)")
    for i, p in enumerate(ports):
        print(f"  [{i}] {p}")
    return ports


def _print_audio_devices() -> None:
    from . import audio_output
    try:
        print("\nHost APIs audio (par ordre de latence conseille) :")
        for a in audio_output.list_host_apis():
            print(f"  - {a['name']}")
        print("\nSorties audio disponibles :")
        for d in audio_output.list_output_devices():
            print(f"  [{d['index']}] {d['name']}  "
                  f"({d['hostapi']}, {d['max_output_channels']} ch, "
                  f"{int(d['default_samplerate'])} Hz)")
    except Exception as e:  # noqa: BLE001
        print(f"  (impossible de lister les sorties audio : {e})")


def _choose(prompt: str, maxidx: int, default: int | None = None) -> int | None:
    while True:
        suffix = f" [defaut {default}]" if default is not None else ""
        raw = input(f"{prompt}{suffix} : ").strip()
        if raw == "" and default is not None:
            return default
        if raw.lower() in ("q", "quit", ""):
            return None
        try:
            v = int(raw)
            if 0 <= v <= maxidx:
                return v
        except ValueError:
            pass
        print("  Entree invalide.")


# --------------------------------------------------------------------------- #
# Construction du moteur
# --------------------------------------------------------------------------- #
def _build_engine_and_bank(cfg: dict, verbose: bool = True):
    a = cfg["audio"]
    print("Chargement des sons (synthese en RAM)...")
    bank = build_bank(cfg, n_layers=3, verbose=verbose)
    engine = AudioEngine(
        samplerate=a["samplerate"],
        blocksize=a["blocksize"],
        channels=a["channels"],
        max_voices=a["max_voices"],
        master_gain=a["master_gain"],
    )
    router = DrumRouter(cfg, bank, engine)
    print(f"{len(bank)} pads charges.")
    return engine, bank, router


# --------------------------------------------------------------------------- #
# Sous-commande : devices
# --------------------------------------------------------------------------- #
def cmd_devices(args) -> int:
    print("Ports MIDI d'entree :")
    _print_midi_ports()
    _print_audio_devices()
    return 0


# --------------------------------------------------------------------------- #
# Sous-commande : play
# --------------------------------------------------------------------------- #
def cmd_play(args) -> int:
    cfg = cfgmod.load_config(args.config)
    if args.master is not None:
        cfg["audio"]["master_gain"] = args.master

    engine, bank, router = _build_engine_and_bank(cfg)

    from . import audio_output

    # --- choix de la sortie audio ---
    dev, api_name, extra = audio_output.resolve_output(
        cfg["audio"]["host_api_preference"],
        cfg["audio"]["wasapi_exclusive"],
        device_index=args.audio_device,
    )
    stream = audio_output.AudioStream(
        engine, dev, cfg["audio"]["samplerate"], cfg["audio"]["blocksize"],
        cfg["audio"]["channels"], extra_settings=extra,
    )

    # --- file d'affichage (le callback MIDI ne fait qu'empiler) ---
    hit_log: deque = deque(maxlen=200)
    router.on_hit = lambda note, label, vel: hit_log.append((time.time(), note, label, vel))
    router.on_unknown_note = lambda note, vel: hit_log.append((time.time(), note, f"?? (non assigne)", vel))

    # --- demarrage audio ---
    try:
        latency_ms = stream.start()
    except Exception as e:  # noqa: BLE001
        print(f"\nERREUR a l'ouverture de la sortie audio : {e}")
        print("Astuce : essaie une autre sortie (drumkit devices) ou desactive le mode exclusif "
              "dans la config (audio.wasapi_exclusive = false).")
        return 2

    mode = "exclusif" if getattr(stream, "exclusive", False) else "partage"
    print(f"\nSortie audio : {api_name}  (device {dev}, mode {mode})")
    print(f"Latence de sortie estimee : {latency_ms:.1f} ms  "
          f"(bloc {cfg['audio']['blocksize']} @ {cfg['audio']['samplerate']} Hz)")

    # --- source d'evenements : MIDI reel ou simulation ---
    sim_stop = threading.Event()
    midi_port = None
    if args.simulate:
        print("Mode SIMULATION : groove joue en boucle (aucune batterie requise).")
        t = threading.Thread(target=_simulate_loop, args=(router, sim_stop, args.bpm), daemon=True)
        t.start()
    else:
        from . import midi_input
        ports = midi_input.list_input_ports()
        if not ports:
            print("\nAucun port MIDI detecte. Branche ta batterie, ou relance avec --simulate.")
            stream.stop()
            return 2
        idx = args.midi_port
        if idx is None:
            print("\nPorts MIDI d'entree :")
            for i, p in enumerate(ports):
                print(f"  [{i}] {p}")
            idx = _choose("Choisis ton port MIDI", len(ports) - 1, default=0)
            if idx is None:
                stream.stop()
                return 0
        midi_port = midi_input.MidiInputPort(
            on_note_on=router.note_on,
            on_control_change=router.control_change,
            on_note_off=router.note_off,
        )
        name = midi_port.open(idx)
        print(f"Port MIDI ouvert : {name}")

    print("\n--- EN ECOUTE ---  (Ctrl+C pour quitter)\n")
    last_printed = 0.0
    try:
        while True:
            time.sleep(0.03)
            # vide le journal des frappes
            while hit_log:
                ts, note, label, vel = hit_log.popleft()
                print(f"  note {note:<3} {label:<22} vel {vel:>3} |{_vel_bar(vel)}|")
            # statut periodique (voix actives / xruns)
            now = time.time()
            if now - last_printed > 2.0:
                last_printed = now
                if stream.xruns:
                    print(f"    [!] {stream.xruns} coupures audio (xruns) — augmente blocksize "
                          f"ou verifie le pilote.")
    except KeyboardInterrupt:
        print("\nArret...")
    finally:
        sim_stop.set()
        if midi_port:
            midi_port.close()
        stream.stop()
    return 0


def _simulate_loop(router: DrumRouter, stop: threading.Event, bpm: float) -> None:
    """Rejoue le groove de demo en temps reel, en boucle, jusqu'a l'arret."""
    from .offline import demo_groove
    events, duration = demo_groove(bpm=bpm, bars=2)
    while not stop.is_set():
        start = time.time()
        for (t, kind, a, b) in events:
            if stop.is_set():
                return
            wait = start + t - time.time()
            if wait > 0:
                stop.wait(wait)
            if kind == "note":
                router.note_on(a, b)
            elif kind == "cc":
                router.control_change(a, b)
        stop.wait(max(0.0, start + duration - time.time()))


# --------------------------------------------------------------------------- #
# Sous-commande : learn
# --------------------------------------------------------------------------- #
def cmd_learn(args) -> int:
    cfg = cfgmod.load_config(args.config)
    engine, bank, router = _build_engine_and_bank(cfg)

    from . import audio_output, midi_input

    dev, api_name, extra = audio_output.resolve_output(
        cfg["audio"]["host_api_preference"], cfg["audio"]["wasapi_exclusive"],
        device_index=args.audio_device,
    )
    stream = audio_output.AudioStream(
        engine, dev, cfg["audio"]["samplerate"], cfg["audio"]["blocksize"],
        cfg["audio"]["channels"], extra_settings=extra,
    )

    ports = midi_input.list_input_ports()
    if not ports:
        print("Aucun port MIDI detecte. Branche ta batterie.")
        return 2
    print("Ports MIDI d'entree :")
    for i, p in enumerate(ports):
        print(f"  [{i}] {p}")
    idx = args.midi_port if args.midi_port is not None else _choose(
        "Choisis ton port MIDI", len(ports) - 1, default=0)
    if idx is None:
        return 0

    instruments = synth.available_instruments()
    print("\nInstruments disponibles :")
    for i, name in enumerate(instruments):
        print(f"  [{i}] {name}")

    # Etat partage entre le callback MIDI et la boucle principale
    last = {"note": None, "vel": 0}
    lock = threading.Lock()

    def on_note(note, vel):
        with lock:
            last["note"] = note
            last["vel"] = vel
        s = bank.get(note)
        if s:  # joue le son actuel pour reference
            engine.queue_trigger(s.pick_layer(vel), vel, s.choke_group)

    port = midi_input.MidiInputPort(on_note_on=on_note)
    port.open(idx)
    try:
        stream.start()
    except Exception as e:  # noqa: BLE001
        print(f"(Sortie audio indisponible, assignation possible sans son : {e})")

    print("\n=== MODE APPRENTISSAGE ===")
    print("Frappe un pad, puis tape le numero d'instrument a lui assigner.")
    print("Commandes : 'g <groupe>' pour un choke group, 's' pour sauvegarder, 'q' pour quitter.\n")

    out_path = args.output or cfg.get("_source_path", "config/default_mapping.json")
    try:
        while True:
            with lock:
                note = last["note"]
                vel = last["vel"]
            if note is None:
                time.sleep(0.05)
                continue
            current = bank.get(note)
            cur_txt = current.label if current else "(non assigne)"
            cmd = input(f"Note {note} (vel {vel}) = {cur_txt} -> instrument # "
                        f"(ou g/s/q) : ").strip().lower()
            if cmd == "q":
                break
            if cmd == "s":
                cfgmod.save_config(cfg, out_path)
                print(f"  Sauvegarde dans {out_path}")
                continue
            if cmd.startswith("g "):
                grp = cmd[2:].strip() or None
                cfgmod.set_pad(cfg, note, choke_group=grp)
                print(f"  Choke group de la note {note} = {grp}")
                continue
            try:
                sel = int(cmd)
                instr = instruments[sel]
            except (ValueError, IndexError):
                print("  Numero invalide.")
                continue
            cfgmod.set_pad(cfg, note, instrument=instr, label=instr)
            # recharge ce pad dans la banque pour l'entendre tout de suite
            from .samplebank import PadSound
            layers = synth.synth_layers(instr, cfg["audio"]["samplerate"], 3)
            bank.add(PadSound(note=note, label=instr,
                              choke_group=cfg["pads"][str(note)].get("choke_group"),
                              layers=layers))
            print(f"  Note {note} -> {instr}  (tape 's' pour sauvegarder)")
            with lock:
                last["note"] = None
    except KeyboardInterrupt:
        pass
    finally:
        port.close()
        stream.stop()
    return 0


# --------------------------------------------------------------------------- #
# Sous-commande : render (test/demo hors-ligne)
# --------------------------------------------------------------------------- #
def cmd_render(args) -> int:
    from .offline import demo_groove, render, write_wav

    cfg = cfgmod.load_config(args.config)
    engine, bank, router = _build_engine_and_bank(cfg, verbose=args.verbose)
    events, duration = demo_groove(bpm=args.bpm, bars=args.bars)
    print(f"Rendu de {len(events)} evenements sur {duration:.1f}s...")
    audio = render(router, engine, events, duration, blocksize=cfg["audio"]["blocksize"])
    write_wav(args.output, audio, cfg["audio"]["samplerate"])
    import numpy as np
    print(f"Ecrit : {args.output}  "
          f"(pic {np.max(np.abs(audio)):.3f}, {audio.shape[0] / cfg['audio']['samplerate']:.1f}s)")
    return 0
