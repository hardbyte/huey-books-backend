"""Validate disjoint benchmark batches and summarize by query family and scope."""

import argparse
import collections
import json
import statistics
from pathlib import Path

from run_benchmark import percentile


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    suite = json.loads(args.queries.read_text())
    rows = [
        json.loads(line)
        for run in args.runs
        for line in (run / "results.jsonl").read_text().splitlines()
    ]
    keys = [(row["query_id"], row["scope"], row["engine"]) for row in rows]
    assert len(keys) == len(set(keys)), (
        "Duplicate measurements would bias the aggregate"
    )
    assert not any("error" in row for row in rows), (
        "SQL failures must be resolved or explicitly reported"
    )
    expected_engines = [
        "legacy_fts",
        "fts_plain",
        "fts_weighted",
        "fts_weighted_popularity",
        "trgm_substring",
        "trgm_fuzzy",
        "bm25_index",
        "bm25_exact",
        "bm25_fts_prefilter",
        "trgm_content_substring",
        "trgm_content_fuzzy",
        "trgm_title_knn",
    ]
    expected = {
        (q["id"], s["name"], e)
        for q in suite["queries"]
        for s in suite["scopes"]
        for e in expected_engines + (["isbn_exact"] if q["family"] == "isbn" else [])
    }
    assert set(keys) == expected, (
        f"Missing {len(expected - set(keys))} or unexpected {len(set(keys) - expected)} cases"
    )
    assert all(row["duplicates"] == 0 for row in rows if row["engine"] != "legacy_fts")

    def aggregate(selected: list[dict]) -> dict:
        elapsed = [x for r in selected for x in r["elapsed_ms"]]
        judged = [r for r in selected if r["ndcg10"] is not None]
        return {
            "cases": len(selected),
            "judged": len(judged),
            "ndcg10": statistics.mean(r["ndcg10"] for r in judged) if judged else None,
            "hit10": statistics.mean(r["hit10"] for r in judged) if judged else None,
            "recall10": statistics.mean(r["recall10"] for r in judged)
            if judged
            else None,
            "p50_ms": percentile(elapsed, 0.5),
            "p95_ms": percentile(elapsed, 0.95),
            "p99_ms": percentile(elapsed, 0.99),
            "duplicate_cases": sum(r["duplicates"] > 0 for r in selected),
        }

    summaries = {}
    for dimension in ("scope", "family"):
        grouped = collections.defaultdict(list)
        for row in rows:
            grouped[(row["engine"], row[dimension])].append(row)
        summaries[dimension] = {
            f"{engine}/{value}": aggregate(group)
            for (engine, value), group in sorted(grouped.items())
        }
    lexical = [r for r in rows if r["family"] not in ("isbn", "empty_or_no_match")]
    summaries["lexical"] = {
        e: aggregate([r for r in lexical if r["engine"] == e]) for e in expected_engines
    }
    bm25 = [r for r in rows if r["engine"] == "bm25_index"]
    exact = {
        (r["query_id"], r["scope"]): r for r in rows if r["engine"] == "bm25_exact"
    }
    summaries["bm25_completeness"] = {
        "cases": len(bm25),
        "underfilled": sum(r["underfilled"] for r in bm25),
        "different_top10": sum(r["exact_top10_recall"] < 1 for r in bm25),
        "different_order": sum(
            r["ids"] != exact[(r["query_id"], r["scope"])]["ids"] for r in bm25
        ),
        "mean_exact_top10_recall": statistics.mean(
            r["exact_top10_recall"] for r in bm25
        ),
    }
    summaries["engine_cases"] = len(rows)
    args.output.write_text(json.dumps(summaries, indent=2))
    print(
        json.dumps(
            {
                "engine_cases": len(rows),
                "bm25_completeness": summaries["bm25_completeness"],
                "lexical": summaries["lexical"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
