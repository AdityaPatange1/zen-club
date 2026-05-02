#!/usr/bin/env python3
"""
Zen Club — terminal group chat with OpenAI-backed personas.
Run: python zen_club.py --profile data/code_group.json
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from rich import box
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.theme import Theme

from zen_analytics import MessageAnalytics, analytics_to_dict, compute_message_analytics
from zen_club_core import (
    PROFILE_SCHEMA,
    POWER_STAGES,
    Persona,
    ZenSession,
    get_openai_client,
    load_env,
    load_persona_from_add_arg,
    load_profile,
    parse_slash,
    run_user_message,
)
from zen_interview import run_interview_session
from zen_transcript import TranscriptRecorder, default_transcript_path

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

_ID_OK = re.compile(r"^[a-zA-Z0-9_-]+$")


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
  web_search        optional { enabled, max_results } — DuckDuckGo snippets for agents
  model             default model (default in app: gpt-5.4-nano)
  personas[]        power_stage (beginner→ultimate), fun_threshold (0–1), …

CLI:
  --model NAME (-m)             override profile default model
  --save-transcript             append JSON Lines log under data/transcripts
  --transcripts-dir PATH        folder for logs (default: data/transcripts)
  --interview                   timed Zen oral exam with 87-parameter report (see --help)
  --choose-interviewer          pick examiner name / difficulty / tradition interactively
  --interview-master NAME       set examiner display name (else default Zen monk at difficulty)
  --interview-minutes N         5–10 (default 7)
  --interview-difficulty S      beginner…ultimate (default ultimate)
  --interview-output-dir DIR    markdown packet folder (default data/interviews)

Slash commands:
  /add_to_group                 interactive wizard (expandable steps)
  /add_to_group <json|path>     merge persona from JSON or file
  /clear_chat                   wipe transcript
  /boost_responses              slightly raise sampling temperature for replies
  /light                        toggle low-contrast UI
  /help                         commands
  /quit                         exit
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
            "Override the profile's default OpenAI model for personas without their own model."
        ),
    )
    p.add_argument(
        "--save-transcript",
        action="store_true",
        help="Write JSON Lines transcript to data/transcripts (see --transcripts-dir).",
    )
    p.add_argument(
        "--transcripts-dir",
        type=Path,
        default=Path("data/transcripts"),
        metavar="DIR",
        help="Directory for transcript JSONL files (default: data/transcripts).",
    )
    p.add_argument(
        "--interview",
        action="store_true",
        help=(
            "Run a timed oral Zen interview (≈5–10 min) with a rigorous examiner; "
            "writes markdown review packet under data/interviews."
        ),
    )
    p.add_argument(
        "--choose-interviewer",
        action="store_true",
        help="Interactive examiner setup (name, difficulty, tradition, notes).",
    )
    p.add_argument(
        "--interview-master",
        metavar="NAME",
        default=None,
        help="Examiner display name (omit with --choose-interviewer or use default monk).",
    )
    p.add_argument(
        "--interview-minutes",
        type=int,
        default=7,
        metavar="N",
        help="Target interview length in minutes (clamped to 5–10). Default: 7.",
    )
    p.add_argument(
        "--interview-difficulty",
        choices=list(POWER_STAGES),
        default="ultimate",
        metavar="STAGE",
        help="Default examiner / monk difficulty when not using --choose-interviewer.",
    )
    p.add_argument(
        "--interview-output-dir",
        type=Path,
        default=Path("data/interviews"),
        metavar="DIR",
        help="Where to write zen_interview_*.md packets (default: data/interviews).",
    )
    return p


def persona_color_tag(persona: Persona) -> str:
    c = (persona.color or "cyan").strip()
    return c if c else "cyan"


def render_agent_reply(console: Console, persona: Persona, text: str, light_mode: bool) -> None:
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
    console.print(panel, overflow="fold", crop=False)


def _metric_bar(score: float, width: int = 14) -> str:
    filled = int(round((max(0.0, min(100.0, score)) / 100.0) * width))
    return "█" * filled + "·" * (width - filled)


def render_analytics_panel(
    console: Console,
    analytics: MessageAnalytics,
    light_mode: bool,
) -> None:
    table = Table(box=box.SIMPLE_HEAD, show_lines=False, pad_edge=False)
    table.add_column("Metric", style="dim" if light_mode else None)
    table.add_column("Bar", justify="left")
    table.add_column("0–100", justify="right", style="bold")

    rows = [
        ("Enlightenment threshold", analytics.enlightenment_threshold),
        ("Repetition threshold", analytics.repetition_threshold),
        ("Fixation on concepts", analytics.fixation_on_concepts),
        ("Data reliance", analytics.data_reliance),
    ]
    for label, score in rows:
        table.add_row(label, _metric_bar(score), f"{score:.0f}")

    border = "dim white" if light_mode else "grey42"
    console.print(
        Panel(
            table,
            title="[zen.muted]Your message — analytics[/]",
            border_style=border,
            padding=(0, 1),
        ),
        overflow="fold",
        crop=False,
    )


def print_banner(console: Console, session: ZenSession) -> None:
    prof = session.profile
    ws = prof.web_search
    ws_note = "on" if ws.enabled else "off"
    meta = Group(
        Markdown(f"## {prof.name}"),
        Markdown(prof.description or "_No description._"),
        Markdown(
            f"**Model:** `{prof.model}` · **Mode:** `{prof.response_mode}` · "
            f"**Personas:** {len(prof.personas)} · **Web search:** `{ws_note}`"
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


def run_interactive_persona_wizard(console: Console, session: ZenSession) -> None:
    """Step-by-step persona builder with expandable panels per stage."""
    steps = [
        "Identity",
        "Voice",
        "Power & play",
    ]
    console.print(
        Panel(
            Markdown(
                "### Add persona — guided setup\n"
                f"Steps: **{' → '.join(steps)}**. Expand each section by answering prompts."
            ),
            title="[zen.title]New persona[/]",
            border_style="green",
            padding=(1, 2),
        ),
        overflow="fold",
        crop=False,
    )

    console.print(Panel("[bold]Step 1 — Identity[/]", border_style="blue", expand=False))
    pid = Prompt.ask("Short id [a-z A-Z 0-9 _ -]", console=console).strip()
    if not _ID_OK.match(pid):
        console.print("[zen.error]Invalid id. Use letters, digits, underscore, hyphen only.[/]")
        return
    name = Prompt.ask("Display name", console=console).strip()
    if not name:
        console.print("[zen.error]Name required.[/]")
        return

    console.print(
        Panel("[bold]Step 2 — Voice[/]", border_style="blue", expand=False),
        overflow="fold",
        crop=False,
    )
    console.print(
        "[dim]System prompt: enter lines below; finish with a single line containing only a dot (.)[/]"
    )
    lines: list[str] = []
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[zen.error]Aborted.[/]")
            return
        if line.strip() == ".":
            break
        lines.append(line)
    system_prompt = "\n".join(lines).strip()
    if not system_prompt:
        console.print("[zen.error]Empty system prompt.[/]")
        return

    color = Prompt.ask("Panel border color (Rich name)", default="cyan", console=console).strip()

    console.print(
        Panel("[bold]Step 3 — Power & play[/]", border_style="blue", expand=False),
        overflow="fold",
        crop=False,
    )
    stage = Prompt.ask(
        "Power stage",
        choices=list(POWER_STAGES),
        default="beginner",
        console=console,
    )
    ft_raw = Prompt.ask(
        "Fun threshold (0 = serious only, 1 = more room for light touches)",
        default="0.15",
        console=console,
    ).strip()
    try:
        ft = max(0.0, min(1.0, float(ft_raw)))
    except ValueError:
        ft = 0.15

    persona = Persona(
        id=pid,
        name=name,
        system_prompt=system_prompt,
        model=None,
        color=color or "cyan",
        power_stage=stage,
        fun_threshold=ft,
    )
    try:
        session.add_persona(persona)
    except ValueError as e:
        console.print(f"[zen.error]{e}[/]")
        return

    console.print(
        Panel(
            Markdown(
                f"**Added:** `{persona.id}` — _{persona.name}_  \n"
                f"**Stage:** `{persona.power_stage}` · **Fun:** `{persona.fun_threshold:.2f}`"
            ),
            title="[green]Saved[/]",
            border_style="green",
        ),
        overflow="fold",
        crop=False,
    )


def handle_slash(
    console: Console,
    session: ZenSession,
    command: str,
    arg: str,
) -> bool:
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
| `/add_to_group` | Interactive wizard (no args) or JSON / file path |
| `/add_persona` | Same as `/add_to_group` with no args |
| `/clear_chat` | Clear transcript |
| `/boost_responses` | Toggle slightly higher sampling temperature |
| `/light` | Toggle subtle UI |
| `/help` | This help |
| `/quit` | Exit |
"""
                ),
                title="Commands",
                border_style="blue",
            ),
            overflow="fold",
            crop=False,
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

    if command in ("add_to_group", "add_persona"):
        if not arg:
            run_interactive_persona_wizard(console, session)
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


