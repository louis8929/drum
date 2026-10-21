# Batterie électronique virtuelle (USB-MIDI → sons)

Reçoit les notes MIDI d'une batterie électronique (ex. **Alesis Nitro Max**) via USB
et joue les sons de batterie correspondants sur le PC, avec une **latence la plus
faible possible** (WASAPI exclusif / ASIO, samples préchargés en RAM).

Testé sur **Windows 11, Python 3.13 et 3.14**, sortie audio intégrée (Realtek) :
flux **WASAPI exclusif à ~4,7 ms de latence, 0 coupure**.

---

## 1. Choix de la stack (et pourquoi)

**Python + `sounddevice` (PortAudio) + `python-rtmidi` *ou* `pygame.midi` + NumPy.**

Pour un sampler de batterie, le cœur temps réel est simple : recevoir une note MIDI
et mixer des buffers audio **déjà en RAM**. Le facteur limitant de la latence n'est
pas le langage mais **la taille de bloc audio et le pilote**. `sounddevice` donne accès
au **WASAPI exclusif** (aucun pilote à installer sous Windows) et à **ASIO** ; le mixage
se fait avec NumPy sur des buffers préalloués (zéro allocation dans le callback, GC
désactivé pendant le jeu) → pas de craquements. Résultat mesuré : **< 7 ms bout-en-bout**,
pour un cycle de développement bien plus court qu'en C++/Rust. Si un jour tu veux
< 3 ms garantis, seul le moteur audio (`engine.py`) serait à porter.

> **Entrée MIDI** : le logiciel utilise `pygame.midi` (via **`pygame-ce`**, qui a des
> wheels à jour pour Python 3.13 **et 3.14**) — scrutation à ~1 ms, latence ajoutée
> négligeable. Si `python-rtmidi` est installé (callback, latence encore plus basse),
> il est détecté et utilisé en priorité ; mais il n'a pas de wheel pour 3.13/3.14.

---

## 2. Installation

```bash
# 1. Se placer dans le dossier du projet
cd drum

# 2. Créer un environnement virtuel
python -m venv .venv
.\.venv\Scripts\activate        # Windows PowerShell
# source .venv/bin/activate     # macOS / Linux

# 3. Installer les dépendances
pip install -r requirements.txt

# 4. (optionnel, latence MIDI minimale si un compilateur C++ est présent)
pip install python-rtmidi
```

Aucun sample à télécharger : les sons sont **synthétisés en RAM au démarrage**
(libres de droits, voir §6).

---

## 3. Utilisation

Toutes les commandes se lancent via `main.py`.

### Lister le matériel
```bash
python main.py devices
```
Affiche les ports MIDI d'entrée, le backend MIDI actif, et les sorties audio
(avec leur host API). Repère ta batterie et, éventuellement, la sortie audio voulue.

### Jouer en direct
```bash
python main.py play                      # choix interactif du port MIDI
python main.py play --midi-port 1        # port MIDI imposé
python main.py play --audio-device 12    # sortie audio imposée (voir "devices")
python main.py play --master 0.8         # volume général (0.0 à 1.0)
```
À chaque frappe, la note, le son joué et la vélocité s'affichent en direct :
```
  note 38  Caisse claire          vel 115 |██████████████████··|
```
`Ctrl+C` pour quitter.

### Tester sans batterie (simulation)
```bash
python main.py play --simulate           # un groove tourne en boucle (son réel)
python main.py play --simulate --bpm 120
```

### Mode apprentissage MIDI
```bash
python main.py learn
```
Frappe un pad : le logiciel affiche la note reçue et te laisse lui assigner un son.
Commandes : un **numéro** d'instrument pour l'assigner, `g <groupe>` pour définir un
choke group (ex. `g hihat`), `s` pour **sauvegarder**, `q` pour quitter.
Par défaut la sauvegarde écrase la config chargée (`--output` pour un autre fichier).

### Rendu de démo / test hors-ligne (aucun matériel requis)
```bash
python main.py render demo.wav --bpm 100 --bars 2
```

### Exporter les sons en WAV / lancer les tests
```bash
python tools/generate_samples.py         # -> samples/synth/*.wav (un par instrument)
python tools/selftest.py                 # vérifie toute la chaîne audio (sans matériel)
python tools/selftest.py --wav           # écrit aussi selftest_demo.wav
```

---

## 4. Configuration (`config/default_mapping.json`)

JSON éditable. Le mapping par défaut suit la norme **General MIDI**.

```jsonc
{
  "audio": {
    "samplerate": 48000,
    "blocksize": 128,          // ↓ = moins de latence, ↑ = plus de stabilité
    "channels": 2,
    "host_api_preference": ["ASIO", "Windows WASAPI"],
    "wasapi_exclusive": true,  // false = mode partagé (plus compatible, un peu +)
    "max_voices": 64,          // polyphonie max
    "master_gain": 0.9
  },
  "hihat": {
    "pedal_cc": 4,             // CC de la pédale charley (CC4 sur Nitro Max)
    "closed_threshold": 60,    // au-dessus = charley considérée fermée
    "choke_group": "hihat",
    "invert_cc": false,        // true si 0=fermé / 127=ouvert sur ton module
    "cc_controls_openness": false // true = le CC4 choisit ouvert/fermé au lieu de la note
  },
  "pads": {
    "36": { "instrument": "kick",         "label": "Grosse caisse" },
    "38": { "instrument": "snare",        "label": "Caisse claire" },
    "42": { "instrument": "hihat_closed", "label": "Charley fermée", "choke_group": "hihat" },
    "46": { "instrument": "hihat_open",   "label": "Charley ouverte", "choke_group": "hihat" }
    // ...
  }
}
```

