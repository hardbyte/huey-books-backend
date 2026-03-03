import random
from concurrent.futures import ThreadPoolExecutor
from time import sleep

import pydantic.v1.error_wrappers
import vertexai
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.pydantic_v1 import BaseModel, Field

# from vertexai.generative_models import GenerativeModel, Part
from langchain_google_vertexai import ChatVertexAI
from rich import print
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from structlog import get_logger

from app import crud
from app.config import get_settings
from app.db.session import database_connection
from app.models import Edition, Work
from app.models.labelset import LabelSet
from app.models.series_works_association import series_works_association_table
from app.repositories.labelset_repository import labelset_repository
from app.repositories.work_repository import work_repository
from app.schemas.labelset import ControversialThemeKey
from app.schemas.recommendations import HueKeys, ReadingAbilityKey
from app.services.labelling import (
    create_labelset_from_ml_labelled_work,
    prepare_context_for_labelling,
    system_prompt,
)


class LabelledWorkData(BaseModel):
    """Information about a Work."""

    short_summary: str | None = Field(
        None,
        description="""Use the long summary to create the short summary, maintain the same phrases and expressions.
Focus on the content of the book, targeted at a potential reader.
Pay attention to the title of the book for clues as to what is important to mention.
The short description should be one to three sentences long.
Do not mention the title or start with the title.
The language should appeal to an age-appropriate reader, and should be engaging and friendly.

Example short summaries:
- “Harry and his dog Hopper have done everything together, ever since Hopper was a jumpy little puppy. But one day the unthinkable happens. Harry comes home from school... and Hopper isn't there to greet him.”
- “Something strange is happening in Alfie's town. Instead of shiny coins from the Tooth Fairy, kids are waking up to dead slugs, live spiders, and other dreadfully icky things under their pillows. Who would do something so horrific?”
- “Twelve-year-old Percy is sent to a summer camp for demigods like himself, and joins his new friends on a quest to prevent a war between the gods.”
""",
    )
    long_summary: str | None = Field(
        None,
        description="""The long summary should be a friendly, engaging description of the book and its contents.
Aim for between 60 - 200 words. Pay attention to the title of the book for clues as to what is important to mention.
A good long description might mention the characters, their relationships, and the events that take place.
Don't mention the title or the fact that it is a story or a book. 
It is okay to mention any awards.
Information about the origin or creation of the book should be kept to a minimum, and should not use more words than the description of what happens in the book. 

Example long summaries:
- “An affectionate, sometimes bashful pig named Wilbur befriends a spider named Charlotte, who lives in the rafters above his pen. A prancing, playful bloke, Wilbur is devastated when he learns of the destiny that befalls all those of porcine persuasion. Determined to save her friend, Charlotte spins a web that reads Some Pig, convincing the farmer and surrounding community that Wilbur is no ordinary animal and should be saved. In this story of friendship, hardship and the passing on into time, E.B. White reminds us to open our eyes to the wonder and miracle often found in the simplest of things.”
- “Andy and Terry's have the most amazing treehouse in the world! It's got a bowling alley, a see-through swimming pool, a tank full of man-eating sharks, a giant catapult, a secret underground laboratory and a marshmallow machine that follows you around and shoots marshmallows into your mouth whenever you're hungry. Well, what are you waiting for? Come on up!”
""",
    )

    reading_ability: list[ReadingAbilityKey] = Field(
        None,
        description="""A list of keys, each corresponding with an example book, and a general guide to its difficulty.
 You must look at every key and apply up to two that match the book you are describing.

SPOT:  Picture books or board books for children who do not know how to read yet. These books have full page illustrations. (e.g. Where’s Spot by Eric Hill, Goodnight Moon, The Very Hungry Caterpillar, Who Sank the Boat)

CAT_HAT: Simple books, more complex than a picture book for early child readers who have basic reading skills. These books often have an illustration on every page. (e.g. Cat in the Hat by Dr. Seuss)

TREEHOUSE: Early chapter books for developing child readers who can handle longer and slightly more complex sentences and can read independently  (e.g. the 13-Storey Treehouse, the Captain Underpants series, National Geographic Kids Readers: Rocks and Minerals)

CHARLIE_CHOCOLATE:
Chapter books for proficient child readers who can independently read more challenging books with complex plots and themes. (e.g. Charlie and the Chocolate Factory by Roald Dahl)

HARRY_POTTER: Advanced Chapter books for children who are comfortable independently reading complex texts with sophisticated vocabulary and ideas. (e.g. Harry Potter and the Philosopher's Stone, Emma by Jane Austen)

You must exclusively include 1-2 keys from this list.
""",
    )

    styles: list[str] | None = Field(
        None,
        description="""Below is a list of writing styles. You must look at every style and apply all of the styles that match the book you are describing.
- DARK
- LIGHT
- FUNNY
- SERIOUS
- QUIRKY
- ACTION_PACKED
- WHIMSICAL
- BEAUTIFUL
- CHARMIN
- GRIM
- INSPIRING
- CALM
- SUSPENSEFUL
- SILLY
- PLAYFUL
- REALISTIC
- INFORMATIVE

Only use exact keys from this list. Consider the 'notes' you have already written to help decide on the right styles.
""",
    )

    genres: list[str] | None = Field(
        None,
        description="""'genres' must be a list of any of the following keys that apply to the text.

- FACTUAL_NON_FICTION,
- FUNNY (humorous books),
- ROMANCE,
- SCIENCE,
- SCIFI,
- CLASSIC_FICTION (considered a timeless book that everyone should read),
- HISTORICAL,
- FANTASY,
- HORROR_SPOOKY,
- MYSTERY_SUSPENSE,
- ADVENTURE_AND_ACTION,
- CRIME,
- RHYMES_POETRY,
- FAIRYTALES,
- BIOGRAPHICAL,
- GRAPHIC_NOVELS,
- WAR,
- DYSTOPIAN,
- AUSTRALIAN,
- AMERICAN,
- BRITISH,
- INDIGENOUS,
- SPORTS
- PICTURE_BOOK
- YOUNG_ADULT
- LGBTQ

`genres` must only contain exact keys from this list.""",
    )

    hue_map: dict[HueKeys, float] = Field(
        None,
        description="""Hues describe a complex set of writing styles, tones and themes that are all present within a book.
In addition to the provided book data: take note of the summaries of the book you have found; the long summaries you have created;  'styles' you have already applied (styles are often mentioned in the relevant hue description). Each hue should be independently given a score between 0.0 and 1.0 to two decimal places measuring how strongly the hue applies to the book.

Below is a list of Hues, you must map these Hues and only these Hues depending on how strongly the book fits the tones expressed by the Hue:

HUE01_DARK_SUSPENSE: Dark, often suspenseful or mysterious, these books are suspenseful and adventurous. Eg. “A Wrinkle In Time”, the later “Harry Potter” books and “Maleficent Seven”

HUE02_BEAUTIFUL_WHIMSICAL: Beautiful stories with whimsical or offbeat characters. E.g. “A Most Magical Girl”, “The Wind in the Willows”, “A Wrinkle in Time”

HUE03_DARK_BEAUTIFUL: These stories are dark, beautiful, and often heartbreaking. E.g, “How to Bee”, “Artemis Fowl”, “A Monster Calls”

HUE04_JOYFUL_CHARMING: Charming, inspiring, gentle and joyful books that help us explore human emotions and experiences. For example: “Run Pip Run”, “Mustara”, “Clementine Rose and the Birthday Emergency”, “EJ12 Girl Hero: Time to Shine”, “Alice-Miranda in Hollywood”,”Up and Away”

HUE05_FUNNY_COMIC: Purely funny books! Often have a comic illustration style, but are not just comic books. E.g. “Diary of a Wimpy Kid”, “Captain Underpants”

HUE06_DARK_GRITTY: Grim, serious books that take you to the dark side of humanity. E.g. “1984”, “Lord of the Flies”, “Ender's Game”

HUE07_SILLY_CHARMING: Charming, funny books that are a little bit slapstick and active. For example: “Pig the Pug”, many Dr. Seuss titles, “Giraffes Can't Dance”, “Thelma the Unicorn”, “Fearless: Sons and Daughters”, "It's a story, Rory!", “Rocky Road”, “Pyramid Puzzle”, “EJ12 Girl Hero: Big Brother”, “Jump Start”, “Making Waves”, The Fairytale Princess, Scratch Kitten Goes to Sea, The Last Viking, Ida Always , “Whacko the Chook”, "Good Night, Sleep Tight ", "Slow Down, Monkey!"

HUE08_CHARMING_COURAGEOUS: Winsome, whimsical and resilient characters set in adventurous, charming, and sometimes funny stories. For example: “Tashi”, “Under My Bed”, “The Last Viking Returns”, “Potato Music”, Digby & Claude, Mbobo Tree, Adelaide's Secret World, Water Witcher, Little Princesses: The Rain Princess, Tashi and the Stolen Bus, Laika: The Astronaut , You and Me: Our Place, Hannah and the Tomorrow Room, Black Fella White Fella, The Wish Pony ,

HUE09_CHARMING_PLAYFUL: Charming and playful, these books are gentle and a little funny. E.g. “Curious George”, “Are We There Yet?”, “Ninnyhammer”, “Snot Chocolate and Other Funny Stories”, “All Cats have Asperger Syndrome”, the “WeirDo” series, “Thea Stilton and the Chocolate Sabotage”, “The Tuckshop Kid” , “The New Kid”.

HUE10_INSPIRING: Beautiful, inspiring books, evocative and sometimes based in reality. For example "Free as a Bird - the Story of Malala" by Lino Maslo, Big Fella Rain,

HUE11_REALISTIC_HOPE: Realistic characters with relatable problems. These are often coming of age books. E.g. “Girl Underground”, “Wonder” by R.J. Palacio, “I am Jack”, “Hating Alison Ashley”, “My Australian Story: Escape from Cockatoo Island”, “Billy Mack's War”, “When Hitler Stole Pink Rabbit”, "Offside, Upfront", “Mary's Australia”, “Along the road to Gundagai”, “The Anzac Tree”, “Stuff Happens: Sean”, “Ugly: a Beaut Story About One Very Ugly Kid”, “Australia's Explorers”, “Blossom”, “Out of Bounds, Izzy Folau 1: Chance of a Lifetime”, "Say yes : A Story of Friendship, Fairness and a Vote for Hope"

HUE12_FUNNY_QUIRKY: Funny and a little offbeat. These books have a quirk that is a little witty or strange. For example: “Curious George”, “Don't Let the Pigeon Drive the Bus!”, “The Shaggy Gully Times”, “Fiona the Pig”, “The Pocket Dogs and the Lost Kitten”, “Samurai Kids: Owl Ninja”, “Left & Right”, “Goblin in the Rainforest”, “Sam and the Killer Robot”

HUE13_INFORMATIVE: Informative/Factual books with very little tone. Encyclopaedic or factual books. E.g “Australian Birds”, “ABC Book of Animals”
""",
    )

    characters: list[str] | None = Field(
        None,
        description="""'characters' must contain a list of these and only these labels, relating to the main character(s) of the book.
A reasonable number of labels should be used, but not too many.

- BUGS,
- CATS_DOGS_AND_MICE,
- HORSES_AND_FARM_ANIMALS,
- OCEAN_CREATURES,
- WOLVES_AND_WILD_ANIMALS,
- AUSTRALIAN_ANIMALS,
- BRITISH_ANIMALS,
- AMERICAN_ANIMALS,
- DINOSAURS,
- PRINCESSES_FAIRIES_MERMAIDS,
- UNICORNS,
- SUPERHEROES,
- FAMILIES_AND_FRIENDS,
- MONSTERS_GHOSTS_AND_VAMPIRES,
- ALIENS,
- TRAINS_CARS_AND_TRUCKS,
- MISFITS_AND_UNDERDOGS,
- PIRATES,
- ROBOTS,
- ATHLETES_AND_SPORT_STARS,
- WIZARDS_WITCHES_MAGIC

`characters` must only contain exact keys from this list.""",
    )

    # gender: str | None = Field(None, description="""'gender' must be whichever of the following keys is most appropriate, relating to the main character(s) of the book.
    # Pronouns in the assorted summaries and genres may be valuable clues.
    # Generally use: "male", "female", "nonbinary", or "unknown".
    # """)

    min_age: int | None = Field(
        None,
        description="Min reader age in years. Eg 9. Leave unset if suitable for all ages.",
    )
    max_age: int | None = Field(
        None,
        description="Max reader age in years. Eg 11. Leave unset if suitable for ages above 14",
    )

    series: str | None = Field(
        None,
        description="The name of the series this book belongs to (if any). E.g. 'Harry Potter'.",
    )
    series_number: int | None = Field(
        None, description="The number of the series this book belongs to (if any)."
    )

    awards: list[str] | None = Field(
        None, description="Notable awards this book has been given (if any)"
    )
    notes: str | None = Field(
        None,
        description="""Any other information you think is relevant for parents when selecting a book for their child, such as content advisory, characters, styles and the main themes.
Adult themes, heavy emotional content, religion and LGBTQ themes should be noted. Similar to movie and streaming classification systems.

Below are some examples:
-  This novel contains mature themes and violence, and may not be suitable for all readers.
- The book contains themes of revolution and fighting back against oppression, as well as some violence and danger. It also touches on the relationship between humans and animals, particularly wolves.
""",
    )
    recommend_status: str = Field(
        None,
        description="""This is advice on whether or not this book should be shown to a child as a recommendation:
"GOOD" # Good to Recommend
"BAD_BORING"  # Too boring
"BAD_REFERENCE"  # Reference/Education book (a textbook or reference book that a child would not read for enjoyment for example a dictionary.)
"BAD_CONTROVERSIAL"  # Extremely controversial content.
"BAD_LOW_QUALITY"  # Not a great example
""",
    )

    controversial_themes: list[ControversialThemeKey] | None = Field(
        None,
        description="""A list of these and only these potentially controversial topics to ensure
    we don't show this book to audiences who are not ready for it:

- VIOLENT: contains explicit violence or gore
- SEXUAL: sexual behaviour, erotic fiction, explicit material sex
- DRUGS: use of illegal substances or excessive alcohol use
- RELIGIOUS: religious ideas
- LGBT: lesbian, gay, bisexual, transgender and queer themes and people may feature in the book
- PROFANITY: contains excessive profanity or offensive language
- MENTAL_HEALTH: mental health issues such as depression, anxiety, and suicide
- OTHER: a controversial topic not included in this list, describe in the 'notes' output
""",
    )


