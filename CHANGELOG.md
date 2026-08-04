# Changelog

Formato: [Keep a Changelog](https://keepachangelog.com/es/) + Conventional Commits (changelog automático vía commitizen desde F2).

## [Unreleased]

### Added

- **F1.2** Bridge OIDC: verificador JWT genérico (JWKS con caché y refetch ante rotación,
  solo RS256/ES256, rechaza alg=none y confusión de clave), bridge `idp_sub`→`iam_user`
  con vinculación en primer login verificado y sin auto-creación, endpoint `GET /iam/v1/me`.
  Realm Keycloak con claim `tenant` y audiencia `ordo-api`. 12 tests de seguridad unit +
  12 integración nuevos. (ADR-003)
- **F1.1** Modelo de datos de principals en `ordo-iam`: `iam_principal`, `iam_user`,
  `iam_service_client`, `iam_agent`, `iam_capability_grant`. Migración Alembic 0001.
  Invariantes: owner activo y mismo tenant, email único por tenant (case-insensitive),
  denegación por defecto (sin grants vigentes = sin capacidades), suspensión en cascada
  owner→agentes. Códigos de error `IAM_*` en `docs/api/errors.md`. (ADR-003, ADR-004, ADR-011)
- **F0** Bootstrap completo: provisioning Ansible, stack compose, ordo-runtime,
  7 esqueletos de servicio, CI/CD, suite agéntica, backups pgBackRest con restore probado,
  runbook, ADRs 001–010.
