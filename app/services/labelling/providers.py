import time
from typing import Protocol

from structlog import get_logger

from app.config import get_settings
from app.schemas.labelling import LLMPromptUsage, LLMResponse

logger = get_logger()


class LLMProvider(Protocol):
    def query(
        self,
        system_prompt: str,
        user_content: str,
        extra_messages: list[dict] | None = None,
    ) -> LLMResponse: ...


class OpenAIProvider:
    def __init__(self):
        settings = get_settings()
        from openai import OpenAI

        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_MODEL
        self.timeout = settings.OPENAI_TIMEOUT

    def query(
        self,
        system_prompt: str,
        user_content: str,
        extra_messages: list[dict] | None = None,
    ) -> LLMResponse:
        input_messages = [{"role": "user", "content": user_content}]
        if extra_messages:
            input_messages.extend(extra_messages)

        logger.debug("Prompts prepared, sending to OpenAI...")

        start_time = time.time()
        response = self.client.responses.create(
            model=self.model,
            instructions=system_prompt,
            input=input_messages,
            temperature=0,
            timeout=self.timeout,
        )
        duration = time.time() - start_time

        logger.debug(f"OpenAI responded after {duration:.1f}s")

        usage = response.usage
        return LLMResponse(
            usage=LLMPromptUsage(
                prompt_tokens=usage.input_tokens,
                completion_tokens=usage.output_tokens,
                total_tokens=usage.input_tokens + usage.output_tokens,
                duration=duration,
            ),
            output=response.output_text.strip(),
        )


class GeminiProvider:
    def __init__(self):
        settings = get_settings()
        from google import genai

        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.model = settings.GEMINI_MODEL

    def query(
        self,
        system_prompt: str,
        user_content: str,
        extra_messages: list[dict] | None = None,
    ) -> LLMResponse:
        from google.genai import types

        contents = [{"role": "user", "parts": [{"text": user_content}]}]
        if extra_messages:
            for msg in extra_messages:
                role = "model" if msg["role"] == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": msg["content"]}]})

        logger.debug("Prompts prepared, sending to Gemini...")

        start_time = time.time()
        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0,
            ),
        )
        duration = time.time() - start_time

        logger.debug(f"Gemini responded after {duration:.1f}s")

        meta = response.usage_metadata
        return LLMResponse(
            usage=LLMPromptUsage(
                prompt_tokens=meta.prompt_token_count,
                completion_tokens=meta.candidates_token_count,
                total_tokens=meta.total_token_count,
                duration=duration,
            ),
            output=response.text.strip(),
        )


def get_provider() -> LLMProvider:
    settings = get_settings()
    provider_name = settings.LABELLING_PROVIDER.lower()
    if provider_name == "openai":
        return OpenAIProvider()
    elif provider_name == "gemini":
        return GeminiProvider()
    else:
        raise ValueError(
            f"Unknown labelling provider: {provider_name!r}. Use 'openai' or 'gemini'."
        )
