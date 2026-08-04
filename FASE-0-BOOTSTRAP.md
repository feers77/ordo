# Fase 0 — Bootstrap (para ejecutar con un agente de desarrollo)

Objetivo: al terminar esta fase tienes un servidor Ubuntu provisionado, un repositorio con estructura, CI funcionando, el stack de infraestructura levantado con healthchecks verdes, y los 10 ADRs fundacionales escritos. **Ningún módulo de negocio todavía.**

Duración estimada: 1–2 semanas.

---

## Prompt inicial para el agente de desarrollo

Copia esto tal cual en la primera sesión, tras poner `PLAN-MAESTRO.md` y `AGENTS.md` en la raíz del repo.

> Lee `PLAN-MAESTRO.md` y `AGENTS.md` completos antes de hacer nada. Vamos a ejecutar la Fase 0 descrita en `FASE-0-BOOTSTRAP.md`.
>
> Trabaja tarea por tarea, en orden. Antes de cada tarea, escribe en 5 líneas qué vas a hacer y espera mi confirmación. Después de cada tarea, muéstrame los archivos creados y el resultado de la verificación correspondiente.
>
> Tienes acceso sudo al servidor Ubuntu. Instala y configura lo que necesites, pero: (a) nada fuera de lo listado sin decírmelo antes, (b) todo lo que instales debe quedar reflejado en código de infraestructura versionado, nunca solo en el servidor, (c) ningún secreto en git.
>
> No escribas código de negocio en esta fase. Si crees que falta algo del plan, dímelo en vez de improvisarlo.

---

## T0.1 — Provisionamiento del servidor

**Verificación:** `ansible-playbook infra/ansible/site.yml --check` sin cambios pendientes tras la primera aplicación.

```
- Ubuntu 24.04 LTS, actualizado
- Usuario de servicio `ordo` sin login por password, sudo con contraseña
- SSH: solo clave, sin root login, puerto no estándar, fail2ban
- UFW: 22(o alterno)/tcp, 80, 443. Todo lo demás cerrado
- Zona horaria UTC, NTP (chrony)
- Límites del kernel: file descriptors, somaxconn, vm.overcommit
- Swap configurado si RAM < 32 GB
- Docker Engine + compose plugin
- Certificados TLS: Caddy o Traefik con ACME automático
- unattended-upgrades para parches de seguridad
- Backups: pgBackRest o wal-g hacia almacenamiento externo, con restore probado
```

Todo esto en `infra/ansible/`. **Nada de configuración manual que no quede en el playbook.**

---

## T0.2 — Repositorio y herramientas

**Verificación:** `make check` pasa en un repo vacío con un test dummy.

```
ordo/
├── ADR/                    ADR-000-template.md
├── docs/
├── services/               esqueletos vacíos con healthcheck
├── packages/
├── modules/
├── localizations/
├── infra/{ansible,compose,observability}
├── tests/{unit,integration,contract,load,security,golden,agent}
├── tools/
├── Makefile
├── pyproject.toml          uv como gestor; ruff, mypy, pytest configurados
├── .pre-commit-config.yaml
├── AGENTS.md
└── PLAN-MAESTRO.md
```

Decisiones a tomar aquí (y registrar en ADR):
- Monorepo con `uv` workspaces (recomendado) vs. multi-repo.
- Convención de commits: Conventional Commits + `commitizen` para changelog automático.
- Ramas: trunk-based con feature branches cortas y merge queue.

---

## T0.3 — Stack de infraestructura local

**Verificación:** `make up && make health` devuelve verde en todos los servicios.

`infra/compose/docker-compose.yml`:

| Servicio | Imagen | Notas |
|---|---|---|
| postgres | `postgres:17` | extensiones: `pgvector`, `pg_trgm`, `pg_stat_statements`, `btree_gist`. Config tuneada (shared_buffers, work_mem, max_connections) |
| pgbouncer | | pooling en modo transaction |
| redis | `redis:7` | AOF activado |
| nats | `nats:2` con JetStream | |
| minio | | buckets: `attachments`, `reports`, `einvoicing` |
| keycloak | `keycloak:26` | realm `ordo` importado desde `infra/keycloak/realm.json` |
| mailpit | | captura de emails en dev |
| otel-collector, prometheus, grafana, loki, tempo | | dashboards base versionados |

Perfil `dev` y perfil `prod`. En prod: sin mailpit, con réplicas y límites de recursos.

---

## T0.4 — Esqueletos de servicio

**Verificación:** cada servicio responde `GET /healthz` (liveness) y `GET /readyz` (readiness, verifica dependencias).

Crea `services/gateway`, `services/iam`, `services/api`, `services/jobs`, `services/events`, `services/render`, `services/mcp` con:

