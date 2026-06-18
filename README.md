# 🚀 Ragnify — AI Document Intelligence Platform

> **AI-powered RAG system for complex documents. Upload PDFs, Word docs, images — get instant, accurate answers with zero hallucination, powered by Google Gemini.**

![Ragnify](https://img.shields.io/badge/Powered%20by-Gemini%20%2B%20FAISS-blue?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.10%2B-green?style=for-the-badge)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?style=for-the-badge)

---

## ✨ Features

| Feature | Description |
|---|---|
| 📄 **Multi-format Support** | PDF, DOCX, JPG, PNG, PPTX, TIFF |
| 🌐 **Hyperlink Crawling** | Automatically crawls URLs found in documents (incl. linked PDFs) |
| 🧠 **Vector Search** | FAISS-powered semantic retrieval, isolated per document |
| 🤖 **Gemini Answers** | Streaming real-time answers via `gemini-2.5-flash-lite` |
| 🛡️ **Zero Hallucination** | Strictly grounded — no external knowledge used |
| ⚡ **Real-time Streaming** | Token-by-token streaming via Server-Sent Events |
| 📍 **Source Citations** | Every answer includes inline page/URL citations |
| 🚀 **Premium UI** | Premium glassmorphism interface |

---

## 🚀 Quick Start

### Option 1: One-click startup
```
Double-click: run.bat
```

### Option 2: Manual startup
```bash
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Then open: **http://localhost:8000**

---

## 🏗️ Architecture

```
Upload (PDF/DOCX/IMG)
    ↓
[Parser] → extract text per page + tables (Markdown) + all hyperlinks
    ↓
[Crawler] → crawl URLs concurrently (async httpx) + download linked PDFs
    ↓
[Chunker] → 1000-token chunks, 200-token overlap (tiktoken cl100k_base)
    ↓
[Embedder] → Gemini gemini-embedding-001 (3072-dim, batched + cached)
    ↓
[FAISS Index] → IndexFlatIP (cosine similarity), one index per document
    ↓
[Query] → embed question → top-25 chunks → gemini-2.5-flash-lite → stream answer
```

---

## 📁 Project Structure

```
Ragnify/
├── backend/
│   ├── main.py          ← FastAPI server (routes, SSE streaming)
│   ├── rag_engine.py    ← Full RAG pipeline orchestrator + registry
│   ├── parser.py        ← PDF/DOCX/Image/PPTX parser (+ tables)
│   ├── crawler.py       ← Async hyperlink crawler (+ linked PDFs)
│   ├── chunker.py       ← Token-aware text chunker
│   ├── embedder.py      ← Gemini embeddings + local cache
│   ├── vector_store.py  ← FAISS index management
│   ├── config.py        ← Settings & API keys
│   └── requirements.txt
├── frontend/
│   ├── index.html       ← Premium AI assistant UI
│   ├── style.css        ← Design system (glassmorphism)
│   └── app.js           ← Chat + upload + streaming logic
├── data/indexes/        ← Per-document FAISS indexes (auto-created)
├── uploads/             ← Temporary uploaded files
├── run.bat              ← One-click startup
└── install.bat          ← Dependency installer
```

---

## 🔧 Configuration

Edit `backend/config.py`:

```python
GEMINI_API_KEY  = "AIza..."                  # or set via .env / Settings modal
EMBEDDING_MODEL = "gemini-embedding-001"     # 3072-dim, best quality
CHAT_MODEL      = "gemini-2.5-flash-lite"    # fast, free-tier friendly
CHUNK_SIZE      = 1000   # tokens per chunk
CHUNK_OVERLAP   = 200    # overlap tokens
TOP_K           = 25     # chunks to retrieve
MAX_CRAWL_URLS  = 50     # max URLs to crawl per doc
```

The Gemini API key is resolved in this order: **environment variable `GEMINI_API_KEY`
→ `.env` file in the project root → hardcoded fallback**. You can also update it at
runtime via the in-app **Settings** modal, which persists it to the project-root `.env`.

---

## 📋 Supported File Types

| Type | Extension | Method |
|---|---|---|
| PDF | `.pdf` | PyMuPDF (fitz) — text + tables + hyperlinks |
| Word | `.docx`, `.doc` | python-docx |
| Images | `.jpg`, `.jpeg`, `.png`, `.bmp`, `.tiff` | Tesseract OCR |
| PowerPoint | `.pptx`, `.ppt` | python-pptx |

---

## 🌐 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/upload` | Upload a document |
| `GET` | `/documents` | List all documents |
| `GET` | `/documents/{id}` | Get document status |
| `POST` | `/chat` | Ask a question (SSE stream) |
| `DELETE` | `/documents/{id}` | Delete a document |
| `GET`/`POST` | `/settings` | View / update the Gemini API key |
| `GET` | `/health` | Health check |

---

## ⚙️ How It Works

1. **You upload** a PDF/DOCX/image
2. **Ragnify parses** all text content (by page for PDFs) and extracts tables as Markdown
3. **Hyperlinks are extracted** from the document
4. **URLs are crawled** concurrently — content extracted from each (linked PDFs too)
5. **All content is chunked** into 1000-token segments with 200-token overlap
6. **Embeddings are generated** via Gemini `gemini-embedding-001` (3072-dim)
7. **A per-document FAISS index is built** for semantic similarity search
8. **You ask a question** — it's embedded and the top-25 chunks retrieved
9. **Gemini generates an answer** using ONLY the retrieved context
10. **Answer streams** to your browser in real-time with inline source citations

---

## 🛡️ Anti-Hallucination Design

The system uses a strict system prompt that instructs Gemini to:
- Only use the provided context — no external knowledge
- If the answer isn't in the context: *"⚠️ This information is not found in the uploaded document or its linked sources."*
- Cite inline with short references like `(Page X)` or `(Table Y, Page Z)`
- Never extrapolate or assume
- Temperature = 0.0 for maximum determinism

---

## 🚀 Use Cases

- **Contract analysis** — extract terms, conditions, and obligations
- **Regulatory compliance** — find specific clauses in policy docs
- **Tenders & RFPs** — extract manpower, quantities, eligibility criteria
- **Research papers** — summarize findings and extract key data
- **Annual reports** — extract financial figures
- **Legal agreements** — identify key obligations

---

*Built with ❤️ for professionals who need instant, accurate document intelligence.*
