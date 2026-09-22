"""Submit and observe native Cloud Deploy rollouts without changing Cloud Run traffic."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from lease import assert_lease

TERMINAL_FAILURES = {"FAILED", "CANCELLED", "HALTED", "APPROVAL_REJECTED"}


def gcloud(arguments: list[str], project: str, region: str) -> dict | list:
    return json.loads(
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
        )
    )


def digest(image: str, project: str) -> str:
    if "@sha256:" in image:
        return image
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
        )
    )
    return image.rsplit(":", 1)[0] + "@" + result["image_summary"]["digest"]


def rollout_state(rollouts: list[dict], target: str) -> str | None:
    controllers = [rollout for rollout in rollouts if rollout["targetId"] == target]
    if len(controllers) > 1:
        raise RuntimeError(
            f"Multiple rollouts for {target}; inspect Cloud Deploy before continuing"
        )
    return controllers[0]["state"] if controllers else None


def main() -> None:
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
        "--target", choices=["chat-dev", "chat-prod"], default="chat-dev"
    )
    parser.add_argument("--image")
    parser.add_argument("--verifier-image")
    parser.add_argument("--ui-url", default="https://hueybooks.com")
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()
    assert_lease(args.project, args.owner, args.lease_file)
    common = [f"--delivery-pipeline={args.pipeline}"]
    if args.action == "create":
        if not args.image or not args.verifier_image:
            parser.error("create requires both images")
        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("prepare_release.py")),
                f"--project={args.project}",
                f"--region={args.region}",
                f"--release={args.release}",
                f"--image={digest(args.image, args.project)}",
                f"--verifier-image={digest(args.verifier_image, args.project)}",
                f"--ui-url={args.ui_url}",
            ],
            check=True,
        )
        gcloud(
            [
                "deploy",
                "releases",
                "create",
                args.release,
                *common,
                "--source=clouddeploy-release",
                "--disable-initial-rollout",
                f"--gcs-source-staging-dir=gs://{args.project}-chat-deploy-artifacts/source",
            ],
            args.project,
            args.region,
        )
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            release = gcloud(
                ["deploy", "releases", "describe", args.release, *common],
                args.project,
                args.region,
            )
            if release["renderState"] == "SUCCEEDED":
                break
            if release["renderState"] == "FAILED":
                raise RuntimeError("Native release rendering failed")
            time.sleep(10)
        else:
            raise TimeoutError("Native release rendering did not finish")
    if args.action in {"create", "promote"}:
        assert_lease(args.project, args.owner, args.lease_file)
        if args.target == "chat-prod":
            rollouts = gcloud(
                ["deploy", "rollouts", "list", f"--release={args.release}", *common],
                args.project,
                args.region,
            )
            if rollout_state(rollouts, "chat-dev") != "SUCCEEDED":
                raise RuntimeError(
                    "Production requires a successfully verified development rollout"
                )
        gcloud(
            [
                "deploy",
                "releases",
                "promote",
                f"--release={args.release}",
                f"--to-target={args.target}",
                f"--rollout-id={args.target}",
                *common,
            ],
            args.project,
            args.region,
        )
    deadline = time.monotonic() + args.timeout
    previous = None
    while time.monotonic() < deadline:
        rollouts = gcloud(
            ["deploy", "rollouts", "list", f"--release={args.release}", *common],
            args.project,
            args.region,
        )
        state = rollout_state(rollouts, args.target)
        if state != previous:
            print(f"{args.release}/{args.target}: {state}", flush=True)
            previous = state
        if state == "SUCCEEDED":
            return
        if state in TERMINAL_FAILURES:
            raise RuntimeError(
                f"Rollout {state}; inspect native repair automation. Do not cancel or override its jobs."
            )
        time.sleep(15)
    raise TimeoutError(
        "Observation timed out; Cloud Deploy still owns rollout and repair"
    )


if __name__ == "__main__":
    main()
