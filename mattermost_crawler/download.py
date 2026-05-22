"""Datei-Anhänge eines Channels herunterladen.

Pro Datei::
    GET /files/{file_id}/info   -> Metadaten (name, extension, size, ...)
    GET /files/{file_id}        -> Rohbytes

Zielstruktur::
    <root>/<Team>/<Channel>/<YYYY-MM-DD>_<dateiname>

Ein per-Channel ``.manifest.json`` (file_id -> gespeicherter Dateiname) erlaubt
verlässliches Überspringen bereits geladener Dateien bei erneuten Läufen –
auch bei doppelten Dateinamen.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.console import Console

from .channels import Channel
from .client import MattermostAPIError, MattermostClient
from .posts import FileRef
from .sanitize import sanitize_dir_name, sanitize_file_name

console = Console()

_MANIFEST_NAME = ".manifest.json"


@dataclass
class DownloadStats:
    new: int = 0
    skipped: int = 0
    failed: int = 0


def _load_manifest(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_manifest(path: Path, manifest: dict[str, str]) -> None:
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def _date_prefix(create_at_ms: int) -> str:
    if create_at_ms <= 0:
        return "0000-00-00"
    return datetime.fromtimestamp(create_at_ms / 1000).strftime("%Y-%m-%d")


def _target_filename(file_id: str, info: dict, create_at_ms: int) -> str:
    raw_name = info.get("name") or f"{file_id}"
    name = sanitize_file_name(raw_name)
    # Falls die Sanitisierung die Extension verschluckt hat, aus info ergänzen.
    ext = info.get("extension") or ""
    if ext and not name.endswith(f".{ext.lower()}"):
        name = f"{name}.{ext.lower()}"
    return f"{_date_prefix(create_at_ms)}_{name}"


def _resolve_collision(target_dir: Path, fname: str, file_id: str) -> Path:
    """Vermeidet Überschreiben bei doppelten Dateinamen verschiedener file_ids."""
    target = target_dir / fname
    if not target.exists():
        return target
    stem = Path(fname).stem
    suffix = Path(fname).suffix
    return target_dir / f"{stem}_{file_id[:8]}{suffix}"


def download_channel(
    client: MattermostClient,
    channel: Channel,
    file_refs: list[FileRef],
    root_dir: Path,
) -> DownloadStats:
    """Lädt alle übergebenen Datei-Anhänge des Channels herunter."""
    target_dir = (
        root_dir
        / sanitize_dir_name(channel.team_display_name)
        / sanitize_dir_name(channel.display_name)
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = target_dir / _MANIFEST_NAME
    manifest = _load_manifest(manifest_path)

    stats = DownloadStats()

    for ref in file_refs:
        # Bereits geladen?
        existing = manifest.get(ref.file_id)
        if existing and (target_dir / existing).exists():
            stats.skipped += 1
            continue

        try:
            info = client.get_json(f"/files/{ref.file_id}/info")
            fname = _target_filename(ref.file_id, info, ref.create_at)
            target = _resolve_collision(target_dir, fname, ref.file_id)

            content = client.get_bytes(f"/files/{ref.file_id}")
            target.write_bytes(content)

            manifest[ref.file_id] = target.name
            stats.new += 1
            console.print(f"  [green]+[/green] {target.name}")
        except MattermostAPIError as e:
            stats.failed += 1
            console.print(
                f"  [red]![/red] Datei {ref.file_id} fehlgeschlagen "
                f"(HTTP {e.status_code}: {e.message})"
            )
        except OSError as e:
            stats.failed += 1
            console.print(f"  [red]![/red] Datei {ref.file_id} nicht schreibbar: {e}")

    _save_manifest(manifest_path, manifest)
    return stats
