import pytest
from sqlalchemy import text
from sqlalchemy.exc import InternalError
from sqlalchemy.orm import Session


def test_pg_textsearch_supports_indexed_ranking(session: Session):
    assert session.scalar(
        text("SELECT extversion FROM pg_extension WHERE extname = 'pg_textsearch'")
    )
    assert session.scalar(
        text("SELECT EXISTS (SELECT 1 FROM pg_am WHERE amname = 'bm25')")
    )
    session.execute(
        text("CREATE TEMP TABLE bm25_probe (id int PRIMARY KEY, content text)")
    )
    session.execute(
        text(
            "INSERT INTO bm25_probe VALUES (1, 'dragon books and dragon stories'), (2, 'gardening tips'), (3, 'books about dragon adventures')"
        )
    )
    session.execute(
        text(
            "CREATE INDEX bm25_probe_content ON bm25_probe USING bm25(content) WITH (text_config='english')"
        )
    )
    rows = session.execute(
        text(
            "SELECT id, content <@> 'dragon' AS score FROM bm25_probe ORDER BY content <@> 'dragon' LIMIT 2"
        )
    ).all()
    assert {row.id for row in rows} == {1, 3}
    assert rows[0].score <= rows[1].score < 0
    with session.begin_nested():
        with pytest.raises(InternalError, match="depend on it"):
            with session.begin_nested():
                session.execute(text("DROP EXTENSION pg_textsearch RESTRICT"))
        assert session.scalar(text("SELECT count(*) FROM bm25_probe")) == 3
    session.rollback()
