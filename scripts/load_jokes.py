#!/usr/bin/env python3
"""Load jokes from Jokes.csv into CMS content via the API.

Parses the Landbot joke extraction CSV and creates CMS content items.
Jokes are stored as type=QUESTION with a single "Tell me!" answer that
carries the punchline and reward GIF in metadata. This lets the existing
CMS random source pattern work for joke delivery in the chatflow.

Usage:
    poetry run python scripts/load_jokes.py \
        --api http://localhost:8000 \
        --token "$ADMIN_JWT"

    poetry run python scripts/load_jokes.py --dry-run
"""

import argparse
import csv
import sys
from pathlib import Path

import httpx

SCRIPTS_DIR = Path(__file__).resolve().parent
CSV_PATH = SCRIPTS_DIR.parent / "landbot_extraction_output" / "Jokes.csv"

TAG_JOKE = "huey-joke"


def parse_age_groups(age_str: str) -> list[int]:
    """Parse comma-separated age string like '5,6,7' into list of ints."""
    if not age_str or not age_str.strip():
        return []
    try:
        return [int(a.strip()) for a in age_str.split(",")]
    except ValueError:
        return []


def load_jokes(csv_path: Path) -> list[dict]:
    """Load joke rows from CSV as CMS question content dicts."""
    jokes: list[dict] = []

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            step = (row.get("step") or "").strip()
            question = (row.get("question") or "").strip()
            answer = (row.get("answer") or "").strip()
            reward = (row.get("reward") or "").strip()
            age_str = (row.get("age") or "").strip()

            if step != "joke" or not question or not answer:
                continue

            ages = parse_age_groups(age_str)
            min_age = min(ages) if ages else 3
            max_age = max(ages) if ages else 13

            jokes.append({
                "type": "joke",
                "content": {
                    "question_text": question.strip(),
                    "min_age": min_age,
                    "max_age": max_age,
                    "answers": [
                        {
                            "text": "Tell me! \U0001f914",
                            "value": "tell_me",
                            "punchline": answer.strip(),
                            "reward_url": reward if reward else None,
                        }
                    ],
                },
                "tags": [TAG_JOKE],
                "is_active": True,
                "status": "published",
                "visibility": "wriveted",
                "info": {
                    "source": "landbot",
                    "content_subtype": "joke",
                    "min_age": min_age,
                    "max_age": max_age,
                },
            })

    return jokes


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
    parser = argparse.ArgumentParser(description="Load jokes into CMS content")
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--token", help="Admin JWT bearer token")
    parser.add_argument("--csv", type=Path, default=CSV_PATH, help="Path to Jokes.csv")
    parser.add_argument("--dry-run", action="store_true", help="Print without making changes")
    parser.add_argument("--replace", action="store_true", help="Delete existing jokes before loading")
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"Error: CSV file not found: {args.csv}")
        sys.exit(1)

    print(f"Loading jokes from {args.csv}")
    jokes = load_jokes(args.csv)
    print(f"  {len(jokes)} jokes parsed")

    # Summarize by age range
    from collections import Counter
    age_ranges = Counter(f"{j['info']['min_age']}-{j['info']['max_age']}" for j in jokes)
    for age_range, count in sorted(age_ranges.items()):
        print(f"    ages {age_range}: {count} jokes")

    if args.dry_run:
        print("\nDry run:")
        for j in jokes:
            c = j["content"]
            print(f"  [{j['info']['min_age']}-{j['info']['max_age']}] {c['question_text'][:60]}")
            print(f"     -> {c['answers'][0]['punchline'][:60]}")
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
        existing_count, existing_ids = count_existing(client, TAG_JOKE)
        if existing_ids:
            print(f"  Deleting {existing_count} existing joke content items")
            resp = client.request("DELETE", "/v1/cms/content/bulk", json={"content_ids": existing_ids})
            if resp.status_code not in (200, 204):
                print(f"  Warning: Bulk delete returned {resp.status_code}")

    print("\nCreating jokes...")
    created = 0
    errors = 0
    for j in jokes:
        resp = client.post("/v1/cms/content", json=j)
        if resp.status_code == 201:
            created += 1
        else:
            errors += 1
            if errors <= 3:
                print(f"  Warning: {resp.status_code} {resp.text[:100]}")

    if errors > 3:
        print(f"  ... and {errors - 3} more errors")

    print(f"  Created {created} jokes ({errors} errors)")
    client.close()


if __name__ == "__main__":
    main()
