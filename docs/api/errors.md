# Catálogo de códigos de error (contrato público)

Los `code` se agregan, nunca se renombran ni eliminan (AGENTS.md §5).
Formato de payload: ver `packages/ordo-runtime/src/ordo_runtime/errors.py`.

## Runtime (todos los servicios)

| Código | HTTP | Retryable | Significado |
|---|---|---|---|
| `INTERNAL_ERROR` | 500 | sí | Error no manejado; reintentar con mismo Idempotency-Key |
| `REQUEST_TIMEOUT` | 504 | sí | La operación excedió el tiempo máximo |

## IAM

| Código | HTTP | Retryable | Significado |
|---|---|---|---|
| `IAM_PRINCIPAL_NOT_FOUND` | 404 | no | Principal inexistente |
| `IAM_OWNER_NOT_FOUND` | 404 | no | owner_user_id no existe al crear agente |
| `IAM_OWNER_INACTIVE` | 409 | no | El dueño del agente no está activo |
| `IAM_TENANT_MISMATCH` | 409 | no | Agente y dueño de tenants distintos |
| `IAM_EMAIL_TAKEN` | 409 | no | Email ya registrado en el tenant |
| `IAM_CLIENT_ID_TAKEN` | 409 | no | client_id ya registrado (único global) |
| `IAM_GRANT_NOT_FOUND` | 404 | no | Capability grant inexistente |
| `IAM_TOKEN_INVALID` | 401 | no | Token malformado, firma/iss/aud/alg inválidos o claim faltante |
| `IAM_TOKEN_EXPIRED` | 401 | sí | Token vencido; renovar y reintentar |
| `IAM_UNKNOWN_IDENTITY` | 401 | no | Identidad no registrada en el tenant (sin auto-creación) |
| `IAM_PRINCIPAL_SUSPENDED` | 403 | no | Principal suspendido |
| `IAM_AGENT_AUTH_FAILED` | 401 | no | client_id/client_secret de agente inválidos |
| `IAM_AGENT_SUSPENDED` | 403 | no | Agente suspendido |
| `IAM_DELEGATION_NOT_ALLOWED` | 403 | no | El subject no es el owner del agente |
| `IAM_NO_CAPABILITIES` | 403 | no | Agente sin grants vigentes |
| `IAM_UNSUPPORTED_GRANT` | 400 | no | grant_type no soportado en /iam/v1/token |
| `IAM_NOT_AGENT_OWNER` | 403 | no | Solo el dueño puede otorgar capacidades |

## PDP (razones de decisión en /iam/v1/authorize)

| Razón | Significado |
|---|---|
| `OK` | Permitido |
| `CAP_DENIED` | Coincide patrón deny del capability token |
| `CAP_NOT_GRANTED` | Modelo/operación no otorgados en el cap |
| `CAP_AMOUNT_EXCEEDED` | Supera max_amount_per_op |
| `CAP_DAILY_LIMIT` | Supera max_amount_per_day acumulado |
| `CAP_LIMIT_BACKEND_DOWN` | Contador de límites caído (fail-closed) |
| `RBAC_DENIED` | Usuario efectivo sin ACL para la operación |

## Aprobaciones HITL

| Código | HTTP | Retryable | Significado |
|---|---|---|---|
| `IAM_APPROVAL_NOT_FOUND` | 404 | no | Solicitud inexistente o de otro agente |
| `IAM_APPROVAL_PENDING` | 409 | sí | Aún sin resolver; reintentar con la misma Idempotency-Key |
| `IAM_APPROVAL_REJECTED` | 403 | no | El aprobador rechazó la operación |
| `IAM_APPROVAL_EXPIRED` | 410 | no | Venció la ventana de aprobación |
| `IAM_APPROVAL_CONSUMED` | 409 | no | Ya se ejecutó (una aprobación ejecuta una sola vez) |
| `IAM_APPROVAL_MISMATCH` | 409 | no | La operación no coincide byte a byte con lo aprobado |
| `IAM_NOT_APPROVER` | 403 | no | Solo el dueño del agente puede resolver |
| `IAM_IDEMPOTENCY_KEY_REQUIRED` | 400 | no | Falta header Idempotency-Key |

