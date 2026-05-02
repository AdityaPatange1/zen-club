"""
Timed Zen oral interview mode: examiner dialogue + 87-parameter rubric grading + markdown export.
"""

from __future__ import annotations

import json
import re
import textwrap
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

from zen_club_core import POWER_STAGES, get_openai_client, load_profile

# ---------------------------------------------------------------------------
# 87-dimension rubric (names must stay stable for JSON grading + markdown tables)
# ---------------------------------------------------------------------------

RUBRIC_SLUGS: tuple[str, ...] = (
    "enlightenment",
    "grasping_threshold",
    "mind_control",
    "scattering",
    "integration",
    "field_resonance",
    "accuracy",
    "no_self_tuning",
    "brain_wave_health",
    "sustained_attention",
    "meta_cognitive_clarity",
    "ethical_discernment",
    "compassion_depth",
    "emptiness_familiarity",
    "non_attachment_skill",
    "somatic_grounding",
    "breath_integration",
    "emotional_equanimity",
    "cognitive_flexibility",
    "paradox_tolerance",
    "direct_pointing_receptivity",
    "lineage_respect",
    "humility_signal",
    "effort_quality",
    "relaxation_without_dullness",
    "energy_body_coherence",
    "sensory_gate_discipline",
    "narrative_loosening",
    "identity_diffusion_skill",
    "time_orientation_health",
    "circadian_language_health",
    "trauma_sensitivity",
    "relational_presence",
    "speech_economy",
    "listening_depth",
    "doubt_quality",
    "faith_inquiry_balance",
    "ritual_understanding",
    "sutra_literacy",
    "koan_readiness",
    "vipassana_clarity",
    "shamatha_stability",
    "zen_lineage_fluency",
    "chan_directness",
    "theravada_precision",
    "mahayana_expansiveness",
    "open_presence_range",
    "ethical_precepts_alignment",
    "harm_avoidance_instinct",
    "teacher_student_boundary_awareness",
    "projection_awareness",
    "spiritual_materialism_resistance",
    "bypassing_detection",
    "embodiment_vs_escapism",
    "intellectual_experiential_balance",
    "heart_mind_alignment",
    "subtle_clinging_detection",
    "subtle_aversion_detection",
    "rest_in_uncertainty",
    "beginner_mind_access",
    "peak_experience_integration",
    "dark_night_recognition",
    "motivation_purity",
    "discipline_consistency",
    "daily_practice_signal",
    "community_orientation",
    "service_orientation",
    "nature_connectedness",
    "death_awareness_maturity",
    "impermanence_living",
    "dependent_origination_grasp",
    "two_truths_navigation",
    "skillful_means_sensitivity",
    "silence_comfort",
    "speech_honesty",
    "scatter_recovery_speed",
    "fixation_release_speed",
    "joy_without_grasping",
    "grief_without_collapse",
    "anger_skillfulness",
    "desire_clarity",
    "fear_honesty",
    "identity_story_thinness",
    "witness_stability",
    "unified_field_sense",
    "transmission_readiness",
    "holistic_index_synthesis",
)

assert len(RUBRIC_SLUGS) == 87


def _slug_title(slug: str) -> str:
    return slug.replace("_", " ").title()


@dataclass
class InterviewTurn:
    role: str
    content: str
    t_mono: float


@dataclass
class InterviewConfig:
    model: str
    minutes: int  # 5–10
    choose_interviewer: bool
    master_display_name: str
    difficulty: str  # one of POWER_STAGES
    tradition_note: str
    extra_instructions: str
    output_dir: Path


ULTIMATE_INTERVIEWER_CORE = """\
You are the principal examiner in a formal Zen oral assessment. Your calibration equals \
the most rigorous, ethically uncompromising, and spiritually lucid meditation masters alive: \
absolute fidelity to truth, zero flattery, maximal discernment of subtle grasping and bypass, \
and compassion expressed as clarity—not sentiment. You conduct one-on-one timed interviews \
only; you do not role-play fantasy powers. You speak with compressed precision; questions \
are sharp but never cruel. You assess suitability for serious practice, not social approval.
"""


