"""Modelos de inventario: almacenes, ubicaciones, movimientos y capas.

La capa de valorización es la verdad contable del inventario: cada entrada
o salida valorizada deja exactamente una, y la suma de capas de un producto
ES su valor. Un picking hecho es historia y no se edita, igual que un
asiento contabilizado.
"""

from ordo_core.fields import (
    Boolean,
    Char,
    Date,
    Many2one,
    Monetary,
    Selection,
    Text,
)
from ordo_core.model import Model

LOCATION_TYPES = [
    ("internal", "Interna"),
    ("supplier", "Proveedor (virtual)"),
    ("customer", "Cliente (virtual)"),
    ("inventory_loss", "Ajuste de inventario (virtual)"),
]

REPLENISH_ROUTES = [
    ("internal", "Traslado desde otra ubicación"),
    ("buy", "Compra al proveedor"),
]

PICKING_TYPES = [
    ("in", "Recepción"),
    ("out", "Entrega"),
    ("internal", "Traslado interno"),
]


class Warehouse(Model):
    _name = "stock.warehouse"
    _description = "Almacén físico"

    name = Char(required=True, agent_hint="Nombre del almacén", examples=["Bodega Central"])
    code = Char(
        required=True,
        index=True,
        agent_hint="Código corto del almacén",
        examples=["BC"],
    )
    company_id = Many2one("res.company", required=True, agent_hint="Compañía dueña", examples=["1"])
    active = Boolean(default=True, agent_hint="Almacén operativo", examples=["true"])


class Location(Model):
    _name = "stock.location"
    _description = "Ubicación de inventario"

    name = Char(
        required=True,
        index=True,
        agent_hint="Nombre de la ubicación",
        examples=["BC/Existencias", "Proveedores"],
    )
    location_type = Selection(
        LOCATION_TYPES,
        default="internal",
        agent_hint=(
            "Interna cuenta stock propio; proveedor, cliente y ajuste son "
            "virtuales: el afuera contra el que se mueve todo"
        ),
        examples=["internal", "supplier"],
    )
    warehouse_id = Many2one(
        "stock.warehouse",
        agent_hint="Almacén al que pertenece, si es interna",
        examples=["1"],
    )
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía de la ubicación", examples=["1"]
    )
    active = Boolean(default=True, agent_hint="Ubicación disponible", examples=["true"])


class Lot(Model):
    _name = "stock.lot"
    _description = "Lote o número de serie"

    name = Char(
        required=True,
        index=True,
        agent_hint="Código del lote o serie",
        examples=["L-2026-081", "SN-000412"],
    )
    product_id = Many2one(
        "product.product",
        required=True,
        index=True,
        agent_hint="Producto al que pertenece el lote",
        examples=["3"],
    )
    expiration_date = Date(
        agent_hint="Vencimiento del lote, si aplica",
        examples=["2027-03-01"],
    )
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía del lote", examples=["1"]
    )


class Picking(Model):
    _name = "stock.picking"
    _description = "Orden de movimiento de stock"

    name = Char(
        index=True,
        agent_hint="Número del picking; se asigna al validar, no al crear",
        examples=["IN/00001", "OUT/00003"],
    )
    picking_type = Selection(
        PICKING_TYPES,
        required=True,
        agent_hint="Recepción (entra), entrega (sale) o traslado interno",
        examples=["in", "out"],
    )
    state = Selection(
        [("draft", "Borrador"), ("done", "Hecho"), ("cancelled", "Cancelado")],
        default="draft",
        agent_hint=(
            "Un picking hecho movió stock y asentó su valor: no se edita ni se "
            "borra. Usa action_validate y action_cancel, nunca escribas esto"
        ),
        examples=["draft", "done"],
    )
    partner_id = Many2one(
        "res.partner",
        agent_hint="Tercero del movimiento (proveedor que entrega, cliente que recibe)",
        examples=["7"],
    )
    date = Date(
        required=True,
        index=True,
        agent_hint="Fecha del movimiento; también fecha contable de su asiento",
        examples=["2026-08-05"],
    )
    move_id = Many2one(
        "account.move",
        agent_hint="Asiento de valorización generado al validar, si hubo valor",
        examples=["40"],
    )
    origin = Char(
        agent_hint="Documento de origen (orden de compra o venta)",
        examples=["PO/00002"],
    )
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía del picking", examples=["1"]
    )
    note = Text(agent_hint="Observaciones", examples=["Entrega parcial"])


