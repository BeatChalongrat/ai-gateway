# Knowledge base (FIX132 B)

A knowledge base (KB) is a set of documents a user uploads. Each answer in a chat bound to it (directly, or through its project) quotes the relevant passages. The models run on this machine. A KB is private to the user who created it.

## Pieces

| piece | where | what it does |
|---|---|---|
| `kb-models` | `services/kb-models`, compose service `kb-models`, no host port | bge-m3 embeddings (a dense vector and sparse keyword weights from one pass), bge-reranker-v2-m3 (only when `KB_RERANK_ENABLED`), PyThaiNLP `newmm` word segmentation |
| chat-edge | `internal/service/kb_models_client.go` | the client: URL guard, internal-only dialer, per-call time limits |
| chat-edge | `knowledge_worker.go` | background indexing |
| chat-edge | `rag_service.go`, `knowledge_context.go` | retrieval, the prompt block and the answer's sources |
| Postgres | migration `049_kb_local_hybrid.sql` | `knowledge_chunks.embedding vector(1024)`, `sparse sparsevec(250002)`, progress and claim columns, `knowledge_bases.user_id` |

### Models

The weights are pinned in `services/kb-models/app/main.py` and live in the `kb_models` volume (`ai-gateway_kb_models`, about 4.6 GB).

| model | revision | size |
|---|---|---|
| `BAAI/bge-m3` | `5617a9f61b028005a4858fdac845db406aefb181` | 1024-d dense, 250,002-token sparse vocabulary, 1,024-token input cap (`KB_EMBED_MAX_TOKENS`) |
| `BAAI/bge-reranker-v2-m3` | `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` | 512-token pairs |

**RAM:** about 1.1 GB per model in bf16 and 2.3 GB in fp32, plus PyTorch overhead. The container is capped at 6 GB and 6 CPUs. With the reranker off, only bge-m3 is loaded.

**Speed on this PC** (CPU, bf16, 6 threads, measured while other lanes ran):
- Embedding runs at about 230 tokens/s, so a batch of 16 passages takes 13–22 s (about 0.8 s per passage).
- A question embeds in 0.2 s (p50).
- The SQL takes 16 ms (p50).
- Reranking 30 candidates takes 21.7 s (p50), 46 s at worst.
- bf16 is about twice as fast as fp32 here (AVX512-BF16).

The first start loads for 1–2 minutes. During that time `/health` says `loading` and chat-edge reports `models_loading`.

## The no-egress guarantee

- **Embeddings, rerank and segmentation never leave the host.** chat-edge calls kb-models directly on the compose network (`KB_MODELS_URL`, default `http://kb-models:8090`).
  - The URL must name a compose service (a single-label name), `localhost`, `host.docker.internal`, a loopback address or an RFC 1918 address. Anything else is refused at boot, and the KB then reports `models_unavailable`.
  - The dialer checks the address it actually connects to. It refuses anything that is not loopback or private, whatever DNS returns, and it never uses an HTTP proxy from the environment.
  - The backend's Python test `test_dlp_egress_invariant.py` pins this. Before FIX132 the text went through the gateway's DLP gate to OpenAI; there is now no third party to protect it from.
- **kb-models itself does not go online.** `HF_HUB_OFFLINE=1` (`KB_MODELS_OFFLINE`, default 1), and it loads from the volume only.
  - To fill an empty volume, start kb-models once with `KB_MODELS_OFFLINE=0`. It downloads the pinned files, writes a `.revision` marker, and never downloads again.
  - For more hardening, put kb-models on an `internal: true` network shared only with chat-edge.
- **What does leave:** the passages chosen for an answer are part of the system prompt sent to the chat model the user picked, exactly like an attached file's text.
  - The backend DLP scans system messages only when the tenant's profile sets `scan_system_messages` (default off).
  - So for an external chat model, a KB passage is treated the same as file context. Use a self-hosted chat model, or turn that profile setting on, where that matters.

## Indexing

1. `POST /knowledge-bases/{id}/documents` stores the file and a `pending` row, and answers 202 at once.
2. `KB_INDEX_WORKERS` goroutines (default 2) claim documents with `FOR UPDATE SKIP LOCKED`, which is safe across processes. For each document the worker:
   - extracts the whole text (the sandboxed extractor for PDF and Office; text files are read in full);
   - chunks it;
   - embeds batches of at most 16 with priority `index`, so a chat-time question goes first;
   - stores every batch together with `progress_done/progress_total`;
   - finally sets `indexed` and `indexed_at`.
