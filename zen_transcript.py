"""
JSONL transcript logging under data/transcripts/.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _json_safe(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, Path):
        return str(obj)
    return obj


class TranscriptRecorder:
    """Append one JSON object per line (JSON Lines) for durability."""

    def __init__(
        self,
        path: Path,
        *,
        profile_path: str,
        profile_name: str,
        default_model: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._append(
            {
                "type": "session_start",
                "timestamp": _utc_now(),
                "profile_path": profile_path,
                "profile_name": profile_name,
                "default_model": default_model,
                "meta": meta or {},
            }
        )

    def _append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, default=_json_safe)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def log_user(self, content: str, analytics: dict[str, float] | None = None) -> None:
        self._append(
            {
                "type": "user_message",
                "timestamp": _utc_now(),
                "content": content,
                "analytics": analytics,
            }
        )

    def log_agent(
        self,
        *,
        persona_id: str,
        persona_name: str,
        content: str,
        power_stage: str,
        fun_threshold: float,
        model: str,
        web_search_used: bool,
    ) -> None:
        self._append(
            {
                "type": "agent_message",
                "timestamp": _utc_now(),
                "persona_id": persona_id,
                "persona_name": persona_name,
                "content": content,
                "power_stage": power_stage,
                "fun_threshold": fun_threshold,
                "model": model,
                "web_search_used": web_search_used,
            }
        )

    def log_session_end(self, reason: str = "exit") -> None:
        self._append({"type": "session_end", "timestamp": _utc_now(), "reason": reason})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_transcript_path(profile_name: str, transcripts_dir: Path) -> Path:
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in profile_name.lower())[
        :48
    ]
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return transcripts_dir / f"zen_{ts}_{slug}.jsonl"
