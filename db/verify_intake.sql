-- Read-only proof that db/001_intake.sql left website_intake locked down.
-- Run as `postgres` after applying 001:
--   psql "<postgres session-pooler URL>" -f db/verify_intake.sql
-- Every row of the first result must say ok = t. The two results after it are
-- for information only: the platform defaults every login shares (TEMP,
-- large-object functions), then the functions anyone may execute that run with
-- their owner's rights.

WITH app AS (
  SELECT * FROM pg_catalog.pg_roles WHERE rolname = 'website_app'
), app_settings AS (
  SELECT coalesce(array_agg(c), '{}') AS cfg
    FROM pg_catalog.pg_db_role_setting s
    JOIN app ON s.setrole = app.oid AND s.setdatabase = 0
    CROSS JOIN LATERAL unnest(s.setconfig) AS c
), api_roles AS (
  SELECT unnest(ARRAY['anon', 'authenticated', 'service_role']) AS r
), intake_tables AS (
  SELECT c.oid, c.relname, c.relrowsecurity, c.relacl
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'website_intake' AND c.relname IN ('submissions', 'notify_events')
), public_rels AS (
  SELECT c.oid
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm')
), throttle AS (
  SELECT p.* FROM pg_catalog.pg_proc p
    JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'website_intake' AND p.proname = '_throttle'
)
SELECT check_name, ok FROM (
  -- the login
            SELECT 1 AS n, 'website_app exists and can log in' AS check_name, coalesce((SELECT rolcanlogin FROM app), false) AS ok
  UNION ALL SELECT 2, 'website_app is not a superuser', NOT coalesce((SELECT rolsuper FROM app), true)
  UNION ALL SELECT 3, 'website_app does not bypass row-level security', NOT coalesce((SELECT rolbypassrls FROM app), true)
  UNION ALL SELECT 4, 'website_app inherits nothing', NOT coalesce((SELECT rolinherit FROM app), true)
  UNION ALL SELECT 5, 'website_app cannot create roles or databases', NOT coalesce((SELECT rolcreaterole OR rolcreatedb FROM app), true)
  UNION ALL SELECT 6, 'website_app cannot replicate', NOT coalesce((SELECT rolreplication FROM app), true)
  UNION ALL SELECT 7, 'website_app connection limit is 10', coalesce((SELECT rolconnlimit = 10 FROM app), false)
  UNION ALL SELECT 8, 'website_app is a member of no other role',
            NOT EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN app ON m.member = app.oid)
  UNION ALL SELECT 9, 'website_app statement_timeout is 5s',
            EXISTS (SELECT 1 FROM unnest((SELECT cfg FROM app_settings)) c WHERE c = 'statement_timeout=5s')
  UNION ALL SELECT 10, 'website_app search_path is empty',
            EXISTS (SELECT 1 FROM unnest((SELECT cfg FROM app_settings)) c WHERE c IN ('search_path=""', 'search_path='))
  -- what website_app may do
  UNION ALL SELECT 11, 'website_app may insert submissions',
            has_any_column_privilege('website_app', 'website_intake.submissions', 'INSERT')
  UNION ALL SELECT 12, 'website_app may insert notify_events',
            has_any_column_privilege('website_app', 'website_intake.notify_events', 'INSERT')
  UNION ALL SELECT 13, 'website_app cannot read, change or delete submissions',
            NOT (has_any_column_privilege('website_app', 'website_intake.submissions', 'SELECT')
                 OR has_any_column_privilege('website_app', 'website_intake.submissions', 'UPDATE')
                 OR has_table_privilege('website_app', 'website_intake.submissions', 'DELETE')
                 OR has_table_privilege('website_app', 'website_intake.submissions', 'TRUNCATE'))
  UNION ALL SELECT 14, 'website_app cannot read, change or delete notify_events',
            NOT (has_any_column_privilege('website_app', 'website_intake.notify_events', 'SELECT')
                 OR has_any_column_privilege('website_app', 'website_intake.notify_events', 'UPDATE')
                 OR has_table_privilege('website_app', 'website_intake.notify_events', 'DELETE')
                 OR has_table_privilege('website_app', 'website_intake.notify_events', 'TRUNCATE'))
  UNION ALL SELECT 15, 'website_app cannot set received_at, status or triage columns',
            NOT (has_column_privilege('website_app', 'website_intake.submissions', 'received_at', 'INSERT')
                 OR has_column_privilege('website_app', 'website_intake.submissions', 'status', 'INSERT')
                 OR has_column_privilege('website_app', 'website_intake.submissions', 'status_note', 'INSERT')
                 OR has_column_privilege('website_app', 'website_intake.submissions', 'triaged_at', 'INSERT')
                 OR has_column_privilege('website_app', 'website_intake.submissions', 'triaged_by_uid', 'INSERT')
                 OR has_column_privilege('website_app', 'website_intake.submissions', 'triaged_by', 'INSERT')
                 OR has_column_privilege('website_app', 'website_intake.submissions', 'schema_version', 'INSERT'))
  UNION ALL SELECT 16, 'website_app has no rights on any public table',
            NOT EXISTS (SELECT 1 FROM public_rels p
                         WHERE has_any_column_privilege('website_app', p.oid, 'SELECT')
                            OR has_any_column_privilege('website_app', p.oid, 'INSERT')
                            OR has_any_column_privilege('website_app', p.oid, 'UPDATE')
                            OR has_any_column_privilege('website_app', p.oid, 'REFERENCES')
                            OR has_table_privilege('website_app', p.oid, 'DELETE')
                            OR has_table_privilege('website_app', p.oid, 'TRUNCATE')
                            OR has_table_privilege('website_app', p.oid, 'TRIGGER'))
  -- nobody else gets in
  UNION ALL SELECT 17, 'anon, authenticated and service_role cannot use the schema',
            NOT EXISTS (SELECT 1 FROM api_roles WHERE has_schema_privilege(r, 'website_intake', 'USAGE'))
  UNION ALL SELECT 18, 'anon, authenticated and service_role hold no table rights',
            NOT EXISTS (SELECT 1 FROM api_roles a CROSS JOIN intake_tables t
                         WHERE has_any_column_privilege(a.r, t.oid, 'SELECT')
                            OR has_any_column_privilege(a.r, t.oid, 'INSERT')
                            OR has_any_column_privilege(a.r, t.oid, 'UPDATE')
                            OR has_any_column_privilege(a.r, t.oid, 'REFERENCES')
                            OR has_table_privilege(a.r, t.oid, 'DELETE')
                            OR has_table_privilege(a.r, t.oid, 'TRUNCATE')
                            OR has_table_privilege(a.r, t.oid, 'TRIGGER'))
  UNION ALL SELECT 19, 'PUBLIC holds nothing on the schema',
            NOT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n, aclexplode(n.nspacl) a
                         WHERE n.nspname = 'website_intake' AND a.grantee = 0)
  UNION ALL SELECT 20, 'PUBLIC holds nothing on either table',
            NOT EXISTS (SELECT 1 FROM intake_tables t, aclexplode(t.relacl) a WHERE a.grantee = 0)
  UNION ALL SELECT 21, 'row-level security is on for both tables',
            (SELECT count(*) FROM intake_tables WHERE relrowsecurity) = 2
  UNION ALL SELECT 22, 'only the two insert policies exist',
            (SELECT count(*) FROM pg_catalog.pg_policies WHERE schemaname = 'website_intake') = 2
            AND (SELECT count(*) FROM pg_catalog.pg_policies
                  WHERE schemaname = 'website_intake' AND cmd = 'INSERT') = 2
  -- the flood guard
  UNION ALL SELECT 23, 'throttle runs as its owner with a pinned search_path',
            coalesce((SELECT prosecdef AND proconfig IS NOT NULL
                             AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')
                        FROM throttle), false)
  UNION ALL SELECT 24, 'nobody but its owner may call the throttle directly',
            coalesce((SELECT proacl IS NOT NULL
                             AND NOT EXISTS (SELECT 1 FROM aclexplode(proacl) a
                                              WHERE a.grantee <> proowner AND a.privilege_type = 'EXECUTE')
                        FROM throttle), false)
  UNION ALL SELECT 25, 'throttle trigger is attached and enabled',
            EXISTS (SELECT 1 FROM pg_catalog.pg_trigger tg JOIN intake_tables t ON t.oid = tg.tgrelid
                     WHERE t.relname = 'submissions' AND tg.tgname = 'submissions_throttle'
                       AND tg.tgenabled = 'O')
  -- the Data API must never serve this schema
  UNION ALL SELECT 26, 'the Data API role is not configured to expose website_intake',
            NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r
                         WHERE r.rolname = 'authenticator'
                           AND array_to_string(r.rolconfig, ',') LIKE '%website_intake%')
            AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_db_role_setting s
                              JOIN pg_catalog.pg_roles r ON r.oid = s.setrole
                             WHERE r.rolname = 'authenticator'
                               AND array_to_string(s.setconfig, ',') LIKE '%website_intake%')
  UNION ALL SELECT 27, 'website_app has no per-database setting overrides',
            NOT EXISTS (SELECT 1 FROM pg_catalog.pg_db_role_setting s JOIN app ON s.setrole = app.oid
                         WHERE s.setdatabase <> 0)
  -- no CREATE anywhere and no owner-rights function reachable, including through
  -- PUBLIC (TEMP and large objects remain: see the information results)
  UNION ALL SELECT 28, 'website_app cannot create objects in any schema',
            NOT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n
                         WHERE has_schema_privilege('website_app', n.oid, 'CREATE'))
  UNION ALL SELECT 29, 'website_app cannot call any function that runs with its owner''s rights',
            NOT EXISTS (SELECT 1 FROM pg_catalog.pg_proc p
                          JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
                         WHERE p.prosecdef
                           AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                           AND has_schema_privilege('website_app', n.oid, 'USAGE')
                           AND has_function_privilege('website_app', p.oid, 'EXECUTE'))
  UNION ALL SELECT 30, 'notify_events rows must belong to a stored submission',
            EXISTS (SELECT 1 FROM pg_catalog.pg_constraint c JOIN intake_tables t ON t.oid = c.conrelid
                     WHERE t.relname = 'notify_events' AND c.contype = 'f'
                       AND c.confrelid = 'website_intake.submissions'::regclass)
  -- the lasting places website_app can write to on its own (see 001, section 1):
  -- large objects, and defaults stored on its own role
  UNION ALL SELECT 31, 'website_app owns no large objects',
            NOT EXISTS (SELECT 1 FROM pg_catalog.pg_largeobject_metadata m JOIN app ON m.lomowner = app.oid)
  UNION ALL SELECT 32, 'website_app stores no settings beyond the three 001 sets',
            coalesce(array_length((SELECT cfg FROM app_settings), 1), 0) = 3
            AND EXISTS (SELECT 1 FROM unnest((SELECT cfg FROM app_settings)) c
                         WHERE c = 'idle_in_transaction_session_timeout=10s')
            AND NOT EXISTS (SELECT 1 FROM unnest((SELECT cfg FROM app_settings)) c
                             WHERE split_part(c, '=', 1) NOT IN
                                   ('statement_timeout', 'idle_in_transaction_session_timeout', 'search_path'))
) checks
ORDER BY n;

-- Information only: platform-wide defaults every login shares. TEMP lets any login
-- (website_app included) create temporary tables; revoking it is a platform decision.
SELECT 'every login may create TEMP tables in this database' AS platform_default,
       has_database_privilege('website_app', current_database(), 'TEMP') AS applies_to_website_app
UNION ALL
SELECT 'every login may write large objects (' || f.proname || ')',
       has_function_privilege('website_app', f.oid, 'EXECUTE')
  FROM pg_catalog.pg_proc f
 WHERE f.pronamespace = 'pg_catalog'::regnamespace
   AND f.proname IN ('lo_from_bytea', 'lo_create', 'lowrite', 'lo_import')
   AND has_function_privilege('website_app', f.oid, 'EXECUTE');

-- Information only: SECURITY DEFINER functions in public/extensions that any
-- login (website_app included, through PUBLIC) may execute.
SELECT n.nspname || '.' || p.proname AS runs_with_owner_rights_callable_by_anyone
  FROM pg_catalog.pg_proc p
  JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
 WHERE p.prosecdef
   AND n.nspname IN ('public', 'extensions')
   AND (p.proacl IS NULL OR EXISTS (SELECT 1 FROM aclexplode(p.proacl) a
                                     WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE'))
 ORDER BY 1;
