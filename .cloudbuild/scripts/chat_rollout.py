"""Capture traffic and restore it when the real-browser release gate fails."""

import argparse
import json
import subprocess
import time
import urllib.request
from pathlib import Path


def traffic_targets(service: dict) -> dict[str, int]:
    targets = {}
    for entry in service["status"]["traffic"]:
        percent = entry.get("percent", 0)
        if percent:
            revision = entry.get("revisionName")
            if not revision:
                raise ValueError("Traffic must resolve to an explicit revision")
            targets[revision] = targets.get(revision, 0) + percent
    if sum(targets.values()) != 100:
        raise ValueError("Expected a complete serving traffic allocation")
    return targets


class Rollout:
    def __init__(self, project: str, region: str, commit: str):
        self.commit = commit
        self.project = project
        self.region = region
        self.flags = ["--project", project, "--region", region, "--quiet"]

    def describe(self, name: str) -> dict:
        return json.loads(
            subprocess.check_output(
                [
                    "gcloud",
                    "run",
                    "services",
                    "describe",
                    name,
                    *self.flags,
                    "--format=json",
                ],
                text=True,
            )
        )

    def capture(self, services: list[str]) -> dict:
        return {name: traffic_targets(self.describe(name)) for name in services}

    def candidate(self, services: list[str], commit: str) -> dict:
        result = {}
        for name in services:
            service = self.describe(name)
            if (
                service.get("metadata", {}).get("labels", {}).get("commit-sha")
                != commit
            ):
                raise RuntimeError(
                    f"{name}: candidate commit no longer owns the service"
                )
            targets = traffic_targets(service)
            if targets != {service["status"]["latestReadyRevisionName"]: 100}:
                raise RuntimeError(f"{name}: candidate is not serving all traffic")
            result[name] = targets
        return result

    def api(self, path: str, payload: dict | None = None) -> dict:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-access-token"], text=True
        ).strip()
        request = urllib.request.Request(
            "https://run.googleapis.com/v2/" + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            method="PATCH" if payload is not None else "GET",
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    def resource(self, name: str) -> str:
        return f"projects/{self.project}/locations/{self.region}/services/{name}"

    def read_owned_service(self, name: str, expected: dict) -> dict:
        current = self.api(self.resource(name))
        serving = {
            row["revision"].rsplit("/", 1)[-1]: row["percent"]
            for row in current.get("trafficStatuses", [])
            if row.get("percent")
        }
        if (
            serving != expected
            or not current.get("etag")
            or current.get("reconciling", False)
            or current.get("labels", {}).get("commit-sha") != self.commit
        ):
            raise RuntimeError("Traffic ownership changed; refusing stale rollback")
        return current

    def restore_service(self, name: str, targets: dict, expected: dict) -> None:
        current = self.read_owned_service(name, expected)
        resource = self.resource(name)
        operation = self.api(
            resource + "?updateMask=traffic",
            {
                "name": resource,
                "etag": current["etag"],
                "traffic": [
                    {
                        "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                        "revision": revision,
                        "percent": percent,
                    }
                    for revision, percent in targets.items()
                ],
            },
        )
        deadline = time.monotonic() + 120
        while not operation.get("done"):
            if time.monotonic() >= deadline:
                raise RuntimeError("Timed out waiting for traffic restoration")
            time.sleep(1)
            operation = self.api(operation["name"])
        if operation.get("error"):
            raise RuntimeError(
                "Traffic restoration operation failed: " + str(operation["error"])
            )

    def restore(self, previous: dict, candidate: dict) -> None:
        # Check the whole pair before changing either service.
        for name, targets in candidate.items():
            self.read_owned_service(name, targets)
        failures = []
        for name, targets in previous.items():
            try:
                self.restore_service(name, targets, candidate[name])
                if traffic_targets(self.describe(name)) != targets:
                    raise RuntimeError("Traffic restoration readback did not match")
            except (subprocess.CalledProcessError, RuntimeError, OSError) as error:
                failures.append(f"{name}: {error}")
        if failures:
            raise RuntimeError("; ".join(failures))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=["capture", "baseline", "deploy", "candidate", "finish"]
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--target-service")
    parser.add_argument("--directory", type=Path, default=Path("/workspace"))
    args, deployment_args = parser.parse_known_args()
    rollout = Rollout(args.project, args.region, args.commit)
    previous_file = args.directory / "chat-previous-traffic.json"
    candidate_file = args.directory / "chat-candidate-traffic.json"
    passed = args.directory / "chat-gate-passed"
    services = [args.service, args.service + "-internal"]
    deploy_markers = [
        args.directory / f"chat-deploy-{name}-passed" for name in services
    ]
    if args.action == "capture":
        for marker in [passed, candidate_file, *deploy_markers]:
            marker.unlink(missing_ok=True)
        previous_file.write_text(json.dumps(rollout.capture(services)))
    elif args.action == "baseline":
        if rollout.capture(services) != json.loads(previous_file.read_text()):
            raise RuntimeError("Traffic changed during baseline UI validation")
    elif args.action == "deploy":
        if (
            args.target_service not in services
            or not deployment_args
            or deployment_args[0] != "--"
        ):
            parser.error(
                "deploy requires a known --target-service and -- followed by gcloud arguments"
            )
        result = subprocess.run(["gcloud", *deployment_args[1:]], check=False)
        if result.returncode:
            raise SystemExit(result.returncode)
        state = rollout.describe(args.target_service)
        if state.get("metadata", {}).get("labels", {}).get("commit-sha") != args.commit:
            raise RuntimeError("Another release owns this service; refusing promotion")
        target = {state["status"]["latestReadyRevisionName"]: 100}
        rollout.restore_service(args.target_service, target, traffic_targets(state))
        if traffic_targets(rollout.describe(args.target_service)) != target:
            raise RuntimeError("Candidate promotion readback did not match")
        (args.directory / f"chat-deploy-{args.target_service}-passed").touch()
    elif args.action == "candidate":
        if not all(marker.exists() for marker in deploy_markers):
            raise RuntimeError(
                "Both deployment steps must succeed before UI verification"
            )
        candidate_file.write_text(json.dumps(rollout.candidate(services, args.commit)))
    elif passed.exists():
        rollout.candidate(services, args.commit)
        print("Real UI reader journeys passed for the serving candidate")
    else:
        previous = json.loads(previous_file.read_text())
        if candidate_file.exists():
            candidate = json.loads(candidate_file.read_text())
            rollout.restore(previous, candidate)
        else:
            # A deployment can fail after moving traffic on only one service.
            changed = {}
            for name, targets in previous.items():
                state = rollout.describe(name)
                serving = traffic_targets(state)
                if serving != targets:
                    if (
                        state.get("metadata", {}).get("labels", {}).get("commit-sha")
                        != args.commit
                    ):
                        raise RuntimeError(
                            "A newer release owns traffic; refusing rollback"
                        )
                    changed[name] = serving
            rollout.restore({name: previous[name] for name in changed}, changed)
        raise SystemExit(
            "Real UI reader journeys failed; previous traffic restored. Database schema was retained."
        )


if __name__ == "__main__":
    main()
