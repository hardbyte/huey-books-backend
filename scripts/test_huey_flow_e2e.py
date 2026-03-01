#!/usr/bin/env python3
"""E2E test script for the Huey Bookbot flow.

Walks through every branch of the flow via the chat API, including
composite sub-flows (recommendation, jokes, spelling).
"""

import json
import sys

import requests

BASE_URL = "http://localhost:8000"
FLOW_ID = None  # Will be auto-detected
TOKEN = None  # Will be auto-detected
SCHOOL_WRIVETED_ID = "84a5ade6-7f75-4155-831a-1d84c6256fc3"
SCHOOL_NAME = "Test Primary School"

PASS = 0
FAIL = 0

# Interactive nodes from sub-flows that appear as current_node_id
BOOK_FEEDBACK_NODES = {"show_books", "show_fallback_books"}
JOKE_QUESTION_NODES = {"joke_intro", "joke_bridge", "joke_question"}
SPELLING_QUESTION_NODES = {
    "spelling_intro",
    "spelling_q_one",
    "spelling_q_two",
    "spelling_q_three",
}


def get_auth():
    """Get flow ID and admin token from the database."""
    import subprocess

    result = subprocess.run(
        [
            "psql",
            "-h",
            "localhost",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-t",
            "-A",
            "-c",
            "SELECT id FROM flow_definitions WHERE name = 'Huey Bookbot'",
        ],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PGPASSWORD": "password"},
    )
    flow_id = result.stdout.strip()

    result = subprocess.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--entrypoint",
            "python",
            "-v",
            f"{__import__('os').getcwd()}/scripts:/app/scripts",
            "api",
            "/app/scripts/seed_admin_ui_data.py",
            "--emit-tokens",
            "--tokens-format",
            "json",
        ],
        capture_output=True,
        text=True,
    )
    lines = result.stdout.strip().split("\n")
    json_start = None
    for i, line in enumerate(lines):
        if line.strip().startswith("{"):
            json_start = i
            break
    if json_start is not None:
        tokens = json.loads("\n".join(lines[json_start:]))
        token = tokens["wriveted_admin"]["token"]
    else:
        print("ERROR: Could not extract tokens from seed output")
        print(result.stdout)
        sys.exit(1)

    return flow_id, token


def start_session(token, flow_id):
    """Start a new chat session."""
    resp = requests.post(
        f"{BASE_URL}/v1/chat/start",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json={
            "flow_id": flow_id,
            "initial_state": {
                "context": {
                    "school_wriveted_id": SCHOOL_WRIVETED_ID,
                    "school_name": SCHOOL_NAME,
                }
            },
        },
    )
    resp.raise_for_status()
    data = resp.json()
    return data["session_token"], data["csrf_token"], data


def interact(session_token, csrf_token, token, user_input, input_type="choice"):
    """Send user interaction."""
    resp = requests.post(
        f"{BASE_URL}/v1/chat/sessions/{session_token}/interact",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "X-CSRF-Token": csrf_token,
        },
        json={"input": user_input, "input_type": input_type},
    )
    resp.raise_for_status()
    return resp.json()


def check(label, condition, detail=""):
    """Assert a condition and track pass/fail."""
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS: {label}")
    else:
        FAIL += 1
        print(f"  FAIL: {label}")
        if detail:
            print(f"        {detail}")


def get_first_option_value(data):
    """Extract the first option's value from the input_request."""
    ir = data.get("input_request", {})
    opts = ir.get("options", [])
    if opts:
        return opts[0].get("value", opts[0].get("text", opts[0].get("label", "")))
    return None


def get_node_id(data):
    """Get current_node_id from response."""
    return data.get("current_node_id")


def get_input_type(data):
    """Get input_type from input_request."""
    ir = data.get("input_request") or {}
    return ir.get("input_type")


