import ast
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parents[3] / "alembic" / "versions"
LEGACY_APPLICATION_IMPORTS = {
    "9e2c2d162ac7_add_country_data.py": {"app.models"},
    "3d0364f5cfe8_add_reading_abilities.py": {"app.models"},
    "e4f0666727f3_add_initial_root_users.py": {"app.models"},
    "a1b2c3d4e5f6_add_recommendable_editions_mv.py": {
        "app.db.functions",
        "app.db.views",
    },
}


@pytest.mark.parametrize(
    "path", sorted(VERSIONS.glob("*.py")), ids=lambda path: path.name
)
def test_revision_has_no_application_imports(path):
    modules = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    application_imports = {
        module for module in modules if module == "app" or module.startswith("app.")
    }
    assert application_imports == LEGACY_APPLICATION_IMPORTS.get(path.name, set())
