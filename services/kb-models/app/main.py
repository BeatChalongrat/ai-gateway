"""kb-models — the knowledge base's local models (FIX132 B).

Two models, both CPU, both on this machine; no text leaves the host:

  * BAAI/bge-m3 — one forward pass gives the dense vector (1024-d, CLS,
    L2-normalised) AND the sparse lexical weights (relu(sparse_linear(h))
    per token, max over repeats, special tokens dropped). The sparse side is
    bge-m3's own keyword signal: it works on the XLM-R sentencepiece pieces,
    so Thai needs no word segmentation for it.
  * BAAI/bge-reranker-v2-m3 — a multilingual cross-encoder; scores
    (query, passage) pairs, sigmoid → 0..1.

Plus /v1/segment (PyThaiNLP newmm) so Postgres full-text search can index Thai,
which has no spaces between words.

API (internal network only):
  GET  /health                       {"status": "loading"|"ready"|"failed", ...}
  POST /v1/embeddings                OpenAI-shaped; extras: return_sparse, priority
  POST /v1/rerank                    {query, documents[], top_n?} → {results:[{index, relevance_score}]}
  POST /v1/segment                   {texts[]} → {texts[]} (space-joined Thai words)

Weights download once into KB_MODELS_DIR at pinned revisions (a marker file
records what is there); after that the service loads from disk only.

CPU work runs one request at a time behind a priority lock: a chat-time query
(priority "query") goes ahead of document indexing (priority "index"), so a
big upload never makes a user's answer wait behind hundreds of chunks. The
lock is released between requests; the indexer sends small batches.
"""
from __future__ import annotations

import asyncio
import heapq
import itertools
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Literal

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger("kb-models")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

