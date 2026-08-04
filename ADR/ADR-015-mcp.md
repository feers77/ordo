# ADR-015 — Servidor MCP sin SDK externo

- **Estado:** aceptado (2026-08-04, autorizado por @feers77 al aprobar F3)
- **Fecha:** 2026-08-04
- **Decisores:** @feers77

## Contexto

F3 exige que un agente opere ORDO por MCP (Model Context Protocol). El
protocolo es JSON-RPC 2.0 sobre HTTP (transporte "streamable HTTP"): los
métodos que un servidor de herramientas necesita son `initialize`, `ping`,
`tools/list` y `tools/call`. Existe un SDK oficial de Python (`mcp`), que
sería una dependencia nueva (AGENTS.md §2.7).

## Opciones consideradas

1. **SDK oficial `mcp`** — cubre todo el protocolo (recursos, prompts,
   sampling, SSE), pero arrastra su propio stack de servidor y hoy solo
   necesitamos tools.
2. **Implementación propia mínima** — un endpoint FastAPI que habla
   JSON-RPC 2.0 y responde los cuatro métodos. ~200 líneas, cero
   dependencias nuevas, sobre el stack ya sancionado.
3. **No hacer MCP y esperar F3 completo** — deja el diferenciador del
   proyecto sin materializar.

## Decisión

Implementación propia mínima (opción 2). `ordo-mcp` gana la dependencia
interna `ordo-core` (workspace, sin dependencias externas nuevas) y expone
`POST /mcp`. Si más adelante se necesitan recursos, prompts o
notificaciones de progreso, se reevalúa el SDK **actualizando este ADR**;
la superficie pública (nombres y schemas de tools) no cambiaría.

El tenant viaja en la cabecera `X-Ordo-Tenant`, igual que en la API
genérica: el servidor MCP no inventa su propio modelo de identidad, y la
autorización fina sigue siendo del PDP cuando el gateway esté delante.

## Consecuencias

- Positivas: cero dependencias nuevas; el agente descubre y opera ORDO con
  el mismo contrato de la API (dry-run, códigos estables, aprobaciones
  declaradas).
- Negativas: solo tools (sin recursos ni prompts); el protocolo hay que
  seguirlo a mano si evoluciona.
- Invalidaría: necesitar capacidades del protocolo más allá de tools.