- **`instrument`** : nom d'un son synthétisé (voir liste ci-dessous). Alternative :
  **`"file": "samples/mon_kick.wav"`** pour utiliser un vrai sample (chemin relatif à
  la config ; rééchantillonné automatiquement, mono converti en stéréo).
- **`choke_group`** : les sons d'un même groupe se coupent entre eux (avec un court
  fondu anti-clic). Indispensable pour la charley (fermée coupe ouverte) et les chokes
  de cymbales.

**Instruments synthétisés disponibles :** `kick`, `snare`, `sidestick`, `clap`,
`hihat_closed`, `hihat_open`, `hihat_pedal`, `tom_low`, `tom_mid`, `tom_high`,
`crash`, `ride`, `ride_bell`.

### Charley (ouverte / fermée / pédale)
Deux mécanismes, combinables :
1. **Par note** : le module envoie 42 (fermée), 46 (ouverte), 44 (pédale). Chaque note
   d'un même `choke_group` coupe la précédente → l'ouverte s'arrête quand tu joues fermé.
2. **Par CC4** : quand tu enfonces la pédale (CC4 franchit `closed_threshold`), un son
   de pédale (« chick ») est joué et la charley ouverte est coupée. Active
   `cc_controls_openness` si tu veux que la position de la pédale choisisse elle-même
   ouvert/fermé au moment de la frappe.

---

## 5. Architecture (modules)

```
main.py                     Point d'entrée + arguments (devices/play/learn/render)
config/default_mapping.json Mapping General MIDI par défaut
src/drumkit/
  synth.py          Synthèse procédurale des sons (libre de droits, en RAM)
  samplebank.py     Banque : instrument/WAV → buffers (couches de vélocité)
  config.py         Chargement / sauvegarde / validation du mapping JSON
  midi_input.py     Entrée MIDI (backends python-rtmidi ou pygame.midi)
  router.py         MIDI → banque → moteur (logique charley/CC4)
  engine.py         Moteur audio temps réel (polyphonie, choke, fades) — sans allocation
  audio_output.py   Flux sounddevice (WASAPI exclusif/ASIO, repli partagé)
  offline.py        Rendu hors-ligne d'une séquence en WAV (démo/test)
  app.py            Logique des sous-commandes CLI
tools/
  generate_samples.py  Exporte les sons synthétisés en WAV
  selftest.py          Tests de bout en bout sans matériel
```

Chemin critique : `midi_input` (thread MIDI) → `router.note_on` → `engine.queue_trigger`
(dépôt non bloquant dans une `deque`) ; le **callback audio** vide la deque, assigne les
voix et mixe. Aucun verrou, aucune allocation dans le callback.

---

## 6. Sons : d'où viennent-ils ?

Par défaut, **100 % synthétisés** par `synth.py` (grosse caisse = sinus à hauteur
descendante, caisse claire = corps tonal + bruit, charley/cymbales = bruit filtré +
partiels inharmoniques, etc.). Libres de droits, aucun téléchargement.

Pour des sons plus réalistes, remplace par de vrais samples **libres de droits** :
- **Freesound** (https://freesound.org) — filtrer par licence CC0.
- **99 Sounds – Drums** ou kits CC0 divers.
- Pose tes `.wav` dans `samples/` et pointe-les via `"file": "..."` dans la config
  (ou garde plusieurs couches de vélocité en créant plusieurs pads/fichiers).

---

## 7. Dépannage

| Problème | Piste |
|---|---|
| **Port MIDI introuvable** | `python main.py devices`. Branche la batterie avant de lancer. Sous Windows, un seul logiciel à la fois peut ouvrir le port MIDI — ferme les autres (DAW, BFD, etc.). |
| **Craquements / coupures (xruns)** | Augmente `blocksize` (128 → 256), ferme les applis gourmandes, branche le PC sur secteur (désactive l'économie d'énergie du CPU). |
| **Latence trop élevée** | Passe `wasapi_exclusive: true`, baisse `blocksize`. Pour aller plus loin, installe **ASIO4ALL** (http://www.asio4all.org) : il apparaîtra dans `host_api_preference` et sera choisi en priorité. |
| **« Error opening OutputStream » / périphérique occupé** | Le mode exclusif requiert un accès réservé ; ferme ce qui utilise la carte son, ou mets `wasapi_exclusive: false`. Le logiciel bascule aussi tout seul en mode partagé si l'exclusif échoue. |
| **Charley ouverte qui ne se coupe pas** | Vérifie que 42/44/46 ont bien `"choke_group": "hihat"`. Si la pédale agit à l'envers, mets `invert_cc: true`. Vérifie le CC de ta pédale dans `learn` (il s'affiche). |
| **Pas de son en simulation** | Vérifie la sortie audio (`--audio-device`) et le volume `--master`. |
| **`python-rtmidi` ne s'installe pas** | Normal sans compilateur sur Python 3.13/3.14 : le logiciel utilise `pygame.midi` (pygame-ce) automatiquement. Pour rtmidi, utiliser Python 3.11/3.12 (wheels dispo) ou installer les *Build Tools for Visual Studio*. |
| **`pip install pygame` échoue sur Python 3.14** | Utilise **`pygame-ce`** (déjà dans `requirements.txt`) : `pygame` classique n'a pas encore de wheel 3.14, pygame-ce oui. N'installe pas les deux en même temps ; au besoin `pip uninstall pygame` puis `pip install pygame-ce`. |
| **Note non reconnue** | Elle s'affiche `?? (non assigné)`. Utilise `learn` pour l'assigner, ou ajoute-la dans la config. |

---

## 8. Plus tard (non implémenté)

Kits de sons multiples, enregistrement MIDI, métronome, effets (reverb), mixage par pad,
round-robin (variation de samples pour éviter l'effet « mitraillette »).
