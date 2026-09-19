-- ============================================================================
-- seed_hardcoded_run.sql — one fully hardcoded, ready-to-start run.
--
-- Replaces the YAML fixture path (apps/engine/fixtures/scale_test.yaml +
-- seed_scale_test.py) for a fast end-to-end test: every id is a fixed literal,
-- and the whole pipeline configuration (kbq, claims, avatar_ids, anchors,
-- penalties) lives in runs.runs.config_snapshot, which is the ONLY thing
-- apps/engine's fetch_study_context activity reads.
--
-- Avatar *prompts* are NOT in this file and NOT in core.avatars.profile. They
-- come from apps/engine/fixtures/avatar_prompts.txt, resolved by avatar id
-- through apps/engine/app/avatar_prompts.py::AVATAR_ID_TO_PERSONA. The
-- core.avatars rows below exist because runs.run_reactions.avatar_id is a
-- foreign key onto them; `profile` is set to a pointer string, not a prompt.
--
-- Run it:
--     psql "$DATABASE_URL" -f apps/api/scripts/seed_hardcoded_run.sql
--   or, with the docker-compose Postgres:
--     docker compose exec -T postgres psql -U postgres -d digital_twin \
--         < apps/api/scripts/seed_hardcoded_run.sql
--
-- Then (worker + api up):
--     curl -X POST http://localhost:8000/api/v1/runs/40000000-0000-0000-0000-000000000001/start
--
-- Idempotent: every statement is ON CONFLICT DO UPDATE / DO NOTHING, so
-- re-running it re-seeds cleanly. To re-run the SAME run id from scratch,
-- delete its results first (last section of this file).
--
-- ---------------------------------------------------------------------------
-- Fixed ids used here (all hand-readable so they're checkable by eye):
--   user     00000000-0000-0000-0000-000000000001   (the dev user)
--   domain   d0000000-0000-0000-0000-000000000001
--   study    50000000-0000-0000-0000-000000000001
--   anchors  c0000000-0000-0000-0000-00000000000{1..5}    scale points 1..5
--   claims   e0000000-0000-0000-0000-00000000000{1..5}    core.messages
--   avatars  a0000000-0000-0000-0000-00000000000{1..c}    12 personas
--   run      40000000-0000-0000-0000-000000000001         status = approved
-- ============================================================================

-- ---------------------------------------------------------------------------
-- WHO OWNS THE STUDY  (read this before your first API call)
--
-- Every API route resolves the caller from their Entra token and checks that
-- they own the study (core.studies.owner_id). A run you do not own reads as
-- **404, not 403**, so it never confirms that someone else's id exists.
--
-- That means: if you drive the API with a real bearer token, the study must be
-- owned by YOUR user, not the built-in dev user — otherwise every call returns
--     "No study exists with id 50000000-..."
-- even though the row is right there.
--
-- Pass your own email to fix that (the user row is created the first time you
-- call GET /api/v1/me, so sign in once before seeding):
--
--   docker exec -i chorus-postgres psql -U nms3 -d chorus \
--     -v owner_email="'you@example.com'" < apps/api/scripts/seed_hardcoded_run.sql
--
-- Omit it and the seed picks the most recently active real user automatically
-- (anyone but the built-in dev user), falling back to dev@example.com when
-- nobody has signed in yet — which is right for the no-auth workflow.py path.
-- The verification row at the end prints who ended up owning it.
-- Re-running with a different owner_email just re-assigns it.
-- ---------------------------------------------------------------------------
\if :{?owner_email}
\else
  \set owner_email ''''''
\endif

-- ---------------------------------------------------------------------------
-- SECTIONS 1-7 vs SECTION 8
--
-- Sections 1-7 create the STUDY DATA: domain, study, anchors, claims, avatars.
-- There is no API endpoint that creates any of those yet, so this file is the
-- only way to get them into the database.
--
-- Section 8 creates a RUN with a literal config_snapshot. That part is
-- OPTIONAL — POST /api/v1/studies/{study_id}/runs does exactly the same job by
-- reading sections 1-7 back out of the tables. Skip it when you want the API
-- to own run creation:
--
--   docker exec -i chorus-postgres psql -U nms3 -d chorus \
--     -v skip_run=1 < apps/api/scripts/seed_hardcoded_run.sql
--
-- Keep it (the default) for the no-auth path: apps/engine/scripts/workflow.py
-- starts a run that already exists and never calls the API.
-- ---------------------------------------------------------------------------
\if :{?skip_run}
\else
  \set skip_run 0
\endif

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Dev user — core.studies.owner_id is NOT NULL REFERENCES core.users(id).
--    Matches apps/api/app/core/config.py Settings.dev_user_id. Always created
--    so the no-auth (workflow.py) path works out of the box.
-- ---------------------------------------------------------------------------
INSERT INTO core.users (id, email, name, role)
VALUES ('00000000-0000-0000-0000-000000000001', 'dev@example.com', 'Dev User', 'admin')
ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2. Domain
-- ---------------------------------------------------------------------------
INSERT INTO core.domains (id, name, type, description, compliance_profile)
VALUES (
    'd0000000-0000-0000-0000-000000000001',
    'Pharmaceutical Marketing — Hardcoded Test',
    'custom',
    'HCP messaging for a 4th-line RRMM tri-specific antibody. Test domain — not real study data.',
    'standard'
)
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description;

-- ---------------------------------------------------------------------------
-- 3. Study.
--    `outcome_dimension` is the KBQ (Key Belief Question) — every persona is
--    asked to react against it. It is ALSO copied into config_snapshot.kbq
--    below; the snapshot wins, this column is only the fallback.
--    `intent->'penalties'` is where the API's _build_config_snapshot lifts
--    penalties from when it creates a run through POST /studies/{id}/runs.
-- ---------------------------------------------------------------------------
INSERT INTO core.studies (
    id, domain_id, owner_id, name, description,
    outcome_dimension, scale_min, scale_max, status, intent
)
VALUES (
    '50000000-0000-0000-0000-000000000001',
    'd0000000-0000-0000-0000-000000000001',
    -- Owner resolution, in order:
    --   1. the email passed as -v owner_email='...'
    --   2. else the most recently active REAL user (anyone but the built-in
    --      dev user) — so a developer who has signed in once through the API
    --      owns the study automatically and never hits the 404
    --   3. else the dev user, which is right for the no-auth workflow.py path
    -- A NULL here would fail the NOT NULL constraint; step 3 guarantees a row.
    COALESCE(
        (SELECT id FROM core.users WHERE email = NULLIF(:'owner_email', '')),
        (SELECT id FROM core.users
          WHERE id <> '00000000-0000-0000-0000-000000000001'
          ORDER BY last_login_at DESC NULLS LAST, created_at DESC LIMIT 1),
        '00000000-0000-0000-0000-000000000001'
    ),
    'JNJ-5322 4L Messaging — Hardcoded Test',
    'Which claim most compellingly positions JNJ-5322 for an eligible 4th-line RRMM patient?',
    'How compelling is this as a reason to prescribe JNJ-5322 (BCMAxCD3xGPRC5D tri-specific) to an eligible 4th-line RRMM patient? (1 = Extremely compelling, 5 = Not compelling at all)',
    1, 5, 'ready',
    '{
      "penalties": [
        {"trigger": "reimbursement",     "adjustment": 0.30, "reason": "Payer/reimbursement concern"},
        {"trigger": "prior authorization","adjustment": 0.30, "reason": "Prior-authorization burden concern"},
        {"trigger": "cost",              "adjustment": 0.25, "reason": "Direct cost concern"},
        {"trigger": "IVIG",              "adjustment": 0.25, "reason": "IVIG operational burden"},
        {"trigger": "tocilizumab",       "adjustment": 0.25, "reason": "Tocilizumab logistics concern"},
        {"trigger": "infection risk",    "adjustment": 0.20, "reason": "Infection management concern"},
        {"trigger": "real-world data",   "adjustment": 0.20, "reason": "Wants real-world data before adopting"},
        {"trigger": "monitoring burden", "adjustment": 0.20, "reason": "Monitoring/logistics burden concern"},
        {"trigger": "CAR-T",             "adjustment": 0.15, "reason": "CAR-T preference over tri-specific"},
        {"trigger": "referral",          "adjustment": 0.15, "reason": "Would rather refer than administer"}
      ]
    }'::jsonb
)
ON CONFLICT (id) DO UPDATE
    SET outcome_dimension = EXCLUDED.outcome_dimension,
        intent            = EXCLUDED.intent,
        status            = EXCLUDED.status,
        -- Re-assign ownership, so re-running with a different owner_email
        -- hands the study to that user instead of erroring.
        owner_id          = EXCLUDED.owner_id;

