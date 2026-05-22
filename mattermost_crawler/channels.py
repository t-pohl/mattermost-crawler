"""Teams und Channels des angemeldeten Users auflisten und auswählen.

In Mattermost liegen Channels unter Teams; ein User kann mehreren Teams
angehören. Diese lesenden Endpunkte werden genutzt:
* ``GET /users/me/teams``                         – Teams des Users
* ``GET /users/me/teams/{team_id}/channels``      – Channels (Mitgliedschaft)
"""
from __future__ import annotations

from dataclasses import dataclass

from rich.table import Table

from .client import MattermostClient

# Channel-Typen: O=öffentlich, P=privat, D=Direktnachricht, G=Gruppen-DM.
# Wir berücksichtigen nur echte Channels (öffentlich/privat).
_RELEVANT_CHANNEL_TYPES = frozenset({"O", "P"})


class ChannelError(RuntimeError):
    pass


@dataclass(frozen=True)
class Team:
    id: str
    name: str
    display_name: str


@dataclass(frozen=True)
class Channel:
    id: str
    name: str
    display_name: str
    type: str
    team_id: str
    team_name: str  # Team-Slug (URL-Name), z. B. "lehrer-sgs"
    team_display_name: str

    @property
    def label(self) -> str:
        return f"{self.team_display_name} / {self.display_name}"


def list_teams(client: MattermostClient) -> list[Team]:
    raw = client.get_json("/users/me/teams")
    teams = [
        Team(
            id=t["id"],
            name=t.get("name", ""),
            display_name=t.get("display_name") or t.get("name", ""),
        )
        for t in raw
    ]
    teams.sort(key=lambda t: t.display_name.lower())
    return teams


def list_channels(client: MattermostClient) -> list[Channel]:
    """Alle (öffentlichen/privaten) Channels über alle Teams des Users."""
    channels: list[Channel] = []
    for team in list_teams(client):
        raw = client.get_json(f"/users/me/teams/{team.id}/channels")
        for c in raw:
            if c.get("type") not in _RELEVANT_CHANNEL_TYPES:
                continue
            channels.append(
                Channel(
                    id=c["id"],
                    name=c.get("name", ""),
                    display_name=c.get("display_name") or c.get("name", ""),
                    type=c["type"],
                    team_id=team.id,
                    team_name=team.name,
                    team_display_name=team.display_name,
                )
            )
    channels.sort(key=lambda c: (c.team_display_name.lower(), c.display_name.lower()))
    return channels


def channels_table(channels: list[Channel]) -> Table:
    """Rich-Tabelle der Channels, gruppiert nach Team."""
    table = Table(title="Verfügbare Channels", header_style="bold")
    table.add_column("Team")
    table.add_column("Channel")
    table.add_column("Typ", justify="center")
    last_team = None
    for c in channels:
        team_cell = c.team_display_name if c.team_display_name != last_team else ""
        kind = "privat" if c.type == "P" else "öffentlich"
        table.add_row(team_cell, c.display_name, kind)
        last_team = c.team_display_name
    return table


def find_channel(
    channels: list[Channel], name: str, team: str | None = None
) -> Channel:
    """Sucht einen Channel anhand des (case-insensitiven) Anzeige- oder Slugnamens.

    ``team`` grenzt bei mehrdeutigen Namen auf ein Team ein (Anzeige- oder
    Slugname des Teams). Wirft ``ChannelError`` bei keiner oder mehreren
    Übereinstimmungen.
    """
    target = name.strip().lower()
    candidates = [
        c
        for c in channels
        if c.display_name.lower() == target or c.name.lower() == target
    ]
    if team:
        t = team.strip().lower()
        candidates = [
            c
            for c in candidates
            if c.team_display_name.lower() == t or c.team_name.lower() == t
        ]

    if not candidates:
        available = ", ".join(sorted({c.display_name for c in channels})) or "(keine)"
        hint = f" im Team '{team}'" if team else ""
        raise ChannelError(
            f"Kein Channel mit Namen '{name}'{hint} gefunden. "
            f"Verfügbar: {available}"
        )
    if len(candidates) > 1:
        teams = ", ".join(sorted(c.team_display_name for c in candidates))
        raise ChannelError(
            f"Channel '{name}' ist in mehreren Teams vorhanden ({teams}). "
            "Bitte mit --team eingrenzen."
        )
    return candidates[0]
