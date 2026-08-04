# ADR-010 — Licencia del producto y política anti-contaminación

- **Estado:** propuesto — **requiere decisión explícita del dueño del proyecto**
- **Fecha:** 2026-08-04
- **Decisores:** @feers77

## Contexto

El repo `github.com/feers77/ordo` es público. Odoo Community es LGPLv3: copiar código, datos, planes contables o traducciones contaminaría nuestra licencia. Necesitamos licencia propia que permita negocio comercial sin cerrar la comunidad.

## Opciones consideradas

1. **AGPLv3 + excepción comercial (dual licensing)** — protege contra SaaS parasitario; requiere CLA para vender excepciones.
2. **BSL 1.1 → Apache 2.0 a los 4 años** — protege ventana comercial; no es open source formal al inicio.
3. **Apache 2.0 puro** — máxima adopción; cualquiera puede revender el producto como SaaS.

## Decisión

**Propuesta pendiente de aprobación:** AGPLv3 con excepción comercial y CLA (opción 1).

Política anti-contaminación (vigente ya, independiente de la licencia):
- Prohibido copiar código/datos/traducciones de Odoo o derivados (CLAUDE.md §2.1). Solo reimplementación de comportamiento observable.
- Datos fiscales desde fuentes normativas primarias (SII, AEAT, etc.), citadas en `manifest.yaml` del pack.
- Revisión de similitud de código antes de cada release.
- Dependencias: permitidas MIT/BSD/Apache/PSF; GPL/AGPL de terceros requiere ADR propio.
- "Odoo" es marca registrada: decimos "compatible con", nunca implicamos afiliación.

## Consecuencias

- Positivas: negocio protegido, código abierto real, packs de localización auditables.
- Negativas: CLA agrega fricción a contribuciones externas.
- Invalidaría: decisión comercial de ofrecer el core como servicio cerrado.
