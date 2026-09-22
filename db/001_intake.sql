-- website_intake: where yantrailabs.com keeps its form submissions.
--
-- It lives in the platform Supabase (project vxnflumpectzqdamjqsc) but is closed
-- to everything else on it. The website connects as website_app, a login that
-- can INSERT new rows here and do nothing else: it cannot read a row back (so
-- the website makes its own ids and never uses RETURNING), cannot change or
-- delete one, and has no table rights in public.*. Nothing else is granted to it;
-- like every login it keeps PUBLIC's defaults (CONNECT, TEMP, large objects,
-- USAGE on public, EXECUTE on PUBLIC functions, reading the system catalogs).
--
-- Apply as the project's `postgres` login through the session pooler (:5432):
--   psql "<postgres session-pooler URL>" -v ON_ERROR_STOP=1 -f db/001_intake.sql
-- then give the runtime login its password interactively (never in a file):
--   \password website_app
-- and prove the result with db/verify_intake.sql (every row of the first result
-- must say t).
--
-- Rules this file keeps:
--   * idempotent and re-asserting: IF NOT EXISTS / CREATE OR REPLACE / guarded DO
--     blocks, so it is safe to run again; no DROP, TRUNCATE or DELETE of data
--     anywhere. A re-run puts its own state back: it resets website_app's
--     settings, revokes any rights granted since on website_intake to PUBLIC,
--     anon, authenticated, service_role and website_app, and replaces _throttle.
--   * the schema is invisible to Supabase's API roles (anon, authenticated,
--     service_role). Never add website_intake to the Data API's exposed schemas.
--   * everything runs in one transaction: it applies completely or not at all.

BEGIN;
-- Changing a table's row-level rules briefly locks it against the website's own
-- inserts. If something else is holding the table (a backup, an open editor
-- transaction), give up after 3 s rather than stall the live form behind the
-- wait; everything rolls back. Run the file again a minute later.
SET LOCAL lock_timeout = '3s';

-- 1. The runtime login ---------------------------------------------------------
-- Created with only the attributes a CREATEROLE (non-superuser) login may set,
-- which is what `postgres` is on Supabase. Superuser, BYPASSRLS, replication,
-- CREATEDB and CREATEROLE are all off by default; verify_intake.sql asserts it.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'website_app') THEN
    CREATE ROLE website_app LOGIN NOINHERIT CONNECTION LIMIT 10;
  END IF;
END
$$;
-- re-asserted on every run, so a hand edit is put back. Any login may store
-- defaults for itself (ALTER ROLE website_app SET ...), role-wide or in any
-- database, and those outlive its sessions: every one of them is cleared here,
-- in every database, before the three below are set again.
ALTER ROLE website_app LOGIN NOINHERIT CONNECTION LIMIT 10;
ALTER ROLE website_app RESET ALL;
DO $$
DECLARE
  db text;
BEGIN
  FOR db IN SELECT d.datname
              FROM pg_catalog.pg_db_role_setting s
              JOIN pg_catalog.pg_database d ON d.oid = s.setdatabase
             WHERE s.setrole = 'website_app'::regrole
  LOOP
    EXECUTE format('ALTER ROLE website_app IN DATABASE %I RESET ALL', db);
  END LOOP;
END
$$;
ALTER ROLE website_app SET statement_timeout = '5s';
ALTER ROLE website_app SET idle_in_transaction_session_timeout = '10s';
-- empty: every name the website uses is schema-qualified
ALTER ROLE website_app SET search_path = '';
-- postgres cannot reset a setting only a superuser may store: fail here, loudly,
-- rather than leave it for the verify script to find
DO $$
DECLARE
  leftovers text;
BEGIN
  -- name only the leftovers, not the three settings set just above
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
   WHERE s.setrole = 'website_app'::regrole
     AND (s.setdatabase <> 0 OR cardinality(s.setconfig) <> 3);
  IF leftovers IS NOT NULL THEN
    RAISE EXCEPTION 'website_app keeps settings this file could not reset (stored by a superuser): %', leftovers
      USING HINT = 'A superuser must run ALTER ROLE website_app RESET ALL, and '
                   'ALTER ROLE website_app IN DATABASE <name> RESET ALL for each database named '
                   'above; then run this file again.';
  END IF;
END
$$;
-- Note: any login may change its own session settings and its stored defaults
-- (and its own password), so these are defaults, not a wall; verify checks 9,
-- 10, 27 and 32 between them fail if anything other than these three values is
-- stored. What bounds a leaked website_app
-- password here is the INSERT-only grant, the flood guard (per open
-- transaction, see section 5), the foreign key on notify_events and the
-- connection limit. Two platform-wide defaults are NOT closed by this file, because they
-- belong to every login on the database: PUBLIC may create TEMP tables (gone at
-- disconnect) and may call the large-object functions (lo_from_bytea, lo_create,
-- lowrite), which store data that stays. verify_intake.sql check 31 fails if
-- website_app ever owns a large object, and lists both defaults for review.

