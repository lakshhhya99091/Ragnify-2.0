"""
Embedder — wraps Google Gemini gemini-embedding-001 with batching & caching.
Produces 3072-dimensional embeddings.

Accuracy: document chunks are embedded with task_type=RETRIEVAL_DOCUMENT and
queries with RETRIEVAL_QUERY, which is what the model is tuned for in RAG search.
"""
import hashlib
import json
import logging
import os
import time
from typing import List, Dict

import numpy as np
from google import genai
from google.genai import types

from config import EMBEDDING_MODEL, DATA_DIR

logger = logging.getLogger(__name__)

# ── Gemini client (reused; rebuilt only if the API key changes at runtime) ──────
_client = None
_client_key = None
_EMBED_TIMEOUT_MS = 120_000  # 2 min/request so a hung call can't stall ingestion


def _get_client():
    """Return a cached genai client, rebuilding only when the API key changes."""
    global _client, _client_key
    import config as cfg
    if _client is None or _client_key != cfg.GEMINI_API_KEY:
        _client = genai.Client(
            api_key=cfg.GEMINI_API_KEY,
            http_options=types.HttpOptions(timeout=_EMBED_TIMEOUT_MS),
        )
        _client_key = cfg.GEMINI_API_KEY
    return _client


# ── Embedding cache (keyed by model + task_type + text hash) ────────────────────
# Persisted so re-ingesting identical content skips paid API calls. Query
# embeddings are intentionally NOT cached (they would bloat the file forever).
_cache: Dict[str, List[float]] = {}
_CACHE_FILE = os.path.join(DATA_DIR, ".embedding_cache.json")


def _load_cache():
    global _cache
    if os.path.exists(_CACHE_FILE):
        try:
            with open(_CACHE_FILE, "r") as f:
                raw = json.load(f)
            # Keep only entries for the current key scheme (model|task|hash) so
            # legacy/stale keys don't make the cache grow without bound.
            _cache = {k: v for k, v in raw.items() if k.startswith(EMBEDDING_MODEL + "|")}
            dropped = len(raw) - len(_cache)
            logger.info(f"Loaded {len(_cache)} cached embeddings" + (f" ({dropped} stale dropped)" if dropped else ""))
        except Exception:
            _cache = {}


def _save_cache():
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        with open(_CACHE_FILE, "w") as f:
            json.dump(_cache, f)
    except Exception as e:
        logger.warning(f"Cache save failed: {e}")


def _cache_key(text: str, task_type: str) -> str:
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"{EMBEDDING_MODEL}|{task_type}|{h}"


# Keep batches small: the free tier rejects requests that pack too many tokens
# into one call (large tender chunks blow past the per-request limit at size 100).
# 20 is the proven-safe value; spacing stays within the ~15 RPM free-tier limit.
_GEMINI_BATCH_SIZE = 20
_INTER_BATCH_DELAY = 3.0   # seconds between batches (free-tier friendly)


def _embed_batch_gemini(texts: List[str], task_type: str) -> List[List[float]]:
    """Embed a batch via Gemini, retrying with backoff on rate limits."""
    import config as cfg
    max_attempts = 5
    for attempt in range(max_attempts):
        try:
            resp = _get_client().models.embed_content(
                model=cfg.EMBEDDING_MODEL,
                contents=texts,
                config=types.EmbedContentConfig(task_type=task_type),
            )
            return [e.values for e in resp.embeddings]
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                wait = min(5 * (2 ** attempt), 90)
                logger.warning(f"Rate limit hit, waiting {wait}s (attempt {attempt+1}/{max_attempts})...")
                time.sleep(wait)
            elif attempt < max_attempts - 1:
                wait = 2 * (attempt + 1)
                logger.warning(f"Embed attempt {attempt+1} failed: {e}, retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError(f"Embedding failed after {max_attempts} attempts")


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1e-10, norms)
    return matrix / norms


def embed_texts(
    texts: List[str],
    task_type: str = "RETRIEVAL_DOCUMENT",
    use_cache: bool = True,
) -> np.ndarray:
    """
    Embed a list of texts. Returns (len(texts), dim) float32, L2-normalized.
    Document chunks use RETRIEVAL_DOCUMENT and are cached; queries pass
    use_cache=False so they don't bloat the persistent cache.
    """
    embeddings = []          # (orig_index, vector)
    to_embed_indices = []
    to_embed_texts = []

    for i, text in enumerate(texts):
        key = _cache_key(text, task_type)
        if use_cache and key in _cache:
            embeddings.append((i, _cache[key]))
        else:
            to_embed_indices.append(i)
            to_embed_texts.append(text)

    if to_embed_texts:
        total_batches = (len(to_embed_texts) + _GEMINI_BATCH_SIZE - 1) // _GEMINI_BATCH_SIZE
        logger.info(f"Embedding {len(to_embed_texts)} texts in {total_batches} batch(es) (size={_GEMINI_BATCH_SIZE})")
        new_embeddings = []
        for batch_num, start in enumerate(range(0, len(to_embed_texts), _GEMINI_BATCH_SIZE), 1):
            batch = to_embed_texts[start:start + _GEMINI_BATCH_SIZE]
            logger.info(f"  Batch {batch_num}/{total_batches} ({len(batch)} texts)...")
            new_embeddings.extend(_embed_batch_gemini(batch, task_type))
            if start + _GEMINI_BATCH_SIZE < len(to_embed_texts):
                time.sleep(_INTER_BATCH_DELAY)

        changed = False
        for orig_idx, vec in zip(to_embed_indices, new_embeddings):
            if use_cache:
                _cache[_cache_key(texts[orig_idx], task_type)] = vec
                changed = True
            embeddings.append((orig_idx, vec))
        if changed:
            _save_cache()

    embeddings.sort(key=lambda x: x[0])
    matrix = np.array([vec for _, vec in embeddings], dtype=np.float32)
    return _l2_normalize(matrix)


def embed_query(query: str) -> np.ndarray:
    """Embed a single query (RETRIEVAL_QUERY, not cached). Returns (1, dim)."""
    return embed_texts([query], task_type="RETRIEVAL_QUERY", use_cache=False)


# Load the persisted cache once at import (not on every embed call).
_load_cache()
