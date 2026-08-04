# F4-03 — Framework de facturación electrónica (diseño)

La facturación electrónica es lo que convierte un pack fiscal en algo útil:
sin ella, el plan de cuentas y los impuestos son una tabla bonita. El diseño
sigue PLAN-MAESTRO §6.2.3: una máquina de estados común y un adaptador por
país. Las decisiones de firma están en ADR-014.

## Modelos (`modules/einvoicing`)

- `edi.document` — un documento electrónico emitido o por emitir.
  `country_code`, `document_type_code` (33/39/61 en CL; 1/5/6 en PY),
  `number` (folio o número asignado), `move_id` (asiento origen, opcional),
  `partner_id`, `state`, `xml_payload`, `track_id` (TrackID del SII, número
  de protocolo del SIFEN), `response_payload`, `error_message`, `attempts`,
  `contingency`, `company_id`.
- `edi.certificate` — metadatos del certificado de firma: `subject`,
  `serial`, `valid_from`, `valid_to`, `vault_ref` (referencia al secreto;
  la clave nunca toca la base de datos), `country_code`, `company_id`.
- `edi.folio.range` — rango de numeración autorizado por la autoridad:
  el CAF chileno y el timbrado paraguayo son el mismo concepto.
  `document_type_code`, `range_from`, `range_to`, `next_number`,
  `authorization_code` (el XML del CAF o el nro. de timbrado),
  `valid_until`, `country_code`, `company_id`.

## Máquina de estados

```
draft → generated → signed → sent → accepted
                                  ↘ rejected → generated (corregir y regenerar)
                       signed → contingency → sent (reintento)
draft|generated|rejected → cancelled
accepted → cancelled  (solo si el adaptador soporta anulación directa;
                       en CL la corrección es una NC, el adaptador lo explica)
```

Las transiciones son métodos explícitos (`action_generate`, `action_sign`,
`action_send`, `action_accept`, `action_reject`, `action_cancel`,
`action_contingency`); escribir `state` directo no existe como API. Cada
transición inválida es `EDI_INVALID_TRANSITION`. `attempts` se incrementa en
cada envío; el reintento desde contingencia no pierde el folio asignado.

## Contratos (protocolos, sin herencia)

- `EinvoiceAdapter` — `render(invoice, folio) -> bytes`,
  `parse_send_response(raw)`, `parse_status_response(raw)`,
  `supports_direct_cancellation`.
- `Signer` — `sign(xml: bytes, reference: str) -> bytes`. Implementación
  productiva pendiente de ADR-014; tests con firmador determinista.
- `Transport` — `async send(payload) -> bytes`. La red no entra en los
  tests unitarios; la implementación HTTP vive en `services/` cuando haya
  ambiente de certificación.

`InvoiceData` es el dato neutro entre contabilidad y adaptadores: emisor,
receptor (con tax id validado por el pack), líneas, resultado del motor de
impuestos (`TaxResult`), totales en `Decimal`.

## Folios

`FolioService.assign(country, document_type, company)` entrega el siguiente
número del rango vigente con `FOR UPDATE`; agotarlo es `EDI_FOLIO_EXHAUSTED`
y un rango vencido `EDI_FOLIO_EXPIRED`. Sin folio no hay documento: el SII
rechaza folios fuera de CAF y el SIFEN exige timbrado vigente.

## Errores

`EDI_INVALID_TRANSITION`, `EDI_NO_ADAPTER`, `EDI_FOLIO_EXHAUSTED`,
`EDI_FOLIO_EXPIRED`, `EDI_NOT_SIGNED`, `EDI_ALREADY_SENT`,
`EDI_CANCEL_UNSUPPORTED`, `EDI_MISSING_TAX_ID`.

## Qué NO entra aquí

- Firma XMLDSig productiva (ADR-014, pendiente de aprobación de deps).
- Transporte HTTP real y ambientes de certificación (Maullín del SII,
  test del SIFEN): cuando existan certificados en el vault.
- Representación impresa (PDF con timbre/QR): motor de reportes, F2 pendiente.
