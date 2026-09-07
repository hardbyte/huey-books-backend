from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.repositories.school_insights import (
    query_parameters,
    read_collection,
    read_engagement,
    read_interests,
    read_snapshot,
)


@pytest.mark.asyncio
async def test_school_insights_http_contract(
    async_client,
    test_school,
    test_schooladmin_account_headers,
    test_student_user_account_headers,
):
    path = f"/v1/school/{test_school.wriveted_identifier}/insights"
    response = await async_client.get(path, headers=test_schooladmin_account_headers)
    assert response.status_code == 200, response.text
    assert response.json()["school_id"] == str(test_school.wriveted_identifier)
    assert response.headers["cache-control"] == "private, no-store"
    assert (await async_client.get(path)).status_code in (401, 403)
    assert (
        await async_client.get(path, headers=test_student_user_account_headers)
    ).status_code == 403


@pytest.mark.asyncio
async def test_school_aggregate_queries(async_session):
    # Temporary tables shadow production schema within this test transaction.
    definitions = {
        "conversation_sessions": "id uuid, started_at timestamp, state jsonb, school_id uuid",
        "conversation_history": "session_id uuid, interaction_type text, content jsonb, created_at timestamp, id uuid DEFAULT gen_random_uuid()",
        "collections": "id uuid, school_id uuid",
        "collection_items": "collection_id uuid, edition_isbn text",
        "editions": "isbn text, work_id int",
        "labelsets": "id int, work_id int, checked boolean, min_age int, max_age int",
        "labelset_hue_association": "labelset_id int, hue_id int",
        "labelset_reading_ability_association": "labelset_id int",
        "hues": "id int, key text, name text",
    }
    for table, columns in definitions.items():
        await async_session.execute(
            text(f"CREATE TEMP TABLE {table} ({columns}) ON COMMIT DROP")
        )
    school, other, collection = uuid4(), uuid4(), uuid4()
    start, end = datetime(2026, 8, 3), datetime(2026, 8, 31)
    import json

    for index in range(12):
        session_id = uuid4()
        state = {
            "context": {"school_wriveted_id": str(school if index < 10 else other)},
            "temp": {
                "book_feedback": {"liked": ["a"], "disliked": None, "read": "malformed"}
            },
            "user": {"hue_keys": ["funny", "funny"]},
        }
        await async_session.execute(
            text(
                "INSERT INTO conversation_sessions VALUES (:id, :started, CAST(:state AS jsonb), :school)"
            ),
            {
                "id": session_id,
                "started": start,
                "state": json.dumps(state),
                "school": school if index < 10 else other,
            },
        )
        if index < 5 or index >= 10:
            for interaction in ("MESSAGE", "MESSAGE", "INPUT"):
                await async_session.execute(
                    text(
                        'INSERT INTO conversation_history (session_id, interaction_type, content, created_at) VALUES (:id, :kind, \'{"input_type":"book_feedback", "validated_feedback":{"liked":["9780140328721"],"disliked":[],"read":[]}}\', :created)'
                    ),
                    {
                        "id": session_id,
                        "kind": interaction,
                        "created": start + timedelta(minutes=1),
                    },
                )
    # Future and malformed/unattributed sessions must not enter the cohort.
    await async_session.execute(
        text(
            "INSERT INTO conversation_sessions VALUES (:id, :started, :state, :school)"
        ),
        {
            "id": uuid4(),
            "school": school,
            "started": end,
            "state": json.dumps({"context": {"school_wriveted_id": str(school)}}),
        },
    )
    parameters = query_parameters(school, start, end)
    rows = await read_engagement(async_session, parameters)
    assert len(rows) == 1
    assert rows[0]["sessions"] == 10
    assert rows[0]["reached"] == rows[0]["feedback"] == 5
    assert rows[0]["liked"] == rows[0]["liked_sessions"] == 5
    assert rows[0]["disliked"] == rows[0]["already_read"] == 0
    assert rows[0]["unverified_feedback"] == 0
    assert (
        await read_engagement(async_session, query_parameters(uuid4(), start, end))
        == []
    )

    await async_session.execute(
        text("INSERT INTO collections VALUES (:id, :school)"),
        {"id": collection, "school": school},
    )
    await async_session.execute(
        text(
            "INSERT INTO collection_items VALUES (:id, 'a'), (:id, 'b'), (:id, 'c'), (:id, NULL)"
        ),
        {"id": collection},
    )
    await async_session.execute(
        text("INSERT INTO editions VALUES ('a', 1), ('b', 1), ('c', 2)")
    )
    await async_session.execute(
        text(
            "INSERT INTO labelsets VALUES (1, 1, true, 5, 8), (2, 1, false, NULL, NULL), (3, 2, true, 5, 8)"
        )
    )
    await async_session.execute(
        text("INSERT INTO labelset_hue_association VALUES (1, 1), (3, 1)")
    )
    await async_session.execute(
        text("INSERT INTO labelset_reading_ability_association VALUES (1), (3)")
    )
    await async_session.execute(text("INSERT INTO hues VALUES (1, 'funny', 'Funny')"))
    assert await read_collection(async_session, school) == {
        "works": 2,
        "labelled": 1,
        "awaiting_review": 1,
        "missing_age": 1,
        "unmatched_items": 1,
    }
    assert await read_collection(async_session, other) == {
        "works": 0,
        "labelled": 0,
        "awaiting_review": 0,
        "missing_age": 0,
        "unmatched_items": 0,
    }
    assert await read_interests(async_session, parameters) == [
        {"name": "Funny", "sessions": 10, "labelled_works": 1}
    ]
    snapshot = await read_snapshot(async_session, parameters)
    assert snapshot["engagement"] == rows
    assert snapshot["collection"] == await read_collection(async_session, school)
    assert snapshot["interests"] == await read_interests(async_session, parameters)
    assert (
        await read_interests(async_session, query_parameters(other, start, end)) == []
    )

    await async_session.execute(
        text(
            "UPDATE conversation_sessions SET state = '{}'::jsonb WHERE id = (SELECT id FROM conversation_sessions WHERE school_id = :school LIMIT 1)"
        ),
        {"school": school},
    )
    assert await read_interests(async_session, parameters) == []
    assert (await read_engagement(async_session, parameters))[0]["sessions"] == 10
    await async_session.execute(
        text("UPDATE conversation_history SET content = content - 'validated_feedback'")
    )
    assert (await read_engagement(async_session, parameters))[0][
        "unverified_feedback"
    ] == 5