-- 2. The schema ----------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS website_intake AUTHORIZATION postgres;
COMMENT ON SCHEMA website_intake IS
  'yantrailabs.com form submissions. Written only by website_app (INSERT-only); '
  'closed to anon, authenticated and service_role; never expose it in the Data API.';
REVOKE ALL ON SCHEMA website_intake FROM PUBLIC;
REVOKE ALL ON SCHEMA website_intake FROM anon, authenticated, service_role;

-- 3. Tables --------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS website_intake.submissions (
  -- made by the website (it cannot read one back); also the idempotency key,
  -- so a retried or replayed submission lands once
  id              uuid        PRIMARY KEY,
  form            text        NOT NULL CHECK (form IN ('savings_check', 'careers')),
  received_at     timestamptz NOT NULL DEFAULT now(),
  locale          text        CHECK (locale IN ('en', 'fr')),
  page            text        CHECK (char_length(page) <= 300),
  name            text        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
  email           text        NOT NULL CHECK (char_length(email) BETWEEN 3 AND 180),
  company         text        CHECK (char_length(company) <= 180),
  role            text        CHECK (char_length(role) <= 120),
  erp             text        CHECK (char_length(erp) <= 120),
  outflow         text        CHECK (char_length(outflow) <= 120),
  note            text        CHECK (char_length(note) <= 6000),
  linkedin        text        CHECK (char_length(linkedin) <= 300),
  work            text        CHECK (char_length(work) <= 300),
  area            text        CHECK (char_length(area) <= 120),
  -- careers: the CV itself still travels by email; the row records what was sent
  cv_filename     text        CHECK (char_length(cv_filename) <= 200),
  cv_mime         text        CHECK (cv_mime IN (
                                'application/pdf',
                                'application/msword',
                                'application/vnd.openxmlformats-officedocument.wordprocessingml.document')),
  cv_bytes        integer     CHECK (cv_bytes BETWEEN 0 AND 10485760),
  cv_sha256       text        CHECK (cv_sha256 ~ '^[0-9a-f]{64}$'),
  ip_hash         text        CHECK (char_length(ip_hash) <= 64),
  user_agent      text        CHECK (char_length(user_agent) <= 300),
  source_revision text        CHECK (char_length(source_revision) <= 100),
  -- triage, for the inbox (LEAD-03). website_app has no INSERT grant on these,
  -- so a submission always starts as 'new' and cannot claim to be triaged.
  status          text        NOT NULL DEFAULT 'new'
                              CHECK (status IN ('new', 'contacted', 'qualified', 'closed', 'spam')),
  status_note     text        CHECK (char_length(status_note) <= 2000),
  triaged_at      timestamptz,
  triaged_by_uid  uuid,
  triaged_by      text        CHECK (char_length(triaged_by) <= 120),
  schema_version  smallint    NOT NULL DEFAULT 1,
  CONSTRAINT savings_check_has_company
    CHECK (form <> 'savings_check' OR char_length(coalesce(company, '')) >= 1)
);
COMMENT ON TABLE website_intake.submissions IS
  'One row per accepted form submission from yantrailabs.com, stored before any mail is sent.';

CREATE INDEX IF NOT EXISTS submissions_received_idx
  ON website_intake.submissions (received_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS submissions_form_status_idx
  ON website_intake.submissions (form, status, received_at DESC);
-- serves the throttle's per-form, last-hour count
CREATE INDEX IF NOT EXISTS submissions_form_received_idx
  ON website_intake.submissions (form, received_at);
CREATE INDEX IF NOT EXISTS submissions_email_idx
  ON website_intake.submissions (lower(email));

-- whether each submission's notification mail went out; at most one row per
-- event per stored submission (the foreign key below bounds it)
CREATE TABLE IF NOT EXISTS website_intake.notify_events (
  submission_id uuid        NOT NULL,
  event         text        NOT NULL CHECK (event IN ('mail_sent', 'mail_failed', 'mail_disabled')),
  at            timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (submission_id, event)
);
COMMENT ON TABLE website_intake.notify_events IS
  'Delivery record for each submission''s notification mail.';
-- Checked as the table owner, so website_app needs no SELECT for it.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                 WHERE conname = 'notify_events_submission_fk'
                   AND conrelid = 'website_intake.notify_events'::regclass) THEN
    ALTER TABLE website_intake.notify_events
      ADD CONSTRAINT notify_events_submission_fk
      FOREIGN KEY (submission_id) REFERENCES website_intake.submissions (id) ON DELETE CASCADE;
  END IF;
