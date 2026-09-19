"""Activities: the only place in the engine that does I/O or non-deterministic
work. StudyDataActivities holds the shared asyncpg pool (created once in
worker.py) and owns every read/write against runs.runs, runs.run_reactions,
runs.run_message_results, and runs.run_reports."""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime

import asyncpg
import numpy as np
from temporalio import activity

from app import avatar_prompts, llm
from app.config import settings

# ---------------------------------------------------------------- shapes ---

@dataclass
class Pair:
    """One unit of work: respondent R of persona `avatar_id` reacting to
    claim `message_id`.

    An avatar is a *persona* (an archetype like "Academic Oncologist"), not a
    person. A run panels `respondents_per_avatar` respondents of each persona,
    so `respondent` (1..N) is what distinguishes them — they share the same
    prompt but are sampled independently, which is what makes a larger panel
    produce a more stable ranking.

    Deliberately does NOT carry the persona prompt. Prompts are ~2.5 KB and a
    Pair crosses the Temporal boundary; inlining one per pair put a 6,000-pair
    run at ~17 MB of workflow payload, far past Temporal's 2 MB limit.
    `generate_reaction_batch` resolves the prompt from `avatar_prompts` (a
    file read, so a stateless activity can do it) once per avatar per batch."""

    avatar_id: str
    message_id: str
    message_text: str
    respondent: int = 1


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
    """Everything the run executes against — as SOURCES, not a pair list.

    The workflow carries this across `continue_as_new`, so its size has to be
    independent of the panel size. Four personas, five claims and ten penalties
    is a few KB whether the run is 100 reactions or 100,000; materialising the
    cross product here instead would put a large run past Temporal's payload
    limit. `pairs_for_slice()` expands it on demand, one batch at a time.
    """

    kbq: str
    anchors: AnchorSet
    avatar_ids: list[str]
    #: [(message_id, text)] — the claims, in a fixed order that the index
    #: arithmetic in `pairs_for_slice` depends on.
    claims: list[tuple[str, str]]
    penalties: list[Penalty] = field(default_factory=list)
    respondents_per_avatar: int = 1

    @property
    def total_reactions(self) -> int:
        return len(self.avatar_ids) * self.respondents_per_avatar * len(self.claims)


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
class ReactionResult:
    """One generated reaction, or the record of why it could not be generated.

    Per-reaction rather than per-batch so a single bad LLM call does not throw
    away the other 49 — at a few thousand reactions that difference is hours of
    re-work and re-spend. A failed reaction still occupies its slot, so every
    downstream list stays positionally aligned with the batch.
    """

    text: str
    ok: bool = True
    error: str | None = None


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
    #: 1..N — part of runs.run_reactions' unique key, so respondent 2 of a
    #: persona is a distinct row rather than an overwrite of respondent 1.
    respondent: int = 1


@dataclass
class UpdateRunStatusInput:
    run_id: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    error: dict | None = None
    #: Percentage of the run's avatar x message pairs that produced a
    #: reaction. Written alongside the status so runs.runs.coverage_pct (which
    #: GET /runs/{id} returns) never lags the status the API reports.
    coverage_pct: float | None = None


def pairs_for_slice(
    avatar_ids: list[str],
    claims: list[tuple[str, str]],
    respondents_per_avatar: int,
    start: int,
    count: int,
) -> list[Pair]:
    """Expand reactions [start, start+count) of the cross product.

    Pure index arithmetic — no I/O, no clock, no randomness — so the workflow
    can call it directly and it replays identically. This is what keeps the
    workflow's carried input constant-size: a 6,000-reaction run holds the same
    few KB of avatar ids and claims as a 100-reaction one, and only ever
    materialises `batch_size` Pairs at a time.

    The ordering is `for avatar, for respondent, for claim`, which must match
    what the rest of the pipeline assumes — the workflow zips a batch against
    its scores positionally.
    """
    per_respondent = len(claims)
    per_avatar = respondents_per_avatar * per_respondent
    total = len(avatar_ids) * per_avatar

    pairs: list[Pair] = []
    for i in range(start, min(start + count, total)):
        avatar_idx, rem = divmod(i, per_avatar)
        respondent, claim_idx = divmod(rem, per_respondent)
        claim_id, claim_text = claims[claim_idx]
        pairs.append(
            Pair(
                avatar_id=avatar_ids[avatar_idx],
                message_id=claim_id,
                message_text=claim_text,
                respondent=respondent + 1,
            )
        )
    return pairs


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
    mean_ssr = sum(sp * p for sp, p in zip(scale_points, pmf, strict=True))
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
    """Recovers the persona family an avatar belongs to, for the report's
    cohort breakdown.

    Strips a trailing " #NN" respondent-index suffix, so twenty cloned
    `core.avatars` rows of one persona ("Community Oncologist #01" ..
    "#20") collapse back into a single cohort. Avatars seeded without a
    suffix are their own cohort, which is the normal case now."""
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

