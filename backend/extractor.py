"""
Bid/Tender Extractor — detects GeM-style tender documents and extracts:
  1. Tender Details  — the key/value fields used in the buyer's cost sheet.
  2. Asset Details   — consolidated equipment/product list with quantities,
                       AGGREGATED across the main document and every crawled
                       hyperlink / linked PDF (same model => summed; different
                       models => separate rows), with a per-source breakdown.

Extraction is grounded: it uses ONLY the supplied document + crawled content.
Results are cached as extraction.json inside the document's index folder.
"""
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

from google import genai
from google.genai import types

from config import DATA_DIR

logger = logging.getLogger(__name__)

# ── Canonical Tender Details schema ─────────────────────────────────────────────
# (machine_key, display_label, hint). Display labels match the buyer's cost sheet.
TENDER_FIELDS: List[Tuple[str, str, str]] = [
    ("bid_no_and_date",          "BID No & Date",                                   "Bid number and its dated/published date"),
    ("customer_name",            "Customer Name",                                   "Buyer organisation / ministry / department / bank name"),
    ("city_state",               "City/State",                                      "City and/or state of the consignee / delivery location"),
    ("single_multilocation",     "Single / Multilocation Support",                  "'Single' if one consignee/location, else 'Multilocation'"),
    ("bid_due_date",             "BID Due date",                                    "Bid end / closing date and time"),
    ("estimated_tender_cost",    "Estimated Tender Cost",                           "Estimated bid value if stated, else NA"),
    ("contract_period",          "Contract period",                                 "Contract / AMC duration"),
    ("bid_type",                 "BID Type - Enterprise/ EUC /FMS",                 "Enterprise, EUC or FMS if derivable, else NA"),
    ("tender_fee_msme_exemption","Tender FEE- MSME Exemption (Yes/No)",             "Yes/No: tender-fee MSME exemption"),
    ("emd_required",             "EMD Required (MSME Exemption (Yes/No)",           "EMD required? note exemption if any"),
    ("pgb_required_pct",         "PGB Required - %",                                "ePBG / performance guarantee percentage, else NA"),
    ("proactive_reactive",       "Proactive / Reactive BID",                        "Proactive or Reactive if derivable, else NA"),
    ("tender_submission",        "Tender Submission (Online / Offline )",           "Online or Offline"),
    ("ra_enabled",               "RA Enabled (Yes /No)",                            "Reverse Auction enabled? Yes/No"),
    ("oem_maf_required",         "Any OEM Authorisation MAF Required (Yes / No)",   "OEM authorisation / MAF required? Yes/No"),
    ("specific_tc",              "Any Specific T&C",                                "Short note of notable special terms, else NA"),
    ("pm_schedule",              "PM Schedule (Monthly /Qtrly /Half yearly / Yearly /NA)", "Preventive maintenance frequency"),
    ("no_of_technicians",        "No of technicians required",                      "Number of technicians / manpower required, else NA"),
    ("min_wages_criteria",       "Need to Follow Min Wages Criteria (State / Central)", "State or Central minimum wages, else NA"),
    ("penalty_capping",          "Penalty capping",                                 "Penalty cap if stated, else NA"),
    ("payment_terms",            "Payment Terms",                                   "Payment terms / mode"),
    ("existing_vendor",          "Existing Vendor",                                 "INTERNAL field — almost never in the tender; return NA"),
    ("approx_running_cost",      "Appox Contract Running Cost with Existing vendor (if Available)", "INTERNAL field — return NA"),
    ("inhouse_b2b",              "InHouse / B2B with Partner",                      "INTERNAL field — return NA"),
]

_TENDER_KEYS = [k for k, _, _ in TENDER_FIELDS]

# ── Bid detection ───────────────────────────────────────────────────────────────
_BID_MARKERS = [
    r"gem/\d", r"bid number", r"bid end date", r"bid opening", r"estimated bid value",
    r"\bconsignee", r"notice inviting", r"\brfp\b", r"\brfq\b", r"\bepbg\b",
    r"buyer added bid", r"two packet bid", r"bid details", r"\bemd\b", r"tender",
]


def detect_bid(full_text: str) -> bool:
    """Heuristic: a document is a tender/bid if it hits several GeM/tender markers."""
    if not full_text:
        return False
    low = full_text.lower()
    hits = sum(1 for pat in _BID_MARKERS if re.search(pat, low))
    return hits >= 3


# ── Source labelling ────────────────────────────────────────────────────────────
def _friendly_source(label: str) -> str:
    """Turn an internal source label into a short, human-readable one."""
    m = re.search(r"(https?://[^\s\"')]+)", label)
    if m:
        host = urlparse(m.group(1)).netloc.removeprefix("www.")
        return host or m.group(1)
    return f"Main document ({label})"


def _build_sources_block(all_sources: List[Tuple[str, str]], max_chars: int = 500_000) -> str:
    """Concatenate (label, text) pairs into labelled blocks, capped in size."""
    parts, total = [], 0
    for label, text in all_sources:
        if not text or not text.strip():
            continue
        block = f"\n[SOURCE: {_friendly_source(label)}]\n{text.strip()}\n"
        if total + len(block) > max_chars:
            block = block[: max(0, max_chars - total)]
            parts.append(block)
            break
        parts.append(block)
        total += len(block)
    return "".join(parts)


