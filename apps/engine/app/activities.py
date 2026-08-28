"""Activities: the only place in the engine that does I/O or non-deterministic
work. StudyDataActivities holds the shared asyncpg pool (created once in
worker.py) and owns every read/write against runs.runs, runs.run_reactions,
runs.run_message_results, and runs.run_reports."""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime

import asyncpg
import numpy as np
from temporalio import activity

from app import llm
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
class Penalty:
    trigger: str
    adjustment: float
    reason: str


@dataclass
class StudyContext:
    kbq: str
    anchors: AnchorSet
    pairs: list[Pair]
    penalties: list[Penalty] = field(default_factory=list)


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
class GenerateReactionBatchInput:
    pairs: list[Pair]
    kbq: str


@dataclass
class ApplyPenaltiesBatchInput:
    texts: list[str]
    scores: list[ScoreResult]
    penalties: list[Penalty]


@dataclass
class PenaltyResult:
    final_score: float
    penalty: float
    triggered: list[str]


@dataclass
class ReactionRow:
    avatar_id: str
    message_id: str
    score: float | None
    distribution: list[float] | None
    status: str  # "ok" | "failed"
    penalty: float | None = None
    text: str | None = None


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


def _apply_penalties(mean_ssr: float, penalties: list[Penalty], text: str) -> tuple[float, float, list[str]]:
    """Scan free-text for trigger phrases; each hit adds its adjustment to
    the base mean SSR. Ported from the POC's apply_penalties()."""
    lowered = text.lower()
    penalty_sum = 0.0
    triggered: list[str] = []
    for p in penalties:
        if p.trigger.lower() in lowered:
            penalty_sum += p.adjustment
            triggered.append(p.reason)
    return round(mean_ssr + penalty_sum, 2), round(penalty_sum, 2), triggered


def _bradley_terry(n_items: int, wins_matrix: list[list[float]]) -> list[float]:
    """500-iteration minorization-maximization fit, ported from the POC's
    bradley_terry(). Returns per-item strengths that sum to 1."""
    strengths = np.ones(n_items)
    for _ in range(500):
        new_s = np.zeros(n_items)
        for i in range(n_items):
            num = sum(wins_matrix[i][j] for j in range(n_items) if j != i)
            den = sum(
                (wins_matrix[i][j] + wins_matrix[j][i]) / (strengths[i] + strengths[j])
                for j in range(n_items)
                if j != i and (strengths[i] + strengths[j]) > 0
            )
            new_s[i] = num / den if den > 0 else 0
        total = sum(new_s)
        strengths = new_s / total if total > 0 else new_s
    return [float(s) for s in strengths]


_RESPONDENT_SUFFIX = re.compile(r"\s+#\d+$")


def _persona_family(avatar_name: str) -> str:
    """Strips the " #NN" respondent-index suffix seed_scale_test.py gives
    each cloned avatar row, recovering which persona family it belongs to."""
    return _RESPONDENT_SUFFIX.sub("", avatar_name)


def _parse_dt(value: str | None) -> datetime | None:
    """Activity inputs cross the Temporal boundary as ISO strings (Temporal's
    default data converter is JSON-based, no native datetime type), but
    asyncpg needs a real datetime object to encode a timestamptz parameter —
    it does not accept strings even with an explicit ::cast in the SQL."""
    return datetime.fromisoformat(value) if value else None


# --------------------------------------------------- stateless activities ---
# Sync/blocking on purpose (requests/openai + numpy) — run on worker.py's
# activity_executor thread pool, not the asyncio event loop.

@activity.defn
def embed_batch(input: EmbedBatchInput) -> list[list[float]]:
    import requests

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


def _physician_prompt(kbq: str, message_text: str) -> str:
    return (
        f'You have been presented with this claim:\n\n'
        f'CLAIM: "{message_text}"\n\n'
        f"Key Belief Question: {kbq}\n\n"
        "Respond in your own voice as this persona. Write 3-5 sentences giving "
        "your genuine perspective on how compelling this message is and why. "
        "Be specific about what resonates or doesn't resonate based on your "
        "background and experience. Do NOT give a numeric rating — write as "
        "if you are in a market research interview."
    )