def _embedding_chunks(texts: list[str]) -> list[list[str]]:
    """Split `texts` into request-sized chunks, capped on BYTES first.

    The embedding service's ceiling is the request's total size, not its item
    count — measured at 200 for ~25 KB and 500 for ~30 KB. Chunking on count
    alone silently breaks as soon as reactions get longer, which is exactly
    what happened when the panel grew. A single text over the cap is still sent
    on its own: it cannot be split, and the service may well accept it.
    """
    max_items = max(1, settings.embedding_batch_size)
    max_bytes = max(1, settings.embedding_max_request_bytes)

    chunks: list[list[str]] = []
    current: list[str] = []
    current_bytes = 0
    for text in texts:
        size = len(text.encode("utf-8"))
        if current and (len(current) >= max_items or current_bytes + size > max_bytes):
            chunks.append(current)
            current, current_bytes = [], 0
        current.append(text)
        current_bytes += size
    if current:
        chunks.append(current)
    return chunks


@activity.defn
def embed_batch(input: EmbedBatchInput) -> list[list[float]]:
    """Embed every text, chunking the HTTP requests.

    The embedding service has a per-request ceiling on total payload size
    (~25 KB ok, ~30 KB returns 500), so the whole list cannot be sent at once.
    That limit is invisible on a small panel — a 20-reaction run fits in one
    request — and fatal on a large one, which is why this chunks. See
    `_embedding_chunks` for why the cap is on bytes rather than item count.

    Order is preserved across chunks — the caller zips these vectors against
    its texts positionally, so that is load-bearing.
    """
    import requests

    vectors: list[list[float]] = []
    done = 0
    for chunk in _embedding_chunks(input.texts):
        done += len(chunk)
        activity.heartbeat(f"embedding {done}/{len(input.texts)} texts")
        resp = requests.post(
            settings.embedding_model_endpoint, json={"texts": chunk}, timeout=60
        )
        resp.raise_for_status()
        vectors.extend(resp.json()["embeddings"])

    if len(vectors) != len(input.texts):
        raise ValueError(
            f"embedding service returned {len(vectors)} vectors for {len(input.texts)} "
            "texts — refusing to continue, the caller zips these positionally"
        )
    return vectors


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


def _reaction_prompt(kbq: str, message_text: str) -> str:
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


#: If more than this fraction of a batch fails, the cause is systemic (bad
#: credentials, the deployment gone, a hard rate limit) rather than a few
#: unlucky calls — raise so Temporal retries the batch instead of persisting a
#: wall of failures and moving on.
_MAX_BATCH_FAILURE_RATIO = 0.5


