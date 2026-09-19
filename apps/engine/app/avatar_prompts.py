"""Avatar (persona) system prompts, loaded from a flat text file instead of
the `core.avatars.profile` column.

Why a file and not the DB
-------------------------
The persona prompt is *prompt engineering*, not study data: it is iterated on
by hand, reviewed in diffs, and is identical across every study that uses the
persona. Keeping it in `fixtures/avatar_prompts.txt` means editing a prompt is
a code change (reviewable, revertable) rather than an UPDATE statement, and
the DB keeps only the identity of the persona — its `id` and `name`.

So `core.avatars` rows still exist (they have to: `runs.run_reactions.avatar_id`
is a foreign key onto them), but `profile` is no longer what the engine sends
to the LLM. The engine resolves `avatar_id -> persona key -> prompt text`
through `AVATAR_ID_TO_PERSONA` below.

File format
-----------
Persona blocks separated by runs of `=` characters. Inside a block, the first
non-empty line is the persona *name*; everything after it, up to the next
separator, is the prompt body::

    ==========================================
    Academic Oncologist

    You are an academic medical oncologist ...

    What shapes your judgment:
    ...

Lookup is normalized (lowercased, non-alphanumerics collapsed to `-`), so
"Academic Oncologist", "academic-oncologist" and "Academic  Oncologist" all
resolve to the same block.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

#: fixtures/avatar_prompts.txt, resolved absolutely (like config.py's .env
#: lookup) so the worker behaves the same whatever directory it starts in.
PROMPTS_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "avatar_prompts.txt"

_SEPARATOR = re.compile(r"^=+\s*$")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def persona_key(name: str) -> str:
    """Normalize a persona name into its lookup key.

    "Budget-Conscious Procurement Lead in IT" -> "budget-conscious-procurement-lead-in-it"
    """
    return _NON_ALNUM.sub("-", name.strip().lower()).strip("-")


@lru_cache(maxsize=1)
def load_prompts() -> dict[str, str]:
    """Parse the prompts file into `{persona_key: prompt_text}`.

    Cached for the life of the worker process — the file is read once, on the
    first reaction batch. Restart the worker after editing it.
    """
    text = PROMPTS_PATH.read_text(encoding="utf-8")

    prompts: dict[str, str] = {}
    name: str | None = None
    body: list[str] = []

    def flush() -> None:
        if name and body:
            prompts[persona_key(name)] = "\n".join(body).strip()

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if _SEPARATOR.match(line):
            # A separator ends the current block and arms the next one.
            flush()
            name, body = None, []
            continue
        if name is None:
            # First non-empty line after a separator is the persona name.
            if line.strip():
                name = line.strip()
            continue
        body.append(line)
    flush()

    if not prompts:
        raise ValueError(f"no persona prompts parsed from {PROMPTS_PATH}")
    return prompts


# --------------------------------------------------------------------------
# Hardcoded avatar_id -> persona mapping
# --------------------------------------------------------------------------
# These UUIDs are the ones apps/api/scripts/seed_hardcoded_run.sql inserts
# into core.avatars. They are fixed literals on purpose: the SQL seed, this
# mapping, and the `avatar_ids` list inside runs.runs.config_snapshot all have
# to agree, and a hand-readable id (…-000000000001) makes that checkable by
# eye. Replacing this with a lookup table (core.avatars.prompt_key) is the
# natural next step once personas are managed through the API.
AVATAR_ID_TO_PERSONA: dict[str, str] = {
    # ---- Healthcare personas (used by the JNJ-5322 seed study) ----
    "a0000000-0000-0000-0000-000000000001": "academic-oncologist",
    "a0000000-0000-0000-0000-000000000002": "community-oncologist",
    "a0000000-0000-0000-0000-000000000003": "medical-director",
    "a0000000-0000-0000-0000-000000000004": "evidence-driven-specialist",
    # ---- B2B / IT personas (available, not in the seed study) ----
    "a0000000-0000-0000-0000-000000000005": "pragmatic-it-director",
    "a0000000-0000-0000-0000-000000000006": "security-first-ciso",
    "a0000000-0000-0000-0000-000000000007": "innovation-seeking-cto",
    "a0000000-0000-0000-0000-000000000008": "budget-conscious-procurement-lead-in-it",
    "a0000000-0000-0000-0000-000000000009": "roi-focused-economic-buyer",
    "a0000000-0000-0000-0000-00000000000a": "skeptical-evaluator",
    # ---- Consumer personas ----
    "a0000000-0000-0000-0000-00000000000b": "health-conscious-parent",
    "a0000000-0000-0000-0000-00000000000c": "growth-oriented-investor",
}

#: Fallback when an avatar_id has no entry in the mapping above. Keeps a run
#: moving (with a warning) instead of failing a whole batch on one unmapped
#: avatar — every reaction still gets a coherent persona voice.
DEFAULT_AVATAR_ID = "a0000000-0000-0000-0000-000000000002"  # community-oncologist


def prompt_for_avatar(avatar_id: str, *, fallback_name: str | None = None) -> str:
    """Resolve the system prompt for one avatar id.

    Resolution order:
      1. `AVATAR_ID_TO_PERSONA[avatar_id]` -> that persona's prompt.
      2. `fallback_name` (normally `core.avatars.name`) parsed as a persona
         name — lets a DB-seeded avatar work without touching this mapping,
         as long as its name matches a block in the prompts file.
      3. `DEFAULT_AVATAR_ID`'s persona.
    """
    prompts = load_prompts()
    key = AVATAR_ID_TO_PERSONA.get(str(avatar_id).lower())

    if key is None and fallback_name:
        candidate = persona_key(fallback_name)
        if candidate in prompts:
            key = candidate

    if key is None:
        key = AVATAR_ID_TO_PERSONA[DEFAULT_AVATAR_ID]

    return prompts.get(key) or prompts[AVATAR_ID_TO_PERSONA[DEFAULT_AVATAR_ID]]


def available_personas() -> list[str]:
    """Persona keys found in the prompts file — handy for a startup log line
    or a quick `python -m app.avatar_prompts` sanity check."""
    return sorted(load_prompts())


if __name__ == "__main__":  # pragma: no cover — manual check
    for key in available_personas():
        body = load_prompts()[key]
        print(f"{key:45s} {len(body):5d} chars  | {body.splitlines()[0][:60]}…")