class StockMove(Model):
    _name = "stock.move"
    _description = "Movimiento de stock (línea de picking)"

    picking_id = Many2one(
        "stock.picking",
        required=True,
        index=True,
        agent_hint="Picking al que pertenece el movimiento",
        examples=["1"],
    )
    product_id = Many2one(
        "product.product",
        required=True,
        index=True,
        agent_hint="Producto que se mueve; solo almacenables",
        examples=["3"],
    )
    quantity = Char(
        required=True,
        agent_hint="Cantidad como string decimal, nunca float",
        examples=["10", "2.5"],
    )
    location_from_id = Many2one(
        "stock.location",
        required=True,
        index=True,
        agent_hint="Ubicación de origen",
        examples=["2"],
    )
    location_to_id = Many2one(
        "stock.location",
        required=True,
        index=True,
        agent_hint="Ubicación de destino",
        examples=["3"],
    )
    lot_id = Many2one(
        "stock.lot",
        agent_hint="Lote o serie, obligatorio si el producto lo exige",
        examples=["5"],
    )
    price_unit = Monetary(
        agent_hint=(
            "Costo unitario de la entrada (precio de compra). En salidas lo fija "
            "el sistema al costo promedio vigente al validar"
        ),
        examples=["425000.00"],
    )
    state = Selection(
        [("draft", "Borrador"), ("done", "Hecho"), ("cancelled", "Cancelado")],
        default="draft",
        agent_hint="Sigue el estado de su picking; nunca se escribe directo",
        examples=["draft", "done"],
    )
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía del movimiento", examples=["1"]
    )


class ValuationLayer(Model):
    _name = "stock.valuation.layer"
    _description = "Capa de valorización de inventario"

    stock_move_id = Many2one(
        "stock.move",
        required=True,
        index=True,
        agent_hint="Movimiento que generó la capa",
        examples=["12"],
    )
    product_id = Many2one(
        "product.product",
        required=True,
        index=True,
        agent_hint="Producto valorizado",
        examples=["3"],
    )
    quantity = Char(
        required=True,
        agent_hint="Cantidad con signo: positiva entra, negativa sale (string decimal)",
        examples=["10", "-4"],
    )
    unit_cost = Monetary(
        agent_hint="Costo unitario aplicado a la capa",
        examples=["425000.00"],
    )
    value = Monetary(
        agent_hint=(
            "Valor firmado de la capa. La suma de capas de un producto ES su "
            "valor contable de inventario"
        ),
        examples=["4250000.00", "-1700000.00"],
    )
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía de la capa", examples=["1"]
    )


class StockConfig(Model):
    _name = "stock.config"
    _description = "Cuentas de valorización por compañía"

    company_id = Many2one(
        "res.company",
        required=True,
        index=True,
        agent_hint="Compañía configurada; una fila por compañía",
        examples=["1"],
    )
    valuation_account_id = Many2one(
        "account.account",
        agent_hint="Cuenta de inventario (activo) donde vive el valor del stock",
        examples=["8"],
    )
    input_account_id = Many2one(
        "account.account",
        agent_hint="Contrapartida de recepciones pendientes de factura (pasivo)",
        examples=["9"],
    )
    cogs_account_id = Many2one(
        "account.account",
        agent_hint="Costo de venta: se carga al entregar",
        examples=["10"],
    )
    loss_account_id = Many2one(
        "account.account",
        agent_hint="Ajustes y mermas de inventario",
        examples=["11"],
    )
    journal_id = Many2one(
        "account.journal",
        agent_hint="Diario donde se asientan los movimientos de inventario",
        examples=["4"],
    )


class ReorderRule(Model):
    _name = "stock.reorder.rule"
    _description = "Regla de reabastecimiento mínimo/máximo"

    product_id = Many2one(
        "product.product",
        required=True,
        index=True,
        agent_hint="Producto vigilado",
        examples=["3"],
    )
    location_id = Many2one(
        "stock.location",
        required=True,
        agent_hint="Ubicación interna donde se vigila el stock",
        examples=["2"],
    )
    min_quantity = Char(
        required=True,
        agent_hint="Bajo este nivel hay que reponer (string decimal)",
        examples=["5"],
    )
    max_quantity = Char(
        required=True,
        agent_hint="Nivel objetivo al reponer (string decimal)",
        examples=["50"],
    )
    route = Selection(
        REPLENISH_ROUTES,
        default="buy",
        agent_hint=(
            "De dónde sale la reposición. En una tienda casi siempre es un "
            "traslado desde la bodega, no una compra al proveedor"
        ),
        examples=["internal", "buy"],
    )
    source_location_id = Many2one(
        "stock.location",
        index=True,
        agent_hint=(
            "Ubicación interna desde la que se repone cuando la ruta es un "
            "traslado; vacía solo tiene sentido si se compra"
        ),
        examples=["2"],
    )
    supplier_id = Many2one(
        "res.partner",
        index=True,
        agent_hint="Proveedor al que se compra cuando la ruta es comprar",
        examples=["3"],
    )
    multiple_quantity = Char(
        agent_hint=(
            "Redondea la reposición hacia arriba a múltiplos de esta cantidad, "
            "como una caja de 12; vacío repone la cantidad exacta"
        ),
        examples=["12"],
    )
    active = Boolean(default=True, agent_hint="Regla vigente", examples=["true"])
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía de la regla", examples=["1"]
    )
