-- 010_enable_pgvector.sql
--
-- Enables the pgvector extension so chat.knowledge_chunks can store
-- ``vector(1536)`` embedding columns. Idempotent: a no-op when the
-- extension is already present (the existing SRAN Chatbot product
-- already enables it on shared DBs, so this just guards against
-- environments where it isn't pre-installed).
CREATE EXTENSION IF NOT EXISTS vector;
