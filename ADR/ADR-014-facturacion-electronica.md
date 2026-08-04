# ADR-014 — Framework de facturación electrónica y firma XML

- **Estado:** aceptado (2026-08-04, @feers77)
- **Fecha:** 2026-08-04
- **Decisores:** @feers77

## Contexto

Los packs fiscales de Chile y Paraguay (F4) declaran tipos de documento
electrónicos (DTE del SII, DE del SIFEN) pero no existe todavía la maquinaria
que los emite. El PLAN-MAESTRO §6.2 exige un framework común — máquina de
estados, certificados, firma, reintentos, contingencia — con un adaptador por
país. La firma XML plantea una decisión de dependencias (AGENTS.md §2.7).

## Opciones consideradas para la firma

1. **`signxml` + `lxml`** — XMLDSig/XAdES completo y validado contra los
   esquemas oficiales. Dos dependencias nuevas de C, superficie grande.
2. **`cryptography` directa** — ya está instalada (dependencia transitiva de
   `joserfc`, sancionada por ADR-011). Cubre RSA-SHA1/SHA256, carga de claves
   PEM y HMAC. No trae canonicalización XML (C14N 1.0), que la firma de
   documento completo requiere.
3. **Implementar C14N a mano** — terreno clásico de bugs de seguridad.
   Descartada.

## Decisión

- El framework (`modules/einvoicing`) define la firma como **interfaz**
  (`Signer`): la máquina de estados no sabe de criptografía.
- Se usa **`cryptography` directamente** para lo que no requiere C14N:
  la firma del TED chileno (RSA-SHA1 sobre los bytes aplanados de `DD`,
  con la clave del CAF, como especifica el SII) y el HMAC del QR del SIFEN.
  No es una dependencia nueva: ya está en el árbol y este ADR sanciona su
  uso directo.
- La firma XMLDSig **de documento completo** (EnvioDTE, DE del SIFEN) vive
  detrás de la interfaz `Signer`. Con la aceptación de este ADR se agregan
  `signxml` + `lxml` y se implementa `XmlDSigSigner` (firma enveloped;
  RSA-SHA256 por defecto, RSA-SHA1 solo porque el formato del SII lo exige,
  habilitado por instancia y nunca global). La verificación usa la
  configuración segura por defecto de `signxml`.
- Claves y certificados **nunca en la base de datos en claro** (AGENTS.md §7):
  el modelo `edi.certificate` guarda metadatos y una referencia al secreto
  (`vault_ref`); el material criptográfico se inyecta en runtime.

## Consecuencias

- Positivas: todo el circuito de firma queda implementado y probado (firma,
  verificación, detección de manipulación); el TED chileno y el documento
  completo se firman de verdad.
- Negativas: dos dependencias con extensiones en C (`lxml`, `signxml`);
  el envío productivo al SII/SIFEN sigue requiriendo certificados reales en
  el vault y el ambiente de certificación de cada autoridad.
- Invalidaría: que el SII o el SIFEN publiquen un mecanismo de firma no
  basado en XMLDSig.
