-- website_intake, part 2: the inbox (LEAD-03 "YantrAI Web").
--
-- YantrAI Web is the lead inbox the YantrAI platform shows its platform admins as
-- a tile. It is served by the website itself (/admin), which reads the leads as
-- website_admin, a second login separate from the form's INSERT-only website_app:
--   * it may read the leads (submissions, notify_events) and the inbox's own log;
--   * it changes a lead only through website_intake.set_status(), which records
--     who changed what in the same transaction; it has no UPDATE, DELETE or
--     TRUNCATE anywhere and no table rights in public.*;
--   * it may add sign-in, view and export entries to the log, never edit one.
--
-- Apply as the project's `postgres` login through the session pooler (:5432),
-- AFTER db/001_intake.sql:
--   psql "<postgres session-pooler URL>" -v ON_ERROR_STOP=1 -f db/002_inbox.sql
-- then give the login its password with scripts/set_website_login.py --role website_admin
-- (never in a file), and prove the result with db/verify_inbox.sql (every row of
-- the first result must say t).
--
-- Rules this file keeps (as 001):
--   * idempotent and re-asserting; no DROP, TRUNCATE or DELETE of data. A re-run
--     resets website_admin's settings, revokes rights granted since on the objects
--     below, and replaces set_status. 001 and 002 can be re-run in either order:
--     001 never touches website_admin or inbox_events.
--   * nothing is granted to website_app, PUBLIC or the Data API roles.
--   * one transaction: it applies completely or not at all.

BEGIN;
-- Changing a table's row-level rules briefly locks it against the website's own
-- inserts. If something else is holding the table (a backup, an open editor
-- transaction), give up after 3 s rather than stall the live form behind the
-- wait; everything rolls back. Run the file again a minute later.
SET LOCAL lock_timeout = '3s';

-- 0. 001 must be in place ------------------------------------------------------------
DO $$
BEGIN
  IF to_regclass('website_intake.submissions') IS NULL
     OR to_regclass('website_intake.notify_events') IS NULL THEN
    RAISE EXCEPTION 'website_intake is not set up: apply db/001_intake.sql first';
  END IF;
END
$$;

-- 1. The inbox login --------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'website_admin') THEN
    CREATE ROLE website_admin LOGIN NOINHERIT CONNECTION LIMIT 5;
  END IF;
END
$$;
ALTER ROLE website_admin LOGIN NOINHERIT CONNECTION LIMIT 5;
ALTER ROLE website_admin RESET ALL;
DO $$
DECLARE
  db text;
BEGIN
  FOR db IN SELECT d.datname
              FROM pg_catalog.pg_db_role_setting s
              JOIN pg_catalog.pg_database d ON d.oid = s.setdatabase
             WHERE s.setrole = 'website_admin'::regrole
  LOOP
    EXECUTE format('ALTER ROLE website_admin IN DATABASE %I RESET ALL', db);
  END LOOP;
END
$$;
ALTER ROLE website_admin SET statement_timeout = '5s';
ALTER ROLE website_admin SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE website_admin SET search_path = '';
DO $$
DECLARE
  leftovers text;
BEGIN
  SELECT string_agg(CASE WHEN s.setdatabase = 0 THEN 'role-wide'
                         ELSE 'IN DATABASE ' || quote_ident(d.datname) END
                    || ': ' || array_to_string(
                         CASE WHEN s.setdatabase = 0
                              THEN ARRAY(SELECT unnest(s.setconfig)
                                         EXCEPT SELECT unnest(ARRAY['statement_timeout=5s',
                                                                    'idle_in_transaction_session_timeout=10s',
                                                                    'search_path=""', 'search_path=']))
                              ELSE s.setconfig END, ', '), '; ')
    INTO leftovers
    FROM pg_catalog.pg_db_role_setting s
    LEFT JOIN pg_catalog.pg_database d ON d.oid = s.setdatabase
   WHERE s.setrole = 'website_admin'::regrole
     AND (s.setdatabase <> 0 OR cardinality(s.setconfig) <> 3);
  IF leftovers IS NOT NULL THEN
    RAISE EXCEPTION 'website_admin keeps settings this file could not reset (stored by a superuser): %', leftovers
      USING HINT = 'A superuser must run ALTER ROLE website_admin RESET ALL, and '
                   'ALTER ROLE website_admin IN DATABASE <name> RESET ALL for each database named '
                   'above; then run this file again.';
  END IF;
END
$$;
-- As for website_app, these are defaults the login could change for itself;
-- verify_inbox.sql fails if anything else is stored. What bounds a leaked
-- website_admin password: it reads the leads (that is its job) but changes them
-- only through set_status, which logs every change, and cannot delete anything.

