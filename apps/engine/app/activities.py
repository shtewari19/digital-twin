"""Activities: the only place in the engine that does I/O or non-deterministic
work. StudyDataActivities holds the shared asyncpg pool (created once in
worker.py) and owns every read/write against runs.runs, runs.run_reactions,
and runs.run_message_results."""

import json
from datetime import datetime

import asyncpg
import numpy as np
import requests
from dataclasses import dataclass
from temporalio import activity

from app.config import settings


# ---------------------------------------------------------------- shapes ---

@dataclass
class Pair:
    avatar_id: str
    message_id: str
    avatar_profile: str
    message_text: str


@dataclass
class AnchorSet:
    ids: list[str]
    texts: list[str]
    scale_points: list[int]


@dataclass
class StudyContext:
    anchors: AnchorSet
    pairs: list[Pair]


@dataclass
class EmbedBatchInput:
    texts: list[str]


@dataclass
class ScoreBatchInput:
    response_vectors: list[list[float]]
    anchor_vectors: list[list[float]]
    anchor_scale_points: list[int]
    delta: float = 0.02


@dataclass
class ScoreResult:
    similarities: list[float]
    pmf: list[float]
    mean_ssr: float


@dataclass
class ReactionRow:
    avatar_id: str
    message_id: str
    score: float | None
    distribution: list[float] | None
    status: str  # "ok" | "failed"


@dataclass
class UpdateRunStatusInput:
    run_id: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    error: dict | None = None


# ------------------------------------------------------- pure math (POC) ---

def _cosine_similarity(a, b) -> float:
    a, b = np.array(a), np.array(b)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / denom) if denom > 0 else 0.0


def _compute_pmf(response_vec, anchor_vecs, scale_points, delta):
    sims = [_cosine_similarity(response_vec, av) for av in anchor_vecs]
    min_s = min(sims)
    adjusted = [s - min_s + delta for s in sims]
    total = sum(adjusted)
    pmf = [a / total for a in adjusted]
    mean_ssr = sum(sp * p for sp, p in zip(scale_points, pmf))
    return sims, pmf, mean_ssr


def _parse_dt(value: str | None) -> datetime | None:
    """Activity inputs cross the Temporal boundary as ISO strings (Temporal's
    default data converter is JSON-based, no native datetime type), but
    asyncpg needs a real datetime object to encode a timestamptz parameter —
    it does not accept strings even with an explicit ::cast in the SQL."""
    return datetime.fromisoformat(value) if value else None


# --------------------------------------------------- stateless activities ---
# Sync/blocking on purpose (requests + numpy) — run on worker.py's
# activity_executor thread pool, not the asyncio event loop.

@activity.defn
def embed_batch(input: EmbedBatchInput) -> list[list[float]]:
    activity.heartbeat(f"embedding {len(input.texts)} texts")
    resp = requests.post(
        settings.embedding_model_endpoint, json={"texts": input.texts}, timeout=60
    )
    resp.raise_for_status()
    return resp.json()["embeddings"]


@activity.defn
def score_batch(input: ScoreBatchInput) -> list[ScoreResult]:
    results = []
    for i, rv in enumerate(input.response_vectors):
        if i % 500 == 0:
            activity.heartbeat(i)
        sims, pmf, mean_ssr = _compute_pmf(rv, input.anchor_vectors, input.anchor_scale_points, input.delta)
        results.append(ScoreResult(
            similarities=[round(s, 4) for s in sims],
            pmf=[round(p, 4) for p in pmf],
            mean_ssr=round(mean_ssr, 2),
        ))
    return results


@activity.defn
def generate_reaction_batch(pairs: list[Pair]) -> list[str]:
    """STUB. Real implementation is generate_physician_response() — one LLM
    call per (avatar, message) pair — a separate, larger ticket. Kept as its
    own Activity now (correct shape for an eventual LLM call) so swapping it
    in later is a one-function change with zero workflow edits."""
    return [
        f"[stub reaction] avatar={p.avatar_id} responding to message={p.message_id}: {p.message_text[:60]}"
        for p in pairs
    ]


# ------------------------------------------------------ DB-bound activities ---

