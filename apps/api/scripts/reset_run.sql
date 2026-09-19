-- ============================================================================
-- reset_run.sql — clear one run's results and return it to `approved`,
-- so it can be started again with the same id.
--
-- Run it:
--     docker compose exec -T postgres psql -U "$APP_POSTGRES_USER" \
--         -d "$APP_POSTGRES_DB" < apps/api/scripts/reset_run.sql
--
-- This resets the DEFAULT hardcoded run. For a different run, change the id
-- in the \set line below, or pass it in:
--     psql ... -v run_id="'<uuid>'" -f apps/api/scripts/reset_run.sql
--
-- IMPORTANT — Temporal, not just Postgres:
--   The workflow id is `study-run-<run_id>`, and Temporal refuses to open a
--   second workflow with the same id while the first is still OPEN. This
--   script only touches the database, so if the previous execution has not
--   closed you must also terminate it before restarting:
--
--     - Temporal UI: http://localhost:8080 -> find study-run-<run_id> -> Terminate
--     - or from apps/engine/:
--         uv run python scripts/inspect_workflow.py     # check status first
--         temporal workflow terminate --workflow-id study-run-<run_id>
--
--   A run that already reached finalized/cancelled/expired/failed has a
--   CLOSED workflow and needs no Temporal action — this script is enough.
--
-- To seed the study and run from scratch instead, use seed_hardcoded_run.sql.
-- ============================================================================

\if :{?run_id}
\else
  \set run_id '''40000000-0000-0000-0000-000000000001'''
\endif

BEGIN;

-- Results, child rows first. run_reactions and run_message_results cascade on
-- the run, but the run row survives this script, so delete them explicitly.
DELETE FROM runs.run_reactions       WHERE run_id = :run_id;
DELETE FROM runs.run_message_results WHERE run_id = :run_id;
DELETE FROM runs.run_reports         WHERE run_id = :run_id;

-- Back to the state POST /runs/{id}/start expects. config_snapshot is left
-- untouched — it is the run's frozen configuration, not a result.
UPDATE runs.runs
   SET status       = 'approved',
       workflow_id  = NULL,
       error        = NULL,
       coverage_pct = NULL,
       started_at   = NULL,
       finished_at  = NULL,
       updated_at   = now()
 WHERE id = :run_id;

COMMIT;

SELECT id,
       status,
       coverage_pct,
       jsonb_array_length(config_snapshot -> 'avatar_ids')
         * jsonb_array_length(config_snapshot -> 'claims') AS pairs_to_run,
       (SELECT count(*) FROM runs.run_reactions       WHERE run_id = :run_id) AS reactions,
       (SELECT count(*) FROM runs.run_message_results WHERE run_id = :run_id) AS ranked,
       (SELECT count(*) FROM runs.run_reports         WHERE run_id = :run_id) AS reports
  FROM runs.runs
 WHERE id = :run_id;
