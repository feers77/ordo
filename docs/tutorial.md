# Tutorial: de cero a un asiento contabilizado

Este recorrido levanta ORDO, registra un agente, le da permisos acotados y lo pone a
emitir un documento contable. Al final vas a haber visto las cuatro cosas que hacen
distinto a este ERP: descubrimiento en runtime, simulación antes de escribir,
idempotencia y autorización humana.

Todo lo que aparece aquí funciona hoy. Lo que todavía no existe está marcado.

## 1. Levantar el entorno

```bash
git clone https://github.com/feers77/ordo.git && cd ordo
cp infra/compose/.env.example infra/compose/.env   # completa las contraseñas
bash infra/compose/minio/gen-certs.sh              # certificados locales de MinIO
uv sync
make up                                            # PostgreSQL, Redis, NATS, MinIO, Keycloak
make health                                        # todo debe decir healthy
```

Si algo no arranca, `docs/runbook.md` tiene el procedimiento de diagnóstico.

## 2. Preparar un tenant

Cada cliente vive en su propio schema de PostgreSQL, con RLS como segunda barrera.
Crear uno es instalar los módulos que va a usar:

```python
from pathlib import Path
from ordo_core import Environment, Registry
from ordo_core.modules import ModuleLoader
from ordo_core.installer import ModuleInstaller
from ordo_core.services.schema import create_kernel_tables

loader = ModuleLoader([Path("modules")])
registry = Registry.build(loader.load())

env = Environment(session=session, tenant="acme", registry=registry)
await env.bind()                      # fija schema y filtro de tenant
await create_kernel_tables(session)

installer = ModuleInstaller(session, registry, loader.models_by_module)
manifests = loader.discover()
for name in ("base", "account"):
    await installer.install(manifests[name])
await session.commit()
```

`env.bind()` es lo que hace que el resto del código no tenga que pensar en el tenant:
fija el schema, la variable que usa RLS y el rol de base de datos sin privilegios.

## 3. Datos mínimos: moneda, compañía, cuentas

```python
from ordo_core.recordset import RecordSet

currencies = RecordSet(env, "res.currency")
[clp] = await currencies.create([
    {"name": "CLP", "symbol": "$", "decimal_places": "0"},
])

companies = RecordSet(env, "res.company")
[company] = await companies.create([
    {"name": "ACME SpA", "vat": "76.123.456-0", "currency_id": clp, "country_code": "CL"},
])

accounts = RecordSet(env, "account.account")
clientes, ventas = await accounts.create([
    {"code": "1201", "name": "Clientes", "account_type": "asset",
     "reconcile": True, "company_id": company},
    {"code": "4101", "name": "Ventas", "account_type": "income", "company_id": company},
])
```

El RUT se valida con su dígito verificador:

```python
from ordo_core.taxid import validate_rut
validate_rut("76.123.456-0")   # -> "76123456-0"
validate_rut("76.123.456-7")   # -> TaxIdError: debería ser 0, no 7
```

## 4. Un agente descubre qué puede hacer

Aquí empieza lo que distingue a ORDO. El agente no lee documentación: le pregunta al
sistema.

```bash
curl "$ORDO/meta/v1/schema?models=account.move&format=llm"
```

Cada campo viene con una explicación en lenguaje llano y ejemplos. Del campo `state`
de un asiento, por ejemplo, el sistema dice que un asiento contabilizado no se
modifica ni se borra, y que hay que usar `action_post` en vez de escribirlo.

## 5. Registrar el agente y darle permisos acotados

```bash
# 1. La persona registra el agente. El secreto se muestra UNA vez.
curl -X POST "$ORDO/iam/v1/agents" \
     -H "Authorization: Bearer $TOKEN_USUARIO" \
     -d '{"display_name": "Contador automático", "model": "agente-v1"}'
# -> {"agent_id": "...", "agent_secret": "..."}

# 2. La persona le concede permisos con límites explícitos
curl -X POST "$ORDO/iam/v1/agents/$AGENT_ID/grants" \
     -H "Authorization: Bearer $TOKEN_USUARIO" \
     -d '{"cap": {
            "models": {"account.move": ["read", "create"]},
            "limits": {"max_amount_per_op": {"CLP": 5000000}},
            "requires_approval": ["account.move.action_post"],
            "deny": ["res.users.*"]
          }}'
```

