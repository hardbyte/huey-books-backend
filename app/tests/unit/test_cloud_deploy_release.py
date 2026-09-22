import copy
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "prepare_release", Path(__file__).parents[3] / "deploy/prepare_release.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def service(name="example-api"):
    return {
        "apiVersion": "serving.knative.dev/v1",
        "kind": "Service",
        "metadata": {
            "name": name,
            "resourceVersion": "123",
            "annotations": {"run.googleapis.com/ingress": "all"},
        },
        "status": {"url": "https://example-api-hash-ts.a.run.app", "traffic": []},
        "spec": {
            "traffic": [{"latestRevision": True, "percent": 100}],
            "template": {
                "metadata": {
                    "name": "previous",
                    "annotations": {
                        "run.googleapis.com/cloudsql-instances": "test-instance"
                    },
                },
                "spec": {
                    "serviceAccountName": "runtime@example.invalid",
                    "containers": [
                        {
                            "image": "previous-image",
                            "ports": [{"containerPort": 8080}],
                            "resources": {"limits": {"memory": "4Gi"}},
                            "env": [
                                {
                                    "name": "SECRET_KEY",
                                    "valueFrom": {
                                        "secretKeyRef": {"name": "secret", "key": "1"}
                                    },
                                },
                                {
                                    "name": "WRIVETED_INTERNAL_API",
                                    "value": "https://old.example.invalid",
                                },
                            ],
                        }
                    ],
                },
            },
        },
    }


def test_release_freezes_runtime_configuration_without_owning_traffic():
    source = service()
    original = copy.deepcopy(source)
    rendered = module.prepare_service(
        source, "new@sha256:abc", "release-one", "https://pinned.example.invalid"
    )
    assert source == original
    assert "status" not in rendered and "traffic" not in rendered["spec"]
    template = rendered["spec"]["template"]
    assert template["spec"]["serviceAccountName"] == "runtime@example.invalid"
    container = template["spec"]["containers"][0]
    assert container["image"] == "new@sha256:abc"
    assert (
        container["env"][0]
        == original["spec"]["template"]["spec"]["containers"][0]["env"][0]
    )
    assert container["env"][1]["value"] == "https://pinned.example.invalid"
    assert container["resources"] == {"limits": {"memory": "4Gi"}}
    assert template["metadata"]["labels"]["huey-release"] == "release-one"


def test_template_preserves_custom_audience_and_scaling_configuration():
    source = service()
    source["metadata"]["annotations"].update(
        {
            "run.googleapis.com/custom-audiences": '["https://api.example.invalid"]',
            "run.googleapis.com/maxScale": "10",
            "run.googleapis.com/operation-id": "read-only",
        }
    )
    rendered = module.prepare_service(
        source, "new@sha256:abc", "release-one", "https://pinned.example.invalid"
    )
    assert (
        rendered["metadata"]["annotations"]["run.googleapis.com/custom-audiences"]
        == '["https://api.example.invalid"]'
    )
    assert rendered["metadata"]["annotations"]["run.googleapis.com/maxScale"] == "10"
    assert "run.googleapis.com/operation-id" not in rendered["metadata"]["annotations"]
