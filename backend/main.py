"""
Ragnify FastAPI Backend
"""
import logging
import os
import uuid
from pathlib import Path

import aiofiles
from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import UPLOAD_DIR
from rag_engine import (
    process_document,
    answer_question_stream,
    get_doc_info,
    get_all_docs,
)
from vector_store import delete_index
from extractor import load_extraction, tender_rows
from excel_export import build_tender_xlsx, build_asset_xlsx, safe_filename

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── FastAPI App ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Ragnify — Smart Document Intelligence",
    version="2.3.0",
    description="RAG-powered document intelligence for professionals — powered by Gemini and FAISS",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend static files
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# ── Models ────────────────────────────────────────────────────────────────────
class QuestionRequest(BaseModel):
    doc_id: str
    question: str


class SettingsRequest(BaseModel):
    gemini_api_key: str


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    """Serve the frontend."""
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Ragnify API running. Frontend not found."}


@app.get("/settings")
async def get_settings():
    """Return current settings (API key masked)."""
    from config import GEMINI_API_KEY
    key = GEMINI_API_KEY or ""
    masked = (key[:8] + "..." + key[-4:]) if len(key) > 12 else "not set"
    return {"api_key_masked": masked, "has_key": bool(key), "provider": "Google Gemini"}


@app.post("/settings")
async def update_settings(req: SettingsRequest):
    """Update Gemini API key at runtime and persist to .env file."""
    import config as cfg_module

    new_key = req.gemini_api_key.strip()
    # Light sanity check only. Gemini key formats change over time — both the
    # older "AIza…" and the newer "AQ.…" keys are valid — so we do NOT hard-reject
    # by prefix (that previously blocked valid new-format keys).
    if len(new_key) < 20 or any(c.isspace() for c in new_key):
        raise HTTPException(status_code=400, detail="That doesn't look like a valid API key.")

    is_openai = new_key.startswith("sk-")  # everything else is treated as a Gemini key

    # Update runtime config
    cfg_module.GEMINI_API_KEY = new_key

    # Persist to the SAME .env file that config.py reads on startup, otherwise the
    # updated key would be silently lost on the next restart.
    from config import ENV_FILE
    os.makedirs(os.path.dirname(ENV_FILE), exist_ok=True)
    with open(ENV_FILE, "w") as f:
        f.write(f'{"OPENAI_API_KEY" if is_openai else "GEMINI_API_KEY"}="{new_key}"\n')

    logger.info(f"API key updated: {new_key[:8]}...")
    return {
        "message": "API key updated successfully",
        "api_key_masked": new_key[:8] + "..." + new_key[-4:],
        "provider": "OpenAI" if is_openai else "Google Gemini",
    }


