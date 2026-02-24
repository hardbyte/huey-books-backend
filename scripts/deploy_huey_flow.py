#!/usr/bin/env python3
"""Deploy the Huey Bookbot flow, theme, and CMS questions via the API.

Deploys sub-flows first (profile, preferences, recommendation), then resolves
composite node references and deploys the main flow.

Uses admin JWT authentication — no direct database access required.

    # Against production
    poetry run python scripts/deploy_huey_flow.py \
        --api https://api.wriveted.com \
        --token "$ADMIN_JWT"

    # Against local dev
    poetry run python scripts/deploy_huey_flow.py \
        --api http://localhost:8000 \
        --token "$ADMIN_JWT"

Flags:
    --dry-run         Print what would be created/updated without making changes
    --skip-questions  Skip CMS question seeding
    --skip-subflows   Skip sub-flow deployment (use existing sub-flows)
    --flow-only       Only deploy the flow (skip theme, sub-flows, and questions)
"""

import argparse
import json
import sys
from pathlib import Path

import httpx

SCRIPTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = SCRIPTS_DIR / "fixtures"
FLOW_FILE = FIXTURES_DIR / "huey-bookbot-flow.json"
SEED_FILE = FIXTURES_DIR / "admin-ui-seed.json"

FLOW_NAME = "Huey Bookbot"
THEME_NAME = "Huey Bookbot"

SUB_FLOWS = [
    {"file": "huey-profile-flow.json", "seed_key": "huey-profile", "name": "Huey Profile"},
    {"file": "huey-preferences-flow.json", "seed_key": "huey-preferences", "name": "Huey Preferences"},
    {"file": "huey-recommendation-flow.json", "seed_key": "huey-recommendation", "name": "Huey Recommendation"},
    {"file": "huey-jokes-flow.json", "seed_key": "huey-jokes", "name": "Huey Jokes"},
    {"file": "huey-spelling-flow.json", "seed_key": "huey-spelling", "name": "Huey Spelling"},
]


def load_theme_config() -> dict:
    """Load the Huey theme config from the seed fixture."""
    seed = json.loads(SEED_FILE.read_text())
    for theme in seed.get("themes", []):
        if theme.get("seed_key") == "huey-bookbot-theme":
            return theme
    print("Error: huey-bookbot-theme not found in seed fixture")
    sys.exit(1)


def load_flow_config() -> dict:
    """Load the Huey flow definition from its JSON file."""
    if not FLOW_FILE.exists():
        print(f"Error: Flow file not found: {FLOW_FILE}")
        sys.exit(1)
    return json.loads(FLOW_FILE.read_text())


def find_csv() -> Path | None:
    """Find the pre-processed AI-Questions.csv."""
    candidates = [
        SCRIPTS_DIR.parent / "AI-Questions.csv",
        SCRIPTS_DIR / "AI-Questions.csv",
        FIXTURES_DIR / "AI-Questions.csv",
    ]
    return next((p for p in candidates if p.exists()), None)


def api_get(client: httpx.Client, path: str, params: dict | None = None) -> httpx.Response:
    resp = client.get(path, params=params)
    return resp


def api_post(client: httpx.Client, path: str, body: dict) -> httpx.Response:
    resp = client.post(path, json=body)
    return resp


def api_put(client: httpx.Client, path: str, body: dict) -> httpx.Response:
    resp = client.put(path, json=body)
    return resp


def api_delete(client: httpx.Client, path: str, body: dict | None = None) -> httpx.Response:
    if body:
        resp = client.request("DELETE", path, json=body)
    else:
        resp = client.delete(path)
    return resp


# ── Theme ─────────────────────────────────────────────────────────────────


def find_existing_theme(client: httpx.Client) -> dict | None:
    """Find an existing global theme by name."""
    resp = api_get(client, "/v1/cms/themes", params={"include_global": True, "limit": 100})
    if resp.status_code != 200:
        print(f"  Warning: Failed to list themes: {resp.status_code}")
        return None
    for theme in resp.json().get("data", []):
        if theme["name"] == THEME_NAME and theme.get("school_id") is None:
            return theme
    return None


