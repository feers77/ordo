"""Servicio de inventario: mover stock es valorizarlo y asentarlo, junto.

`action_validate` hace las tres cosas en la misma operación: marca los
movimientos, escribe las capas de valorización y contabiliza el asiento.
No existe el picking "hecho pero sin valor", que es donde el inventario
físico y el contable empiezan a mentirse.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet
from ordo_core.services.sequences import SequenceService

from modules.account.services import AccountingService
from modules.stock.valuation import ValuationError, money, new_average

ZERO = Decimal("0")

SEQUENCES = {
    "in": ("stock.picking.in", "Recepciones", "IN/"),
    "out": ("stock.picking.out", "Entregas", "OUT/"),
    "internal": ("stock.picking.internal", "Traslados", "INT/"),
}


class StockError(ValuationError):
    """Error de inventario con código estable."""


class StockService:
    def __init__(self, env: Environment) -> None:
        self.env = env
        self.pickings = RecordSet(env, "stock.picking")
        self.moves = RecordSet(env, "stock.move")
        self.layers = RecordSet(env, "stock.valuation.layer")
        self.products = RecordSet(env, "product.product")
        self.locations = RecordSet(env, "stock.location")
        self.accounting = AccountingService(env)

    # ------------------------------------------------------------- creación

    async def create_picking(
        self,
        *,
        picking_type: str,
        date: str,
        company_id: int,
        moves: list[dict[str, Any]],
        partner_id: int | None = None,
        origin: str | None = None,
        note: str | None = None,
    ) -> int:
        if not moves:
            raise StockError("STOCK_PICKING_EMPTY", "Un picking sin movimientos no mueve nada")
        [picking_id] = await self.pickings.create(
            [
                {
                    "picking_type": picking_type,
                    "date": date,
                    "company_id": company_id,
                    "partner_id": partner_id,
                    "origin": origin,
                    "note": note,
                    "state": "draft",
                }
            ]
        )
        for move in moves:
            quantity = Decimal(str(move.get("quantity", "0")))
            if quantity <= ZERO:
                raise StockError(
                    "STOCK_INVALID_QUANTITY", "La cantidad de un movimiento debe ser positiva"
                )
            [product] = await self.products.read(
                [move["product_id"]], fields=["product_type", "tracking", "name"]
            )
            if product["product_type"] != "consu":
                raise StockError(
                    "STOCK_SERVICE_PRODUCT",
                    f"'{product['name']}' es un servicio: no mueve stock",
                )
            if product["tracking"] != "none" and not move.get("lot_id"):
                raise StockError(
                    "STOCK_LOT_REQUIRED",
                    f"'{product['name']}' exige lote o serie en cada movimiento",
                    hint="Crea el stock.lot y pásalo en lot_id.",
                )
        await self.moves.create(
            [
                {
                    "picking_id": picking_id,
                    "product_id": move["product_id"],
                    "quantity": str(move["quantity"]),
                    "location_from_id": move["location_from_id"],
                    "location_to_id": move["location_to_id"],
                    "lot_id": move.get("lot_id"),
                    "price_unit": move.get("price_unit"),
                    "company_id": company_id,
                    "state": "draft",
                }
                for move in moves
            ]
        )
        return picking_id

    # ----------------------------------------------------------- existencias

    async def on_hand(self, product_id: int, location_id: int) -> Decimal:
        """Existencias en una ubicación: entradas menos salidas de moves hechos."""
        incoming = await self._sum_moves(product_id, "location_to_id", location_id)
        outgoing = await self._sum_moves(product_id, "location_from_id", location_id)
        return incoming - outgoing

    async def on_hand_company(self, product_id: int, company_id: int) -> Decimal:
        """Existencias totales en ubicaciones internas de la compañía."""
        result = await self.locations.search(
            [("company_id", "=", company_id), ("location_type", "=", "internal")],
            fields=["id"],
            limit=500,
        )
        total = ZERO
        for row in result["rows"]:
            total += await self.on_hand(product_id, row["id"])
        return total

    # ------------------------------------------------------------ validación

    async def action_validate(self, picking_id: int) -> str:
        """Mueve, valoriza y asienta. Devuelve el número asignado."""
        picking = await self._get(picking_id)
        if picking["state"] != "draft":
            raise StockError(
                "STOCK_INVALID_TRANSITION",
                f"Solo se valida un picking en borrador; este está en {picking['state']}",
            )
        moves = await self._moves_of(picking_id)
        config = await self._config(picking["company_id"])

        journal_lines: list[dict[str, Any]] = []
        for move in moves:
            await self._validate_one(move, picking, config, journal_lines)

        number = await self._next_number(picking["picking_type"])
        account_move_id = None
        if journal_lines:
            self._require_journal(config)
            account_move_id = await self.accounting.create_move(
                journal_id=config["journal_id"],
                move_date=picking["date"],
                currency_id=await self._company_currency(picking["company_id"]),
                company_id=picking["company_id"],
                lines=journal_lines,
                ref=f"Inventario {number}",
                partner_id=picking["partner_id"],
            )
            await self.accounting.action_post(account_move_id)

        await self.moves.write([m["id"] for m in moves], {"state": "done"})
        await self.pickings.write(
            [picking_id], {"state": "done", "name": number, "move_id": account_move_id}
        )
        return number

    async def action_cancel(self, picking_id: int) -> None:
        picking = await self._get(picking_id)
        if picking["state"] == "done":
            raise StockError(
                "STOCK_DONE_IMMUTABLE",
                "Un picking hecho no se cancela: haz el movimiento inverso",
                hint="Crea un picking en sentido contrario para revertir.",
            )
        moves = await self._moves_of(picking_id)
        if moves:
            await self.moves.write([m["id"] for m in moves], {"state": "cancelled"})
        await self.pickings.write([picking_id], {"state": "cancelled"})

    # -------------------------------------------------------------- internos

    async def _validate_one(
        self,
        move: dict[str, Any],
        picking: dict[str, Any],
        config: dict[str, Any],
        journal_lines: list[dict[str, Any]],
    ) -> None:
        quantity = Decimal(move["quantity"])
        [origin] = await self.locations.read(
            [move["location_from_id"]], fields=["location_type", "name"]
        )
        [target] = await self.locations.read(
            [move["location_to_id"]], fields=["location_type", "name"]
        )
        from_type, to_type = origin["location_type"], target["location_type"]
        if from_type != "internal" and to_type != "internal":
            raise StockError(
                "STOCK_INVALID_ROUTE",
                "Un movimiento entre ubicaciones virtuales no significa nada",
            )
        if move["location_from_id"] == move["location_to_id"]:
            raise StockError("STOCK_INVALID_ROUTE", "Origen y destino son la misma ubicación")

        [product] = await self.products.read([move["product_id"]], fields=["name", "cost"])
        avg = product["cost"] or ZERO

        if from_type == "internal":
            available = await self.on_hand(move["product_id"], move["location_from_id"])
            if available < quantity:
                raise StockError(
                    "STOCK_INSUFFICIENT",
                    f"'{product['name']}' en {origin['name']}: hay {available}, "
                    f"se piden {quantity}",
                    hint="Recibe stock o ajusta la cantidad.",
                )

        if from_type == "internal" and to_type == "internal":
            return  # traslado: ni valor ni asiento, solo cambia de lugar

        if to_type == "internal":
            # Entrada: a costo de compra (o promedio vigente si es reposición
            # desde ajuste y no se indicó costo).
            unit_cost = move["price_unit"]
            if unit_cost is None and from_type == "inventory_loss":
                unit_cost = avg
            if unit_cost is None:
                raise StockError(
                    "STOCK_PRICE_REQUIRED",
                    f"La recepción de '{product['name']}' exige price_unit",
                    hint="Indica el costo unitario de la entrada.",
                )
            on_hand = await self.on_hand_company(move["product_id"], picking["company_id"])
            updated_avg = new_average(on_hand, avg, quantity, unit_cost)
            await self.products.write([move["product_id"]], {"cost": updated_avg})
            value = money(quantity * unit_cost)
            await self._layer(move, quantity, unit_cost, value)
            # De dónde viene la mercadería decide contra qué se acredita el
            # inventario. Una devolución de cliente revierte el costo de esa
            # venta; acreditarla contra recepciones por facturar diría que le
            # debemos la mercadería a un proveedor, que es falso.
            counterpart = {
                "inventory_loss": config["loss_account_id"],
                "customer": config["cogs_account_id"],
            }.get(from_type, config["input_account_id"])
            self._require_accounts(config, counterpart)
            journal_lines.append(
                {
                    "account_id": config["valuation_account_id"],
                    "name": f"Entrada {product['name']}",
                    "debit": value,
                }
            )
            journal_lines.append(
                {"account_id": counterpart, "name": f"Entrada {product['name']}", "credit": value}
            )
            await self.moves.write([move["id"]], {"price_unit": unit_cost})
            return

        # Salida (internal → customer / inventory_loss): al costo promedio.
        value = money(quantity * avg)
        await self._layer(move, -quantity, avg, -value)
        expense = (
            config["loss_account_id"] if to_type == "inventory_loss" else config["cogs_account_id"]
        )
        self._require_accounts(config, expense)
        if value > ZERO:
            journal_lines.append(
                {"account_id": expense, "name": f"Salida {product['name']}", "debit": value}
            )
            journal_lines.append(
                {
                    "account_id": config["valuation_account_id"],
                    "name": f"Salida {product['name']}",
                    "credit": value,
                }
            )
        await self.moves.write([move["id"]], {"price_unit": avg})

    async def _layer(
        self, move: dict[str, Any], quantity: Decimal, unit_cost: Decimal, value: Decimal
    ) -> None:
        await self.layers.create(
            [
                {
                    "stock_move_id": move["id"],
                    "product_id": move["product_id"],
                    "quantity": str(quantity),
                    "unit_cost": unit_cost,
                    "value": value,
                    "company_id": move["company_id"],
                }
            ]
        )

    def _require_accounts(self, config: dict[str, Any], counterpart: int | None) -> None:
        if config["valuation_account_id"] is None or counterpart is None:
            raise StockError(
                "STOCK_CONFIG_MISSING",
                "Faltan cuentas de valorización en stock.config",
                hint="Configura valuation/input/cogs/loss y el diario de inventario.",
            )

    def _require_journal(self, config: dict[str, Any]) -> None:
        if config["journal_id"] is None:
            raise StockError(
                "STOCK_CONFIG_MISSING",
                "Falta el diario de inventario en stock.config",
                hint="Configura journal_id para asentar la valorización.",
            )

    async def _config(self, company_id: int) -> dict[str, Any]:
        result = await RecordSet(self.env, "stock.config").search(
            [("company_id", "=", company_id)],
            fields=[
                "id",
                "valuation_account_id",
                "input_account_id",
                "cogs_account_id",
                "loss_account_id",
                "journal_id",
            ],
            limit=1,
        )
        if not result["rows"]:
            raise StockError(
                "STOCK_CONFIG_MISSING",
                "La compañía no tiene configuración de inventario (stock.config)",
                hint="Crea la fila con las cuentas y el diario de valorización.",
            )
        return result["rows"][0]

    async def _next_number(self, picking_type: str) -> str:
        code, name, prefix = SEQUENCES[picking_type]
        sequences = SequenceService(self.env.session)
        await sequences.create(code=code, name=name, prefix=prefix)
        return await sequences.next_by_code(code)

    async def _company_currency(self, company_id: int) -> int:
        [company] = await RecordSet(self.env, "res.company").read(
            [company_id], fields=["currency_id"]
        )
        return int(company["currency_id"])

    async def _sum_moves(self, product_id: int, field: str, location_id: int) -> Decimal:
        result = await self.moves.search(
            [
                ("product_id", "=", product_id),
                (field, "=", location_id),
                ("state", "=", "done"),
            ],
            fields=["quantity"],
            limit=10000,
        )
        return sum((Decimal(row["quantity"]) for row in result["rows"]), ZERO)

    async def _get(self, picking_id: int) -> dict[str, Any]:
        rows = await self.pickings.read(
            [picking_id],
            fields=["id", "state", "picking_type", "date", "company_id", "partner_id"],
        )
        if not rows:
            raise StockError("STOCK_PICKING_NOT_FOUND", f"No existe el picking {picking_id}")
        return rows[0]

    async def _moves_of(self, picking_id: int) -> list[dict[str, Any]]:
        result = await self.moves.search(
            [("picking_id", "=", picking_id)],
            fields=[
                "id",
                "product_id",
                "quantity",
                "location_from_id",
                "location_to_id",
                "lot_id",
                "price_unit",
                "company_id",
            ],
            limit=500,
        )
        return sorted(result["rows"], key=lambda row: row["id"])
