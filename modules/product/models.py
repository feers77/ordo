"""Productos: lo que se vende, se compra y —si es almacenable— se cuenta.

El costo promedio (`cost`) lo mantiene el sistema al validar recepciones;
escribirlo a mano desalinea inventario y contabilidad, por eso el hint lo
dice sin rodeos.

Sobre variantes (ADR-018): `product.product` **es** la variante. Una polera
talla M roja y una XL negra son dos productos distintos, con su propio stock
y su propio costo promedio —que es lo correcto: se compraron en lotes
distintos y no valen lo mismo—. `product.template` es solo el agrupador
comercial del que cuelgan, y `template_id` vacío significa producto sin
variantes, que se vende tal cual.
"""

from ordo_core.fields import Boolean, Char, Integer, Many2one, Monetary, Selection, Text
from ordo_core.model import Model

PRODUCT_TYPES = [
    ("consu", "Almacenable"),
    ("service", "Servicio"),
]

TRACKING = [
    ("none", "Sin seguimiento"),
    ("lot", "Por lote"),
    ("serial", "Por número de serie"),
]

DISPLAY_TYPES = [
    ("select", "Lista"),
    ("size", "Talla"),
    ("color", "Color"),
]


class ProductCategory(Model):
    _name = "product.category"
    _description = "Categoría comercial de producto"

    name = Char(
        required=True,
        index=True,
        agent_hint="Nombre de la familia comercial bajo la que se agrupa y se reporta",
        examples=["Poleras", "Pantalones"],
    )
    parent_id = Many2one(
        "product.category",
        index=True,
        agent_hint="Categoría superior; el árbol define cómo se agregan las ventas por familia",
        examples=["2"],
    )
    active = Boolean(default=True, agent_hint="Categoría en uso", examples=["true"])
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía de la categoría", examples=["1"]
    )


class ProductAttribute(Model):
    _name = "product.attribute"
    _description = "Atributo que distingue variantes"

    name = Char(
        required=True,
        index=True,
        agent_hint="Característica que distingue una variante de otra, como talla o color",
        examples=["Talla", "Color"],
    )
    display_type = Selection(
        DISPLAY_TYPES,
        default="select",
        agent_hint="Pista de presentación para el cliente; no afecta ningún cálculo",
        examples=["size", "color"],
    )
    sequence = Integer(
        default=10,
        agent_hint="Orden en que se muestra el atributo y en que se compone el nombre "
        "de la variante",
        examples=["10"],
    )
    active = Boolean(default=True, agent_hint="Atributo en uso", examples=["true"])
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía del atributo", examples=["1"]
    )


class ProductAttributeValue(Model):
    _name = "product.attribute.value"
    _description = "Valor posible de un atributo"

    attribute_id = Many2one(
        "product.attribute",
        required=True,
        index=True,
        agent_hint="Atributo al que pertenece este valor",
        examples=["1"],
    )
    name = Char(
        required=True,
        index=True,
        agent_hint="Valor concreto tal como se imprime en la etiqueta",
        examples=["M", "Rojo"],
    )
    code = Char(
        agent_hint="Código corto con el que este valor entra en el SKU de la variante",
        examples=["M", "ROJ"],
    )
    sequence = Integer(
        default=10,
        agent_hint="Orden natural del valor: S antes que M, y M antes que L",
        examples=["20"],
    )
    active = Boolean(default=True, agent_hint="Valor en uso", examples=["true"])
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía del valor", examples=["1"]
    )


class ProductTemplate(Model):
    _name = "product.template"
    _description = "Modelo comercial del que cuelgan las variantes"

    name = Char(
        required=True,
        index=True,
        agent_hint=("Nombre del modelo sin talla ni color; la variante añade la combinación"),
        examples=["Polera Oversize"],
    )
    default_code = Char(
        index=True,
        agent_hint="Prefijo de SKU del modelo; las variantes le agregan sus códigos de atributo",
        examples=["POL-OVR"],
    )
    category_id = Many2one(
        "product.category",
        index=True,
        agent_hint="Categoría comercial del modelo, que las variantes heredan al crearse",
        examples=["3"],
    )
    product_type = Selection(
        PRODUCT_TYPES,
        default="consu",
        agent_hint="Almacenable o servicio; toda variante generada lo copia de aquí",
        examples=["consu"],
    )
    uom_id = Many2one(
        "uom.uom",
        agent_hint="Unidad de medida por defecto de las variantes",
        examples=["1"],
    )
    list_price = Monetary(
        agent_hint=(
            "Precio de lista base, sin impuestos. Una variante puede sobrescribirlo: "
            "la talla XXL suele costar más"
        ),
        examples=["19990.00"],
    )
    tracking = Selection(
        TRACKING,
        default="none",
        agent_hint="Seguimiento por lote o serie que heredan las variantes",
        examples=["none"],
    )
    income_account_id = Many2one(
        "account.account",
        agent_hint="Cuenta de ingreso por defecto de las variantes; si falta, la del diario",
        examples=["4"],
    )
    expense_account_id = Many2one(
        "account.account",
        agent_hint="Cuenta de gasto por defecto de las variantes; si falta, la del diario",
        examples=["5"],
    )
    description = Text(
        agent_hint="Descripción larga común a todas las variantes del modelo",
        examples=["Polera de algodón peinado, corte oversize"],
    )
    active = Boolean(default=True, agent_hint="Modelo disponible", examples=["true"])
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía del modelo", examples=["1"]
    )