def deploy_theme(client: httpx.Client, config: dict, dry_run: bool) -> str | None:
    """Create or update the Huey theme. Returns the theme ID."""
    existing = find_existing_theme(client)

    theme_body = {
        "name": config["name"],
        "description": config.get("description"),
        "config": config.get("config", {}),
        "avatar_url": config.get("avatar_url"),
        "logo_url": config.get("logo_url"),
        "is_active": True,
        "version": "1.0",
    }

    if existing:
        theme_id = existing["id"]
        print(f"  Updating existing theme: {config['name']} (id={theme_id})")
        if dry_run:
            return theme_id
        resp = api_put(client, f"/v1/cms/themes/{theme_id}", theme_body)
        if resp.status_code != 200:
            print(f"  Error updating theme: {resp.status_code} {resp.text[:200]}")
            sys.exit(1)
        return theme_id
    else:
        print(f"  Creating theme: {config['name']}")
        if dry_run:
            return None
        resp = api_post(client, "/v1/cms/themes", theme_body)
        if resp.status_code != 201:
            print(f"  Error creating theme: {resp.status_code} {resp.text[:200]}")
            sys.exit(1)
        theme_id = resp.json()["id"]
        print(f"  Created theme: {theme_id}")
        return theme_id


# ── Flow ──────────────────────────────────────────────────────────────────


def find_existing_flow(client: httpx.Client, name: str) -> dict | None:
    """Find an existing flow by name."""
    resp = api_get(client, "/v1/cms/flows", params={"search": name, "limit": 50})
    if resp.status_code != 200:
        print(f"  Warning: Failed to list flows: {resp.status_code}")
        return None
    for flow in resp.json().get("data", []):
        if flow["name"] == name:
            return flow
    return None


def deploy_flow(
    client: httpx.Client, config: dict, theme_id: str | None, dry_run: bool
) -> str | None:
    """Create or update a flow. Returns the flow ID."""
    flow_name = config["name"]
    existing = find_existing_flow(client, flow_name)

    flow_data = config.get("flow_data", {})
    if theme_id:
        flow_data["theme_id"] = theme_id

    nodes = flow_data.get("nodes", [])
    connections = flow_data.get("connections", [])

    flow_body = {
        "name": config["name"],
        "description": config.get("description"),
        "version": config.get("version", "1.0.0"),
        "entry_node_id": config.get("entry_node_id"),
        "flow_data": flow_data,
        "info": {"seed_key": config.get("seed_key")},
        "visibility": (config.get("visibility") or "wriveted").upper(),
        "is_published": True,
        "is_active": True,
    }

    if existing:
        flow_id = existing["id"]
        print(f"  Updating existing flow: {config['name']} (id={flow_id})")
        print(f"  {len(nodes)} nodes, {len(connections)} connections")
        if dry_run:
            return flow_id
        # PUT replaces flow_data atomically (nodes + connections)
        resp = api_put(client, f"/v1/cms/flows/{flow_id}", flow_body)
        if resp.status_code != 200:
            print(f"  Error updating flow: {resp.status_code} {resp.text[:200]}")
            sys.exit(1)
        # Publish after update
        api_put(client, f"/v1/cms/flows/{flow_id}", {"publish": True})
        print(f"  Updated and published flow: {flow_id}")
        return flow_id
    else:
        print(f"  Creating flow: {config['name']} ({len(nodes)} nodes, {len(connections)} connections)")
        if dry_run:
            return None
        resp = api_post(client, "/v1/cms/flows", flow_body)
        if resp.status_code != 201:
            print(f"  Error creating flow: {resp.status_code} {resp.text[:200]}")
            sys.exit(1)
        flow_id = resp.json()["id"]
        # Publish after create
        api_put(client, f"/v1/cms/flows/{flow_id}", {"publish": True})
        print(f"  Created and published flow: {flow_id}")
        return flow_id


# ── Sub-Flows & Composite Resolution ─────────────────────────────────────


