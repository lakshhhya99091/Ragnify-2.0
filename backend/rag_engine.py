"""
Ragnify RAG Engine — orchestrates document processing and question answering.
Uses Google Gemini for embeddings and chat.
"""
import asyncio
import json
import logging
import os
import uuid
from typing import AsyncGenerator, Dict, Any, List, Optional

from google import genai

from config import SYSTEM_PROMPT, TOP_K, DATA_DIR
from parser import parse_document
from crawler import crawl_urls
from chunker import chunk_all_sources
from embedder import embed_texts, embed_query
from vector_store import build_index, search, index_exists, list_indexes

logger = logging.getLogger(__name__)

# ── In-memory document registry ───────────────────────────────────────────────
_doc_registry: Dict[str, Dict[str, Any]] = {}
_REGISTRY_FILE = os.path.join(DATA_DIR, ".registry.json")


def _load_registry():
    """Load the registry from disk into the EXISTING dict (mutating in place,
    not rebinding) so references held elsewhere — e.g. main.py's upload handler —
    stay valid, then auto-discover any on-disk indexes not yet tracked.
    Called once at startup."""
    data = {}
    if os.path.exists(_REGISTRY_FILE):
        try:
            with open(_REGISTRY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    _doc_registry.clear()
    _doc_registry.update(data)
    # Also auto-discover indexes not in registry
    for doc_id in list_indexes():
        if doc_id not in _doc_registry:
            _doc_registry[doc_id] = {
                "doc_id": doc_id,
                "filename": doc_id,
                "status": "ready",
                "num_chunks": 0,
                "num_links": 0,
            }


def _save_registry():
    os.makedirs(os.path.dirname(_REGISTRY_FILE), exist_ok=True)
    with open(_REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(_doc_registry, f, ensure_ascii=False, indent=2)


# Statuses that mean ingestion was still in flight (no finished index yet).
_IN_PROGRESS_STATES = {
    "uploading", "processing", "parsing", "crawling", "embedding", "indexing",
}


def _cleanup_orphaned_entries():
    """
    Remove zombie registry entries left behind when the server was stopped or
    crashed mid-ingestion. After a restart there is no background task driving
    these docs, so any entry still stuck in an in-progress state is dead:
      - if a finished FAISS index exists for it, promote it to 'ready';
      - otherwise drop it (this covers 'pending_*' placeholder tokens and any
        document whose processing was interrupted before it was indexed).

    Runs ONCE at startup only — never during a normal /documents poll — so it
    can never race with a document that is legitimately being processed now.
    """
    removed, recovered = [], []
    for doc_id, info in list(_doc_registry.items()):
        if info.get("status") in _IN_PROGRESS_STATES:
            if index_exists(doc_id):
                info["status"] = "ready"
                info["status_message"] = info.get("status_message") or "✅ Document ready."
                recovered.append(doc_id)
            else:
                del _doc_registry[doc_id]
                removed.append(doc_id)
    if removed or recovered:
        _save_registry()
        logger.info(
            f"Registry cleanup: removed {len(removed)} orphaned entries, "
            f"recovered {len(recovered)} interrupted-but-indexed docs."
        )


_load_registry()
_cleanup_orphaned_entries()


def get_doc_info(doc_id: str) -> Optional[Dict[str, Any]]:
    return _doc_registry.get(doc_id)


def get_all_docs() -> List[Dict[str, Any]]:
    # Serve from the in-memory registry, which is kept current on every write.
    # Re-reading the file on every poll previously swapped the dict object, which
    # left upload placeholders (`pending_*`) uncleaned — i.e. the ghost entries.
    return list(_doc_registry.values())


# ── Document Processing Pipeline ──────────────────────────────────────────────

async def process_document(
    file_path: str,
    original_filename: str,
    status_callback=None,
) -> str:
    """
    Full RAG pipeline for a document:
    1. Parse (text + links)
    2. Crawl hyperlinks
    3. Chunk all content
    4. Embed chunks (Gemini)
    5. Build FAISS index
    Returns doc_id.
    """
    doc_id = str(uuid.uuid4()).replace("-", "")[:8]

    def _update(msg: str, stage: str = "processing"):
        logger.info(f"[{doc_id}] {msg}")
        _doc_registry[doc_id] = _doc_registry.get(doc_id, {})
        _doc_registry[doc_id].update({
            "doc_id": doc_id,
            "filename": original_filename,
            "status": stage,
            "status_message": msg,
        })
        _save_registry()
        if status_callback:
            status_callback(doc_id, stage, msg)

    try:
        loop = asyncio.get_event_loop()

        # ── Step 1: Parse ──────────────────────────────────────────────────
        _update("📄 Parsing document...", "parsing")
        parsed = await loop.run_in_executor(None, parse_document, file_path)

        text_by_source = parsed.get("text_by_source", [])
        links = parsed.get("links", [])
        _update(f"✓ Parsed: {len(text_by_source)} sections, {len(links)} hyperlinks found", "parsing")

        # ── Step 2: Crawl Hyperlinks ──────────────────────────────────────
        crawled_sources = []
        if links:
            _update(f"🌐 Crawling {len(links)} hyperlinks...", "crawling")
            crawled = await loop.run_in_executor(None, crawl_urls, links)
            for url, text in crawled:
                # Include the originating filename in the source label
                # so the LLM can cite both the URL and the document it came from
                source_label = f"Source: {url} (linked from \"{original_filename}\")"
                crawled_sources.append((source_label, text))
            _update(f"✓ Crawled {len(crawled_sources)} URLs successfully", "crawling")

        # Combine document + crawled content
        all_sources = text_by_source + crawled_sources
        total_chars = sum(len(t) for _, t in all_sources)
        _update(f"📊 Total content: {total_chars:,} chars from {len(all_sources)} sources", "indexing")

        # ── Step 3: Chunk ──────────────────────────────────────────────────
        _update("✂️ Creating text chunks...", "indexing")
        chunks = await loop.run_in_executor(None, chunk_all_sources, all_sources, doc_id)
        _update(f"✓ Created {len(chunks)} chunks", "indexing")

        if not chunks:
            raise ValueError("No text content could be extracted from the document.")

        # ── Step 4: Embed (Gemini) ─────────────────────────────────────────
        _update(f"🧠 Generating Gemini embeddings for {len(chunks)} chunks...", "embedding")
        texts = [c["text"] for c in chunks]
        embeddings = await loop.run_in_executor(None, embed_texts, texts)
        _update(f"✓ Embeddings created — dim={embeddings.shape[1]}, chunks={len(chunks)}", "embedding")

        # ── Step 5: Build FAISS Index ──────────────────────────────────────
        _update("🔍 Building FAISS vector index...", "indexing")
        await loop.run_in_executor(None, build_index, doc_id, embeddings, chunks)

        _doc_registry[doc_id].update({
            "doc_id":        doc_id,
            "filename":      original_filename,
            "status":        "ready",
            "status_message": f"✅ Ready — {len(chunks)} chunks indexed",
            "num_chunks":    len(chunks),
            "num_links":     len(links),
            "num_crawled":   len(crawled_sources),
            "file_path":     file_path,
        })
        _save_registry()
        _update(f"✅ Document ready! {len(chunks)} chunks indexed.", "ready")
        logger.info(f"[{doc_id}] Processing complete.")
        return doc_id

    except Exception as e:
        error_msg = f"❌ Error: {str(e)}"
        _doc_registry[doc_id] = _doc_registry.get(doc_id, {})
        _doc_registry[doc_id].update({
            "doc_id":         doc_id,
            "filename":       original_filename,
            "status":         "error",
            "status_message": error_msg,
        })
        _save_registry()
        logger.exception(f"[{doc_id}] Processing failed: {e}")
        raise


# ── Question Answering ─────────────────────────────────────────────────────────

async def answer_question_stream(
    doc_id: str,
    question: str,
) -> AsyncGenerator[str, None]:
    """
    Retrieve relevant chunks and stream an answer using Gemini.
    Yields SSE-formatted data strings.
    """
    if not index_exists(doc_id):
        yield f"data: {json.dumps({'type': 'error', 'content': 'Document not indexed. Please upload it again.'})}\n\n"
        return

    try:
        import config as cfg

        # Embed query
        q_vec = embed_query(question)

        # Retrieve top-k chunks
        results = search(doc_id, q_vec, k=TOP_K)

        if not results:
            yield f"data: {json.dumps({'type': 'answer', 'content': '⚠️ No relevant content found in the document for your question.'})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return

        # Get the document filename for citation context
        doc_info = get_doc_info(doc_id)
        doc_filename = doc_info.get("filename", "uploaded document") if doc_info else "uploaded document"

        # Build context with citations
        context_parts = []
        sources_used = []
        for chunk in results:
            source = chunk["source"]
            text   = chunk["text"]
            context_parts.append(f"[{source}]\n{text}")
            if source not in sources_used:
                sources_used.append(source)

        context = "\n\n---\n\n".join(context_parts)

        full_prompt = f"""{SYSTEM_PROMPT}

DOCUMENT NAME: "{doc_filename}"

CONTEXT FROM DOCUMENT AND LINKED SOURCES:
{context}

---

QUESTION: {question}

Remember: Answer ONLY based on the context above. Use inline parenthetical references like (Page X) or (Table Y, Page Z) or (Linked URL) after each factual claim. Do NOT include a Sources Used section or any URLs in your answer. If the answer is not in the context, say so explicitly."""

        # Send sources first
        yield f"data: {json.dumps({'type': 'sources', 'sources': sources_used})}\n\n"

        # Run Gemini streaming (sync SDK) in a thread executor and yield tokens
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        _done_sentinel = object()

        def _producer():
            try:
                curr_client = genai.Client(api_key=cfg.GEMINI_API_KEY)
                stream = curr_client.models.generate_content_stream(
                    model=cfg.CHAT_MODEL,
                    contents=full_prompt,
                    config=genai.types.GenerateContentConfig(
                        temperature=0.0,
                        max_output_tokens=cfg.MAX_TOKENS_ANSWER,
                    ),
                )
                for chunk in stream:
                    if chunk.text:
                        asyncio.run_coroutine_threadsafe(queue.put(chunk.text), loop)
            except Exception as e:
                asyncio.run_coroutine_threadsafe(queue.put(("ERROR", str(e))), loop)
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(_done_sentinel), loop)

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(_producer)

            while True:
                item = await queue.get()
                if item is _done_sentinel:
                    break
                if isinstance(item, tuple) and item[0] == "ERROR":
                    yield f"data: {json.dumps({'type': 'error', 'content': f'Gemini error: {item[1]}'})}\n\n"
                    return
                yield f"data: {json.dumps({'type': 'token', 'content': item})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    except Exception as e:
        logger.exception(f"Answer generation failed: {e}")
        yield f"data: {json.dumps({'type': 'error', 'content': f'Error generating answer: {str(e)}'})}\n\n"