-- ---------------------------------------------------------------------------
-- 4. Anchors — the 1..5 reference sentences the SSR score is computed against.
--    IMPORTANT ordering convention: scale_point 1 = MOST compelling, 5 = LEAST.
--    mean_ssr is a pmf-weighted average of these scale points, so throughout
--    the pipeline a LOWER score = a BETTER claim (that's why rollup's
--    Bradley-Terry comparison treats the lower score as the winner).
-- ---------------------------------------------------------------------------
INSERT INTO core.anchors (id, scope_type, scope_id, scale_point, text)
VALUES
 ('c0000000-0000-0000-0000-000000000001', 'study', '50000000-0000-0000-0000-000000000001', 1,
  'This message is extremely compelling — it directly addresses my biggest concern in 4L and would strongly influence my decision to prescribe JNJ-5322'),
 ('c0000000-0000-0000-0000-000000000002', 'study', '50000000-0000-0000-0000-000000000001', 2,
  'This is a persuasive message — it highlights a genuine clinical advantage and would make me more likely to consider JNJ-5322 for appropriate patients'),
 ('c0000000-0000-0000-0000-000000000003', 'study', '50000000-0000-0000-0000-000000000001', 3,
  'This message is somewhat relevant but neutral — it does not meaningfully change my view of JNJ-5322 compared to other 4L options'),
 ('c0000000-0000-0000-0000-000000000004', 'study', '50000000-0000-0000-0000-000000000001', 4,
  'This is a weak message for me — it touches on a minor factor or one I already knew, and would not significantly influence my prescribing'),
 ('c0000000-0000-0000-0000-000000000005', 'study', '50000000-0000-0000-0000-000000000001', 5,
  'This message is not compelling at all — it either overstates the benefit, misses what I care about, or describes something I consider a disadvantage')
