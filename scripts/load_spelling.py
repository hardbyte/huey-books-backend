#!/usr/bin/env python3
"""Load spelling questions from CSV files into CMS content via the API.

Each CSV has columns: word, round, correct (yes/no).
Rows are grouped by round — 3 options per round, one correct.
Each round becomes a CMS QUESTION with 3 choice options.

Usage:
    poetry run python scripts/load_spelling.py \
        --api http://localhost:8000 \
        --token "$ADMIN_JWT"

    poetry run python scripts/load_spelling.py --dry-run
"""

import argparse
import csv
import sys
from pathlib import Path

import httpx

SCRIPTS_DIR = Path(__file__).resolve().parent
EXTRACTION_DIR = SCRIPTS_DIR.parent / "landbot_extraction_output"

DIFFICULTY_FILES = {
    "easy": EXTRACTION_DIR / "SpellingEasy.csv",
    "medium": EXTRACTION_DIR / "SpellingMedium.csv",
    "hard": EXTRACTION_DIR / "SpellingHard.csv",
}

TAG_SPELLING = "huey-spelling"


def load_spelling_csv(csv_path: Path, difficulty: str) -> list[dict]:
    """Parse a spelling CSV into CMS question content dicts.

    Groups rows by round number. Each round has 3 options (one correct).
    """
    rounds: dict[int, list[dict]] = {}

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            word = (row.get("word") or "").strip()
            round_num = int(row.get("round", 0))
            is_correct = (row.get("correct") or "").strip().lower() == "yes"

            if not word or not round_num:
                continue

            rounds.setdefault(round_num, []).append({
                "word": word,
                "correct": is_correct,
            })

    questions: list[dict] = []
    tag = f"{TAG_SPELLING}-{difficulty}"

    for round_num in sorted(rounds.keys()):
        options = rounds[round_num]
        correct_word = next((o["word"] for o in options if o["correct"]), None)
        if not correct_word:
            continue

        answers = []
        for opt in options:
            answers.append({
                "text": opt["word"],
                "value": "correct" if opt["correct"] else "wrong",
                "label": opt["word"],
                "correct_word": correct_word,
            })

        questions.append({
            "type": "question",
            "content": {
                "question_text": "Which is the correct spelling? \U0001f4dd",
                "answers": answers,
            },
            "tags": [TAG_SPELLING, tag],
            "is_active": True,
            "status": "published",
            "visibility": "wriveted",
            "info": {
                "source": "landbot",
                "content_subtype": "spelling",
                "difficulty": tag,
                "round": round_num,
                "correct_word": correct_word,
            },
        })

    return questions


def count_existing(client: httpx.Client, tag: str) -> tuple[int, list[str]]:
    """Count existing content with given tag and return IDs."""
    ids: list[str] = []
    skip = 0
    while True:
        resp = client.get("/v1/cms/content", params={"tags": tag, "limit": 100, "skip": skip})
        if resp.status_code != 200:
            return 0, []
        data = resp.json()
        items = data.get("data", [])
        total = data.get("pagination", {}).get("total", len(items))
        ids.extend(item["id"] for item in items)
        if len(ids) >= total or not items:
            break
        skip += len(items)
    return len(ids), ids


def main():
    parser = argparse.ArgumentParser(description="Load spelling questions into CMS content")
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--token", help="Admin JWT bearer token")
    parser.add_argument("--dry-run", action="store_true", help="Print without making changes")
    parser.add_argument("--replace", action="store_true", help="Delete existing spelling content before loading")
    args = parser.parse_args()

    all_questions: list[dict] = []
    for difficulty, csv_path in DIFFICULTY_FILES.items():
        if not csv_path.exists():
            print(f"Warning: {csv_path} not found, skipping {difficulty}")
            continue
        questions = load_spelling_csv(csv_path, difficulty)
        print(f"  {difficulty}: {len(questions)} rounds from {csv_path.name}")
        all_questions.extend(questions)

    print(f"\nTotal: {len(all_questions)} spelling questions")

    if args.dry_run:
        print("\nDry run:")
        for q in all_questions:
            answers = q["content"]["answers"]
            correct = next((a["text"] for a in answers if a["value"] == "correct"), "?")
            wrong = [a["text"] for a in answers if a["value"] != "correct"]
            print(f"  [{q['info']['difficulty']}] {correct} (vs {', '.join(wrong)})")
        return

    if not args.token:
        print("Error: --token required when not in --dry-run mode")
        sys.exit(1)

    client = httpx.Client(
        base_url=args.api.rstrip("/"),
        headers={"Authorization": f"Bearer {args.token}"},
        timeout=60.0,
    )

    if args.replace:
        existing_count, existing_ids = count_existing(client, TAG_SPELLING)
        if existing_ids:
            print(f"  Deleting {existing_count} existing spelling content items")
            resp = client.request("DELETE", "/v1/cms/content/bulk", json={"content_ids": existing_ids})
            if resp.status_code not in (200, 204):
                print(f"  Warning: Bulk delete returned {resp.status_code}")

    print("\nCreating spelling questions...")
    created = 0
    errors = 0
    for q in all_questions:
        resp = client.post("/v1/cms/content", json=q)
        if resp.status_code == 201:
            created += 1
        else:
            errors += 1
            if errors <= 3:
                print(f"  Warning: {resp.status_code} {resp.text[:100]}")

    if errors > 3:
        print(f"  ... and {errors - 3} more errors")

    print(f"  Created {created} spelling questions ({errors} errors)")
    client.close()


if __name__ == "__main__":
    main()
