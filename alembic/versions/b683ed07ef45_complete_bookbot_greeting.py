"""Complete the seeded Bookbot greeting prompt.

Revision ID: b683ed07ef45
Revises: a572dc96de34
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "b683ed07ef45"
down_revision = "a572dc96de34"
branch_labels = None
depends_on = None


def complete_prompt(content):
    question = content.get("question")
    if isinstance(question, dict) and question.get("text") == "":
        return {
            **content,
            "question": {**question, "text": "Ready to find your next book?"},
        }
    return content


def upgrade():
    connection = op.get_bind()
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    flows = (
        connection.execute(
            sa.text("""
        SELECT id, flow_data FROM flow_definitions
        WHERE info->>'seed_key' = 'huey-bookbot' FOR UPDATE
    """)
        )
        .mappings()
        .all()
    )
    for flow in flows:
        nodes = (
            connection.execute(
                sa.text("""
            SELECT id, content FROM flow_nodes
            WHERE flow_id=:flow AND node_id='greeting_response'
            AND node_type='QUESTION' FOR UPDATE
        """),
                {"flow": flow["id"]},
            )
            .mappings()
            .all()
        )
        for node in nodes:
            original = node["content"] or {}
            updated = complete_prompt(original)
            if updated != original:
                connection.execute(
                    sa.text("""
                    UPDATE flow_nodes SET content=CAST(:content AS jsonb), updated_at=now()
                    WHERE id=:id
                """),
                    {"content": json.dumps(updated), "id": node["id"]},
                )
        data = flow["flow_data"] or {}
        changed = False
        for node in data.get("nodes", []):
            if (
                node.get("id") == "greeting_response"
                and node.get("type", "").lower() == "question"
            ):
                original = node.get("content") or {}
                updated = complete_prompt(original)
                if updated != original:
                    node["content"] = updated
                    changed = True
        if changed:
            connection.execute(
                sa.text("""
                UPDATE flow_definitions SET flow_data=CAST(:data AS jsonb), updated_at=now()
                WHERE id=:id
            """),
                {"data": json.dumps(data), "id": flow["id"]},
            )


def downgrade():
    # The complete prompt remains compatible with the previous runtime.
    pass
