import importlib.util
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

spec = importlib.util.spec_from_file_location(
    "chat_rollout", Path(__file__).parents[3] / ".cloudbuild/scripts/chat_rollout.py"
)
rollout_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rollout_module)


def service(revision, percent=100, commit="new"):
    return {
        "metadata": {"labels": {"commit-sha": commit}},
        "status": {
            "latestReadyRevisionName": revision,
            "traffic": [{"revisionName": revision, "percent": percent}],
        },
    }


def test_capture_preserves_split_and_ignores_tags():
    state = service("old", 75)
    state["status"]["traffic"] += [
        {"revisionName": "other", "percent": 25},
        {"revisionName": "tagged", "tag": "preview"},
    ]
    assert rollout_module.traffic_targets(state) == {"old": 75, "other": 25}


@pytest.mark.parametrize(
    "state", [service("old", 99), {"status": {"traffic": [{"percent": 100}]}}]
)
def test_invalid_traffic_is_rejected(state):
    with pytest.raises(ValueError):
        rollout_module.traffic_targets(state)


def test_candidate_requires_exact_commit_and_full_traffic():
    rollout = rollout_module.Rollout("project", "region", "new")
    rollout.describe = Mock(return_value=service("revision", commit="someone-else"))
    with pytest.raises(RuntimeError, match="commit"):
        rollout.candidate(["public"], "new")


def test_stale_rollback_never_changes_either_service():
    rollout = rollout_module.Rollout("project", "region", "new")
    rollout.read_owned_service = Mock(
        side_effect=[{}, RuntimeError("refusing stale rollback")]
    )
    with patch.object(rollout, "restore_service") as run:
        with pytest.raises(RuntimeError, match="stale rollback"):
            rollout.restore(
                {"public": {"old-public": 100}, "internal": {"old-internal": 100}},
                {"public": {"new-public": 100}, "internal": {"new-internal": 100}},
            )
    run.assert_not_called()


def test_rollback_restores_both_targets_and_verifies_readback():
    rollout = rollout_module.Rollout("project", "region", "new")
    rollout.read_owned_service = Mock(return_value={})
    rollout.describe = Mock(
        side_effect=[
            service("old-public"),
            service("old-internal"),
        ]
    )
    with patch.object(rollout, "restore_service") as run:
        rollout.restore(
            {"public": {"old-public": 100}, "internal": {"old-internal": 100}},
            {"public": {"new-public": 100}, "internal": {"new-internal": 100}},
        )
    assert run.call_count == 2
    assert run.call_args_list[0].args == (
        "public",
        {"old-public": 100},
        {"new-public": 100},
    )
    assert run.call_args_list[1].args == (
        "internal",
        {"old-internal": 100},
        {"new-internal": 100},
    )


def test_partial_rollback_failure_still_attempts_other_service():
    rollout = rollout_module.Rollout("project", "region", "new")
    rollout.read_owned_service = Mock(return_value={})
    rollout.describe = Mock(
        side_effect=[
            service("old-internal"),
        ]
    )
    with patch.object(
        rollout,
        "restore_service",
        side_effect=[rollout_module.subprocess.CalledProcessError(1, "gcloud"), None],
    ) as run:
        with pytest.raises(RuntimeError):
            rollout.restore(
                {"public": {"old-public": 100}, "internal": {"old-internal": 100}},
                {"public": {"new-public": 100}, "internal": {"new-internal": 100}},
            )
    assert run.call_count == 2


def rest_state(**overrides):
    return {
        "etag": "etag-before",
        "labels": {"commit-sha": "new"},
        "trafficStatuses": [{"revision": "new-public", "percent": 100}],
        **overrides,
    }


def response(payload):
    import io
    import json

    return io.BytesIO(json.dumps(payload).encode())


@pytest.mark.parametrize(
    "state",
    [
        rest_state(reconciling=True),
        rest_state(labels={"commit-sha": "newer"}),
        rest_state(etag=""),
        rest_state(trafficStatuses=[{"revision": "someone-else", "percent": 100}]),
    ],
)
def test_rest_refuses_changed_owner_or_unsettled_state(state):
    rollout = rollout_module.Rollout("project", "region", "new")
    with (
        patch.object(rollout_module.subprocess, "check_output", return_value="token"),
        patch.object(
            rollout_module.urllib.request, "urlopen", return_value=response(state)
        ) as request,
    ):
        with pytest.raises(RuntimeError, match="stale rollback"):
            rollout.restore_service("public", {"old-public": 100}, {"new-public": 100})
    assert request.call_count == 1