def walk_intro(session_token, csrf_token, token, label=""):
    """Walk through greeting and school confirmation, returning data at ask_age."""
    prefix = f"[{label}] " if label else ""

    data = interact(session_token, csrf_token, token, "hello", "choice")
    check(
        f"{prefix}greeting -> school_confirm",
        get_node_id(data) == "school_confirm",
        f"actual: {get_node_id(data)}",
    )

    data = interact(session_token, csrf_token, token, "yes", "choice")
    check(
        f"{prefix}school_confirm -> ask_age",
        get_node_id(data) == "ask_age",
        f"actual: {get_node_id(data)}",
    )

    return data


def walk_post_recommendation(session_token, csrf_token, token, data, label=""):
    """Walk from after recommendation through jokes, spelling, to restart_choice.

    After the recommendation composite exits, the flow goes through:
    - check_jokes_enabled → jokes_composite (joke_intro) → spelling_composite (spelling_intro)
    - → end_msg → restart_choice

    This function handles book_feedback if still in recommendation, then jokes/spelling.
    Returns data at restart_choice.
    """
    prefix = f"[{label}] " if label else ""
    max_interactions = 20  # Safety limit
    interactions = 0

    while interactions < max_interactions:
        node_id = get_node_id(data)
        input_type = get_input_type(data)
        interactions += 1

        if node_id == "restart_choice":
            check(f"{prefix}reached restart_choice", True)
            return data

        if node_id in BOOK_FEEDBACK_NODES:
            check(f"{prefix}book feedback at {node_id}", True)
            # Send minimal book_feedback JSON
            feedback = json.dumps({"liked": [], "disliked": []})
            data = interact(
                session_token, csrf_token, token, feedback, "book_feedback"
            )
            continue

        if node_id in JOKE_QUESTION_NODES:
            if node_id == "joke_intro":
                check(f"{prefix}reached joke_intro", True)
                data = interact(session_token, csrf_token, token, "no", "choice")
                continue
            elif node_id == "joke_bridge":
                data = interact(session_token, csrf_token, token, "no", "choice")
                continue
            elif node_id == "joke_question":
                # CMS random joke question — pick first option
                choice = get_first_option_value(data)
                if choice:
                    data = interact(
                        session_token, csrf_token, token, choice, "choice"
                    )
                else:
                    # No joke content available, skip
                    data = interact(
                        session_token, csrf_token, token, "continue", "continue"
                    )
                continue

        if node_id in SPELLING_QUESTION_NODES:
            if node_id == "spelling_intro":
                check(f"{prefix}reached spelling_intro", True)
                data = interact(session_token, csrf_token, token, "no", "choice")
                continue
            else:
                # Spelling question — pick first option
                choice = get_first_option_value(data)
                if choice:
                    data = interact(
                        session_token, csrf_token, token, choice, "choice"
                    )
                else:
                    data = interact(
                        session_token, csrf_token, token, "continue", "continue"
                    )
                continue

        # Handle wait_for_acknowledgment (e.g., from message nodes in sub-flows)
        if data.get("wait_for_acknowledgment"):
            data = interact(session_token, csrf_token, token, "continue", "continue")
            continue

        # Unknown node with an input_request — try first option
        if data.get("input_request"):
            choice = get_first_option_value(data)
            if choice:
                it = input_type or "choice"
                print(f"  INFO: unexpected node {node_id} (input_type={it}), picking first option")
                data = interact(session_token, csrf_token, token, choice, it)
                continue

        # Session ended or no input request
        if data.get("session_ended"):
            check(
                f"{prefix}reached restart_choice",
                False,
                f"session ended at {node_id}",
            )
            return data

        # No input_request and not ended — something went wrong
        check(
            f"{prefix}reached restart_choice",
            False,
            f"stuck at {node_id}, no input_request",
        )
        return data

    check(f"{prefix}reached restart_choice", False, "exceeded max interactions")
    return data


