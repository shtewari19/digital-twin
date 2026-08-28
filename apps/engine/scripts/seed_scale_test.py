"""Seed apps/engine/fixtures/scale_test.yaml into the DB, at either scale.

Usage (from apps/engine/, with .env configured — see app/config.py):
    python scripts/seed_scale_test.py --scale small   # 3 personas x 3 messages x 2 reps, cheap dry run
    python scripts/seed_scale_test.py --scale full    # 50 personas x 15 messages x 20 reps = 1000 avatars

Then, to actually run it (apps/api + apps/engine worker + docker compose up
already running):
    curl -X POST http://localhost:8000/api/v1/studies/{study_id}/runs
    python scripts/seed_scale_test.py --set-run-config {run_id}   # writes penalties
    curl -X POST http://localhost:8000/api/v1/runs/{run_id}/start

Idempotent: every row uses a UUID deterministically derived from stable
fixture content (domain/study/persona/message text) plus ON CONFLICT DO
NOTHING, so re-running with the same fixture is a no-op after the first
time — same convention as apps/api/scripts/seed_dev_data.py.

Assumes apps/api/scripts/seed_dev_data.py has already been run at least
once against this DB — studies.owner_id references that seeded dev user.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path

import asyncpg
import yaml

from app.config import settings

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "scale_test.yaml"

# Matches apps/api/app/core/config.py Settings.dev_user_id's default — the
# fixed dev user apps/api/scripts/seed_dev_data.py seeds. studies.owner_id
# is NOT NULL REFERENCES core.users(id), so this must exist first.
DEV_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

# Fixed namespace so every id this script generates is stable across runs —
# uuid5(NAMESPACE, some stable string) is deterministic, unlike uuid4.
_NAMESPACE = uuid.UUID("6f6f6f6f-0000-0000-0000-00000000aa00")


def det_uuid(*parts: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, "|".join(parts))


def load_fixture() -> dict:
    with FIXTURE_PATH.open() as f:
        return yaml.safe_load(f)


async def seed(scale: str) -> None:
    fixture = load_fixture()

    library = fixture["personas"]["library"]
    all_messages = fixture["messages"]
    if scale == "small":
        personas = library[:3]
        messages = all_messages[:3]
        reps = 2
    else:
        personas = library
        messages = all_messages
        reps = fixture["personas"]["respondents_per_persona"]

    domain_id = det_uuid("domain", fixture["domain"]["name"])
    study_id = det_uuid("study", fixture["study"]["name"])

    conn = await asyncpg.connect(settings.asyncpg_dsn)
    try:
        d = fixture["domain"]
        await conn.execute(
            """INSERT INTO core.domains (id, name, type, description, compliance_profile)
               VALUES ($1, $2, $3, $4, $5) ON CONFLICT (id) DO NOTHING""",
            domain_id, d["name"], d["type"], d.get("description"), d["compliance_profile"],
        )

        s = fixture["study"]
        await conn.execute(
            """INSERT INTO core.studies
                   (id, domain_id, owner_id, name, description, outcome_dimension,
                    scale_min, scale_max, status)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'ready')
               ON CONFLICT (id) DO NOTHING""",
            study_id, domain_id, DEV_USER_ID, s["name"], s.get("description"),
            s["outcome_dimension"], s["scale_min"], s["scale_max"],
        )

        anchor_rows = [
            (det_uuid("anchor", str(study_id), str(a["scale_point"])), study_id,
             a["scale_point"], a["text"])
            for a in fixture["anchors"]
        ]
        await conn.executemany(
            """INSERT INTO core.anchors (id, scope_type, scope_id, scale_point, text)
               VALUES ($1, 'study', $2, $3, $4) ON CONFLICT (id) DO NOTHING""",
            anchor_rows,
        )

        message_rows = [
            (det_uuid("message", str(study_id), m["text"]), study_id, m["text"], i)
            for i, m in enumerate(messages)
        ]
        await conn.executemany(
            """INSERT INTO core.messages (id, study_id, text, position)
               VALUES ($1, $2, $3, $4) ON CONFLICT (id) DO NOTHING""",
            message_rows,
        )

        avatar_rows = []
        for persona in personas:
            for rep in range(1, reps + 1):
                name = f"{persona['name']} #{rep:02d}"
                avatar_id = det_uuid("avatar", str(study_id), persona["name"], str(rep))
                avatar_rows.append((avatar_id, study_id, name, persona["profile"]))
        await conn.executemany(
            """INSERT INTO core.avatars (id, scope, study_id, name, profile, source)
               VALUES ($1, 'study', $2, $3, $4, 'custom') ON CONFLICT (id) DO NOTHING""",
            avatar_rows,
        )
        await conn.executemany(
            """INSERT INTO core.study_avatars (study_id, avatar_id)
               VALUES ($1, $2) ON CONFLICT (study_id, avatar_id) DO NOTHING""",
            [(study_id, row[0]) for row in avatar_rows],
        )
    finally:
        await conn.close()

    n_pairs = len(avatar_rows) * len(message_rows)
    print(f"Seeded scale='{scale}': domain={domain_id}")
    print(f"  study={study_id}")
    print(f"  {len(anchor_rows)} anchors, {len(message_rows)} messages, "
          f"{len(avatar_rows)} avatars ({len(personas)} personas x {reps} reps)")
    print(f"  -> {n_pairs} avatar/message pairs = {n_pairs} reactions this run will generate")
    print()
    print("Next steps:")
    print(f"  curl -X POST http://localhost:8000/api/v1/studies/{study_id}/runs")
    print("  # ^ note the returned run id, then:")
    print("  python scripts/seed_scale_test.py --set-run-config <run_id>")
    print("  curl -X POST http://localhost:8000/api/v1/runs/<run_id>/start")


async def set_run_config(run_id: str) -> None:
    """Write this fixture's penalties into an existing run's config_snapshot
    — there's no API route for this yet (out of scope here, see the plan),
    so it's a direct DB write. Must run after POST .../runs, before
    POST .../start (the workflow reads config_snapshot on its first hop)."""
    fixture = load_fixture()
    config = {"penalties": fixture["penalties"]}

    conn = await asyncpg.connect(settings.asyncpg_dsn)
    try:
        result = await conn.execute(
            "UPDATE runs.runs SET config_snapshot = $2::jsonb WHERE id = $1",
            uuid.UUID(run_id), json.dumps(config),
        )
    finally:
        await conn.close()

    if result == "UPDATE 0":
        raise SystemExit(f"no run found with id {run_id} — create it first via POST .../runs")
    print(f"run {run_id}: config_snapshot set with {len(fixture['penalties'])} penalties")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--scale", choices=["small", "full"],
        help="Seed domain/study/anchors/messages/avatars. 'small' first for a cheap dry run.",
    )
    group.add_argument(
        "--set-run-config", metavar="RUN_ID",
        help="Write this fixture's penalties into an existing run's config_snapshot.",
    )
    args = parser.parse_args()

    if args.scale:
        asyncio.run(seed(args.scale))
    else:
        asyncio.run(set_run_config(args.set_run_config))


if __name__ == "__main__":
    main()
