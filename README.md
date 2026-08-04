# ORDO

**ERP/CRM completo, API-first, sin frontend, diseñado para que lo opere un agente de IA.**

La mayoría de los ERP asumen que del otro lado hay una persona mirando una pantalla.
ORDO asume lo contrario: el consumidor principal es un programa que descubre la API en
runtime, simula antes de escribir, reintenta sin duplicar y pide autorización humana
cuando toca algo delicado. Eso cambia el diseño de raíz.

| | ERP tradicional | ORDO |
|---|---|---|
| Descubrimiento | Manual y capacitación | El sistema se describe a sí mismo: `GET /meta/v1/schema` |
| Errores | Texto para un humano | Código estable + causa + acción sugerida + `retryable` |
| Reintentos | El usuario reintenta | `Idempotency-Key` obligatorio en toda escritura |
| Validación | Formulario con onchange | `?dry_run=true` en cualquier operación |
| Permisos | Rol del usuario | Capacidad delegada con límites de monto, modelo y tiempo |
| Auditoría | Quién | Quién, qué agente, bajo qué delegación, con qué traza |
| Errores del actor | Poco frecuentes | Frecuentes y creativos: el sistema es hostil a estados inválidos |

**Regla de oro:** si un agente puede dejar la contabilidad en un estado inconsistente,
el bug es del sistema, no del agente.

## Estado

En desarrollo, todavía sin release. Lo que ya funciona y está probado:

- **Identidad y autorización** — login OIDC, agentes como principales de primera clase,
  token exchange RFC 8693 con cadena de delegación, capability tokens, motor de políticas
  de tres capas, aprobaciones humanas y auditoría encadenada por hash.
- **Kernel** — registro de modelos con herencia, compilador de dominios a SQL, campos
  calculados con grafo de dependencias, ORM de escritura con bloqueo optimista, API
  genérica con transacciones multi-operación, secuencias legales sin huecos, cola de
  trabajos, outbox transaccional, chatter, adjuntos y schema semántico.
- **Módulos** — sistema de módulos con manifiesto y migraciones propias, más los
  módulos `base` (compañías, monedas, contactos, unidades), `account` (contabilidad
  con partida doble, motor de impuestos), `sale` y `purchase` (órdenes que al
  facturarse generan y contabilizan su asiento solas) y `einvoicing` (documentos
  electrónicos como máquina de estados, folios autorizados, certificados).
- **Facturación electrónica** — framework común con adaptadores **SII (Chile)**
  (CAF, timbre TED firmado con la clave del CAF, DTE, sobre EnvioDTE, acuses) y
  **SIFEN (Paraguay)** (CDC de 44 dígitos, XML del DE, QR firmado con el CSC).
  Firma XMLDSig de documento completo implementada (ADR-014: `signxml`,
  enveloped, con verificación y detección de manipulación probadas); el envío
  productivo requiere certificados reales en el vault y el ambiente de
  certificación de cada autoridad.
- **Localizaciones** — framework declarativo de packs fiscales con packs de Chile y
  Paraguay **en borrador**: contienen lo verificable citando la norma, pero el plan de
  cuentas y los impuestos específicos requieren revisión de un contador antes de usarse
  para declarar impuestos.

Cobertura: más de 500 tests entre unitarios, de integración contra PostgreSQL real y de
extremo a extremo. Los componentes donde un error sale caro tienen property-based
testing con Hypothesis: el compilador de dominios (tests de inyección incluidos), la
contabilidad (partida doble, inalterabilidad de asientos contabilizados, redondeo de
impuestos), el constructor de asientos de factura y la máquina de estados de la
facturación electrónica.

Lo que falta antes de la primera versión usable está en
[`docs/design/F2-00-resumen.md`](docs/design/F2-00-resumen.md); el plan completo, en
[`PLAN-MAESTRO.md`](PLAN-MAESTRO.md).

## Empezar

```bash
uv sync                # dependencias
make up                # PostgreSQL, Redis, NATS, MinIO, Keycloak
make check             # lint + tipos estrictos + tests
make docs-serve        # documentación de la API en http://localhost:8888
```

## Cómo se ve usarlo

Un agente descubre qué puede hacer, simula, y recién entonces escribe:

```bash
# 1. ¿Qué modelos hay y qué significa cada campo?
curl "$ORDO/meta/v1/schema?models=sale.order&format=llm"

# 2. Simular: devuelve lo que pasaría y qué validaciones fallarían, sin escribir nada
curl -X POST "$ORDO/api/v1/sale.order?dry_run=true" \
     -H "X-Ordo-Tenant: acme" \
     -d '{"values": {"partner_name": "ACME", "amount_total": "11900.00"}}'

# 3. Escribir de verdad: reintentar con la misma clave nunca duplica
curl -X POST "$ORDO/api/v1/sale.order" \
     -H "X-Ordo-Tenant: acme" -H "Idempotency-Key: $(uuidgen)" \
     -d '{"values": {"partner_name": "ACME", "amount_total": "11900.00"}}'
```

Los importes viajan como string decimal, nunca como float. Los timestamps son UTC
ISO-8601. Las colecciones se paginan por cursor, no por offset.

## Documentación

| Documento | Contenido |
|---|---|
| [`PLAN-MAESTRO.md`](PLAN-MAESTRO.md) | Arquitectura, roadmap y decisiones estratégicas |
| [`AGENTS.md`](AGENTS.md) | Reglas de trabajo vinculantes, para personas y agentes |
| [`ADR/`](ADR/) | Decisiones de arquitectura, con su contexto y consecuencias |
| [`docs/design/`](docs/design/) | Diseño detallado de cada entrega |
| [`docs/tutorial.md`](docs/tutorial.md) | De cero a un asiento contabilizado, paso a paso |
| [`docs/api/errors.md`](docs/api/errors.md) | Catálogo de códigos de error (contrato público) |
| [`docs/runbook.md`](docs/runbook.md) | Despliegue, rollback, restore, incidentes |

## Licencia

**AGPLv3** ([`LICENSE`](LICENSE)). Puedes usar ORDO gratis, también comercialmente, y
modificarlo. Si lo modificas y lo despliegas —incluso si solo lo ofreces por red, como
SaaS— debes publicar el código de tus cambios bajo la misma licencia.

El razonamiento está en [`ADR/ADR-010-licencia.md`](ADR/ADR-010-licencia.md): queremos
que las mejoras vuelvan al proyecto, no que alguien lo cierre y lo revenda.

Para contribuir, ver [`CONTRIBUTING.md`](CONTRIBUTING.md): DCO en cada commit
(`git commit -s`) y el [CLA](CLA.md) una sola vez, en tu primer PR. El CLA **no cede
copyright**; otorga una licencia amplia al proyecto.

> ORDO no está afiliado a ningún otro proyecto ni empresa de ERP. Su código está escrito
> desde cero; no reutiliza código de terceros con licencia copyleft.
