"""
Entree MIDI avec deux backends possibles (selection automatique) :

- python-rtmidi : pilote par callback, latence minimale. Backend prefere.
  (Necessite un wheel precompile ou un compilateur C++ ; indisponible sur
   certaines versions de Python comme 3.13 sans outils de build.)
- pygame.midi : scrute le port dans un thread dedie (~1 ms). Repli robuste,
  wheels disponibles partout, latence ajoutee negligeable (< 1 ms).

Interface publique commune :
    list_input_ports() -> list[str]
    MidiInputPort(on_note_on, on_control_change, on_note_off).open(index) / .close()
    backend_name() -> "python-rtmidi" | "pygame.midi" | None
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")  # masque le message pygame

NOTE_ON = 0x90
NOTE_OFF = 0x80
CONTROL_CHANGE = 0xB0

_BACKEND: str | None = None


def _select_backend() -> str | None:
    """Choisit le backend une seule fois (rtmidi prefere, sinon pygame)."""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    try:
        import rtmidi  # noqa: F401
        _BACKEND = "rtmidi"
        return _BACKEND
    except Exception:  # noqa: BLE001
        pass
    try:
        import pygame.midi  # noqa: F401
        _BACKEND = "pygame"
        return _BACKEND
    except Exception:  # noqa: BLE001
        _BACKEND = None
    return _BACKEND


def backend_name() -> str | None:
    b = _select_backend()
    return {"rtmidi": "python-rtmidi", "pygame": "pygame.midi"}.get(b)


def list_input_ports() -> list[str]:
    b = _select_backend()
    if b == "rtmidi":
        import rtmidi
        m = rtmidi.MidiIn()
        try:
            return list(m.get_ports())
        finally:
            del m
    if b == "pygame":
        import pygame.midi
        _ensure_pygame_init()
        ports = []
        for dev_id in range(pygame.midi.get_count()):
            info = pygame.midi.get_device_info(dev_id)
            # info = (interface, name, is_input, is_output, opened)
            if info[2]:  # is_input
                name = info[1].decode() if isinstance(info[1], bytes) else info[1]
                ports.append(f"{name}  [id {dev_id}]")
        return ports
    raise RuntimeError("Aucun backend MIDI disponible (installe python-rtmidi ou pygame).")


# --------------------------------------------------------------------------- #
# pygame helpers
# --------------------------------------------------------------------------- #
def _ensure_pygame_init() -> None:
    import pygame.midi
    if not pygame.midi.get_init():
        pygame.midi.init()


def _pygame_input_device_ids() -> list[int]:
    import pygame.midi
    _ensure_pygame_init()
    return [d for d in range(pygame.midi.get_count())
            if pygame.midi.get_device_info(d)[2]]


# --------------------------------------------------------------------------- #
# Port unifie
# --------------------------------------------------------------------------- #
class MidiInputPort:
    def __init__(self,
                 on_note_on: Callable[[int, int], None],
                 on_control_change: Callable[[int, int], None] | None = None,
                 on_note_off: Callable[[int, int], None] | None = None) -> None:
        self.on_note_on = on_note_on
        self.on_control_change = on_control_change
        self.on_note_off = on_note_off
        self._backend = _select_backend()
        self._midiin = None          # rtmidi
        self._pg_input = None        # pygame
        self._pg_thread = None
        self._pg_stop = threading.Event()

    # ----- dispatch commun ------------------------------------------------- #
    def _dispatch(self, status: int, d1: int, d2: int) -> None:
        kind = status & 0xF0
        if kind == NOTE_ON:
            if d2 > 0:
                self.on_note_on(d1, d2)
            elif self.on_note_off:
                self.on_note_off(d1, 0)
        elif kind == NOTE_OFF:
            if self.on_note_off:
                self.on_note_off(d1, d2)
        elif kind == CONTROL_CHANGE:
            if self.on_control_change:
                self.on_control_change(d1, d2)

    def open(self, port_index: int) -> str:
        if self._backend == "rtmidi":
            return self._open_rtmidi(port_index)
        if self._backend == "pygame":
            return self._open_pygame(port_index)
        raise RuntimeError("Aucun backend MIDI disponible.")

    # ----- rtmidi ---------------------------------------------------------- #
    def _open_rtmidi(self, port_index: int) -> str:
        import rtmidi
        self._midiin = rtmidi.MidiIn()
        name = self._midiin.get_ports()[port_index]
        self._midiin.open_port(port_index)
        self._midiin.ignore_types(sysex=True, timing=True, active_sense=True)

        def cb(event, _data=None):
            msg, _delta = event
            if len(msg) >= 2:
                self._dispatch(msg[0], msg[1], msg[2] if len(msg) > 2 else 0)

        self._midiin.set_callback(cb)
        return name

    # ----- pygame ---------------------------------------------------------- #
    def _open_pygame(self, port_index: int) -> str:
        import pygame.midi
        _ensure_pygame_init()
        dev_ids = _pygame_input_device_ids()
        dev_id = dev_ids[port_index]
        info = pygame.midi.get_device_info(dev_id)
        name = info[1].decode() if isinstance(info[1], bytes) else info[1]
        self._pg_input = pygame.midi.Input(dev_id)
        self._pg_stop.clear()
        self._pg_thread = threading.Thread(target=self._pygame_loop, daemon=True)
        self._pg_thread.start()
        return name

    def _pygame_loop(self) -> None:
        inp = self._pg_input
        while not self._pg_stop.is_set():
            try:
                if inp.poll():
                    for event in inp.read(64):
                        data, _ts = event
                        status, d1, d2 = data[0], data[1], data[2]
                        self._dispatch(status, d1, d2)
                else:
                    time.sleep(0.001)   # scrutation ~1 ms
            except Exception:  # noqa: BLE001
                break

    def close(self) -> None:
        if self._midiin is not None:
            try:
                self._midiin.close_port()
            finally:
                self._midiin = None
        if self._pg_input is not None:
            self._pg_stop.set()
            if self._pg_thread:
                self._pg_thread.join(timeout=0.5)
            try:
                self._pg_input.close()
            finally:
                self._pg_input = None
