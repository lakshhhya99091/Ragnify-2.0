# 🚀 Ragnify — AI Document Intelligence Platform

> **AI-powered RAG system for banking documents. Upload PDFs, Word docs, images — get instant, accurate answers with zero hallucination.**

![Ragnify](https://img.shields.io/badge/Powered%20by-GPT--4o%20%2B%20FAISS-blue?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.10%2B-green?style=for-the-badge)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?style=for-the-badge)

---

## ✨ Features

| Feature | Description |
|---|---|
| 📄 **Multi-format Support** | PDF, DOCX, JPG, PNG, PPTX, TIFF |
| 🌐 **Hyperlink Crawling** | Automatically crawls all URLs found in documents |
| 🧠 **Vector Search** | FAISS-powered semantic retrieval |
| 🤖 **GPT-4o Answers** | Streaming real-time answers via OpenAI |
| 🛡️ **Zero Hallucination** | Strictly grounded — no external knowledge used |
| ⚡ **Real-time Streaming** | Token-by-token streaming via Server-Sent Events |
| 📍 **Source Citations** | Every answer includes page/URL citations |
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
[Parser] → extract text per page + all hyperlinks
    ↓
[Crawler] → crawl URLs concurrently (async httpx)
    ↓
[Chunker] → 500-token chunks, 80-token overlap (tiktoken)
    ↓
[Embedder] → OpenAI text-embedding-3-small (batched)
    ↓
[FAISS Index] → IndexFlatIP (cosine similarity)
    ↓
[Query] → embed question → top-10 chunks → GPT-4o → stream answer
```

---

## 📁 Project Structure

```
Ragnify/
├── backend/
│   ├── main.py          ← FastAPI server (routes, SSE streaming)
│   ├── rag_engine.py    ← Full RAG pipeline orchestrator
│   ├── parser.py        ← PDF/DOCX/Image parser
│   ├── crawler.py       ← Async hyperlink crawler
│   ├── chunker.py       ← Token-aware text chunker
│   ├── embedder.py      ← OpenAI embeddings + cache
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
OPENAI_API_KEY  = "your-key-here"
EMBEDDING_MODEL = "text-embedding-3-small"   # fast & affordable
CHAT_MODEL      = "gpt-4o"                   # best reasoning
CHUNK_SIZE      = 500    # tokens per chunk
CHUNK_OVERLAP   = 80     # overlap tokens
TOP_K           = 10     # chunks to retrieve
MAX_CRAWL_URLS  = 30     # max URLs to crawl per doc
```

---

## 📋 Supported File Types

| Type | Extension | Method |
|---|---|---|
| PDF | `.pdf` | PyMuPDF (fitz) — text + hyperlinks |
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
| `GET` | `/health` | Health check |

---

## ⚙️ How It Works

1. **You upload** a PDF/DOCX/image
2. **Ragnify parses** all text content (by page for PDFs)
3. **Hyperlinks are extracted** from the document
4. **URLs are crawled** concurrently — content extracted from each
5. **All content is chunked** into 500-token segments with overlap
6. **Embeddings are generated** via OpenAI text-embedding-3-small
7. **FAISS index is built** for semantic similarity search
8. **You ask a question** — it's embedded and top-10 chunks retrieved
9. **GPT-4o generates an answer** using ONLY the retrieved context
10. **Answer streams** to your browser in real-time with source citations

---

## 🛡️ Anti-Hallucination Design

The system uses a strict system prompt that instructs GPT-4o:
- Only use the provided context — no external knowledge
- If the answer isn't in the context: *"This information is not found in the uploaded document or its linked sources."*
- Always cite: `[Page X]` or `[Source: URL]`
- Never extrapolate or assume
- Temperature = 0.0 for maximum determinism

---

## 🚀 Use Cases

- **Contract analysis** — extract terms, conditions, and obligations
- **Regulatory compliance** — find specific clauses in policy docs
- **Research papers** — summarize findings and extract key data
- **Insurance policies** — locate coverage details
- **Annual reports** — extract financial figures
- **Legal agreements** — identify key obligations

---

*Built with ❤️ for professionals who need instant, accurate document intelligence.*