@app.post("/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """
    Upload a document and kick off the RAG processing pipeline.
    Returns immediately with a doc_id; processing happens in background.
    """
    # Validate file type
    allowed_extensions = {
        ".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png",
        ".bmp", ".tiff", ".tif", ".pptx", ".ppt",
    }
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{file_ext}'. Supported: {sorted(allowed_extensions)}",
        )

    # Save uploaded file
    unique_prefix = str(uuid.uuid4()).replace("-", "")[:8]
    safe_name = f"{unique_prefix}{file_ext}"
    file_path = os.path.join(UPLOAD_DIR, safe_name)
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    async with aiofiles.open(file_path, "wb") as f:
        content = await file.read()
        await f.write(content)

    logger.info(f"Saved upload: {file.filename} → {file_path}")

    # Pre-register a temp placeholder so the client can poll
    from rag_engine import _doc_registry, _save_registry
    temp_token = f"pending_{unique_prefix}"
    _doc_registry[temp_token] = {
        "doc_id": temp_token,
        "filename": file.filename,
        "status": "uploading",
        "status_message": "📤 File received, starting processing...",
        "file_path": file_path,
    }
    _save_registry()

    # Process in background, remove placeholder when done
    async def _process_and_cleanup():
        try:
            doc_id = await process_document(file_path, file.filename)
            # Remove placeholder
            if temp_token in _doc_registry:
                del _doc_registry[temp_token]
                _save_registry()
            logger.info(f"Processing complete: {doc_id}")
        except Exception as e:
            # process_document already recorded the failure under its own doc_id,
            # so just drop the placeholder to avoid a duplicate error row.
            if temp_token in _doc_registry:
                del _doc_registry[temp_token]
                _save_registry()
            logger.exception(f"Processing failed: {e}")

    background_tasks.add_task(_process_and_cleanup)

    return {
        "temp_token": temp_token,
        "filename": file.filename,
        "message": "Upload received. Processing started. Poll /documents to track progress.",
    }


@app.get("/documents")
async def list_documents():
    """List all indexed documents."""
    docs = get_all_docs()
    return {"documents": docs}


@app.get("/documents/{doc_id}")
async def get_document(doc_id: str):
    """Get status/info for a specific document."""
    info = get_doc_info(doc_id)
    if not info:
        # Search by partial match
        all_docs = get_all_docs()
        for doc in all_docs:
            if doc.get("doc_id", "").startswith(doc_id):
                return doc
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found")
    return info


@app.post("/chat")
async def chat(req: QuestionRequest):
    """
    Stream an answer to a question about a document.
    Uses Server-Sent Events (SSE) for real-time token streaming.
    """
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    doc_info = get_doc_info(req.doc_id)
    if not doc_info:
        raise HTTPException(status_code=404, detail=f"Document '{req.doc_id}' not found")

    if doc_info.get("status") != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Document is not ready yet (status: {doc_info.get('status')}). Please wait.",
        )

    return StreamingResponse(
        answer_question_stream(req.doc_id, req.question),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Bid extraction: tender & asset details ──────────────────────────────────────

def _require_extraction(doc_id: str):
    """Return (doc_info, extraction) or raise 404 if missing/not a bid."""
    info = get_doc_info(doc_id)
    if not info:
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found")
    data = load_extraction(doc_id)
    if not data:
        raise HTTPException(
            status_code=404,
            detail="No tender/asset extraction is available for this document.",
        )
    return info, data


_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@app.get("/documents/{doc_id}/tender-details")
async def tender_details(doc_id: str):
    """Extracted tender details as ordered label/value rows."""
    info, data = _require_extraction(doc_id)
    rows = [{"label": label, "value": value} for label, value in tender_rows(data)]
    return {"doc_id": doc_id, "filename": info.get("filename"), "rows": rows}


@app.get("/documents/{doc_id}/asset-details")
async def asset_details(doc_id: str):
    """Extracted, cross-source-aggregated asset list with a grand total."""
    info, data = _require_extraction(doc_id)
    assets = data.get("asset_details", []) or []
    grand_total = sum(int(a.get("quantity", 0) or 0) for a in assets)
    return {
        "doc_id": doc_id,
        "filename": info.get("filename"),
        "assets": assets,
        "grand_total": grand_total,
    }


@app.get("/documents/{doc_id}/tender-details.xlsx")
async def tender_details_xlsx(doc_id: str):
    info, data = _require_extraction(doc_id)
    content = build_tender_xlsx(data)
    fname = safe_filename(info.get("filename", "document"), "tender_details")
    return Response(
        content=content,
        media_type=_XLSX_MEDIA,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/documents/{doc_id}/asset-details.xlsx")
async def asset_details_xlsx(doc_id: str):
    info, data = _require_extraction(doc_id)
    content = build_asset_xlsx(data)
    fname = safe_filename(info.get("filename", "document"), "asset_details")
    return Response(
        content=content,
        media_type=_XLSX_MEDIA,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    """Delete a document's index and metadata."""
    from rag_engine import _doc_registry, _save_registry

    if doc_id not in _doc_registry:
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found")

    # Delete FAISS index
    deleted = delete_index(doc_id)

    # Delete uploaded file
    doc_info = _doc_registry.get(doc_id, {})
    file_path = doc_info.get("file_path", "")
    if file_path and os.path.exists(file_path):
        os.remove(file_path)

    # Remove from registry
    del _doc_registry[doc_id]
    _save_registry()

    return {"message": f"Document '{doc_id}' deleted", "index_deleted": deleted}


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "Ragnify", "version": app.version}


# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
