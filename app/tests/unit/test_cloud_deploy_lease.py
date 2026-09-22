import json
import subprocess
from dataclasses import asdict
from unittest.mock import Mock

import pytest

from deploy import lease as module


def lease_file(tmp_path, **overrides):
    path = tmp_path / "lease.json"
    data = asdict(module.Lease("project", "build", module.lease_url("project"), "42"))
    data.update(overrides)
    path.write_text(json.dumps(data))
    return path


def storage_responses(monkeypatch, *responses):
    storage = Mock(side_effect=[json.dumps(value) for value in responses])
    monkeypatch.setattr(module, "command", storage)
    return storage


@pytest.mark.parametrize(
    "overrides", [{"owner": "different"}, {"project": "different"}]
)
def test_rejects_a_different_build_before_contacting_storage(
    tmp_path, monkeypatch, overrides
):
    storage = Mock()
    monkeypatch.setattr(module, "command", storage)
    with pytest.raises(RuntimeError, match="another build"):
        module.assert_lease("project", "build", lease_file(tmp_path, **overrides))
    storage.assert_not_called()


def test_rejects_replaced_lease_generation(tmp_path, monkeypatch):
    storage = storage_responses(monkeypatch, {"generation": "43"})
    with pytest.raises(RuntimeError, match="ownership changed"):
        module.assert_lease("project", "build", lease_file(tmp_path))
    assert storage.call_count == 1


def test_accepts_only_matching_remote_owner_and_generation(tmp_path, monkeypatch):
    storage = storage_responses(monkeypatch, {"generation": "42"}, {"owner": "build"})
    assert (
        module.assert_lease("project", "build", lease_file(tmp_path)).generation == "42"
    )
    assert storage.call_args.args[0] == ["cat", f"{module.lease_url('project')}#42"]


@pytest.mark.parametrize(
    "overrides", [{"url": "gs://other/lock"}, {"generation": ""}, {"generation": 42}]
)
def test_invalid_receipt_cannot_select_a_storage_object(
    tmp_path, monkeypatch, overrides
):
    storage = Mock()
    monkeypatch.setattr(module, "command", storage)
    with pytest.raises(ValueError):
        module.assert_lease("project", "build", lease_file(tmp_path, **overrides))
    storage.assert_not_called()


def test_acquire_never_adopts_another_builds_replacement(tmp_path, monkeypatch):
    storage = storage_responses(
        monkeypatch, {}, {"generation": "43"}, {"owner": "replacement"}
    )
    path = tmp_path / "receipt.json"
    with pytest.raises(RuntimeError, match="another build"):
        module.acquire_lease("project", "build", path)
    assert "--if-generation-match=0" in storage.call_args_list[0].args[0]
    assert not path.exists()


def test_acquire_writes_receipt_only_after_remote_owner_verified(tmp_path, monkeypatch):
    storage_responses(monkeypatch, {}, {"generation": "42"}, {"owner": "build"})
    path = tmp_path / "receipt.json"
    lease = module.acquire_lease("project", "build", path)
    assert module.Lease.read(path) == lease


def test_release_is_generation_conditional_and_removes_receipt(tmp_path, monkeypatch):
    storage = storage_responses(
        monkeypatch, {"generation": "42"}, {"owner": "build"}, {}
    )
    path = lease_file(tmp_path)
    module.release_lease("project", "build", path)
    assert storage.call_args.args[0] == [
        "rm",
        module.lease_url("project"),
        "--if-generation-match=42",
    ]
    assert not path.exists()


def test_failed_conditional_delete_retains_receipt(tmp_path, monkeypatch):
    storage = Mock(
        side_effect=[
            '{"generation":"42"}',
            '{"owner":"build"}',
            subprocess.CalledProcessError(1, "gcloud"),
        ]
    )
    monkeypatch.setattr(module, "command", storage)
    path = lease_file(tmp_path)
    with pytest.raises(subprocess.CalledProcessError):
        module.release_lease("project", "build", path)
    assert path.exists()
