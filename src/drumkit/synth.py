"""
Synthese procedurale de sons de batterie (libres de droits, generes en RAM).

Chaque instrument est rendu en plusieurs "couches de timbre" (layers) de
brillance croissante. La vitesse de frappe (velocity) choisit la couche
(timbre) ; le volume, lui, est applique au declenchement par le moteur audio.
Ainsi : frappe douce -> son mat et court, frappe forte -> son brillant et long.

Aucune dependance autre que numpy. Le filtrage se fait dans le domaine
frequentiel (FFT) pour rester vectorise et rapide au chargement.
"""
from __future__ import annotations

import numpy as np

TARGET_PEAK = 0.9  # chaque couche est normalisee a ce pic (le volume vient de la velocity)


# --------------------------------------------------------------------------- #
# Briques de base
# --------------------------------------------------------------------------- #
def _t(n: int, sr: int) -> np.ndarray:
    return np.arange(n, dtype=np.float64) / sr


def _exp_env(n: int, sr: int, decay: float, attack: float = 0.001) -> np.ndarray:
    """Enveloppe attaque rapide + decroissance exponentielle."""
    t = _t(n, sr)
    env = np.exp(-t / max(decay, 1e-4))
    a = int(max(1, attack * sr))
    env[:a] *= np.linspace(0.0, 1.0, a)
    return env


def _colored_noise(n: int, sr: int, rng: np.random.Generator,
                   low_hz: float = 0.0, high_hz: float | None = None) -> np.ndarray:
    """Bruit blanc filtre par FFT (passe-haut a low_hz, passe-bas a high_hz)."""
    x = rng.standard_normal(n)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    mask = np.ones_like(f)
    if high_hz:
        mask *= 1.0 / np.sqrt(1.0 + (f / high_hz) ** 2)        # passe-bas 1er ordre
    if low_hz > 0:
        r = f / low_hz
        mask *= r / np.sqrt(1.0 + r ** 2)                       # passe-haut 1er ordre
    return np.fft.irfft(X * mask, n)


def _pitch_sine(n: int, sr: int, f0: float, f1: float, drop: float) -> np.ndarray:
    """Sinusoide avec chute de hauteur de f0 vers f1 (constante de temps 'drop')."""
    t = _t(n, sr)
    freq = f1 + (f0 - f1) * np.exp(-t / max(drop, 1e-4))
    phase = 2 * np.pi * np.cumsum(freq) / sr
    return np.sin(phase)


def _normalize(x: np.ndarray, peak: float = TARGET_PEAK) -> np.ndarray:
    m = np.max(np.abs(x))
    if m > 1e-9:
        x = x * (peak / m)
    return x


def _stereo(mono: np.ndarray, width: float = 0.0,
            rng: np.random.Generator | None = None) -> np.ndarray:
    """Transforme un signal mono en stereo (pic final garanti a TARGET_PEAK)."""
    if width <= 0 or rng is None:
        st = np.column_stack([mono, mono])
    else:
        n = len(mono)
        diff = _colored_noise(n, 48000, rng, low_hz=500) * width
        env = np.abs(mono)
        st = np.column_stack([mono + diff * env, mono - diff * env])
    # normalisation finale sur l'ensemble des deux canaux
    m = np.max(np.abs(st))
    if m > 1e-9:
        st = st * (TARGET_PEAK / m)
    return st.astype(np.float32)


# --------------------------------------------------------------------------- #
# Recettes par instrument : rendu d'UNE couche pour une brillance 'b' (0..1).
#
# Chaque recette accepte un dict de parametres 'prm' (issu de la config) pour
# modeler le son sans toucher au code :
#   tune   multiplie les frequences (hauteur)          defaut 1.0
#   decay  multiplie la duree/traine                   defaut 1.0
#   bright ajoute de la brillance (-1..+1)             defaut 0.0
#   click  multiplie l'attaque/transitoire             defaut 1.0
#   body   multiplie le corps tonal (grave)            defaut 1.0
# Sans parametres, le son est identique au kit par defaut.
# --------------------------------------------------------------------------- #
def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def _pf(prm, key, default):
    try:
        return float(prm.get(key, default))
    except (TypeError, ValueError, AttributeError):
        return default


def _kick(sr, rng, b, prm):
    tune, dec = _pf(prm, "tune", 1.0), _pf(prm, "decay", 1.0)
    click, body_g = _pf(prm, "click", 1.0), _pf(prm, "body", 1.0)
    bb = _clip(b + _pf(prm, "bright", 0.0), 0.0, 1.0)
    n = int(sr * 0.45 * _clip(dec, 0.3, 3.0))
    body = _pitch_sine(n, sr, (120 + 30 * bb) * tune, 45 * tune, 0.04 * dec) * _exp_env(n, sr, 0.22 * dec)
    clk = _colored_noise(n, sr, rng, low_hz=1500) * _exp_env(n, sr, 0.006) * (0.3 + 0.4 * bb) * click
    return _stereo(_normalize(body * body_g + clk))


