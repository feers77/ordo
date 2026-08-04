# F2-03 — Campos calculados, related y recomputación (diseño)

## Declaración

```python
class SaleOrder(Model):
    _name = "sale.order"

    amount_untaxed = Monetary(compute="_compute_amounts", store=True,
                              agent_hint="Base imponible", examples=["9500.00"])
    amount_total   = Monetary(compute="_compute_amounts", store=True,
                              agent_hint="Total con impuestos", examples=["11305.00"])
    partner_country = Char(related="partner_id.country_id.code", store=False,
                           agent_hint="País del cliente", examples=["CL"])

    @depends("line_ids.price_total")
    def _compute_amounts(self, records: list[Record]) -> None:
        ...
```

- `compute` nombra un método del modelo; el método declara sus dependencias
  con `@depends("campo", "rel.campo", ...)`.
- **Recomputación en lote**: el método recibe la lista completa de registros
  afectados, nunca uno por uno (evita N+1 por diseño).
- `store=True` persiste la columna (indexable, filtrable en dominios);
  `store=False` se calcula al leer.
- `related="a.b.c"` es azúcar sobre compute: el kernel genera el compute y las
  dependencias a partir de la ruta.

## Grafo de dependencias

`DependencyGraph` se construye en el registry:
- nodo = `(modelo, campo)`; arista = "si cambia X, recalcular Y".
- Dependencias con ruta (`line_ids.price_total`) registran el disparador en el
  modelo relacionado más el camino inverso para localizar los registros a recomputar.
- **Ciclos ⇒ `COMPUTE_DEPENDENCY_CYCLE`** al construir el registry (falla el boot,
  no en runtime).
- `graph.affected(model, changed_fields)` → orden topológico de campos a recomputar,
  incluyendo cascadas (A→B→C).

## Reglas

- Un campo calculado sin `compute` declarado ⇒ `COMPUTE_METHOD_MISSING`.
- Un `compute` que apunta a un método inexistente ⇒ `COMPUTE_METHOD_MISSING`.
- `@depends` sobre un campo inexistente ⇒ `COMPUTE_UNKNOWN_DEPENDENCY`.
- Un campo `related` cuya ruta no resuelve ⇒ `COMPUTE_INVALID_RELATED`.
- Los calculados **no almacenados** no son filtrables en dominios:
  `DOMAIN_FIELD_NOT_STORED` (evita SQL imposible en vez de fallar silencioso).

## Caché e invalidación

Caché por transacción en el `Environment` (`env.cache`): clave `(modelo, id, campo)`.
Escribir un campo invalida ese campo y todos sus dependientes según el grafo.
`env.cache.invalidate_all()` al cerrar la transacción.

## Tests (primero)

Grafo: dependencia directa, en cadena, con ruta relacional, ciclo detectado,
orden topológico. Compute: lote (una llamada para N registros), store=True escribe
columna, store=False calcula al leer, related resuelve ruta y falla si no existe.
Caché: hit, invalidación al escribir, invalidación en cascada, aislamiento por transacción.
Dominios: filtrar por calculado no almacenado se rechaza.