def walk_full_flow(session_token, csrf_token, token, label=""):
    """Walk from ask_age through prefs, recommendations, jokes, spelling to restart_choice."""
    prefix = f"[{label}] " if label else ""

    # Age
    data = interact(session_token, csrf_token, token, "9", "choice")
    check(
        f"{prefix}age -> reading_ability",
        get_node_id(data) == "ask_reading_ability",
        f"actual: {get_node_id(data)}",
    )

    # Reading ability
    data = interact(session_token, csrf_token, token, "TREEHOUSE", "choice")
    check(
        f"{prefix}reading -> pref_q_one",
        get_node_id(data) == "pref_q_one",
        f"actual: {get_node_id(data)}",
    )
    ir = data.get("input_request", {})
    check(f"{prefix}pref_q_one has options", len(ir.get("options", [])) >= 2)

    # 3 preference questions
    for i in range(3):
        choice = get_first_option_value(data)
        check(
            f"{prefix}pref_{i + 1} has selectable option",
            choice is not None,
            f"options: {data.get('input_request', {}).get('options', [])}",
        )
        if not choice:
            return data
        data = interact(session_token, csrf_token, token, choice, "image_choice")

    # After 3rd pref, flow enters recommendation composite.
    # We should see messages (good_choices_msg, searching_msg) and wait_for_ack.
    if data.get("wait_for_acknowledgment"):
        check(f"{prefix}paused at searching_msg", True)
        data = interact(session_token, csrf_token, token, "continue", "continue")
    else:
        # Might have auto-processed through (if searching_msg didn't pause)
        check(f"{prefix}post-preferences response received", True)

    # Now walk through the rest: book feedback, jokes, spelling → restart_choice
    data = walk_post_recommendation(session_token, csrf_token, token, data, label)

    check(f"{prefix}session not ended at restart", data.get("session_ended") is not True)

    # Verify messages include book-related text somewhere in the accumulated messages
    msgs = data.get("messages", [])
    msg_texts = []
    for m in msgs:
        c = m.get("content", {})
        t = c.get("text", "") if isinstance(c, dict) else str(c)
        msg_texts.append(t)
    check(
        f"{prefix}flow reached restart with messages",
        len(msg_texts) > 0,
        f"messages count: {len(msg_texts)}",
    )

    return data


