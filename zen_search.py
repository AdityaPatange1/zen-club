"""
Optional web search snippets for grounding agent replies (DuckDuckGo text results).
"""

from __future__ import annotations


def fetch_web_snippets(query: str, max_results: int = 5) -> str:
    """
    Return a compact bullet list of search snippets for injection into the prompt.
    Empty string on failure or empty query.
    """
    q = query.strip()
    if not q:
        return ""
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return ""

    lines: list[str] = []
    try:
        with DDGS() as ddgs:
            gen = ddgs.text(q, max_results=max(1, min(max_results, 10)))
            for r in gen:
                title = (r.get("title") or "").strip()
                body = (r.get("body") or "").strip()
                href = (r.get("href") or "").strip()
                excerpt = body[:400] + ("…" if len(body) > 400 else "")
                if title or excerpt:
                    lines.append(f"- **{title}** — {excerpt}" + (f" ({href})" if href else ""))
    except Exception:
        return ""

    if not lines:
        return ""
    return "\n".join(lines)