@activity.defn
def generate_reaction_batch(input: GenerateReactionBatchInput) -> list[str]:
    """One chat completion per (avatar, message) pair — real LLM call,
    replacing the earlier canned-string stub. Fanned out across a thread
    pool: at a few thousand pairs per run, serial calls would take hours."""
    results: list[str | None] = [None] * len(input.pairs)

    def _one(pair: Pair) -> str:
        return llm.call_chat(
            pair.avatar_profile, _physician_prompt(input.kbq, pair.message_text), max_tokens=300
        )

    with ThreadPoolExecutor(max_workers=settings.reaction_concurrency) as pool:
        futures = {pool.submit(_one, pair): i for i, pair in enumerate(input.pairs)}
        for done, future in enumerate(as_completed(futures), start=1):
            i = futures[future]
            results[i] = future.result()
            if done % 25 == 0 or done == len(input.pairs):
                activity.heartbeat(f"{done}/{len(input.pairs)} reactions generated")

    return results  # type: ignore[return-value]


@activity.defn
def apply_penalties_batch(input: ApplyPenaltiesBatchInput) -> list[PenaltyResult]:
    return [
        PenaltyResult(*_apply_penalties(score.mean_ssr, input.penalties, text))
        for text, score in zip(input.texts, input.scores)
    ]


# ------------------------------------------------------ DB-bound activities ---