-- 2. The inbox log ----------------------------------------------------------------------
-- Who signed in, opened a lead, changed a status or exported the list. The website
-- appends; nobody edits (website_admin has no UPDATE or DELETE). There is no
-- foreign key to submissions on purpose: the log outlives the rows it mentions.
CREATE TABLE IF NOT EXISTS website_intake.inbox_events (
  id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  at            timestamptz NOT NULL DEFAULT now(),
  action        text        NOT NULL CHECK (action IN ('sign_in', 'view_lead', 'set_status', 'export')),
  -- the platform account the sign-in token was minted for (users.id, username, org)
  actor_uid     uuid        NOT NULL,
  actor         text        CHECK (char_length(actor) <= 120),
  actor_org     uuid,
  submission_id uuid,
  from_status   text        CHECK (from_status IN ('new', 'contacted', 'qualified', 'closed', 'spam')),
  to_status     text        CHECK (to_status IN ('new', 'contacted', 'qualified', 'closed', 'spam')),
  note          text        CHECK (char_length(note) <= 2000),
  detail        jsonb       CHECK (detail IS NULL OR (jsonb_typeof(detail) = 'object'
                                                      AND octet_length(detail::text) <= 2000)),
  -- sign_in only: SHA-256 of the platform token used, so each is accepted once
  token_sha256  text        CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT inbox_events_shape CHECK (
    CASE action
      WHEN 'sign_in'    THEN token_sha256 IS NOT NULL AND submission_id IS NULL
                             AND from_status IS NULL AND to_status IS NULL AND note IS NULL
      WHEN 'view_lead'  THEN token_sha256 IS NULL AND submission_id IS NOT NULL
                             AND from_status IS NULL AND to_status IS NULL AND note IS NULL
      WHEN 'set_status' THEN token_sha256 IS NULL AND submission_id IS NOT NULL
                             AND to_status IS NOT NULL
      WHEN 'export'     THEN token_sha256 IS NULL AND submission_id IS NULL
                             AND from_status IS NULL AND to_status IS NULL AND note IS NULL
    END)
);
COMMENT ON TABLE website_intake.inbox_events IS
  'YantrAI Web access log: sign-ins, leads opened, status changes, exports. Append-only for website_admin.';

-- a platform sign-in token is good for one sign-in, whichever website instance sees it
CREATE UNIQUE INDEX IF NOT EXISTS inbox_events_token_once
  ON website_intake.inbox_events (token_sha256) WHERE action = 'sign_in';
CREATE INDEX IF NOT EXISTS inbox_events_submission_idx
  ON website_intake.inbox_events (submission_id, at DESC) WHERE submission_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS inbox_events_at_idx
  ON website_intake.inbox_events (at DESC);

-- 3. Changing a lead's status -------------------------------------------------------------
-- The only way website_admin changes a lead. Runs as its owner (postgres), so the
-- login needs no UPDATE right at all; stamps triaged_at itself; writes the log row
-- in the same transaction. p_expected is the status the admin was looking at: if
-- someone changed it meanwhile the call fails (WI409) rather than overwrite them.
-- WI404: no such lead. The actor arguments come from the website, which takes
-- them from the verified platform sign-in.
CREATE OR REPLACE FUNCTION website_intake.set_status(
    p_id        uuid,
    p_status    text,
    p_note      text,
    p_expected  text,
    p_actor_uid uuid,
    p_actor     text,
    p_actor_org uuid)
  RETURNS TABLE (new_status text, new_note text, changed_at timestamptz)
  LANGUAGE plpgsql
  SECURITY DEFINER
  SET search_path = pg_catalog, pg_temp
AS $$
#variable_conflict error
DECLARE
  v_old  text;
  v_note text := nullif(btrim(coalesce(p_note, '')), '');
  v_at   timestamptz := now();