Lee ese `cap` con calma: el agente puede crear asientos hasta cinco millones, pero
**contabilizarlos requiere que una persona apruebe**, y nunca podrá tocar usuarios.

## 6. El agente obtiene su token

```bash
curl -X POST "$ORDO/iam/v1/token" \
  -d "grant_type=urn:ietf:params:oauth:grant-type:token-exchange" \
  -d "subject_token=$TOKEN_USUARIO" \
  -d "subject_token_type=urn:ietf:params:oauth:token-type:access_token" \
  -d "client_id=$AGENT_ID" -d "client_secret=$AGENT_SECRET"
```

El token resultante dice `sub: agent:...` y `act: {sub: user:...}`: queda registrado
que actuó un agente **en nombre de** una persona concreta. Vive 15 minutos y no tiene
refresh: si la persona pierde permisos, el agente deja de poder actuar. El porqué está
en `ADR/ADR-013-tokens-autenticacion.md`.

## 7. Simular antes de escribir

```bash
curl -X POST "$ORDO/api/v1/account.move?dry_run=true" \
     -H "Authorization: Bearer $TOKEN_AGENTE" -H "X-Ordo-Tenant: acme" \
     -d '{"values": {"journal_id": 1, "date": "2026-08-04", "currency_id": 1}}'
```

Devuelve lo que pasaría y qué validaciones fallarían, **sin escribir nada**, ni
siquiera parcialmente. Un agente que duda puede probar sin consecuencias.

## 8. Escribir, con idempotencia

```bash
KEY=$(uuidgen)
curl -X POST "$ORDO/api/v1/account.move" \
     -H "Authorization: Bearer $TOKEN_AGENTE" -H "X-Ordo-Tenant: acme" \
     -H "Idempotency-Key: $KEY" \
     -d '{"values": {...}}'
```

Repetir la llamada con la misma clave devuelve la misma respuesta sin crear un segundo
asiento. Es lo que permite que un agente reintente tras un timeout sin duplicar la
contabilidad. Reusar la clave con un cuerpo distinto es un error explícito, no un
sobrescrito silencioso.

## 9. Contabilizar: aquí entra la persona

El `cap` del agente marcaba `account.move.action_post` como operación que requiere
aprobación. Al intentarlo:

```bash
curl -X POST "$ORDO/iam/v1/authorize" \
     -H "Authorization: Bearer $TOKEN_AGENTE" \
     -d '{"model": "account.move", "operation": "action_post"}'
# -> {"allowed": true, "requires_approval": true, ...}
```

El agente crea entonces una solicitud de aprobación, la persona la resuelve, y solo
entonces el agente ejecuta **exactamente** lo aprobado: la operación se guarda sellada
por hash, así que intentar consumir la aprobación con otros datos falla con
`IAM_APPROVAL_MISMATCH`.

```bash
curl -X POST "$ORDO/iam/v1/approvals" \
     -H "Authorization: Bearer $TOKEN_AGENTE" -H "Idempotency-Key: $KEY" \
     -d '{"operation": {"model": "account.move", "operation": "action_post",
                        "payload": {"move_id": 42}}}'
# -> 201 {"approval_id": "...", "status": "pending", "expires_at": "..."}
```

## 10. Lo que ve la contabilidad

Cuando el asiento se contabiliza:

- toma su número de una secuencia **sin huecos**, y lo toma al contabilizar, no al
  crear el borrador: un borrador descartado no consume numeración;
- queda inmutable. Corregirlo es emitir una reversión, y el original permanece;
- si el período está cerrado, falla con `ACCOUNT_PERIOD_LOCKED` en vez de escribir en
  un ejercicio ya declarado.

```python
from modules.account.services import AccountingService

service = AccountingService(env)
move_id = await service.create_move(
    journal_id=journal, move_date="2026-08-04", currency_id=clp,
    company_id=company,
    lines=[
        {"account_id": clientes, "debit": Decimal("119000"), "name": "Cliente"},
        {"account_id": ventas,   "credit": Decimal("119000"), "name": "Venta"},
    ],
)
numero = await service.action_post(move_id)     # "VTA/2026/00001"
reversion = await service.action_reverse(move_id)  # el original queda intacto
```

Si las partidas no cuadran, `create_move` falla antes de tocar la base. No existe el
estado "asiento descuadrado guardado".