# ── Prompt ──────────────────────────────────────────────────────────────────────
def _build_prompt(sources_block: str, filename: str) -> str:
    field_lines = "\n".join(f'  - "{k}": {hint}' for k, _, hint in TENDER_FIELDS)
    return f"""You are a precise data-extraction engine for Indian government e-procurement (GeM) tenders/bids.
Extract ONLY facts present in the SOURCES below. Never use outside knowledge. If a value is absent, use the string "NA".

Document filename: "{filename}"

Return STRICT, MINIFIED JSON ONLY (no markdown, no commentary) with exactly this shape:
{{
  "tender_details": {{ "bid_no_and_date": "...", "...": "..." }},
  "asset_details": [
    {{ "description": "item name with brand/model if given", "quantity": 6,
       "sources": [ {{ "source": "Main document (Page 4)", "quantity": 2 }},
                    {{ "source": "example.com", "quantity": 4 }} ] }}
  ]
}}

TENDER_DETAILS — include every one of these keys (value = string, or "NA" if not found):
{field_lines}

ASSET_DETAILS rules:
  - List every distinct product / equipment / service that has a quantity
    (e.g., Desktop PC, Laptop, Printer, Scanner, Multifunction Printer, UPS, AIO PC).
  - IMPORTANT: search ALL sources thoroughly, ESPECIALLY linked RFP / Scope of Work /
    BOQ / Annexure / price-bid / "list of equipment" documents — the itemised list with
    quantities is usually given there as a table (e.g. Sr.No | Item | Qty). Extract every row.
  - Put brand/model in "description" when the document specifies it (e.g., "Dell Latitude 5520 Laptop").
  - AGGREGATE: if the SAME item/model appears in multiple SOURCES, SUM into ONE row and
    list each contributing source with its own quantity under "sources". "quantity" is the total.
  - Keep DIFFERENT models/items as SEPARATE rows.
  - "source" must be the SOURCE label shown for the block the number came from.
  - "quantity" values must be integers. If a quantity is unclear, omit that item.
  - If NO asset quantities are found anywhere, return "asset_details": [].

SOURCES:
{sources_block}
"""


# ── JSON parsing helpers ─────────────────────────────────────────────────────────
def _coerce_int(v: Any) -> int:
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        m = re.search(r"-?\d[\d,]*", v)
        if m:
            return int(m.group(0).replace(",", ""))
    return 0


def _parse_response(raw: str) -> Dict[str, Any]:
    """Parse the model's JSON, tolerating code fences / surrounding text."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise
        data = json.loads(text[start : end + 1])
    return data


def _normalize(data: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure all tender keys exist and asset rows are well-formed."""
    raw_td = data.get("tender_details") or {}
    tender = {k: (str(raw_td.get(k)).strip() if raw_td.get(k) not in (None, "") else "NA")
              for k in _TENDER_KEYS}

    assets = []
    for item in (data.get("asset_details") or []):
        if not isinstance(item, dict):
            continue
        desc = str(item.get("description", "")).strip()
        if not desc:
            continue
        srcs = []
        for s in (item.get("sources") or []):
            if isinstance(s, dict) and s.get("source"):
                srcs.append({"source": str(s["source"]).strip(), "quantity": _coerce_int(s.get("quantity"))})
        qty = _coerce_int(item.get("quantity"))
        if qty == 0 and srcs:
            qty = sum(s["quantity"] for s in srcs)
        assets.append({"description": desc, "quantity": qty, "sources": srcs})

    return {"tender_details": tender, "asset_details": assets}


# ── Public API ──────────────────────────────────────────────────────────────────
def run_extraction(all_sources: List[Tuple[str, str]], filename: str) -> Dict[str, Any]:
    """Run the grounded Gemini extraction over all (label, text) sources.
    Returns {"tender_details": {...}, "asset_details": [...]}. Synchronous (blocking)."""
    import config as cfg

    sources_block = _build_sources_block(all_sources)
    prompt = _build_prompt(sources_block, filename)

    client = genai.Client(
        api_key=cfg.GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=180_000),  # 3 min ceiling for large contexts
    )
    gen_config = genai.types.GenerateContentConfig(
        temperature=0.0,
        max_output_tokens=8192,
        response_mime_type="application/json",
    )

    # Retry on transient model errors (free tier returns 503/429 under load).
    resp = None
    max_attempts = 4
    for attempt in range(max_attempts):
        try:
            resp = client.models.generate_content(
                model=cfg.CHAT_MODEL, contents=prompt, config=gen_config
            )
            break
        except Exception as e:
            transient = any(s in str(e) for s in
                            ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "500", "INTERNAL"))
            if transient and attempt < max_attempts - 1:
                wait = min(5 * (2 ** attempt), 60)
                logger.warning(f"Extraction model busy ({e}); retry {attempt+1}/{max_attempts} in {wait}s...")
                time.sleep(wait)
            else:
                raise

    data = _parse_response(resp.text)
    result = _normalize(data)
    result["filename"] = filename
    logger.info(
        f"Extraction done for '{filename}': "
        f"{sum(1 for v in result['tender_details'].values() if v != 'NA')}/{len(_TENDER_KEYS)} tender fields, "
        f"{len(result['asset_details'])} asset rows"
    )
    return result


# ── Persistence ─────────────────────────────────────────────────────────────────
def _extraction_path(doc_id: str) -> str:
    return os.path.join(DATA_DIR, doc_id, "extraction.json")


def save_extraction(doc_id: str, data: Dict[str, Any]) -> None:
    path = _extraction_path(doc_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_extraction(doc_id: str) -> Dict[str, Any] | None:
    path = _extraction_path(doc_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def tender_rows(data: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Return ordered (display_label, value) pairs for the tender details table."""
    td = (data or {}).get("tender_details", {})
    return [(label, td.get(key, "NA") or "NA") for key, label, _ in TENDER_FIELDS]
