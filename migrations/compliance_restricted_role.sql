-- migrations/compliance_restricted_role.sql
--
-- 21 CFR Part 11 custody (P8): a LEAST-PRIVILEGE Postgres role for the running app's
-- compliance-trail connection, so the application can INSERT and SELECT the audit trail but
-- CANNOT UPDATE / DELETE / TRUNCATE / DROP it. The append-only triggers in compliance_node
-- are then defense-in-depth, not the only line of custody — a buggy or compromised code path
-- (or a stolen app credential) physically cannot rewrite or erase history through this role.
--
-- This applies to RDS / Postgres deployments only. Local SQLite has no role concept; there the
-- triggers + file custody are the control. Run this ONCE, as the database OWNER, after the
-- compliance_log / audit_log tables exist (the app creates them on first use). Then point the
-- app's compliance connection at this role via the GIMS_COMPLIANCE_DSN env var, e.g.:
--
--   GIMS_COMPLIANCE_DSN=postgresql://gims_compliance_writer:<password>@<host>:5432/<db>
--
-- The app keeps using its normal (owner) nodes_db connection for schema creation; only the
-- runtime INSERT/SELECT on the trail uses the restricted role. (The grant block below is also
-- applied idempotently by the app's schema-ensure when the role already exists, so re-granting
-- after a future ALTER is automatic.)

-- 1) The login role the app connects AS. Set a real password out-of-band (psql \password or
--    ALTER ROLE ... WITH PASSWORD), or manage it via your secrets store. NOLOGIN-by-default is
--    flipped to LOGIN here because the app must connect as it.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gims_compliance_writer') THEN
        CREATE ROLE gims_compliance_writer LOGIN;
    END IF;
END
$$;

-- 2) Allow it to reach the schema/sequences but NOT to mutate existing rows.
GRANT USAGE ON SCHEMA public TO gims_compliance_writer;

-- compliance_log: INSERT + SELECT only (BIGSERIAL id needs the sequence).
GRANT INSERT, SELECT ON public.compliance_log TO gims_compliance_writer;
REVOKE UPDATE, DELETE, TRUNCATE, TRIGGER, REFERENCES ON public.compliance_log FROM gims_compliance_writer;
GRANT USAGE, SELECT ON SEQUENCE public.compliance_log_id_seq TO gims_compliance_writer;

-- audit_log (auth events; chained too — P10): same posture.
GRANT INSERT, SELECT ON public.audit_log TO gims_compliance_writer;
REVOKE UPDATE, DELETE, TRUNCATE, TRIGGER, REFERENCES ON public.audit_log FROM gims_compliance_writer;

-- 3) Make sure no privileges leak in via PUBLIC.
REVOKE UPDATE, DELETE, TRUNCATE ON public.compliance_log FROM PUBLIC;
REVOKE UPDATE, DELETE, TRUNCATE ON public.audit_log FROM PUBLIC;
