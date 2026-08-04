# F3-01 — Servidor MCP (diseño)

El diferenciador de ORDO es que su operador primario es un agente. MCP es el
enchufe estándar para eso: cualquier cliente compatible (Claude, IDEs,
orquestadores) descubre y opera el ERP sin código a medida. Decisiones de
dependencias en ADR-015.

## Transporte

JSON-RPC 2.0 sobre `POST /mcp` (streamable HTTP). Métodos: `initialize`
(devuelve capacidades e instrucciones de uso), `ping`, `tools/list`,
`tools/call`. Las notificaciones se acusan con 202. El tenant viaja en
`X-Ordo-Tenant`, igual que en la API genérica.

## Tools

| Tool | Qué hace |
|---|---|
| `ordo_schema` | Schema semántico: campos con significado y ejemplos |
| `ordo_search` | Búsqueda por dominio, paginada por cursor |
| `ordo_read` | Lectura por ids |
| `ordo_create` / `ordo_write` | Escritura con `dry_run` e idempotencia |
| `ordo_list_actions` | Acciones del modelo con `requires_approval` |
| `ordo_run_action` | Ejecuta una transición de negocio, con `dry_run` |
| `ordo_list_reports` / `ordo_run_report` | Reportes de solo lectura |

Contratos idénticos a la API: importes como string decimal, errores con
código estable (`isError: true` + payload `{code, message, hint}`), dry-run
que revierte todo. La `idempotency_key` es opcional: por defecto cada
llamada usa una propia, y un agente que reintenta puede fijarla.

## Qué NO entra aquí

- Recursos, prompts y suscripciones del protocolo (cuando se necesiten,
  ADR-015 se actualiza y probablemente entre el SDK).
- Autenticación: el servidor asume que está detrás del gateway; la
  autorización fina es del PDP. No exponer a internet sin eso delante.
- `explain`, búsqueda semántica y NL→dominio (resto de F3).
