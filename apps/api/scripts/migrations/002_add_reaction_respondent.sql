-- ============================================================================
-- 002_add_reaction_respondent.sql — N respondents per avatar
--
-- WHY
--   An avatar is a *persona* (a department-level archetype within a domain:
--   "Academic Oncologist"), not a single person. A study panels N respondents
--   per persona — 5, 100, 300 — and each respondent reacts to every claim
--   independently. The run's shape is therefore:
--
--       avatars x respondents_per_avatar = respondents
--       respondents x claims             = reactions
--
--       e.g. 4 personas x 5 respondents = 20 respondents
--            20 respondents x 5 claims  = 100 reactions
--
--   The old UNIQUE (run_id, avatar_id, message_id) allowed exactly one
--   reaction per (persona, claim), so a second respondent silently OVERWROTE
--   the first. `respondent` (1..N) is what makes them distinct rows.
--
--   Bradley-Terry treats each (avatar, respondent) as its own judge, so a
--   bigger panel means more pairwise comparisons and a more stable ranking —
--   which is the point of raising the count.
--
-- SAFE TO RUN
--   `respondent` defaults to 1, so every existing row is respondent 1 of its
--   persona and the widened constraint still holds. Re-runnable.
--
--   docker exec -i chorus-postgres psql -U nms3 -d chorus \
--       < apps/api/scripts/migrations/002_add_reaction_respondent.sql
-- ============================================================================

ALTER TABLE runs.run_reactions
    ADD COLUMN IF NOT EXISTS respondent integer NOT NULL DEFAULT 1;

COMMENT ON COLUMN runs.run_reactions.respondent IS
  'Which respondent of this avatar/persona produced the reaction, 1..N where '
  'N = config_snapshot.respondents_per_avatar. Each (avatar, respondent) is an '
  'independent judge in the Bradley-Terry rollup.';

ALTER TABLE runs.run_reactions DROP CONSTRAINT IF EXISTS uq_reaction;
ALTER TABLE runs.run_reactions
    ADD CONSTRAINT uq_reaction UNIQUE (run_id, avatar_id, message_id, respondent);

CREATE INDEX IF NOT EXISTS idx_reactions_run_avatar_resp
    ON runs.run_reactions (run_id, avatar_id, respondent);

-- Verify
SELECT conname, pg_get_constraintdef(oid) AS definition
  FROM pg_constraint
 WHERE conrelid = 'runs.run_reactions'::regclass AND conname = 'uq_reaction';
