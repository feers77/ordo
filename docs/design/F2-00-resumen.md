# Fase 2 — Kernel: resumen de entrega

Estado: kernel funcional con CI verde. Pendiente de revisión humana y merge
(`packages/ordo-core/**` exige revisión obligatoria, AGENTS.md §7).

## Entregables vs. §3 del PLAN-MAESTRO

| Requisito del plan | Dónde | Verificación |
|---|---|---|
| Registry de modelos + metadatos introspectables | `registry.py`, `irmodel.py` | 25 unit + 3 integración |
| Herencia por extensión y delegación | `registry.py` | tests de merge y conflicto de tipo |
| Sistema de campos con `agent_hint`/`examples` | `fields.py` | el registry falla al construir si faltan |
| Lenguaje de dominios → SQL | `domains.py` | 41 unit, 7 de inyección, 4 property-based, 11 ejecutando SQL real |
| Campos calculados y grafo de dependencias | `compute.py`, `cache.py` | 20 unit, ciclos detectados al boot |
| Environment, multi-tenancy y RLS | `environment.py` | 8 integración de aislamiento |
| Unit of work, bloqueo optimista | `recordset.py` | conflicto devuelve estado actual |
| Secuencias sin huecos | `services/sequences.py` | 5 sesiones concurrentes, sin saltos |
| Cola de jobs y cron | `services/jobs.py` | doble toma imposible (SKIP LOCKED) |
| Outbox transaccional | `services/outbox.py` | rollback no deja evento; relay idempotente |
| Chatter y adjuntos | `services/chatter.py`, `attachments.py` | 17 integración |
| API genérica CRUD/batch/tx | `services/api` | 19 endpoints |
| Dry-run universal e idempotencia | `recordset.py`, `idempotency.py` | rollback siempre; reuso detectado |
| Schema semántico | `semantic.py`, `/meta/v1/schema` | 8 unit |

## Decisiones de seguridad tomadas

- **Rol de aplicación sin `BYPASSRLS`**: conectarse con el rol dueño dejaba RLS inerte.
  `Environment` fuerza `SET LOCAL ROLE` y re-aplica el binding en cada transacción.
- **Cero interpolación** en el compilador de dominios; identificadores validados
  contra el registry; profundidad y tamaño acotados.
- **Dinero solo `Decimal`**: `Monetary` rechaza floats en definición y en escritura.
- **Dry-run nunca escribe**, ni parcialmente: siempre rollback de savepoint.

## Fuera del alcance entregado (F2 restante)

- i18n (traducciones de campos y catálogo de mensajes).
- Automated actions declarativas (diseñadas en F2-06, sin implementar).
- Motor de reportes e import/export con validación previa.
- Tests de carga k6 con los SLO de §4.3 — **necesarios antes de cerrar F2**.
- Migraciones Alembic para modelos de negocio (hoy el kernel crea sus tablas;
  los módulos de F4+ traerán las suyas).