#


def sample_editions_to_label(session, limit=10, skip=0):
    q = (
        select(Edition)
        .join(Work, Edition.work_id == Work.id)
        .join(LabelSet, LabelSet.work_id == Work.id, isouter=True)
        # .where(Edition.hydrated == False)
        .where(Edition.work_id != None)
        # .where(Work.labelset == None)
        .where(LabelSet.huey_summary == None)
        # .where(LabelSet.checked == None)
        # .where(LabelSet.recommend_status != RecommendStatus.GOOD)
        .order_by(Edition.collection_count.desc())
        .offset(skip)
        .limit(limit)
    )
    return session.execute(q).scalars().all()


def label_books(count=10, offset=0):
    logger = get_logger().bind(offset=offset)
    sleep((1 + offset / 2.0) * random.random())

    GCP_PROJECT_ID: str = "wriveted-api"
    GCP_LOCATION: str = "us-central1"

    settings = get_settings()

    engine, SessionLocal = database_connection(
        settings.SQLALCHEMY_DATABASE_URI,
        pool_size=settings.DATABASE_POOL_SIZE,
        max_overflow=settings.DATABASE_MAX_OVERFLOW,
    )

    vertexai.init(project=GCP_PROJECT_ID, location=GCP_LOCATION)

    llm = ChatVertexAI(model="gemini-1.5-pro-preview-0514")

    with Session(engine) as session:
        logger.info("Getting editions to label")

        service_account = crud.service_account.get_or_404(
            db=session, id=settings.GPT_SERVICE_ACCOUNT_ID
        )

        editions = sample_editions_to_label(session, count, skip=offset)
        logger.info(f"Labeling {len(editions)} editions")

        for e in editions:
            logger.bind(edition_id=e.id)
            logger.info(e)
            work = e.work
            logger.info(work)
            user_content = prepare_context_for_labelling(
                work, extra=str(work.labelset.get_label_dict(session))
            )

            retries = 0
            response = None
            while retries < 3:
                retries += 1
                try:
                    response = llm.with_structured_output(
                        schema=LabelledWorkData
                    ).invoke(
                        [
                            SystemMessage(content=system_prompt),
                            HumanMessage(content=user_content),
                        ]
                    )
                except pydantic.v1.error_wrappers.ValidationError as e:
                    logger.debug("Retrying")
                    sleep(2)
                    continue
                except Exception:
                    logger.debug("Error. Sleeping")
                    sleep(120)
                    continue

            if response is not None:
                try:
                    labelset_data = create_labelset_from_ml_labelled_work(response)

                    logger.info(labelset_data)

                    labelset = labelset_repository.get_or_create(session, work, False)
                    labelset_repository.patch(session, labelset, labelset_data, True)
                    logger.info(f"Updated labelset for {work.title}")

                    if response.series is not None:
                        logger.info("updating series")
                        series = work_repository.get_or_create_series(
                            session, response.series
                        )

                        series_works_values = {
                            "series_id": series.id,
                            "work_id": work.id,
                        }
                        if response.series_number:
                            series_works_values["order_id"] = response.series_number

                        session.execute(
                            insert(series_works_association_table)
                            .values(**series_works_values)
                            .on_conflict_do_nothing()
                        )
                        session.commit()

                except Exception as e:
                    logger.info("Something went wrong. Skipping")
                    logger.debug(e)
                    session.rollback()
                    continue

                crud.event.create(
                    session,
                    title="Work updated",
                    description=f"Made a change to '{work.title}'",
                    info={
                        "changes": labelset_data.model_dump(mode="json"),
                        "work_id": work.id,
                    },
                    account=service_account,
                )
            session.commit()
            sleep(10 * random.random())

        logger.info("Complete")


def run_in_threads(count_per_thread=10, num_threads=10):
    offsets = [i * count_per_thread for i in range(num_threads)]
    count = count_per_thread

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(label_books, count, offset) for offset in offsets]
        for future in futures:
            future.result()


if __name__ == "__main__":
    # label_books(count=10, offset=0)
    for i in range(500):
        run_in_threads(count_per_thread=300, num_threads=25)
        sleep(100)
        print("going again")
