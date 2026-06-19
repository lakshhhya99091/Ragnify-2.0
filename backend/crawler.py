"""
Hyperlink Crawler — fetches content from URLs found in documents.
Returns cleaned text chunks tagged with their source URL.
Enhanced: PDF download support, retry logic, broader domain coverage.
"""
import asyncio
import hashlib
import logging
import re
from typing import List, Tuple, Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from config import MAX_CRAWL_URLS, CRAWL_TIMEOUT, MAX_CRAWL_WORKERS

logger = logging.getLogger(__name__)

# ── Only skip truly useless binary / media / asset extensions ─────────────────
SKIP_EXTENSIONS = {
    ".zip", ".rar", ".gz", ".tar", ".7z",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".ico", ".webp",
    ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav", ".flac", ".ogg",
    ".exe", ".dll", ".so", ".dylib", ".msi",
    ".css", ".woff", ".woff2", ".ttf", ".eot",
    ".ppt", ".pptx",  # Skip presentations (can't parse inline)
}

# ── Only skip domains that never have useful readable content ─────────────────
# Removed: linkedin.com, github.com (they have useful profile/repo data)
# Removed: google.com (some links go to docs.google.com which is useful)
SKIP_DOMAINS = {
    "youtube.com", "youtu.be",
    "twitter.com", "x.com",
    "facebook.com", "instagram.com", "tiktok.com",
    "fonts.googleapis.com", "cdn.jsdelivr.net",
    "cloudflare.com", "gstatic.com",
    "play.google.com", "apps.apple.com",
}

# Max size for downloading linked PDFs (5 MB)
MAX_PDF_DOWNLOAD_SIZE = 5 * 1024 * 1024
# Max number of linked PDFs to parse per document
MAX_LINKED_PDFS = 10

# Common headers for requests
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _should_skip(url: str) -> bool:
    """Return True if URL should not be crawled."""
    try:
        parsed = urlparse(url)
        # NOTE: use removeprefix, not lstrip("www.") — lstrip strips any leading
        # 'w'/'.' characters, which mangles domains like "web.x.com" -> "eb.x.com".
        domain = parsed.netloc.lower().removeprefix("www.")

        # Check exact domain and parent domain
        if domain in SKIP_DOMAINS:
            return True
        # Check if it's a subdomain of a skip domain
        for skip in SKIP_DOMAINS:
            if domain.endswith("." + skip):
                return True

        # Check file extension
        path = parsed.path.lower()
        if "." in path:
            ext = "." + path.rsplit(".", 1)[-1]
            if ext in SKIP_EXTENSIONS:
                return True

        return False
    except Exception:
        return True


def _is_pdf_url(url: str) -> bool:
    """Check if URL likely points to a PDF."""
    path = urlparse(url).path.lower()
    return path.endswith(".pdf")


def _clean_html(html: str, url: str) -> str:
    """Extract readable text from HTML with improved quality."""
    soup = BeautifulSoup(html, "html.parser")

    # Extract page title first
    title = ""
    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        title = title_tag.string.strip()

    # Remove noise elements
    for tag in soup(["script", "style", "nav", "footer", "header",
                     "aside", "noscript", "iframe", "form", "button",
                     "meta", "link", "svg", "img"]):
        tag.decompose()

    # Try to find main content area (prioritize article/main)
    main_content = (
        soup.find("article") or
        soup.find("main") or
        soup.find(id=re.compile(r"content|main|article|body|post", re.I)) or
        soup.find(class_=re.compile(r"content|main|article|post|entry|body", re.I)) or
        soup.body or
        soup
    )

    text = main_content.get_text(separator="\n", strip=True)

    # Clean up excessive whitespace but keep short lines (bullets, data points)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    # Only remove very short noise lines (< 10 chars) — keep bullets, specs
    lines = [l for l in lines if len(l) > 10 or any(c.isdigit() for c in l)]
    text = "\n".join(lines)

    # Prepend title if meaningful and not already in text
    if title and len(title) > 5 and title not in text[:200]:
        text = f"Page Title: {title}\n\n{text}"

    # Truncate to 15000 chars max per URL (increased from 8000)
    return text[:15000]