END
$$;

-- 4. Row-level security ----------------------------------------------------------
-- Enabled (not forced: website_app is not the owner). The only policies admit
-- website_app's INSERTs; there is no SELECT, UPDATE or DELETE policy for anyone.
ALTER TABLE website_intake.submissions   ENABLE ROW LEVEL SECURITY;
ALTER TABLE website_intake.notify_events ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_policies
                 WHERE schemaname = 'website_intake' AND tablename = 'submissions'
                   AND policyname = 'intake_insert') THEN
    CREATE POLICY intake_insert ON website_intake.submissions
      FOR INSERT TO website_app
      WITH CHECK (status = 'new' AND status_note IS NULL AND triaged_at IS NULL
                  AND triaged_by_uid IS NULL AND triaged_by IS NULL);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_policies
                 WHERE schemaname = 'website_intake' AND tablename = 'notify_events'
                   AND policyname = 'notify_insert') THEN
    CREATE POLICY notify_insert ON website_intake.notify_events
      FOR INSERT TO website_app
      WITH CHECK (true);
  END IF;
END
$$;

-- 5. Flood guard ---------------------------------------------------------------
-- The hourly bound on stored rows, shared by every website instance (each of
-- which admits up to 60 in 10 minutes): about 300 savings checks and 100
-- careers applications an hour. It counts committed rows (plus the inserting
-- transaction's own), so transactions open at the same time can each add up to
-- the cap: the website inserts one row per transaction and overshoots by at
-- most one row per concurrent insert (2 with one instance and its 3 write
-- slots, up to 9 under the 10-connection limit), but a leaked password with the
-- 10 allowed connections could add about 10x. Over the cap the INSERT fails with SQLSTATE
-- WI429; the website then sends at most 5 [NOT SAVED] mails an hour per
-- instance and answers the rest 429. SECURITY DEFINER so it can count rows
-- website_app itself cannot read; search_path pinned.
CREATE OR REPLACE FUNCTION website_intake._throttle()
  RETURNS trigger
  LANGUAGE plpgsql
  SECURITY DEFINER
  SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  recent integer;
  cap    integer := CASE NEW.form WHEN 'careers' THEN 100 ELSE 300 END;
BEGIN
  -- a retry of a row that is already stored: let the primary key answer it
  -- (the website treats that as "already stored"), not the throttle
  IF EXISTS (SELECT 1 FROM website_intake.submissions WHERE id = NEW.id) THEN
    RETURN NEW;
  END IF;
  SELECT count(*) INTO recent
    FROM website_intake.submissions
   WHERE form = NEW.form
     AND received_at > now() - interval '1 hour';
  IF recent >= cap THEN
    RAISE EXCEPTION 'website intake throttled: % % submissions in the last hour', recent, NEW.form
      USING ERRCODE = 'WI429';
  END IF;
  RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION website_intake._throttle() FROM PUBLIC;
REVOKE ALL ON FUNCTION website_intake._throttle() FROM anon, authenticated, service_role;

CREATE OR REPLACE TRIGGER submissions_throttle
  BEFORE INSERT ON website_intake.submissions
  FOR EACH ROW EXECUTE FUNCTION website_intake._throttle();

-- 6. Privileges --------------------------------------------------------------------
-- Explicit revokes on each object, whatever defaults may have granted.
REVOKE ALL ON website_intake.submissions, website_intake.notify_events FROM PUBLIC;
REVOKE ALL ON website_intake.submissions, website_intake.notify_events
  FROM anon, authenticated, service_role;
REVOKE ALL ON website_intake.submissions, website_intake.notify_events FROM website_app;
REVOKE ALL ON SCHEMA website_intake FROM website_app;
REVOKE ALL ON FUNCTION website_intake._throttle() FROM website_app;

GRANT USAGE ON SCHEMA website_intake TO website_app;
-- column list on purpose: received_at, status and the triage columns are not in
-- it, so the website can neither set a row's time nor mark it triaged.
-- received_at is the inserting transaction's start: the website's one-row
-- inserts are stamped when they happen, while a transaction held open (a leaked
-- password, say) stamps its rows with the time it began.
GRANT INSERT (id, form, locale, page, name, email, company, role, erp, outflow, note,
              linkedin, work, area, cv_filename, cv_mime, cv_bytes, cv_sha256,
              ip_hash, user_agent, source_revision)
  ON website_intake.submissions TO website_app;
GRANT INSERT (submission_id, event) ON website_intake.notify_events TO website_app;

COMMIT;