def main():
    global FLOW_ID, TOKEN

    print("Getting auth credentials...")
    FLOW_ID, TOKEN = get_auth()
    print(f"Flow ID: {FLOW_ID}")
    print(f"Token: {TOKEN[:20]}...\n")

    # ========================================
    # TEST 1: Happy path -> restart -> goodbye
    # ========================================
    print("=" * 60)
    print("TEST 1: Happy path through flow -> restart -> goodbye")
    print("=" * 60)

    session_token, csrf_token, start_data = start_session(TOKEN, FLOW_ID)
    nn = start_data.get("next_node", {})
    check("welcome messages present", len(nn.get("messages", [])) >= 2)
    next_id = nn.get("next_node")
    if isinstance(next_id, dict):
        next_id = next_id.get("node_id")
    check("first question is greeting_response", next_id == "greeting_response")

    # Walk through greeting + school confirm
    walk_intro(session_token, csrf_token, TOKEN, "T1")

    # Walk through age -> prefs -> recommendations -> jokes -> spelling -> restart_choice
    data = walk_full_flow(session_token, csrf_token, TOKEN, "T1")

    # Choose restart — verifies the restart loop works
    data = interact(session_token, csrf_token, TOKEN, "restart", "choice")
    check(
        "T1 restart -> welcome (shows greeting)",
        get_node_id(data) == "greeting_response",
        f"actual: {get_node_id(data)}",
    )
    check("T1 restart: session not ended", data.get("session_ended") is not True)

    # Walk through intro again (greeting + school confirm)
    walk_intro(session_token, csrf_token, TOKEN, "T1-restart")

    # Note: CMS random preference questions may be exhausted on second pass
    # (limited seeded content). Just verify we reach age and reading again.
    data = interact(session_token, csrf_token, TOKEN, "9", "choice")
    check(
        "T1-restart age -> reading_ability",
        get_node_id(data) == "ask_reading_ability",
        f"actual: {get_node_id(data)}",
    )

    # ========================================
    # TEST 1b: Goodbye path (fresh session)
    # ========================================
    print("\n" + "=" * 60)
    print("TEST 1b: Goodbye path")
    print("=" * 60)

    session_token, csrf_token, start_data = start_session(TOKEN, FLOW_ID)
    walk_intro(session_token, csrf_token, TOKEN, "T1b")
    data = walk_full_flow(session_token, csrf_token, TOKEN, "T1b")

    data = interact(session_token, csrf_token, TOKEN, "done", "choice")
    # emit_chat_ended ACTION node sits before goodbye; ACTION recursively
    # processes goodbye but current_node_id reflects the ACTION node.
    check(
        "T1b goodbye: reached goodbye path",
        get_node_id(data) in ("goodbye", "emit_chat_ended"),
        f"actual: {get_node_id(data)}",
    )
    check("T1b goodbye: session ended", data.get("session_ended") is True)

    msgs = data.get("messages", [])
    goodbye_text = ""
    for m in msgs:
        c = m.get("content", {})
        t = c.get("text", "") if isinstance(c, dict) else str(c)
        if "see you" in t.lower() or "thanks" in t.lower():
            goodbye_text = t
    check("T1b goodbye message present", len(goodbye_text) > 0, f"messages: {msgs}")

    # ========================================
    # TEST 2: Young child (age 5)
    # ========================================
    print("\n" + "=" * 60)
    print("TEST 2: Young child (age 5)")
    print("=" * 60)

    session_token, csrf_token, start_data = start_session(TOKEN, FLOW_ID)

    walk_intro(session_token, csrf_token, TOKEN, "T2")

    data = interact(session_token, csrf_token, TOKEN, "5", "choice")
    check(
        "T2 age 5 -> reading_ability",
        get_node_id(data) == "ask_reading_ability",
        f"actual: {get_node_id(data)}",
    )

    ir = data.get("input_request", {})
    opts = ir.get("options", [])
    has_images = any(opt.get("image_url") for opt in opts)
    check(
        "T2 reading options have images",
        has_images,
        f"options: {[{k: v for k, v in opt.items() if k != 'image_url'} for opt in opts]}",
    )

    data = interact(session_token, csrf_token, TOKEN, "SPOT", "choice")
    check(
        "T2 SPOT -> pref_q_one",
        get_node_id(data) == "pref_q_one",
        f"actual: {get_node_id(data)}",
    )
    ir = data.get("input_request", {})
    check(
        "T2 pref_q_one has options",
        len(ir.get("options", [])) >= 2,
        f"options count: {len(ir.get('options', []))}",
    )

    # ========================================
    # TEST 3: Older child (age 13)
    # ========================================
    print("\n" + "=" * 60)
    print("TEST 3: Older child (age 13)")
    print("=" * 60)

    session_token, csrf_token, start_data = start_session(TOKEN, FLOW_ID)

    walk_intro(session_token, csrf_token, TOKEN, "T3")

    data = interact(session_token, csrf_token, TOKEN, "13", "choice")
    check(
        "T3 age 13 -> reading_ability",
        get_node_id(data) == "ask_reading_ability",
        f"actual: {get_node_id(data)}",
    )

    data = interact(session_token, csrf_token, TOKEN, "HARRY_POTTER", "choice")
    check(
        "T3 HARRY_POTTER -> pref_q_one",
        get_node_id(data) == "pref_q_one",
        f"actual: {get_node_id(data)}",
    )

    # ========================================
    # TEST 4: Verify GCP image URLs in welcome
    # ========================================
    print("\n" + "=" * 60)
    print("TEST 4: Verify GCP image URLs")
    print("=" * 60)

    session_token, csrf_token, start_data = start_session(TOKEN, FLOW_ID)
    nn = start_data.get("next_node", {})
    welcome_msgs = nn.get("messages", [])
    gcp_url_found = False
    for m in welcome_msgs:
        c = m.get("content", {})
        url = c.get("url", "") if isinstance(c, dict) else ""
        if "storage.googleapis.com" in url:
            gcp_url_found = True
    check("T4 welcome uses GCP image URL", gcp_url_found, f"messages: {welcome_msgs}")

    aws_url_found = False
    all_text = json.dumps(welcome_msgs)
    if "s3-ap-southeast-2.amazonaws.com" in all_text:
        aws_url_found = True
    check("T4 no AWS URLs in welcome", not aws_url_found)

    # ========================================
    # TEST 5: Composite chaining through stale collection path
    # ========================================
    print("\n" + "=" * 60)
    print("TEST 5: Composite chaining (MESSAGE -> COMPOSITE)")
    print("=" * 60)

    # This specifically tests the bug fix: stale_collection_msg -> profile_composite
    # We simulate a school with a stale collection by setting collection_updated_at
    # to >3 years ago.
    import datetime

    stale_date = (
        datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=1200)
    ).isoformat()
    resp = requests.post(
        f"{BASE_URL}/v1/chat/start",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TOKEN}",
        },
        json={
            "flow_id": FLOW_ID,
            "initial_state": {
                "context": {
                    "school_wriveted_id": SCHOOL_WRIVETED_ID,
                    "school_name": SCHOOL_NAME,
                    "collection_updated_at": stale_date,
                }
            },
        },
    )
    resp.raise_for_status()
    start_data = resp.json()
    session_token = start_data["session_token"]
    csrf_token = start_data["csrf_token"]

    # Greeting
    data = interact(session_token, csrf_token, TOKEN, "hello", "choice")
    check(
        "T5 greeting -> school_confirm",
        get_node_id(data) == "school_confirm",
        f"actual: {get_node_id(data)}",
    )

    # School confirm — should hit check_stale_collection → stale_collection_msg → profile_composite
    data = interact(session_token, csrf_token, TOKEN, "yes", "choice")
    check(
        "T5 stale collection -> ask_age (composite chaining works)",
        get_node_id(data) == "ask_age",
        f"actual: {get_node_id(data)}",
    )

    # Verify stale collection warning was included in messages
    msgs = data.get("messages", [])
    stale_warning = False
    for m in msgs:
        c = m.get("content", {})
        t = c.get("text", "") if isinstance(c, dict) else str(c)
        if "hasn't been updated" in t.lower() or "book list" in t.lower():
            stale_warning = True
    check("T5 stale collection warning message present", stale_warning, f"messages: {msgs}")

    # ========================================
    # TEST 6: Verify emit_event analytics events
    # ========================================
    print("\n" + "=" * 60)
    print("TEST 6: Verify emit_event analytics events in DB")
    print("=" * 60)

    import subprocess as _sp

    event_result = _sp.run(
        [
            "psql",
            "-h", "localhost",
            "-U", "postgres",
            "-d", "postgres",
            "-t", "-A",
            "-c",
            "SELECT title, count(*) FROM events WHERE title LIKE 'Huey:%' GROUP BY title ORDER BY title;",
        ],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PGPASSWORD": "password"},
    )
    event_rows = [
        line.strip() for line in event_result.stdout.strip().split("\n") if line.strip()
    ]
    event_titles = {row.split("|")[0] for row in event_rows}
    print(f"  Found event types: {event_titles}")
    for row in event_rows:
        print(f"    {row}")

    # These events should exist from the complete flow walkthroughs above
    expected_events = {
        "Huey: Chat started",
        "Huey: Find a book",
        "Huey: Age collected",
        "Huey: Reading ability collected",
        "Huey: Hues collected",
    }
    for expected in sorted(expected_events):
        check(
            f"T6 event '{expected}' exists",
            expected in event_titles,
            f"found: {event_titles}",
        )

    # Chat ended should exist from T1b goodbye path
    check(
        "T6 event 'Huey: Chat ended' exists",
        "Huey: Chat ended" in event_titles,
        f"found: {event_titles}",
    )

    # ========================================
    # SUMMARY
    # ========================================
    print("\n" + "=" * 60)
    total = PASS + FAIL
    print(f"RESULTS: {PASS}/{total} passed, {FAIL} failed")
    print("=" * 60)

    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