def build_master_system_prompt(cfg: InterviewConfig) -> str:
    diff = cfg.difficulty
    name = cfg.master_display_name
    parts = [
        ULTIMATE_INTERVIEWER_CORE,
        f"You examine under the rubric name: **{name}**.",
        f"Difficulty / intensity calibration: **{diff}** (scale beginner→ultimate). "
        "At higher levels, demand finer distinctions, less consolation, more paradox tolerance.",
    ]
    if cfg.tradition_note.strip():
        parts.append(f"Tradition / stylistic emphasis requested: {cfg.tradition_note.strip()}")
    if cfg.extra_instructions.strip():
        parts.append(f"Additional examiner instructions: {cfg.extra_instructions.strip()}")

    parts.append(
        textwrap.dedent(
            """
            Rules for this session:
            - Target wall-clock duration is approximately the requested minutes (student sees a timer).
            - Pace: roughly 4–9 concise question rounds, then synthesis—not endless chat.
            - Each of your turns: brief (under ~180 words unless synthesis), one clear question at a time unless synthesizing.
            - When the student types END or time expires, give closing remarks and end with a single line containing exactly: INTERVIEW_COMPLETE
            - Never invent scores during dialogue; evaluation happens after the interview in a separate pass.
            """
        ).strip()
    )
    return "\n\n".join(parts)


def choose_interviewer_interactive(console: Console) -> tuple[str, str, str, str]:
    console.print(
        Panel(
            "[bold]Configure examiner[/]\n"
            "You can emulate any named master as *style*—the model remains a rigorous Zen examiner.",
            title="Interview setup",
            border_style="cyan",
        ),
        overflow="fold",
        crop=False,
    )
    name = Prompt.ask("Examiner display name", default="Vajra Gate Master", console=console).strip()
    diff = Prompt.ask(
        "Difficulty",
        choices=list(POWER_STAGES),
        default="ultimate",
        console=console,
    )
    tradition = Prompt.ask(
        "Tradition / emphasis (optional, empty to skip)",
        default="",
        console=console,
    ).strip()
    extra = Prompt.ask(
        "Extra instructions for examiner (optional, one line; empty to skip)",
        default="",
        console=console,
    ).strip()
    return name, diff, tradition, extra


def default_monk_config(difficulty: str) -> tuple[str, str, str, str]:
    d = difficulty if difficulty in POWER_STAGES else "ultimate"
    return (
        "Mountain Zen Monk — Oral Examiner",
        d,
        "Classical Zen with Mahayana ethics; mountain temple severity with warmth.",
        "",
    )


