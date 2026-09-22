"""Submit and observe native Cloud Deploy rollouts without changing Cloud Run traffic."""

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from deploy.lease import assert_lease
from deploy.prepare_release import pinned_image


class Target(StrEnum):
    DEVELOPMENT = "chat-dev"
    PRODUCTION = "chat-prod"


class RolloutState(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    IN_PROGRESS = "IN_PROGRESS"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    PENDING = "PENDING"
    PENDING_RELEASE = "PENDING_RELEASE"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    HALTED = "HALTED"


TERMINAL_FAILURES = {
    RolloutState.FAILED,
    RolloutState.CANCELLED,
    RolloutState.HALTED,
    RolloutState.APPROVAL_REJECTED,
}


def gcloud(arguments: list[str], project: str, region: str) -> dict[str, Any]:
    result = json.loads(
        subprocess.check_output(
            [
                "gcloud",
                *arguments,
                f"--project={project}",
                f"--region={region}",
                "--format=json",
                "--quiet",
            ],
            text=True,
            timeout=120,
        )
    )
    if not isinstance(result, dict):
        raise ValueError("Expected a Cloud Deploy resource object")
    return result


def digest(image: str, project: str) -> str:
    if "@sha256:" in image:
        return pinned_image(image)
    result = json.loads(
        subprocess.check_output(
            [
                "gcloud",
                "artifacts",
                "docker",
                "images",
                "describe",
                image,
                f"--project={project}",
                "--format=json",
            ],
            text=True,
            timeout=120,
        )
    )
    repository, separator, name = image.rpartition("/")
    untagged = name.split(":", 1)[0]
    return pinned_image(
        repository + separator + untagged + "@" + result["image_summary"]["digest"]
    )


@dataclass(frozen=True)
class Release:
    project: str
    region: str
    name: str
    pipeline: str = "chat"

    def command(self, arguments: list[str]) -> dict[str, Any]:
        return gcloud(
            ["deploy", *arguments, f"--delivery-pipeline={self.pipeline}"],
            self.project,
            self.region,
        )

    def rollout_state(self, target: Target) -> RolloutState:
        rollout = self.command(
            ["rollouts", "describe", target, f"--release={self.name}"]
        )
        if rollout.get("targetId") != target or rollout.get("controllerRollout"):
            raise ValueError("Expected the environment controller rollout")
        # Rollback creates other controllers for the old release. Observe only
        # the rollout ID submitted by this build, never its recovery rollout.
        return RolloutState(rollout["state"])

    def wait_for_render(self, timeout: float = 900) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            release = self.command(["releases", "describe", self.name])
            state = release["renderState"]
            if state == "SUCCEEDED":
                return
            if state != "IN_PROGRESS":
                raise RuntimeError(f"Native release rendering did not succeed: {state}")
            time.sleep(10)
        raise TimeoutError("Native release rendering did not finish")

    def promote(self, target: Target) -> None:
        if (
            target == Target.PRODUCTION
            and self.rollout_state(Target.DEVELOPMENT) != RolloutState.SUCCEEDED
        ):
            raise RuntimeError(
                "Production requires a successfully verified development rollout"
            )
        self.command(
            [
                "releases",
                "promote",
                f"--release={self.name}",
                f"--to-target={target}",
                f"--rollout-id={target}",
            ]
        )

    def wait_for_rollout(self, target: Target, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        previous = None
        while time.monotonic() < deadline:
            state = self.rollout_state(target)
            if state != previous:
                print(f"{self.name}/{target}: {state}", flush=True)
                previous = state
            if state == RolloutState.SUCCEEDED:
                return
            if state in TERMINAL_FAILURES:
                raise RuntimeError(
                    f"Rollout {state}; inspect native repair automation. Do not cancel or override its jobs."
                )
            time.sleep(15)
        raise TimeoutError(
            "Observation timed out; Cloud Deploy still owns rollout and repair"
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["create", "wait", "promote"])
    parser.add_argument("--owner", required=True)
    parser.add_argument(
        "--lease-file", type=Path, default=Path("/workspace/chat-deploy-lease.json")
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--pipeline", default="chat")
    parser.add_argument(
        "--target", type=Target, choices=list(Target), default=Target.DEVELOPMENT
    )
    parser.add_argument("--image")
    parser.add_argument("--verifier-image")
    parser.add_argument("--ui-url", default="https://hueybooks.com")
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args(argv)
    assert_lease(args.project, args.owner, args.lease_file)
    release = Release(args.project, args.region, args.release, args.pipeline)
    if args.action == "create":
        if not args.image or not args.verifier_image:
            parser.error("create requires both images")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "deploy.prepare_release",
                f"--project={args.project}",
                f"--region={args.region}",
                f"--release={args.release}",
                f"--image={digest(args.image, args.project)}",
                f"--verifier-image={digest(args.verifier_image, args.project)}",
                f"--ui-url={args.ui_url}",
            ],
            check=True,
            timeout=600,
        )
        release.command(
            [
                "releases",
                "create",
                args.release,
                "--source=clouddeploy-release",
                "--disable-initial-rollout",
                f"--gcs-source-staging-dir=gs://{args.project}-chat-deploy-artifacts/source",
            ]
        )
        release.wait_for_render()
    if args.action in {"create", "promote"}:
        assert_lease(args.project, args.owner, args.lease_file)
        release.promote(args.target)
    release.wait_for_rollout(args.target, args.timeout)


if __name__ == "__main__":
    main()
