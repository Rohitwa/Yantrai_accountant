-- Read-only proof that db/002_inbox.sql left the inbox locked down.
-- Run as `postgres` after applying 002 (and verify_intake.sql for 001):
--   psql "<postgres session-pooler URL>" -f db/verify_inbox.sql
-- Every row of the result must say ok = t.

WITH adm AS (
  SELECT * FROM pg_catalog.pg_roles WHERE rolname = 'website_admin'
), adm_settings AS (
  SELECT coalesce(array_agg(c), '{}') AS cfg
    FROM pg_catalog.pg_db_role_setting s
    JOIN adm ON s.setrole = adm.oid AND s.setdatabase = 0
    CROSS JOIN LATERAL unnest(s.setconfig) AS c
), api_roles AS (
  SELECT unnest(ARRAY['anon', 'authenticated', 'service_role']) AS r
), lead_tables AS (
  SELECT c.oid, c.relname
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'website_intake' AND c.relname IN ('submissions', 'notify_events')
), events AS (
  SELECT c.oid, c.relrowsecurity, c.relacl
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'website_intake' AND c.relname = 'inbox_events'
), public_rels AS (
  SELECT c.oid
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm')
), setfn AS (
  SELECT p.* FROM pg_catalog.pg_proc p
    JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'website_intake' AND p.proname = 'set_status'
), adm_policies AS (
  SELECT * FROM pg_catalog.pg_policies
   WHERE schemaname = 'website_intake' AND 'website_admin' = ANY (roles)
)
SELECT check_name, ok FROM (
  -- the login
            SELECT 1 AS n, 'website_admin exists and can log in' AS check_name, coalesce((SELECT rolcanlogin FROM adm), false) AS ok
  UNION ALL SELECT 2, 'website_admin is not a superuser and does not bypass row-level security',
            NOT coalesce((SELECT rolsuper OR rolbypassrls FROM adm), true)
  UNION ALL SELECT 3, 'website_admin inherits nothing and cannot create roles, databases or replicate',
            NOT coalesce((SELECT rolinherit OR rolcreaterole OR rolcreatedb OR rolreplication FROM adm), true)
  UNION ALL SELECT 4, 'website_admin connection limit is 5', coalesce((SELECT rolconnlimit = 5 FROM adm), false)
  UNION ALL SELECT 5, 'website_admin is a member of no other role',
            NOT EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN adm ON m.member = adm.oid)
  UNION ALL SELECT 6, 'website_admin stores exactly the three 002 settings, and none per database',
            coalesce(array_length((SELECT cfg FROM adm_settings), 1), 0) = 3
            AND EXISTS (SELECT 1 FROM unnest((SELECT cfg FROM adm_settings)) c WHERE c = 'statement_timeout=5s')
            AND EXISTS (SELECT 1 FROM unnest((SELECT cfg FROM adm_settings)) c
                         WHERE c = 'idle_in_transaction_session_timeout=10s')
            AND EXISTS (SELECT 1 FROM unnest((SELECT cfg FROM adm_settings)) c
                         WHERE c IN ('search_path=""', 'search_path='))
            AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_db_role_setting s JOIN adm ON s.setrole = adm.oid
                             WHERE s.setdatabase <> 0)
  -- what website_admin may do
  UNION ALL SELECT 7, 'website_admin may read the leads, their mail records and the inbox log',
            has_table_privilege('website_admin', 'website_intake.submissions', 'SELECT')
            AND has_table_privilege('website_admin', 'website_intake.notify_events', 'SELECT')
            AND has_table_privilege('website_admin', 'website_intake.inbox_events', 'SELECT')
  UNION ALL SELECT 8, 'website_admin cannot add, change or delete leads or mail records',
            NOT EXISTS (SELECT 1 FROM lead_tables t
                         WHERE has_any_column_privilege('website_admin', t.oid, 'INSERT')
                            OR has_any_column_privilege('website_admin', t.oid, 'UPDATE')
                            OR has_any_column_privilege('website_admin', t.oid, 'REFERENCES')
                            OR has_table_privilege('website_admin', t.oid, 'DELETE')
                            OR has_table_privilege('website_admin', t.oid, 'TRUNCATE')
                            OR has_table_privilege('website_admin', t.oid, 'TRIGGER')
                            OR CASE WHEN current_setting('server_version_num')::int >= 170000
                                    THEN has_table_privilege('website_admin', t.oid, 'MAINTAIN')
                                    ELSE false END)
  UNION ALL SELECT 9, 'website_admin may only append to the inbox log, never edit it',
            NOT (has_any_column_privilege('website_admin', 'website_intake.inbox_events', 'UPDATE')
                 OR has_any_column_privilege('website_admin', 'website_intake.inbox_events', 'REFERENCES')
                 OR has_table_privilege('website_admin', 'website_intake.inbox_events', 'DELETE')
                 OR has_table_privilege('website_admin', 'website_intake.inbox_events', 'TRUNCATE')
                 OR has_table_privilege('website_admin', 'website_intake.inbox_events', 'TRIGGER')
                 OR CASE WHEN current_setting('server_version_num')::int >= 170000
                         THEN has_table_privilege('website_admin', 'website_intake.inbox_events', 'MAINTAIN')
                         ELSE false END)
  UNION ALL SELECT 10, 'website_admin cannot set a log entry''s id, time or status change directly',
            has_column_privilege('website_admin', 'website_intake.inbox_events', 'action', 'INSERT')
            AND NOT (has_column_privilege('website_admin', 'website_intake.inbox_events', 'id', 'INSERT')
                     OR has_column_privilege('website_admin', 'website_intake.inbox_events', 'at', 'INSERT')
                     OR has_column_privilege('website_admin', 'website_intake.inbox_events', 'from_status', 'INSERT')
                     OR has_column_privilege('website_admin', 'website_intake.inbox_events', 'to_status', 'INSERT')
                     OR has_column_privilege('website_admin', 'website_intake.inbox_events', 'note', 'INSERT'))
  UNION ALL SELECT 11, 'website_admin has no rights on any public table',
            NOT EXISTS (SELECT 1 FROM public_rels p
                         WHERE has_any_column_privilege('website_admin', p.oid, 'SELECT')
                            OR has_any_column_privilege('website_admin', p.oid, 'INSERT')
                            OR has_any_column_privilege('website_admin', p.oid, 'UPDATE')
                            OR has_any_column_privilege('website_admin', p.oid, 'REFERENCES')
                            OR has_table_privilege('website_admin', p.oid, 'DELETE')
                            OR has_table_privilege('website_admin', p.oid, 'TRUNCATE')
                            OR has_table_privilege('website_admin', p.oid, 'TRIGGER')
                            OR CASE WHEN current_setting('server_version_num')::int >= 170000
                                    THEN has_table_privilege('website_admin', p.oid, 'MAINTAIN')
                                    ELSE false END)
  UNION ALL SELECT 12, 'website_admin cannot create objects in any schema',
            NOT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n
                         WHERE has_schema_privilege('website_admin', n.oid, 'CREATE'))
  UNION ALL SELECT 13, 'the only owner-rights function website_admin may call is set_status',
            NOT EXISTS (SELECT 1 FROM pg_catalog.pg_proc p
                          JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
                         WHERE p.prosecdef
                           AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                           AND has_schema_privilege('website_admin', n.oid, 'USAGE')
                           AND has_function_privilege('website_admin', p.oid, 'EXECUTE')
                           AND NOT (n.nspname = 'website_intake' AND p.proname = 'set_status'))
  -- set_status
  UNION ALL SELECT 14, 'set_status exists once and runs as its owner with a pinned search_path',
            (SELECT count(*) FROM setfn) = 1
            AND coalesce((SELECT prosecdef AND proconfig IS NOT NULL
                                 AND EXISTS (SELECT 1 FROM unnest(proconfig) c WHERE c LIKE 'search_path=%')
                            FROM setfn), false)
  UNION ALL SELECT 15, 'only website_admin (and the owner) may call set_status',
            coalesce((SELECT proacl IS NOT NULL
                             AND NOT EXISTS (SELECT 1 FROM aclexplode(proacl) a
                                              WHERE a.privilege_type = 'EXECUTE'
                                                AND a.grantee <> proowner
                                                AND a.grantee <> (SELECT oid FROM adm))
                        FROM setfn), false)
  -- the log's own protections
  UNION ALL SELECT 16, 'row-level security is on for the inbox log',
            coalesce((SELECT relrowsecurity FROM events), false)
  UNION ALL SELECT 17, 'website_admin''s policies: read the three tables; log only sign-ins, views and exports',
            (SELECT count(*) FROM adm_policies) = 4
            AND (SELECT count(*) FROM adm_policies
                  WHERE cmd = 'SELECT' AND qual = 'true'
                    AND tablename IN ('submissions', 'notify_events', 'inbox_events')) = 3
            AND (SELECT count(*) FROM adm_policies
                  WHERE cmd = 'INSERT' AND tablename = 'inbox_events'
                    AND with_check LIKE '%sign_in%' AND with_check LIKE '%view_lead%'
                    AND with_check LIKE '%export%' AND with_check NOT LIKE '%set_status%') = 1
  UNION ALL SELECT 18, 'a platform sign-in token can be used once',
            EXISTS (SELECT 1 FROM pg_catalog.pg_index i JOIN events e ON e.oid = i.indrelid
                     JOIN pg_catalog.pg_class ic ON ic.oid = i.indexrelid
                     WHERE ic.relname = 'inbox_events_token_once' AND i.indisunique AND i.indisvalid
                       AND pg_get_indexdef(i.indexrelid) LIKE '%(token_sha256)%'
                       AND pg_get_expr(i.indpred, i.indrelid) LIKE '%sign_in%')
  -- nobody else gets in
  UNION ALL SELECT 19, 'PUBLIC, anon, authenticated and service_role hold nothing on the inbox log',
            NOT EXISTS (SELECT 1 FROM events e, aclexplode(e.relacl) a WHERE a.grantee = 0)
            AND NOT EXISTS (SELECT 1 FROM api_roles
                             WHERE has_any_column_privilege(r, 'website_intake.inbox_events', 'SELECT')
                                OR has_any_column_privilege(r, 'website_intake.inbox_events', 'INSERT')
                                OR has_any_column_privilege(r, 'website_intake.inbox_events', 'UPDATE')
                                OR has_table_privilege(r, 'website_intake.inbox_events', 'DELETE')
                                OR has_table_privilege(r, 'website_intake.inbox_events', 'TRUNCATE'))
  UNION ALL SELECT 20, 'website_app has nothing on the inbox log and cannot call set_status',
            NOT (has_any_column_privilege('website_app', 'website_intake.inbox_events', 'SELECT')
                 OR has_any_column_privilege('website_app', 'website_intake.inbox_events', 'INSERT')
                 OR has_any_column_privilege('website_app', 'website_intake.inbox_events', 'UPDATE')
                 OR has_table_privilege('website_app', 'website_intake.inbox_events', 'DELETE')
                 OR has_table_privilege('website_app', 'website_intake.inbox_events', 'TRUNCATE')
                 OR coalesce((SELECT has_function_privilege('website_app', oid, 'EXECUTE') FROM setfn), true))
  UNION ALL SELECT 21, 'website_admin owns no large objects',
            NOT EXISTS (SELECT 1 FROM pg_catalog.pg_largeobject_metadata m JOIN adm ON m.lomowner = adm.oid)
) checks
ORDER BY n;