def interview_loop(
    console: Console,
    client: Any,
    cfg: InterviewConfig,
    master_system: str,
) -> tuple[list[InterviewTurn], float]:
    """Returns transcript turns (monotonic time per turn) and elapsed seconds."""
    max_sec = float(cfg.minutes * 60)
    start = time.monotonic()
    turns: list[InterviewTurn] = []

    messages: list[dict[str, str]] = [
        {"role": "system", "content": master_system},
        {
            "role": "user",
            "content": (
                f"SESSION: Begin now. Wall-clock budget ≈ {cfg.minutes} minutes. "
                "Open with one short paragraph of intent, then Question 1 (label Q1). "
                "Remember to conclude with a line containing exactly INTERVIEW_COMPLETE when finishing."
            ),
        },
    ]

    # First examiner reply
    with console.status("[bold]Examiner preparing opening…[/]", spinner="dots"):
        resp = client.chat.completions.create(
            model=cfg.model,
            messages=messages,
            temperature=0.55,
        )
    opening = (resp.choices[0].message.content or "").strip()
    if not opening:
        opening = "(Examiner produced no text.)"
    turns.append(InterviewTurn("examiner", opening, time.monotonic() - start))
    messages.append({"role": "assistant", "content": opening})
    console.print(
        Panel(Markdown(opening), title=f"[bold cyan]{cfg.master_display_name}[/]", border_style="cyan"),
        overflow="fold",
        crop=False,
    )

    complete_re = re.compile(r"\bINTERVIEW_COMPLETE\b", re.I)
    # ~5–10 min real-time: cap student rounds so dialogue cannot run away.
    max_student_rounds = min(18, max(6, cfg.minutes + 6))
    student_rounds = 0

    while True:
        elapsed = time.monotonic() - start
        remaining = max(0.0, max_sec - elapsed)
        if remaining <= 0:
            break
        if complete_re.search(turns[-1].content):
            break
        if student_rounds >= max_student_rounds:
            break

        console.print(
            f"[dim]~{remaining / 60.0:.1f} min left (target session {cfg.minutes} min)[/]"
        )
        try:
            line = Prompt.ask("[bold green]You (student)[/]", console=console).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Session interrupted.[/]")
            break

        if not line:
            continue
        if line.upper() == "END":
            line = (
                "END — Student requests closing synthesis and INTERVIEW_COMPLETE per protocol."
            )

        student_rounds += 1

        student_block = (
            f"[~{elapsed / 60:.1f} min elapsed, ~{remaining / 60:.1f} min remaining budget]\n"
            f"{line}"
        )
        turns.append(InterviewTurn("student", line, time.monotonic() - start))
        messages.append({"role": "user", "content": student_block})

        elapsed = time.monotonic() - start
        remaining = max(0.0, max_sec - elapsed)
        force_time_close = remaining <= 0
        force_round_close = student_rounds >= max_student_rounds
        if force_time_close or force_round_close:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "SESSION_CLOSE — Time or round limit reached. Deliver your final synthesis "
                        "of this interview now; end with a single line containing exactly INTERVIEW_COMPLETE"
                    ),
                }
            )

        with console.status("[bold]Examiner thinking…[/]", spinner="dots"):
            resp = client.chat.completions.create(
                model=cfg.model,
                messages=messages,
                temperature=0.55,
            )
        reply = (resp.choices[0].message.content or "").strip()
        if not reply:
            reply = "(No reply.)"
        turns.append(InterviewTurn("examiner", reply, time.monotonic() - start))
        messages.append({"role": "assistant", "content": reply})
        console.print(
            Panel(
                Markdown(reply),
                title=f"[bold cyan]{cfg.master_display_name}[/]",
                border_style="cyan",
            ),
            overflow="fold",
            crop=False,
        )

        if force_time_close or force_round_close:
            break
        if complete_re.search(reply):
            break

        elapsed = time.monotonic() - start
        if elapsed >= max_sec:
            break

    # Ensure examiner signs off with INTERVIEW_COMPLETE when missing (hard stop).
    if turns and not complete_re.search(turns[-1].content):
        messages.append(
            {
                "role": "user",
                "content": (
                    "SESSION_CLOSE — End the formal interview now: final remarks and assessment posture. "
                    "End with a single line containing exactly INTERVIEW_COMPLETE"
                ),
            }
        )
        with console.status("[bold]Examiner closing…[/]", spinner="dots"):
            resp = client.chat.completions.create(
                model=cfg.model,
                messages=messages,
                temperature=0.45,
            )
        reply = (resp.choices[0].message.content or "").strip() or "(No closing reply.)"
        turns.append(InterviewTurn("examiner", reply, time.monotonic() - start))
        console.print(
            Panel(
                Markdown(reply),
                title=f"[bold cyan]{cfg.master_display_name}[/] — closing",
                border_style="magenta",
            ),
            overflow="fold",
            crop=False,
        )

    total_elapsed = time.monotonic() - start
    return turns, total_elapsed


