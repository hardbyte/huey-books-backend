import copy

import pytest

from deploy import prepare_release as module


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
        source,
        "new@sha256:" + "a" * 64,
        "release-one",
        "https://pinned.example.invalid",
    )
    assert source == original
    assert "status" not in rendered and "traffic" not in rendered["spec"]
    template = rendered["spec"]["template"]
    assert template["spec"]["serviceAccountName"] == "runtime@example.invalid"
    container = template["spec"]["containers"][0]
    assert container["image"] == "new@sha256:" + "a" * 64
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
        source,
        "new@sha256:" + "a" * 64,
        "release-one",
        "https://pinned.example.invalid",
    )
    assert (
        rendered["metadata"]["annotations"]["run.googleapis.com/custom-audiences"]
        == '["https://api.example.invalid"]'
    )
    assert rendered["metadata"]["annotations"]["run.googleapis.com/maxScale"] == "10"
    assert "run.googleapis.com/operation-id" not in rendered["metadata"]["annotations"]


@pytest.mark.parametrize(
    "environment",
    [
        [],
        [
            {
                "name": "WRIVETED_INTERNAL_API",
                "valueFrom": {"secretKeyRef": {"name": "old", "key": "1"}},
            }
        ],
    ],
)
def test_internal_endpoint_is_explicit_even_if_missing_or_previously_secret(
    environment,
):
    source = service()
    source["spec"]["template"]["spec"]["containers"][0]["env"] = environment
    rendered = module.prepare_service(
        source,
        "new@sha256:" + "a" * 64,
        "release-one",
        "https://internal.example.invalid",
    )
    assert rendered["spec"]["template"]["spec"]["containers"][0]["env"] == [
        {"name": "WRIVETED_INTERNAL_API", "value": "https://internal.example.invalid"}
    ]


@pytest.mark.parametrize(
    "image",
    [
        "image:latest",
        "image@sha256:abc",
        "image@sha256:" + "g" * 64,
        " image@sha256:" + "a" * 64,
    ],
)
def test_release_rejects_mutable_or_invalid_images(image):
    with pytest.raises(ValueError, match="complete SHA-256"):
        module.prepare_service(
            service(), image, "release-one", "https://internal.example.invalid"
        )