def repl_loop(
    console: Console,
    session: ZenSession,
    client,
    recorder: TranscriptRecorder | None,
) -> None:
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

        prior_user_lines = session.recent_user_messages()
        analytics = compute_message_analytics(line, prior_user_lines)

        try:
            with console.status("[zen.muted]Zen agents are thinking…[/]", spinner="dots"):
                pairs, web_snippets = run_user_message(client, session, line)
            web_used = bool(web_snippets.strip())

            if recorder and pairs:
                recorder.log_user(line, analytics_to_dict(analytics))

            for persona, text in pairs:
                render_agent_reply(console, persona, text, session.light_mode)
                render_analytics_panel(console, analytics, session.light_mode)
                if recorder:
                    model = persona.model or session.profile.model
                    recorder.log_agent(
                        persona_id=persona.id,
                        persona_name=persona.name,
                        content=text,
                        power_stage=persona.power_stage,
                        fun_threshold=persona.fun_threshold,
                        model=model,
                        web_search_used=web_used,
                    )
        except Exception as e:
            console.print(f"[zen.error]Request failed:[/] {e}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.schema:
        Console().print_json(data=PROFILE_SCHEMA)
        return 0

    if args.interview:
        if not args.no_dotenv:
            load_env()
        console = Console(highlight=True, soft_wrap=False)
        console.push_theme(DEFAULT_THEME)
        try:
            return run_interview_session(console, args)
        except BrokenPipeError:
            try:
                sys.stdout.close()
            except Exception:
                pass
            return 0

    if not args.profile:
        parser.error("--profile is required unless using --schema or --interview.")

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
    console = Console(highlight=True, soft_wrap=False)
    console.push_theme(LIGHT_THEME if session.light_mode else DEFAULT_THEME)

    recorder: TranscriptRecorder | None = None
    if args.save_transcript:
        tdir = args.transcripts_dir.expanduser().resolve()
        tpath = default_transcript_path(profile.name, tdir)
        recorder = TranscriptRecorder(
            tpath,
            profile_path=str(profile_path.resolve()),
            profile_name=profile.name,
            default_model=profile.model,
            meta={"cli_model_override": args.model},
        )
        console.print(f"[zen.muted]Transcript log:[/] [bold]{tpath}[/]")

    try:
        client = get_openai_client()
    except Exception as e:
        console.print(f"[zen.error]{e}[/]")
        return 1

    print_banner(console, session)

    try:
        repl_loop(console, session, client, recorder)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0
    finally:
        if recorder:
            recorder.log_session_end("exit")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
