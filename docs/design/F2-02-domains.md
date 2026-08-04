# F2-02 — Compilador de dominios a SQL (diseño)

**Componente de mayor riesgo del kernel** (PLAN §10): una falla aquí filtra datos
entre tenants o permite inyección. Revisión humana obligatoria en cada cambio
(AGENTS.md §7). Property-based testing y tests de inyección son bloqueantes.

## Lenguaje (sintaxis prefija con tuplas, ADR-006)

```python
[("state", "=", "sale"), "|", ("partner_id.country_id.code", "=", "CL"),
                              ("amount_total", ">", 1000000)]
```
- Notación polaca prefija con `&` (implícito entre términos), `|`, `!`.
- Operadores: `=, !=, >, >=, <, <=, in, not in, like, ilike, not like, not ilike,
  =like, =ilike, child_of` (F2.2 sin `child_of`: llega con jerarquías), `is null`
  se expresa como `("campo", "=", None)`.
- Rutas punteadas sobre `Many2one` generan JOINs; el último segmento es el campo.

## Garantías de seguridad (no negociables)

1. **Cero interpolación**: todo valor va como parámetro vinculado de SQLAlchemy Core.
2. **Identificadores validados contra el registry**: un campo o modelo que no existe
   es `DOMAIN_UNKNOWN_FIELD`, nunca texto que llegue al SQL.
3. **Profundidad y tamaño acotados**: máx. 4 saltos por ruta, máx. 200 términos;
   evita explosión de JOINs como vector de DoS.
4. **Todas las tablas se resuelven con el schema del tenant** del `Environment`;
   el compilador jamás recibe un nombre de schema por parámetro.
5. **Record rules siempre aplicadas**: `compile(...)` exige el resultado del PDP.
   Semántica clásica: reglas globales en `AND`, reglas de grupo en `OR`.
6. `active_test`: si el modelo tiene `active` y el contexto no lo desactiva,
   se agrega `active = true` automáticamente.

## API

```python
compiler = DomainCompiler(env)
stmt = compiler.select(model="sale.order", domain=[...], fields=["name"],
                       rules={"global_and": [...], "role_or": [...]},
                       limit=80, order="id desc")
```
Devuelve un `Select` de SQLAlchemy Core listo para ejecutar en la sesión del `Environment`.

## Tests (primero)

Traducción de cada operador; `&`/`|`/`!` y anidamiento; ruta punteada de 1 y 2 saltos;
`in` con lista vacía ⇒ falso constante; `None` ⇒ `IS NULL`; campo inexistente;
modelo inexistente; ruta que atraviesa un campo no relacional; profundidad excedida;
dominio malformado; **inyección** en nombre de campo, en operador y en valor;
límite de términos; record rules global AND + rol OR; `active_test`;
property-based (Hypothesis): todo dominio generado válido compila sin excepción y
nunca produce SQL con literales del valor.