@activity.defn
def generate_reaction_batch(input: GenerateReactionBatchInput) -> list[ReactionResult]:
    """One chat completion per (avatar, claim, respondent) — a real LLM call.

    Fanned out across a thread pool: at a few thousand reactions per run,
    serial calls would take hours.

    Individual failures are captured, not raised. A transient 429 or content
    filter on one reaction marks that one `ok=False` and leaves the rest
    intact; the workflow persists it with `status='failed'` and the rollup
    ignores it. Only a systemic failure (more than half the batch) raises, so
    Temporal's retry covers the case that is actually worth retrying.
    """
    results: list[ReactionResult | None] = [None] * len(input.pairs)

    # Resolve each persona's prompt ONCE per batch rather than carrying a copy
    # on every Pair — the prompts are ~2.5 KB and would otherwise dominate the
    # Temporal payload. `prompt_for_avatar` reads a cached file, so this is
    # safe in a stateless activity with no DB pool.
    prompts = {aid: avatar_prompts.prompt_for_avatar(aid) for aid in {p.avatar_id for p in input.pairs}}

    def _one(pair: Pair) -> ReactionResult:
        try:
            text = llm.call_chat(
                prompts[pair.avatar_id],
                _reaction_prompt(input.kbq, pair.message_text),
                max_tokens=300,
            )
        except Exception as exc:  # one bad call must not sink the batch
            return ReactionResult(text="", ok=False, error=f"{type(exc).__name__}: {exc}"[:500])
        if not text.strip():
            return ReactionResult(text="", ok=False, error="empty completion")
        return ReactionResult(text=text)

    with ThreadPoolExecutor(max_workers=settings.reaction_concurrency) as pool:
        futures = {pool.submit(_one, pair): i for i, pair in enumerate(input.pairs)}
        for done, future in enumerate(as_completed(futures), start=1):
            i = futures[future]
            results[i] = future.result()
            if done % 25 == 0 or done == len(input.pairs):
                activity.heartbeat(f"{done}/{len(input.pairs)} reactions generated")

    out: list[ReactionResult] = results  # type: ignore[assignment]
    failed = [r for r in out if not r.ok]
    if failed and len(failed) > len(out) * _MAX_BATCH_FAILURE_RATIO:
        raise RuntimeError(
            f"{len(failed)}/{len(out)} reactions failed to generate — treating as systemic. "
            f"First error: {failed[0].error}"
        )
    if failed:
        activity.logger.warning(
            "%s/%s reactions failed and will be stored as status='failed'. First: %s",
            len(failed), len(out), failed[0].error,
        )
    return out


@activity.defn
def apply_penalties_batch(input: ApplyPenaltiesBatchInput) -> list[PenaltyResult]:
    return [
        PenaltyResult(*_apply_penalties(score.mean_ssr, input.penalties, text))
        for text, score in zip(input.texts, input.scores, strict=True)
    ]


# ------------------------------------------- config_snapshot readers ---
# Each returns None when the snapshot does not carry the key at all, which
# is the signal for fetch_study_context to fall back to the core.* tables.
# An explicitly EMPTY list is not None — it means "this run has none".


def _anchors_from_config(config: dict) -> AnchorSet | None:
    """`config["anchors"]` -> AnchorSet, sorted by scale_point.

    Accepts either a list of objects (`{"scale_point": 1, "text": "..."}`) or
    a bare list of strings, in which case scale points are implied 1..N —
    the order in the file is the scale, which is how the POC expressed it.
    """
    raw = config.get("anchors")
    if raw is None:
        return None
    if not raw:
        raise ValueError("config_snapshot.anchors is empty — scoring needs at least 2 anchors")

    if isinstance(raw[0], str):
        entries = [{"scale_point": i, "text": t} for i, t in enumerate(raw, start=1)]
    else:
        entries = list(raw)

    entries.sort(key=lambda a: int(a["scale_point"]))
    return AnchorSet(
        ids=[str(a.get("id") or a["scale_point"]) for a in entries],
        texts=[a["text"] for a in entries],
        scale_points=[int(a["scale_point"]) for a in entries],
    )


def _claims_from_config(config: dict) -> list[tuple[str, str]] | None:
    """`config["claims"]` -> [(message_id, text)].

    `id` must be a real `core.messages.id`: runs.run_reactions.message_id is a
    foreign key onto it, so a claim invented only in the snapshot would fail
    on persist. `message_ids` (what the API's snapshot builder writes today,
    ids only, no text) is accepted as an alias and resolved from the DB
    instead — returning None here sends fetch_study_context down that path.
    """
    raw = config.get("claims") or config.get("messages")
    if raw is None:
        return None
    if isinstance(raw[0], str):
        # A bare id list carries no text; let the DB fallback supply both.
        return None
    return [(str(c["id"]), c["text"]) for c in raw]


