import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location(
    "deploy_lease", Path(__file__).parents[3] / "deploy/lease.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def lease_file(tmp_path):
    path = tmp_path / "lease.json"
    path.write_text(
        json.dumps(
            {
                "project": "project",
                "owner": "build",
                "generation": "42",
                "url": "gs://private/pipeline.lock",
            }
        )
    )
    return path


def test_rejects_a_different_build_before_contacting_storage(tmp_path, monkeypatch):
    storage = Mock()
    monkeypatch.setattr(module, "command", storage)
    with pytest.raises(RuntimeError, match="another build"):
        module.assert_lease("project", "different", lease_file(tmp_path))
    storage.assert_not_called()


def test_rejects_replaced_lease_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        module, "command", lambda args: json.dumps({"generation": "43"})
    )
    with pytest.raises(RuntimeError, match="ownership changed"):
        module.assert_lease("project", "build", lease_file(tmp_path))


def test_accepts_only_matching_owner_and_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        module, "command", lambda args: json.dumps({"generation": "42"})
    )
    assert (
        module.assert_lease("project", "build", lease_file(tmp_path))["generation"]
        == "42"
    )
