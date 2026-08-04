# Changelog

Formato: [Keep a Changelog](https://keepachangelog.com/es/) + Conventional Commits (changelog automático vía commitizen desde F2).

## [Unreleased]

### Added

- **F5.3** Notas de crédito comerciales: `action_credit_note` en ventas y compras
  revierte la factura completa con motivo obligatorio y deja la orden en `credited`
  (estado y asiento cambian juntos; el neto por cuenta tras factura + NC es exactamente
  cero, verificado). `action_einvoice_credit_note` emite la nota de crédito electrónica
  (DTE 61 en Chile, DE tipo 5 en Paraguay) referenciando el documento original — que
  debe estar enviado o aceptado; uno en borrador se anula, no se corrige. 6 tests de
  integración nuevos.

- **F4.4** Tesorería (diseño F4-04): `account.payment` con asiento automático al
  contabilizar (banco contra por cobrar/pagar; un pago contabilizado se revierte, no se
  anula); conciliación de partidas con grupo explícito (`account.reconcile`) que exige
  misma cuenta, cuenta conciliable, asientos contabilizados y suma exactamente cero,
  más `open_items()` para que un agente elija qué saldar; extractos bancarios con
  emparejamiento automático conservador (solo con candidato único por importe; ante
  ambigüedad no adivina) y validación que exige todo emparejado y cuadrado contra los
  saldos del banco. Registro de reportes en el kernel (`ordo_core.reports`, simétrico
  al de acciones) con `GET /api/v1/reports/{name}`: balance de comprobación, estado de
  resultados y balance general, cada uno con su check de cuadratura. Todo expuesto
  también como acciones con `requires_approval` donde corresponde. 13 tests de
  integración de tesorería y 3 de API nuevos.

- **F5.2** Acciones de negocio por la API: registro `ordo_core.actions` con decorador
  (`actions.py` por módulo, importado por el loader), descubrimiento en
  `GET /api/v1/{model}/actions` con metadato `requires_approval` para el PDP, y
  ejecución en `POST /api/v1/{model}/{id}/actions/{action}` con el contrato de toda
  escritura: Idempotency-Key obligatorio, `dry_run` que ejecuta dentro de un savepoint
  y lo revierte todo (una confirmación simulada no quema número legal, verificado), y
  evento al outbox en la misma transacción. Expuestas: post/reverse/cancel de asientos,
  confirmar/facturar/cancelar ventas y compras, y `action_einvoice` que emite el DTE
  desde la orden vía un puente que valida identificadores tributarios y resuelve
  impuestos. `ordo-api` además deja de arrancar con registry vacío: carga los módulos
  de `ORDO_MODULES_PATH`. Firmar/enviar quedan fuera hasta tener vault y ambiente de
  certificación. 4 tests unitarios y 9 de integración HTTP nuevos.

### Changed

- **Deuda saldada.** (1) La cola de jobs vuelve a ser una sola: `ordo-iam` ahora depende
  de `ordo-core` (paquete interno, actualización en ADR-011) y se elimina la copia
  `ordo_iam/jobs.py` que podía divergir. (2) El worker de notificaciones existe de
  verdad: un loop de fondo en el proceso IAM drena `ir_job` cada pocos segundos cuando
  hay canal configurado (`TELEGRAM_BOT_TOKEN`), con kill switch `ORDO_NOTIFY_WORKER=0`;
  varias réplicas no duplican envíos porque el claim usa FOR UPDATE SKIP LOCKED.
  (3) Los documentos electrónicos guardan `payload_encoding` y firman/envían con los
  bytes de la codificación que exige el formato del país (ISO-8859-1 en el SII): una ñ
  ya no se corrompe entre generar y enviar, verificado de punta a punta. 8 tests nuevos.

### Added

- **F4.3b** Firma XMLDSig de documento completo (ADR-014 aceptado): `XmlDSigSigner`
  implementa la interfaz `Signer` con firma enveloped vía `signxml` + `lxml`, nuevas
  dependencias sancionadas por el ADR. RSA-SHA256 por defecto; RSA-SHA1 disponible solo
  porque el formato del SII lo exige, habilitado por instancia y nunca global. La
  verificación (`verify_signature`) usa la configuración segura por defecto y detecta
  manipulación del contenido y certificado incorrecto, con códigos estables
  (`EDI_SIGN_*`). El material criptográfico llega por parámetro (vault en producción);
  nunca toca la base de datos. 6 tests de módulo y 1 de integración nuevos.

- **F5.1** Ventas y compras con asiento automático (diseño F5-01): `sale.order` y
  `purchase.order` con transiciones explícitas (confirmar fija totales con el motor de
  impuestos y toma número de secuencia; facturar crea y contabiliza el asiento en la
  misma operación; una orden facturada no se cancela, se revierte su asiento). El
  impuesto pasa a ser registro (`account.tax`) con su cuenta contable y su lado
  (venta/compra/ambos), y `account.settings` define por cobrar y por pagar por
  compañía. El constructor de partidas es común a ambos módulos y está probado con
  hypothesis: cualquier combinación de líneas, descuentos y retenciones produce un
  asiento que cuadra o un error estable (`*_ZERO_TOTAL` cuando el documento redondea a
  cero). Retenciones reducen la contrapartida y quedan en su propia cuenta. 8 tests de
  propiedad y 10 de integración nuevos.
- **F4.3** Framework de facturación electrónica (ADR-014): documento electrónico como
  máquina de estados explícita (draft, generated, signed, sent, accepted, rejected,
  contingency, cancelled) donde cada transición es un método y las inválidas fallan con
  código estable; rangos de numeración autorizados como concepto común (el CAF chileno y
  el timbrado paraguayo son lo mismo con otro nombre), con folio quemado que no se
  recicla y errores propios al agotarse o vencer; certificados solo como metadatos con
  referencia al vault, nunca la clave en la base. Adaptador SII (Chile): parser del CAF,
  timbre TED firmado de verdad con RSA-SHA1 y la clave del CAF, DTE 33/34/39/52/56/61
  con referencia obligatoria en NC/ND, sobre EnvioDTE con carátula y subtotales, y
  lectura de acuses (TRACKID, EPR/RCT y familia). Adaptador SIFEN (Paraguay): CDC de 44
  dígitos con módulo 11 verificable, XML del DE con subtotales por tasa (10 %, 5 %,
  exento; una tasa desconocida es error), URL del QR firmada con el CSC vía SHA-256, y
  respuestas 0260/0300/1xxx. La firma XMLDSig de documento completo queda detrás de una
  interfaz hasta aprobar sus dependencias (ADR-014). 47 tests de módulo y 7 de
  integración nuevos.

- **F1.7** Telegram como primer canal HITL: el aprobador resuelve la solicitud con dos
  botones y queda en el mismo estado que por API. El vinculo chat-principal se establece
  con un codigo de un solo uso, de 10 minutos, emitido por un endpoint autenticado y
  guardado solo como sha256: un chat_id por si mismo no prueba identidad. El callback_data
  va firmado con HMAC derivado del secreto del servidor y el webhook exige la cabecera
  secreta antes de leer el cuerpo; una firma valida desde otro usuario no aprueba nada,
  porque la resolucion sigue exigiendo que el aprobador sea el dueno del agente. El aviso
  se encola como job en la misma transaccion que crea la aprobacion: el request nunca sale
  a la red. 36 tests nuevos.
- **F2.6** Chatter como canal agente-humano: mensajes con author_kind obligatorio (user,
  agent o system, para que quien lee un hilo distinga persona de agente sin inferirlo),
  seguidores, actividades con estado derivado de la fecha, y tracking automatico de
  cambios con valor anterior y nuevo. Adjuntos con deduplicacion por sha256: dos archivos
  identicos comparten objeto y borrar uno no borra los bytes del otro; tamano, checksum
  y mimetype se derivan del contenido, nunca se confian del cliente.
- **E2E del kernel**: ciclo completo de un documento (schema semantico, secuencia legal,
  dry-run, creacion, chatter, bloqueo optimista, adjuntos, outbox y jobs) contra Postgres
  real, con tenant aislado por test. 19 tests nuevos.
- **F2.5** Servicios transversales del kernel: secuencias con modo no_gap que bloquea la
  fila (documentos legales sin huecos, verificado con 5 sesiones concurrentes), cola de jobs
  en Postgres con FOR UPDATE SKIP LOCKED (dos workers nunca toman el mismo job), reintentos
  con backoff exponencial y DLQ, cron con lock de fila que avanza next_call antes de
  ejecutar, y outbox transaccional con relay idempotente (message id = id del outbox, para
  que el broker deduplique tras un crash).
- **Schema semantico** generado desde el registry (`GET /meta/v1/schema`, formato llm o
  full) con convenciones para agentes: dinero como string decimal, dry_run e Idempotency-Key
  en escrituras, paginacion por cursor. 25 tests nuevos.
- **F2.4** ORM de escritura y API generica: RecordSet batch-first (create/read/write/
  unlink/search) con validaciones (required, readonly, selection, tipos, Monetary rechaza
  float), bloqueo optimista que devuelve el estado actual del registro en el 409,
  paginacion por cursor (nunca offset), dry-run universal que hace rollback siempre,
  idempotencia con respuesta cacheada 24h y deteccion de reuso, y transacciones
  multi-operacion atomicas o con reporte parcial por indice. Endpoints /api/v1/{model},
  batch y tx. 47 tests nuevos.
- **Seguridad**: el binding de tenant se re-aplica en cada transaccion nueva de la sesion;
  antes un commit a mitad de request dejaba las consultas siguientes sin filtro.
- **F2.3** Campos calculados: decorador @depends, grafo de dependencias con orden
  topologico y deteccion de ciclos al construir el registry (falla el boot, no en runtime),
  recomputacion siempre en lote (N+1 imposible por diseno), campos related resueltos como
  compute con dependencia en cada segmento de la ruta, cache por transaccion con
  invalidacion en cascada. Filtrar por calculado no almacenado se rechaza con
  DOMAIN_FIELD_NOT_STORED. 20 tests nuevos.
- **F2.2** Compilador de dominios a SQL (ADR-006): sintaxis prefija con tuplas, operadores de
  comparacion y logicos con notacion polaca, joins por rutas punteadas (max 4 saltos),
  record rules global AND / rol OR, active_test. Garantias: cero interpolacion (todo
  parametro vinculado), identificadores validados contra el registry, limites de tamano.
  52 tests: 7 de inyeccion, 4 property-based (Hypothesis) y 11 que ejecutan el SQL
  generado contra Postgres real.
- **F2.1** Kernel `ordo-core`: sistema de campos (Monetary solo Decimal), registry con
  grafo topologico de modulos, herencia por extension (_inherit) y delegacion (_inherits),
  agent_hint/examples obligatorios en campos de negocio, metadatos en ir_model/ir_model_field
  y Environment con schema-per-tenant + RLS.
- **Seguridad**: rol `ordo_app` sin SUPERUSER ni BYPASSRLS. Conectarse con el rol dueno
  dejaba RLS inerte (segunda barrera de ADR-002 no se aplicaba). Environment fuerza
  `SET LOCAL ROLE` en cada transaccion. 15 tests nuevos de aislamiento y registry.
- **F1.6** Aprobaciones HITL: iam_approval_request con operacion serializada y sellada por
  hash (se ejecuta exactamente lo aprobado), creacion idempotente por Idempotency-Key,
  approve/reject solo por el dueno, consumo unico, expiracion 24h; cada transicion auditada.
  Suite e2e contra Keycloak real: login OIDC, vinculacion, agente, grant, token exchange
  con act, PDP allow/deny/monto, HITL completo y verificacion de la cadena de auditoria.
  Nuevo job e2e en CI. 11 tests nuevos.
- **F1.5** PDP tres capas (cap primero, RBAC del usuario efectivo, record rules global
  AND / rol OR) con denegacion por defecto, limites monetarios en Decimal y acumulados
  diarios en micros enteros (Redis, fail-closed), POST /iam/v1/authorize, y auditoria
  append-only con cadena de hash por tenant (deteccion de tamper y borrado). 24 tests.
- **F1.3/F1.4** Token exchange RFC 8693: POST /iam/v1/token (agente autenticado por secret
  intercambia token del owner por JWT propio con act, tenant, cap merged, jti, exp 900s),
  registro de agentes POST /iam/v1/agents (secret una sola vez), grants por owner,
  JWKS propio en /iam/v1/jwks. Merge de caps: union modelos/deny/requires_approval,
  limites al minimo, record_domain AND. 19 tests nuevos. (ADR-004)
- **F1.2** Bridge OIDC: verificador JWT genérico (JWKS con caché y refetch ante rotación,
  solo RS256/ES256, rechaza alg=none y confusión de clave), bridge `idp_sub`→`iam_user`
  con vinculación en primer login verificado y sin auto-creación, endpoint `GET /iam/v1/me`.
  Realm Keycloak con claim `tenant` y audiencia `ordo-api`. 12 tests de seguridad unit +
  12 integración nuevos. (ADR-003)
- **F1.1** Modelo de datos de principals en `ordo-iam`: `iam_principal`, `iam_user`,
  `iam_service_client`, `iam_agent`, `iam_capability_grant`. Migración Alembic 0001.
  Invariantes: owner activo y mismo tenant, email único por tenant (case-insensitive),
  denegación por defecto (sin grants vigentes = sin capacidades), suspensión en cascada
  owner→agentes. Códigos de error `IAM_*` en `docs/api/errors.md`. (ADR-003, ADR-004, ADR-011)
- **F0** Bootstrap completo: provisioning Ansible, stack compose, ordo-runtime,
  7 esqueletos de servicio, CI/CD, suite agéntica, backups pgBackRest con restore probado,
  runbook, ADRs 001–010.
