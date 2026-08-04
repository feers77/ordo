# ADR-009 — Versionado de API y política de deprecación

- **Estado:** propuesto
- **Fecha:** 2026-08-04
- **Decisores:** @feers77

## Contexto

El consumidor primario es un agente: los errores de contrato no los detecta un humano leyendo un changelog. La compatibilidad es un feature del producto.

## Opciones consideradas

1. **Versión en la ruta (`/api/v1`) + tests de contrato bloqueantes** — explícito, cacheable, verificable en CI.
2. **Versión por header** — rutas limpias; invisible para logs/proxies y fácil de omitir.
3. **Sin versión, solo aditivo** — simple; imposible corregir errores de diseño.

## Decisión

Versión mayor en la ruta. Cambios aditivos dentro de la versión; cualquier cambio incompatible exige bump. Deprecación: anuncio con 2 versiones de anticipación, headers `Deprecation` y `Sunset`, códigos de error estables que solo se agregan (nunca se renombran ni eliminan). CI corre diff de OpenAPI contra baseline y **bloquea** el merge ante ruptura no declarada. Convenciones no negociables de §4.2 del plan (cursor pagination, ETag/If-Match, montos como string decimal, UTC ISO-8601) forman parte del contrato desde v1.

## Consecuencias

- Positivas: agentes pueden confiar en el contrato; rupturas se detectan en CI, no en producción.
- Negativas: mantener N y N-1 en paralelo durante ventanas de deprecación.
- Invalidaría: nada previsible; política pensada para durar.