ON CONFLICT (id) DO UPDATE SET text = EXCLUDED.text, scale_point = EXCLUDED.scale_point;

-- ---------------------------------------------------------------------------
-- 5. Claims (core.messages) — the things being tested against each other.
--    run_reactions.message_id is a FK onto this table, so these rows must
--    exist even though config_snapshot.claims also carries the text.
-- ---------------------------------------------------------------------------
INSERT INTO core.messages (id, study_id, text, position)
VALUES
 ('e0000000-0000-0000-0000-000000000001', '50000000-0000-0000-0000-000000000001',
  'JNJ-5322 delivers CAR-T-like efficacy (>90% ORR, 28-month mPFS) as a fully outpatient therapy — no hospitalization, no REMS required', 1),
 ('e0000000-0000-0000-0000-000000000002', '50000000-0000-0000-0000-000000000001',
  'Proven remission in triple-class exposed patients who have failed BCMA-targeted therapy — with no Grade 3+ CRS observed in the Phase 3 trial', 2),
 ('e0000000-0000-0000-0000-000000000003', '50000000-0000-0000-0000-000000000001',
  'Fixed 18-month treatment duration with subcutaneous dosing — giving your patients certainty and your practice predictability', 3),
 ('e0000000-0000-0000-0000-000000000004', '50000000-0000-0000-0000-000000000001',
  'Superior infection risk profile vs BCMA BsAb (Grade 3+: <25% vs 36-55%) with outpatient-manageable safety in community settings', 4),
 ('e0000000-0000-0000-0000-000000000005', '50000000-0000-0000-0000-000000000001',
  'No ocular toxicity, no REMS, no boxed warning — a clean safety profile that lets you focus on efficacy, not monitoring burden', 5)
