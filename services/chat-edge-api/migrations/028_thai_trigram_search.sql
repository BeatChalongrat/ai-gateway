-- 028_thai_trigram_search.sql
--
-- Phase-2 fix: the S6 search (migration 016) tokenizes with the
-- 'simple' dictionary, which splits on whitespace. Thai is written
-- WITHOUT spaces, so a mid-phrase query ("เสนอราคา" inside
-- "ใบเสนอราคาลูกค้า") never matches a tsvector token — Thai content
-- search effectively returned nothing. pg_trgm (contrib, present in
-- the pgvector/pgvector:pg16 image) gives substring matching plus
-- similarity() ranking that works for unsegmented scripts.
--
-- Additive only: the tsvector columns / GIN indexes / triggers from
-- migration 016 are untouched. Spaced-language queries keep the
-- ts_rank branch; the repos route per query via
-- repository.NeedsTrigramSearch (internal/repository/search_lang.go).

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Backs `content_masked ILIKE '%…%'` in MessageRepo.SearchUserMessages.
CREATE INDEX IF NOT EXISTS idx_messages_content_trgm
    ON chat.messages_masked USING gin (content_masked gin_trgm_ops);

-- Backs `title ILIKE '%…%'` in ConversationRepo.SearchTitlesForUser.
CREATE INDEX IF NOT EXISTS idx_conversations_title_trgm
    ON chat.conversations USING gin (title gin_trgm_ops);

-- Backs `content ILIKE '%…%'` in MemoryRepo.SearchByContent.
CREATE INDEX IF NOT EXISTS idx_user_memories_content_trgm
    ON chat.user_memories USING gin (content gin_trgm_ops);
