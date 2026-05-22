"""HTTP-Client für die Mattermost-REST-API (v4).

READ-ONLY-GARANTIE (projektweite Invariante)
=============================================
Dieser Crawler darf NIEMALS etwas in einem Channel schreiben, veröffentlichen
oder darauf reagieren. Diese Klasse erzwingt das strukturell:

* Es gibt ausschließlich lesende Methoden (``get``, ``get_json``, ``get_bytes``)
  und genau EINEN schreibenden Aufruf: ``login()`` (POST /users/login), der nur
  eine eigene Session erzeugt und nichts im Channel verändert.
* Es existiert KEINE generische ``post``/``put``/``delete``-Methode. Das Erstellen
  einer Nachricht oder Reaktion ist damit im Code nicht erreichbar.
* Endpunkte, die serverseitigen Zustand verändern, werden bewusst nicht benutzt –
  insbesondere NICHT ``POST /channels/{id}/view`` (das würde den Channel als
  "gelesen" markieren). Das Lesen von Posts via ``GET /channels/{id}/posts``
  verändert weder den Lesestatus noch benachrichtigt es jemanden.

Wer hier eine schreibende Methode ergänzt, verletzt die Kern-Anforderung.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

# Mattermost antwortet bei Fehlern mit JSON wie
#   {"id": "...", "message": "...", "status_code": 401}
# Bei 429 (Rate-Limit) wird mit Backoff erneut versucht.
_MAX_RETRIES = 3
_DEFAULT_TIMEOUT = 30.0
_DOWNLOAD_TIMEOUT = 90.0


class MattermostAPIError(RuntimeError):
    """Fehler einer API-Antwort (non-2xx) inklusive Statuscode und Servermeldung."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"HTTP {status_code}: {message}")


def _extract_message(resp: httpx.Response) -> str:
    """Holt die menschenlesbare Fehlermeldung aus einer Mattermost-Antwort."""
    try:
        data = resp.json()
        if isinstance(data, dict):
            return str(data.get("message") or data.get("id") or resp.text)
    except ValueError:
        pass
    return resp.text or resp.reason_phrase


class MattermostClient:
    """Schlanker, ausschließlich lesender Wrapper um die Mattermost-API v4."""

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_base = f"{self.base_url}/api/v4"
        self._token: str | None = None
        self._http = httpx.Client(
            base_url=self.api_base,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "mattermost-crawler/0.1 (read-only)"},
        )
        if token:
            self.set_token(token)

    # -- Token-Verwaltung ---------------------------------------------------
    @property
    def token(self) -> str | None:
        return self._token

    def set_token(self, token: str) -> None:
        self._token = token
        self._http.headers["Authorization"] = f"Bearer {token}"

    # -- Der einzige erlaubte schreibende Aufruf ---------------------------
    def login(self, login_id: str, password: str) -> dict[str, Any]:
        """POST /users/login. Erzeugt eine Session und liefert das User-Objekt.

        Der Session-Token steht im ``Token``-Response-Header und wird sofort
        als Bearer-Token übernommen. Dies ist der EINZIGE nicht-lesende
        Request des Crawlers (siehe Modul-Docstring).
        """
        resp = self._http.post(
            "/users/login",
            json={"login_id": login_id, "password": password},
        )
        if resp.status_code != 200:
            raise MattermostAPIError(resp.status_code, _extract_message(resp))
        token = resp.headers.get("Token")
        if not token:
            raise MattermostAPIError(
                resp.status_code, "Login erfolgreich, aber kein Token-Header erhalten."
            )
        self.set_token(token)
        return resp.json()

    # -- Lesende Zugriffe ---------------------------------------------------
    def get(self, path: str, **params: Any) -> httpx.Response:
        """GET mit Query-Parametern; gibt die Response zurück (Retry bei 429)."""
        return self._request("GET", path, params=params or None)

    def get_json(self, path: str, **params: Any) -> Any:
        """GET, das eine geparste JSON-Antwort liefert."""
        return self.get(path, **params).json()

    def get_bytes(self, path: str) -> bytes:
        """GET, das den Rohinhalt liefert (z. B. Datei-Download)."""
        resp = self._request("GET", path, timeout=_DOWNLOAD_TIMEOUT)
        return resp.content

    # -- intern -------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            resp = self._http.request(method, path, params=params, timeout=timeout)
            if resp.status_code == 429:
                # Rate-Limit: Retry-After respektieren, sonst exponentieller Backoff.
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else 2 ** attempt
                last_exc = MattermostAPIError(429, "Rate limited")
                time.sleep(min(delay, 30.0))
                continue
            if resp.status_code >= 400:
                raise MattermostAPIError(resp.status_code, _extract_message(resp))
            return resp
        # Alle Retries erschöpft.
        assert last_exc is not None
        raise last_exc

    # -- Lifecycle ----------------------------------------------------------
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "MattermostClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
