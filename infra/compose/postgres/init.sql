-- Extensiones base ORDO (se ejecuta solo en el primer arranque del volumen)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Base para Keycloak (perfil dev)
CREATE DATABASE keycloak OWNER ordo;