def test_rest_uses_etag_and_explicit_traffic_then_waits_for_completion():
    import json

    rollout = rollout_module.Rollout("project", "region", "new")
    with (
        patch.object(rollout_module.subprocess, "check_output", return_value="token"),
        patch.object(
            rollout_module.urllib.request,
            "urlopen",
            side_effect=[
                response(rest_state()),
                response(
                    {"name": "projects/project/locations/region/operations/restore"}
                ),
                response({"done": True}),
            ],
        ) as request,
        patch.object(rollout_module.time, "sleep"),
    ):
        rollout.restore_service(
            "public", {"old-public": 75, "older-public": 25}, {"new-public": 100}
        )
    mutation = request.call_args_list[1].args[0]
    assert mutation.method == "PATCH"
    assert mutation.full_url.endswith("/services/public?updateMask=traffic")
    payload = json.loads(mutation.data)
    assert payload["etag"] == "etag-before"
    assert payload["traffic"] == [
        {
            "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
            "revision": "old-public",
            "percent": 75,
        },
        {
            "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
            "revision": "older-public",
            "percent": 25,
        },
    ]
    assert request.call_count == 3


def test_rest_operation_failure_does_not_report_success():
    rollout = rollout_module.Rollout("project", "region", "new")
    with (
        patch.object(rollout_module.subprocess, "check_output", return_value="token"),
        patch.object(
            rollout_module.urllib.request,
            "urlopen",
            side_effect=[
                response(rest_state()),
                response({"done": True, "error": {"code": 9}}),
            ],
        ),
    ):
        with pytest.raises(RuntimeError, match="operation failed"):
            rollout.restore_service("public", {"old-public": 100}, {"new-public": 100})


def cli_args(action, directory):
    return [
        "chat_rollout.py",
        action,
        "--project=project",
        "--region=region",
        "--service=public",
        "--commit=new",
        f"--directory={directory}",
    ]


def test_finish_restores_partial_deploy_and_still_fails_build(tmp_path):
    import json

    (tmp_path / "chat-previous-traffic.json").write_text(
        json.dumps(
            {"public": {"old-public": 100}, "public-internal": {"old-internal": 100}}
        )
    )
    with (
        patch("sys.argv", cli_args("finish", tmp_path)),
        patch.object(rollout_module, "Rollout") as factory,
    ):
        rollout = factory.return_value
        rollout.describe.side_effect = [
            service("new-public"),
            service("old-internal", commit="old"),
        ]
        with pytest.raises(SystemExit, match="previous traffic restored"):
            rollout_module.main()
    rollout.restore.assert_called_once_with(
        {"public": {"old-public": 100}}, {"public": {"new-public": 100}}
    )


def test_candidate_requires_both_successful_deploy_markers(tmp_path):
    with (
        patch("sys.argv", cli_args("candidate", tmp_path)),
        patch.object(rollout_module, "Rollout") as factory,
    ):
        with pytest.raises(RuntimeError, match="Both deployment steps"):
            rollout_module.main()
    factory.return_value.candidate.assert_not_called()


def test_baseline_change_blocks_production_mutation(tmp_path):
    import json

    (tmp_path / "chat-previous-traffic.json").write_text(
        json.dumps({"public": {"old": 100}})
    )
    with (
        patch("sys.argv", cli_args("baseline", tmp_path)),
        patch.object(rollout_module, "Rollout") as factory,
    ):
        factory.return_value.capture.return_value = {"public": {"newer": 100}}
        with pytest.raises(RuntimeError, match="baseline"):
            rollout_module.main()


@pytest.mark.parametrize(
    "internal",
    [
        rest_state(
            labels={"commit-sha": "newer"},
            trafficStatuses=[{"revision": "new-internal", "percent": 100}],
        ),
        rest_state(
            reconciling=True,
            trafficStatuses=[{"revision": "new-internal", "percent": 100}],
        ),
    ],
)
def test_pair_preflight_checks_ownership_before_any_mutation(internal):
    rollout = rollout_module.Rollout("project", "region", "new")
    with patch.object(rollout, "api", side_effect=[rest_state(), internal]) as api:
        with pytest.raises(RuntimeError, match="stale rollback"):
            rollout.restore(
                {"public": {"old-public": 100}, "internal": {"old-internal": 100}},
                {"public": {"new-public": 100}, "internal": {"new-internal": 100}},
            )
    assert api.call_count == 2
    assert all(len(call.args) == 1 for call in api.call_args_list)
