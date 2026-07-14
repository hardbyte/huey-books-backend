"""Broadcast service: audience resolution, opt-out, unsubscribe tokens.

A broadcast reaches active users in the chosen segment who have not opted out;
country/school filters apply to school-affiliated users; the unsubscribe token
round-trips and flips the opt-out flag.
"""

import uuid

from sqlalchemy import select

from app.models.educator import Educator
from app.models.event_outbox import EventOutbox
from app.models.user import UserAccountType
from app.schemas.broadcast import BroadcastAudience
from app.services import broadcast as bc

EDUCATORS = BroadcastAudience(user_types=[UserAccountType.EDUCATOR])


def _make_educator(
    session,
    school_id: int,
    *,
    is_active: bool = True,
    opted_out: bool = False,
) -> Educator:
    educator = Educator(
        name=f"Teacher {uuid.uuid4().hex[:6]}",
        email=f"teacher-{uuid.uuid4().hex[:10]}@example.com",
        is_active=is_active,
        school_id=school_id,
        marketing_opt_out=opted_out,
    )
    session.add(educator)
    session.commit()
    return educator


def test_resolve_recipients_includes_active_excludes_optout_and_inactive(
    session, test_school
):
    keep = _make_educator(session, test_school.id)
    opted_out = _make_educator(session, test_school.id, opted_out=True)
    inactive = _make_educator(session, test_school.id, is_active=False)

    ids = {u.id for u in bc.resolve_recipients(session, EDUCATORS)}

    assert keep.id in ids
    assert opted_out.id not in ids
    assert inactive.id not in ids


def test_user_type_filter_excludes_other_types(session, test_school):
    educator = _make_educator(session, test_school.id)

    parents_only = BroadcastAudience(user_types=[UserAccountType.PARENT])
    ids = {u.id for u in bc.resolve_recipients(session, parents_only)}
    assert educator.id not in ids


def test_school_scope_filters(session, test_school):
    keep = _make_educator(session, test_school.id)

    in_school = {
        u.id
        for u in bc.resolve_recipients(
            session,
            BroadcastAudience(
                user_types=[UserAccountType.EDUCATOR],
                school_id=test_school.wriveted_identifier,
            ),
        )
    }
    assert keep.id in in_school

    other_school = {
        u.id
        for u in bc.resolve_recipients(
            session,
            BroadcastAudience(
                user_types=[UserAccountType.EDUCATOR], school_id=uuid.uuid4()
            ),
        )
    }
    assert keep.id not in other_school


def test_country_scope_filters(session, test_school):
    keep = _make_educator(session, test_school.id)

    same_country = {
        u.id
        for u in bc.resolve_recipients(
            session,
            BroadcastAudience(
                user_types=[UserAccountType.EDUCATOR],
                country_code=test_school.country_code,
            ),
        )
    }
    assert keep.id in same_country

    other_country = {
        u.id
        for u in bc.resolve_recipients(
            session,
            BroadcastAudience(
                user_types=[UserAccountType.EDUCATOR], country_code="ZZZ"
            ),
        )
    }
    assert keep.id not in other_country


def test_unsubscribe_token_round_trip_and_flag(session, test_school):
    educator = _make_educator(session, test_school.id)

    token = bc.make_unsubscribe_token(educator.id)
    assert bc.verify_unsubscribe_token(token) == str(educator.id)
    assert bc.verify_unsubscribe_token("not-a-token") is None

    assert bc.unsubscribe_user(session, token) is True
    session.refresh(educator)
    assert educator.marketing_opt_out is True

    ids = {u.id for u in bc.resolve_recipients(session, EDUCATORS)}
    assert educator.id not in ids


def test_unsubscribe_with_bad_token_is_safe(session):
    assert bc.unsubscribe_user(session, "garbage") is False