ON CONFLICT (id) DO UPDATE SET text = EXCLUDED.text, position = EXCLUDED.position;

-- ---------------------------------------------------------------------------
-- 6. Avatars (personas) — IDENTITY ONLY.
--
--    `profile` used to hold the LLM system prompt. It no longer does: the
--    prompt is read from apps/engine/fixtures/avatar_prompts.txt, keyed by
--    the avatar id through AVATAR_ID_TO_PERSONA in
--    apps/engine/app/avatar_prompts.py. The `profile` values below are
--    pointers, so anyone reading the table knows where the real text lives.
--
--    `name` matters for two reasons: the engine can fall back to resolving a
--    prompt by normalizing the name (persona_key("Academic Oncologist") ->
--    "academic-oncologist"), and generate_run_report groups reactions into
--    cohorts by name.
--
--    All 12 personas from the prompts file are inserted; only the four
--    healthcare ones are attached to this study.
-- ---------------------------------------------------------------------------
INSERT INTO core.avatars (id, scope, domain_id, study_id, name, profile, source)
VALUES
 -- healthcare
 ('a0000000-0000-0000-0000-000000000001', 'library', 'd0000000-0000-0000-0000-000000000001', NULL,
  'Academic Oncologist',        'prompt: fixtures/avatar_prompts.txt#academic-oncologist',        'prebuilt'),
 ('a0000000-0000-0000-0000-000000000002', 'library', 'd0000000-0000-0000-0000-000000000001', NULL,
  'Community Oncologist',       'prompt: fixtures/avatar_prompts.txt#community-oncologist',       'prebuilt'),
 ('a0000000-0000-0000-0000-000000000003', 'library', 'd0000000-0000-0000-0000-000000000001', NULL,
  'Medical Director',           'prompt: fixtures/avatar_prompts.txt#medical-director',           'prebuilt'),
 ('a0000000-0000-0000-0000-000000000004', 'library', 'd0000000-0000-0000-0000-000000000001', NULL,
  'Evidence-Driven Specialist', 'prompt: fixtures/avatar_prompts.txt#evidence-driven-specialist', 'prebuilt'),
 -- B2B / IT
 ('a0000000-0000-0000-0000-000000000005', 'library', 'd0000000-0000-0000-0000-000000000001', NULL,
  'Pragmatic IT Director',      'prompt: fixtures/avatar_prompts.txt#pragmatic-it-director',      'prebuilt'),
 ('a0000000-0000-0000-0000-000000000006', 'library', 'd0000000-0000-0000-0000-000000000001', NULL,
  'Security-First CISO',        'prompt: fixtures/avatar_prompts.txt#security-first-ciso',        'prebuilt'),
 ('a0000000-0000-0000-0000-000000000007', 'library', 'd0000000-0000-0000-0000-000000000001', NULL,
  'Innovation-Seeking CTO',     'prompt: fixtures/avatar_prompts.txt#innovation-seeking-cto',     'prebuilt'),
 ('a0000000-0000-0000-0000-000000000008', 'library', 'd0000000-0000-0000-0000-000000000001', NULL,
  'Budget-Conscious Procurement Lead in IT',
  'prompt: fixtures/avatar_prompts.txt#budget-conscious-procurement-lead-in-it', 'prebuilt'),
 ('a0000000-0000-0000-0000-000000000009', 'library', 'd0000000-0000-0000-0000-000000000001', NULL,
  'ROI-Focused Economic Buyer', 'prompt: fixtures/avatar_prompts.txt#roi-focused-economic-buyer', 'prebuilt'),
 ('a0000000-0000-0000-0000-00000000000a', 'library', 'd0000000-0000-0000-0000-000000000001', NULL,
  'Skeptical Evaluator',        'prompt: fixtures/avatar_prompts.txt#skeptical-evaluator',        'prebuilt'),
 -- consumer
 ('a0000000-0000-0000-0000-00000000000b', 'library', 'd0000000-0000-0000-0000-000000000001', NULL,
  'Health-Conscious Parent',    'prompt: fixtures/avatar_prompts.txt#health-conscious-parent',    'prebuilt'),
 ('a0000000-0000-0000-0000-00000000000c', 'library', 'd0000000-0000-0000-0000-000000000001', NULL,
  'Growth-Oriented Investor',   'prompt: fixtures/avatar_prompts.txt#growth-oriented-investor',   'prebuilt')
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, profile = EXCLUDED.profile;

