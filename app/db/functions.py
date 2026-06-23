from alembic_utils.pg_function import PGFunction

public_encode_uri_component = PGFunction(
    schema="public",
    signature="encode_uri_component(text)",
    definition="returns text\n LANGUAGE sql\n IMMUTABLE STRICT\nAS $function$\n    select string_agg(\n        case\n            when bytes > 1 or c !~ '[0-9a-zA-Z_.!~*''()-]+' then\n                regexp_replace(encode(convert_to(c, 'utf-8')::bytea, 'hex'), '(..)', E'%\\\\1', 'g')\n            else\n                c\n        end,\n        ''\n    )\n    from (\n        select c, octet_length(c) bytes\n        from regexp_split_to_table($1, '') c\n    ) q;\n$function$",
)

update_edition_title = PGFunction(
    schema="public",
    signature="update_edition_title()",
    definition="""returns trigger LANGUAGE plpgsql
    AS $function$
    BEGIN
    UPDATE editions SET title = COALESCE(editions.edition_title, works.title)
    FROM works
    WHERE editions.id = NEW.id AND (NEW.edition_title IS NOT NULL OR NEW.work_id IS NOT NULL);
    RETURN NULL;
    END;
    $function$
    """,
)

update_edition_title_from_work = PGFunction(
    schema="public",
    signature="update_edition_title_from_work()",
    definition="""returns trigger LANGUAGE plpgsql
    AS $function$
    BEGIN
        UPDATE editions SET title = COALESCE(editions.edition_title, works.title)
        FROM works
        WHERE editions.work_id = NEW.id AND (NEW.title IS NOT NULL);
        RETURN NULL;
    END;
    $function$""",
)

update_collections_function = PGFunction(
    schema="public",
    signature="update_collections_function()",
    definition="""returns trigger LANGUAGE plpgsql
      AS $function$
        BEGIN
        UPDATE collections
        SET updated_at = NOW()
        WHERE collections.id = NEW.collection_id;
        RETURN NEW;
      END;
      $function$
    """,
)

refresh_search_view_v1_function = PGFunction(
    schema="public",
    signature="refresh_search_view_v1_function()",
    definition="""returns trigger LANGUAGE plpgsql
      AS $function$
        BEGIN
        REFRESH MATERIALIZED VIEW search_view_v1;
        RETURN NEW;
      END;
      $function$
    """,
)

refresh_recommendable_editions_function = PGFunction(
    schema="public",
    signature="refresh_recommendable_editions_function()",
    definition="""returns void LANGUAGE plpgsql
      AS $function$
        BEGIN
        -- CONCURRENTLY avoids an ACCESS EXCLUSIVE lock so recommendation reads are
        -- not blocked during the refresh; it requires the unique index on work_id.
        REFRESH MATERIALIZED VIEW CONCURRENTLY public.recommendable_editions;
        END;
      $function$
    """,
)

refresh_work_collection_frequency_view_function = PGFunction(
    schema="public",
    signature="refresh_work_collection_frequency_view_function()",
    definition="""returns trigger LANGUAGE plpgsql
      AS $function$
        BEGIN
        REFRESH MATERIALIZED VIEW work_collection_frequency;
        RETURN NEW;
      END;
      $function$
    """,
)

notify_flow_event_function = PGFunction(
    schema="public",
    signature="notify_flow_event()",
    definition="""returns trigger LANGUAGE plpgsql
      AS $function$
        BEGIN
        -- Notify on session state changes with comprehensive event data
        IF TG_OP = 'INSERT' THEN
            PERFORM pg_notify(
                'flow_events',
                json_build_object(
                    'event_type', 'session_started',
                    'session_id', NEW.id,
                    'flow_id', NEW.flow_id,
                    'user_id', NEW.user_id,
                    'current_node', NEW.current_node_id,
                    'status', NEW.status,
                    'revision', NEW.revision,
                    'timestamp', extract(epoch from NEW.started_at)
                )::text
            );
            RETURN NEW;
        ELSIF TG_OP = 'UPDATE' THEN
            -- Only notify on significant state changes (use IS DISTINCT FROM for NULL-safe comparison)
            IF OLD.current_node_id IS DISTINCT FROM NEW.current_node_id
               OR OLD.status IS DISTINCT FROM NEW.status
               OR OLD.revision IS DISTINCT FROM NEW.revision THEN
                PERFORM pg_notify(
                    'flow_events',
                    json_build_object(
                        'event_type', CASE
                            WHEN OLD.status IS DISTINCT FROM NEW.status THEN 'session_status_changed'
                            WHEN OLD.current_node_id IS DISTINCT FROM NEW.current_node_id THEN 'node_changed'
                            ELSE 'session_updated'
                        END,
                        'session_id', NEW.id,
                        'flow_id', NEW.flow_id,
                        'user_id', NEW.user_id,
                        'current_node', NEW.current_node_id,
                        'previous_node', OLD.current_node_id,
                        'status', NEW.status,
                        'previous_status', OLD.status,
                        'revision', NEW.revision,
                        'previous_revision', OLD.revision,
                        'timestamp', extract(epoch from NEW.last_activity_at)
                    )::text
                );
            END IF;
            RETURN NEW;
        ELSIF TG_OP = 'DELETE' THEN
            PERFORM pg_notify(
                'flow_events',
                json_build_object(
                    'event_type', 'session_deleted',
                    'session_id', OLD.id,
                    'flow_id', OLD.flow_id,
                    'user_id', OLD.user_id,
                    'timestamp', extract(epoch from NOW())
                )::text
            );
            RETURN OLD;
        END IF;
        RETURN NULL;
      END;
      $function$
    """,
)

# Full-text search maintenance for CMS content
cms_content_tsvector_update = PGFunction(
    schema="public",
    signature="cms_content_tsvector_update()",
    definition="""returns trigger LANGUAGE plpgsql
      AS $function$
        BEGIN
            NEW.search_document := to_tsvector(
                'english',
                coalesce(NEW.content->>'text','') || ' ' ||
                coalesce(NEW.content->>'setup','') || ' ' ||
                coalesce(NEW.content->>'punchline','') || ' ' ||
                coalesce(NEW.content->>'question','') || ' ' ||
                coalesce(array_to_string(NEW.tags, ' '), '')
            );
            RETURN NEW;
        END;
      $function$
    """,
)
