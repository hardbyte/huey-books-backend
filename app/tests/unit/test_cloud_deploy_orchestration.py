from unittest.mock import Mock

import pytest

from deploy import release as module
from deploy.release import Release, RolloutState, Target


@pytest.fixture
def release():
    return Release("project", "region", "release")


@pytest.fixture
def cloud(monkeypatch):
    cloud = Mock()
    monkeypatch.setattr(module, "gcloud", cloud)
    return cloud


@pytest.mark.parametrize(
    "state", [state for state in RolloutState if state != RolloutState.SUCCEEDED]
)
def test_production_promotion_requires_verified_development(release, cloud, state):
    cloud.return_value = {"targetId": "chat-dev", "state": state}
    with pytest.raises(RuntimeError, match="verified development"):
        release.promote(Target.PRODUCTION)
    assert cloud.call_count == 1
    assert cloud.call_args.args[0][:3] == ["deploy", "rollouts", "describe"]


def test_verified_development_allows_production_promotion(release, cloud):
    cloud.return_value = {"targetId": "chat-dev", "state": "SUCCEEDED"}
    release.promote(Target.PRODUCTION)
    assert "--to-target=chat-prod" in cloud.call_args.args[0]
    assert "--rollout-id=chat-prod" in cloud.call_args.args[0]


@pytest.mark.parametrize(
    "resource",
    [
        {"targetId": "chat-prod", "state": "SUCCEEDED"},
        {"targetId": "chat-dev", "state": "SUCCEEDED", "controllerRollout": "other"},
        {"targetId": "chat-dev", "state": "UNKNOWN"},
    ],
)
def test_ambiguous_or_unknown_rollout_cannot_pass(release, cloud, resource):
    cloud.return_value = resource
    with pytest.raises(ValueError):
        release.rollout_state(Target.DEVELOPMENT)


def test_observes_submitted_controller_instead_of_any_successful_recovery(
    release, cloud
):
    cloud.return_value = {"targetId": "chat-dev", "state": "FAILED"}
    assert release.rollout_state(Target.DEVELOPMENT) == RolloutState.FAILED
    assert cloud.call_args.args[0] == [
        "deploy",
        "rollouts",
        "describe",
        "chat-dev",
        "--release=release",
        "--delivery-pipeline=chat",
    ]


@pytest.mark.parametrize("state", list(module.TERMINAL_FAILURES))
def test_failed_rollout_does_not_cancel_or_override_native_repair(
    release, cloud, state
):
    cloud.return_value = {"targetId": "chat-dev", "state": state}
    with pytest.raises(RuntimeError, match="native repair"):
        release.wait_for_rollout(Target.DEVELOPMENT, 60)
    assert cloud.call_count == 1
    assert cloud.call_args.args[0][1:3] == ["rollouts", "describe"]


def test_wait_observes_progress_then_success(release, cloud, monkeypatch):
    cloud.side_effect = [
        {"targetId": "chat-dev", "state": state}
        for state in ["PENDING", "IN_PROGRESS", "SUCCEEDED"]
    ]
    monkeypatch.setattr(module.time, "sleep", Mock())
    release.wait_for_rollout(Target.DEVELOPMENT, 60)
    assert cloud.call_count == 3


def test_timeout_never_changes_traffic_or_cancels_jobs(release, cloud, monkeypatch):
    cloud.return_value = {"targetId": "chat-dev", "state": "IN_PROGRESS"}
    monkeypatch.setattr(module.time, "monotonic", Mock(side_effect=[0, 0, 61]))
    monkeypatch.setattr(module.time, "sleep", Mock())
    with pytest.raises(TimeoutError, match="still owns rollout"):
        release.wait_for_rollout(Target.DEVELOPMENT, 60)
    assert cloud.call_count == 1
    assert cloud.call_args.args[0][1:3] == ["rollouts", "describe"]


@pytest.mark.parametrize("state", ["FAILED", "STATE_UNSPECIFIED"])
def test_render_failure_prevents_promotion(release, cloud, state):
    cloud.return_value = {"renderState": state}
    with pytest.raises(RuntimeError, match="rendering did not succeed"):
        release.wait_for_render()
    assert cloud.call_count == 1


def test_lost_lease_prevents_production_promotion(cloud, monkeypatch):
    lease = Mock(side_effect=[None, RuntimeError("ownership changed")])
    monkeypatch.setattr(module, "assert_lease", lease)
    with pytest.raises(RuntimeError, match="ownership changed"):
        module.main(
            [
                "promote",
                "--owner=build",
                "--project=project",
                "--region=region",
                "--release=release",
                "--target=chat-prod",
            ]
        )
    cloud.assert_not_called()


def test_render_waits_for_all_targets_to_succeed(release, cloud, monkeypatch):
    cloud.side_effect = [{"renderState": "IN_PROGRESS"}, {"renderState": "SUCCEEDED"}]
    monkeypatch.setattr(module.time, "sleep", Mock())
    release.wait_for_render()
    assert cloud.call_count == 2
    assert all(
        call.args[0][1:3] == ["releases", "describe"] for call in cloud.call_args_list
    )


def test_render_timeout_does_not_create_a_rollout(release, cloud, monkeypatch):
    cloud.return_value = {"renderState": "IN_PROGRESS"}
    monkeypatch.setattr(module.time, "monotonic", Mock(side_effect=[0, 0, 901]))
    monkeypatch.setattr(module.time, "sleep", Mock())
    with pytest.raises(TimeoutError, match="rendering did not finish"):
        release.wait_for_render()
    assert cloud.call_count == 1
    assert cloud.call_args.args[0][1:3] == ["releases", "describe"]


@pytest.mark.parametrize(
    "image", ["registry.example:5000/team/api:commit", "registry.example:5000/team/api"]
)
def test_digest_resolution_preserves_registry_port(image, monkeypatch):
    monkeypatch.setattr(
        module.subprocess,
        "check_output",
        Mock(return_value='{"image_summary":{"digest":"sha256:' + "a" * 64 + '"}}'),
    )
    assert (
        module.digest(image, "project")
        == "registry.example:5000/team/api@sha256:" + "a" * 64
    )


def test_pinned_image_needs_no_registry_lookup(monkeypatch):
    command = Mock()
    monkeypatch.setattr(module.subprocess, "check_output", command)
    image = "registry.example/team/api@sha256:" + "a" * 64
    assert module.digest(image, "project") == image
    command.assert_not_called()
