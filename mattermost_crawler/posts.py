"""Die komplette Historie eines Channels durchblättern und Datei-Referenzen sammeln.

Nutzt ausschließlich ``GET /channels/{id}/posts`` (verändert keinen Zustand,
markiert nichts als gelesen). Geblättert wird rückwärts über den
``before``-Cursor, bis der Channel-Anfang erreicht ist.

Die Posts-Antwort hat die Form::

    {"order": [<ids, neueste zuerst>], "posts": {id: {...}},
     "next_post_id": "", "prev_post_id": "...", "has_next": bool}

Antworten von Replies/Threads erscheinen ebenfalls als Posts desselben
Channels – an Thread-Antworten angehängte Dateien sind damit abgedeckt.
"""
from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console

from .client import MattermostClient

console = Console()

_PER_PAGE = 200


@dataclass(frozen=True)
class FileRef:
    file_id: str
    post_id: str
    create_at: int  # ms seit Epoch (Erstellzeit des Posts)


def collect_file_refs(client: MattermostClient, channel_id: str) -> list[FileRef]:
    """Sammelt alle Datei-Referenzen über die gesamte Channel-Historie.

    Rückgabe ist chronologisch (älteste zuerst).
    """
    refs: list[FileRef] = []
    seen_files: set[str] = set()
    before: str | None = None
    pages = 0
    posts_scanned = 0

    while True:
        params: dict[str, object] = {"per_page": _PER_PAGE}
        if before:
            params["before"] = before
        data = client.get_json(f"/channels/{channel_id}/posts", **params)
        order: list[str] = data.get("order", [])
        posts: dict[str, dict] = data.get("posts", {})
        if not order:
            break

        for pid in order:
            post = posts.get(pid, {})
            posts_scanned += 1
            for fid in post.get("file_ids", []) or []:
                if fid in seen_files:
                    continue
                seen_files.add(fid)
                refs.append(
                    FileRef(
                        file_id=fid,
                        post_id=pid,
                        create_at=int(post.get("create_at", 0)),
                    )
                )

        pages += 1
        before = order[-1]
        if not data.get("has_next", False):
            break

    console.log(
        f"[posts] {pages} Seite(n), {posts_scanned} Post(s) gescannt, "
        f"{len(refs)} Datei-Anhang/-Anhänge gefunden."
    )
    refs.sort(key=lambda r: r.create_at)
    return refs