def deploy_sub_flows(client: httpx.Client, dry_run: bool) -> dict[str, str]:
    """Deploy sub-flows and return a mapping of seed_key → flow_id."""
    seed_key_to_id: dict[str, str] = {}
    for sub in SUB_FLOWS:
        fixture_path = FIXTURES_DIR / sub["file"]
        if not fixture_path.exists():
            print(f"  Error: Sub-flow fixture not found: {fixture_path}")
            sys.exit(1)
        config = json.loads(fixture_path.read_text())
        flow_id = deploy_flow(client, config, theme_id=None, dry_run=dry_run)
        if flow_id:
            seed_key_to_id[sub["seed_key"]] = flow_id
        else:
            print(f"  Warning: No ID for sub-flow '{sub['name']}' (new flow in dry-run mode)")
    return seed_key_to_id


def resolve_composite_refs(flow_config: dict, seed_key_to_id: dict[str, str]) -> dict:
    """Replace composite_flow_seed_key with composite_flow_id in node content."""
    nodes = flow_config.get("flow_data", {}).get("nodes", [])
    resolved = 0
    for node in nodes:
        if node.get("type") != "composite":
            continue
        content = node.get("content", {})
        seed_key = content.get("composite_flow_seed_key")
        if not seed_key:
            continue
        if seed_key in seed_key_to_id:
            content["composite_flow_id"] = seed_key_to_id[seed_key]
            resolved += 1
            print(f"  Resolved {seed_key} → {seed_key_to_id[seed_key]}")
        else:
            print(f"  Error: No flow ID found for seed_key '{seed_key}'")
            print("  Deploy would produce a broken flow. Aborting.")
            sys.exit(1)
    if resolved:
        print(f"  {resolved} composite reference(s) resolved")
    return flow_config


# ── CMS Questions ─────────────────────────────────────────────────────────


def count_existing_questions(client: httpx.Client) -> tuple[int, list[str]]:
    """Count existing huey-preference questions and collect their IDs."""
    ids = []
    total = 0
    skip = 0
    while True:
        resp = api_get(
            client,
            "/v1/cms/content",
            params={"tags": "huey-preference", "limit": 100, "skip": skip},
        )
        if resp.status_code != 200:
            print(f"  Warning: Failed to list content: {resp.status_code}")
            return 0, []
        data = resp.json()
        items = data.get("data", [])
        total = data.get("pagination", {}).get("total", len(items))
        ids.extend(item["id"] for item in items)
        if len(ids) >= total or not items:
            break
        skip += len(items)
    return total, ids


def deploy_questions(client: httpx.Client, dry_run: bool) -> int:
    """Parse questions from CSV and deploy via API."""
    csv_path = find_csv()
    if csv_path is None:
        print("  No AI-Questions.csv found — skipping question seeding")
        print("  (Place the CSV at AI-Questions.csv or scripts/AI-Questions.csv)")
        return 0

    # Import the CSV parser (no DB imports needed)
    sys.path.insert(0, str(SCRIPTS_DIR.parent))
    from scripts.migrate_airtable_questions import load_questions_from_csv

    questions = load_questions_from_csv(csv_path)
    if not questions:
        print("  No valid questions parsed from CSV")
        return 0

    print(f"  Parsed {len(questions)} questions from {csv_path.name}")
    if dry_run:
        return len(questions)

    # Delete existing airtable-sourced questions
    existing_count, existing_ids = count_existing_questions(client)
    if existing_ids:
        print(f"  Deleting {len(existing_ids)} existing questions")
        resp = api_delete(client, "/v1/cms/content/bulk", {"content_ids": existing_ids})
        if resp.status_code != 200:
            print(f"  Warning: Bulk delete returned {resp.status_code}: {resp.text[:200]}")

    # Create questions one by one
    created = 0
    errors = 0
    for q in questions:
        body = {
            "type": q["type"].value,
            "content": q["content"],
            "tags": q["tags"],
            "is_active": q["is_active"],
            "status": q["status"].value if hasattr(q["status"], "value") else q["status"],
            "visibility": q["visibility"].value if hasattr(q["visibility"], "value") else q["visibility"],
            "info": q["info"],
        }
        resp = api_post(client, "/v1/cms/content", body)
        if resp.status_code == 201:
            created += 1
        else:
            errors += 1
            if errors <= 3:
                print(f"  Warning: Failed to create question: {resp.status_code} {resp.text[:100]}")

    if errors > 3:
        print(f"  ... and {errors - 3} more errors")

    return created


