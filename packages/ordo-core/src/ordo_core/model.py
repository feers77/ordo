"""Model declaration base class (design F2-01)."""

from __future__ import annotations

from typing import Any, ClassVar

from ordo_core.fields import Field


class Model:
    """Declarative model. Subclasses declare fields as class attributes.

    `_name` defines a new model; `_inherit` extends an existing one;
    `_inherits` delegates to a parent through a link field.
    """

    _name: ClassVar[str] = ""
    _inherit: ClassVar[str] = ""
    _inherits: ClassVar[dict[str, str]] = {}
    _description: ClassVar[str] = ""
    _table: ClassVar[str] = ""
    _order: ClassVar[str] = "id"

    @classmethod
    def declared_fields(cls) -> dict[str, Field]:
        fields: dict[str, Any] = {}
        for klass in reversed(cls.__mro__):
            for name, value in vars(klass).items():
                if isinstance(value, Field):
                    fields[name] = value
        return fields

    @classmethod
    def target_model_name(cls) -> str:
        return cls._name or cls._inherit
