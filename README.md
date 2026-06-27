# mattermost-crawler

Ein **rein lesender** Crawler für eine [Mattermost](https://mattermost.com/)-Instanz
(z. B. `https://mm.schulen-saar.de`). Er meldet sich mit Benutzername/E-Mail und
Passwort an, lässt einen Channel auswählen und lädt **alle in diesem Channel
veröffentlichten Datei-Anhänge** (PDFs, Office-Dokumente, Bilder, ZIPs …) über die
gesamte Historie herunter.

> **Wichtigste Eigenschaft:** Der Crawler schreibt, postet oder reagiert
> **niemals** in einem Channel. Er nutzt ausschließlich lesende API-Aufrufe
> (`GET`) plus den einen Login-Request. Es gibt im Code keine Methode, die eine
> Nachricht oder Reaktion erzeugen könnte (siehe `mattermost_crawler/client.py`).
> Auch der „als gelesen markieren“-Endpunkt (`/channels/{id}/view`) wird bewusst
> nicht aufgerufen.

## Funktionsweise

Anders als ein klassischer Browser-Crawler nutzt dieses Tool die offizielle
**Mattermost-REST-API v4** direkt – kein Browser, keine Browser-Automatisierung.
Das ist robuster und macht die Read-only-Garantie strukturell einfach.

1. **Login** – `POST /api/v4/users/login`. Der Session-Token wird in
   `.mmauth.json` gespeichert und bei späteren Läufen wiederverwendet.
2. **Channel wählen** – Teams (`/users/me/teams`) und Channels
   (`/users/me/teams/{team}/channels`) auflisten und einen per Name auswählen.
3. **Download** – die komplette Channel-Historie über den `before`-Cursor
   durchblättern (`/channels/{id}/posts`), alle `file_ids` einsammeln und die
   Dateien (`/files/{id}` + `/files/{id}/info`) herunterladen.

## Installation

```bash
cd mattermost-crawler
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Konfiguration

Es gibt zwei Wege – je nachdem, ob du eine oder mehrere Instanzen nutzt.

### Eine Instanz: `.env`

`.env.example` nach `.env` kopieren und ausfüllen:

```bash
cp .env.example .env
```

```ini
MM_USERNAME=deine.email@example.org   # E-Mail oder Benutzername
MM_PASSWORD=dein-passwort
MM_BASE_URL=https://mm.schulen-saar.de
```

`.env` und `.mmauth.json` stehen in `.gitignore` und werden nicht eingecheckt.

### Mehrere Instanzen: Profile (`config.toml`)

Für mehrere Mattermost-Instanzen werden benannte **Profile** in einer
TOML-Datei im User-Config-Verzeichnis hinterlegt:

```
~/.config/mattermost-crawler/config.toml
```

(bzw. `$XDG_CONFIG_HOME/mattermost-crawler/config.toml`). Vorlage:

```bash
mkdir -p ~/.config/mattermost-crawler
cp config.example.toml ~/.config/mattermost-crawler/config.toml
chmod 600 ~/.config/mattermost-crawler/config.toml   # Passwörter im Klartext
```

```toml
default = "schule"          # gilt, wenn --profile weggelassen wird

[profiles.schule]
base_url = "https://mm.schulen-saar.de"
username = "thomas"
password = "..."

[profiles.work]
base_url = "https://chat.example.com"
username = "tp"
password = "..."
```

Auswahl zur Laufzeit mit `--profile`:

```bash
mattermost-crawler --list-profiles                       # Profile anzeigen
mattermost-crawler --profile work --list-channels
mattermost-crawler --profile schule --channel "Klasse 8a Info"
```

- Jedes Profil hat eine **eigene** gespeicherte Session unter
  `~/.config/mattermost-crawler/sessions/<profil>.json`.
- Ohne `--profile` wird das `default`-Profil verwendet (bzw. das einzige, falls
  nur eines existiert).
- **Vorrang:** Existiert eine `config.toml`, wird sie genutzt. Nur wenn keine
  vorhanden ist, greift die `.env` aus dem aktuellen Verzeichnis.
- Das Verzeichnis lässt sich per `MATTERMOST_CRAWLER_CONFIG_DIR` überschreiben.

## Benutzung

```bash
# 1) Login testen und Session speichern
mattermost-crawler --auth-only

# Frischen Login erzwingen (gespeicherte Session ignorieren)
mattermost-crawler --login --auth-only

# 2) Verfügbare Teams/Channels auflisten
mattermost-crawler --list-channels

# 3) Alle Anhänge eines Channels herunterladen
mattermost-crawler --channel "Klasse 8a Info"

# Falls der Channel-Name in mehreren Teams vorkommt:
mattermost-crawler --channel "Allgemein" --team "Kollegium"

# Nur Anhänge ab einem Datum (inklusive, lokale Zeit, Tagesbeginn)
mattermost-crawler --channel "Klasse 8a Info" --after 2026-01-15

# Eigener Zielordner (Default: ./downloads)
mattermost-crawler --channel "Klasse 8a Info" --target ~/material

# Bei mehreren Instanzen: jeweils mit --profile (siehe Konfiguration)
mattermost-crawler --profile work --channel "Allgemein"
```

`--profile` lässt sich mit allen obigen Befehlen kombinieren. Ohne `--profile`
gilt das `default`-Profil bzw. – ohne `config.toml` – die `.env`.

### Ablagestruktur

```
downloads/
└── <Team>/
    └── <Channel>/
        ├── 2025-09-12_arbeitsblatt.pdf
        ├── 2025-09-19_loesungen.pdf
        └── .manifest.json        # interne Liste bereits geladener Dateien
```

- Dateinamen werden mit dem Datum des Posts (`YYYY-MM-DD`) versehen und
  sanitisiert (Umlaute → `ae/oe/ue`, verbotene Zeichen entfernt).
- Bereits geladene Dateien werden bei erneuten Läufen anhand des `.manifest.json`
  übersprungen (zuverlässig auch bei doppelten Dateinamen).

### Exit-Codes

| Code | Bedeutung                       |
|-----:|---------------------------------|
| 0    | Erfolg                          |
| 1    | Unerwarteter API-Fehler         |
| 2    | Login fehlgeschlagen            |
| 3    | Channel-Auswahl fehlgeschlagen  |
| 4    | Konfigurationsfehler (Profil)   |
| 130  | Abbruch (Ctrl-C)                |

## Nicht implementiert (bewusst außerhalb des Umfangs)

- Filter auf bestimmte Dateitypen (z. B. nur PDFs) – aktuell werden **alle**
  Anhänge geladen.
- Zeitraum-/Recent-Beschränkung der Historie.
- Direktnachrichten (DMs) und Gruppen-DMs (nur öffentliche/private Channels).
- Parallele Downloads.
- SSO/MFA-Login (die Zielinstanz nutzt nativen Passwort-Login).

## Voraussetzungen / Hinweise

- Python ≥ 3.11.
- Es werden nur Channels berücksichtigt, in denen der angemeldete Account
  **Mitglied** ist.
- Getestet gegen Mattermost Server v10.7.
