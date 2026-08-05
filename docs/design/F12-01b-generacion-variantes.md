# F12-01b — Generación de la matriz de variantes (diseño)

Continúa [F12-01](F12-01-variantes.md), que define el catálogo. Aquí se genera.

## La operación normal es regenerar

Una tienda declara talla S/M/L y color rojo/negro en marzo, y en octubre agrega
la XL. Vuelve a pedir la matriz. Por eso `action_generate_variants` **crea las
que faltan y no toca las que existen**, en vez de fallar con "ya generado" o
duplicar el catálogo. Devuelve `{created, existing, product_ids}` para que quien
la llamó sepa qué pasó sin volver a leer.

Las variantes archivadas cuentan como existentes. Si no, regenerar resucitaría
como nueva una que alguien archivó a propósito — y eso es precisamente deshacer
una decisión en silencio.

## Qué hereda la variante y qué no

Al nacer copia `product_type`, `uom_id`, `tracking`, las dos cuentas y
`category_id`, más `name`, `description` y `list_price` del modelo. Es una
copia, no un `related`: el kernel resuelve `related` como compute no almacenado
y un catálogo cuyo nombre no vive en una columna no se puede filtrar ni ordenar
en SQL (ADR-018). Con 600 SKUs esa es la diferencia entre buscar en la caja y
hacer un scan.

`cost` **no** se hereda: nace en cero y lo escribe el sistema al validar la
primera recepción. Escribirlo a mano desalinea inventario físico y contable.

## Sobreprecio por valor, en decimal

`price_by_value` es un mapa de id de valor a **string decimal**: la XXL que
cuesta $1.500 más no puede convertirse en $1.499,9999 por pasar por un binario.
El precio final de la variante es el del modelo más los sobreprecios de todos
sus valores.

## El tope se comprueba antes de materializar

Seis ejes de diez valores son un millón de productos. `combination_count`
multiplica los tamaños y compara con `MAX_VARIANTS` (500) **antes** de construir
una sola tupla; comprobarlo después es comprobarlo tarde. El tope no es una
limitación del modelo —una cadena puede tener más variantes— sino un cortafuegos:
casi siempre significa que sobra un atributo en la declaración.

## Lo que la función pura decide y lo que no

`modules/product/variants.py` no toca la base y no decide políticas. Un eje sin
valores devuelve una matriz vacía **sin lanzar**; es el servicio quien distingue
"modelo sin atributos" de "eje declarado a medias" y lo explica en el `hint`. Así
la combinatoria se prueba entera con property-based testing: tamaño igual al
producto de los ejes, sin combinaciones repetidas, ningún valor perdido, orden
de ejes estable.

Ese testing ya encontró un defecto real durante la implementación: un prefijo
tecleado como `"POL-"` producía el SKU `"POL--M-ROJ"`, justo la ambigüedad que
la función dice evitar. Se corrigió limpiando los guiones de los extremos de
cada pieza, y **no** los interiores: `"POL-OVR"` es un prefijo legítimo y
reescribir lo que alguien tecleó sería corregirle el dato a sus espaldas.

## Archivar vive en `stock`, no en `product`

`product.product.action_archive` se registra desde `modules/stock/actions.py`.
Dos razones, y las dos apuntan al mismo lado: la restricción —no archivar algo
que todavía está en la bodega, porque el inventario contable seguiría contándolo
y el físico no lo encontraría— es una regla de inventario; y la flecha de
dependencia va de `stock` hacia `product`, nunca al revés.

Por lo mismo el reporte de la matriz con existencias es `stock.variant_matrix` y
no `product.variant_matrix`: necesita stock, y `product` no puede depender de él.

## `stock.variant_matrix`

Params: `template_id`, `company_id`. Devuelve los ejes (para que un cliente
pueda pivotear talla x color) y una fila por variante con cantidad, costo
promedio y valor. **Incluye las variantes en cero**: en moda, la talla agotada
es justo la fila que hay que ver.

## Permisos

No hay roles nuevos. El PDP resuelve los métodos de negocio como `write` sobre
el modelo, así que `action_generate_variants` lo puede ejecutar quien ya podía
escribir `product.template` —`inventario`— y `action_archive` quien podía
escribir `product.product`.
