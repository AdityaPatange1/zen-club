#!/usr/bin/env python3
"""
Zen Club — terminal group chat with OpenAI-backed personas.
Run: python zen_club.py --profile data/code_group.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.theme import Theme

from zen_club_core import (
    PROFILE_SCHEMA,
    ZenSession,
    get_openai_client,
    load_env,
    load_persona_from_add_arg,
    load_profile,
    parse_slash,
    run_user_message,
)

DEFAULT_THEME = Theme(
    {
        "zen.title": "bold bright_cyan",
        "zen.muted": "dim",
        "zen.prompt": "bold green",
        "zen.user": "bold white",
        "zen.error": "bold red",
        "zen.hint": "italic dim",
    }
)

LIGHT_THEME = Theme(
    {
        "zen.title": "dim cyan",
        "zen.muted": "dim white",
        "zen.prompt": "dim green",
        "zen.user": "white",
        "zen.error": "red",
        "zen.hint": "italic dim white",
    }
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="zen_club.py",
        description=(
            "Zen Club — group chat in the terminal with OpenAI-powered personas. "
            "Configure agents via a JSON profile (see README for the full schema). "
            "Set OPENAI_API_KEY in a .env file (see .env.example)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Profile JSON (summary — full schema: python zen_club.py --schema or README):
  version           integer, must be 1
  name              optional room title
  description       optional subtitle
  model             default OpenAI model (e.g. gpt-4o-mini)
  temperature       0.0–2.0
  max_tokens        per-reply cap (boost multiplies via /boost_responses)
  response_mode     "all" (every persona replies) or "single" (round-robin)
  personas[]        required; each item:
      id            stable id [a-zA-Z0-9_-]
      name          display name
      system_prompt instructions for the agent
      model         optional override or null
      color         optional Rich color name for panel borders

CLI:
  --model NAME (-m)             override profile default model for personas without their own model

Slash commands:
  /add_to_group <json or path>   merge a persona into the live group
  /clear_chat                      wipe transcript (personas unchanged)
  /boost_responses                 toggle higher max_tokens for replies
  /light                           toggle light / low-contrast (“magic”) UI mode
  /help                            show commands
  /quit or /exit                   leave Zen Club
""".strip(),
    )
    p.add_argument(
        "--profile",
        "-p",
        required=False,
        type=Path,
        metavar="PATH",
        help="Path to profile JSON (see schema in README or use --schema).",
    )
    p.add_argument(
        "--schema",
        action="store_true",
        help="Print the JSON Schema for profile files and exit.",
    )
    p.add_argument(
        "--no-dotenv",
        action="store_true",
        help="Skip loading .env (use only process environment).",
    )
    p.add_argument(
        "--model",
        "-m",
        metavar="NAME",
        default=None,
        help=(
            "Override the profile's default OpenAI model for all personas that do not "
            "set their own model (e.g. gpt-5.4-mini, gpt-5-nano)."
        ),
    )
    return p


def persona_color_tag(persona) -> str:
    c = (persona.color or "cyan").strip()
    return c if c else "cyan"


def render_agent_reply(console: Console, persona, text: str, light_mode: bool) -> None:
    """Render markdown in a panel with proper word wrap (requires soft_wrap=False on Console)."""
    tag = persona_color_tag(persona)
    md = Markdown(text)
    border = "dim " + tag if light_mode else tag
    title = f"[{tag}]{persona.name}[/]"
    panel = Panel(
        md,
        title=title,
        border_style=border,
        padding=(1, 2),
        expand=True,
    )
    # overflow=fold ensures long tokens wrap; crop=False avoids terminal-width clipping.
    console.print(panel, overflow="fold", crop=False)


