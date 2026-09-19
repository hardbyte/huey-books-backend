"""Compare retrieval engines on the same local private snapshot."""

import argparse
import collections
import json
import math
import random
import statistics
import time
from pathlib import Path

import psycopg2


def metrics(ids: list[int], relevant: set[int], k: int = 10) -> dict:
    seen = set()
    gains = []
    for work_id in ids[:k]:
        gains.append(1 if work_id in relevant and work_id not in seen else 0)
        seen.add(work_id)
    dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal = sum(1 / math.log2(index + 2) for index in range(min(k, len(relevant))))
    return {
        "ndcg10": dcg / ideal if ideal else None,
        "recall10": len(set(ids[:k]) & relevant) / len(relevant) if relevant else None,
        "hit10": int(bool(set(ids[:k]) & relevant)) if relevant else None,
        "duplicates": len(ids) - len(set(ids)),
    }


def statements(scope_id: int | None, query: str) -> dict[str, tuple[str, dict]]:
    params = {
        "query": query,
        "scope": scope_id,
        "pattern": "%"
        + query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        + "%",
    }
    eligibility = (
        "TRUE"
        if scope_id is None
        else "EXISTS(SELECT 1 FROM membership m WHERE m.scope_id=%(scope)s AND m.work_id=d.work_id)"
    )
    score = "d.content <@> to_bm25query(%(query)s,'documents_bm25')"
    fts = "websearch_to_tsquery('english',%(query)s)"
    select = f"SELECT d.work_id,{{score}} AS score FROM documents d WHERE {eligibility} AND {{predicate}} ORDER BY {{order}} LIMIT 10"
    result = {
        "legacy_fts": (
            f"""SELECT d.work_id,ts_rank(s.document,{fts}) * log(1+p.frequency) AS score
          FROM legacy_search s JOIN documents d USING(work_id) JOIN popularity p USING(work_id)
          WHERE {eligibility} AND s.document @@ {fts}
          GROUP BY d.work_id,s.document,p.frequency ORDER BY score DESC,d.work_id LIMIT 10""",
            params,
        ),
        "fts_plain": (
            select.format(
                score=f"ts_rank(d.document,{fts})",
                predicate=f"d.document @@ {fts}",
                order="score DESC,d.work_id",
            ),
            params,
        ),
        "fts_weighted": (
            select.format(
                score=f"ts_rank(d.weighted,{fts})",
                predicate=f"d.weighted @@ {fts}",
                order="score DESC,d.work_id",
            ),
            params,
        ),
        "fts_weighted_popularity": (
            select.format(
                score=f"ts_rank(d.weighted,{fts}) * (1 + log(1 + greatest(d.frequency,0)))",
                predicate=f"d.weighted @@ {fts}",
                order="score DESC,d.work_id",
            ),
            params,
        ),
        "trgm_substring": (
            select.format(
                score="1.0",
                predicate="lower(d.title) LIKE %(pattern)s",
                order="d.work_id",
            ),
            params,
        ),
        "trgm_fuzzy": (
            select.format(
                score="similarity(lower(d.title),lower(%(query)s))",
                predicate="lower(d.title) %% lower(%(query)s)",
                order="score DESC,d.work_id",
            ),
            params,
        ),
        "trgm_content_substring": (
            select.format(
                score="1.0",
                predicate="lower(d.content) LIKE %(pattern)s",
                order="d.work_id",
            ),
            params,
        ),
        "trgm_content_fuzzy": (
            select.format(
                score="similarity(lower(d.content),lower(%(query)s))",
                predicate="lower(d.content) %% lower(%(query)s)",
                order="score DESC,d.work_id",
            ),
            params,
        ),
        "trgm_title_knn": (
            select.format(
                score="lower(d.title) <-> lower(%(query)s)",
                predicate="lower(d.title) %% lower(%(query)s)",
                order="score,d.work_id",
            ),
            params,
        ),
        "bm25_index": (
            select.format(score=score, predicate=f"{score}<0", order="score,d.work_id"),
            params,
        ),
        "bm25_exact": (
            f"""WITH eligible AS MATERIALIZED (SELECT d.work_id,d.content FROM documents d WHERE {eligibility}),
          scored AS MATERIALIZED (SELECT work_id,content <@> to_bm25query(%(query)s,'documents_bm25') AS score FROM eligible)
          SELECT work_id,score FROM scored WHERE score<0 ORDER BY score,work_id LIMIT 10""",
            params,
        ),
        "bm25_fts_prefilter": (
            f"""WITH eligible AS MATERIALIZED (SELECT d.work_id,d.content FROM documents d WHERE {eligibility} AND d.document @@ {fts})
          SELECT work_id,content <@> to_bm25query(%(query)s,'documents_bm25') AS score FROM eligible
          ORDER BY score,work_id LIMIT 10""",
            params,
        ),
    }
    if query.isdigit() and len(query) == 13:
        result["isbn_exact"] = (
            f"""SELECT DISTINCT d.work_id,1.0 AS score FROM editions e JOIN documents d USING(work_id)
          WHERE e.isbn=%(query)s AND {eligibility} ORDER BY d.work_id LIMIT 10""",
            params,
        )
    return result


