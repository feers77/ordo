# ADR-016 — Enforcement de tokens en los servicios, PDP central

- **Estado:** aceptado (2026-08-04, autorizado por @feres77: "avanza con los tokens")
- **Fecha:** 2026-08-04
- **Decisores:** @feers77

## Contexto

`ordo-api` y `ordo-mcp` quedaron desplegados confiando en la red interna:
cualquier proceso con acceso al puerto opera cualquier tenant. IAM ya emite
tokens (agentes vía RFC 8693, usuarios vía Keycloak), tiene PDP de tres
capas y expone `POST /iam/v1/authorize`, que verifica el token, evalúa la
política y audita. Falta que alguien lo consulte en el camino del request.

## Opciones consideradas

1. **Gateway proxy dedicado** (`ordo-gateway`) que autentica y reenvía.
   Punto único correcto a largo plazo, pero hoy duplicaría el ruteo de
   Caddy y agregaría un salto y un servicio que mantener.
2. **Enforcement en cada servicio**: un cliente PDP en `ordo-runtime`
   reenvía el Bearer con (modelo, operación) a `/iam/v1/authorize` y actúa
   según la decisión. IAM sigue siendo la única autoridad: los servicios no
   verifican firmas ni interpretan caps.
3. **Verificar el JWT localmente en cada servicio** y evaluar la política
   ahí. Reimplementa el PDP fuera de IAM; descartada.

## Decisión

Opción 2. Reglas:

- **Encendido por configuración**: con `ORDO_IAM_URL` definida, todo
  request de datos exige Bearer válido; sin ella el servicio arranca en
  modo abierto y lo dice a gritos en el log (solo red interna).
- **Fail-closed**: si IAM no responde, el request se rechaza (503
  `AUTH_PDP_UNAVAILABLE`), nunca se deja pasar.
- **El tenant sale del token**, no de la cabecera: `/iam/v1/authorize`
  devuelve ahora el tenant resuelto y el servicio lo usa para el binding.
  Una cabecera `X-Ordo-Tenant` que contradiga al token es 403.
- Operaciones no-CRUD (acciones) viajan con su nombre; el PDP ya las evalúa
  como `write` del modelo más el `requires_approval` del cap. Si la
  decisión exige aprobación, el servicio responde 403
  `IAM_APPROVAL_REQUIRED` con el hint del flujo de aprobaciones; el consumo
  de una aprobación dentro del request llega en una iteración posterior.
- `ordo-runtime` gana `httpx` (ya sancionada en ADR-011 para IAM; misma
  librería, mismo criterio) para el cliente PDP.

## Consecuencias

- Positivas: un solo lugar decide (PDP + auditoría); los servicios quedan
  exponibles fuera de la LAN; el modo abierto queda explícito y ruidoso.
- Negativas: una llamada a IAM por request de datos (mitigable con caché
  corta de decisiones si alguna vez duele); IAM pasa a ser dependencia de
  disponibilidad de api/mcp cuando el enforcement está activo.
- Invalidaría: un gateway dedicado en el futuro; este cliente se movería
  ahí sin cambiar el contrato.
