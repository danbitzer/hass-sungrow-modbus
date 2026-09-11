"""Cross-cutting contracts: docstrings cite the right register, exports agree."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from modbus_connection.model import RegisterField

import sungrow_inverter
from sungrow_inverter import (
    DESIRED_BATTERY_MODES,
    BatteryMode,
    components as components_pkg,
)

COMPONENTS_DIR = Path(components_pkg.__file__).parent
REG = re.compile(r"\(reg (\d+)[,;)]")


def _field_docstrings(path: Path) -> dict[tuple[str, str], str]:
    """``(class, field) -> docstring`` for every annotated field in a module."""
    tree = ast.parse(path.read_text())
    found: dict[tuple[str, str], str] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        body = node.body
        for index, stmt in enumerate(body):
            target = None
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                target = stmt.targets[0]
            elif isinstance(stmt, ast.AnnAssign):
                target = stmt.target
            if not isinstance(target, ast.Name):
                continue
            nxt = body[index + 1] if index + 1 < len(body) else None
            if (
                isinstance(nxt, ast.Expr)
                and isinstance(nxt.value, ast.Constant)
                and isinstance(nxt.value.value, str)
            ):
                found[(node.name, target.id)] = nxt.value.value
    return found


@pytest.mark.parametrize(
    "path", sorted(COMPONENTS_DIR.glob("*.py")), ids=lambda p: p.stem
)
def test_every_field_docstring_cites_its_register(path: Path) -> None:
    docs = _field_docstrings(path)
    module = getattr(components_pkg, path.stem, None)
    if module is None:
        pytest.skip("no fields")
    checked = 0
    for name in dir(module):
        cls = getattr(module, name)
        if not isinstance(cls, type) or not hasattr(cls, "declared_fields"):
            continue
        for field_name, field in cls.declared_fields.items():
            if (name, field_name) not in docs:
                continue  # inherited or undocumented
            assert isinstance(field, RegisterField)
            doc = docs[(name, field_name)]
            match = REG.search(doc)
            assert match, f"{name}.{field_name} has no (reg N) in its docstring"
            assert int(match.group(1)) == field.address + 1, (
                f"{name}.{field_name}: docstring says reg {match.group(1)}, "
                f"address {field.address} means reg {field.address + 1}"
            )
            checked += 1
    if path.stem not in ("base", "__init__"):
        assert checked, f"{path.stem}: no documented fields found"


def test_desired_modes_are_the_first_five_battery_modes() -> None:
    assert DESIRED_BATTERY_MODES == frozenset(list(BatteryMode)[:6])
    assert BatteryMode.UNKNOWN not in DESIRED_BATTERY_MODES


def test_public_api_is_exported() -> None:
    for name in sungrow_inverter.__all__:
        assert hasattr(sungrow_inverter, name), name
    assert sungrow_inverter.__version__ == "0.1.0a1"
