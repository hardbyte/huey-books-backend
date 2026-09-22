"""Serialize CI migrations and release submission with a conditional GCS object."""

import argparse
import json
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


def command(arguments: list[str]) -> str:
    return subprocess.check_output(
        ["gcloud", "storage", *arguments], text=True, timeout=120
    )


def lease_url(project: str) -> str:
    return f"gs://{project}-chat-deploy-artifacts/pipeline.lock"


@dataclass(frozen=True)
class Lease:
    project: str
    owner: str
    url: str
    generation: str

    @classmethod
    def read(cls, path: Path) -> "Lease":
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or any(
            not isinstance(data.get(key), str) or not data[key]
            for key in ("project", "owner", "url", "generation")
        ):
            raise ValueError("Invalid deployment lease receipt")
        if not data["generation"].isdigit():
            raise ValueError("Invalid deployment lease generation")
        return cls(data["project"], data["owner"], data["url"], data["generation"])


def verify_owner(lease: Lease) -> None:
    # Pin the read to the observed generation: never adopt a replacement lock.
    stored = json.loads(command(["cat", f"{lease.url}#{lease.generation}"]))
    if not isinstance(stored, dict) or stored.get("owner") != lease.owner:
        raise RuntimeError("Deployment lease belongs to another build")


def assert_lease(project: str, owner: str, path: Path) -> Lease:
    lease = Lease.read(path)
    if lease.owner != owner or lease.project != project:
        raise RuntimeError("Deployment lease belongs to another build")
    if lease.url != lease_url(project):
        raise ValueError("Deployment lease receipt references another object")
    current = json.loads(command(["objects", "describe", lease.url, "--format=json"]))
    if str(current["generation"]) != lease.generation:
        raise RuntimeError("Deployment lease ownership changed")
    verify_owner(lease)
    return lease


def acquire_lease(project: str, owner: str, path: Path) -> Lease:
    url = lease_url(project)
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "lease.json"
        source.write_text(
            json.dumps(
                {"owner": owner, "created": datetime.now(timezone.utc).isoformat()}
            )
        )
        command(["cp", str(source), url, "--if-generation-match=0"])
    metadata = json.loads(command(["objects", "describe", url, "--format=json"]))
    lease = Lease(project, owner, url, str(metadata["generation"]))
    verify_owner(lease)
    path.write_text(json.dumps(asdict(lease)))
    return lease


def release_lease(project: str, owner: str, path: Path) -> None:
    lease = assert_lease(project, owner, path)
    command(["rm", lease.url, f"--if-generation-match={lease.generation}"])
    path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["acquire", "release"])
    parser.add_argument("--project", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument(
        "--file", type=Path, default=Path("/workspace/chat-deploy-lease.json")
    )
    args = parser.parse_args()
    if args.action == "acquire":
        acquire_lease(args.project, args.owner, args.file)
    else:
        release_lease(args.project, args.owner, args.file)
    print(f"Deployment lease {args.action} succeeded for {args.owner}")


if __name__ == "__main__":
    main()