def _snare(sr, rng, b, prm):
    tune, dec = _pf(prm, "tune", 1.0), _pf(prm, "decay", 1.0)
    click, body_g = _pf(prm, "click", 1.0), _pf(prm, "body", 1.0)
    bb = _clip(b + _pf(prm, "bright", 0.0), 0.0, 1.0)
    n = int(sr * 0.35 * _clip(dec, 0.3, 3.0))
    tone = (_pitch_sine(n, sr, 190 * tune, 175 * tune, 0.08)
            + 0.7 * _pitch_sine(n, sr, 285 * tune, 262 * tune, 0.08))
    tone *= _exp_env(n, sr, 0.09 * dec)
    noise = _colored_noise(n, sr, rng, low_hz=900, high_hz=9000 + 5000 * bb)
    noise *= _exp_env(n, sr, (0.10 + 0.06 * bb) * dec)
    mix = 0.55 * tone * body_g + (0.9 + 0.3 * bb) * noise * click
    return _stereo(_normalize(mix), width=0.15, rng=rng)


def _sidestick(sr, rng, b, prm):
    tune, dec = _pf(prm, "tune", 1.0), _pf(prm, "decay", 1.0)
    n = int(sr * 0.08 * _clip(dec, 0.3, 3.0))
    click = _colored_noise(n, sr, rng, low_hz=1800, high_hz=7000) * _exp_env(n, sr, 0.02 * dec)
    tone = _pitch_sine(n, sr, 420 * tune, 400 * tune, 0.02) * _exp_env(n, sr, 0.015 * dec) * 0.5
    return _stereo(_normalize(click + tone))


def _clap(sr, rng, b, prm):
    dec = _pf(prm, "decay", 1.0)
    bb = _clip(b + _pf(prm, "bright", 0.0), 0.0, 1.0)
    n = int(sr * 0.3 * _clip(dec, 0.3, 3.0))
    out = np.zeros(n)
    offsets = [0.0, 0.009, 0.018, 0.028]
    for i, off in enumerate(offsets):
        s = int(off * sr)
        seg = _colored_noise(n - s, sr, rng, low_hz=900, high_hz=6000 + 3000 * bb)
        seg *= _exp_env(n - s, sr, 0.012 * dec)
        out[s:] += seg * (1.0 if i < 3 else 0.6)
    out += _colored_noise(n, sr, rng, low_hz=1000, high_hz=5000) * _exp_env(n, sr, 0.12 * dec) * 0.4
    return _stereo(_normalize(out), width=0.2, rng=rng)


def _hihat(sr, rng, b, prm, decay):
    tune = _pf(prm, "tune", 1.0)
    bb = _clip(b + _pf(prm, "bright", 0.0), 0.0, 1.0)
    n = int(sr * (decay * 3 + 0.03))
    # Charley = somme de carres metalliques + bruit tres haut
    partials = [2800, 3400, 4100, 5300, 6400, 7900]
    sig = np.zeros(n)
    t = _t(n, sr)
    for freq in partials:
        sig += np.sign(np.sin(2 * np.pi * freq * tune * t))
    sig *= 0.15
    sig += _colored_noise(n, sr, rng, low_hz=_clip(6000 + 2000 * bb, 2000, 14000)) * 1.2
    sig *= _exp_env(n, sr, decay)
    return _stereo(_normalize(sig), width=0.1, rng=rng)


def _hihat_closed(sr, rng, b, prm):
    return _hihat(sr, rng, b, prm, decay=(0.04 + 0.02 * b) * _pf(prm, "decay", 1.0))


def _hihat_open(sr, rng, b, prm):
    return _hihat(sr, rng, b, prm, decay=(0.35 + 0.25 * b) * _pf(prm, "decay", 1.0))


def _hihat_pedal(sr, rng, b, prm):
    return _hihat(sr, rng, b, prm, decay=0.03 * _pf(prm, "decay", 1.0))


def _tom(sr, rng, b, prm, f0, f1):
    tune, dec = _pf(prm, "tune", 1.0), _pf(prm, "decay", 1.0)
    body_g, click = _pf(prm, "body", 1.0), _pf(prm, "click", 1.0)
    bb = _clip(b + _pf(prm, "bright", 0.0), 0.0, 1.0)
    n = int(sr * 0.5 * _clip(dec, 0.3, 3.0))
    body = _pitch_sine(n, sr, f0 * tune, f1 * tune, 0.09) * _exp_env(n, sr, 0.28 * dec)
    body += 0.4 * _pitch_sine(n, sr, f0 * 1.5 * tune, f1 * 1.5 * tune, 0.09) * _exp_env(n, sr, 0.18 * dec)
    noise = _colored_noise(n, sr, rng, low_hz=400, high_hz=4000) * _exp_env(n, sr, 0.02) * (0.2 + 0.3 * bb) * click
    return _stereo(_normalize(body * body_g + noise), width=0.1, rng=rng)


