"""Suite agéntica (T0.7): valida el catálogo y reporta el resumen.

Con catálogo vacío reporta 0/0 sin fallar — la infraestructura de la
suite queda verificada aunque aún no exista negocio (F0).
"""

from pathlib import Path

import pytest
from ordo_testing import TaskSpec, load_tasks

TASKS_DIR = Path(__file__).parent / "tasks"

pytestmark = pytest.mark.agent


def test_catalog_loads_and_reports() -> None:
    tasks = load_tasks(TASKS_DIR)
    print(f"\n[suite agéntica] tareas ejecutadas: 0/{len(tasks)} (sin AgentClient hasta F3)")
    assert isinstance(tasks, list)


def test_example_task_format_is_valid() -> None:
    example = TASKS_DIR / "sales-001.example.yaml"
    spec = TaskSpec.from_file(example)
    assert spec.id == "sales-001"
    assert spec.goal
    assert spec.setup
    assert spec.assertions


@pytest.mark.parametrize(
    "task",
    [pytest.param(t, id=t.id) for t in load_tasks(TASKS_DIR)],
)
def test_task_spec_is_valid(task: TaskSpec) -> None:
    assert task.id and task.goal and task.assertions
