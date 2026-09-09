import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "module",
    [
        "services/organisation_management.py",
        "services/organisation_workspace.py",
        "services/organisation_entitlements.py",
    ],
)
def test_workspace_services_do_not_construct_sql_or_depend_on_http(module):
    tree = ast.parse((APP / module).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(
                ("fastapi", "starlette", "app.api")
            )
            if (node.module or "").startswith("sqlalchemy"):
                assert node.module == "sqlalchemy.ext.asyncio"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {
                "execute",
                "scalar",
                "scalars",
                "query",
                "run_sync",
            }


def test_workspace_http_adapter_does_not_access_persistence():
    tree = ast.parse((APP / "api/organisations.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(
                ("sqlalchemy", "app.repositories")
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {
                "commit",
                "flush",
                "execute",
                "scalar",
                "scalars",
                "run_sync",
            }


def test_workspace_repository_does_not_own_transaction_or_http_policy():
    tree = ast.parse((APP / "repositories/organisation_repository.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(
                ("fastapi", "starlette", "app.api", "app.services")
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"commit", "rollback", "run_sync"}


def test_collection_service_conflicts_are_transport_independent():
    tree = ast.parse((APP / "services/collection_service.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(("fastapi", "starlette", "app.api"))
    service = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    for method in service.body:
        if isinstance(method, ast.FunctionDef) and method.name in {
            "replace_collection", "delete_collection"
        }:
            for node in ast.walk(method):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in {"execute", "scalar", "scalars", "query"}