def _respondents_per_avatar(config: dict) -> int:
    """How many respondents to panel for each avatar/persona.

    An avatar is an archetype, not a person — a study panels N of them. This
    multiplies the run: `avatars x N x claims` reactions. Absent or <1 means 1,
    which reproduces the one-respondent-per-persona behaviour of older runs.
    """
    raw = config.get("respondents_per_avatar") or config.get("respondents") or 1
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"config_snapshot.respondents_per_avatar is not a number: {raw!r}") from None
    if n < 1:
        raise ValueError(f"config_snapshot.respondents_per_avatar must be >= 1, got {n}")
    return n


def _avatar_ids_from_config(config: dict) -> list[str]:
    """`config["avatar_ids"]` (list) or `config["avatar_id"]` (single) -> ids.

    Tolerates a single id written as a string, which is what a hand-written
    snapshot for a one-persona smoke test looks like.
    """
    raw = config.get("avatar_ids") or config.get("avatar_id") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(a["id"]) if isinstance(a, dict) else str(a) for a in raw]


# ------------------------------------------------------ DB-bound activities ---

class StudyDataActivities:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @activity.defn
    async def fetch_study_context(self, run_id: str, study_id: str) -> StudyContext:
        """Assemble everything the rest of the run needs, from ONE source of
        truth: `runs.runs.config_snapshot`.

        The snapshot is written once (by `POST /studies/{id}/runs`, or by the
        SQL seed for a hardcoded run) and is what makes a run reproducible —
        editing the study's claims, avatars or anchors afterwards cannot change
        what an already-created run executes. The keys read here are:

            {
              "kbq":        "<the Key Belief Question / outcome dimension>",
              "claims":     [{"id": "<uuid>", "text": "<claim text>"}, ...],
              "avatar_ids": ["<uuid>", ...],          # or "avatar_id": "<uuid>"
              "anchors":    [{"scale_point": 1, "text": "..."}, ...],
              "penalties":  [{"trigger": "cost", "adjustment": 0.25,
                              "reason": "Direct cost concern"}, ...]
            }

        Every key falls back to the live `core.*` tables when absent, so runs
        created before the snapshot carried claims/anchors/avatars still work
        and nothing here is a breaking change.

        The avatar *prompt* is deliberately NOT read from the database. It
        comes from `fixtures/avatar_prompts.txt`, resolved by avatar id
        through `app.avatar_prompts` — see that module for why.
        """
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

            # -- kbq ------------------------------------------------------
            # The question every persona is asked to react against. Snapshot
            # first; the study row is the fallback.
            kbq = config.get("kbq") or study["outcome_dimension"] or ""

            # -- penalties ------------------------------------------------
            penalties = [
                Penalty(trigger=p["trigger"], adjustment=float(p["adjustment"]), reason=p["reason"])
                for p in config.get("penalties", [])
            ]

            # -- anchors --------------------------------------------------
            anchors = _anchors_from_config(config)
            if anchors is None:
                anchors = await self._anchors_from_db(conn, study_id, study["domain_id"])

            # -- claims (messages) ----------------------------------------
            claims = _claims_from_config(config)
            if claims is None:
                claims = await self._claims_from_db(conn, study_id)
            if not claims:
                raise ValueError(f"no claims/messages found for study {study_id}")

            # -- avatars & panel size -------------------------------------
            respondents = _respondents_per_avatar(config)
            avatar_ids = _avatar_ids_from_config(config)
            avatar_names = await self._avatar_names(conn, avatar_ids) if avatar_ids else {}
            if not avatar_ids:
                db_avatars = await self._avatars_from_db(conn, study_id)
                avatar_ids = [a[0] for a in db_avatars]
                avatar_names = dict(db_avatars)
            if not avatar_ids:
                raise ValueError(f"no avatars found for run {run_id} / study {study_id}")

        # Fail fast if a persona has no prompt: every respondent of it would
        # otherwise silently speak in the default voice, and on a large panel
        # that is thousands of wasted LLM calls before anyone notices.
        unmapped = [
            a for a in avatar_ids
            if str(a).lower() not in avatar_prompts.AVATAR_ID_TO_PERSONA
            and avatar_prompts.persona_key(avatar_names.get(a, "")) not in avatar_prompts.load_prompts()
        ]
        if unmapped:
            raise ValueError(
                f"no persona prompt resolves for avatar(s) {unmapped} — add them to "
                "AVATAR_ID_TO_PERSONA in app/avatar_prompts.py, or name the core.avatars "
                "row after a block in fixtures/avatar_prompts.txt"
            )

        total = len(avatar_ids) * respondents * len(claims)
        activity.logger.info(
            "run %s context: %s personas x %s respondents = %s respondents; "
            "x %s claims = %s reactions | %s anchors, %s penalties",
            run_id, len(avatar_ids), respondents, len(avatar_ids) * respondents,
            len(claims), total, len(anchors.texts), len(penalties),
        )
        # Sources, not a pair list — see StudyContext. The workflow expands
        # each batch on demand with pairs_for_slice().
        return StudyContext(
            kbq=kbq, anchors=anchors, avatar_ids=avatar_ids, claims=claims,
            penalties=penalties, respondents_per_avatar=respondents,
        )

    # -- fallback readers, used only when config_snapshot omits the key ----

    async def _anchors_from_db(self, conn, study_id: str, domain_id) -> AnchorSet:
        """Study-scoped anchors win; a study with none inherits its domain's."""
        rows = await conn.fetch(
            """SELECT id, text, scale_point FROM core.anchors
               WHERE scope_type = 'study' AND scope_id = $1
               ORDER BY scale_point""",
            study_id,
        )
        if not rows:
            rows = await conn.fetch(
                """SELECT id, text, scale_point FROM core.anchors
                   WHERE scope_type = 'domain' AND scope_id = $1
                   ORDER BY scale_point""",
                domain_id,
            )
        if not rows:
            raise ValueError(f"no anchors found for study {study_id} or its domain")
        return AnchorSet(
            ids=[str(r["id"]) for r in rows],
            texts=[r["text"] for r in rows],
            scale_points=[r["scale_point"] for r in rows],
        )

    async def _claims_from_db(self, conn, study_id: str) -> list[tuple[str, str]]:
        rows = await conn.fetch(
            "SELECT id, text FROM core.messages WHERE study_id = $1 ORDER BY position, id",
            study_id,
        )
        return [(str(r["id"]), r["text"]) for r in rows]

    async def _avatars_from_db(self, conn, study_id: str) -> list[tuple[str, str]]:
        rows = await conn.fetch(
            """SELECT a.id, a.name FROM core.study_avatars sa
               JOIN core.avatars a ON a.id = sa.avatar_id
               WHERE sa.study_id = $1
               ORDER BY a.name""",
            study_id,
        )
        return [(str(r["id"]), r["name"]) for r in rows]

    async def _avatar_names(self, conn, avatar_ids: list[str]) -> dict[str, str]:
        """`core.avatars.name` for the snapshotted ids — used only as a
        secondary way to resolve a prompt (see prompt_for_avatar) and to fail
        loudly if a snapshot references an avatar that no longer exists."""
        rows = await conn.fetch(
            "SELECT id, name FROM core.avatars WHERE id = ANY($1::uuid[])", avatar_ids
        )
        names = {str(r["id"]): r["name"] for r in rows}
        missing = [a for a in avatar_ids if a not in names]
        if missing:
            raise ValueError(
                f"config_snapshot references avatar(s) not in core.avatars: {missing} — "
                "runs.run_reactions.avatar_id is a foreign key, so these rows must exist"
            )
        return names

    @activity.defn
    async def persist_reactions(self, run_id: str, rows: list[ReactionRow]) -> None:
        """Idempotent — Temporal may retry this after a partial failure, so
        re-running with the same rows must not duplicate/corrupt data."""
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """INSERT INTO runs.run_reactions
                       (run_id, avatar_id, message_id, respondent,
                        score, distribution, penalty, reaction, status)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                   ON CONFLICT (run_id, avatar_id, message_id, respondent)
                   DO UPDATE SET score = EXCLUDED.score,
                                 distribution = EXCLUDED.distribution,
                                 penalty = EXCLUDED.penalty,
                                 reaction = EXCLUDED.reaction,
                                 status = EXCLUDED.status,
                                 updated_at = now()""",
                [
                    (run_id, r.avatar_id, r.message_id, r.respondent, r.score,
                     json.dumps(r.distribution) if r.distribution else None, r.penalty, r.text, r.status)
                    for r in rows
                ],
            )

    @activity.defn
    async def rollup_message_results(self, run_id: str) -> dict:
        """Bradley-Terry ranking, derived from independently-scored
        reactions: for each avatar, every pair of messages it reacted to is
        one pairwise comparison (lower final score wins). This is
        mathematically equivalent to the POC's literal per-pair
        regeneration — the reaction prompt never references the opposing
        claim, so re-generating it per pair is just re-sampling the same
        distribution — and it's O(N) LLM calls instead of O(N^2).

        Writes `runs.run_message_results`, which IS the run's result record —
        rank, Bradley-Terry strength, aggregate score and recommendation per
        claim. Returns a small summary of rank 1 purely so the workflow can log
        which claim won; nothing is stored twice.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT avatar_id, respondent, message_id, score
                   FROM runs.run_reactions
                   WHERE run_id = $1 AND status = 'ok' AND score IS NOT NULL""",
                run_id,
            )
            if not rows:
                activity.logger.warning("run %s: no scored reactions to rank", run_id)
                return {}

            message_ids = sorted({r["message_id"] for r in rows}, key=str)
            idx = {mid: i for i, mid in enumerate(message_ids)}
            n = len(message_ids)

            # Key on (avatar, respondent), NOT avatar alone. An avatar is a
            # persona panelled with N respondents; keying on the persona would
            # collapse all N into one dict and silently keep only the last
            # respondent's scores. Each respondent is an independent judge, and
            # more judges is precisely what makes the ranking more stable.
            by_respondent: dict = {}
            for r in rows:
                judge = (r["avatar_id"], r["respondent"])
                by_respondent.setdefault(judge, {})[r["message_id"]] = float(r["score"])

            wins = [[0.0] * n for _ in range(n)]
            score_sums = [0.0] * n
            score_counts = [0] * n
            for scores in by_respondent.values():
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

            winner_idx = ranked[0]
            margin = round(strengths[ranked[0]] - strengths[ranked[1]], 6) if n > 1 else None
            winner_text = await conn.fetchval(
                "SELECT text FROM core.messages WHERE id = $1", message_ids[winner_idx]
            )

        activity.logger.info(
            "run %s ranked %s claims from %s respondents | winner: '%s' "
            "(strength %.4f, margin %s)",
            run_id, n, len(by_respondent), (winner_text or "")[:60],
            strengths[winner_idx], margin,
        )
        return {
            "message_id": str(message_ids[winner_idx]),
            "text": winner_text,
            "bt_strength": round(strengths[winner_idx], 6),
            "margin_over_runner_up": margin,
        }

    @activity.defn
    async def generate_run_report(self, run_id: str) -> None:
        """Assembles the full report the POC's Results Report tab produced:
        executive summary, rankings table, cohort breakdown with a
        stakeholder-alignment check, penalty impact summary, detailed
        interpretation, and a strategic recommendation. Only the executive
        summary and interpretation are LLM calls (2 total); everything else
        is rendered from data already computed by rollup_message_results.
        Runs once, after that activity has written the ranking."""
        async with self._pool.acquire() as conn:
            study_row = await conn.fetchrow(
                """SELECT s.outcome_dimension AS kbq, r.config_snapshot
                   FROM runs.runs r JOIN core.studies s ON s.id = r.study_id
                   WHERE r.id = $1""",
                run_id,
            )
            ranking_rows = await conn.fetch(
                """SELECT rmr.rank, rmr.bt_strength, rmr.aggregate_score, rmr.recommendation,
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
                """SELECT rr.avatar_id, rr.respondent, rr.message_id, rr.score,
                          rr.reaction, a.name AS avatar_name
                   FROM runs.run_reactions rr
                   JOIN core.avatars a ON a.id = rr.avatar_id
                   WHERE rr.run_id = $1 AND rr.status = 'ok' AND rr.score IS NOT NULL""",
                run_id,
            )

        config = study_row["config_snapshot"] or {}
        if isinstance(config, str):
            config = json.loads(config)
        penalties = [
            Penalty(trigger=p["trigger"], adjustment=float(p["adjustment"]), reason=p["reason"])
            for p in config.get("penalties", [])
        ]

        # Same precedence as fetch_study_context: the snapshot is the run's
        # frozen config, the study row only a fallback.
        kbq = config.get("kbq") or study_row["kbq"] or ""

        rankings = [
            {
                "rank": r["rank"],
                "text": r["message_text"],
                "bt_strength": float(r["bt_strength"] or 0),
                "aggregate_score": float(r["aggregate_score"] or 0),
                "recommendation": r["recommendation"],
            }
            for r in ranking_rows
        ]
        message_text_by_id = {r["message_id"]: r["message_text"] for r in ranking_rows}

        # Same keying as the rollup: one entry per (avatar, respondent), so the
        # respondent count reported in the report is the real panel size.
        by_respondent: dict = {}
        judge_family: dict = {}
        for r in reaction_rows:
            judge = (r["avatar_id"], r["respondent"])
            by_respondent.setdefault(judge, {})[r["message_id"]] = float(r["score"])
            judge_family[judge] = _persona_family(r["avatar_name"])

        # Cohorts are per PERSONA, not per respondent — every respondent of
        # "Academic Oncologist" feeds that one cohort's averages.
        family_message_scores: dict = {}
        for judge, scores in by_respondent.items():
            fam = family_message_scores.setdefault(judge_family[judge], {})
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

        # Re-derive which penalties fired per reaction from the stored text —
        # runs.run_reactions only keeps the summed adjustment, not which
        # trigger phrases matched, so this is recomputed rather than stored.
        penalty_hits: dict[str, int] = {}
        for r in reaction_rows:
            _, _, triggered = _apply_penalties(0.0, penalties, r["reaction"] or "")
            for reason in triggered:
                penalty_hits[reason] = penalty_hits.get(reason, 0) + 1

        exec_summary = llm.call_chat(
            "You are a senior market research analyst writing an executive summary.",
            _exec_summary_prompt(rankings, kbq, len(by_respondent)),
            max_tokens=800,
        )
        interpretation = llm.call_chat(
            "You are a market research analyst.",
            _interpretation_prompt(rankings, cohort_breakdown),
            max_tokens=600,
        )

        report_md = _assemble_report_md(
            kbq=kbq,
            n_respondents=len(by_respondent),
            rankings=rankings,
            exec_summary=exec_summary,
            cohort_breakdown=cohort_breakdown,
            interpretation=interpretation,
            penalty_hits=penalty_hits,
            n_reactions=len(reaction_rows),
        )

        baseline_lift_pct = _baseline_lift_pct(
            rankings[0]["bt_strength"], rankings[-1]["bt_strength"]
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
                json.dumps(
                    {"cohort_breakdown": cohort_breakdown, "penalty_hits": penalty_hits}, default=str
                ),
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
                       coverage_pct = COALESCE($6, coverage_pct),
                       updated_at = now()
                   WHERE id = $1""",
                input.run_id, input.status,
                _parse_dt(input.started_at), _parse_dt(input.finished_at),
                json.dumps(input.error) if input.error else None,
                input.coverage_pct,
            )