3. Chunking (`chunker.go`):
   - Sizes count characters, not bytes. The split is recursive: blank line → line break → sentence end → space.
   - A Thai run longer than a chunk with none of those boundaries is split only between words, using `/v1/segment`.
   - Chunks are `KB_CHUNK_SIZE` (1,000) characters with `KB_CHUNK_OVERLAP` (150) overlap. That measured a mean of 805 characters and 191 bge-m3 tokens (max 269), and cut no key fact.
4. Failures carry an `error_code`:

   | code | cause |
   |---|---|
   | `encrypted` | password-protected PDF |
   | `empty_text` | no text layer, or an empty file |
   | `extract_failed` | any other extraction failure |
   | `models_unavailable` | see the next point |

5. When kb-models is unavailable:
   - While it is **loading**, the document goes back to pending and is retried in 30 s without using an attempt.
   - When it is **down** (unreachable, 5xx, timeout), retries back off at 30 s, 1, 2, 4 min, and the document fails as `models_unavailable` after 5 attempts. Re-index it to try again.
6. Every write is fenced on the worker's claim (`claimed_at`, renewed per batch):
   - Deleting or re-indexing a document mid-way stops the worker. No chunk and no error row is left.
   - On shutdown the claim is handed back.
   - A `processing` row whose claim is older than 5 minutes (a crashed process) goes back to `pending`.

## Retrieval (what a chat turn does)

1. The question is embedded once (dense and sparse, priority `query`).
2. Postgres takes the top `KB_CANDIDATES` (50) of two exact scans over the KB's **indexed** chunks: cosine distance (`embedding <=> q`) and bge-m3's sparse inner product (`sparse <#> q`, kept when < 0).
3. Every candidate in the union scores `cosine + KB_SPARSE_WEIGHT × lexical` (0.3). This is bge-m3's own dense + sparse combination.
4. A passage is kept if its score is at least `KB_MIN_SCORE` (0.40), up to `KB_TOP_K` (20). If none passes, no passage is given to the model and the answer is a normal one.
5. Optionally (`KB_RERANK_ENABLED`) the fused top `KB_RERANK_CANDIDATES` (40) are reranked within `KB_RERANK_BUDGET_MS` (4 s), keeping passages with relevance at or above `KB_RERANK_FLOOR` (0.01). A timeout or error keeps the fused order.
   - A rerank the budget cut short still runs to the end inside kb-models, so the next question may wait behind it. Keep the candidate count small if you turn it on.
6. The block (`<knowledge_base name="…">`) lists numbered passages labelled `[Source: <filename>]`. It tells the model to cite that way, and to say plainly when the KB does not contain the answer.
   - It names files still being indexed and asks the model to answer from the ready ones.
   - It stops before `KB_CONTEXT_MAX_CHARS` (24,000) characters, and before a quarter of the model's context window (the FIX102 budget model).
7. The answer's `context_sources`:
   - a `knowledge_chunk` for every passage given to the model: chunk id, kb, document, filename, chunk index, a 500-character excerpt, score and rank;
   - one `knowledge_pending` for files still being indexed.

   They are saved before `message_complete` and returned by GET /messages.

### Measured on the FIX132 eval set

The set is 16 documents, 270 chunks and 36 Thai/English questions. "fail@k" means the right passage was not in the top k.

| retrieval | fail@1 | fail@5 | fail@20 |
|---|---|---|---|
| dense only | 19 % | 3 % | 0 % |
| **dense + 0.3 × sparse (default)** | **19 %** | **0 %** | **0 %** |
| equal-weight RRF dense + sparse | 42 % | 17 % | 3 % |
| Postgres full text over segmented Thai | 86 % | 61 % | 39 % |
| hybrid + reranker (30 candidates) | 8 % | 3 % | 0 % |