## Canales de notificación (HITL por Telegram)

| Código | HTTP | Retryable | Significado |
|---|---|---|---|
| `IAM_LINK_CODE_INVALID` | 400 | no | Código de vinculación inexistente, vencido o ya usado |
| `IAM_CHANNEL_ALREADY_LINKED` | 409 | no | La dirección ya está vinculada a otro principal |
| `IAM_CHANNEL_NOT_VERIFIED` | 403 | no | El chat no está verificado para resolver aprobaciones |
| `IAM_CALLBACK_INVALID` | 403 | no | Callback sin firma HMAC válida del servidor |
| `IAM_WEBHOOK_UNAUTHORIZED` | 403 | no | Falta o no coincide el secreto del webhook |
| `IAM_TELEGRAM_NOT_CONFIGURED` | 503 | no | Falta TELEGRAM_BOT_TOKEN o TELEGRAM_WEBHOOK_SECRET |
| `IAM_TELEGRAM_DELIVERY_FAILED` | 502 | sí | Telegram no aceptó el envío; el job reintenta con backoff |

## Contabilidad e impuestos

| Código | HTTP | Retryable | Significado |
|---|---|---|---|
| `ACCOUNT_MOVE_EMPTY` | 400 | no | Un asiento necesita al menos una partida |
| `ACCOUNT_NEGATIVE_AMOUNT` | 400 | no | Debe o haber negativo; invierte el lado, no el signo |
| `ACCOUNT_LINE_BOTH_SIDES` | 400 | no | Una partida no lleva debe y haber a la vez |
| `ACCOUNT_LINE_EMPTY` | 400 | no | Debe o haber tiene que ser distinto de cero |
| `ACCOUNT_LINE_NO_ACCOUNT` | 400 | no | Falta la cuenta contable de la partida |
| `ACCOUNT_UNBALANCED` | 400 | no | La suma del debe no iguala a la del haber |
| `ACCOUNT_FLOAT_AMOUNT` | 400 | no | Los importes contables no admiten float |
| `ACCOUNT_ALREADY_POSTED` | 409 | no | El asiento ya está contabilizado |
| `ACCOUNT_MOVE_CANCELLED` | 409 | no | Un asiento anulado no se contabiliza |
| `ACCOUNT_POSTED_IMMUTABLE` | 409 | no | Un asiento contabilizado no se anula: emite reversión |
| `ACCOUNT_NOT_POSTED` | 409 | no | Solo se revierte un asiento contabilizado |
| `ACCOUNT_MOVE_NOT_FOUND` | 404 | no | El asiento no existe |
| `ACCOUNT_PERIOD_LOCKED` | 409 | no | El período está cerrado y no admite asientos |
| `TAX_FLOAT_RATE` | 400 | no | Las tasas de impuesto no admiten float |
| `TAX_FLOAT_AMOUNT` | 400 | no | Los importes del motor de impuestos no admiten float |
| `TAX_INVALID_TYPE` | 400 | no | Tipo de impuesto desconocido (percent o fixed) |
| `TAX_INVALID_LINE` | 400 | no | La línea del documento está malformada |
| `TAXID_INVALID_FORMAT` | 400 | no | El identificador fiscal no tiene el formato del país |
| `TAXID_INVALID_CHECK_DIGIT` | 400 | no | Dígito verificador incorrecto |

## Facturación electrónica