#: runs.run_reports.baseline_lift_pct is numeric(6,2) — it can hold at most
#: 9999.99. Anything larger raises "numeric field overflow" and, because this
#: runs inside an activity, retries five times and then FAILS THE WHOLE RUN
#: after every reaction has already been paid for.
_MAX_LIFT_PCT = 9999.99


def _baseline_lift_pct(winner_strength: float, lowest_strength: float) -> float | None:
    """The winner's Bradley-Terry strength as a % lift over the weakest claim.

    Returns None rather than a number in the two cases where the ratio is not
    meaningful, both of which are common on small panels:

    * `lowest_strength == 0` — the weakest claim was shut out (won no pairwise
      comparison at all), so the lift is infinite. Unregularised Bradley-Terry
      assigns exactly 0 to a zero-win item, and with a handful of personas that
      happens often.
    * the ratio exceeds what `numeric(6,2)` can store — a near-zero (but not
      quite zero) denominator produces lifts in the millions of percent. Such a
      number is noise, not signal, and storing a clamped 9999.99 instead would
      be a quietly wrong figure rather than an honest "not meaningful".

    A NULL here is expected, not a failure; see TESTING.md 'Known quirks'.
    """
    if lowest_strength <= 0:
        return None
    lift = round((winner_strength - lowest_strength) / lowest_strength * 100, 2)
    return lift if abs(lift) <= _MAX_LIFT_PCT else None


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