- FastAPI mínimo, logging estructurado JSON, OpenTelemetry instrumentado.
- Middleware común extraído a `packages/ordo-runtime`: request id, trace propagation, manejo de errores con el formato estándar de `AGENTS.md` §5, timeouts.
- Dockerfile multi-stage, imagen no-root, distroless o slim.
- Graceful shutdown.

---

## T0.5 — CI/CD

**Verificación:** un PR de prueba dispara el pipeline completo y bloquea el merge si falla.

Pipeline (GitHub Actions o Forgejo Actions si es self-hosted):

```
lint (ruff) → types (mypy strict) → unit → build imágenes
  → integration (testcontainers: postgres, redis, nats)
  → contract (openapi diff vs. baseline)
  → security (bandit, pip-audit, trivy sobre imágenes, gitleaks)
  → publish a registry (solo en main)
  → deploy a staging (automático) / prod (manual)
```

Reglas de protección de rama: `packages/ordo-core/**`, `services/iam/**` y `modules/account/**` requieren revisión humana (CODEOWNERS).

---

## T0.6 — Los 10 ADRs

Escribir en `ADR/`, formato corto (contexto / opciones / decisión / consecuencias), máximo 1 página cada uno:

1. `ADR-001` Stack: Python 3.12 + FastAPI + SQLAlchemy 2.0 + Postgres 17
2. `ADR-002` Multi-tenancy: schema-per-tenant + RLS, DB dedicada para grandes
3. `ADR-003` IAM: Keycloak como OP en F0–F2, `ordo-iam` propio desde F3, interfaz OIDC estable
4. `ADR-004` Capability tokens: estructura del claim `cap`, intersección de permisos
5. `ADR-005` Campos dinámicos: JSONB `x_custom` + índices de expresión, materialización opcional
6. `ADR-006` Lenguaje de dominios de sintaxis prefija; compilador propio a SQLAlchemy Core
7. `ADR-007` Jobs: cola en Postgres con `SKIP LOCKED`; NATS solo para eventos
8. `ADR-008` Eventos: patrón outbox transaccional → relay → JetStream → webhooks
9. `ADR-009` Versionado de API, política de deprecación, tests de contrato bloqueantes
10. `ADR-010` Licencia del producto y política anti-contaminación de código de terceros

---

## T0.7 — Esqueleto de la suite agéntica

**Verificación:** `make test-agent` corre y reporta 0/0 tareas (aún no hay negocio), pero la infraestructura de la suite funciona.

Construye ya el arnés que en F3 se vuelve el KPI del producto:

- Un runner que levanta un tenant limpio, autentica un agente, le entrega un objetivo en lenguaje natural, le da acceso al MCP server y mide:
  - tarea completada / no completada
  - número de llamadas y latencia total
  - estados inválidos generados (chequeados con aserciones de invariantes)
  - operaciones bloqueadas por el PDP y si el bloqueo fue correcto
- Un catálogo `tests/agent/tasks/*.yaml` donde cada tarea declara: objetivo, estado inicial, aserciones sobre el estado final.

Ejemplo de tarea (a poblar en fases posteriores):

```yaml
id: sales-001
goal: "Crea una cotización para el cliente Acme por 10 unidades del producto SKU-100 con 5% de descuento y confírmala."
setup: [partner:acme, product:SKU-100@1000CLP]
assert:
  - model: sale.order
    domain: [["partner_id.name","=","Acme"],["state","=","sale"]]
    count: 1
  - expression: "order.amount_untaxed == 9500"
  - invariant: no_invalid_states
```

---

## T0.8 — Runbook operativo

**Verificación:** un restore completo desde backup en una máquina limpia, cronometrado.

`docs/runbook.md`: despliegue, rollback, restore de backup, rotación de secretos, escalado, procedimiento ante incidente, contactos.

---

## Criterios de salida de la Fase 0

- [ ] `make up` levanta todo el stack en una máquina limpia en < 5 min
- [ ] Todos los `/readyz` en verde
- [ ] CI completo verde en un PR de prueba
- [ ] Restore de backup probado y cronometrado
- [ ] 10 ADRs escritos y aprobados
- [ ] `make test-agent` ejecuta el arnés sin errores
- [ ] Ninguna configuración del servidor existe fuera de `infra/`

---

## Después de la Fase 0

La Fase 1 (IAM) es la siguiente y **no debe solaparse** con el kernel. Su prompt inicial debería empezar así:

> Fase 1: implementar `ordo-iam` según §2 del `PLAN-MAESTRO.md`. Empieza por el modelo de datos de principals (User, ServiceClient, Agent), luego el bridge OIDC con Keycloak, luego token exchange RFC 8693 con cadena `act`, luego capability tokens, luego el PDP con RBAC + ABAC, y por último el flujo de aprobaciones HITL. Escribe la suite de tests de seguridad antes de cada componente, no después.
