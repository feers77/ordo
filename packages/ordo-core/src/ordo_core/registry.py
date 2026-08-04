"""Model registry: dependency graph, inheritance merge, validation (F2-01)."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from ordo_core.compute import DependencyGraph, declared_depends
from ordo_core.errors import KernelError
from ordo_core.fields import TECHNICAL_FIELDS, Datetime, Field, Integer, Many2one
from ordo_core.model import Model


@dataclass(frozen=True)
class Module:
    name: str
    models: list[type[Model]] = dc_field(default_factory=list)
    depends: list[str] = dc_field(default_factory=list)


def _technical_fields() -> dict[str, Field]:
    return {
        "id": Integer(readonly=True, index=True),
        "create_uid": Integer(readonly=True),
        "create_date": Datetime(readonly=True),
        "write_uid": Integer(readonly=True),
        "write_date": Datetime(readonly=True, index=True),
        "version": Integer(readonly=True, default=1),
    }


class ModelDefinition:
    def __init__(self, name: str, description: str, table: str, order: str = "id") -> None:
        self.name = name
        self.description = description
        self.table = table
        self.order = order
        self.fields: dict[str, Field] = {}
        self.inherits: dict[str, str] = {}
        self.modules: list[str] = []
        self.classes: list[type[Model]] = []

    def add_field(self, name: str, field: Field, *, module: str) -> None:
        existing = self.fields.get(name)
        if existing is not None and existing.field_type != field.field_type:
            raise KernelError(
                "FIELD_TYPE_CONFLICT",
                f"El módulo '{module}' intenta redefinir {self.name}.{name} de "
                f"'{existing.field_type}' a '{field.field_type}'",
                hint="La extensión puede agregar campos, no cambiar su tipo.",
            )
        bound = field.clone()
        bound.bind(self.name, name)
        self.fields[name] = bound

    def compute_method(self, name: str) -> Any:
        """Resolve a compute method declared by any of the model's classes."""
        for model_cls in reversed(self.classes):
            method = getattr(model_cls, name, None)
            if method is not None:
                return method
        return None

    def run_compute(
        self,
        method_name: str,
        records: list[dict[str, Any]],
        *,
        implementation: Any = None,
    ) -> None:
        """Run a compute in batch: one call for every affected record."""
        method = implementation or self.compute_method(method_name)
        if method is None:
            raise KernelError(
                "COMPUTE_METHOD_MISSING",
                f"{self.name} no define el método '{method_name}'",
            )
        if implementation is None:
            method(None, records)
        else:
            method(records)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "table": self.table,
            "inherits": self.inherits,
            "fields": {name: f.describe() for name, f in sorted(self.fields.items())},
        }


