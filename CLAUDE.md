# CLAUDE.md — Reglas de trabajo para ORDO ERP

Este archivo va en la raíz del repositorio. Claude Code lo lee en cada sesión. Es vinculante.

---

## 1. Qué es este proyecto

ERP/CRM completo **solo backend (API)**, con paridad funcional respecto a Odoo Community más equivalentes propios de las funciones Enterprise, diseñado para que el operador principal sea un **agente de IA**, no un humano frente a una pantalla.

**No se escribe frontend. Nunca.** Si una tarea parece requerir UI, el entregable es un endpoint más el schema que permite a un cliente construir esa UI.

---

## 2. Prohibiciones absolutas

0. **Licencia del proyecto: AGPLv3** (ADR-010). Todo aporte se publica bajo ella; se contribuye con DCO (`git commit -s`), no con CLA.
1. **No copiar código, datos ni traducciones de Odoo** (ni de repos derivados). Odoo Community es LGPLv3; copiar contamina la licencia del producto. Se permite estudiar el comportamiento observable y reimplementarlo. Los planes de cuentas y tablas de impuestos se obtienen de **fuentes normativas oficiales**, citadas en `manifest.yaml` de cada pack.
2. **No escribir un OpenID Provider desde cero** antes de la Fase 3. Hasta entonces, Keycloak.
3. **No usar `float` para dinero.** `Decimal` en Python, `NUMERIC` en Postgres, string decimal en JSON.
4. **No hacer `datetime.now()` sin tz.** Siempre UTC explícito.
5. **No escribir SQL con interpolación de strings.** El compilador de dominios usa parámetros vinculados, siempre.
6. **No borrar ni modificar asientos contables contabilizados.** Corrección = asiento de reversión.
7. **No introducir un servicio, framework o dependencia nueva** sin un ADR aprobado.
8. **No hacer commit de secretos.** `.env` fuera de git; usar el gestor de secretos definido en infra.
9. **No romper compatibilidad de API** sin bump de versión y anuncio de deprecación.
10. **No marcar una tarea como terminada sin tests que la cubran.**

---

## 3. Flujo de trabajo por tarea

1. Leer el issue y el ADR relevante. Si falta el ADR y la decisión es estructural, **escribir el ADR primero y esperar aprobación**.
2. Escribir el diseño en el issue: modelos, campos, endpoints, eventos, errores. Máx. 40 líneas.
3. Escribir los tests **antes** que la implementación cuando se trate de lógica de dominio (contabilidad, impuestos, stock, permisos).
4. Implementar.
5. Correr localmente: `make check` (ruff + mypy strict + pytest + contract tests).
6. Actualizar `docs/` y el schema semántico.
7. Abrir PR con la plantilla. Un PR = una unidad lógica. Si supera ~600 líneas de diff útil, dividir.

### Definition of Done

- [ ] Tests unitarios y de integración pasando
- [ ] Cobertura de la nueva lógica ≥ 85 %
- [ ] Endpoint documentado en OpenAPI con ejemplos reales
- [ ] Campos con `agent_hint` y `examples` poblados
- [ ] Errores con código estable registrado en `docs/api/errors.md`
- [ ] `dry_run` soportado si la operación escribe
- [ ] Idempotencia soportada si la operación escribe
- [ ] Evento emitido al outbox si la operación cambia estado de negocio
- [ ] Permisos: entrada en ACL y record rules cuando aplique
- [ ] Sin regresión de SLO en el test de carga del endpoint
- [ ] Registro en `CHANGELOG.md`

---

## 4. Reglas de código

### Python
- 3.12, type hints completos, `mypy --strict` en `packages/` y `services/`.
- `ruff` con la config del repo. Formato: `ruff format`.
- Async por defecto en la capa de I/O. Nada de `time.sleep`, `requests`, ni ORM síncrono.
- Docstrings en métodos públicos de dominio, en español o inglés — pero **consistente por paquete** (decisión: inglés en `packages/`, español permitido en `localizations/`).
- Excepciones de dominio derivan de `OrdoError` y llevan `code` estable.

