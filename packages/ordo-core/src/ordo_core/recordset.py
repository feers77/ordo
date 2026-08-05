"""Write ORM over the domain compiler (design F2-04).

Everything is batch-first: a single-record call is a batch of one. Reads
always go through the domain compiler, so tenant scoping and record rules
are never bypassed.
"""

from __future__ import annotations

import base64
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Table, delete, insert, select, update

from ordo_core.coercion import parse_decimal, parse_temporal
from ordo_core.domains import DomainCompiler
from ordo_core.environment import Environment
from ordo_core.errors import KernelError
from ordo_core.fields import TECHNICAL_FIELDS, Field, Monetary, Selection
from ordo_core.registry import ModelDefinition

WRITABLE_TECHNICAL = frozenset({"company_id"})
MAX_GROUPS = 500
# Cero por tipo de campo: el vacío se devuelve con la misma forma que un
# total real (string decimal para dinero, número para el resto).
_ZERO_BY_TYPE: dict[str, Any] = {"monetary": "0", "float": 0.0, "integer": 0}


class RecordSet:
    def __init__(self, env: Environment, model: str) -> None:
        self.env = env
        self.model_name = model
        self.definition: ModelDefinition = env.registry[model]
        self.compiler = DomainCompiler(env.registry, schema=env.schema)

    @property
    def _table(self) -> Table:
        return self.compiler._table_for(self.definition)

    # -- validación ------------------------------------------------------

    def _validate(self, values: dict[str, Any], *, creating: bool) -> dict[str, Any]:
        cleaned: dict[str, Any] = {}
        for name, value in values.items():
            field = self.definition.fields.get(name)
            if field is None:
                raise KernelError(
                    "FIELD_UNKNOWN",
                    f"El campo '{name}' no existe en {self.model_name}",
                    hint="Consulta /meta/v1/schema para ver los campos disponibles.",
                )
            if name in TECHNICAL_FIELDS and name not in WRITABLE_TECHNICAL:
                raise KernelError("FIELD_READONLY", f"'{name}' es un campo técnico y no se escribe")
            if field.readonly:
                raise KernelError(
                    "FIELD_READONLY", f"'{self.model_name}.{name}' es de solo lectura"
                )
            if not field.store:
                raise KernelError(
                    "FIELD_NOT_STORED",
                    f"'{self.model_name}.{name}' no se almacena; no se puede escribir",
                )
            cleaned[name] = _coerce(field, value, self.model_name, name)

        if creating:
            for name, field in self.definition.fields.items():
                if name in TECHNICAL_FIELDS or not field.store:
                    continue
                if name in cleaned:
                    continue
                if field.default is not None:
                    cleaned[name] = field.default
                elif field.required:
                    raise KernelError(
                        "FIELD_REQUIRED",
                        f"'{self.model_name}.{name}' es obligatorio",
                        hint=field.agent_hint,
                    )
        return cleaned

    # -- escritura --------------------------------------------------------

    async def create(self, values_list: list[dict[str, Any]], *, dry_run: bool = False) -> Any:
        validations: list[dict[str, Any]] = []
        rows: list[dict[str, Any]] = []
        for index, values in enumerate(values_list):
            try:
                rows.append(self._stamp(self._validate(values, creating=True), creating=True))
            except KernelError as exc:
                if not dry_run:
                    raise
                validations.append({"index": index, "code": exc.code, "message": exc.message})

        if dry_run:
            return {"would_create": len(rows), "validations": validations}

        result = await self.env.session.execute(
            insert(self._table).returning(self._table.c.id), rows
        )
        ids = [row[0] for row in result.all()]
        await self.env.session.flush()
        return ids

    async def write(
        self,
        ids: list[int],
        values: dict[str, Any],
        *,
        expected_version: int | None = None,
        dry_run: bool = False,
    ) -> int:
        cleaned = self._stamp(self._validate(values, creating=False), creating=False)
        if expected_version is not None:
            await self._check_versions(ids, expected_version)

        savepoint = await self.env.session.begin_nested() if dry_run else None
        stmt = (
            update(self._table)
            .where(self._table.c.id.in_(ids))
            .values(**cleaned, version=self._table.c.version + 1)
        )
        result = await self.env.session.execute(stmt)
        rowcount = getattr(result, "rowcount", 0)
        if savepoint is not None:
            await savepoint.rollback()
        return int(rowcount or 0)

    async def unlink(self, ids: list[int], *, dry_run: bool = False) -> int:
        savepoint = await self.env.session.begin_nested() if dry_run else None
        result = await self.env.session.execute(
            delete(self._table).where(self._table.c.id.in_(ids))
        )
        rowcount = getattr(result, "rowcount", 0)
        if savepoint is not None:
            await savepoint.rollback()
        return int(rowcount or 0)

    async def _check_versions(self, ids: list[int], expected_version: int) -> None:
        table = self._table
        rows = (
            await self.env.session.execute(
                select(table.c.id, table.c.version).where(table.c.id.in_(ids))
            )
        ).all()
        stale = [row.id for row in rows if row.version != expected_version]
        if not stale:
            return
        current = await self.read(stale)
        raise KernelError(
            "CONCURRENT_MODIFICATION",
            "El registro fue modificado por otra operación",
            hint="Relee el registro, reconcilia los cambios y reintenta.",
            current_state=current,
        )

    def _stamp(self, values: dict[str, Any], *, creating: bool) -> dict[str, Any]:
        now = datetime.now(UTC)
        actor = _actor_id(self.env)
        stamped = dict(values)
        if creating:
            stamped.update({"create_date": now, "write_date": now, "version": 1})
            if actor is not None:
                stamped.update({"create_uid": actor, "write_uid": actor})
        else:
            stamped["write_date"] = now
            if actor is not None:
                stamped["write_uid"] = actor
        return stamped

    # -- lectura ----------------------------------------------------------

    async def read(self, ids: list[int], fields: list[str] | None = None) -> list[dict[str, Any]]:
        names = fields or self._default_fields()
        stmt = self.compiler.select(
            model=self.model_name,
            domain=[("id", "in", ids)],
            fields=names,
            active_test=False,
        )
        result = await self.env.session.execute(stmt)
        return [dict(zip(names, row, strict=True)) for row in result.all()]

    async def search(
        self,
        domain: list[Any],
        *,
        fields: list[str] | None = None,
        limit: int = 80,
        cursor: str | None = None,
        rules: dict[str, list[Any]] | None = None,
        active_test: bool = True,
    ) -> dict[str, Any]:
        names = fields or self._default_fields()
        if "id" not in names:
            names = ["id", *names]
        effective_domain = list(domain)
        if cursor is not None:
            effective_domain = [*effective_domain, ("id", ">", _decode_cursor(cursor))]
        stmt = self.compiler.select(
            model=self.model_name,
            domain=effective_domain,
            fields=names,
            rules=rules,
            limit=limit + 1,
            order="id asc",
            active_test=active_test,
        )
        result = await self.env.session.execute(stmt)
        rows = [dict(zip(names, row, strict=True)) for row in result.all()]
        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            next_cursor = _encode_cursor(int(rows[-1]["id"]))
        return {"rows": rows, "next_cursor": next_cursor}

    async def read_group(
        self,
        domain: list[Any],
        *,
        group_by: list[str] | None = None,
        aggregates: list[str] | None = None,
        order: str | None = None,
        limit: int = 80,
        rules: dict[str, list[Any]] | None = None,
        active_test: bool = True,
    ) -> dict[str, Any]:
        """Group and aggregate in the database instead of paging records out.

        Money comes back as a decimal string and dates in ISO format, like
        everywhere else in the API (AGENTS.md §2.3).
        """
        keys = list(group_by or [])
        specs = list(aggregates or ["count"])
        stmt = self.compiler.aggregate(
            model=self.model_name,
            domain=domain,
            group_by=keys,
            aggregates=specs,
            rules=rules,
            order=order,
            limit=min(limit, MAX_GROUPS),
            active_test=active_test,
        )
        result = await self.env.session.execute(stmt)
        groups: list[dict[str, Any]] = []
        for row in result.all():
            values = list(row)
            group = {name: _serialize(values[index]) for index, name in enumerate(keys)}
            for offset, spec in enumerate(specs):
                group[spec] = self._serialize_aggregate(spec, values[len(keys) + offset])
            groups.append(group)
        return {"groups": groups, "total_groups": len(groups)}

    def _serialize_aggregate(self, spec: str, value: Any) -> Any:
        if spec == "count":
            return int(value or 0)
        name, _, field_name = spec.partition(":")
        if value is None and name == "sum":
            # SUM sobre un grupo sin valores devuelve NULL; un total ausente
            # se lee como cero, no como None, para que quien consuma la
            # respuesta pueda sumar sin ramificar.
            field = self.definition.fields.get(field_name)
            return _ZERO_BY_TYPE.get(field.field_type if field else "", 0)
        return _serialize(value)

    def _default_fields(self) -> list[str]:
        return [
            name
            for name, field in self.definition.fields.items()
            if field.store and field.field_type not in {"one2many", "many2many"}
        ]


