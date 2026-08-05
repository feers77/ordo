# F3-04 — Lenguaje natural a dominio (diseño)

`POST /meta/v1/translate-query` traduce "las órdenes de agosto sin facturar
del cliente ACME" a un dominio ORDO válido y **lo devuelve sin ejecutar**.
Las decisiones de dependencia y seguridad están en ADR-017.

## Contrato

```
POST /meta/v1/translate-query
{"question": "órdenes confirmadas de agosto", "models": ["sale.order"]}
→ 200 {"model": "sale.order",
       "domain": [["state","=","confirmed"], ["date_order",">=","2026-08-01"]],
       "attempts": 1}
```

`models` es opcional: acota el schema que se le muestra al modelo y reduce
el ruido. Sin él se usan los modelos del registry, que en un tenant grande
es mucho contexto: la recomendación es acotar.

## Piezas

- `QueryModel` (Protocol): `async complete(prompt: str) -> str`.
- `CommandQueryModel`: ejecuta `ORDO_NL_COMMAND` sin shell (`shlex.split`),
  prompt por stdin, respuesta por stdout. Si `ORDO_NL_RESULT_PATH` está
  definido, la salida se lee como JSON y se extrae esa clave (los CLIs
  suelen envolver el texto en un sobre con metadatos); si no, se usa la
  salida cruda. Timeout y tope de bytes configurables.
- `translate_query(env, question, *, models, client)`:
  1. arma el prompt con el schema semántico compacto (`build_schema`) de
     los modelos pedidos — **estructura, nunca filas**;
  2. pide un JSON `{"model": ..., "domain": [...]}`;
  3. extrae el JSON aunque venga con texto alrededor (los modelos a veces
     lo envuelven en ```json);
  4. **valida compilando** el dominio con `DomainCompiler.select(...)`, sin
     ejecutarlo;
  5. si no compila, reintenta **una sola vez** pasándole el error;
  6. devuelve `{model, domain, attempts}`.

## Errores

`NL_UNAVAILABLE` (503, sin comando configurado), `NL_TIMEOUT` (504),
`NL_MODEL_FAILED` (502, el proceso murió o no escribió nada),
`NL_INVALID_RESPONSE` (422, no había JSON en la respuesta),
`NL_INVALID_DOMAIN` (422, el dominio no compila tras el reintento;
el mensaje incluye el error del compilador).

## Qué NO entra

- Ejecutar la consulta traducida: es del agente, con sus permisos.
- Elegir el modelo por pregunta, streaming o herramientas: ADR nuevo.
- Traducir escrituras ("crea una orden…"): solo consultas.
