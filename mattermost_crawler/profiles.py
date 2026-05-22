"""Mehrere Mattermost-Instanzen als benannte Profile.

Profile werden per Hand in einer TOML-Datei im User-Config-Verzeichnis abgelegt::

    ~/.config/mattermost-crawler/config.toml

    default = "schule"

    [profiles.schule]
    base_url = "https://mm.schulen-saar.de"
    username = "thomas"
    password = "..."

    [profiles.work]
    base_url = "https://chat.example.com"
    username = "tp"
    password = "..."

Pro Profil wird die Session separat unter
``~/.config/mattermost-crawler/sessions/<profil>.json`` gespeichert.

Auswahl:
* ``--profile NAME`` wählt explizit ein Profil.
* sonst der ``default``-Schlüssel; bei genau einem Profil dieses.

Abwärtskompatibilität: existiert keine ``config.toml``, wird wie bisher die
``.env`` im aktuellen Verzeichnis genutzt (ein einzelnes implizites Profil).

Das Verzeichnis lässt sich per ``MATTERMOST_CRAWLER_CONFIG_DIR`` überschreiben
(vor allem für Tests). Andernfalls gilt ``XDG_CONFIG_HOME`` bzw. ``~/.config``.
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import Settings

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class ConfigError(RuntimeError):
    pass


def config_dir() -> Path:
    override = os.environ.get("MATTERMOST_CRAWLER_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "mattermost-crawler"


def config_file() -> Path:
    return config_dir() / "config.toml"


def session_dir() -> Path:
    return config_dir() / "sessions"


def _session_file(name: str) -> Path:
    safe = _SAFE_NAME.sub("_", name).strip("_") or "default"
    return session_dir() / f"{safe}.json"


@dataclass(frozen=True)
class ProfileInfo:
    name: str
    base_url: str
    is_default: bool


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"Konfigurationsdatei {path} unlesbar: {e}") from e


def list_profiles() -> list[ProfileInfo]:
    """Alle konfigurierten Profile (leer, falls keine config.toml existiert)."""
    path = config_file()
    if not path.exists():
        return []
    data = _read_toml(path)
    default = data.get("default")
    profiles = data.get("profiles", {})
    return [
        ProfileInfo(
            name=n,
            base_url=str(p.get("base_url", "")),
            is_default=(n == default),
        )
        for n, p in profiles.items()
    ]


def load_settings(profile: str | None = None) -> Settings:
    """Lädt Settings aus dem gewählten Profil (TOML) oder – als Fallback – aus .env.

    Reihenfolge:
    * Kein --profile und keine config.toml → ``.env`` (bisheriges Verhalten).
    * config.toml vorhanden → das gewählte/Default-Profil daraus.
    * --profile gesetzt, aber keine config.toml → ConfigError.
    """
    path = config_file()

    if profile is None and not path.exists():
        return Settings()  # .env-Fallback

    if not path.exists():
        raise ConfigError(
            f"Profil '{profile}' verlangt, aber keine Konfigurationsdatei unter "
            f"{path}. Lege sie an (siehe README) oder lass --profile weg."
        )

    data = _read_toml(path)
    profiles = data.get("profiles", {})
    if not profiles:
        raise ConfigError(
            f"In {path} sind keine Profile definiert ([profiles.<name>] fehlt)."
        )

    name = profile or data.get("default")
    if name is None:
        if len(profiles) == 1:
            name = next(iter(profiles))
        else:
            avail = ", ".join(sorted(profiles))
            raise ConfigError(
                "Mehrere Profile vorhanden, aber kein 'default' gesetzt und kein "
                f"--profile angegeben. Verfügbar: {avail}"
            )
    if name not in profiles:
        avail = ", ".join(sorted(profiles))
        raise ConfigError(f"Profil '{name}' nicht gefunden. Verfügbar: {avail}")

    p = profiles[name]
    base_url = str(p.get("base_url", "")).rstrip("/")
    if not base_url:
        raise ConfigError(f"Profil '{name}' hat keine 'base_url'.")

    session_dir().mkdir(parents=True, exist_ok=True)
    return Settings(
        mm_base_url=base_url,
        mm_username=str(p.get("username", "")),
        mm_password=str(p.get("password", "")),
        auth_state_path=_session_file(name),
    )