BEGIN
  IF p_id IS NULL OR p_actor_uid IS NULL THEN
    RAISE EXCEPTION 'set_status: lead and actor are required' USING ERRCODE = '22023';
  END IF;
  IF p_status IS NULL OR p_status NOT IN ('new', 'contacted', 'qualified', 'closed', 'spam') THEN
    RAISE EXCEPTION 'set_status: unknown status' USING ERRCODE = '22023';
  END IF;
  IF p_expected IS NOT NULL AND p_expected NOT IN ('new', 'contacted', 'qualified', 'closed', 'spam') THEN
    RAISE EXCEPTION 'set_status: unknown expected status' USING ERRCODE = '22023';
  END IF;
  IF char_length(v_note) > 2000 OR char_length(p_actor) > 120 THEN
    RAISE EXCEPTION 'set_status: note or actor too long' USING ERRCODE = '22001';
  END IF;

  SELECT s.status INTO v_old
    FROM website_intake.submissions AS s
   WHERE s.id = p_id
     FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'set_status: no such lead' USING ERRCODE = 'WI404';
  END IF;
  IF p_expected IS NOT NULL AND v_old IS DISTINCT FROM p_expected THEN
    RAISE EXCEPTION 'set_status: the lead''s status changed meanwhile' USING ERRCODE = 'WI409';
  END IF;

  UPDATE website_intake.submissions AS s
     SET status = p_status,
         status_note = v_note,
         triaged_at = v_at,
         triaged_by_uid = p_actor_uid,
         triaged_by = p_actor
   WHERE s.id = p_id;

  INSERT INTO website_intake.inbox_events
         (at, action, actor_uid, actor, actor_org, submission_id, from_status, to_status, note)
  VALUES (v_at, 'set_status', p_actor_uid, p_actor, p_actor_org, p_id, v_old, p_status, v_note);

  new_status := p_status;
  new_note := v_note;
  changed_at := v_at;
  RETURN NEXT;
END
$$;
REVOKE ALL ON FUNCTION website_intake.set_status(uuid, text, text, text, uuid, text, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION website_intake.set_status(uuid, text, text, text, uuid, text, uuid)
  FROM anon, authenticated, service_role, website_app;

-- 4. Row-level security --------------------------------------------------------------------
-- Enabled (not forced) as in 001. website_admin may read all three tables and add
-- only sign-in, view and export entries to the log; status changes are written by
-- set_status, which runs as the owner.
ALTER TABLE website_intake.inbox_events ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_policies
                 WHERE schemaname = 'website_intake' AND tablename = 'submissions'
                   AND policyname = 'inbox_read') THEN
    CREATE POLICY inbox_read ON website_intake.submissions
      FOR SELECT TO website_admin USING (true);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_policies
                 WHERE schemaname = 'website_intake' AND tablename = 'notify_events'
                   AND policyname = 'inbox_read') THEN
    CREATE POLICY inbox_read ON website_intake.notify_events
      FOR SELECT TO website_admin USING (true);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_policies
                 WHERE schemaname = 'website_intake' AND tablename = 'inbox_events'
                   AND policyname = 'inbox_read') THEN
    CREATE POLICY inbox_read ON website_intake.inbox_events
      FOR SELECT TO website_admin USING (true);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_policies
                 WHERE schemaname = 'website_intake' AND tablename = 'inbox_events'
                   AND policyname = 'inbox_log') THEN
    CREATE POLICY inbox_log ON website_intake.inbox_events
      FOR INSERT TO website_admin
      WITH CHECK (action IN ('sign_in', 'view_lead', 'export'));
  END IF;
END
$$;
-- re-asserted: a hand edit of a policy's rule is put back
ALTER POLICY inbox_read ON website_intake.submissions TO website_admin USING (true);
ALTER POLICY inbox_read ON website_intake.notify_events TO website_admin USING (true);
ALTER POLICY inbox_read ON website_intake.inbox_events TO website_admin USING (true);
ALTER POLICY inbox_log ON website_intake.inbox_events TO website_admin
  WITH CHECK (action IN ('sign_in', 'view_lead', 'export'));

-- 5. Privileges -----------------------------------------------------------------------------
-- Explicit revokes on each object, whatever defaults or hand edits may have granted.
REVOKE ALL ON website_intake.inbox_events FROM PUBLIC;
REVOKE ALL ON website_intake.inbox_events FROM anon, authenticated, service_role, website_app;
REVOKE ALL ON website_intake.submissions, website_intake.notify_events, website_intake.inbox_events
  FROM website_admin;
REVOKE ALL ON SCHEMA website_intake FROM website_admin;
REVOKE ALL ON FUNCTION website_intake._throttle() FROM website_admin;
REVOKE ALL ON FUNCTION website_intake.set_status(uuid, text, text, text, uuid, text, uuid) FROM website_admin;

GRANT USAGE ON SCHEMA website_intake TO website_admin;
GRANT SELECT ON website_intake.submissions, website_intake.notify_events, website_intake.inbox_events
  TO website_admin;
-- column list on purpose: id and at come from the table, and the status columns
-- (from_status, to_status, note) are only ever written by set_status
GRANT INSERT (action, actor_uid, actor, actor_org, submission_id, detail, token_sha256)
  ON website_intake.inbox_events TO website_admin;
GRANT EXECUTE ON FUNCTION website_intake.set_status(uuid, text, text, text, uuid, text, uuid)
  TO website_admin;

COMMIT;
