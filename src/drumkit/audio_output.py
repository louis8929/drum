"""
Sortie audio live via sounddevice (PortAudio).

Selectionne de preference un host API basse latence (ASIO, puis WASAPI en mode
exclusif) et ouvre un flux dont le callback appelle AudioEngine.process().
Le ramasse-miettes (GC) est desactive pendant le flux pour eviter toute pause.
"""
from __future__ import annotations

import gc

import numpy as np

from .engine import AudioEngine


def _sd():
    import sounddevice as sd  # import tardif
    return sd


def list_host_apis() -> list[dict]:
    sd = _sd()
    return [dict(a) for a in sd.query_hostapis()]


def list_output_devices() -> list[dict]:
    sd = _sd()
    out = []
    apis = sd.query_hostapis()
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0:
            out.append({
                "index": i,
                "name": d["name"],
                "hostapi": apis[d["hostapi"]]["name"],
                "max_output_channels": d["max_output_channels"],
                "default_samplerate": d["default_samplerate"],
            })
    return out


def resolve_output(host_api_preference: list[str], wasapi_exclusive: bool,
                   device_index: int | None = None):
    """
    Choisit (device_index, host_api_name, extra_settings) selon les preferences.
    Si device_index est fourni, il est respecte tel quel.
    """
    sd = _sd()
    apis = sd.query_hostapis()

    def extra_for(api_name: str):
        name = api_name.lower()
        if "wasapi" in name and wasapi_exclusive:
            try:
                return sd.WasapiSettings(exclusive=True)
            except Exception:
                return None
        return None

    if device_index is not None:
        dev = sd.query_devices(device_index)
        api_name = apis[dev["hostapi"]]["name"]
        return device_index, api_name, extra_for(api_name)

    for pref in host_api_preference:
        for api in apis:
            if pref.lower() in api["name"].lower():
                dev = api["default_output_device"]
                if dev is not None and dev != -1:
                    return dev, api["name"], extra_for(api["name"])

    # repli : sortie par defaut du systeme
    default_out = sd.default.device[1]
    dev = sd.query_devices(default_out)
    return default_out, apis[dev["hostapi"]]["name"], None


class AudioStream:
    """Flux de sortie temps reel pilote par AudioEngine."""

    def __init__(self, engine: AudioEngine, device_index: int,
                 samplerate: int, blocksize: int, channels: int,
                 extra_settings=None) -> None:
        self.engine = engine
        self.device_index = device_index
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.channels = channels
        self.extra_settings = extra_settings
        self._stream = None
        self.xruns = 0

    def _callback(self, outdata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            self.xruns += 1
        self.engine.process(outdata)

    def _open(self, extra):
        sd = _sd()
        return sd.OutputStream(
            samplerate=self.samplerate,
            blocksize=self.blocksize,
            device=self.device_index,
            channels=self.channels,
            dtype="float32",
            latency="low",
            callback=self._callback,
            extra_settings=extra,
        )

    def start(self) -> float:
        """
        Ouvre et demarre le flux. Retourne la latence de sortie estimee (ms).
        Si le mode exclusif echoue (peripherique occupe, non supporte), repli
        automatique en mode partage.
        """
        gc.disable()  # evite les pauses du GC pendant le jeu
        self.exclusive = self.extra_settings is not None
        try:
            self._stream = self._open(self.extra_settings)
            self._stream.start()
        except Exception:
            if self.extra_settings is None:
                gc.enable()
                raise
            # repli mode partage
            self.exclusive = False
            self._stream = self._open(None)
            self._stream.start()
        return self._stream.latency * 1000.0

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        gc.enable()
