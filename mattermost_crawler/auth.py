"""Authentifizierung gegen eine Mattermost-Instanz (native E-Mail/Passwort-API).

Die Instanz nutzt den klassischen Mattermost-Login (kein SSO, kein MFA). Login
läuft daher direkt über ``POST /api/v4/users/login`` – kein Browser nötig.

Zwei Login-Tiers in dieser Reihenfolge:
1. Bestehenden Token aus ``.mmauth.json`` wiederverwenden, falls noch gültig
   (verifiziert via ``GET /users/me``).
2. Credential-Login mit ``MM_USERNAME``/``MM_PASSWORD``; der erhaltene Token
   wird in ``.mmauth.json`` gespeichert.

Mit ``force_login=True`` wird der gespeicherte Token ignoriert und direkt neu
eingeloggt.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator

from rich.console import Console

from .client import MattermostAPIError, MattermostClient
from .config import Settings

console = Console()


class AuthError(RuntimeError):
    pass


def _load_saved_token(settings: Settings) -> str | None:
    """Liest den Token aus .mmauth.json, sofern er zur aktuellen Instanz passt."""
    path = settings.auth_state_path
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        console.log(f"[auth] {path} unlesbar, ignoriere: {e}")
        return None
    if data.get("base_url") and data["base_url"] != settings.mm_base_url:
        console.log("[auth] Gespeicherte Session gehört zu anderer Instanz, ignoriere.")
        return None
    token = data.get("token")
    return token if isinstance(token, str) and token else None


def _save_token(settings: Settings, token: str, me: dict[str, Any]) -> None:
    payload = {
        "token": token,
        "base_url": settings.mm_base_url,
        "user_id": me.get("id", ""),
        "username": me.get("username", ""),
    }
    settings.auth_state_path.write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


def _verify(client: MattermostClient) -> dict[str, Any] | None:
    """GET /users/me; gibt das User-Objekt zurück oder None bei ungültiger Session."""
    try:
        return client.get_json("/users/me")
    except MattermostAPIError as e:
        if e.status_code in (401, 403):
            return None
        raise


@contextmanager
def authenticated_client(
    settings: Settings, force_login: bool = False
) -> Iterator[MattermostClient]:
    """Liefert einen eingeloggten MattermostClient (Token wiederverwendet oder neu)."""
    client = MattermostClient(settings.mm_base_url)
    try:
        # Tier 1: gespeicherten Token wiederverwenden.
        if not force_login:
            token = _load_saved_token(settings)
            if token:
                client.set_token(token)
                me = _verify(client)
                if me is not None:
                    console.log(
                        f"[auth] Bestehende Session aus {settings.auth_state_path} "
                        f"wiederverwendet (User: {me.get('username', '?')})."
                    )
                    yield client
                    return
                console.log("[auth] Gespeicherter Token ungültig/abgelaufen.")
                client.set_token("")  # verworfener Token

        # Tier 2: Credential-Login.
        if not settings.has_credentials():
            raise AuthError(
                "Keine gültige Session und keine Zugangsdaten gesetzt. "
                "Bitte MM_USERNAME und MM_PASSWORD in .env eintragen."
            )
        try:
            me = client.login(settings.mm_username, settings.mm_password)
        except MattermostAPIError as e:
            raise AuthError(f"Login fehlgeschlagen: {e.message}") from e

        # Token sollte jetzt gültig sein – kurz verifizieren und persistieren.
        assert client.token is not None
        _save_token(settings, client.token, me)
        console.log(
            f"[auth] Neu eingeloggt als {me.get('username', '?')}, "
            f"Session in {settings.auth_state_path} gespeichert."
        )
        yield client
    finally:
        client.close()