def print_banner(console: Console, session: ZenSession) -> None:
    prof = session.profile
    meta = Group(
        Markdown(f"## {prof.name}"),
        Markdown(prof.description or "_No description._"),
        Markdown(
            f"**Model:** `{prof.model}` · **Mode:** `{prof.response_mode}` · "
            f"**Personas:** {len(prof.personas)}"
        ),
    )
    console.print(
        Panel(
            meta,
            title="[zen.title]Zen Club[/]",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    console.print(
        "[zen.hint]Type a message, /help for commands, Ctrl+D or /quit to exit.[/]\n"
    )


def handle_slash(
    console: Console,
    session: ZenSession,
    command: str,
    arg: str,
) -> bool:
    """
    Process a slash command. Returns False if the REPL should exit.
    """
    if command in ("quit", "exit", "q"):
        console.print("[zen.muted]Goodbye.[/]")
        return False

    if command in ("help", "h", "?"):
        console.print(
            Panel(
                Markdown(
                    """
| Command | Action |
|---------|--------|
| `/add_to_group` | Add a persona: inline JSON object or path to a JSON file |
| `/clear_chat` | Clear transcript |
| `/boost_responses` | Toggle larger replies (max_tokens × ~1.75) |
| `/light` | Toggle light / subtle UI (magic-friendly) |
| `/help` | This help |
| `/quit` | Exit |
"""
                ),
                title="Commands",
                border_style="blue",
            )
        )
        return True

    if command == "clear_chat":
        session.clear_chat()
        console.print("[zen.muted]Transcript cleared.[/]")
        return True

    if command == "boost_responses":
        session.boost_responses = not session.boost_responses
        state = "on" if session.boost_responses else "off"
        console.print(f"[zen.muted]Boost responses: [bold]{state}[/][/]")
        return True

    if command in ("light", "light_mode"):
        session.light_mode = not session.light_mode
        try:
            console.pop_theme()
        except Exception:
            pass
        console.push_theme(LIGHT_THEME if session.light_mode else DEFAULT_THEME)
        state = "on" if session.light_mode else "off"
        console.print(f"[zen.muted]Light mode: [bold]{state}[/][/]")
        return True

    if command == "add_to_group":
        if not arg:
            console.print(
                "[zen.error]Usage:[/] `/add_to_group` followed by JSON or a path to a persona JSON file."
            )
            return True
        try:
            persona = load_persona_from_add_arg(arg)
            session.add_persona(persona)
            console.print(
                f"[zen.muted]Added persona [bold]{persona.name}[/] (`{persona.id}`).[/]"
            )
        except Exception as e:
            console.print(f"[zen.error]Could not add persona:[/] {e}")
        return True

    console.print(f"[zen.error]Unknown command:[/] /{command}. Try /help.")
    return True


def repl_loop(console: Console, session: ZenSession, client) -> None:
    while True:
        try:
            line = Prompt.ask(
                "[zen.prompt]You[/]",
                console=console,
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[zen.muted]Interrupted — goodbye.[/]")
            break

        if not line:
            continue

        cmd, rest = parse_slash(line)
        if cmd is not None:
            cont = handle_slash(console, session, cmd, rest)
            if not cont:
                break
            continue

        try:
            with console.status("[zen.muted]Zen agents are thinking…[/]", spinner="dots"):
                pairs = run_user_message(client, session, line)
            for persona, text in pairs:
                render_agent_reply(console, persona, text, session.light_mode)
        except Exception as e:
            console.print(f"[zen.error]Request failed:[/] {e}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.schema:
        Console().print_json(data=PROFILE_SCHEMA)
        return 0

    if not args.profile:
        parser.error("--profile is required unless using --schema.")

    if not args.no_dotenv:
        load_env()

    profile_path: Path = args.profile
    try:
        profile = load_profile(profile_path)
    except Exception as e:
        Console(stderr=True).print(f"[bold red]Profile error:[/] {e}")
        return 1

    if args.model and args.model.strip():
        profile.model = args.model.strip()

    session = ZenSession(profile=profile)
    # soft_wrap=True disables word wrap in Rich print() (no_wrap), which clips panel text.
    console = Console(highlight=True, soft_wrap=False)
    console.push_theme(LIGHT_THEME if session.light_mode else DEFAULT_THEME)

    try:
        client = get_openai_client()
    except Exception as e:
        console.print(f"[zen.error]{e}[/]")
        return 1

    print_banner(console, session)

    try:
        repl_loop(console, session, client)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
