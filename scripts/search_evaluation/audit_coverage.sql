SELECT jsonb_build_object(
 'works',(SELECT count(*) FROM works),
 'editions',(SELECT count(*) FROM editions),
 'editions_without_work',(SELECT count(*) FROM editions WHERE work_id IS NULL),
 'authorless_works',(SELECT count(*) FROM documents WHERE authors='[]'),
 'missing_popularity_works',(SELECT count(*) FROM documents WHERE NOT has_popularity),
 'legacy_rows',(SELECT count(*) FROM legacy_search),
 'legacy_unique_works',(SELECT count(DISTINCT work_id) FROM legacy_search),
 'legacy_works_without_popularity',(SELECT count(DISTINCT s.work_id) FROM legacy_search s LEFT JOIN popularity p USING(work_id) WHERE p.work_id IS NULL),
 'legacy_endpoint_unique_works',(SELECT count(DISTINCT s.work_id) FROM legacy_search s JOIN popularity p USING(work_id)),
 'multiple_search_rows',(SELECT count(*) FROM (SELECT work_id FROM legacy_search GROUP BY work_id HAVING count(*)>1) x),
 'distinct_document_duplicates',(SELECT count(*) FROM (SELECT work_id FROM legacy_search GROUP BY work_id HAVING count(DISTINCT document)>1) x),
 'missing_author_name_text',(SELECT count(*) FROM documents WHERE authors<>'[]' AND trim(author_text)=''),
 'identical_normalized_titles',(SELECT count(*) FROM (SELECT lower(trim(title)) FROM works GROUP BY lower(trim(title)) HAVING count(*)>1) x),
 'school_scopes',(SELECT count(DISTINCT scope_id) FROM membership),
 'school_work_memberships',(SELECT count(*) FROM membership),
 'unavailable_memberships',(SELECT count(*) FROM membership WHERE NOT available)
);
