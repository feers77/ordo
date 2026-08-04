"""Modelos fundacionales: compañía, moneda, contacto y unidad de medida.

De estos cuelga todo lo demás. Cada campo declara qué significa en lenguaje
llano (`agent_hint`) y con qué valores se ve (`examples`), porque un agente
descubre el modelo leyendo eso y no una documentación aparte.
"""

from ordo_core.fields import (
    Boolean,
    Char,
    Date,
    Float,
    Many2one,
    Selection,
    Text,
)
from ordo_core.model import Model


class Currency(Model):
    _name = "res.currency"
    _description = "Moneda"

    name = Char(
        required=True,
        index=True,
        agent_hint="Código ISO 4217 de la moneda",
        examples=["CLP", "USD", "EUR"],
    )
    symbol = Char(
        required=True,
        agent_hint="Símbolo con que se muestra la moneda",
        examples=["$", "US$", "€"],
    )
    decimal_places = Selection(
        [("0", "Sin decimales"), ("2", "Dos decimales"), ("4", "Cuatro decimales")],
        default="2",
        agent_hint=(
            "Cuántos decimales admite la moneda. El peso chileno no usa decimales; "
            "el dólar usa dos. Redondear mal aquí produce descuadres contables"
        ),
        examples=["0", "2"],
    )
    active = Boolean(
        default=True,
        agent_hint="Si está desactivada, no aparece al crear documentos nuevos",
        examples=["true"],
    )


class CurrencyRate(Model):
    _name = "res.currency.rate"
    _description = "Tasa de cambio"

    currency_id = Many2one(
        "res.currency",
        required=True,
        index=True,
        agent_hint="Moneda a la que aplica esta tasa",
        examples=["2"],
    )
    company_id = Many2one(
        "res.company",
        agent_hint="Compañía dueña de la tasa; si está vacío aplica a todas",
        examples=["1"],
    )
    date_from = Date(
        required=True,
        index=True,
        agent_hint=(
            "Fecha desde la que rige la tasa. Para convertir un importe se usa la "
            "tasa vigente a la fecha del documento, no la de hoy"
        ),
        examples=["2026-08-01"],
    )
    rate = Float(
        required=True,
        agent_hint="Unidades de esta moneda por una unidad de la moneda de la compañía",
        examples=["0.00105", "950.5"],
    )


class Company(Model):
    _name = "res.company"
    _description = "Compañía"

    name = Char(
        required=True,
        index=True,
        agent_hint="Razón social de la compañía",
        examples=["ACME SpA"],
    )
    vat = Char(
        agent_hint="Identificador tributario, con el formato del país",
        examples=["76.123.456-7", "ES-B12345678"],
    )
    currency_id = Many2one(
        "res.currency",
        required=True,
        agent_hint=(
            "Moneda en que la compañía lleva su contabilidad. Los importes de otras "
            "monedas se convierten a esta para los reportes"
        ),
        examples=["1"],
    )
    country_code = Char(
        agent_hint="Código ISO 3166-1 alfa-2 del país, que determina la localización fiscal",
        examples=["CL", "ES", "MX"],
    )
    parent_id = Many2one(
        "res.company",
        agent_hint="Compañía matriz, si esta forma parte de un grupo",
        examples=["1"],
    )
    active = Boolean(default=True, agent_hint="Compañía operativa", examples=["true"])


class Partner(Model):
    _name = "res.partner"
    _description = "Contacto"

    name = Char(
        required=True,
        index=True,
        agent_hint="Nombre de la persona o razón social de la empresa",
        examples=["ACME SpA", "María Pérez"],
    )
    is_company = Boolean(
        default=False,
        agent_hint="Verdadero si es una empresa; falso si es una persona natural",
        examples=["true", "false"],
    )
    parent_id = Many2one(
        "res.partner",
        agent_hint="Empresa a la que pertenece este contacto, si es una persona",
        examples=["1"],
    )
    vat = Char(
        index=True,
        agent_hint=(
            "Identificador tributario. Su formato y validación dependen del país; "
            "el pack de localización correspondiente lo verifica"
        ),
        examples=["76.123.456-7"],
    )
    email = Char(agent_hint="Correo electrónico de contacto", examples=["contacto@acme.cl"])
    phone = Char(agent_hint="Teléfono de contacto", examples=["+56 2 2345 6789"])
    street = Char(agent_hint="Calle y número de la dirección", examples=["Av. Apoquindo 1234"])
    city = Char(agent_hint="Ciudad o comuna", examples=["Las Condes"])
    country_code = Char(
        agent_hint="Código ISO 3166-1 alfa-2 del país",
        examples=["CL"],
    )
    customer_rank = Float(
        default=0,
        agent_hint="Mayor que cero si el contacto actúa como cliente",
        examples=["0", "1"],
    )
    supplier_rank = Float(
        default=0,
        agent_hint="Mayor que cero si el contacto actúa como proveedor",
        examples=["0", "1"],
    )
    comment = Text(agent_hint="Notas internas sobre el contacto", examples=["Paga a 30 días"])
    company_id = Many2one(
        "res.company",
        agent_hint="Compañía a la que pertenece el contacto; vacío significa compartido",
        examples=["1"],
    )
    active = Boolean(
        default=True,
        agent_hint="Los contactos desactivados no aparecen en búsquedas por defecto",
        examples=["true"],
    )


class UomCategory(Model):
    _name = "uom.category"
    _description = "Categoría de unidad de medida"

    name = Char(
        required=True,
        agent_hint="Magnitud que agrupa unidades convertibles entre sí",
        examples=["Unidades", "Peso", "Volumen"],
    )


class Uom(Model):
    _name = "uom.uom"
    _description = "Unidad de medida"

    name = Char(
        required=True,
        index=True,
        agent_hint="Nombre de la unidad",
        examples=["Unidad", "Kilogramo", "Litro"],
    )
    category_id = Many2one(
        "uom.category",
        required=True,
        agent_hint=(
            "Categoría de la unidad. Solo se pueden convertir unidades de la misma "
            "categoría: kilos a gramos sí, kilos a litros no"
        ),
        examples=["1"],
    )
    factor = Float(
        default=1.0,
        agent_hint=(
            "Cuántas unidades de esta equivalen a una de la unidad de referencia de "
            "la categoría. Un gramo respecto del kilo es 1000"
        ),
        examples=["1.0", "1000.0"],
    )
    uom_type = Selection(
        [
            ("reference", "Referencia de la categoría"),
            ("bigger", "Mayor que la de referencia"),
            ("smaller", "Menor que la de referencia"),
        ],
        default="reference",
        agent_hint="Relación de tamaño con la unidad de referencia de su categoría",
        examples=["reference", "smaller"],
    )
    active = Boolean(default=True, agent_hint="Unidad disponible", examples=["true"])