-- ---------------------------------------------------------------------------
-- 7. Attach the four healthcare personas to the study.
--    The engine reads config_snapshot.avatar_ids, not this table — but the
--    API needs it: _build_config_snapshot reads it when creating a run
--    through POST /studies/{id}/runs, and _study_counts() falls back to it
--    for a snapshot that predates the avatar_ids/claims keys.
-- ---------------------------------------------------------------------------
INSERT INTO core.study_avatars (study_id, avatar_id)
VALUES
 ('50000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000001'),
 ('50000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000002'),
 ('50000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000003'),
 ('50000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000004')
ON CONFLICT (study_id, avatar_id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 8. The run, pre-approved, with the complete config_snapshot.
--
--    THIS JSON IS THE ENGINE'S ENTIRE INPUT. fetch_study_context reads:
--      kbq        -> the question put to every persona
--      claims     -> [{id, text}]; id must be a real core.messages.id (FK)
--      avatar_ids -> [uuid];       each must be a real core.avatars.id (FK),
--                                  and each maps to a prompt in the txt file
--      anchors    -> [{scale_point, text}], sorted by scale_point
--      penalties  -> [{trigger, adjustment, reason}], substring-matched
--                    against each reaction's text
--    respondents_per_avatar is the panel size. An avatar is a PERSONA (an
--    archetype like "Academic Oncologist"), not a person, so the study panels
--    N respondents of each. That expands the cross product:
--
--        4 personas x 5 respondents       =  20 respondents
--        20 respondents x 5 claims        = 100 reactions
--
--    Nothing derivable is stored. There is no `pair_count`: the count is
--    always avatar_ids x respondents_per_avatar x claims, computed by the
--    API's _pair_count() for the progress denominator and by
--    fetch_study_context when it builds the cross product, so the two can
--    never disagree. scale_min/scale_max are not copied either — the scale is
--    defined by anchors[].scale_point.
--
--    status = 'approved' so POST /runs/{id}/start works immediately (start
--    requires `approved`). Set it to 'draft' instead if you want to walk the
--    full gate sequence: estimate -> approve (Gate 1) -> start.
-- ---------------------------------------------------------------------------
\if :skip_run
  \echo '  (skip_run=1 — no run row inserted; create runs with POST /studies/{id}/runs)'
\else
INSERT INTO runs.runs (id, study_id, status, config_snapshot, model_config)
VALUES (
    '40000000-0000-0000-0000-000000000001',
    '50000000-0000-0000-0000-000000000001',
    'approved',
    '{
      "kbq": "How compelling is this as a reason to prescribe JNJ-5322 (BCMAxCD3xGPRC5D tri-specific) to an eligible 4th-line RRMM patient? (1 = Extremely compelling, 5 = Not compelling at all)",

      "claims": [
        {"id": "e0000000-0000-0000-0000-000000000001",
         "text": "JNJ-5322 delivers CAR-T-like efficacy (>90% ORR, 28-month mPFS) as a fully outpatient therapy — no hospitalization, no REMS required"},
        {"id": "e0000000-0000-0000-0000-000000000002",
         "text": "Proven remission in triple-class exposed patients who have failed BCMA-targeted therapy — with no Grade 3+ CRS observed in the Phase 3 trial"},
        {"id": "e0000000-0000-0000-0000-000000000003",
         "text": "Fixed 18-month treatment duration with subcutaneous dosing — giving your patients certainty and your practice predictability"},
        {"id": "e0000000-0000-0000-0000-000000000004",
         "text": "Superior infection risk profile vs BCMA BsAb (Grade 3+: <25% vs 36-55%) with outpatient-manageable safety in community settings"},
        {"id": "e0000000-0000-0000-0000-000000000005",
         "text": "No ocular toxicity, no REMS, no boxed warning — a clean safety profile that lets you focus on efficacy, not monitoring burden"}
      ],

      "avatar_ids": [
        "a0000000-0000-0000-0000-000000000001",
        "a0000000-0000-0000-0000-000000000002",
        "a0000000-0000-0000-0000-000000000003",
        "a0000000-0000-0000-0000-000000000004"
      ],

      "respondents_per_avatar": 5,

      "anchors": [
        {"id": "c0000000-0000-0000-0000-000000000001", "scale_point": 1, "text": "This message is extremely compelling — it directly addresses my biggest concern in 4L and would strongly influence my decision to prescribe JNJ-5322"},
        {"id": "c0000000-0000-0000-0000-000000000002", "scale_point": 2, "text": "This is a persuasive message — it highlights a genuine clinical advantage and would make me more likely to consider JNJ-5322 for appropriate patients"},
        {"id": "c0000000-0000-0000-0000-000000000003", "scale_point": 3, "text": "This message is somewhat relevant but neutral — it does not meaningfully change my view of JNJ-5322 compared to other 4L options"},
        {"id": "c0000000-0000-0000-0000-000000000004", "scale_point": 4, "text": "This is a weak message for me — it touches on a minor factor or one I already knew, and would not significantly influence my prescribing"},
        {"id": "c0000000-0000-0000-0000-000000000005", "scale_point": 5, "text": "This message is not compelling at all — it either overstates the benefit, misses what I care about, or describes something I consider a disadvantage"}
      ],

      "penalties": [
        {"trigger": "reimbursement",      "adjustment": 0.30, "reason": "Payer/reimbursement concern"},
        {"trigger": "prior authorization","adjustment": 0.30, "reason": "Prior-authorization burden concern"},
        {"trigger": "cost",               "adjustment": 0.25, "reason": "Direct cost concern"},
        {"trigger": "IVIG",               "adjustment": 0.25, "reason": "IVIG operational burden"},
        {"trigger": "tocilizumab",        "adjustment": 0.25, "reason": "Tocilizumab logistics concern"},
        {"trigger": "infection risk",     "adjustment": 0.20, "reason": "Infection management concern"},
        {"trigger": "real-world data",    "adjustment": 0.20, "reason": "Wants real-world data before adopting"},
        {"trigger": "monitoring burden",  "adjustment": 0.20, "reason": "Monitoring/logistics burden concern"},
        {"trigger": "CAR-T",              "adjustment": 0.15, "reason": "CAR-T preference over tri-specific"},
        {"trigger": "referral",           "adjustment": 0.15, "reason": "Would rather refer than administer"}
      ],

      "domain": {
        "id": "d0000000-0000-0000-0000-000000000001",
        "name": "Pharmaceutical Marketing — Hardcoded Test"
      },
      "study": {
        "id": "50000000-0000-0000-0000-000000000001",
        "name": "JNJ-5322 4L Messaging — Hardcoded Test"
      },
      "snapshot_at": "2026-09-14T00:00:00+00:00"
    }'::jsonb,
    '{"temperature": 0.4, "max_tokens": 300}'::jsonb
)
ON CONFLICT (id) DO UPDATE
    SET status          = 'approved',
        config_snapshot = EXCLUDED.config_snapshot,
        model_config    = EXCLUDED.model_config,
        workflow_id     = NULL,
        error           = NULL,
        coverage_pct    = NULL,
        started_at      = NULL,
        finished_at     = NULL,
        updated_at      = now();