def test_render_email_html_escapes_and_links():
    html = bc.render_email_html(
        "Hello <script>alert(1)</script>\n\nSecond para",
        "https://api.example.com/v1/email/unsubscribe?token=abc",
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "Second para" in html
    assert "unsubscribe?token=abc" in html


# --- API layer ---


def test_preview_requires_admin(client):
    resp = client.post("/v1/broadcast/preview", json={"user_types": ["educator"]})
    assert resp.status_code in (401, 403)


def test_send_requires_admin(client):
    resp = client.post(
        "/v1/broadcast",
        json={
            "subject": "Hi",
            "body": "Hello",
            "audience": {"user_types": ["educator"]},
        },
    )
    assert resp.status_code in (401, 403)


def test_preview_rejects_empty_user_types(client, test_wrivetedadmin_account_headers):
    resp = client.post(
        "/v1/broadcast/preview",
        headers=test_wrivetedadmin_account_headers,
        json={"user_types": []},
    )
    assert resp.status_code == 422


def test_preview_counts_recipients(
    client, test_wrivetedadmin_account_headers, session, test_school
):
    _make_educator(session, test_school.id)
    resp = client.post(
        "/v1/broadcast/preview",
        headers=test_wrivetedadmin_account_headers,
        json={
            "user_types": ["educator"],
            "school_id": str(test_school.wriveted_identifier),
        },
    )
    assert resp.status_code == 200
    assert resp.json()["recipient_count"] >= 1


def _committed_email_events(session, subject: str) -> list[EventOutbox]:
    """Outbox email rows for the given subject, as visible to a clean transaction.

    Rolling back first discards anything pending on this session, so a
    non-empty result proves the rows were actually committed.
    """
    session.rollback()
    rows = session.scalars(
        select(EventOutbox).where(EventOutbox.event_type == "email_notification")
    ).all()
    return [r for r in rows if r.payload["email_data"]["subject"] == subject]


def test_test_send_goes_to_self_only(
    client, test_wrivetedadmin_account_headers, session, monkeypatch
):
    triggered = []
    monkeypatch.setattr(
        bc,
        "queue_background_task",
        lambda endpoint, payload=None: triggered.append(endpoint),
    )
    subject = f"Test send {uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/v1/broadcast/test",
        headers=test_wrivetedadmin_account_headers,
        json={"subject": subject, "body": "Hello there"},
    )
    assert resp.status_code == 200
    assert resp.json()["queued"] == 1

    events = _committed_email_events(session, f"[Test] {subject}")
    assert len(events) == 1
    assert triggered == ["process-outbox-events"]


def test_send_broadcast_commits_outbox_rows(session, test_school, monkeypatch):
    educator = _make_educator(session, test_school.id)
    monkeypatch.setattr(
        bc, "queue_background_task", lambda endpoint, payload=None: None
    )

    subject = f"Broadcast {uuid.uuid4().hex[:8]}"
    queued = bc.send_broadcast(
        session,
        subject=subject,
        body="Hello everyone",
        audience=BroadcastAudience(
            user_types=[UserAccountType.EDUCATOR],
            school_id=test_school.wriveted_identifier,
        ),
    )
    assert queued >= 1

    events = _committed_email_events(session, subject)
    assert len(events) == queued
    assert any(e.payload["email_data"]["to_emails"] == [educator.email] for e in events)


def test_unsubscribe_get_does_not_mutate(client, session, test_school):
    educator = _make_educator(session, test_school.id)
    token = bc.make_unsubscribe_token(educator.id)

    resp = client.get(f"/v1/email/unsubscribe?token={token}")
    assert resp.status_code == 200

    session.refresh(educator)
    assert educator.marketing_opt_out is False  # GET must not unsubscribe


def test_unsubscribe_post_opts_out(client, session, test_school):
    educator = _make_educator(session, test_school.id)
    token = bc.make_unsubscribe_token(educator.id)

    resp = client.post(f"/v1/email/unsubscribe?token={token}")
    assert resp.status_code == 200

    session.refresh(educator)
    assert educator.marketing_opt_out is True


def test_unsubscribe_get_bad_token(client):
    resp = client.get("/v1/email/unsubscribe?token=garbage")
    assert resp.status_code == 400
