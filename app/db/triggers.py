from alembic_utils.pg_trigger import PGTrigger

from app.db.functions import cms_content_tsvector_update

collection_legacy_default_trigger = PGTrigger(
    schema="public",
    signature="collection_legacy_default_trigger",
    on_entity="public.collections",
    is_constraint=False,
    definition="BEFORE INSERT ON public.collections FOR EACH ROW EXECUTE FUNCTION public.collection_legacy_default()",
)

editions_update_edition_title_trigger = PGTrigger(
    schema="public",
    signature="update_edition_title_trigger",
    on_entity="public.editions",
    is_constraint=False,
    definition="AFTER INSERT OR UPDATE OF edition_title, work_id ON public.editions FOR EACH ROW EXECUTE FUNCTION update_edition_title()",
)

works_update_edition_title_from_work_trigger = PGTrigger(
    schema="public",
    signature="update_edition_title_from_work_trigger",
    on_entity="public.works",
    is_constraint=False,
    definition="AFTER INSERT OR UPDATE OF title ON public.works FOR EACH ROW EXECUTE FUNCTION update_edition_title_from_work()",
)

# works_update_search_v1_trigger = PGTrigger(
#     schema="public",
#     signature="update_search_v1_from_works_trigger",
#     on_entity="public.works",
#     is_constraint=False,
#     definition="""
#     AFTER INSERT OR UPDATE OF title OR DELETE ON public.works
#     FOR EACH STATEMENT EXECUTE FUNCTION refresh_search_view_v1_function()
#     """,
# )

# authors_update_search_v1_trigger = PGTrigger(
#     schema="public",
#     signature="update_search_v1_from_authors_trigger",
#     on_entity="public.authors",
#     is_constraint=False,
#     definition="""
#     AFTER INSERT OR UPDATE OR DELETE ON public.authors
#     FOR EACH STATEMENT EXECUTE FUNCTION refresh_search_view_v1_function()
#     """,
# )
#
# series_update_search_v1_trigger = PGTrigger(
#     schema="public",
#     signature="update_search_v1_from_series_trigger",
#     on_entity="public.series",
#     is_constraint=False,
#     definition="""
#     AFTER INSERT OR UPDATE OR DELETE ON public.series
#     FOR EACH STATEMENT EXECUTE FUNCTION refresh_search_view_v1_function()
#     """,
# )
#
# # This could really be done less frequently - like once a week
# collection_item_update_frequencies_trigger = PGTrigger(
#     schema="public",
#     signature="update_work_collection_frequency_from_collection_item_trigger",
#     on_entity="public.collection_items",
#     is_constraint=False,
#     definition="""
#     AFTER INSERT OR UPDATE OR DELETE ON public.collection_items
#     FOR EACH STATEMENT EXECUTE FUNCTION refresh_work_collection_frequency_view_function()
#     """,
# )

conversation_sessions_notify_flow_event_trigger = PGTrigger(
    schema="public",
    signature="conversation_sessions_notify_flow_event_trigger",
    on_entity="public.conversation_sessions",
    is_constraint=False,
    definition="""AFTER INSERT OR UPDATE OR DELETE ON public.conversation_sessions
                  FOR EACH ROW EXECUTE FUNCTION notify_flow_event()""",
)

# Trigger to update collection timestamps when items change
update_collections_trigger = PGTrigger(
    schema="public",
    signature="update_collections_trigger",
    on_entity="public.collection_items",
    is_constraint=False,
    definition="AFTER INSERT OR UPDATE ON public.collection_items FOR EACH ROW EXECUTE FUNCTION update_collections_function()",
)

# Trigger to maintain CMS content FTS tsvector
cms_content_tsvector_trigger = PGTrigger(
    schema="public",
    signature="trg_cms_content_tsvector_update",
    on_entity="public.cms_content",
    is_constraint=False,
    definition=(
        "BEFORE INSERT OR UPDATE ON public.cms_content FOR EACH ROW EXECUTE FUNCTION "
        f"{cms_content_tsvector_update.signature}"
    ),
)