EMBED_REPO = os.getenv("KB_EMBED_MODEL", "BAAI/bge-m3")
EMBED_REV = os.getenv("KB_EMBED_REVISION", "5617a9f61b028005a4858fdac845db406aefb181")
RERANK_REPO = os.getenv("KB_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
RERANK_REV = os.getenv("KB_RERANK_REVISION", "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e")
MODELS_DIR = Path(os.getenv("KB_MODELS_DIR", "/models"))
DTYPE = os.getenv("KB_DTYPE", "fp32").lower()  # fp32 | bf16


def _default_threads() -> int:
    """FIX133 A1 — torch threads from the container: half the CPUs it may
    use (cgroup quota, else the count), at most 6, at least 1. One request
    runs at a time (the priority lock), so more threads than cores/2 only
    starve the co-tenants."""
    quota = None
    try:
        with open("/sys/fs/cgroup/cpu.max", encoding="utf-8") as fh:
            parts = fh.read().split()
        if len(parts) == 2 and parts[0] != "max":
            quota = -(-int(parts[0]) // int(parts[1]))
    except (OSError, ValueError):
        quota = None
    cpus = quota or (os.cpu_count() or 2)
    return max(1, min(6, cpus // 2))


THREADS = int(os.getenv("KB_THREADS") or _default_threads())
EMBED_MAX_TOKENS = int(os.getenv("KB_EMBED_MAX_TOKENS", "1024"))
RERANK_MAX_TOKENS = int(os.getenv("KB_RERANK_MAX_TOKENS", "512"))
BATCH = int(os.getenv("KB_BATCH", "8"))
MAX_INPUTS = int(os.getenv("KB_MAX_INPUTS", "64"))
RERANK_ENABLED = os.getenv("KB_RERANK_ENABLED", "1") not in ("0", "false", "no")

EMBED_FILES = [
    "config.json", "pytorch_model.bin", "sparse_linear.pt", "tokenizer.json",
    "tokenizer_config.json", "special_tokens_map.json", "sentencepiece.bpe.model",
]
RERANK_FILES = [
    "config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json",
    "special_tokens_map.json", "sentencepiece.bpe.model",
]

EMBED_DIM = 1024


# --------------------------------------------------------------------------- lock

class PriorityLock:
    """One holder at a time; waiters are served lowest priority number first,
    FIFO within a priority. 0 = chat-time query, 1 = indexing."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._held = False
        self._queue: list[tuple[int, int]] = []
        self._seq = itertools.count()

    def acquire(self, prio: int) -> None:
        with self._cond:
            ticket = (prio, next(self._seq))
            heapq.heappush(self._queue, ticket)
            while self._held or self._queue[0] != ticket:
                self._cond.wait()
            heapq.heappop(self._queue)
            self._held = True

    def release(self) -> None:
        with self._cond:
            self._held = False
            self._cond.notify_all()

    def waiting(self) -> int:
        with self._cond:
            return len(self._queue)


LOCK = PriorityLock()
PRIO = {"query": 0, "index": 1}


# --------------------------------------------------------------------------- models

class State:
    status: str = "loading"
    error: str | None = None
    loaded_at: float | None = None
    embed = None
    embed_tok = None
    sparse_linear = None
    unused_ids: set[int] = set()
    rerank = None
    rerank_tok = None
    segment = None


S = State()


def _dtype() -> torch.dtype:
    return torch.bfloat16 if DTYPE == "bf16" else torch.float32


def _fetch(repo: str, rev: str, files: list[str]) -> Path:
    """Download the pinned files once; afterwards never touch the network."""
    dest = MODELS_DIR / repo.replace("/", "__")
    marker = dest / ".revision"
    if marker.exists() and marker.read_text().strip() == rev and all((dest / f).exists() for f in files):
        return dest
    from huggingface_hub import hf_hub_download

    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        t0 = time.time()
        hf_hub_download(repo_id=repo, filename=f, revision=rev, local_dir=str(dest))
        log.info("downloaded %s/%s@%s in %.1fs", repo, f, rev[:8], time.time() - t0)
    marker.write_text(rev)
    return dest


def _load() -> None:
    try:
        from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer

        torch.set_num_threads(THREADS)
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass
        t0 = time.time()
        epath = _fetch(EMBED_REPO, EMBED_REV, EMBED_FILES)
        S.embed_tok = AutoTokenizer.from_pretrained(str(epath))
        S.embed = AutoModel.from_pretrained(str(epath), torch_dtype=_dtype()).eval()
        lin = torch.nn.Linear(S.embed.config.hidden_size, 1)
        lin.load_state_dict(torch.load(str(epath / "sparse_linear.pt"), map_location="cpu", weights_only=True))
        S.sparse_linear = lin.eval()
        tok = S.embed_tok
        S.unused_ids = {i for i in (tok.cls_token_id, tok.eos_token_id, tok.pad_token_id, tok.unk_token_id) if i is not None}
        log.info("bge-m3 loaded (%s) in %.1fs", DTYPE, time.time() - t0)
        if RERANK_ENABLED:
            t1 = time.time()
            rpath = _fetch(RERANK_REPO, RERANK_REV, RERANK_FILES)
            S.rerank_tok = AutoTokenizer.from_pretrained(str(rpath))
            S.rerank = AutoModelForSequenceClassification.from_pretrained(str(rpath), torch_dtype=_dtype()).eval()
            log.info("reranker loaded (%s) in %.1fs", DTYPE, time.time() - t1)
        from pythainlp.tokenize import word_tokenize

        S.segment = word_tokenize
        # Warm both paths so the first real request is not the slow one.
        _embed(["warm up ทดสอบ"], True)
        if S.rerank is not None:
            _rerank("warm", ["up"])
        S.status = "ready"
        S.loaded_at = time.time()
        log.info("ready in %.1fs", time.time() - t0)
    except Exception as exc:  # noqa: BLE001 — surfaced on /health
        S.status = "failed"
        S.error = f"{type(exc).__name__}: {exc}"[:500]
        log.exception("model load failed")


def _batches(order: list[int], n: int):
    for i in range(0, len(order), n):
        yield order[i : i + n]


def _embed(texts: list[str], want_sparse: bool) -> tuple[list[list[float]], list[dict[int, float]] | None, list[int]]:
    n = len(texts)
    dense: list[list[float]] = [[] for _ in range(n)]
    sparse: list[dict[int, float]] | None = [{} for _ in range(n)] if want_sparse else None
    ntok = [0] * n
    order = sorted(range(n), key=lambda i: len(texts[i]))
    with torch.inference_mode():
        for b in _batches(order, BATCH):
            enc = S.embed_tok(
                [texts[i] for i in b], padding=True, truncation=True,
                max_length=EMBED_MAX_TOKENS, return_tensors="pt",
            )
            h = S.embed(**enc).last_hidden_state
            d = torch.nn.functional.normalize(h[:, 0].float(), dim=-1)
            w = torch.relu(S.sparse_linear(h.float())).squeeze(-1) if want_sparse else None
            ids = enc["input_ids"].tolist()
            mask = enc["attention_mask"].tolist()
            for k, i in enumerate(b):
                dense[i] = d[k].tolist()
                ntok[i] = int(sum(mask[k]))
                if w is not None and sparse is not None:
                    row: dict[int, float] = {}
                    wk = w[k].tolist()
                    for tid, wt, m in zip(ids[k], wk, mask[k]):
                        if not m or tid in S.unused_ids or wt <= 0:
                            continue
                        if wt > row.get(tid, 0.0):
                            row[tid] = wt
                    sparse[i] = row
    return dense, sparse, ntok


def _rerank(query: str, docs: list[str]) -> list[float]:
    n = len(docs)
    scores = [0.0] * n
    order = sorted(range(n), key=lambda i: len(docs[i]))
    with torch.inference_mode():
        for b in _batches(order, BATCH):
            enc = S.rerank_tok(
                [query] * len(b), [docs[i] for i in b], padding=True, truncation="only_second",
                max_length=RERANK_MAX_TOKENS, return_tensors="pt",
            )
            logits = S.rerank(**enc).logits.view(-1).float()
            s = torch.sigmoid(logits).tolist()
            for k, i in enumerate(b):
                scores[i] = s[k]
    return scores


# --------------------------------------------------------------------------- API

app = FastAPI(title="kb-models", docs_url=None, redoc_url=None)


@app.on_event("startup")
def _startup() -> None:
    threading.Thread(target=_load, name="kb-models-load", daemon=True).start()


@app.get("/health")
def health() -> dict:
    return {
        "status": S.status,
        "error": S.error,
        "dtype": DTYPE,
        "threads": THREADS,
        "waiting": LOCK.waiting(),
        "models": {
            "embed": {"repo": EMBED_REPO, "revision": EMBED_REV, "dim": EMBED_DIM, "max_tokens": EMBED_MAX_TOKENS},
            "rerank": {"repo": RERANK_REPO, "revision": RERANK_REV, "enabled": S.rerank is not None,
                       "max_tokens": RERANK_MAX_TOKENS},
        },
    }


def _ready() -> None:
    if S.status != "ready":
        raise HTTPException(status_code=503, detail=f"models {S.status}")


async def _run(prio: str, fn, *args):
    p = PRIO.get(prio, 1)

    def work():
        LOCK.acquire(p)
        try:
            return fn(*args)
        finally:
            LOCK.release()

    return await asyncio.to_thread(work)


class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str | None = None
    return_sparse: bool = False
    priority: Literal["query", "index"] = "index"


@app.post("/v1/embeddings")
async def embeddings(req: EmbeddingRequest) -> dict:
    _ready()
    texts = [req.input] if isinstance(req.input, str) else list(req.input)
    if not texts or len(texts) > MAX_INPUTS:
        raise HTTPException(status_code=400, detail=f"input must have 1..{MAX_INPUTS} items")
    if any(not isinstance(t, str) or not t.strip() for t in texts):
        raise HTTPException(status_code=400, detail="empty input item")
    t0 = time.perf_counter()
    dense, sparse, ntok = await _run(req.priority, _embed, texts, req.return_sparse)
    ms = (time.perf_counter() - t0) * 1000
    log.info("embed n=%d tokens=%d sparse=%s prio=%s ms=%.0f", len(texts), sum(ntok), req.return_sparse, req.priority, ms)
    data = []
    for i, v in enumerate(dense):
        item: dict = {"object": "embedding", "index": i, "embedding": v, "tokens": ntok[i],
                      "truncated": ntok[i] >= EMBED_MAX_TOKENS}
        if sparse is not None:
            row = sparse[i]
            keys = sorted(row)
            item["sparse"] = {"indices": keys, "values": [round(row[k], 6) for k in keys]}
        data.append(item)
    return {"object": "list", "model": req.model or EMBED_REPO, "data": data,
            "usage": {"prompt_tokens": sum(ntok), "total_tokens": sum(ntok)}}


class RerankRequest(BaseModel):
    query: str = Field(..., min_length=1)
    documents: list[str]
    top_n: int | None = None
    model: str | None = None
    priority: Literal["query", "index"] = "query"


@app.post("/v1/rerank")
async def rerank(req: RerankRequest) -> dict:
    _ready()
    if S.rerank is None:
        raise HTTPException(status_code=503, detail="reranker disabled")
    if not req.documents or len(req.documents) > 200:
        raise HTTPException(status_code=400, detail="documents must have 1..200 items")
    t0 = time.perf_counter()
    scores = await _run(req.priority, _rerank, req.query, req.documents)
    ms = (time.perf_counter() - t0) * 1000
    log.info("rerank n=%d prio=%s ms=%.0f", len(req.documents), req.priority, ms)
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    if req.top_n:
        ranked = ranked[: req.top_n]
    return {"model": req.model or RERANK_REPO,
            "results": [{"index": i, "relevance_score": round(scores[i], 6)} for i in ranked]}


class SegmentRequest(BaseModel):
    texts: list[str]


@app.post("/v1/segment")
async def segment(req: SegmentRequest) -> dict:
    _ready()
    if len(req.texts) > MAX_INPUTS:
        raise HTTPException(status_code=400, detail=f"at most {MAX_INPUTS} texts")

    def work():
        return [" ".join(t for t in S.segment(x, engine="newmm", keep_whitespace=False) if t.strip()) for x in req.texts]

    return {"texts": await asyncio.to_thread(work)}


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(health()))