\endif

COMMIT;

-- ---------------------------------------------------------------------------
-- Verify
-- ---------------------------------------------------------------------------
\if :skip_run
SELECT s.id AS study_id, u.email AS owned_by,
       (SELECT count(*) FROM core.messages WHERE study_id = s.id)       AS claims,
       (SELECT count(*) FROM core.study_avatars WHERE study_id = s.id)  AS avatars,
       (SELECT count(*) FROM core.anchors WHERE scope_id = s.id)        AS anchors,
       'create a run with POST /studies/'||s.id||'/runs'                AS next_step
  FROM core.studies s JOIN core.users u ON u.id = s.owner_id
 WHERE s.id = '50000000-0000-0000-0000-000000000001';
\else
SELECT r.id AS run_id,
       r.status,
       (SELECT u.email FROM core.users u
          JOIN core.studies st ON st.owner_id = u.id
         WHERE st.id = r.study_id)                          AS owned_by,
       jsonb_array_length(r.config_snapshot -> 'claims')     AS claims,
       jsonb_array_length(r.config_snapshot -> 'avatar_ids') AS avatars,
       jsonb_array_length(r.config_snapshot -> 'anchors')    AS anchors,
       jsonb_array_length(r.config_snapshot -> 'penalties')  AS penalties,
       (r.config_snapshot ->> 'respondents_per_avatar')::int  AS resp_each,
       jsonb_array_length(r.config_snapshot -> 'avatar_ids')
         * (r.config_snapshot ->> 'respondents_per_avatar')::int
         * jsonb_array_length(r.config_snapshot -> 'claims')  AS reactions
  FROM runs.runs r
 WHERE r.id = '40000000-0000-0000-0000-000000000001';
