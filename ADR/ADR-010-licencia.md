# ADR-010 — Licencia del producto y política anti-contaminación

- **Estado:** aprobado por @feers77 el 2026-08-04
- **Fecha:** 2026-08-04
- **Decisores:** @feers77

## Contexto

El repo `github.com/feers77/ordo` es público. El dueño quiere que el producto sea
**libre y gratuito, con uso comercial permitido**, pero que **quien lo modifique
devuelva esos cambios al proyecto** — incluido quien lo ofrezca como servicio, que es
el caso natural de un ERP operado por agentes.

Además, Odoo Community es LGPLv3: copiar su código, datos o traducciones contaminaría
nuestra licencia.

## Opciones consideradas

1. **AGPLv3** — libre, comercial permitido, copyleft fuerte que **alcanza el uso en red**:
   quien modifica y despliega como servicio debe publicar sus cambios. Es exactamente
   la intención declarada.
2. **GPLv3** — mismo copyleft, pero **no cubre SaaS**: alguien podría modificar el ERP,
   ofrecerlo como servicio y no devolver nada. Deja abierto justo el caso que importa.
3. **Apache 2.0** — máxima adopción, pero permite tomar el código, cerrarlo y revenderlo
   sin contribuir. Contradice el requisito.
4. **BSL con conversión a Apache** — protege una ventana comercial, pero no es open
   source al inicio y no obliga a contribuir.

## Decisión

**AGPLv3** (GNU Affero General Public License v3.0), sin licencia dual ni excepciones
comerciales. En concreto, cualquiera puede:

- usar ORDO gratis, incluso con fines comerciales y para operar su empresa;
- modificarlo y desplegarlo;

y a cambio debe:

- publicar el código fuente de sus modificaciones bajo AGPLv3, **también cuando el
  software se ofrece por red** (§13 de la licencia), que es el caso de un ERP SaaS;
- conservar los avisos de copyright y licencia.

Contribuciones bajo **DCO** (`Signed-off-by`) en cada commit **y CLA una sola vez**
(ver ADR-012): el CLA no cede copyright, otorga licencia amplia con derecho de
sublicencia. Eso mantiene abierta la opción de relicenciar sin perseguir después el
permiso de cada contribuyente.

## Política anti-contaminación (vigente, independiente de la licencia)

- Prohibido copiar código, datos o traducciones de Odoo o repos derivados
  (CLAUDE.md §2.1). Solo reimplementación de comportamiento observable.
- Datos fiscales desde fuentes normativas primarias (SII, AEAT, SUNAT, etc.), citadas
  en el `manifest.yaml` de cada pack de localización.
- Dependencias: permitidas MIT/BSD/Apache/PSF. Una dependencia GPL/AGPL de terceros
  requiere ADR propio.
- Revisión de similitud de código antes de cada release.
- "Odoo" es marca registrada: decimos "compatible con", nunca implicamos afiliación.

## Consecuencias

- Positivas: el ecosistema crece con las mejoras de todos; nadie puede cerrar el
  producto ni revenderlo como servicio propietario; el uso comercial queda abierto,
  así que no hay fricción para adoptarlo.
- Negativas: algunas empresas evitan AGPL por política interna, lo que puede frenar
  cierta adopción corporativa. Sin dual licensing no hay ingreso por vender excepciones;
  el modelo de negocio deberá venir de servicios, hosting o soporte.
- Qué invalidaría esta decisión: que el negocio exija licencias propietarias. Ese
  riesgo quedó **mitigado por el CLA de ADR-012**, adoptado mientras el copyright era
  íntegramente del titular (37 commits, cero contribuyentes externos).
