-- Bootstrap SQL executed once by the postgres:16-alpine image.
-- Alembic owns the schema; this file only sets DB-wide knobs.

-- pgcrypto provides gen_random_uuid() used by all UUID PK defaults.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Reasonable defaults for a single-tenant dev DB.
ALTER DATABASE factory SET timezone TO 'UTC';
ALTER DATABASE factory SET statement_timeout TO '60s';
ALTER DATABASE factory SET idle_in_transaction_session_timeout TO '60s';
