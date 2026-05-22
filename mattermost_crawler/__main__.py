"""CLI entry point für mattermost-crawler.

Read-only Crawler für eine Mattermost-Instanz:
1. Login (E-Mail/Benutzername + Passwort), Session-Persistenz.
2. Channel auswählen (--list-channels / --channel).
3. Alle Datei-Anhänge der kompletten Channel-Historie herunterladen.

WICHTIG: Der Crawler schreibt, postet oder reagiert NIEMALS in einem Channel –
er liest ausschließlich (siehe client.py).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console

from .auth import AuthError, authenticated_client
from .channels import ChannelError, channels_table, find_channel, list_channels
from .client import MattermostAPIError
from .config import load_settings
from .download import DownloadStats, download_channel
from .posts import collect_file_refs

console = Console()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="mattermost-crawler",
        description=(
            "Read-only Crawler für Mattermost: Login, Channel-Auswahl und "
            "Download aller Datei-Anhänge eines Channels. Schreibt nie in "
            "einen Channel."
        ),
    )
    p.add_argument(
        "--auth-only",
        action="store_true",
        help=(
            "Einloggen, Session in .mmauth.json schreiben, beenden. "
            "(Default-Verhalten ohne weitere Flags.)"
        ),
    )
    p.add_argument(
        "--login",
        action="store_true",
        help=(
            "Erzwingt einen frischen Login mit MM_USERNAME/MM_PASSWORD und "
            "ignoriert eine gespeicherte Session."
        ),
    )
    p.add_argument(
        "--list-channels",
        action="store_true",
        help="Alle Teams und Channels des Accounts ausgeben und beenden.",
    )
    p.add_argument(
        "--channel",
        metavar="NAME",
        help=(
            "Channel anhand seines (case-insensitiven) Anzeigenamens auswählen "
            "und alle Anhänge herunterladen."
        ),
    )
    p.add_argument(
        "--team",
        metavar="NAME",
        help=(
            "Team eingrenzen, falls ein Channel-Name in mehreren Teams "
            "vorkommt. Nur zusammen mit --channel sinnvoll."
        ),
    )
    p.add_argument(
        "--target",
        type=Path,
        default=Path("downloads"),
        metavar="DIR",
        help=(
            "Wurzelordner für Downloads. Default: ./downloads. Dateien landen "
            "unter <target>/<team>/<channel>/."
        ),
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = load_settings()

    try:
        with authenticated_client(settings, force_login=args.login) as client:
            if args.list_channels:
                console.print(channels_table(list_channels(client)))
                return 0

            if args.channel:
                channels = list_channels(client)
                channel = find_channel(channels, args.channel, args.team)
                console.print(f"[bold cyan]Channel: {channel.label}[/bold cyan]")

                refs = collect_file_refs(client, channel.id)
                if not refs:
                    console.print("[yellow]Keine Anhänge im Channel gefunden.[/yellow]")
                    return 0

                stats = download_channel(client, channel, refs, args.target)
                _print_summary(stats)
                return 0

            # Default / --auth-only
            console.print(
                f"[green]Auth OK. Session: {settings.auth_state_path}[/green]"
            )
            return 0
    except AuthError as e:
        console.print(f"[red]Auth fehlgeschlagen: {e}[/red]")
        return 2
    except ChannelError as e:
        console.print(f"[red]Channel-Auswahl fehlgeschlagen: {e}[/red]")
        return 3
    except MattermostAPIError as e:
        console.print(f"[red]API-Fehler: {e}[/red]")
        return 1
    except KeyboardInterrupt:
        console.print("[yellow]Abgebrochen.[/yellow]")
        return 130


def _print_summary(stats: DownloadStats) -> None:
    console.print(
        f"[green]Download fertig: {stats.new} neu, "
        f"{stats.skipped} übersprungen, {stats.failed} fehlgeschlagen.[/green]"
    )


if __name__ == "__main__":
    sys.exit(main())