def grade_interview_transcript(
    console: Console,
    client: Any,
    cfg: InterviewConfig,
    turns: list[InterviewTurn],
    elapsed_sec: float,
) -> dict[str, Any]:
    """Ask model for JSON scores for all rubric dimensions."""
    lines = []
    for t in turns:
        who = "Examiner" if t.role == "examiner" else "Student"
        lines.append(f"**{who}**: {t.content}")
    transcript_md = "\n\n".join(lines)

    rubric_list = "\n".join(f'- "{s}"' for s in RUBRIC_SLUGS)

    grading_prompt = textwrap.dedent(
        f"""
        You are an independent evaluation committee reviewing a completed Zen oral interview transcript.
        Return ONLY valid JSON (no markdown fences, no commentary outside JSON).

        Required top-level keys:
        - "dimensions": object mapping EACH slug below to an object {{"score": <0-100 integer>, "note": "<one sentence>"}}
        - "overall_summary": string (3–6 sentences, rigorous, compassionate, actionable)
        - "recommended_practice": string (bullet-style sentences ok)
        - "risk_flags": array of strings (empty if none) e.g. bypassing, spiritual emergency cues

        Slugs (exactly 87, all required):
        {rubric_list}

        Context:
        - Examiner display name: {cfg.master_display_name}
        - Difficulty calibration: {cfg.difficulty}
        - Session elapsed seconds (approx): {elapsed_sec:.0f}

        Transcript (markdown-ish):
        {transcript_md}
        """
    ).strip()

    with console.status("[bold]Computing 87-parameter review…[/]", spinner="dots"):
        resp = client.chat.completions.create(
            model=cfg.model,
            messages=[
                {
                    "role": "system",
                    "content": "You output only compact JSON matching the user schema. No markdown.",
                },
                {"role": "user", "content": grading_prompt},
            ],
            temperature=0.35,
        )
    raw = (resp.choices[0].message.content or "").strip()
    # Strip accidental fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {
            "dimensions": {},
            "overall_summary": raw[:8000],
            "recommended_practice": "Parse failed — human reviewer: see raw model output above.",
            "risk_flags": ["json_parse_error"],
            "_raw_model_output": raw,
        }
    return data


def write_interview_markdown(
    path: Path,
    cfg: InterviewConfig,
    turns: list[InterviewTurn],
    elapsed_sec: float,
    grades: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        f"# Zen oral interview — human master review packet",
        "",
        f"- **Generated:** {iso}",
        f"- **Examiner (configured):** {cfg.master_display_name}",
        f"- **Difficulty:** {cfg.difficulty}",
        f"- **Target minutes:** {cfg.minutes}",
        f"- **Elapsed (approx):** {elapsed_sec / 60.0:.1f} min",
        f"- **Model:** `{cfg.model}`",
        "",
        "---",
        "",
        "## Full interview transcript",
        "",
    ]
    for t in turns:
        who = "Examiner" if t.role == "examiner" else "Student"
        lines.append(f"### {who} (~{t.t_mono / 60.0:.1f} min)")
        lines.append("")
        lines.append(t.content)
        lines.append("")

    lines.extend(
        [
            "---",
            "",
            "## Review — 87 parameters",
            "",
        ]
    )

    dims = grades.get("dimensions") or {}
    if isinstance(dims, dict):
        lines.append("| # | Dimension | Score | Note |")
        lines.append("|---|-----------|-------|------|")
        for i, slug in enumerate(RUBRIC_SLUGS, start=1):
            entry = dims.get(slug)
            if isinstance(entry, dict):
                sc = entry.get("score", "")
                note = str(entry.get("note", "")).replace("|", "\\|")
            else:
                sc, note = "", "—"
            lines.append(f"| {i} | {_slug_title(slug)} | {sc} | {note} |")
        lines.append("")

    lines.extend(
        [
            "### Overall summary",
            "",
            str(grades.get("overall_summary", "—")),
            "",
            "### Recommended practice",
            "",
            str(grades.get("recommended_practice", "—")),
            "",
            "### Risk flags",
            "",
        ]
    )
    flags = grades.get("risk_flags") or []
    if isinstance(flags, list) and flags:
        for f in flags:
            lines.append(f"- {f}")
    else:
        lines.append("- _(none listed)_")
    lines.append("")

    if grades.get("_raw_model_output"):
        lines.extend(
            [
                "---",
                "",
                "## Raw grading output (parse fallback)",
                "",
                "```",
                str(grades["_raw_model_output"])[:50000],
                "```",
                "",
            ]
        )

    path.write_text("\n".join(lines), encoding="utf-8")