- Full text was dropped: `ts_rank` has no IDF, so it is not BM25.
- Contextual chunk headers (Anthropic's LLM-per-chunk step) were not needed, because fail@20 is already 0 %.
- Real questions' top score was at least 0.51; off-topic questions scored 0.32–0.50 (median 0.38). Hence `KB_MIN_SCORE` 0.40.

### Why no ANN index

Each search is an exact scan of one KB's chunks, narrowed by the btree on `knowledge_base_id`. At pilot scale that takes milliseconds and never misses a chunk. Add HNSW when one KB passes about 50,000 chunks, or when the dense arm's SQL exceeds about 100 ms:

```sql
CREATE INDEX CONCURRENTLY idx_kc_embedding_hnsw ON chat.knowledge_chunks USING hnsw (embedding vector_cosine_ops);
-- and, per session, so the per-KB filter does not starve the index:
SET hnsw.iterative_scan = relaxed_order;
```

## Knobs

All are chat-edge variables unless noted. The defaults are in `docker-compose.webchat.yml` and `.env.example`.

| variable | default | |
|---|---|---|
| `KB_MODELS_URL` | `http://kb-models:8090` | internal hosts only (see above) |
| `KB_INDEX_WORKERS` | 2 | indexing goroutines (1–4) |
| `KB_CHUNK_SIZE` / `KB_CHUNK_OVERLAP` | 1000 / 150 | characters, for new KBs and KBs still on 512/50 |
| `KB_SPARSE_WEIGHT` | 0.3 | 0 = dense only |
| `KB_CANDIDATES` | 50 | per arm |
| `KB_MIN_SCORE` | 0.40 | fused-score floor |
| `KB_TOP_K` | 20 | passages given to the model |
| `KB_CONTEXT_MAX_CHARS` | 24000 | also capped at ¼ of the model's window |
| `KB_RERANK_ENABLED` | false | read by chat-edge **and** kb-models (which then loads the reranker) |
| `KB_RERANK_CANDIDATES` / `KB_RERANK_BUDGET_MS` / `KB_RERANK_FLOOR` | 40 / 4000 / 0.01 | |
| kb-models: `KB_DTYPE` / `KB_THREADS` / `KB_MODELS_OFFLINE` | bf16 / 6 / 1 | |

`POST /knowledge-bases/{id}/search` takes `mode` (`dense|sparse|hybrid|hybrid_rerank`) and `min_score`, and returns scores and `timing_ms`. Use it to compare layers through the real code path.

## Operations

**Deploy** (first time):
1. Build the images: `scripts/build-images.sh kb-models chat-edge-api`.
2. Apply migration 049: `bash services/chat-edge-api/scripts/apply_migrations.sh`. It is idempotent. It deletes chunks that are not 1,024-d and sets their documents back to pending.
3. Start kb-models: `docker compose -p ai-gateway -f docker-compose.webchat.yml up -d kb-models`.
4. Recreate chat-edge.

chat-edge does not depend on kb-models. Without it, chat works and the KB says `models_unavailable`.

**Re-embed after a model change** (a different `KB_EMBED_MODEL` / `KB_EMBED_REVISION`: vector spaces differ even at the same dimension; a different dimension also needs a migration of `embedding vector(N)`):
1. Download the new weights once (`KB_MODELS_OFFLINE=0`), then restart kb-models.
2. Queue every document again. Per KB, use the admin endpoint `POST /admin/knowledge-bases/{id}/reindex`; for all of them:

   ```sql
   BEGIN;
   UPDATE chat.knowledge_documents SET status='pending', chunk_count=0, progress_done=0, progress_total=0,
          error_code=NULL, error_message=NULL, attempts=0, next_attempt_at=NULL, claimed_at=NULL,
          indexed_at=NULL, updated_at=NOW();
   DELETE FROM chat.knowledge_chunks;
   COMMIT;
   ```

   The workers pick them up. Until a document is indexed again, answers do not use it, and the block names it as still being processed.

**Knowledge bases from before 049** have no owner (`user_id` NULL) and only the admin endpoints reach them. To give one to a user:

```sql
UPDATE chat.knowledge_bases SET user_id = '<uuid>' WHERE id = '<kb>';
```

**Test stack:** its kb-models mounts `ai-gateway_kb_models` **read-only**, as an external volume, so `scripts/test-stack.sh reset` cannot delete it, with `HF_HUB_OFFLINE=1`.
