import json
from statistics import median
from textwrap import dedent
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from structlog import get_logger

import app.api.works
from app.config import get_settings
from app.models.labelset import LabelOrigin
from app.models.work import Work
from app.repositories.service_account_repository import service_account_repository
from app.schemas.labelling import LabelledWorkData, LabellingResult, LLMUsage
from app.schemas.labelset import LabelSetCreateIn
from app.schemas.work import WorkUpdateIn
from app.services.labelling.prompt import (
    retry_prompt_template,
    suffix,
    system_prompt,
    user_prompt_template,
)
from app.services.labelling.providers import get_provider

logger = get_logger()


def label_work(work: Work, prompt: str | None = None, retries: int = 2):
    target_prompt = prompt or system_prompt
    all_usages = []

    logger.debug("Requesting LLM completion for labelling", work_id=work.id)
    user_content = prepare_context_for_labelling(work)

    logger.info("Prompt: ", prompt=user_content)

    provider = get_provider()
    response = provider.query(target_prompt, user_content)
    all_usages.append(response.usage)

    json_data = None
    parsed_data = None
    while True:
        try:
            json_data = json.loads(response.output)
            parsed_data = LabelledWorkData(**json_data)
            break
        except (ValidationError, ValueError) as e:
            retries -= 1
            error_string = str(e)

            logger.warning(
                "LLM response was not valid",
                output=response.output,
                error=error_string,
                retrying=retries > 0,
                work_id=work.id,
            )

            ai_response = {"role": "assistant", "content": response.output}
            validation_response = {
                "role": "user",
                "content": retry_prompt_template.format(
                    user_content=user_content,
                    error_message=error_string,
                ),
            }
            response = provider.query(
                target_prompt,
                user_content,
                extra_messages=[ai_response, validation_response],
            )
            all_usages.append(response.usage)

        if retries <= 0:
            break

    if not json_data:
        raise ValueError("LLM response was not valid JSON after exhausting retries")
    elif not parsed_data:
        raise ValueError("LLM response was not valid after exhausting retries")

    usage = LLMUsage(usages=all_usages)
    logger.info("LLM response was valid", work_id=work.id, usage=usage)

    logger.debug("LLM response", response=response.output)

    return LabellingResult(
        system_prompt=system_prompt,
        user_content=user_content,
        output=parsed_data,
        usage=LLMUsage(usages=all_usages),
    )


# Backwards-compatible alias
label_with_gpt = label_work


def prepare_context_for_labelling(work, extra: str | None = None):
    # TODO: Get a better list of related editions. E.g levenstein distance to title, largest info blobs or biggest delta in info blob content etc
    editions = [
        ed
        for ed in work.editions[:20]
        if ed.info is not None and ed.title == work.title
    ]
    editions.sort(key=lambda e: len(e.info), reverse=True)
    if not editions:
        logger.warning("Insufficient edition data to generate good labels")
        main_edition = work.editions[0]
        if main_edition.info is None:
            main_edition.info = {}
    else:
        main_edition = editions[0]
    huey_summary = (
        work.labelset.huey_summary
        if work.labelset and work.labelset.huey_summary
        else ""
    )
    genre_data_set: set[str] = set()
    short_summary_set: set[str | None] = set()
    page_numbers: set[int] = set()
    for e in editions[:20]:
        if e.info is not None:
            for g in e.info.get("genres", []):
                genre_data_set.add(f"{g['name']}")

            short_summary_set.add(e.info.get("summary_short"))

            if pages := e.info.get("pages"):
                page_numbers.add(pages)
    genre_data = "\n".join(genre_data_set)
    median_page_number = median(page_numbers) if page_numbers else "unknown"
    short_summaries = "\n".join(f"- {s}" for s in short_summary_set if s is not None)
    display_title = work.get_display_title()
    authors_string = work.get_authors_string()
    long_summary = main_edition.info.get("summary_long", "") or ""
    keywords = main_edition.info.get("keywords", "") or ""
    other_info = dedent(
        f"""
    - {main_edition.info.get("cbmctext")}
    - {main_edition.info.get("prodct")}
    """
    )
    extra = extra or ""
    user_provided_values = {
        "display_title": display_title,
        "authors_string": authors_string,
        "huey_summary": huey_summary[:1500],
        "short_summaries": short_summaries[:1500],
        "long_summary": long_summary[:1500],
        "keywords": keywords[:1500],
        "other_info": other_info,
        "number_of_pages": median_page_number,
        "genre_data": genre_data[:1500],
        "extra": extra[:5000],
    }
    user_content = user_prompt_template.format(**user_provided_values) + suffix
    return user_content


def work_to_labelset_update(work: Work):
    label_data = label_work(work, retries=2)
    return create_labelset_from_ml_labelled_work(label_data.output)


def create_labelset_from_ml_labelled_work(
    labelled_work: LabelledWorkData,
) -> LabelSetCreateIn:
    hues = (
        [
            k
            for k, v in sorted(
                labelled_work.hue_map.items(), key=lambda item: -item[1]
            )[:3]
            if v > 0.1
        ]
        if len(labelled_work.hue_map) > 1
        else labelled_work.hues
    )

    labelset_info: dict[str, Any] = {
        "long_summary": labelled_work.long_summary,
        "genres": labelled_work.genres,
        "styles": labelled_work.styles,
        "characters": labelled_work.characters,
        "hue_map": labelled_work.hue_map,
        "series": labelled_work.series,
        "series_number": labelled_work.series_number,
        "gender": labelled_work.gender,
        "awards": labelled_work.awards,
        "notes": labelled_work.notes,
        "controversial_themes": labelled_work.controversial_themes,
    }

    labelset_data: dict[str, Any] = {
        "reading_ability_keys": labelled_work.reading_ability,
        "reading_ability_origin": LabelOrigin.VERTEXAI,
        "hue_origin": LabelOrigin.VERTEXAI,
        "age_origin": LabelOrigin.VERTEXAI,
        "min_age": labelled_work.min_age,
        "max_age": labelled_work.max_age,
        "huey_summary": labelled_work.short_summary,
        "summary_origin": LabelOrigin.VERTEXAI,
        "info": labelset_info,
        "checked": True,
        "recommend_status": labelled_work.recommend_status,
        "recommend_status_origin": LabelOrigin.VERTEXAI,
    }

    if len(hues) > 0:
        labelset_data["hue_primary_key"] = hues[0]
    if len(hues) > 1:
        labelset_data["hue_secondary_key"] = hues[1]
    if len(hues) > 2:
        labelset_data["hue_tertiary_key"] = hues[2]

    return LabelSetCreateIn(**labelset_data)


async def label_and_update_work(work: Work, session):
    settings = get_settings()
    labelset_update = work_to_labelset_update(work)
    changes = WorkUpdateIn(labelset=labelset_update)

    service_account = service_account_repository.get_or_404(
        db=session, id=UUID(settings.GPT_SERVICE_ACCOUNT_ID)
    )

    await app.api.works.update_work(
        changes=changes, work_orm=work, account=service_account, session=session
    )
    logger.info(f"Updated labelset for {work.title}")