# ── Main ──────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Deploy Huey Bookbot flow via the Wriveted API"
    )
    parser.add_argument(
        "--api",
        required=True,
        help="API base URL (e.g. https://api.wriveted.com or http://localhost:8000)",
    )
    parser.add_argument(
        "--token",
        required=True,
        help="Admin JWT bearer token",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print actions without making changes")
    parser.add_argument("--skip-questions", action="store_true", help="Skip CMS question seeding")
    parser.add_argument("--skip-subflows", action="store_true", help="Skip sub-flow deployment (use existing sub-flows)")
    parser.add_argument("--flow-only", action="store_true", help="Only deploy the flow (skip theme, sub-flows, and questions)")
    args = parser.parse_args()

    base_url = args.api.rstrip("/")
    print(f"Target API: {base_url}")
    if args.dry_run:
        print("DRY RUN — no changes will be made\n")

    client = httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {args.token}"},
        timeout=60.0,
    )

    # Verify connectivity
    try:
        resp = client.get("/v1/cms/flows", params={"limit": 1})
        if resp.status_code == 401:
            print("Error: Authentication failed. Check your --token.")
            sys.exit(1)
        if resp.status_code == 403:
            print("Error: Insufficient permissions. Token must be for a WRIVETED admin.")
            sys.exit(1)
        if resp.status_code >= 500:
            print(f"Error: API returned {resp.status_code}. Is the server healthy?")
            sys.exit(1)
    except httpx.ConnectError:
        print(f"Error: Cannot connect to {base_url}")
        sys.exit(1)

    # [1/4] Theme
    theme_id = None
    if not args.flow_only:
        print("\n[1/4] Theme")
        theme_config = load_theme_config()
        theme_id = deploy_theme(client, theme_config, args.dry_run)
    else:
        print("\n[1/4] Theme — skipped (--flow-only)")

    # [2/4] Sub-Flows
    seed_key_to_id: dict[str, str] = {}
    if args.flow_only or args.skip_subflows:
        print("\n[2/4] Sub-Flows — skipped")
        # Look up existing sub-flows so we can still resolve composite refs
        for sub in SUB_FLOWS:
            existing = find_existing_flow(client, sub["name"])
            if existing:
                seed_key_to_id[sub["seed_key"]] = existing["id"]
                print(f"  Found existing: {sub['name']} (id={existing['id']})")
            else:
                print(f"  Warning: Sub-flow '{sub['name']}' not found — composite refs won't resolve")
    else:
        print("\n[2/4] Sub-Flows")
        seed_key_to_id = deploy_sub_flows(client, args.dry_run)

    # [3/4] Main Flow
    print("\n[3/4] Main Flow")
    flow_config = load_flow_config()
    has_composite = any(
        n.get("type") == "composite" and n.get("content", {}).get("composite_flow_seed_key")
        for n in flow_config.get("flow_data", {}).get("nodes", [])
    )
    if has_composite:
        if not seed_key_to_id:
            print("  Error: Flow has composite nodes but no sub-flow IDs available")
            print("  Deploy would produce a broken flow. Aborting.")
            sys.exit(1)
        resolve_composite_refs(flow_config, seed_key_to_id)
    flow_id = deploy_flow(client, flow_config, theme_id, args.dry_run)

    # [4/4] Questions
    if args.flow_only or args.skip_questions:
        print("\n[4/4] CMS Questions — skipped")
    else:
        print("\n[4/4] CMS Questions")
        existing_count, _ = count_existing_questions(client)
        if existing_count > 0:
            print(f"  {existing_count} huey-preference questions already exist")
        count = deploy_questions(client, args.dry_run)
        if count:
            print(f"  Created {count} questions")

    if args.dry_run:
        print("\nDry run complete — no changes made")
    else:
        print("\nDeployed successfully!")
        if flow_id:
            print(f"  Flow ID: {flow_id}")
            print(f"  URL: {base_url}/v1/cms/flows/{flow_id}")

    client.close()


if __name__ == "__main__":
    main()