# ------------------------------------------------------ report assembly ---
# Deterministic rendering — ported section-for-section from the POC's
# Results Report tab (ssr_poc.py, TAB 3). Only the executive summary and
# interpretation text come from an LLM; rankings/cohort/penalty sections are
# rendered straight from data rollup_message_results already computed.

def _rankings_table_md(rankings: list[dict]) -> str:
    lines = ["| Rank | Message | BT Strength | Recommendation |", "|---|---|---|---|"]
    for r in rankings:
        text = r["text"].replace("|", "\\|")
        lines.append(f"| {r['rank']} | {text} | {r['bt_strength']:.3f} | {r['recommendation']} |")
    return "\n".join(lines)


def _cohort_breakdown_md(cohort_breakdown: dict) -> str:
    if not cohort_breakdown:
        return "_No cohort data available._"

    sections = []
    top_picks: dict[str, str] = {}
    for family, ranked in sorted(cohort_breakdown.items()):
        top_picks[family] = ranked[0]["text"]
        lines = [f"**{family}**"]
        for entry in ranked:
            lines.append(f"{entry['preference_rank']}. {entry['text']} — avg score {entry['avg_score']}")
        sections.append("\n".join(lines))

    unique_picks = set(top_picks.values())
    if len(unique_picks) == 1:
        alignment = f"✅ All persona families agree — **{next(iter(unique_picks))}** is the preferred message."
    else:
        detail = " | ".join(f"{fam}: {msg}" for fam, msg in top_picks.items())
        alignment = (
            f"⚠️ Persona families disagree on the top message: {detail}. "
            "Consider differentiated messaging by audience."
        )

    return "\n\n".join(sections) + "\n\n**Stakeholder alignment:** " + alignment


