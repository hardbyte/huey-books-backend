from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.repositories.kpi_usage import read_usage
from app.repositories.school_insights import query_parameters, read_engagement


@pytest.mark.asyncio
async def test_usage_cohort_distinct_sites_and_milestones(async_session):
    await async_session.execute(
        text(
            "CREATE TEMP TABLE conversation_sessions (id uuid, school_id uuid, started_at timestamp) ON COMMIT DROP"
        )
    )
    await async_session.execute(
        text(
            "CREATE TEMP TABLE conversation_history (session_id uuid, interaction_type text, content jsonb, created_at timestamp) ON COMMIT DROP"
        )
    )
    start, end = datetime(2026, 8, 3), datetime(2026, 8, 17)
    school = uuid4()
    assert (await read_usage(async_session, start, end)) == [{
        "week": None, "sessions": 0, "reached_recommendations": 0,
        "active_sites": 0, "unattributed_sessions": 0,
    }]
    for index, (school_id, started) in enumerate(
        [
            (school, start),
            (school, datetime(2026, 8, 10)),
            (None, start),
            (uuid4(), start),
            (school, end),
            (school, datetime(2026, 8, 2)),
        ]
    ):
        session_id = uuid4()
        await async_session.execute(
            text("INSERT INTO conversation_sessions VALUES (:id, :school, :started)"),
            {"id": session_id, "school": school_id, "started": started},
        )
        for _ in range(2):
            await async_session.execute(
                text(
                    """INSERT INTO conversation_history VALUES
                (:id, :kind, '{"input_type":"book_feedback"}', :created)"""
                ),
                {
                    "id": session_id,
                    "kind": "INPUT" if index == 3 else "MESSAGE",
                    "created": started,
                },
            )
        if index == 3:
            await async_session.execute(text(
                """INSERT INTO conversation_history VALUES
                (:id, 'MESSAGE', '{"input_type":"book_feedback"}', :created)"""
            ), {"id": session_id, "created": end})
    rows = await read_usage(async_session, start, end)
    assert rows[-1] == {
        "week": None,
        "sessions": 4,
        "reached_recommendations": 3,
        "active_sites": 1,
        "unattributed_sessions": 1,
    }
    assert [row["active_sites"] for row in rows[:-1]] == [1, 1]
    assert sum(row["sessions"] for row in rows[:-1]) == rows[-1]["sessions"]
    legacy_id = uuid4()
    await async_session.execute(text(
        "INSERT INTO conversation_sessions VALUES (:id, :school, :started)"
    ), {"id": legacy_id, "school": school, "started": start})
    for content in [
        '{"input_type":"carousel"}',
        '{"messages":[{"type":"text","content":{"text":"book_list"}}]}',
    ]:
        await async_session.execute(text(
            "INSERT INTO conversation_history VALUES (:id, 'MESSAGE', CAST(:content AS jsonb), :created)"
        ), {"id": legacy_id, "content": content, "created": start})
    assert (await read_usage(async_session, start, end))[-1]["reached_recommendations"] == 3
    await async_session.execute(text(
        """INSERT INTO conversation_history VALUES
        (:id, 'MESSAGE', '{"messages":[{"type":"book_list","content":{"books":[]}}]}', :created)"""
    ), {"id": legacy_id, "created": start})
    assert (await read_usage(async_session, start, end))[-1]["reached_recommendations"] == 4
    await async_session.execute(text(
        "ALTER TABLE conversation_sessions ADD COLUMN state jsonb DEFAULT '{}'"
    ))
    await async_session.execute(text(
        "ALTER TABLE conversation_history ADD COLUMN id uuid DEFAULT gen_random_uuid()"
    ))
    site_rows = await read_engagement(async_session, query_parameters(school, start, end))
    assert sum(row["reached"] for row in site_rows) == 3


@pytest.mark.asyncio
async def test_usage_endpoints_authorization_and_shape(
    async_client,
    async_client_authenticated_as_wriveted_user,
    test_student_user_account_headers,
    test_schooladmin_account_headers,
):
    for path in ("/v1/kpis/usage", "/v1/kpis/delivery"):
        for headers in (
            test_student_user_account_headers,
            test_schooladmin_account_headers,
        ):
            assert (await async_client.get(path, headers=headers)).status_code == 403
        response = await async_client_authenticated_as_wriveted_user.get(path)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
    response = await async_client_authenticated_as_wriveted_user.get(
        "/v1/kpis/usage?weeks=4"
    )
    body = response.json()
    assert len(body["points"]) == 4
    assert [point["partial"] for point in body["points"]] == [False, False, False, True]
    assert body["totals"]["reached_recommendations"] <= body["totals"]["sessions"]
    assert (
        await async_client_authenticated_as_wriveted_user.get("/v1/kpis/usage?weeks=27")
    ).status_code == 422


@pytest.mark.asyncio
async def test_usage_requires_auth(async_client):
    for path in ("/v1/kpis/usage", "/v1/kpis/delivery"):
        assert (await async_client.get(path)).status_code in (401, 403)


def test_view_as_cannot_read_global_usage(
    client, test_wrivetedadmin_account_headers, test_schooladmin_account,
):
    response = client.post(
        f"/v1/auth/view-as/{test_schooladmin_account.id}",
        headers=test_wrivetedadmin_account_headers,
    )
    assert response.status_code == 200
    headers = {**test_wrivetedadmin_account_headers, "X-View-As": response.json()["context"]}
    for path in ("/v1/kpis/usage", "/v1/kpis/delivery"):
        assert client.get(path, headers=headers).status_code == 403
