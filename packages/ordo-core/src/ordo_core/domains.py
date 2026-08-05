"""Domain language → SQL compiler (design F2-02, ADR-006).

Highest-risk component of the kernel: a defect here leaks data across
tenants or allows injection. Every change needs human review (AGENTS.md §7).

Invariants:
- values only ever travel as bound parameters; nothing is interpolated;
- field and model identifiers are validated against the registry before use;
- path depth and term count are bounded;
- record rules are applied with classic semantics (global AND, role OR).
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    Select,
    String,
    Table,
    and_,
    false,
    func,
    not_,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import ColumnElement, FromClause

from ordo_core.coercion import coerce_query_value
from ordo_core.errors import KernelError
from ordo_core.fields import RELATIONAL_TYPES, Field
from ordo_core.registry import ModelDefinition, Registry

MAX_DEPTH = 4
MAX_TERMS = 200

LOGIC_OPERATORS = frozenset({"&", "|", "!"})
COMPARISON_OPERATORS = frozenset(
    {
        "=",
        "!=",
        "<>",
        ">",
        ">=",
        "<",
        "<=",
        "in",
        "not in",
        "like",
        "not like",
        "ilike",
        "not ilike",
        "=like",
        "=ilike",
    }
)

AGGREGATE_SPECS = ("count", "sum:<campo>", "avg:<campo>", "min:<campo>", "max:<campo>")
NUMERIC_FIELD_TYPES = frozenset({"integer", "float", "monetary"})
TEMPORAL_FIELD_TYPES = frozenset({"date", "datetime"})

_SQL_TYPES: dict[str, Any] = {
    "integer": Integer,
    "many2one": Integer,
    "boolean": Boolean,
    "monetary": Numeric,
    "float": Float,
    "date": Date,
    "datetime": DateTime(timezone=True),
    "json": JSONB,
    "binary": LargeBinary,
}


class DomainCompiler:
    def __init__(self, registry: Registry, schema: str) -> None:
        self.registry = registry
        self.schema = schema
        self._metadata = MetaData(schema=schema)
        self._tables: dict[str, Table] = {}

    # -- API pública ------------------------------------------------------

    def select(
        self,
        *,
        model: str,
        domain: list[Any],
        fields: list[str] | None = None,
        rules: dict[str, list[Any]] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        order: str | None = None,
        active_test: bool = True,
    ) -> Select[Any]:
        definition = self.registry[model]
        table = self._table_for(definition)
        joins = _JoinPlan(self, table)
        conditions = self._conditions(definition, domain, rules, joins, active_test)

        columns = self._select_columns(definition, table, fields)
        stmt = select(*columns).select_from(joins.build_from())
        stmt = stmt.where(and_(*conditions))
        if order is not None:
            stmt = stmt.order_by(*self._order_by(definition, table, order))
        if limit is not None:
            stmt = stmt.limit(limit)
        if offset is not None:
            stmt = stmt.offset(offset)
        return stmt

    def aggregate(
        self,
        *,
        model: str,
        domain: list[Any],
        group_by: list[str] | None = None,
        aggregates: list[str] | None = None,
        rules: dict[str, list[Any]] | None = None,
        order: str | None = None,
        limit: int | None = None,
        active_test: bool = True,
    ) -> Select[Any]:
        """Grouped SELECT built on the same conditions as `select`.

        Aggregating must never become a way around tenant scoping or record
        rules, so the WHERE clause comes from the very same code path. Only
        stored fields of the model itself can be grouped: an implicit join
        would silently change every total (design F2-08).

        `limit` is applied as given; the public ceiling belongs to the API
        and RecordSet layers, not to the compiler.
        """
        definition = self.registry[model]
        table = self._table_for(definition)
        joins = _JoinPlan(self, table)
        conditions = self._conditions(definition, domain, rules, joins, active_test)

        keys = list(group_by or [])
        key_columns = [self._group_column(definition, table, name) for name in keys]
        labels = {
            spec: self._aggregate_column(definition, table, spec)
            for spec in (aggregates or ["count"])
        }
        stmt = select(*key_columns, *labels.values()).select_from(joins.build_from())
        stmt = stmt.where(and_(*conditions))
        if key_columns:
            stmt = stmt.group_by(*key_columns)
        if order is not None:
            stmt = stmt.order_by(self._aggregate_order(table, order, labels, keys))
        if limit is not None:
            stmt = stmt.limit(limit)
        return stmt

    # -- condiciones compartidas -------------------------------------------

    def _conditions(
        self,
        definition: ModelDefinition,
        domain: list[Any],
        rules: dict[str, list[Any]] | None,
        joins: _JoinPlan,
        active_test: bool,
    ) -> list[ColumnElement[bool]]:
        """Domain plus record rules (global AND, role OR) plus active_test."""
        conditions: list[ColumnElement[bool]] = [self._compile(definition, domain, joins)]
        for rule_domain in (rules or {}).get("global_and", []):
            conditions.append(self._compile(definition, rule_domain, joins))
        role_rules = [
            self._compile(definition, rule_domain, joins)
            for rule_domain in (rules or {}).get("role_or", [])
        ]
        if role_rules:
            conditions.append(or_(*role_rules))
        if active_test and "active" in definition.fields:
            conditions.append(joins.column(definition, "active").is_(True))
        return conditions

    # -- agregación ---------------------------------------------------------

    def _group_column(self, definition: ModelDefinition, table: Table, name: str) -> Any:
        field = definition.fields.get(name) if "." not in name else None
        if field is None:
            raise KernelError(
                "FIELD_UNKNOWN",
                f"El campo '{name}' no existe en {definition.name} o no se puede agrupar",
                hint="Agrupa por campos del propio modelo; las rutas con punto no se admiten.",
            )
        if not field.store or field.field_type in {"one2many", "many2many"}:
            raise KernelError(
                "FIELD_NOT_STORED",
                f"'{definition.name}.{name}' no se almacena; no se puede agrupar por él",
                hint="Agrupa por un campo almacenado o por sus dependencias.",
            )
        return table.c[name]

    def _aggregate_column(self, definition: ModelDefinition, table: Table, spec: str) -> Any:
        """One aggregate expression labelled with its own spec string."""
        if spec == "count":
            return func.count().label(spec)
        name, _, field_name = str(spec).partition(":")
        builders: dict[str, Any] = {
            "sum": func.sum,
            "avg": func.avg,
            "min": func.min,
            "max": func.max,
        }
        if name not in builders or not field_name:
            raise KernelError(
                "AGGREGATE_UNKNOWN",
                f"Agregado no soportado: {spec!r}",
                hint=f"Agregados válidos: {', '.join(AGGREGATE_SPECS)}.",
            )
        # min/max también tienen sentido sobre fechas; sum/avg no.
        allowed = NUMERIC_FIELD_TYPES
        if name in {"min", "max"}:
            allowed = allowed | TEMPORAL_FIELD_TYPES
        field = definition.fields.get(field_name)
        if field is None or not field.store or field.field_type not in allowed:
            raise KernelError(
                "AGGREGATE_INVALID_FIELD",
                f"No se puede calcular '{name}' sobre '{definition.name}.{field_name}'",
                hint="Agrega solo campos numéricos, monetarios o de fecha.",
            )
        return builders[name](table.c[field_name]).label(spec)

    def _aggregate_order(
        self, table: Table, order: str, labels: dict[str, Any], keys: list[str]
    ) -> Any:
        """Order by one requested aggregate or by one grouped field."""
        tokens = order.strip().split()
        name = tokens[0] if tokens else ""
        direction = tokens[1].lower() if len(tokens) == 2 else "asc"
        if not tokens or len(tokens) > 2 or direction not in {"asc", "desc"}:
            raise KernelError(
                "AGGREGATE_INVALID_ORDER",
                f"Cláusula de orden inválida: {order!r}",
                hint="Usa '<agregado> desc' o '<campo agrupado> asc'.",
            )
        if name in labels:
            column = labels[name]
        elif name in keys:
            column = table.c[name]
        else:
            raise KernelError(
                "AGGREGATE_INVALID_ORDER",
                f"No se puede ordenar por {name!r}",
                hint="Ordena por un agregado pedido o por un campo del group_by.",
            )
        return column.desc() if direction == "desc" else column.asc()

    # -- tablas y columnas -------------------------------------------------

    def _table_for(self, definition: ModelDefinition, alias_suffix: str = "") -> Table:
        key = f"{definition.table}{alias_suffix}"
        if key not in self._tables:
            columns: list[Column[Any]] = [
                Column(name, _SQL_TYPES.get(field.field_type, String))
                for name, field in definition.fields.items()
                if field.field_type not in {"one2many", "many2many"}
            ]
            self._tables[key] = Table(
                definition.table, self._metadata, *columns, extend_existing=True
            )
        return self._tables[key]

    def _select_columns(
        self, definition: ModelDefinition, table: Table, fields: list[str] | None
    ) -> list[Any]:
        if not fields:
            return [table.c.id]
        columns = []
        for name in fields:
            field = definition.fields.get(name)
            if field is None or field.field_type in {"one2many", "many2many"}:
                raise KernelError(
                    "DOMAIN_UNKNOWN_FIELD",
                    f"El campo '{name}' no existe en {definition.name} o no es seleccionable",
                )
            columns.append(table.c[name])
        return columns

    def _order_by(self, definition: ModelDefinition, table: Table, order: str) -> list[Any]:
        clauses = []
        for part in order.split(","):
            tokens = part.strip().split()
            if not tokens or len(tokens) > 2:
                raise KernelError("DOMAIN_INVALID_ORDER", f"Cláusula de orden inválida: {part!r}")
            name = tokens[0]
            direction = tokens[1].lower() if len(tokens) == 2 else "asc"
            if name not in definition.fields or direction not in {"asc", "desc"}:
                raise KernelError("DOMAIN_INVALID_ORDER", f"Cláusula de orden inválida: {part!r}")
            column = table.c[name]
            clauses.append(column.desc() if direction == "desc" else column.asc())
        return clauses

    # -- compilación del dominio -------------------------------------------

    def _compile(
        self, definition: ModelDefinition, domain: list[Any], joins: _JoinPlan
    ) -> ColumnElement[bool]:
        if not isinstance(domain, list | tuple):
            raise KernelError("DOMAIN_MALFORMED", "El dominio debe ser una lista")
        if len(domain) > MAX_TERMS:
            raise KernelError(
                "DOMAIN_TOO_LARGE",
                f"El dominio excede {MAX_TERMS} elementos",
                hint="Divide la consulta o usa filtros más específicos.",
            )
        if not domain:
            return _true()

        normalized = _to_prefix(list(domain))
        condition, rest = self._parse(definition, normalized, joins)
        if rest:
            raise KernelError("DOMAIN_MALFORMED", "Sobran elementos en el dominio")
        return condition

    def _parse(
        self, definition: ModelDefinition, tokens: list[Any], joins: _JoinPlan
    ) -> tuple[ColumnElement[bool], list[Any]]:
        if not tokens:
            raise KernelError("DOMAIN_MALFORMED", "Faltan operandos en el dominio")
        head, rest = tokens[0], tokens[1:]
        if isinstance(head, str) and head in LOGIC_OPERATORS:
            if head == "!":
                operand, rest = self._parse(definition, rest, joins)
                return not_(operand), rest
            left, rest = self._parse(definition, rest, joins)
            right, rest = self._parse(definition, rest, joins)
            return (and_(left, right) if head == "&" else or_(left, right)), rest
        return self._term(definition, head, joins), rest

    def _term(
        self, definition: ModelDefinition, term: Any, joins: _JoinPlan
    ) -> ColumnElement[bool]:
        if not isinstance(term, list | tuple) or len(term) != 3:
            raise KernelError(
                "DOMAIN_MALFORMED",
                f"Término inválido: {term!r}",
                hint="Cada término es (campo, operador, valor).",
            )
        path, operator, value = term
        if not isinstance(path, str) or not isinstance(operator, str):
            raise KernelError("DOMAIN_MALFORMED", f"Término inválido: {term!r}")
        if operator not in COMPARISON_OPERATORS:
            raise KernelError(
                "DOMAIN_UNKNOWN_OPERATOR",
                f"Operador no soportado: {operator!r}",
                hint=f"Operadores válidos: {sorted(COMPARISON_OPERATORS)}",
            )
        column, field = joins.resolve(definition, path)
        coerced = coerce_query_value(field, value, f"{definition.name}.{path}")
        return _apply_operator(column, operator, coerced)


class _JoinPlan:
    """Resolves dotted paths into joins, reusing aliases per path prefix."""

    def __init__(self, compiler: DomainCompiler, root: Table) -> None:
        self.compiler = compiler
        self.root = root
        self._joins: list[tuple[FromClause, ColumnElement[bool]]] = []
        self._aliases: dict[str, Any] = {"": root}

    def column(self, definition: ModelDefinition, name: str) -> Any:
        return self.resolve(definition, name)[0]

    def resolve(self, definition: ModelDefinition, path: str) -> tuple[Any, Field]:
        parts = path.split(".")
        if len(parts) - 1 > MAX_DEPTH:
            raise KernelError(
                "DOMAIN_PATH_TOO_DEEP",
                f"La ruta '{path}' supera {MAX_DEPTH} saltos",
                hint="Consulta el modelo relacionado directamente.",
            )
        current_def = definition
        table = self.root
        prefix = ""
        for index, part in enumerate(parts):
            field = current_def.fields.get(part)
            if field is None:
                raise KernelError(
                    "DOMAIN_UNKNOWN_FIELD",
                    f"El campo '{part}' no existe en {current_def.name}",
                    hint="Consulta /meta/v1/schema para ver los campos disponibles.",
                )
            is_last = index == len(parts) - 1
            if is_last:
                if field.field_type in {"one2many", "many2many"}:
                    raise KernelError(
                        "DOMAIN_INVALID_PATH",
                        f"'{part}' es {field.field_type}; no se puede comparar directamente",
                    )
                if not field.store:
                    raise KernelError(
                        "DOMAIN_FIELD_NOT_STORED",
                        f"'{current_def.name}.{part}' es calculado sin almacenar; "
                        "no se puede filtrar por él",
                        hint="Marca el campo con store=True o filtra por sus dependencias.",
                    )
                return table.c[part], field
            if field.field_type not in RELATIONAL_TYPES or field.field_type != "many2one":
                raise KernelError(
                    "DOMAIN_INVALID_PATH",
                    f"'{part}' no es un campo relacional; la ruta '{path}' es inválida",
                )
            prefix = f"{prefix}.{part}" if prefix else part
            comodel_def = self.compiler.registry[field.comodel]  # type: ignore[attr-defined]
            if prefix not in self._aliases:
                target = self.compiler._table_for(comodel_def).alias(
                    f"j{len(self._aliases)}_{comodel_def.table}"
                )
                self._joins.append((target, table.c[part] == target.c.id))
                self._aliases[prefix] = target
            table = self._aliases[prefix]
            current_def = comodel_def
        raise KernelError("DOMAIN_INVALID_PATH", f"Ruta inválida: {path!r}")

    def build_from(self) -> Any:
        source: Any = self.root
        for target, condition in self._joins:
            source = source.outerjoin(target, condition)
        return source


def _true() -> ColumnElement[bool]:
    from sqlalchemy import true

    return cast("ColumnElement[bool]", true())


def _apply_operator(column: ColumnElement[Any], operator: str, value: Any) -> ColumnElement[bool]:
    if value is None:
        if operator in {"=", "in"}:
            return cast("ColumnElement[bool]", column.is_(None))
        if operator in {"!=", "<>", "not in"}:
            return cast("ColumnElement[bool]", column.is_not(None))
        raise KernelError("DOMAIN_MALFORMED", f"El operador '{operator}' no admite None como valor")

    if operator in {"in", "not in"}:
        if not isinstance(value, list | tuple | set):
            raise KernelError(
                "DOMAIN_MALFORMED", f"El operador '{operator}' requiere una lista de valores"
            )
        values = list(value)
        if not values:
            return false() if operator == "in" else _true()
        clause = cast("ColumnElement[bool]", column.in_(values))
        return clause if operator == "in" else not_(clause)

    if operator in {"like", "not like", "ilike", "not ilike", "=like", "=ilike"}:
        if not isinstance(value, str):
            raise KernelError(
                "DOMAIN_MALFORMED", f"El operador '{operator}' requiere un valor de texto"
            )
        pattern = value if operator.startswith("=") else f"%{value}%"
        case_insensitive = "ilike" in operator
        clause = cast(
            "ColumnElement[bool]",
            column.ilike(pattern) if case_insensitive else column.like(pattern),
        )
        return not_(clause) if operator.startswith("not") else clause

    comparisons: dict[str, Any] = {
        "=": lambda: column == value,
        "!=": lambda: column != value,
        "<>": lambda: column != value,
        ">": lambda: column > value,
        ">=": lambda: column >= value,
        "<": lambda: column < value,
        "<=": lambda: column <= value,
    }
    return cast("ColumnElement[bool]", comparisons[operator]())


def _to_prefix(domain: list[Any]) -> list[Any]:
    """Insert the implicit `&` operators the prefix notation allows."""
    operand_count = sum(1 for item in domain if not _is_logic(item))
    explicit_binary = sum(1 for item in domain if _is_logic(item) and item != "!")
    missing = operand_count - explicit_binary - 1
    if missing <= 0:
        return list(domain)
    return ["&"] * missing + list(domain)


def _is_logic(item: Any) -> bool:
    return isinstance(item, str) and item in LOGIC_OPERATORS
