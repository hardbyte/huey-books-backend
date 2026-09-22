"""Freeze current runtime configuration into a private Cloud Deploy release bundle."""

import argparse
import copy
import hashlib
import json
import subprocess
from pathlib import Path


def read_service(project: str, region: str, service: str) -> dict:
    return json.loads(
        subprocess.check_output(
            [
                "gcloud",
                "run",
                "services",
                "describe",
                service,
                f"--project={project}",
                f"--region={region}",
                "--format=json",
            ],
            text=True,
        )
    )


def prepare_service(source: dict, image: str, release: str, internal_url: str) -> dict:
    manifest = copy.deepcopy(source)
    manifest.pop("status", None)
    metadata = manifest["metadata"]
    manifest["metadata"] = {
        "name": metadata["name"],
        "annotations": {
            key: value
            for key, value in metadata.get("annotations", {}).items()
            if key
            not in {
                "run.googleapis.com/client-name",
                "run.googleapis.com/client-version",
                "run.googleapis.com/ingress-status",
                "run.googleapis.com/operation-id",
                "run.googleapis.com/urls",
                "serving.knative.dev/creator",
                "serving.knative.dev/lastModifier",
            }
        },
    }
    template = manifest["spec"]["template"]
    revision = (
        f"{metadata['name'][:44]}-{hashlib.sha256(release.encode()).hexdigest()[:16]}"
    )
    template["metadata"]["name"] = revision
    template["metadata"]["labels"] = {"huey-release": release}
    template["metadata"]["annotations"] = {
        key: value
        for key, value in template["metadata"].get("annotations", {}).items()
        if key
        not in {
            "run.googleapis.com/client-name",
            "run.googleapis.com/client-version",
            "client.knative.dev/user-image",
        }
    }
    container = template["spec"]["containers"][0]
    container["image"] = image
    for entry in container.get("env", []):
        if entry["name"] == "WRIVETED_INTERNAL_API":
            entry["value"] = internal_url
    manifest["spec"].pop("traffic", None)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("project", "region", "release", "image", "verifier-image", "ui-url"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--public-service", default="wriveted-api")
    parser.add_argument(
        "--development-service", default="wriveted-api-development-main-branch"
    )
    parser.add_argument(
        "--development-internal-service", default="wriveted-api-dev-internal"
    )
    parser.add_argument("--output", type=Path, default=Path("clouddeploy-release"))
    args = parser.parse_args()
    if "@sha256:" not in args.image or "@sha256:" not in args.verifier_image:
        parser.error("Both images must be pinned by digest")
    sources_by_environment = {
        environment: {
            role: read_service(args.project, args.region, service)
            for role, service in [
                ("public", public),
                (
                    "internal",
                    args.development_internal_service
                    if environment == "dev"
                    else public + "-internal",
                ),
            ]
        }
        for environment, public in [
            ("dev", args.development_service),
            ("prod", args.public_service),
        ]
    }
    tag = "can"
    chart = args.output / "chart"
    (chart / "templates").mkdir(parents=True, exist_ok=True)
    (chart / "services").mkdir(exist_ok=True)
    (chart / "Chart.yaml").write_text(
        "apiVersion: v2\nname: chat-service\nversion: 0.1.0\n"
    )
    (chart / "values.yaml").write_text("target: unset\n")
    (chart / "templates/service.yaml").write_text(
        '{{ .Files.Get (printf "services/%s.json" .Values.target) | required "Unknown deployment target" }}\n'
    )
    expected = {}
    for environment, sources in sources_by_environment.items():
        internal_url = sources["internal"]["status"]["url"]
        for role, source in sources.items():
            target = f"chat-{environment}-{role}"
            manifest = prepare_service(source, args.image, args.release, internal_url)
            (chart / f"services/{target}.json").write_text(json.dumps(manifest))
            expected[target] = {
                "service": source["metadata"]["name"],
                "revision": manifest["spec"]["template"]["metadata"]["name"],
                "image": args.image,
                "tag": tag,
                "url": source["status"]["url"].replace("https://", f"https://{tag}---"),
                "internalUrl": internal_url,
            }
    skaffold = {
        "apiVersion": "skaffold/v4beta7",
        "kind": "Config",
        "metadata": {"name": "chat"},
        "manifests": {"helm": {"releases": [{"name": "chat", "chartPath": "chart"}]}},
        "deploy": {"cloudrun": {}},
        "verify": [
            {
                "name": "real-reader-journeys",
                "container": {
                    "name": "real-reader-journeys",
                    "image": args.verifier_image,
                    "command": ["node", "/verify/verify.mjs"],
                    "env": [
                        {"name": "RELEASE_SPEC", "value": json.dumps(expected)},
                        {"name": "EXPECTED_RELEASE", "value": args.release},
                        {"name": "E2E_UI_URL", "value": args.ui_url},
                        {"name": "DEPLOY_PROJECT", "value": args.project},
                        {"name": "DEPLOY_REGION", "value": args.region},
                    ],
                },
            }
        ],
    }
    (args.output / "skaffold.yaml").write_text(json.dumps(skaffold, indent=2))
    (args.output / ".gcloudignore").write_text(".git\n")
    print(
        f"Prepared immutable release {args.release} with {len(expected)} service definitions"
    )


if __name__ == "__main__":
    main()
