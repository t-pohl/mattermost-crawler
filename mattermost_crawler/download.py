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
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.console import Console

from .channels import Channel
from .client import MattermostAPIError, MattermostClient
from .posts import FileRef
from .sanitize import (
    contains_uuid,
    resolve_uuid_serials,
    sanitize_dir_name,
    sanitize_file_name,
)

console = Console()

_MANIFEST_NAME = "manifest.json"


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


def _nfc(name: str) -> str:
    """Normalisiert einen Dateinamen auf NFC für vergleichszwecke.

    Netzwerk-Shares (Synology/SMB) speichern Dateinamen je nach Lauf mal als
    NFC (``ö`` = ein Codepoint) und mal als NFD (``o`` + kombinierendes Trema).
    Ein exakter String-Vergleich zwischen Manifest und Platte schlägt dann fehl
    und lädt bereits vorhandene Dateien erneut herunter. Der Vergleich erfolgt
    daher konsequent über die NFC-Form.
    """
    return unicodedata.normalize("NFC", name)


def _scan_existing(target_dir: Path) -> dict[str, str]:
    """Index vorhandener Dateien: NFC-normalisierter Name -> tatsächlicher Name."""
    present: dict[str, str] = {}
    try:
        for entry in target_dir.iterdir():
            if entry.is_file():
                present[_nfc(entry.name)] = entry.name
    except OSError:
        pass
    return present


def _date_prefix(create_at_ms: int) -> str:
    if create_at_ms <= 0:
        return "0000-00-00"
    return datetime.fromtimestamp(create_at_ms / 1000).strftime("%Y-%m-%d")


def _target_filename(
    file_id: str, info: dict, create_at_ms: int, *, strip_uuid: bool = True
) -> str:
    raw_name = info.get("name") or f"{file_id}"
    # Extension aus info ergänzen, falls der Rohname sie nicht schon trägt.
    ext = info.get("extension") or ""
    if ext and not raw_name.lower().endswith(f".{ext.lower()}"):
        raw_name = f"{raw_name}.{ext.lower()}"
    # WICHTIG: Datums-Präfix VOR der Sanitisierung ansetzen und den *vollen*
    # Basenamen sanitisieren. Würden wir nur das Namensfragment sanitisieren
    # und danach "<datum>_" davor und ggf. ".<ext>" dahinter kleben, entstünde
    # bei leerem/UUID-Namen ein Unterstrich direkt vor der Endung
    # ("2025-11-26_.jpg") — genau der Fall, den sanitizeNames.sh (Regel 14)
    # nachträglich wieder umbenennt und den die Serien-Nummerierung sonst zu
    # "2025-11-26__2.jpg" verschlimmert. Auf dem ganzen Namen greift Regel 14
    # und wir liefern direkt einen Fixpunkt von sanitizeNames.sh.
    full = f"{_date_prefix(create_at_ms)}_{raw_name}"
    return sanitize_file_name(full, strip_uuid=strip_uuid)


def _resolve_collision(target_dir: Path, fname: str, file_id: str, present: dict[str, str]) -> Path:
    """Vermeidet Überschreiben bei doppelten Dateinamen verschiedener file_ids."""
    if _nfc(fname) not in present:
        return target_dir / fname
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
    # Normalisierungs-unabhängiger Index der bereits vorhandenen Dateien (s. _nfc).
    present = _scan_existing(target_dir)

    stats = DownloadStats()

    # Pass 1: Skip-Prüfung + Info-Abruf für die noch zu ladenden Dateien. Der
    # Manifest-Eintrag ist über die file_id eindeutig; die zusätzliche
    # Platten-Prüfung erfolgt normalisierungs-unabhängig, damit NFC/NFD-Unterschiede
    # auf Netzwerk-Shares keine Doppel-Downloads auslösen.
    pending: list[tuple[FileRef, dict]] = []
    for ref in file_refs:
        existing = manifest.get(ref.file_id)
        if existing and _nfc(existing) in present:
            stats.skipped += 1
            continue
        try:
            info = client.get_json(f"/files/{ref.file_id}/info")
        except MattermostAPIError as e:
            stats.failed += 1
            console.print(
                f"  [red]![/red] Datei {ref.file_id} fehlgeschlagen "
                f"(HTTP {e.status_code}: {e.message})"
            )
            continue
        pending.append((ref, info))

    # UUID-Kollisionen innerhalb dieses Channel-Batches per Seriennummer auflösen.
    # Kollisions-Schlüssel ist der volle Zielname (inkl. Datums-Präfix). ``present``
    # als ``reserved``, weil der Batch bereits geladene Dateien nicht enthält.
    entries = [
        (
            _target_filename(ref.file_id, info, ref.create_at, strip_uuid=True),
            contains_uuid(info.get("name") or ""),
            info.get("name") or ref.file_id,
        )
        for ref, info in pending
    ]
    resolved = resolve_uuid_serials(
        entries, is_file=True, reserved=set(present.values())
    )

    # Pass 2: Download.
    try:
        for (ref, info), fname in zip(pending, resolved):
            try:
                target = _resolve_collision(target_dir, fname, ref.file_id, present)

                content = client.get_bytes(f"/files/{ref.file_id}")
                target.write_bytes(content)

                manifest[ref.file_id] = target.name
                present[_nfc(target.name)] = target.name
                _save_manifest(manifest_path, manifest)
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
    finally:
        _save_manifest(manifest_path, manifest)

    return stats
