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
# JSON Schema — mirrored in README and CLI --schema / --help extended docs
# ---------------------------------------------------------------------------

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
            "description": "Maximum tokens per agent reply (before boost).",
        },
        "response_mode": {
            "type": "string",
            "enum": ["all", "single"],
            "description": "all: each persona replies in order per user message; single: one persona per turn (round-robin).",
        },
        "personas": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "name", "system_prompt"],
                "properties": {
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
                },
            },
        },
    },
}


def validate_profile(data: Any) -> None:
    jsonschema.validate(instance=data, schema=PROFILE_SCHEMA)


@dataclass
class Persona:
    id: str
    name: str
    system_prompt: str
    model: str | None = None
    color: str | None = None


@dataclass
class Profile:
    version: int
    personas: list[Persona]
    name: str = "Zen Club"
    description: str = ""
    model: str = "gpt-4o-mini"
    temperature: float = 0.7
    max_tokens: int = 1024
    response_mode: Literal["all", "single"] = "all"
    source_path: Path | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any], source_path: Path | None = None) -> Profile:
        validate_profile(raw)
        personas = [
            Persona(
                id=p["id"],
                name=p["name"],
                system_prompt=p["system_prompt"],
                model=p.get("model"),
                color=p.get("color"),
            )
            for p in raw["personas"]
        ]
        return cls(
            version=raw["version"],
            name=raw.get("name") or "Zen Club",
            description=raw.get("description") or "",
            model=raw.get("model") or "gpt-4o-mini",
            temperature=float(raw.get("temperature", 0.7)),
            max_tokens=int(raw.get("max_tokens", 1024)),
            response_mode=raw.get("response_mode", "all"),
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
    return Persona(
        id=data["id"],
        name=data["name"],
        system_prompt=data["system_prompt"],
        model=data.get("model"),
        color=data.get("color"),
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

    def effective_max_tokens(self) -> int:
        base = self.profile.max_tokens
        if self.boost_responses:
            return int(base * 1.75)
        return base

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


def build_messages_for_persona(
    session: ZenSession,
    persona: Persona,
    prefix_lines: list[TranscriptLine],
    latest_user: str,
) -> list[dict[str, str]]:
    """Prefix lines include prior transcript plus in-turn agent replies before this persona."""
    ctx = _format_context(prefix_lines)
    system = (
        f"{persona.system_prompt.strip()}\n\n"
        "You are in a Zen Club group chat. Other participants may appear in the transcript. "
        "Respond only as your persona. Stay concise unless the user asks for depth. "
        "Do not simulate other speakers."
    )
    user_block = (
        f"Transcript so far:\n{ctx}\n\n"
        f"Latest user message:\n{latest_user}\n\n"
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
) -> str:
    model = persona.model or session.profile.model
    messages = build_messages_for_persona(session, persona, prefix_lines, latest_user)
    max_tokens = session.effective_max_tokens()
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=session.profile.temperature,
        max_tokens=max_tokens,
    )
    choice = resp.choices[0]
    text = (choice.message.content or "").strip()
    if not text:
        return "(no response)"
    return text


def run_user_message(client: OpenAI, session: ZenSession, user_text: str) -> list[tuple[Persona, str]]:
    """Append user line, then each persona replies in order (context includes prior + in-round replies)."""
    snapshot = list(session.transcript)
    try:
        session.append_user(user_text)
        if not session.transcript or session.transcript[-1].kind != "user":
            return []
        prior = session.transcript[:-1]
        latest = session.transcript[-1].content
        results: list[tuple[Persona, str]] = []
        rolling = list(prior)
        for persona in session.personas_for_turn():
            reply = run_agent_turn(client, session, persona, rolling, latest)
            results.append((persona, reply))
            session.append_agent(persona, reply)
            rolling.append(session.transcript[-1])
        return results
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
