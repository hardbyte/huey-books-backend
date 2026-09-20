"""Create reproducible known-item queries and metadata-grounded silver judgments."""

import argparse
import collections
import json
import random
import re
import unicodedata
from pathlib import Path

import psycopg2
import psycopg2.extras


def normalize(value: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    connection = psycopg2.connect(
        host="127.0.0.1",
        port=55530,
        user="postgres",
        password="password",
        dbname=args.database,
    )
    with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute("SELECT * FROM documents ORDER BY work_id")
        documents = list(cursor)
        cursor.execute(
            "SELECT work_id,min(isbn) isbn FROM editions WHERE isbn ~ %s GROUP BY work_id",
            ("^[0-9]{13}$",),
        )
        isbns = {row["work_id"]: row["isbn"] for row in cursor}
        cursor.execute(
            "SELECT scope_id,count(*) size FROM membership GROUP BY scope_id ORDER BY count(*),scope_id"
        )
        sizes = list(cursor)
    rng = random.Random(779)
    titles: dict[str, list[int]] = collections.defaultdict(list)
    author_works: dict[int, list[int]] = collections.defaultdict(list)
    series_works: dict[int, list[int]] = collections.defaultdict(list)
    for document in documents:
        titles[normalize(document["title"])].append(document["work_id"])
        for author in document["authors"]:
            author_works[author["id"]].append(document["work_id"])
        for series in document["series"]:
            series_works[series["id"]].append(document["work_id"])
    queries = []

    def add(family: str, query: str, relevant: list[int], intent: str, **extra) -> None:
        queries.append(
            {
                "id": f"q{len(queries) + 1:03}",
                "family": family,
                "query": query,
                "intent": intent,
                "judgment_source": "catalogue-derived silver",
                "relevance": {str(work_id): 3 for work_id in sorted(set(relevant))},
                **extra,
            }
        )

    suitable = [
        d
        for d in documents
        if 4 <= len(d["title"]) <= 90 and len(normalize(d["title"]).split()) >= 2
    ]
    popular = sorted(suitable, key=lambda d: (-d["frequency"], d["work_id"]))[:8]
    groups = {
        "popular_title": popular,
        "tail_title": rng.sample([d for d in suitable if 0 < d["frequency"] <= 2], 8),
        "authorless_title": rng.sample([d for d in suitable if not d["authors"]], 6),
        "unheld_title": rng.sample([d for d in suitable if not d["has_popularity"]], 6),
        "multiple_series": rng.sample([d for d in suitable if len(d["series"]) > 1], 6),
        "punctuation": rng.sample(
            [d for d in suitable if re.search(r"['’:&-]", d["title"])], 6
        ),
        "short_title": rng.sample(
            [d for d in documents if 1 <= len(d["title"].strip()) <= 3], 6
        ),
    }
    for family, selected in groups.items():
        for d in selected:
            add(
                family,
                d["title"].strip(),
                titles[normalize(d["title"])],
                "Find this title",
                source_work_id=d["work_id"],
            )
    for d in popular[:6]:
        title = d["title"].strip()
        add(
            "prefix",
            title[: max(4, len(title) // 2)].rstrip(),
            titles[normalize(title)],
            "Complete this partially typed title",
            source_work_id=d["work_id"],
        )
        match = next(m for m in re.finditer(r"[A-Za-z]{3,}", title))
        index = match.start() + 1
        typo = title[:index] + title[index + 1] + title[index] + title[index + 2 :]
        add(
            "typo",
            typo,
            titles[normalize(title)],
            "Find the intended title despite one adjacent-letter transposition",
            source_work_id=d["work_id"],
        )
        if d["work_id"] in isbns:
            add(
                "isbn",
                isbns[d["work_id"]],
                [d["work_id"]],
                "Exact edition ISBN lookup, returning its work",
                source_work_id=d["work_id"],
            )
    for d in popular[:6]:
        author = d["authors"][0]
        add(
            "author",
            author["name"],
            author_works[author["id"]],
            "Find works by this named author",
            source_author_id=author["id"],
        )
    series_choices = sorted(
        [(len(ids), sid) for sid, ids in series_works.items()], reverse=True
    )[:6]
    for _, series_id in series_choices:
        series = next(s for d in documents for s in d["series"] if s["id"] == series_id)
        add(
            "series",
            series["title"],
            series_works[series_id],
            "Find works in this named series",
            source_series_id=series_id,
        )
    for term in [
        "dragon",
        "dinosaurs",
        "space",
        "friendship",
        "magic",
        "ocean",
        "cats",
        "football",
    ]:
        relevant = [
            d["work_id"] for d in documents if term in normalize(d["title"]).split()
        ]
        add(
            "topic_title",
            term,
            relevant,
            "Find titles explicitly naming this topic; thematic relevance beyond metadata is unjudged",
        )
    maori = [d for d in documents if "maori" in normalize(d["title"]).split()]
    if maori:
        add(
            "accent",
            "Māori",
            [d["work_id"] for d in maori],
            "Find Maori titles when query uses a macron",
        )
    for query in ["", "the", "qzxvnotacatalogueterm779"]:
        add(
            "empty_or_no_match",
            query,
            [],
            "Raw-engine diagnostic: target API must route empty/stopword input to browse; impossible token must return no matches",
        )
    scope_indexes = {
        "smallest": 0,
        "p10": len(sizes) // 10,
        "median": len(sizes) // 2,
        "largest": len(sizes) - 1,
    }
    scopes = [{"name": "global", "scope_id": None, "size": len(documents)}] + [
        {"name": name, **dict(sizes[index])} for name, index in scope_indexes.items()
    ]
    for term in ["book", "little", "story", "new", "great", "world"]:
        add(
            "common_term",
            term,
            [d["work_id"] for d in documents if term in normalize(d["title"]).split()],
            "Broad title-term retrieval with selective-library completeness checks",
        )
    with connection.cursor() as cursor:
        for scope in scopes[1:]:
            cursor.execute(
                "SELECT work_id FROM membership WHERE scope_id=%s", (scope["scope_id"],)
            )
            held = {row[0] for row in cursor}
            used_titles = {normalize(q["query"]) for q in queries}
            choices = rng.sample(
                [
                    d
                    for d in suitable
                    if d["work_id"] in held and normalize(d["title"]) not in used_titles
                ],
                2,
            )
            for d in choices:
                add(
                    "library_known_title",
                    d["title"].strip(),
                    titles[normalize(d["title"])],
                    "Known title held in the designated evaluation library",
                    source_scope=scope["name"],
                    source_work_id=d["work_id"],
                )
    single_word_titles = {}
    for d in sorted(documents, key=lambda d: (-d["frequency"], d["work_id"])):
        title = d["title"].strip()
        if re.fullmatch(r"[A-Za-z]{6,}", title):
            single_word_titles.setdefault(normalize(title), d)
    for d in list(single_word_titles.values())[:3]:
        title = d["title"].strip()
        add(
            "single_word_typo",
            title[:1] + title[2] + title[1] + title[3:],
            titles[normalize(title)],
            "Find the intended single-word title with no other correctly spelled query terms",
            source_work_id=d["work_id"],
        )
        add(
            "single_word_prefix",
            title[:-2],
            titles[normalize(title)],
            "Complete a partial single-word title",
            source_work_id=d["work_id"],
        )
    result = {
        "seed": 779,
        "judgment_scale": {
            "3": "exact intended item/author/series or explicit title topic",
            "0": "not in the defined metadata target set; not a human judgment of thematic irrelevance",
        },
        "limitations": "Balanced diagnostic queries derived from catalogue metadata, not observed traffic frequencies or human-labeled relevance. Prefix/typo targets specify intended item; other plausible interpretations are not judged.",
        "scopes": scopes,
        "queries": queries,
    }
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(
        json.dumps(
            {
                "queries": len(queries),
                "families": dict(collections.Counter(q["family"] for q in queries)),
                "scopes": scopes,
            }
        )
    )


if __name__ == "__main__":
    main()
