#!/bin/bash
# Rol de aplicación SIN superuser ni BYPASSRLS: sin esto, RLS (segunda barrera
# de aislamiento entre tenants, ADR-002) no se aplica nunca.
set -euo pipefail

: "${ORDO_APP_PASSWORD:?ORDO_APP_PASSWORD requerida}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
	DO \$\$
	BEGIN
	    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ordo_app') THEN
	        CREATE ROLE ordo_app LOGIN PASSWORD '${ORDO_APP_PASSWORD}'
	            NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
	    END IF;
	END
	\$\$;

	GRANT CONNECT ON DATABASE "$POSTGRES_DB" TO ordo_app;
	GRANT USAGE ON SCHEMA public TO ordo_app;
	GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ordo_app;
	ALTER DEFAULT PRIVILEGES IN SCHEMA public
	    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ordo_app;
EOSQL