## 11. Impuestos

```python
from decimal import Decimal
from modules.account.taxes import Tax, compute_line

iva = Tax(code="IVA19", name="IVA 19%", amount=Decimal("19"))
resultado = compute_line(price_unit="100000", taxes=[iva], decimals=0)
# base 100.000 · impuesto 19.000 · total 119.000
```

Los importes son siempre `Decimal` o string decimal. Pasar un `float` es un error
explícito: en contabilidad, el redondeo binario produce descuadres que aparecen meses
después.

## 12. Localizaciones

```python
from pathlib import Path
from ordo_core.localization import load_pack

pack = load_pack(Path("localizations/cl"))
pack.review_state              # "draft"
pack.needs_professional_review # True
```

**Ambos packs, Chile y Paraguay, están en borrador.** Contienen lo que se puede
afirmar citando la norma —tasa general de IVA, códigos de documento electrónico,
algoritmo del dígito verificador— pero el plan de cuentas, los impuestos específicos y
las retenciones **necesitan revisión de un contador de cada país** antes de usarse
para declarar impuestos. Cada manifiesto dice exactamente qué falta.

El framework rechaza cargar un pack que no cite sus fuentes normativas.

## 13. Ventas y compras: el asiento se genera solo

Una orden de venta se confirma (fija totales y toma número) y se factura (crea
y contabiliza el asiento en la misma operación). Los impuestos se referencian
por código y viven en `account.tax`, cada uno con su cuenta contable:

```python
from modules.sale.services import SaleService

service = SaleService(env)
order_id = await service.create_order(
    partner_id=cliente,
    date_order="2026-08-04",
    currency_id=clp,
    journal_id=diario_ventas,
    company_id=compania,
    lines=[{
        "name": "Licencia anual",
        "quantity": "1",
        "price_unit": Decimal("100000"),
        "tax_codes": "IVA19",
    }],
)
await service.action_confirm(order_id)   # "SO/00001", totales fijados
move_id = await service.action_invoice(order_id)
# El asiento quedó contabilizado: cliente 119.000 al debe,
# venta 100.000 y IVA débito 19.000 al haber.
```

Compras es el espejo (`PurchaseService.action_bill`, que exige el número de la
factura del proveedor). Una orden facturada no se cancela: se revierte su
asiento. Requisitos previos: una fila en `account.settings` con las cuentas por
cobrar y por pagar, e impuestos con `account_id` asignado.

## 14. Facturación electrónica

El módulo `einvoicing` lleva el documento por su ciclo:
`draft → generated → signed → sent → accepted/rejected`, con `contingency`
para cuando la autoridad está caída. El folio sale de un rango autorizado
(`edi.folio.range`): el CAF del SII o el timbrado del SIFEN.

```python
from modules.einvoicing.contracts import AdapterRegistry
from modules.einvoicing.services import EinvoicingService
from localizations.cl.einvoicing import SiiAdapter

registry = AdapterRegistry()
registry.register(SiiAdapter())
service = EinvoicingService(env, registry)

doc_id = await service.create_document(
    country_code="cl", document_type_code="33", company_id=compania,
)
folio = await service.action_generate(doc_id, invoice_data)  # asigna folio, arma el DTE
await service.action_sign(doc_id, signer)                    # firma inyectada
track = await service.action_send(doc_id, transport)         # TrackID del SII
estado = await service.action_check(doc_id, transport)       # accepted / rejected
```

El timbre TED chileno se firma de verdad (RSA-SHA1 con la clave del CAF). La
firma XMLDSig del documento completo queda detrás de la interfaz `Signer`
hasta aprobar sus dependencias (ADR-014); el envío productivo requiere además
certificados en el vault y el ambiente de certificación de cada autoridad.

## Qué no existe todavía

La firma XMLDSig productiva y el transporte real hacia SII/SIFEN (ADR-014
pendiente de aprobación), conciliación bancaria, reportes financieros, el
servidor MCP y el módulo de inventario. El detalle está en `PLAN-MAESTRO.md` y
en `docs/design/F2-00-resumen.md`.

## Siguiente paso

Para escribir tu propio módulo:

```bash
make new-module NAME=ventas DEPENDS=base
uv run pytest modules/ventas
```

El esqueleto ya carga, ya pasa sus tests y ya cumple la regla de que todo campo de
negocio explique qué significa.