def percentile(values: list[float], proportion: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(proportion * len(ordered)) - 1)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--engines", help="Comma-separated engine subset")
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if args.engines:
        unknown = set(args.engines.split(",")) - set(statements(None, "9780000000000"))
        if unknown:
            parser.error(f"Unknown engines: {sorted(unknown)}")
    args.output.mkdir(exist_ok=False)
    suite = json.loads(args.queries.read_text())
    connection = psycopg2.connect(
        host="127.0.0.1",
        port=55530,
        user="postgres",
        password="password",
        dbname=args.database,
    )
    connection.autocommit = True
    cursor = connection.cursor()
    cursor.execute(
        "SET statement_timeout='20s'; SET jit=off; SET pg_trgm.similarity_threshold=0.3"
    )
    cursor.execute("SELECT scope_id,work_id FROM membership")
    memberships = collections.defaultdict(set)
    for scope_id, work_id in cursor:
        memberships[scope_id].add(work_id)
    records = []
    randomizer = random.Random(779)
    with (args.output / "results.jsonl").open("w") as stream:
        for query in suite["queries"]:
            for scope in suite["scopes"]:
                relevant = {int(work_id) for work_id in query["relevance"]}
                if scope["scope_id"] is not None:
                    relevant &= memberships[scope["scope_id"]]
                engines = list(statements(scope["scope_id"], query["query"]).items())
                if args.engines:
                    engines = [
                        (name, statement)
                        for name, statement in engines
                        if name in args.engines.split(",")
                    ]
                randomizer.shuffle(engines)
                case = []
                for engine, (sql, params) in engines:
                    row = {
                        "query_id": query["id"],
                        "family": query["family"],
                        "scope": scope["name"],
                        "engine": engine,
                        "relevant_count": len(relevant),
                    }
                    try:
                        cursor.execute(sql, params)
                        cursor.fetchall()
                        elapsed = []
                        runs = 1 if engine == "bm25_exact" else args.repeats
                        for _ in range(runs):
                            started = time.perf_counter()
                            cursor.execute(sql, params)
                            results = cursor.fetchall()
                            elapsed.append((time.perf_counter() - started) * 1000)
                        ids = [r[0] for r in results]
                        assert (
                            scope["scope_id"] is None
                            or set(ids) <= memberships[scope["scope_id"]]
                        )
                        row.update(
                            {
                                "ids": ids,
                                "scores": [
                                    float(r[1]) if r[1] is not None else None
                                    for r in results
                                ],
                                "elapsed_ms": elapsed,
                                **metrics(ids, relevant),
                            }
                        )
                        cursor.execute(
                            "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + sql, params
                        )
                        row["plan"] = cursor.fetchone()[0][0]
                    except psycopg2.Error as error:
                        row["error"] = error.pgcode
                    case.append(row)
                exact = next((row for row in case if row["engine"] == "bm25_exact"), {})
                indexed = next(
                    (row for row in case if row["engine"] == "bm25_index"), {}
                )
                if "ids" in exact and "ids" in indexed:
                    indexed["exact_top10_recall"] = (
                        len(set(exact["ids"]) & set(indexed["ids"])) / len(exact["ids"])
                        if exact["ids"]
                        else 1.0
                    )
                    indexed["exact_result_count"] = len(exact["ids"])
                    indexed["underfilled"] = len(indexed["ids"]) < len(exact["ids"])
                for row in case:
                    stream.write(json.dumps(row) + "\n")
                    records.append(row)
                stream.flush()
            print(
                json.dumps({"query_complete": query["id"], "family": query["family"]}),
                flush=True,
            )
    summary = {}
    for engine in sorted({row["engine"] for row in records}):
        rows = [row for row in records if row["engine"] == engine]
        elapsed = [value for row in rows for value in row.get("elapsed_ms", [])]
        judged = [row for row in rows if row.get("ndcg10") is not None]
        summary[engine] = {
            "cases": len(rows),
            "errors": sum("error" in row for row in rows),
            "judged_cases": len(judged),
            "ndcg10": statistics.mean(row["ndcg10"] for row in judged)
            if judged
            else None,
            "hit10": statistics.mean(row["hit10"] for row in judged)
            if judged
            else None,
            "duplicate_cases": sum(row.get("duplicates", 0) > 0 for row in rows),
            "underfilled_cases": sum(row.get("underfilled", False) for row in rows),
            "p50_ms": percentile(elapsed, 0.5),
            "p95_ms": percentile(elapsed, 0.95),
            "p99_ms": percentile(elapsed, 0.99),
        }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    connection.close()


if __name__ == "__main__":
    main()