\endif

-- ---------------------------------------------------------------------------
-- To re-run this run id from scratch (clears results, returns it to approved):
--
--   DELETE FROM runs.run_reactions      WHERE run_id = '40000000-0000-0000-0000-000000000001';
--   DELETE FROM runs.run_message_results WHERE run_id = '40000000-0000-0000-0000-000000000001';
--   DELETE FROM runs.run_reports        WHERE run_id = '40000000-0000-0000-0000-000000000001';
--   UPDATE runs.runs SET status='approved', workflow_id=NULL, error=NULL,
--          coverage_pct=NULL, started_at=NULL, finished_at=NULL
--    WHERE id = '40000000-0000-0000-0000-000000000001';
--
-- Note: Temporal rejects a second workflow with the same id while the first
-- is still open (workflow_id is "study-run-<run_id>"). Cancel or terminate
-- the previous execution in the Temporal UI (localhost:8080) first.
--
-- To try a different persona mix, just edit config_snapshot.avatar_ids —
-- e.g. swap in the IT personas and nothing else has to change:
--
--   UPDATE runs.runs
--      SET config_snapshot = jsonb_set(config_snapshot, '{avatar_ids}',
--            '["a0000000-0000-0000-0000-000000000005",
--              "a0000000-0000-0000-0000-000000000006",
--              "a0000000-0000-0000-0000-000000000007"]'::jsonb)
--    WHERE id = '40000000-0000-0000-0000-000000000001';
-- ---------------------------------------------------------------------------
