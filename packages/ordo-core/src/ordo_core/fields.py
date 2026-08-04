"""Field system (design F2-01).

Business fields must carry `agent_hint` and `examples`: they feed the
semantic schema agents consume (CLAUDE.md §4). Money is Decimal only.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ordo_core.errors import KernelError

TECHNICAL_FIELDS = frozenset(
    {
        "id",
        "create_uid",
        "create_date",
        "write_uid",
        "write_date",
        "version",
        "company_id",
        "x_custom",
    }
)


class Field:
    """Base descriptor for every model field."""

    field_type: str = "unknown"
    column_type: str = "text"

    def __init__(
        self,
        *,
        required: bool = False,
        readonly: bool = False,
        index: bool = False,
        default: Any = None,
        store: bool = True,
        related: str | None = None,
        groups: list[str] | None = None,
        company_dependent: bool = False,
        translate: bool = False,
        tracking: bool = False,
        agent_hint: str | None = None,
        examples: list[str] | None = None,
        string: str | None = None,
    ) -> None:
        self.required = required
        self.readonly = readonly
        self.index = index
        self.default = default
        self.store = store
        self.related = related
        self.groups = groups or []
        self.company_dependent = company_dependent
        self.translate = translate
        self.tracking = tracking
        self.agent_hint = agent_hint
        self.examples = examples
        self.string = string
        # asignados por el registry
        self.name: str = ""
        self.model_name: str = ""
        self.delegated_from: str | None = None

    def _invalid(self, message: str) -> KernelError:
        return KernelError("FIELD_INVALID_DEFINITION", message)

    def bind(self, model_name: str, name: str) -> None:
        self.name = name
        self.model_name = model_name

    def clone(self) -> Field:
        import copy

        return copy.copy(self)

    def describe(self) -> dict[str, Any]:
        """Metadata for ir_model_field and the semantic schema."""
        return {
            "name": self.name,
            "type": self.field_type,
            "required": self.required,
            "readonly": self.readonly,
            "index": self.index,
            "store": self.store,
            "translate": self.translate,
            "tracking": self.tracking,
            "company_dependent": self.company_dependent,
            "agent_hint": self.agent_hint,
            "examples": self.examples,
            "delegated_from": self.delegated_from,
        }


class Char(Field):
    field_type = "char"
    column_type = "varchar"

    def __init__(self, size: int | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.size = size


class Text(Field):
    field_type = "text"
    column_type = "text"


class Html(Text):
    field_type = "html"


class Integer(Field):
    field_type = "integer"
    column_type = "integer"


class Float(Field):
    field_type = "float"
    column_type = "double precision"


class Monetary(Field):
    """Money. Always Decimal in Python and NUMERIC in Postgres."""

    field_type = "monetary"
    column_type = "numeric(18,2)"

    def __init__(self, currency_field: str = "currency_id", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if self.default is not None and not isinstance(self.default, Decimal):
            raise self._invalid("Monetary.default debe ser Decimal (nunca float): CLAUDE.md §2.3")
        self.currency_field = currency_field


class Boolean(Field):
    field_type = "boolean"
    column_type = "boolean"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("default", False)
        super().__init__(**kwargs)


class Date(Field):
    field_type = "date"
    column_type = "date"


class Datetime(Field):
    field_type = "datetime"
    column_type = "timestamptz"


class Binary(Field):
    field_type = "binary"
    column_type = "bytea"


class Json(Field):
    field_type = "json"
    column_type = "jsonb"


class Selection(Field):
    field_type = "selection"
    column_type = "varchar"

    def __init__(self, selection: list[tuple[str, str]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if not selection:
            raise self._invalid("Selection requiere al menos una opción")
        self.selection = selection
        self.allowed_values = {value for value, _ in selection}

    def describe(self) -> dict[str, Any]:
        data = super().describe()
        data["selection"] = [{"value": v, "label": label} for v, label in self.selection]
        return data


class Many2one(Field):
    field_type = "many2one"
    column_type = "integer"

    def __init__(self, comodel: str, ondelete: str = "restrict", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if not comodel:
            raise self._invalid("Many2one requiere el modelo destino")
        self.comodel = comodel
        self.ondelete = ondelete

    def describe(self) -> dict[str, Any]:
        data = super().describe()
        data["comodel"] = self.comodel
        return data


class One2many(Field):
    field_type = "one2many"

    def __init__(self, comodel: str, inverse_name: str, **kwargs: Any) -> None:
        kwargs.setdefault("store", False)
        super().__init__(**kwargs)
        if not comodel or not inverse_name:
            raise self._invalid("One2many requiere comodel e inverse_name")
        self.comodel = comodel
        self.inverse_name = inverse_name

    def describe(self) -> dict[str, Any]:
        data = super().describe()
        data["comodel"] = self.comodel
        data["inverse_name"] = self.inverse_name
        return data


class Many2many(Field):
    field_type = "many2many"

    def __init__(
        self,
        comodel: str,
        relation: str | None = None,
        column1: str | None = None,
        column2: str | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("store", False)
        super().__init__(**kwargs)
        if not comodel:
            raise self._invalid("Many2many requiere el modelo destino")
        self.comodel = comodel
        self.relation = relation
        self.column1 = column1
        self.column2 = column2

    def describe(self) -> dict[str, Any]:
        data = super().describe()
        data["comodel"] = self.comodel
        return data


RELATIONAL_TYPES = frozenset({"many2one", "one2many", "many2many"})
