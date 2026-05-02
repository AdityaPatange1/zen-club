# Zen Club

Terminal group chat with multiple AI personas, backed by the OpenAI API. Configure rooms with JSON profiles, chat in the REPL, and use slash commands for session control.

## Setup

1. **Python 3.10+** recommended.

2. Create a virtualenv and install dependencies:

   ```bash
   make install
   ```

   Or manually: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`

3. **API key** — copy `.env.example` to `.env` and set `OPENAI_API_KEY`. The app loads this via `python-dotenv` on startup (unless you pass `--no-dotenv` and export the variable yourself).

## Run

```bash
python zen_club.py --profile data/code_group.json
```

Override the default chat model from the profile (applies to every persona that does not set its own `model`):

```bash
python zen_club.py --profile data/code_group.json --model gpt-5.4-mini
python zen_club.py -p data/code_group.json -m gpt-5-nano
```

Append a JSON Lines transcript for every session turn (`data/transcripts/`):

```bash
python zen_club.py --profile data/med_group.json --save-transcript
```

With Make:

```bash
make run
```

Print the machine-readable JSON Schema for profiles:

```bash
python zen_club.py --schema
# or: make schema
```

CLI help (includes a short schema summary and slash commands):

```bash
python zen_club.py --help
```

## Profile JSON Schema

Profiles must validate against the schema embedded in `zen_club_core.py` and printed by `--schema`. Summary:

| Field | Type | Required | Description |
|--------|------|----------|-------------|
| `version` | integer | yes | Must be `1`. |
| `name` | string | no | Room title. |
| `description` | string | no | Shown at startup. |
| `model` | string | no | Default OpenAI chat model (built-in default is `gpt-5.4-nano`). |
| `temperature` | number | no | `0`–`2`; default `0.7` if omitted in code paths that apply defaults. |
| `max_tokens` | integer | no | Legacy; ignored (length limits are not sent to the API for model compatibility). |
| `response_mode` | string | no | `"all"` — each persona answers every user message in order. `"single"` — one persona per user message, rotating. |
| `web_search` | object | no | `{ "enabled": bool, "max_results": 1–10 }` — injects DuckDuckGo text snippets into agent context when enabled (requires `ddgs`). |
| `personas` | array | yes | At least one persona object. |

Each **persona** object:

| Field | Type | Required | Description |
|--------|------|----------|-------------|
| `id` | string | yes | Stable id; letters, digits, `_`, `-` only. |
| `name` | string | yes | Display name in the transcript. |
| `system_prompt` | string | yes | System instructions for that agent. |
| `model` | string or `null` | no | Overrides the profile default model. |
| `color` | string | no | Rich color name for panel styling (e.g. `cyan`, `bold magenta`). |
| `power_stage` | string | no | One of `beginner`, `novice`, `advanced`, `master`, `god`, `ultimate` — depth and rigor of replies (default `beginner`). |
| `fun_threshold` | number | no | `0`–`1`; room for brief playful Zen-appropriate touches when appropriate (`0` = always earnest). Default `0.15`. |

Example: see `data/code_group.json`.

After each agent reply, a **message analytics** panel scores your latest input (heuristic 0–100): enlightenment-oriented tone, repetition vs prior lines, conceptual fixation, and data reliance.

## Slash commands

| Command | Action |
|---------|--------|
| `/add_to_group` | With **no** arguments: interactive wizard. Otherwise add from inline JSON or a file path. |
| `/add_persona` | Same as `/add_to_group` with no arguments (wizard). |
| `/clear_chat` | Clear the transcript; personas and profile stay loaded. |
| `/boost_responses` | Toggle a slightly higher sampling temperature for replies. |
| `/light` | Toggle “light” low-contrast UI (useful for subtler terminal styling). |
| `/help` | Show commands. |
| `/quit`, `/exit` | Leave Zen Club. |

## Layout

Flat scripts (no pip package layout):

- `zen_club.py` — CLI, REPL, Rich rendering.
- `zen_club_core.py` — profile validation, session state, OpenAI calls.
- `zen_analytics.py` — heuristic scores for the statistics panel.
- `zen_search.py` — optional web snippets (`ddgs`).
- `zen_transcript.py` — JSON Lines logging under `data/transcripts/`.

## License

See `LICENSE`.