def _extract_pdf_text(pdf_bytes: bytes, url: str) -> Optional[str]:
    """Extract text from downloaded PDF bytes using PyMuPDF."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("PyMuPDF not installed, cannot parse linked PDF")
        return None

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text_parts = []
        for page_num, page in enumerate(doc, start=1):
            page_text = page.get_text("text")
            if page_text.strip():
                text_parts.append(f"[Page {page_num}]\n{page_text}")
        doc.close()

        if text_parts:
            full_text = "\n\n".join(text_parts)
            # Cap linked PDFs at 60000 chars so long RFP annexures / BOQ tables
            # (often near the end of the document) are captured, not truncated.
            return full_text[:60000]
        return None
    except Exception as e:
        logger.warning(f"Failed to parse linked PDF from {url}: {e}")
        return None


async def _fetch_one(
    client: httpx.AsyncClient,
    url: str,
    pdf_count: list,
) -> Tuple[str, Optional[str]]:
    """Fetch a single URL with retry logic. Returns (url, clean_text or None)."""
    max_retries = 2

    for attempt in range(max_retries):
        try:
            resp = await client.get(
                url,
                timeout=CRAWL_TIMEOUT,
                follow_redirects=True,
                headers=_HEADERS,
            )

            if resp.status_code == 200:
                content_type = resp.headers.get("content-type", "").lower()

                # Handle PDF responses
                if "application/pdf" in content_type or (
                    _is_pdf_url(url) and "text/html" not in content_type
                ):
                    if pdf_count[0] >= MAX_LINKED_PDFS:
                        logger.debug(f"Skipped PDF {url} (max linked PDFs reached)")
                        return url, None

                    content_length = int(resp.headers.get("content-length", 0))
                    if content_length > MAX_PDF_DOWNLOAD_SIZE:
                        logger.debug(f"Skipped PDF {url} (too large: {content_length} bytes)")
                        return url, None

                    pdf_bytes = resp.content
                    if len(pdf_bytes) > MAX_PDF_DOWNLOAD_SIZE:
                        logger.debug(f"Skipped PDF {url} (downloaded too large)")
                        return url, None

                    pdf_count[0] += 1
                    text = _extract_pdf_text(pdf_bytes, url)
                    if text and len(text) > 50:
                        logger.info(f"✓ Parsed linked PDF {url} ({len(text)} chars)")
                        return url, text
                    return url, None

                # Handle HTML / plain text
                if "text/html" in content_type or "text/plain" in content_type:
                    text = _clean_html(resp.text, url)
                    if len(text) > 50:  # Lowered from 100 to catch small but useful pages
                        logger.info(f"✓ Crawled {url} ({len(text)} chars)")
                        return url, text
                    else:
                        logger.debug(f"Skipped {url} (too short: {len(text)} chars)")
                        return url, None

                # Handle JSON responses (some APIs return useful JSON)
                if "application/json" in content_type:
                    try:
                        import json
                        data = json.loads(resp.text)
                        # Convert JSON to readable text
                        text = json.dumps(data, indent=2, ensure_ascii=False)[:10000]
                        if len(text) > 50:
                            logger.info(f"✓ Parsed JSON from {url} ({len(text)} chars)")
                            return url, f"JSON Data from {url}:\n{text}"
                    except Exception:
                        pass
                    return url, None

                # Handle XML responses
                if "xml" in content_type:
                    text = _clean_html(resp.text, url)
                    if len(text) > 50:
                        logger.info(f"✓ Parsed XML from {url} ({len(text)} chars)")
                        return url, text
                    return url, None

                logger.debug(f"Skipped {url} (unsupported content type: {content_type})")
                return url, None

            elif resp.status_code in (429, 503) and attempt < max_retries - 1:
                # Rate limited or service unavailable — retry with backoff
                wait = 2 ** attempt + 1
                logger.warning(f"HTTP {resp.status_code} for {url}, retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue
            else:
                logger.warning(f"HTTP {resp.status_code} for {url}")
                return url, None

        except httpx.TimeoutException:
            if attempt < max_retries - 1:
                logger.warning(f"Timeout for {url}, retrying...")
                await asyncio.sleep(1)
                continue
            logger.warning(f"Failed to crawl {url}: timeout after {max_retries} attempts")
            return url, None
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Error crawling {url}: {e}, retrying...")
                await asyncio.sleep(1)
                continue
            logger.warning(f"Failed to crawl {url}: {e}")
            return url, None

    return url, None


async def _crawl_all_async(urls: List[str]) -> List[Tuple[str, str]]:
    """Crawl all URLs concurrently and return (url, text) pairs."""
    results = []
    pdf_count = [0]  # Mutable counter for linked PDFs
    limits = httpx.Limits(
        max_connections=MAX_CRAWL_WORKERS,
        max_keepalive_connections=MAX_CRAWL_WORKERS,
    )
    seen_content = set()  # content hashes, to skip identical docs fetched via different URLs
    async with httpx.AsyncClient(limits=limits, verify=False) as client:
        tasks = [_fetch_one(client, url, pdf_count) for url in urls]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        for resp in responses:
            if isinstance(resp, tuple):
                url, text = resp
                if not text:
                    continue
                chash = hashlib.md5(text.encode("utf-8", "ignore")).hexdigest()
                if chash in seen_content:
                    logger.info(f"Skipped {url} (identical content already crawled)")
                    continue
                seen_content.add(chash)
                results.append((url, text))
            elif isinstance(resp, Exception):
                logger.warning(f"Crawl task exception: {resp}")
    return results


def crawl_urls(urls: List[str]) -> List[Tuple[str, str]]:
    """
    Synchronous wrapper for async crawl.
    Filters, deduplicates, and crawls up to MAX_CRAWL_URLS.
    Returns list of (url, clean_text) tuples.
    Always runs in a dedicated thread to avoid event loop conflicts with FastAPI.
    """
    # Filter and deduplicate
    filtered = []
    seen = set()
    for url in urls:
        url = url.strip().rstrip(".,;)")
        # Normalize URL for dedup
        normalized = url.rstrip("/")
        if normalized not in seen and not _should_skip(url):
            seen.add(normalized)
            filtered.append(url)

    # Limit
    filtered = filtered[:MAX_CRAWL_URLS]
    logger.info(f"Crawling {len(filtered)} URLs (from {len(urls)} found, {len(urls) - len(filtered)} filtered)")

    if not filtered:
        return []

    # Always run in a separate thread with its own event loop
    # This avoids conflicts when called from FastAPI's async context via run_in_executor
    import concurrent.futures

    def _run_in_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_crawl_all_async(filtered))
        finally:
            loop.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_run_in_thread)
        return future.result(timeout=CRAWL_TIMEOUT * MAX_CRAWL_URLS + 60)
