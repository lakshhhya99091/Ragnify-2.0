"""
Ragnify Configuration — Gemini Edition
"""
import os

# ── Project paths ───────────────────────────────────────────────────────────────
# Defined up-front so the .env loader (below) and the /settings writer (main.py)
# agree on exactly one location. Previously the key was saved to a different path
# than the one read on startup, so an updated key was silently lost on restart.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(BASE_DIR, ".env")

# ── Gemini API ────────────────────────────────────────────────────────────────
# Priority: Environment variable > .env file > hardcoded key
_env_key = os.environ.get("GEMINI_API_KEY", "")

# Try to load from .env file if exists
if not _env_key and os.path.exists(ENV_FILE):
    with open(ENV_FILE) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line.startswith("GEMINI_API_KEY="):
                _env_key = _line.split("=", 1)[1].strip().strip('"').strip("'")
                break

GEMINI_API_KEY = _env_key or "AIzaSyAD8q7stG0X7lYeZipZDFZ6eXJJwEQhMj8"

# ── Models ────────────────────────────────────────────────────────────────────
EMBEDDING_MODEL = "gemini-embedding-001"    # 3072-dim, best quality
CHAT_MODEL      = "gemini-2.5-flash-lite"  # Confirmed working on free tier
MAX_TOKENS_ANSWER = 4096                    # Increased for detailed answers with citations

# ── Chunking ──────────────────────────────────────────────────────────────────
# Increased chunk size to keep tables and complex tender specs together
CHUNK_SIZE    = 1000  # tokens per chunk (Gemini embedding max is 2048)
CHUNK_OVERLAP = 200   # high overlap to avoid cutting sentences/rows in half

# ── Retrieval ─────────────────────────────────────────────────────────────────
# Increased top_k because Gemini Flash has a massive context window, so we can
# feed it more chunks to ensure no numbers/specs are missed.
TOP_K = 25            # Top-k chunks to retrieve

# ── Crawler ───────────────────────────────────────────────────────────────────
MAX_CRAWL_URLS    = 50      # Max hyperlinks to crawl per document (increased from 30)
CRAWL_TIMEOUT     = 15      # Seconds per URL request (increased from 10 for slow sites)
MAX_CRAWL_WORKERS = 10      # Concurrent crawl workers (increased from 8)

# ── Paths ─────────────────────────────────────────────────────────────────────
# Prefer environment variables (set by render.yaml for Render deployment with disk).
# Falls back to local directory layout for free tier / development.
# (BASE_DIR is defined at the top of this file alongside ENV_FILE.)
_default_data   = os.path.join(BASE_DIR, "data", "indexes")
_default_upload = os.path.join(BASE_DIR, "uploads")

DATA_DIR    = os.environ.get("DATA_DIR",   _default_data)
UPLOAD_DIR  = os.environ.get("UPLOAD_DIR", _default_upload)

# If the configured path is not writable (e.g. /data on free tier without disk),
# fall back to the local path inside the repo which is always writable.
def _ensure_dir(path: str, fallback: str) -> str:
    try:
        os.makedirs(path, exist_ok=True)
        # Quick write-test
        test = os.path.join(path, ".writetest")
        open(test, "w").close()
        os.remove(test)
        return path
    except (OSError, PermissionError):
        os.makedirs(fallback, exist_ok=True)
        return fallback

DATA_DIR   = _ensure_dir(DATA_DIR,   _default_data)
UPLOAD_DIR = _ensure_dir(UPLOAD_DIR, _default_upload)

# ── Anti-hallucination system prompt ──────────────────────────────────────────
SYSTEM_PROMPT = """You are Ragnify — an elite, highly precise document intelligence assistant for professionals across banking, government, enterprise, and all industries.

CRITICAL RULES (follow strictly):
1. Answer ONLY based on the provided CONTEXT from the document and its linked sources.
2. If the answer cannot be found in the CONTEXT, respond EXACTLY: "⚠️ This information is not found in the uploaded document or its linked sources."
3. NEVER use your training knowledge to fill gaps. Only use what is in CONTEXT.
4. Pay EXTRA ATTENTION to numerical values, manpower requirements, quantities, specifications, and eligibility criteria, especially in tender notices, RFPs, and contracts.
5. When extracting numbers (e.g., "4 engineers", "2 bankers", pricing, dates), extract them exactly as they appear in the text or tables.
6. If data is presented in a table format in the context, understand the row/column relationship to provide accurate answers.
7. Be precise, factual, and concise. Do not speculate or extrapolate.
8. If a question is ambiguous, ask for clarification rather than guessing.

CITATION FORMAT (strictly follow):
- Place small textual location references INLINE right after the relevant statement.
- Use parenthetical references like: (Page 3, Line 14-18), (Table 2, Column 1, Page 5), (Section 4.1, Page 8), (Linked URL)
- References must be SHORT plain-text markers only.
- Do NOT include raw URLs in your answer text.
- Do NOT include markdown hyperlinks in your answer text.
- Do NOT include "[Source: URL]" or similar link citations in your answer.

ANSWER FORMAT:
- Direct answer first
- Supporting details with inline parenthetical references
- Bullet points for lists of facts or requirements
- Markdown tables if summarizing multiple tabular data points

STRICTLY DO NOT:
- Do NOT add a "Sources Used" section at the end of your answer.
- Do NOT add a "📎 Sources Used" footer.
- Do NOT list sources, references, or URLs at the end of your answer.
- Do NOT include any URL strings anywhere in your answer body.
- The answer must end with the final piece of content — no trailing source lists.
"""