def _actor_id(env: Environment) -> int | None:
    """Numeric actor for audit columns; UUID principals map at the API layer."""
    return env.context.get("actor_uid")


def _coerce(field: Field, value: Any, model: str, name: str) -> Any:
    if value is None:
        if field.required:
            raise KernelError("FIELD_REQUIRED", f"'{model}.{name}' es obligatorio")
        return None

    if isinstance(field, Monetary):
        return parse_decimal(value, f"'{model}.{name}'")

    if field.field_type in {"date", "datetime"} and isinstance(value, str):
        return parse_temporal(field.field_type, value, f"'{model}.{name}'")

    if isinstance(field, Selection) and value not in field.allowed_values:
        raise KernelError(
            "FIELD_INVALID_VALUE",
            f"'{value}' no es un valor válido para {model}.{name}",
            hint=f"Valores permitidos: {sorted(field.allowed_values)}",
        )

    if field.field_type == "integer" and not isinstance(value, int):
        raise KernelError("FIELD_INVALID_VALUE", f"'{model}.{name}' espera un entero")
    if field.field_type == "boolean" and not isinstance(value, bool):
        raise KernelError("FIELD_INVALID_VALUE", f"'{model}.{name}' espera un booleano")
    if field.field_type in {"char", "text", "html"} and not isinstance(value, str):
        raise KernelError("FIELD_INVALID_VALUE", f"'{model}.{name}' espera texto")
    return value


def _serialize(value: Any) -> Any:
    """JSON-safe value: money as decimal string, temporals as ISO."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _encode_cursor(last_id: int) -> str:
    return base64.urlsafe_b64encode(f"id:{last_id}".encode()).decode()


def _decode_cursor(cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        prefix, value = raw.split(":", 1)
        if prefix != "id":
            raise ValueError(prefix)
        return int(value)
    except Exception as exc:
        raise KernelError(
            "INVALID_CURSOR",
            "El cursor de paginación no es válido",
            hint="Usa el next_cursor devuelto por la consulta anterior.",
        ) from exc