def _tom_low(sr, rng, b, prm):
    return _tom(sr, rng, b, prm, 110, 80)


def _tom_mid(sr, rng, b, prm):
    return _tom(sr, rng, b, prm, 150, 110)


def _tom_high(sr, rng, b, prm):
    return _tom(sr, rng, b, prm, 200, 150)


def _cymbal(sr, rng, b, prm, base_decay, base_bright):
    tune = _pf(prm, "tune", 1.0)
    decay = base_decay * _pf(prm, "decay", 1.0)
    bright = base_bright + _pf(prm, "bright", 0.0)
    bb = _clip(b + _pf(prm, "bright", 0.0), 0.0, 1.0)
    n = int(sr * decay * 2.5)
    t = _t(n, sr)
    sig = _colored_noise(n, sr, rng, low_hz=3000) * 1.0
    # partiels inharmoniques pour le cote metallique
    for freq in [3200, 4700, 5900, 7300, 9100, 11200]:
        sig += 0.08 * np.sin(2 * np.pi * freq * tune * (1 + 0.002 * rng.standard_normal()) * t)
    sig += _colored_noise(n, sr, rng, low_hz=_clip(8000 + 3000 * bright, 2000, 18000)) * (0.4 + 0.5 * bb)
    sig *= _exp_env(n, sr, decay, attack=0.002)
    return _stereo(_normalize(sig), width=0.35, rng=rng)


def _crash(sr, rng, b, prm):
    return _cymbal(sr, rng, b, prm, base_decay=1.1, base_bright=1.0)


def _ride(sr, rng, b, prm):
    tune, dec = _pf(prm, "tune", 1.0), _pf(prm, "decay", 1.0)
    bb = _clip(b + _pf(prm, "bright", 0.0), 0.0, 1.0)
    n = int(sr * 1.6 * _clip(dec, 0.3, 3.0))
    t = _t(n, sr)
    ping = np.zeros(n)
    for freq, g in [(520, 1.0), (1180, 0.6), (2100, 0.4), (3300, 0.25)]:
        ping += g * np.sin(2 * np.pi * freq * tune * t)
    ping *= _exp_env(n, sr, 0.5 * dec)
    wash = _colored_noise(n, sr, rng, low_hz=4000) * _exp_env(n, sr, 0.8 * dec) * (0.3 + 0.3 * bb)
    return _stereo(_normalize(0.8 * ping + wash), width=0.3, rng=rng)


def _ride_bell(sr, rng, b, prm):
    tune, dec = _pf(prm, "tune", 1.0), _pf(prm, "decay", 1.0)
    n = int(sr * 1.2 * _clip(dec, 0.3, 3.0))
    t = _t(n, sr)
    bell = np.zeros(n)
    for freq, g in [(880, 1.0), (1760, 0.7), (2640, 0.5), (3960, 0.3)]:
        bell += g * np.sin(2 * np.pi * freq * tune * t)
    bell *= _exp_env(n, sr, 0.45 * dec)
    bell += _colored_noise(n, sr, rng, low_hz=6000) * _exp_env(n, sr, 0.2 * dec) * 0.2
    return _stereo(_normalize(bell), width=0.2, rng=rng)


# Table nom d'instrument -> fonction de rendu
RECIPES = {
    "kick": _kick,
    "snare": _snare,
    "sidestick": _sidestick,
    "clap": _clap,
    "hihat_closed": _hihat_closed,
    "hihat_open": _hihat_open,
    "hihat_pedal": _hihat_pedal,
    "tom_low": _tom_low,
    "tom_mid": _tom_mid,
    "tom_high": _tom_high,
    "crash": _crash,
    "ride": _ride,
    "ride_bell": _ride_bell,
}


def available_instruments() -> list[str]:
    return sorted(RECIPES.keys())


def synth_layers(instrument: str, samplerate: int, n_layers: int = 3,
                 seed: int | None = None, params: dict | None = None) -> list[np.ndarray]:
    """
    Rend 'n_layers' couches de timbre (brillance croissante) pour un instrument.
    Retourne une liste de tableaux float32 stereo, forme (N, 2), normalises.
    'params' : dict de reglages de synthese (tune/decay/bright/click/body).
    Deterministe si 'seed' est fourni (utile pour les tests).
    """
    if instrument not in RECIPES:
        raise KeyError(f"Instrument inconnu: {instrument!r}. "
                       f"Dispo: {', '.join(available_instruments())}")
    fn = RECIPES[instrument]
    prm = params or {}
    layers = []
    for i in range(n_layers):
        b = i / max(1, n_layers - 1)  # 0.0 .. 1.0
        # seed stable par instrument+couche pour la reproductibilite
        s = None if seed is None else (seed + hash(instrument) % 10000 + i)
        rng = np.random.default_rng(s)
        layers.append(np.ascontiguousarray(fn(samplerate, rng, b, prm), dtype=np.float32))
    return layers
