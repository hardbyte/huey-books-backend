"""Route empty optional CMS questions onward.

Revision ID: a572dc96de34
Revises: f461cb85cd23
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "a572dc96de34"
down_revision = "f461cb85cd23"
branch_labels = None
depends_on = None


def routes(seed):
    if seed == "huey-jokes":
        return [
            (
                "joke_question",
                "jokes_exhausted",
                "message",
                {
                    "messages": [
                        {
                            "type": "text",
                            "text": "That's all my jokes for now. Let's move on!",
                        }
                    ]
                },
                None,
            )
        ]
    return [
        (
            f"pref_q_{number}",
            f"skip_pref_{number}",
            "action",
            {
                "actions": [
                    {
                        "type": "delete_variable",
                        "variable": f"temp.pref_{number}_hue",
                    }
                ]
            },
            next_id,
        )
        for number, next_id in [
            ("one", "pref_q_two"),
            ("two", "pref_q_three"),
            ("three", "aggregate_prefs"),
        ]
    ]


def upgrade():
    connection = op.get_bind()
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    flows = (
        connection.execute(
            sa.text("""
        SELECT id, info->>'seed_key' AS seed, flow_data FROM flow_definitions
        WHERE info->>'seed_key' IN ('huey-jokes', 'huey-preferences') FOR UPDATE
    """)
        )
        .mappings()
        .all()
    )
    for flow in flows:
        data = flow["flow_data"] or {}
        nodes = {
            row["node_id"]: row
            for row in connection.execute(
                sa.text(
                    "SELECT node_id, content FROM flow_nodes WHERE flow_id=:id FOR UPDATE"
                ),
                {"id": flow["id"]},
            ).mappings()
        }
        serialized = {node["id"]: node for node in data.get("nodes", [])}
        for source, target, kind, content, next_id in routes(flow["seed"]):
            if source not in nodes or source not in serialized:
                continue
            original = nodes[source]["content"]
            expected_tag = (
                "huey-joke" if flow["seed"] == "huey-jokes" else "huey-preference"
            )
            if original.get("source") != "random" or expected_tag not in original.get(
                "source_config", {}
            ).get("tags", []):
                continue
            if original.get("on_empty"):
                continue
            if target in nodes or target in serialized:
                raise ValueError("Optional-question route target already exists")
            if next_id and (next_id not in nodes or next_id not in serialized):
                raise ValueError("Optional-question continuation is missing")
            updated = {**original, "on_empty": target}
            connection.execute(
                sa.text("""
                UPDATE flow_nodes SET content=CAST(:content AS jsonb), updated_at=now()
                WHERE flow_id=:flow AND node_id=:node
            """),
                {"content": json.dumps(updated), "flow": flow["id"], "node": source},
            )
            connection.execute(
                sa.text("""
                INSERT INTO flow_nodes (flow_id,node_id,node_type,content,info)
                VALUES (:flow,:node,CAST(:kind AS enum_flow_node_type),CAST(:content AS jsonb),CAST(:info AS jsonb))
            """),
                {
                    "flow": flow["id"],
                    "node": target,
                    "kind": kind.upper(),
                    "content": json.dumps(content),
                    "info": json.dumps({"migration": revision}),
                },
            )
            serialized[source]["content"] = updated
            data["nodes"].append(
                {
                    "id": target,
                    "type": kind,
                    "content": content,
                    "position": {"x": 500, "y": 500},
                }
            )
            if next_id:
                connection.execute(
                    sa.text("""
                    INSERT INTO flow_connections (flow_id,source_node_id,target_node_id,connection_type,info)
                    VALUES (:flow,:source,:target,'DEFAULT',CAST(:info AS jsonb))
                """),
                    {
                        "flow": flow["id"],
                        "source": target,
                        "target": next_id,
                        "info": json.dumps({"migration": revision}),
                    },
                )
                data.setdefault("connections", []).append(
                    {"source": target, "target": next_id, "type": "default"}
                )
        connection.execute(
            sa.text(
                "UPDATE flow_definitions SET flow_data=CAST(:data AS jsonb),updated_at=now() WHERE id=:id"
            ),
            {"data": json.dumps(data), "id": flow["id"]},
        )


def downgrade():
    # Removing routes would strand sessions positioned on their continuation nodes.
    pass
