"""
Zen Club — profile loading, validation, session state, and OpenAI chat turns.
Plain modules (no package layout); imported by zen_club.py.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import jsonschema
from dotenv import load_dotenv
from openai import OpenAI

# ---------------------------------------------------------------------------
# Power stages — shape answer depth and rigor
# ---------------------------------------------------------------------------

PowerStage = Literal["beginner", "novice", "advanced", "master", "god", "ultimate"]

POWER_STAGES: tuple[PowerStage, ...] = (
    "beginner",
    "novice",
    "advanced",
    "master",
    "god",
    "ultimate",
)

POWER_STAGE_GUIDANCE: dict[str, str] = {
    "beginner": (
        "POWER STAGE — BEGINNER: Use plain language, short steps, and gentleness. "
        "Avoid esoteric jargon; offer one clear thread, not a library of concepts."
    ),
    "novice": (
        "POWER STAGE — NOVICE: Add a little more structure and one or two traditional "
        "terms with brief definitions. Keep claims modest; invite practice over theory."
    ),
    "advanced": (
        "POWER STAGE — ADVANCED: You may use nuance, metaphors, and map contradictions. "
        "Connect cause–effect in practice; name common pitfalls and antidotes."
    ),
    "master": (
        "POWER STAGE — MASTER: Expect high reader capacity. Interweave lineages, "
        "paradox, and direct pointing. Be precise, unsentimental, and non-performative."
    ),
    "god": (
        "POWER STAGE — GOD: Maximum density and subtlety; speak to the edge of language. "
        "Integrate ethics, humor-as-upaya when appropriate, and radical non-separation—"
        "without pretense or mystification for its own sake."
    ),
    "ultimate": (
        "POWER STAGE — ULTIMATE: Unrestricted depth; treat the user as a serious "
        "co-investigator. Cut through comfort narratives; offer the most advanced framing "
        "you can without losing contact with ordinary life and compassion."
    ),
}

STAGE_TEMP_BONUS: dict[str, float] = {
    "beginner": 0.0,
    "novice": 0.02,
    "advanced": 0.04,
    "master": 0.06,
    "god": 0.08,
    "ultimate": 0.1,
}


def fun_mode_instructions(threshold: float) -> str:
    if threshold <= 0.0:
        return (
            "FUN MODE: off. Remain fully earnest; do not use humor or playfulness in this reply."
        )
    return (
        f"FUN MODE (threshold {threshold:.2f} on a 0–1 scale): When the exchange is "
        f"overly heavy, the user is playfully testing the room, or a light touch would "
        f"restore balance in a way that still fits Zen / meditative values, you may add "
        f"at most one short, warm, non-cruel beat of humor or gentle levity. "
        f"Never mock suffering. If safety, crisis, or harm appears, stay completely serious. "
        f"Higher threshold allows slightly more frequent (still rare) light touches."
    )


# ---------------------------------------------------------------------------
# JSON Schema — mirrored in README and CLI --schema / --help extended docs
# ---------------------------------------------------------------------------

_PROFILE_PERSONA_PROPERTIES: dict[str, Any] = {
    "id": {
        "type": "string",
        "pattern": r"^[a-zA-Z0-9_-]+$",
        "description": "Stable id for @mentions and commands.",
    },
    "name": {
        "type": "string",
        "description": "Display name in the transcript.",
    },
    "system_prompt": {
        "type": "string",
        "description": "System instructions for this persona.",
    },
    "model": {
        "type": ["string", "null"],
        "description": "Optional per-persona model override.",
    },
    "color": {
        "type": "string",
        "description": "Optional Rich color tag name (e.g. cyan, bold blue).",
    },
    "power_stage": {
        "type": "string",
        "enum": list(POWER_STAGES),
        "description": "How deep and forceful the agent's answers should be.",
    },
    "fun_threshold": {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
        "description": "0 = no humor; up to 1 = more room for brief playful touches when apt.",
    },
}

PROFILE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://zen-club.local/profile.schema.json",
    "title": "Zen Club profile",
    "type": "object",
    "additionalProperties": False,
    "required": ["version", "personas"],
    "properties": {
        "version": {
            "type": "integer",
            "const": 1,
            "description": "Profile format version; must be 1.",
        },
        "name": {
            "type": "string",
            "description": "Display name for this group chat.",
        },
        "description": {
            "type": "string",
            "description": "Optional longer description shown at startup.",
        },
        "model": {
            "type": "string",
            "description": "Default OpenAI chat model for all personas unless overridden.",
        },
        "temperature": {
            "type": "number",
            "minimum": 0,
            "maximum": 2,
            "description": "Sampling temperature (0–2).",
        },
        "max_tokens": {
            "type": "integer",
            "minimum": 1,
            "description": "Legacy field; ignored at runtime (not sent to the API).",
        },
        "response_mode": {
            "type": "string",
            "enum": ["all", "single"],
            "description": "all: each persona replies in order per user message; single: round-robin.",
        },
        "web_search": {
            "type": "object",
            "additionalProperties": False,
            "description": "Optional DuckDuckGo-backed snippets injected before agent replies.",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "When true, agents receive recent web snippets for the user's query.",
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "Max search results to fold into context.",
                },
            },
        },
        "personas": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "name", "system_prompt"],
                "properties": _PROFILE_PERSONA_PROPERTIES,
            },
        },
    },
}


def validate_profile(data: Any) -> None:
    jsonschema.validate(instance=data, schema=PROFILE_SCHEMA)


@dataclass
class WebSearchSettings:
    enabled: bool = False
    max_results: int = 5


@dataclass
class Persona:
    id: str
    name: str
    system_prompt: str
    model: str | None = None
    color: str | None = None
    power_stage: PowerStage = "beginner"
    fun_threshold: float = 0.15


@dataclass
class Profile:
    version: int
    personas: list[Persona]
    name: str = "Zen Club"
    description: str = ""
    model: str = "gpt-5.4-nano"
    temperature: float = 0.7
    max_tokens: int = 1024
    response_mode: Literal["all", "single"] = "all"
    web_search: WebSearchSettings = field(default_factory=WebSearchSettings)
    source_path: Path | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any], source_path: Path | None = None) -> Profile:
        validate_profile(raw)
        ws_raw = raw.get("web_search") or {}
        web_search = WebSearchSettings(
            enabled=bool(ws_raw.get("enabled", False)),
            max_results=int(ws_raw.get("max_results", 5)),
        )
        personas = []
        for p in raw["personas"]:
            stage = p.get("power_stage") or "beginner"
            if stage not in POWER_STAGES:
                stage = "beginner"
            ft = p.get("fun_threshold")
            if ft is None:
                ft = 0.15
            personas.append(
                Persona(
                    id=p["id"],
                    name=p["name"],
                    system_prompt=p["system_prompt"],
                    model=p.get("model"),
                    color=p.get("color"),
                    power_stage=stage,
                    fun_threshold=float(ft),
                )
            )
        return cls(
            version=raw["version"],
            name=raw.get("name") or "Zen Club",
            description=raw.get("description") or "",
            model=raw.get("model") or "gpt-5.4-nano",
            temperature=float(raw.get("temperature", 0.7)),
            max_tokens=int(raw.get("max_tokens", 1024)),
            response_mode=raw.get("response_mode", "all"),
            web_search=web_search,
            personas=personas,
            source_path=source_path,
        )


def load_profile(path: Path) -> Profile:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Profile not found: {path}")
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return Profile.from_dict(data, source_path=path)


def parse_persona_json(text: str) -> Persona:
    """Validate a single persona object (same shape as one personas[] item)."""
    data = json.loads(text)
    item_schema = PROFILE_SCHEMA["properties"]["personas"]["items"]
    jsonschema.validate(instance=data, schema=item_schema)
    stage = data.get("power_stage") or "beginner"
    if stage not in POWER_STAGES:
        stage = "beginner"
    ft = data.get("fun_threshold")
    if ft is None:
        ft = 0.15
    return Persona(
        id=data["id"],
        name=data["name"],
        system_prompt=data["system_prompt"],
        model=data.get("model"),
        color=data.get("color"),
        power_stage=stage,
        fun_threshold=float(ft),
    )


# ---------------------------------------------------------------------------
# Session & transcript
# ---------------------------------------------------------------------------

SpeakerKind = Literal["user", "agent", "system"]


@dataclass
class TranscriptLine:
    kind: SpeakerKind
    speaker_id: str
    speaker_label: str
    content: str


@dataclass
class ZenSession:
    profile: Profile
    transcript: list[TranscriptLine] = field(default_factory=list)
    boost_responses: bool = False
    light_mode: bool = False
    _single_index: int = 0

    def append_user(self, text: str) -> None:
        self.transcript.append(
            TranscriptLine(
                kind="user",
                speaker_id="user",
                speaker_label="You",
                content=text.strip(),
            )
        )

    def append_agent(self, persona: Persona, content: str) -> None:
        self.transcript.append(
            TranscriptLine(
                kind="agent",
                speaker_id=persona.id,
                speaker_label=persona.name,
                content=content.strip(),
            )
        )

    def clear_chat(self) -> None:
        self.transcript.clear()
        self._single_index = 0

    def personas_for_turn(self) -> list[Persona]:
        if self.profile.response_mode == "single":
            p = self.profile.personas[self._single_index % len(self.profile.personas)]
            self._single_index += 1
            return [p]
        return list(self.profile.personas)

    def add_persona(self, persona: Persona) -> None:
        ids = {p.id for p in self.profile.personas}
        if persona.id in ids:
            raise ValueError(f"Persona id already in group: {persona.id}")
        self.profile.personas.append(persona)

    def recent_user_messages(self, *, exclude_last: bool = False) -> list[str]:
        lines = [ln.content for ln in self.transcript if ln.kind == "user"]
        if exclude_last and lines:
            return lines[:-1]
        return lines


def load_env() -> None:
    load_dotenv()


def get_openai_client() -> OpenAI:
    load_env()
    key = os.environ.get("OPENAI_API_KEY")
    if not key or not key.strip():
        raise RuntimeError(
            "OPENAI_API_KEY is missing. Copy .env.example to .env and set your key."
        )
    return OpenAI(api_key=key.strip())


def _format_context(lines: list[TranscriptLine]) -> str:
    parts: list[str] = []
    for ln in lines:
        if ln.kind == "user":
            parts.append(f"{ln.speaker_label}: {ln.content}")
        elif ln.kind == "agent":
            parts.append(f"{ln.speaker_label}: {ln.content}")
    return "\n".join(parts) if parts else "(no prior messages)"


def effective_temperature(session: ZenSession, persona: Persona) -> float:
    base = session.profile.temperature
    bonus = STAGE_TEMP_BONUS.get(persona.power_stage, 0.0)
    t = base + bonus
    if session.boost_responses:
        t += 0.1
    return min(2.0, max(0.0, t))


def build_messages_for_persona(
    session: ZenSession,
    persona: Persona,
    prefix_lines: list[TranscriptLine],
    latest_user: str,
    *,
    web_snippets: str,
) -> list[dict[str, str]]:
    ctx = _format_context(prefix_lines)
    stage_line = POWER_STAGE_GUIDANCE.get(
        persona.power_stage, POWER_STAGE_GUIDANCE["beginner"]
    )
    fun_line = fun_mode_instructions(persona.fun_threshold)

    system = (
        f"{persona.system_prompt.strip()}\n\n"
        f"{stage_line}\n\n"
        f"{fun_line}\n\n"
        "You are in a Zen Club group chat. Other participants may appear in the transcript. "
        "Respond only as your persona. Stay concise unless the user asks for depth. "
        "Do not simulate other speakers."
    )

    web_block = ""
    if web_snippets.strip():
        web_block = (
            "\n\n---\nOptional web snippets for grounding (may be incomplete or biased; "
            "verify critical facts):\n"
            f"{web_snippets.strip()}\n---\n"
        )

    user_block = (
        f"Transcript so far:\n{ctx}\n\n"
        f"Latest user message:\n{latest_user}\n"
        f"{web_block}\n"
        f"Your turn: reply as **{persona.name}** only."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_block},
    ]


def run_agent_turn(
    client: OpenAI,
    session: ZenSession,
    persona: Persona,
    prefix_lines: list[TranscriptLine],
    latest_user: str,
    *,
    web_snippets: str,
) -> str:
    model = persona.model or session.profile.model
    messages = build_messages_for_persona(
        session, persona, prefix_lines, latest_user, web_snippets=web_snippets
    )
    temperature = effective_temperature(session, persona)
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    choice = resp.choices[0]
    text = (choice.message.content or "").strip()
    if not text:
        return "(no response)"
    return text


def run_user_message(
    client: OpenAI,
    session: ZenSession,
    user_text: str,
) -> tuple[list[tuple[Persona, str]], str]:
    """
    Returns (pairs, web_snippets_used).
    """
    snapshot = list(session.transcript)
    web_snippets = ""
    try:
        session.append_user(user_text)
        if not session.transcript or session.transcript[-1].kind != "user":
            return [], ""
        prior = session.transcript[:-1]
        latest = session.transcript[-1].content

        if session.profile.web_search.enabled:
            from zen_search import fetch_web_snippets

            web_snippets = fetch_web_snippets(
                latest, max_results=session.profile.web_search.max_results
            )

        results: list[tuple[Persona, str]] = []
        rolling = list(prior)
        for persona in session.personas_for_turn():
            reply = run_agent_turn(
                client,
                session,
                persona,
                rolling,
                latest,
                web_snippets=web_snippets,
            )
            results.append((persona, reply))
            session.append_agent(persona, reply)
            rolling.append(session.transcript[-1])
        return results, web_snippets
    except Exception:
        session.transcript = snapshot
        raise


_SLASH_ARG = re.compile(r"^/([a-zA-Z_]+)\s*(.*)$")


def parse_slash(line: str) -> tuple[str | None, str]:
    line = line.strip()
    m = _SLASH_ARG.match(line)
    if not m:
        return None, line
    return m.group(1).lower(), m.group(2).strip()


def load_persona_from_add_arg(arg: str) -> Persona:
    """Accept inline JSON or a path to a JSON file containing one persona object."""
    raw = arg.strip()
    if raw.startswith("{"):
        return parse_persona_json(raw)
    p = Path(raw).expanduser()
    if p.is_file():
        with p.open(encoding="utf-8") as f:
            return parse_persona_json(f.read())
    return parse_persona_json(raw)