class Registry:
    def __init__(self) -> None:
        self._models: dict[str, ModelDefinition] = {}
        self._frozen = False
        self.dependency_graph = DependencyGraph()

    # -- construcción ----------------------------------------------------

    @classmethod
    def build(cls, modules: list[Module]) -> Registry:
        registry = cls()
        for module in _topological_order(modules):
            registry._apply_module(module)
        registry._resolve_delegation()
        registry._resolve_related()
        registry._validate()
        registry._build_dependency_graph()
        registry._frozen = True
        return registry

    def _apply_module(self, module: Module) -> None:
        for model_cls in module.models:
            if model_cls._name:
                self._define_model(model_cls, module)
            elif model_cls._inherit:
                self._extend_model(model_cls, module)
            else:
                raise KernelError(
                    "MODEL_INVALID_DEFINITION",
                    f"El modelo {model_cls.__name__} no declara _name ni _inherit",
                )

    def _define_model(self, model_cls: type[Model], module: Module) -> None:
        name = model_cls._name
        definition = self._models.get(name)
        if definition is None:
            definition = ModelDefinition(
                name=name,
                description=model_cls._description or name,
                table=model_cls._table or name.replace(".", "_"),
                order=model_cls._order,
            )
            for tech_name, tech_field in _technical_fields().items():
                definition.add_field(tech_name, tech_field, module=module.name)
            self._models[name] = definition
        if model_cls._inherits:
            definition.inherits.update(model_cls._inherits)
        definition.modules.append(module.name)
        definition.classes.append(model_cls)
        for field_name, field_obj in model_cls.declared_fields().items():
            definition.add_field(field_name, field_obj, module=module.name)

    def _extend_model(self, model_cls: type[Model], module: Module) -> None:
        name = model_cls._inherit
        definition = self._models.get(name)
        if definition is None:
            raise KernelError(
                "MODEL_NOT_FOUND",
                f"El módulo '{module.name}' extiende '{name}', que no existe",
                hint="Declara la dependencia del módulo que define ese modelo.",
            )
        definition.modules.append(module.name)
        definition.classes.append(model_cls)
        for field_name, field_obj in model_cls.declared_fields().items():
            definition.add_field(field_name, field_obj, module=module.name)

    def _resolve_delegation(self) -> None:
        for definition in self._models.values():
            for parent_name, link_field in definition.inherits.items():
                parent = self._models.get(parent_name)
                if parent is None:
                    raise KernelError(
                        "MODEL_NOT_FOUND",
                        f"{definition.name} delega en '{parent_name}', que no existe",
                    )
                if link_field not in definition.fields:
                    raise KernelError(
                        "INHERITS_LINK_FIELD_MISSING",
                        f"{definition.name} delega en '{parent_name}' vía '{link_field}', "
                        "campo que no está declarado",
                    )
                link = definition.fields[link_field]
                if not isinstance(link, Many2one) or link.comodel != parent_name:
                    raise KernelError(
                        "INHERITS_LINK_FIELD_MISSING",
                        f"{definition.name}.{link_field} debe ser Many2one a '{parent_name}'",
                    )
                for field_name, parent_field in parent.fields.items():
                    if field_name in TECHNICAL_FIELDS or field_name in definition.fields:
                        continue
                    delegated = parent_field.clone()
                    delegated.bind(definition.name, field_name)
                    delegated.delegated_from = parent_name
                    definition.fields[field_name] = delegated

    def _resolve_related(self) -> None:
        """`related="a.b.c"` is sugar over compute: derive method and depends."""
        for definition in self._models.values():
            for field_name, field_obj in definition.fields.items():
                if not field_obj.related:
                    continue
                self._validate_path(definition, field_obj.related, field_name)
                if field_obj.compute is None:
                    field_obj.compute = f"_compute_related_{field_name}"
                field_obj.store = False

    def _validate_path(self, definition: ModelDefinition, path: str, field_name: str) -> None:
        current = definition
        parts = path.split(".")
        for index, part in enumerate(parts):
            field = current.fields.get(part)
            if field is None:
                raise KernelError(
                    "COMPUTE_INVALID_RELATED",
                    f"{definition.name}.{field_name}: la ruta '{path}' no resuelve en '{part}'",
                )
            if index == len(parts) - 1:
                return
            if not isinstance(field, Many2one):
                raise KernelError(
                    "COMPUTE_INVALID_RELATED",
                    f"{definition.name}.{field_name}: '{part}' no es relacional en '{path}'",
                )
            current = self._models[field.comodel]

    def _build_dependency_graph(self) -> None:
        graph = self.dependency_graph
        for definition in self._models.values():
            for field_name, field_obj in definition.fields.items():
                if not field_obj.compute:
                    continue
                paths = self._depends_paths(definition, field_name, field_obj)
                for path in paths:
                    for trigger in self._resolve_triggers(definition, path, field_name):
                        graph.add_edge(trigger, (definition.name, field_name))
        graph.validate_acyclic()

    def _depends_paths(
        self, definition: ModelDefinition, field_name: str, field_obj: Field
    ) -> tuple[str, ...]:
        if field_obj.related:
            return (field_obj.related,)
        method = definition.compute_method(field_obj.compute or "")
        if method is None:
            raise KernelError(
                "COMPUTE_METHOD_MISSING",
                f"{definition.name}.{field_name} declara compute='{field_obj.compute}' "
                "pero el método no existe",
            )
        paths = declared_depends(method)
        if not paths:
            raise KernelError(
                "COMPUTE_MISSING_DEPENDS",
                f"{definition.name}.{field_obj.compute} no declara @depends",
                hint="Sin dependencias el kernel no sabe cuándo recomputar.",
            )
        return paths

    def _resolve_triggers(
        self, definition: ModelDefinition, path: str, field_name: str
    ) -> list[tuple[str, str]]:
        """Every hop of the path triggers recomputation, not only the last one.

        For `partner_id.country_id.code`, changing the partner, its country or
        the country code must all invalidate the dependent field.
        """
        triggers: list[tuple[str, str]] = []
        current = definition
        parts = path.split(".")
        for index, part in enumerate(parts):
            field = current.fields.get(part)
            if field is None:
                raise KernelError(
                    "COMPUTE_UNKNOWN_DEPENDENCY",
                    f"{definition.name}.{field_name} depende de '{path}', "
                    f"pero '{part}' no existe en {current.name}",
                )
            triggers.append((current.name, part))
            if index == len(parts) - 1:
                return triggers
            comodel = getattr(field, "comodel", None)
            if comodel is None:
                raise KernelError(
                    "COMPUTE_UNKNOWN_DEPENDENCY",
                    f"{definition.name}.{field_name}: '{part}' no es relacional en '{path}'",
                )
            current = self._models[comodel]
        raise KernelError("COMPUTE_UNKNOWN_DEPENDENCY", f"Ruta inválida: {path!r}")

    def _validate(self) -> None:
        for definition in self._models.values():
            for field_name, field_obj in definition.fields.items():
                if field_name in TECHNICAL_FIELDS or field_obj.delegated_from:
                    continue
                if not field_obj.agent_hint or not field_obj.examples:
                    raise KernelError(
                        "FIELD_MISSING_AGENT_METADATA",
                        f"{definition.name}.{field_name} requiere agent_hint y examples",
                        hint="Todo campo de negocio alimenta el schema semántico (AGENTS.md §4).",
                    )
                if isinstance(field_obj, Many2one) and field_obj.comodel not in self._models:
                    raise KernelError(
                        "MODEL_NOT_FOUND",
                        f"{definition.name}.{field_name} apunta a '{field_obj.comodel}', "
                        "que no está registrado",
                    )

    # -- acceso ----------------------------------------------------------

    def __getitem__(self, model_name: str) -> ModelDefinition:
        try:
            return self._models[model_name]
        except KeyError as exc:
            raise KernelError(
                "MODEL_NOT_FOUND", f"El modelo '{model_name}' no está registrado"
            ) from exc

    def __contains__(self, model_name: str) -> bool:
        return model_name in self._models

    @property
    def model_names(self) -> list[str]:
        return sorted(self._models)

    def add_model_definition(self, name: str, spec: dict[str, Any]) -> None:
        if self._frozen:
            raise KernelError(
                "REGISTRY_FROZEN",
                "El registry está congelado; usa studio-api para campos dinámicos",
            )
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {name: d.describe() for name, d in sorted(self._models.items())}


def _topological_order(modules: list[Module]) -> list[Module]:
    by_name = {module.name: module for module in modules}
    for module in modules:
        for dependency in module.depends:
            if dependency not in by_name:
                raise KernelError(
                    "REGISTRY_MISSING_DEPENDENCY",
                    f"El módulo '{module.name}' depende de '{dependency}', que no está presente",
                )
    ordered: list[Module] = []
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(name: str, path: tuple[str, ...]) -> None:
        if name in done:
            return
        if name in visiting:
            cycle = " -> ".join([*path, name])
            raise KernelError(
                "REGISTRY_DEPENDENCY_CYCLE", f"Ciclo de dependencias entre módulos: {cycle}"
            )
        visiting.add(name)
        module = by_name[name]
        for dependency in sorted(module.depends):
            visit(dependency, (*path, name))
        visiting.discard(name)
        done.add(name)
        ordered.append(module)

    for name in sorted(by_name):
        visit(name, ())
    return ordered
