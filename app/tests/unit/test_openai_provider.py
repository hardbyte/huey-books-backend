"""Unit tests for the OpenAI labelling provider.

Locks the interface we depend on from the OpenAI SDK's Responses API — the
client's ``responses.create`` call arguments and the response fields
(``usage.input_tokens``/``output_tokens`` and ``output_text``) — without making a
real API call, so an SDK upgrade that changes that contract fails here.
"""

from types import SimpleNamespace
from unittest.mock import patch


def test_openai_provider_query_maps_response():
    fake_response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=12, output_tokens=8),
        output_text="  hue05_funny_comic  ",
    )

    with patch("openai.OpenAI") as OpenAICls:
        client = OpenAICls.return_value
        client.responses.create.return_value = fake_response

        from app.services.labelling.providers import OpenAIProvider

        provider = OpenAIProvider()
        result = provider.query(
            system_prompt="You are a labeller.",
            user_content="Label this book.",
            extra_messages=[{"role": "user", "content": "extra"}],
        )

    # Response fields are mapped onto our schema.
    assert result.output == "hue05_funny_comic"  # stripped
    assert result.usage.prompt_tokens == 12
    assert result.usage.completion_tokens == 8
    assert result.usage.total_tokens == 20

    # The Responses API is called with the arguments we rely on.
    kwargs = client.responses.create.call_args.kwargs
    assert kwargs["instructions"] == "You are a labeller."
    assert kwargs["temperature"] == 0
    assert {"role": "user", "content": "Label this book."} in kwargs["input"]
    assert {"role": "user", "content": "extra"} in kwargs["input"]
    assert "model" in kwargs and "timeout" in kwargs


def test_openai_provider_forwards_api_key():
    with patch("openai.OpenAI") as OpenAICls:
        from app.services.labelling.providers import OpenAIProvider

        OpenAIProvider()
        # Client constructed with a keyword api_key (SDK v1/v2 contract).
        assert "api_key" in OpenAICls.call_args.kwargs
