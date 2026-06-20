-- Web-search feature — per-message search citations.
--
-- An assistant turn that answered with the web_search tool stores the
-- cited sources here so the citation chips re-render after a page
-- reload (same persistence rationale as reasoning_content in migration
-- 019). JSONB array of {title, url, snippet, domain, published_date}.
-- Nullable so non-search turns and pre-feature rows pay no storage.
--
-- Stored masked: the snippets come from the Gateway search endpoint
-- which runs the same outbound DLP scan as chat content before the
-- chat-edge ever sees them.

ALTER TABLE chat.messages_masked
    ADD COLUMN IF NOT EXISTS search_sources JSONB;