class StudyDataActivities:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @activity.defn
    async def fetch_study_context(self, study_id: str) -> StudyContext:
        async with self._pool.acquire() as conn:
            study = await conn.fetchrow("SELECT domain_id FROM core.studies WHERE id = $1", study_id)
            if study is None:
                raise ValueError(f"study {study_id} not found")

            anchor_rows = await conn.fetch(
                """SELECT id, text, scale_point FROM core.anchors
                   WHERE scope_type = 'study' AND scope_id = $1
                   ORDER BY scale_point""",
                study_id,
            )
            if not anchor_rows:
                anchor_rows = await conn.fetch(
                    """SELECT id, text, scale_point FROM core.anchors
                       WHERE scope_type = 'domain' AND scope_id = $1
                       ORDER BY scale_point""",
                    study["domain_id"],
                )
            if not anchor_rows:
                raise ValueError(f"no anchors found for study {study_id} or its domain")

            pair_rows = await conn.fetch(
                """SELECT a.id AS avatar_id, a.profile AS avatar_profile,
                          m.id AS message_id, m.text AS message_text
                   FROM core.study_avatars sa
                   JOIN core.avatars a ON a.id = sa.avatar_id
                   JOIN core.messages m ON m.study_id = sa.study_id
                   WHERE sa.study_id = $1""",
                study_id,
            )
            if not pair_rows:
                raise ValueError(f"no avatar/message pairs found for study {study_id}")

        return StudyContext(
            anchors=AnchorSet(
                ids=[str(r["id"]) for r in anchor_rows],
                texts=[r["text"] for r in anchor_rows],
                scale_points=[r["scale_point"] for r in anchor_rows],
            ),
            pairs=[
                Pair(avatar_id=str(r["avatar_id"]), message_id=str(r["message_id"]),
                     avatar_profile=r["avatar_profile"], message_text=r["message_text"])
                for r in pair_rows
            ],
        )

    @activity.defn
    async def persist_reactions(self, run_id: str, rows: list[ReactionRow]) -> None:
        """Idempotent — Temporal may retry this after a partial failure, so
        re-running with the same rows must not duplicate/corrupt data."""
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """INSERT INTO runs.run_reactions
                       (run_id, avatar_id, message_id, score, distribution, status)
                   VALUES ($1, $2, $3, $4, $5, $6)
                   ON CONFLICT (run_id, avatar_id, message_id)
                   DO UPDATE SET score = EXCLUDED.score,
                                 distribution = EXCLUDED.distribution,
                                 status = EXCLUDED.status,
                                 updated_at = now()""",
                [
                    (run_id, r.avatar_id, r.message_id, r.score,
                     json.dumps(r.distribution) if r.distribution else None, r.status)
                    for r in rows
                ],
            )

    @activity.defn
    async def rollup_message_results(self, run_id: str) -> None:
        """Aggregate per-reaction scores into one row per message, ranked.
        bt_strength (Bradley-Terry) needs pairwise comparison modeling —
        intentionally left NULL, flagged as a follow-up ticket."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT message_id, AVG(score) AS aggregate_score
                   FROM runs.run_reactions
                   WHERE run_id = $1 AND status = 'ok'
                   GROUP BY message_id
                   ORDER BY AVG(score) DESC""",
                run_id,
            )
            n = len(rows)
            for rank, row in enumerate(rows, start=1):
                recommendation = "recommended" if rank == 1 else ("runner_up" if rank <= max(2, n // 3) else "drop")
                await conn.execute(
                    """INSERT INTO runs.run_message_results
                           (run_id, message_id, aggregate_score, rank, recommendation)
                       VALUES ($1, $2, $3, $4, $5)
                       ON CONFLICT (run_id, message_id)
                       DO UPDATE SET aggregate_score = EXCLUDED.aggregate_score,
                                     rank = EXCLUDED.rank,
                                     recommendation = EXCLUDED.recommendation,
                                     updated_at = now()""",
                    run_id, row["message_id"], float(row["aggregate_score"]), rank, recommendation,
                )

    @activity.defn
    async def update_run_status(self, input: UpdateRunStatusInput) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE runs.runs
                   SET status = $2,
                       started_at = COALESCE($3, started_at),
                       finished_at = COALESCE($4, finished_at),
                       error = COALESCE($5::jsonb, error),
                       updated_at = now()
                   WHERE id = $1""",
                input.run_id, input.status,
                _parse_dt(input.started_at), _parse_dt(input.finished_at),
                json.dumps(input.error) if input.error else None,
            )