def _penalty_impact_md(penalty_hits: dict[str, int], n_reactions: int) -> str:
    if not penalty_hits or n_reactions == 0:
        return "No penalties were triggered in this run."
    lines = []
    for reason, count in sorted(penalty_hits.items(), key=lambda kv: kv[1], reverse=True):
        pct = count / n_reactions * 100
        lines.append(f"- **{reason}** — triggered {count} times ({pct:.0f}% of reactions)")
    return "\n".join(lines)


def _strategic_recommendation_md(rankings: list[dict], n_respondents: int) -> str:
    winner = rankings[0]
    return (
        f"Lead your messaging with **\"{winner['text']}\"** — the top-ranked message "
        f"across {n_respondents} synthetic respondents, with a Bradley-Terry strength of "
        f"{winner['bt_strength']:.3f} (rank {winner['rank']} of {len(rankings)}, "
        f"tier: {winner['recommendation']}).\n\n"
        f"Before committing to expensive human market research, use this ranking to "
        f"prioritize which 1-2 messages to test — not all {len(rankings)}."
    )


def _assemble_report_md(
    *, kbq: str, n_respondents: int, rankings: list[dict], exec_summary: str,
    cohort_breakdown: dict, interpretation: str, penalty_hits: dict[str, int], n_reactions: int,
) -> str:
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return f"""# SSR Concept Testing — Simulation Report

_Generated {generated_at} · {n_respondents} synthetic respondents · {len(rankings)} messages tested_
_Key Belief Question: {kbq}_

## 1. Executive Summary

{exec_summary}

## 2. Message Rankings — Bradley-Terry Tournament Results

{_rankings_table_md(rankings)}

## 3. Cohort Breakdown by Persona

{_cohort_breakdown_md(cohort_breakdown)}

## 4. Detailed Interpretation

{interpretation}

## 5. Penalty Impact Summary

{_penalty_impact_md(penalty_hits, n_reactions)}

## 6. Strategic Recommendation

{_strategic_recommendation_md(rankings, n_respondents)}
"""