### Base de datos
- Toda migración por Alembic, reversible, probada en CI contra un dump representativo.
- Índices explícitos para todo campo usado en dominios frecuentes.
- Nombres de tabla con prefijo del módulo: `account_move`, `stock_quant`.
- `NUMERIC(18,6)` para cantidades, `NUMERIC(18,2)` o precisión de moneda para importes.
- Toda tabla de negocio: `id, create_uid, create_date, write_uid, write_date, version, company_id`.

### Diseño de modelos
- Un modelo nuevo se declara con: propósito, invariantes, estados válidos y transiciones permitidas. Las transiciones se implementan como métodos explícitos (`action_confirm`), nunca escribiendo `state` directo desde la API.
- Todo campo de negocio lleva `agent_hint` (qué significa en lenguaje llano) y `examples`.

---

## 5. Estándar de errores

```json
{
  "error": {
    "code": "ACCOUNT_PERIOD_LOCKED",
    "message": "El período contable está cerrado y no admite asientos.",
    "model": "account.move",
    "record_id": 4821,
    "field": "date",
    "hint": "Usa una fecha posterior a 2026-06-30 o solicita reapertura del período.",
    "retryable": false,
    "requires_approval": false,
    "docs_url": "https://docs.ordo.example/errors/ACCOUNT_PERIOD_LOCKED",
    "trace_id": "01J9..."
  }
}
```

Los `code` son **contrato público**: se agregan, no se renombran ni se eliminan. `hint` se escribe pensando en que quien lo lee es un agente que debe decidir su siguiente acción.

---

## 6. Reglas específicas para la capa agéntica

- Cualquier operación de escritura nueva debe soportar `?dry_run=true` devolviendo exactamente lo que devolvería la operación real, más `validations[]` con lo que fallaría.
- Cualquier operación destructiva o de alto impacto (contabilizar, anular, eliminar, pagar, confirmar sobre cierto monto) debe declarar `requires_approval` en su metadato de acción, para que el PDP pueda exigir HITL.
- Ningún endpoint devuelve una lista sin paginación por cursor.
- Ningún endpoint obliga a más de 3 llamadas para completar un caso de uso de negocio común. Si las requiere, falta un endpoint compuesto.
- El schema semántico se genera, no se escribe a mano. Si un campo no tiene `agent_hint`, el generador falla el build.

---

## 7. Seguridad

- Denegación por defecto en el PDP.
- Toda consulta pasa por el filtro de tenant **en el kernel**, no en cada módulo. Cualquier PR que construya SQL saltándose el `Environment` se rechaza.
- Cambios en `packages/ordo-core/domain/`, `packages/ordo-core/security/` y `services/iam/` requieren **revisión humana obligatoria**. No se auto-mergean.
- Tests de aislamiento entre tenants corren en cada PR.
- Secretos y certificados de firma electrónica: nunca en base de datos en claro; usar el KMS/vault definido en infra.

---

## 8. Cuándo detenerte y preguntar

Detente y pide decisión humana si:
- La tarea implica una decisión fiscal o contable con interpretación normativa ambigua.
- Necesitas cambiar el contrato de un endpoint ya publicado.
- Una solución requiere degradar un invariante de integridad "temporalmente".
- El diseño requiere un servicio o dependencia nueva.
- Encontraste que la implementación existente tiene un defecto de seguridad: **repórtalo antes de arreglarlo**, para evaluar si hay que rotar credenciales o notificar.

---

## 9. Comandos del repo

```bash
make up          # levanta stack local (compose)
make migrate     # aplica migraciones
make seed        # datos de demo por tenant
make check       # lint + types + tests + contract
make test-load   # k6 contra el stack local
make test-agent  # suite agéntica (tareas de negocio end-to-end)
make schema      # regenera OpenAPI y schema semántico
make new-module NAME=foo   # scaffolding de módulo
make new-loc CC=cl         # scaffolding de pack de localización
```