class StudyDataActivities:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @activity.defn
    async def fetch_study_context(self, run_id: str, study_id: str) -> StudyContext:
        async with self._pool.acquire() as conn:
            study = await conn.fetchrow(
                "SELECT domain_id, outcome_dimension FROM core.studies WHERE id = $1", study_id
            )
            if study is None:
                raise ValueError(f"study {study_id} not found")

            run_row = await conn.fetchrow(
                "SELECT config_snapshot FROM runs.runs WHERE id = $1", run_id
            )
            config = (run_row["config_snapshot"] if run_row else None) or {}
            if isinstance(config, str):
                config = json.loads(config)
            penalties = [
                Penalty(trigger=p["trigger"], adjustment=float(p["adjustment"]), reason=p["reason"])
                for p in config.get("penalties", [])
            ]

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
            kbq=study["outcome_dimension"] or "",
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
            penalties=penalties,
        )

    @activity.defn
    async def persist_reactions(self, run_id: str, rows: list[ReactionRow]) -> None:
        """Idempotent — Temporal may retry this after a partial failure, so
        re-running with the same rows must not duplicate/corrupt data."""
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """INSERT INTO runs.run_reactions
                       (run_id, avatar_id, message_id, score, distribution, penalty, reaction, status)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                   ON CONFLICT (run_id, avatar_id, message_id)
                   DO UPDATE SET score = EXCLUDED.score,
                                 distribution = EXCLUDED.distribution,
                                 penalty = EXCLUDED.penalty,
                                 reaction = EXCLUDED.reaction,
                                 status = EXCLUDED.status,
                                 updated_at = now()""",
                [
                    (run_id, r.avatar_id, r.message_id, r.score,
                     json.dumps(r.distribution) if r.distribution else None, r.penalty, r.text, r.status)
                    for r in rows
                ],
            )

    @activity.defn
    async def rollup_message_results(self, run_id: str) -> None:
        """Bradley-Terry ranking, derived from independently-scored
        reactions: for each avatar, every pair of messages it reacted to is
        one pairwise comparison (lower final score wins). This is
        mathematically equivalent to the POC's literal per-pair
        regeneration — the reaction prompt never references the opposing
        claim, so re-generating it per pair is just re-sampling the same
        distribution — and it's O(N) LLM calls instead of O(N^2)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT avatar_id, message_id, score
                   FROM runs.run_reactions
                   WHERE run_id = $1 AND status = 'ok' AND score IS NOT NULL""",
                run_id,
            )
            if not rows:
                return

            message_ids = sorted({r["message_id"] for r in rows}, key=str)
            idx = {mid: i for i, mid in enumerate(message_ids)}
            n = len(message_ids)

            by_avatar: dict = {}
            for r in rows:
                by_avatar.setdefault(r["avatar_id"], {})[r["message_id"]] = float(r["score"])

            wins = [[0.0] * n for _ in range(n)]
            score_sums = [0.0] * n
            score_counts = [0] * n
            for scores in by_avatar.values():
                for mid, s in scores.items():
                    score_sums[idx[mid]] += s
                    score_counts[idx[mid]] += 1
                ids = list(scores.keys())
                for a in range(len(ids)):
                    for b in range(a + 1, len(ids)):
                        i, j = idx[ids[a]], idx[ids[b]]
                        if scores[ids[a]] < scores[ids[b]]:
                            wins[i][j] += 1
                        elif scores[ids[b]] < scores[ids[a]]:
                            wins[j][i] += 1
                        else:
                            wins[i][j] += 0.5
                            wins[j][i] += 0.5

            strengths = _bradley_terry(n, wins)
            ranked = sorted(range(n), key=lambda i: strengths[i], reverse=True)

            for rank, i in enumerate(ranked, start=1):
                recommendation = (
                    "recommended" if rank == 1 else ("runner_up" if rank <= max(2, n // 3) else "drop")
                )
                aggregate_score = score_sums[i] / score_counts[i] if score_counts[i] else None
                await conn.execute(
                    """INSERT INTO runs.run_message_results
                           (run_id, message_id, aggregate_score, bt_strength, rank, recommendation)
                       VALUES ($1, $2, $3, $4, $5, $6)
                       ON CONFLICT (run_id, message_id)
                       DO UPDATE SET aggregate_score = EXCLUDED.aggregate_score,
                                     bt_strength = EXCLUDED.bt_strength,
                                     rank = EXCLUDED.rank,
                                     recommendation = EXCLUDED.recommendation,
                                     updated_at = now()""",
                    run_id, message_ids[i], aggregate_score, strengths[i], rank, recommendation,
                )

    @activity.defn
    async def generate_run_report(self, run_id: str) -> None:
        """Two more LLM calls (executive summary + interpretation), ported
        from the POC's generate_executive_summary()/generate_interpretation().
        Runs once, after rollup_message_results has written the ranking."""
        async with self._pool.acquire() as conn:
            study_row = await conn.fetchrow(
                """SELECT s.outcome_dimension AS kbq
                   FROM runs.runs r JOIN core.studies s ON s.id = r.study_id
                   WHERE r.id = $1""",
                run_id,
            )
            ranking_rows = await conn.fetch(
                """SELECT rmr.rank, rmr.bt_strength, rmr.aggregate_score,
                          m.id AS message_id, m.text AS message_text
                   FROM runs.run_message_results rmr
                   JOIN core.messages m ON m.id = rmr.message_id
                   WHERE rmr.run_id = $1
                   ORDER BY rmr.rank""",
                run_id,
            )
            if not ranking_rows or study_row is None:
                return

            reaction_rows = await conn.fetch(
                """SELECT rr.avatar_id, rr.message_id, rr.score, a.name AS avatar_name
                   FROM runs.run_reactions rr
                   JOIN core.avatars a ON a.id = rr.avatar_id
                   WHERE rr.run_id = $1 AND rr.status = 'ok' AND rr.score IS NOT NULL""",
                run_id,
            )

        rankings = [
            {
                "rank": r["rank"],
                "text": r["message_text"],
                "bt_strength": float(r["bt_strength"] or 0),
                "aggregate_score": float(r["aggregate_score"] or 0),
            }
            for r in ranking_rows
        ]
        message_text_by_id = {r["message_id"]: r["message_text"] for r in ranking_rows}

        by_avatar: dict = {}
        avatar_family: dict = {}
        for r in reaction_rows:
            by_avatar.setdefault(r["avatar_id"], {})[r["message_id"]] = float(r["score"])
            avatar_family[r["avatar_id"]] = _persona_family(r["avatar_name"])

        family_message_scores: dict = {}
        for avatar_id, scores in by_avatar.items():
            fam = family_message_scores.setdefault(avatar_family[avatar_id], {})
            for mid, s in scores.items():
                fam.setdefault(mid, []).append(s)

        cohort_breakdown = {}
        for family, msg_scores in family_message_scores.items():
            avg_by_msg = {mid: sum(v) / len(v) for mid, v in msg_scores.items()}
            ordered = sorted(avg_by_msg.items(), key=lambda kv: kv[1])  # ascending = most compelling first
            cohort_breakdown[family] = [
                {
                    "text": message_text_by_id.get(mid, ""),
                    "avg_score": round(score, 2),
                    "preference_rank": i + 1,
                }
                for i, (mid, score) in enumerate(ordered)
            ]

        exec_summary = llm.call_chat(
            "You are a senior market research analyst writing an executive summary.",
            _exec_summary_prompt(rankings, study_row["kbq"] or "", len(by_avatar)),
            max_tokens=800,
        )
        interpretation = llm.call_chat(
            "You are a market research analyst.",
            _interpretation_prompt(rankings, cohort_breakdown),
            max_tokens=600,
        )
        report_md = f"## Executive Summary\n\n{exec_summary}\n\n## Interpretation\n\n{interpretation}"

        winner_strength = rankings[0]["bt_strength"]
        lowest_strength = rankings[-1]["bt_strength"]
        baseline_lift_pct = (
            round((winner_strength - lowest_strength) / lowest_strength * 100, 2)
            if lowest_strength > 0 else None
        )

        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO runs.run_reports (run_id, report, baseline_lift_pct, summary)
                   VALUES ($1, $2, $3, $4::jsonb)
                   ON CONFLICT (run_id)
                   DO UPDATE SET report = EXCLUDED.report,
                                 baseline_lift_pct = EXCLUDED.baseline_lift_pct,
                                 summary = EXCLUDED.summary,
                                 updated_at = now()""",
                run_id, report_md, baseline_lift_pct,
                json.dumps({"cohort_breakdown": cohort_breakdown}, default=str),
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


def _exec_summary_prompt(rankings: list[dict], kbq: str, n_respondents: int) -> str:
    ranked_text = "\n".join(
        f"Rank {r['rank']}: \"{r['text'][:80]}\" — BT strength: {r['bt_strength']:.3f}, "
        f"avg score: {r['aggregate_score']:.2f}"
        for r in rankings
    )
    return f"""Generate a professional SSR concept-testing executive summary.

Key Belief Question: {kbq}
Total synthetic respondents: {n_respondents}
Messages tested: {len(rankings)}

MESSAGE RANKINGS (Bradley-Terry tournament results):
{ranked_text}

Write with:
1. One-sentence study objective
2. 3-4 headline findings with "So what?" implications (start each with "•")
3. Key insight about the winning vs losing messages
4. One strategic recommendation

Use professional market research language. Be specific with the numbers. Keep it concise."""


def _interpretation_prompt(rankings: list[dict], cohort_breakdown: dict) -> str:
    winner, loser = rankings[0], rankings[-1]
    cohort_text = json.dumps(cohort_breakdown, indent=2, default=str)
    return f"""Write a detailed interpretation section for an SSR concept-testing study.

WINNING MESSAGE (Rank 1): "{winner['text']}"
- BT strength: {winner['bt_strength']:.3f}

LOWEST-RANKED MESSAGE (Rank {loser['rank']}): "{loser['text']}"
- BT strength: {loser['bt_strength']:.3f}

COHORT BREAKDOWN (by persona family, most-to-least compelling):
{cohort_text}

Write 3 paragraphs:
1. Why the winning message resonated
2. Why the lowest-ranked message underperformed
3. Differentiation across persona families

Use clear market-research language."""
