"""Serialize CI migrations and release submission with a conditional GCS object."""

import argparse
import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def command(arguments: list[str]) -> str:
    return subprocess.check_output(["gcloud", "storage", *arguments], text=True)


def assert_lease(project: str, owner: str, path: Path) -> dict:
    lease = json.loads(path.read_text())
    if lease["owner"] != owner or lease["project"] != project:
        raise RuntimeError("Deployment lease belongs to another build")
    current = json.loads(
        command(["objects", "describe", lease["url"], "--format=json"])
    )
    if str(current["generation"]) != str(lease["generation"]):
        raise RuntimeError("Deployment lease ownership changed")
    return lease


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["acquire", "release"])
    parser.add_argument("--project", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument(
        "--file", type=Path, default=Path("/workspace/chat-deploy-lease.json")
    )
    args = parser.parse_args()
    url = f"gs://{args.project}-chat-deploy-artifacts/pipeline.lock"
    if args.action == "acquire":
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "lease.json"
            source.write_text(
                json.dumps(
                    {
                        "owner": args.owner,
                        "created": datetime.now(timezone.utc).isoformat(),
                    }
                )
            )
            command(["cp", str(source), url, "--if-generation-match=0"])
        metadata = json.loads(command(["objects", "describe", url, "--format=json"]))
        args.file.write_text(
            json.dumps(
                {
                    "owner": args.owner,
                    "project": args.project,
                    "url": url,
                    "generation": metadata["generation"],
                }
            )
        )
    else:
        lease = assert_lease(args.project, args.owner, args.file)
        command(["rm", url, f"--if-generation-match={lease['generation']}"])
        args.file.unlink()
    print(f"Deployment lease {args.action} succeeded for {args.owner}")


if __name__ == "__main__":
    main()