| Código | HTTP | Retryable | Significado |
|---|---|---|---|
| `EDI_INVALID_TRANSITION` | 409 | no | La acción no es válida desde el estado actual |
| `EDI_UNKNOWN_STATE` | 500 | no | Estado fuera de la máquina; corrupción de datos |
| `EDI_NO_ADAPTER` | 501 | no | No hay adaptador registrado para el país |
| `EDI_DOCUMENT_NOT_FOUND` | 404 | no | El documento no existe |
| `EDI_NOT_GENERATED` | 409 | no | No hay XML que firmar; genera primero |
| `EDI_FOLIO_EXHAUSTED` | 409 | no | El rango de numeración autorizado se agotó |
| `EDI_FOLIO_EXPIRED` | 409 | no | La autorización de numeración está vencida |
| `EDI_CANCEL_UNSUPPORTED` | 409 | no | El país no anula aceptados: emite nota de crédito |
| `CL_CAF_INVALID_XML` | 400 | no | El CAF no es XML válido |
| `CL_CAF_INCOMPLETE` | 400 | no | Al CAF le faltan elementos obligatorios |
| `CL_CAF_NO_KEY` | 400 | no | El CAF no trae la clave privada RSASK |
| `CL_CAF_BAD_KEY` | 400 | no | La clave del CAF no se pudo leer o no es RSA |
| `CL_FOLIO_OUT_OF_CAF` | 409 | no | El folio no pertenece al rango del CAF |
| `CL_CAF_WRONG_TYPE` | 409 | no | El CAF autoriza otro tipo de documento |
| `CL_DTE_EMPTY` | 400 | no | Un DTE sin líneas no es un documento |
| `CL_DTE_NO_CAF` | 409 | no | El rango de folios no trae el CAF |
| `CL_DTE_REFERENCE_REQUIRED` | 400 | no | Una NC/ND debe referenciar al documento corregido |
| `PY_CDC_BAD_RUC` | 400 | no | RUC del CDC no numérico o de más de 8 dígitos |
| `PY_CDC_BAD_NUMBER` | 400 | no | Número de documento fuera de 7 dígitos |
| `PY_CDC_BAD_SECURITY_CODE` | 400 | no | Código de seguridad fuera de 9 dígitos |
| `PY_CDC_BAD_LENGTH` | 400 | no | Un CDC tiene exactamente 44 dígitos |
| `PY_CDC_BAD_CHECK_DIGIT` | 400 | no | Dígito verificador del CDC incorrecto |
| `PY_DE_EMPTY` | 400 | no | Un DE sin ítems no es un documento |
| `PY_DE_NO_TIMBRADO` | 409 | no | El rango de numeración no trae el timbrado |
| `PY_DE_UNKNOWN_RATE` | 400 | no | El SIFEN solo conoce IVA 10 %, 5 % y exento |

## Ventas y compras

| Código | HTTP | Retryable | Significado |
|---|---|---|---|
| `SALE_ORDER_EMPTY` | 400 | no | La orden necesita al menos una línea |
| `SALE_ORDER_NOT_FOUND` | 404 | no | La orden no existe |
| `SALE_INVALID_TRANSITION` | 409 | no | La acción no es válida en el estado actual |
| `SALE_TAX_UNKNOWN` | 400 | no | Código de impuesto inexistente o no aplicable a ventas |
| `SALE_NO_ACCOUNT` | 409 | no | Línea sin cuenta y diario sin cuenta por defecto |
| `SALE_ZERO_TOTAL` | 400 | no | El documento redondea a cero; nada que asentar |
| `PURCHASE_ORDER_EMPTY` | 400 | no | La orden necesita al menos una línea |
| `PURCHASE_ORDER_NOT_FOUND` | 404 | no | La orden no existe |
| `PURCHASE_INVALID_TRANSITION` | 409 | no | La acción no es válida en el estado actual |
| `PURCHASE_TAX_UNKNOWN` | 400 | no | Código de impuesto inexistente o no aplicable a compras |
| `PURCHASE_NO_ACCOUNT` | 409 | no | Línea sin cuenta y diario sin cuenta por defecto |
| `PURCHASE_ZERO_TOTAL` | 400 | no | El documento redondea a cero; nada que asentar |
| `PURCHASE_VENDOR_REF_REQUIRED` | 400 | no | Falta el número de la factura del proveedor |
| `ACCOUNT_SETTINGS_MISSING` | 409 | no | La compañía no tiene configuración contable |
| `ACCOUNT_TAX_NO_ACCOUNT` | 409 | no | El impuesto no tiene cuenta contable asignada |
