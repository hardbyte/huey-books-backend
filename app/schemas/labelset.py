from datetime import datetime
from typing import Annotated, Optional
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, field_validator

from app.models.labelset import LabelOrigin, RecommendStatus
from app.schemas import CaseInsensitiveStringEnum
from app.schemas.ai_assistance import AIAssistance
from app.schemas.hue import Hue


class WritingStyleKey(CaseInsensitiveStringEnum):
    DARK = "DARK"
    LIGHT = "LIGHT"
    FUNNY = "FUNNY"
    SERIOUS = "SERIOUS"
    QUIRKY = "QUIRKY"
    ACTION_PACKED = "ACTION_PACKED"
    WHIMSICAL = "WHIMSICAL"
    BEAUTIFUL = "BEAUTIFUL"
    CHARMING = "CHARMING"
    GRIM = "GRIM"
    INSPIRING = "INSPIRING"
    CALM = "CALM"
    SUSPENSEFUL = "SUSPENSEFUL"
    SILLY = "SILLY"
    PLAYFUL = "PLAYFUL"
    REALISTIC = "REALISTIC"
    INFORMATIVE = "INFORMATIVE"


class ControversialThemeKey(CaseInsensitiveStringEnum):
    VIOLENT = "VIOLENT"
    SEXUAL = "SEXUAL"
    DRUGS = "DRUGS"
    RELIGIOUS = "RELIGIOUS"
    LGBTQI = "LGBTQI"
    PROFANITY = "PROFANITY"
    MENTAL_HEALTH = "MENTAL_HEALTH"
    OTHER = "OTHER"


class GenreKey(CaseInsensitiveStringEnum):
    FACTUAL_NON_FICTION = "FACTUAL_NON_FICTION"
    FUNNY = "FUNNY"
    ROMANCE = "ROMANCE"
    SCIENCE = "SCIENCE"
    SCIFI = "SCIFI"
    CLASSIC_FICTION = "CLASSIC_FICTION"
    HISTORICAL = "HISTORICAL"
    FANTASY = "FANTASY"
    FAIRYTALES = "FAIRYTALES"
    HORROR_SPOOKY = "HORROR_SPOOKY"
    MYSTERY_SUSPENSE = "MYSTERY_SUSPENSE"
    ADVENTURE_AND_ACTION = "ADVENTURE_AND_ACTION"
    CRIME = "CRIME"
    RHYMES_POETRY = "RHYMES_POETRY"
    BIOGRAPHICAL = "BIOGRAPHICAL"
    GRAPHIC_NOVELS = "GRAPHIC_NOVELS"
    WAR = "WAR"
    DYSTOPIAN = "DYSTOPIAN"
    AUSTRALIAN = "AUSTRALIAN"
    AMERICAN = "AMERICAN"
    BRITISH = "BRITISH"
    INDIGENOUS = "INDIGENOUS"
    SPORTS = "SPORTS"
    PICTURE_BOOK = "PICTURE_BOOK"
    YOUNG_ADULT = "YOUNG_ADULT"
    LGBTQ = "LGBTQ"


class CharacterKey(CaseInsensitiveStringEnum):
    BUGS = "BUGS"
    CATS_DOGS_AND_MICE = "CATS_DOGS_AND_MICE"
    HORSES_AND_FARM_ANIMALS = "HORSES_AND_FARM_ANIMALS"
    OCEAN_CREATURES = "OCEAN_CREATURES"
    WOLVES_AND_WILD_ANIMALS = "WOLVES_AND_WILD_ANIMALS"
    AUSTRALIAN_ANIMALS = "AUSTRALIAN_ANIMALS"
    BRITISH_ANIMALS = "BRITISH_ANIMALS"
    AMERICAN_ANIMALS = "AMERICAN_ANIMALS"
    DINOSAURS = "DINOSAURS"
    PRINCESSES_FAIRIES_MERMAIDS = "PRINCESSES_FAIRIES_MERMAIDS"
    UNICORNS = "UNICORNS"
    SUPERHEROES = "SUPERHEROES"
    FAMILIES_AND_FRIENDS = "FAMILIES_AND_FRIENDS"
    MONSTERS_GHOSTS_AND_VAMPIRES = "MONSTERS_GHOSTS_AND_VAMPIRES"
    ALIENS = "ALIENS"
    TRAINS_CARS_AND_TRUCKS = "TRAINS_CARS_AND_TRUCKS"
    MISFITS_AND_UNDERDOGS = "MISFITS_AND_UNDERDOGS"
    PIRATES = "PIRATES"
    ROBOTS = "ROBOTS"
    ATHLETES_AND_SPORT_STARS = "ATHLETES_AND_SPORT_STARS"
    WITCHES_WIZARDS_MAGIC = "WITCHES_WIZARDS_MAGIC"


class ReadingAbility(BaseModel):
    id: Annotated[str, BeforeValidator(str)]
    key: str
    name: str
    model_config = ConfigDict(from_attributes=True)


class LabelSetBrief(BaseModel):
    id: Annotated[str, BeforeValidator(str)]
    work_id: Annotated[str, BeforeValidator(str)]
    model_config = ConfigDict(from_attributes=True)


class LabelSetBasic(LabelSetBrief):
    hues: list[Hue]
    min_age: Optional[int] = None
    max_age: Optional[int] = None
    reading_abilities: list[ReadingAbility]
    huey_summary: Optional[str] = None


class LabelSetDetail(LabelSetBrief):
    hues: list[Hue]
    hue_origin: Optional[LabelOrigin] = None

    min_age: Optional[int] = None
    max_age: Optional[int] = None
    age_origin: Optional[LabelOrigin] = None

    reading_abilities: list[ReadingAbility]
    reading_ability_origin: Optional[LabelOrigin] = None

    huey_summary: Optional[str] = None
    summary_origin: Optional[LabelOrigin] = None

    labelled_by_user_id: Optional[UUID] = None
    labelled_by_sa_id: Optional[UUID] = None
    info: Optional[dict] = None

    recommend_status: Optional[RecommendStatus] = None
    recommend_status_origin: Optional[LabelOrigin] = None
    checked: Optional[bool] = None

    created_at: datetime
    updated_at: Optional[datetime] = None


# everything is optional here. also used for patch requests
class LabelSetCreateIn(BaseModel):
    @field_validator("info")
    @classmethod
    def validate_ai_assistance(cls, value):
        if value is not None and "ai_assistance" in value:
            value = {
                **value,
                "ai_assistance": AIAssistance.model_validate(
                    value["ai_assistance"]
                ).model_dump(mode="json", exclude_none=True),
            }
        return value

    hue_primary_key: Optional[str] = None
    hue_secondary_key: Optional[str] = None
    hue_tertiary_key: Optional[str] = None
    hue_origin: Optional[LabelOrigin] = None

    min_age: Optional[int] = None
    max_age: Optional[int] = None
    age_origin: Optional[LabelOrigin] = None
    reading_ability_keys: Optional[list[str]] = None
    reading_ability_origin: Optional[LabelOrigin] = None

    huey_summary: Optional[str] = None
    summary_origin: Optional[LabelOrigin] = None

    labelled_by_user_id: Optional[UUID] = None
    labelled_by_sa_id: Optional[UUID] = None
    info: Optional[dict] = None

    recommend_status: Optional[RecommendStatus] = None
    recommend_status_origin: Optional[LabelOrigin] = None
    checked: Optional[bool] = None

    def empty(self):
        for att in self.__dict__:
            if getattr(self, att):
                return False
        return True


class LabelSetPatch(BaseModel):
    isbn: str
    patch_data: LabelSetCreateIn
    huey_pick: bool