class ProductTemplateAttributeLine(Model):
    _name = "product.template.attribute.line"
    _description = "Eje de la matriz de variantes de un modelo"

    template_id = Many2one(
        "product.template",
        required=True,
        index=True,
        agent_hint="Modelo cuya matriz de variantes define esta línea",
        examples=["7"],
    )
    attribute_id = Many2one(
        "product.attribute",
        required=True,
        index=True,
        agent_hint="Atributo que participa en la matriz de este modelo",
        examples=["1"],
    )
    value_ids = Char(
        required=True,
        agent_hint=(
            "Ids de los valores participantes separados por coma; son el eje del "
            "producto cartesiano que genera las variantes"
        ),
        examples=["3,4,5"],
    )
    sequence = Integer(
        default=10,
        agent_hint="Orden del eje dentro de la matriz y dentro del nombre de la variante",
        examples=["10"],
    )
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía de la línea", examples=["1"]
    )


class ProductVariantValue(Model):
    _name = "product.variant.value"
    _description = "Valor de atributo que identifica una variante"

    product_id = Many2one(
        "product.product",
        required=True,
        index=True,
        agent_hint="Variante concreta a la que pertenece este valor",
        examples=["42"],
    )
    attribute_id = Many2one(
        "product.attribute",
        required=True,
        index=True,
        agent_hint="Atributo que esta fila fija para la variante",
        examples=["1"],
    )
    value_id = Many2one(
        "product.attribute.value",
        required=True,
        index=True,
        agent_hint=(
            "Valor que la variante toma para ese atributo. Filtra por aquí para "
            "preguntar cosas como qué queda en talla M"
        ),
        examples=["4"],
    )
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía de la variante", examples=["1"]
    )


class Product(Model):
    _name = "product.product"
    _description = "Producto o servicio"

    name = Char(
        required=True,
        index=True,
        agent_hint="Nombre comercial del producto",
        examples=["Notebook 14 pulgadas", "Hora de consultoría"],
    )
    default_code = Char(
        index=True,
        agent_hint="Referencia interna o SKU",
        examples=["NB-14-PRO"],
    )
    product_type = Selection(
        PRODUCT_TYPES,
        default="consu",
        agent_hint=(
            "Almacenable mueve stock y se valoriza; un servicio no toca el inventario nunca"
        ),
        examples=["consu", "service"],
    )
    uom_id = Many2one(
        "uom.uom",
        agent_hint="Unidad de medida en que se cuenta el producto",
        examples=["1"],
    )
    list_price = Monetary(
        agent_hint="Precio de venta de lista, sin impuestos",
        examples=["599990.00"],
    )
    cost = Monetary(
        agent_hint=(
            "Costo promedio vigente. Lo actualiza el sistema al validar "
            "recepciones; no lo escribas a mano o el inventario contable y el "
            "físico dejarán de cuadrar"
        ),
        examples=["425000.00"],
    )
    tracking = Selection(
        TRACKING,
        default="none",
        agent_hint="Si sus movimientos exigen lote o número de serie",
        examples=["none", "lot"],
    )
    income_account_id = Many2one(
        "account.account",
        agent_hint="Cuenta de ingreso propia; si falta se usa la del diario de venta",
        examples=["4"],
    )
    expense_account_id = Many2one(
        "account.account",
        agent_hint="Cuenta de gasto propia para compras; si falta, la del diario",
        examples=["5"],
    )
    barcode = Char(
        index=True,
        agent_hint="Código de barras, si existe",
        examples=["7801234567890"],
    )
    active = Boolean(default=True, agent_hint="Producto disponible", examples=["true"])
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía del producto", examples=["1"]
    )
    description = Text(
        agent_hint="Descripción larga para documentos",
        examples=["Notebook 14'' 16GB RAM"],
    )
    template_id = Many2one(
        "product.template",
        index=True,
        agent_hint=(
            "Modelo del que esta variante es una combinación concreta. Vacío "
            "significa producto sin variantes, que se vende tal cual"
        ),
        examples=["7"],
    )
    variant_label = Char(
        agent_hint="Combinación legible que distingue la variante; la compone el sistema",
        examples=["M / Rojo"],
    )
    category_id = Many2one(
        "product.category",
        index=True,
        agent_hint="Categoría comercial; al generar variantes se copia la del modelo",
        examples=["3"],
    )
