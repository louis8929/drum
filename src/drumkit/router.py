"""
Routeur : relie les evenements MIDI (notes + CC charley) a la banque de sons et
au moteur audio. C'est ici qu'on gere la logique de la charley (ouverte / fermee
/ pedale) a partir des notes et du CC4.
"""
from __future__ import annotations

from typing import Callable

from .config import iter_pads
from .engine import AudioEngine
from .samplebank import SampleBank


class DrumRouter:
    def __init__(self, cfg: dict, bank: SampleBank, engine: AudioEngine) -> None:
        self.cfg = cfg
        self.bank = bank
        self.engine = engine

        hh = cfg.get("hihat", {})
        self.pedal_cc = int(hh.get("pedal_cc", 4))
        self.closed_threshold = int(hh.get("closed_threshold", 60))
        self.hihat_group = hh.get("choke_group", "hihat")
        self.invert_cc = bool(hh.get("invert_cc", False))
        self.cc_controls_openness = bool(hh.get("cc_controls_openness", False))

        # Notes representatives de la charley (deduites du mapping)
        self._note_closed = self._find_note("hihat_closed")
        self._note_open = self._find_note("hihat_open")
        self._note_pedal = self._find_note("hihat_pedal")

        self.cc4_value = 0            # 0 = ouvert, 127 = ferme (apres inversion)
        self._was_closed = False

        # Hooks pour l'interface (affichage live / apprentissage)
        self.on_hit: Callable[[int, str, int], None] | None = None
        self.on_unknown_note: Callable[[int, int], None] | None = None
        self.on_cc: Callable[[int, int], None] | None = None

    def _find_note(self, instrument: str) -> int | None:
        for note, pad in iter_pads(self.cfg):
            if pad.get("instrument") == instrument:
                return note
        return None

    # ----- entree MIDI ----------------------------------------------------- #
    def note_on(self, note: int, velocity: int) -> None:
        sound = self.bank.get(note)

        # Option : laisser le CC4 decider ouvert/ferme pour les pads de charley
        if self.cc_controls_openness and sound is not None and sound.choke_group == self.hihat_group:
            if note in (self._note_closed, self._note_open):
                target = self._note_closed if self.cc4_value >= self.closed_threshold else self._note_open
                sound = self.bank.get(target) or sound

        if sound is None:
            if self.on_unknown_note:
                self.on_unknown_note(note, velocity)
            return

        buf = sound.pick_layer(velocity)
        self.engine.queue_trigger(buf, velocity, sound.choke_group)
        if self.on_hit:
            self.on_hit(note, sound.label, velocity)

    def control_change(self, cc: int, value: int) -> None:
        if self.on_cc:
            self.on_cc(cc, value)
        if cc != self.pedal_cc:
            return
        val = (127 - value) if self.invert_cc else value
        self.cc4_value = val
        closed_now = val >= self.closed_threshold
        if closed_now and not self._was_closed:
            # La pedale vient de fermer la charley : "chick" + coupe de la charley ouverte.
            pedal = self.bank.get(self._note_pedal) if self._note_pedal is not None else None
            if pedal is not None:
                self.engine.queue_trigger(pedal.pick_layer(70), 70, self.hihat_group)
            else:
                self.engine.queue_choke(self.hihat_group)
        self._was_closed = closed_now

    def note_off(self, note: int, velocity: int) -> None:
        # Les sons de batterie sont "one-shot" : le note-off est ignore.
        pass
