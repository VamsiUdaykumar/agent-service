"""Structural proof of layer separation: app/domain imports nothing from the
outer layers. A lightweight stand-in for import-linter — walks the AST of
every module in app/domain rather than importing the forbidden packages.
"""

import ast
from pathlib import Path

FORBIDDEN_PREFIXES = ("app.persistence", "app.api", "app.services", "app.runner")
DOMAIN_DIR = Path(__file__).resolve().parents[2] / "app" / "domain"


def _imported_module_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_domain_package_imports_nothing_from_outer_layers() -> None:
    violations: dict[str, set[str]] = {}
    for path in DOMAIN_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = _imported_module_names(tree)
        bad = {
            name
            for name in imported
            if any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN_PREFIXES)
        }
        if bad:
            violations[str(path.relative_to(DOMAIN_DIR))] = bad

    assert not violations, f"app/domain modules importing outer layers: {violations}"