def default_output_path(output_dir: Path, master_slug: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", master_slug.lower())[:40].strip("_") or "interview"
    return output_dir / f"zen_interview_{ts}_{slug}.md"


def run_interview_session(console: Console, args: Any) -> int:
    """Entry from CLI; args from argparse."""
    minutes = getattr(args, "interview_minutes", None) or 7
    minutes = max(5, min(10, int(minutes)))

    model = (getattr(args, "model", None) or "").strip()
    profile_path = getattr(args, "profile", None)
    if profile_path and not model:
        try:
            model = load_profile(Path(profile_path)).model
        except Exception:
            model = ""
    if not model:
        model = "gpt-5.4-nano"

    output_dir = Path(getattr(args, "interview_output_dir", None) or Path("data/interviews"))
    output_dir = output_dir.expanduser().resolve()

    choose = bool(getattr(args, "choose_interviewer", False))
    master_cli = (getattr(args, "interview_master", None) or "").strip()
    difficulty_arg = getattr(args, "interview_difficulty", None) or "ultimate"

    if difficulty_arg not in POWER_STAGES:
        difficulty_arg = "ultimate"

    if choose:
        name, diff, tradition, extra = choose_interviewer_interactive(console)
        cfg = InterviewConfig(
            model=model,
            minutes=minutes,
            choose_interviewer=True,
            master_display_name=name or "Configured Examiner",
            difficulty=diff,
            tradition_note=tradition,
            extra_instructions=extra,
            output_dir=output_dir,
        )
    elif master_cli:
        cfg = InterviewConfig(
            model=model,
            minutes=minutes,
            choose_interviewer=False,
            master_display_name=master_cli,
            difficulty=difficulty_arg,
            tradition_note="",
            extra_instructions="",
            output_dir=output_dir,
        )
    else:
        name, diff, tradition, extra = default_monk_config(difficulty_arg)
        cfg = InterviewConfig(
            model=model,
            minutes=minutes,
            choose_interviewer=False,
            master_display_name=name,
            difficulty=diff,
            tradition_note=tradition,
            extra_instructions=extra,
            output_dir=output_dir,
        )

    console.print(
        Panel(
            f"[bold]{cfg.master_display_name}[/] — [dim]{cfg.difficulty}[/]\n"
            f"Session target: [bold]{cfg.minutes}[/] minutes (hard cap ~10 per CLI design).\n"
            "Type your answers at prompts; type [bold]END[/] to request closing synthesis early.",
            title="Zen oral interview",
            border_style="magenta",
        ),
        overflow="fold",
        crop=False,
    )

    client = get_openai_client()
    master_system = build_master_system_prompt(cfg)

    turns, elapsed = interview_loop(console, client, cfg, master_system)

    console.print("\n[bold]Generating full 87-parameter assessment…[/]\n")
    grades = grade_interview_transcript(console, client, cfg, turns, elapsed)

    out_path = default_output_path(cfg.output_dir, cfg.master_display_name)
    write_interview_markdown(out_path, cfg, turns, elapsed, grades)

    console.print(
        Panel(
            f"Interview packet written for human Zen meditation master review:\n[bold]{out_path.resolve()}[/]",
            title="Saved",
            border_style="green",
        ),
        overflow="fold",
        crop=False,
    )
    return 0
