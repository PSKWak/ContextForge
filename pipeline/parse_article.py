"""Raw text -> structured article dict.

Split deliberately into two tiers:

1. MECHANICAL fields (headline, byline, date, quotes, word count) are
   extracted with regex/heuristics because they are objectively checkable
   against the source text.
2. JUDGMENT fields (content_type, tags, entity roles, license_ok) are NOT
   guessed here. They come from an `overrides` dict the caller supplies
   (see overrides.example.json). If a judgment field is missing, it stays
   None/empty and the validation gate in validate.py will block promotion
   until a human supplies it. This mirrors the "flag for human review"
   principle from the manual tagging pass -- the pipeline automates
   extraction, not editorial judgment.
"""
import re
from datetime import datetime
from typing import Optional

DATE_RE = re.compile(r"\b([A-Z][a-z]+ \d{1,2}, \d{4})\b")
# [ \t] (not \s) keeps the byline from spanning onto the reporter-bio line
# that typically follows it on its own line in these PDFs.
BYLINE_RE = re.compile(r"\bBy[ \t]+([A-Z][A-Za-z.'\-]+(?:[ \t]+[A-Z][A-Za-z.'\-]+)*)")
AUDIO_RE = re.compile(r"Listen\s*[·•.]?\s*(\d+):(\d+)\s*min", re.IGNORECASE)
QUOTE_RE = re.compile(r"[“\"]([^”\"]{8,400})[”\"]")
MIN_QUOTE_WORDS = 5  # excludes short quoted titles/phrases like "style icon" or a book title


def _first_nonblank_lines(text: str, n: int) -> list:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[:n]


def _parse_date(text: str) -> Optional[str]:
    m = DATE_RE.search(text)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def _parse_audio_seconds(text: str) -> Optional[int]:
    m = AUDIO_RE.search(text)
    if not m:
        return None
    minutes, seconds = int(m.group(1)), int(m.group(2))
    return minutes * 60 + seconds


def _extract_quotes(text: str) -> list:
    seen = []
    for q in QUOTE_RE.findall(text):
        q = q.strip()
        # Drop short quoted titles/single terms ("style icon", a book title) --
        # real spoken quotes in this article run noticeably longer.
        if len(q.split()) < MIN_QUOTE_WORDS:
            continue
        # De-duplicate: photo captions in these PDFs often repeat a quote
        # verbatim from the body text right next to it.
        if q not in seen:
            seen.append(q)
    return seen


def parse_article(raw_text: str, source_id: str, url: str, overrides: Optional[dict] = None) -> dict:
    overrides = overrides or {}
    lines = _first_nonblank_lines(raw_text, 5)

    web_headline = lines[0] if lines else None
    dek = None
    for candidate in lines[1:3]:
        if candidate.startswith(("Listen", "By ")) or DATE_RE.search(candidate):
            continue
        dek = candidate
        break

    byline_match = BYLINE_RE.search(raw_text)
    author = byline_match.group(1).strip() if byline_match else None

    parsed = {
        "source_id": source_id,
        "url": url,
        "publication": overrides.get("publication", "The New York Times"),
        "web_section": overrides.get("web_section"),
        "print_section": overrides.get("print_section"),
        "print_page": overrides.get("print_page"),
        "web_headline": web_headline,
        "print_headline": overrides.get("print_headline"),
        "dek": dek,
        "published_date": overrides.get("published_date") or _parse_date(raw_text),
        "content_type": overrides.get("content_type"),        # judgment field
        "word_count_est": len(re.findall(r"\w+", raw_text)),
        "audio_duration_sec": _parse_audio_seconds(raw_text),
        "paywall_status": overrides.get("paywall_status", "paywalled_full_text_via_user_pdf"),
        "ingestion_method": overrides.get("ingestion_method", "user_supplied_pdf"),
        "abstract": overrides.get("abstract"),                # judgment field
        "license_ok": overrides.get("license_ok", False),     # judgment field
        "author": author,
        "quotes": _extract_quotes(raw_text),
        "tags": overrides.get("tags", []),                    # judgment field
        "entities": overrides.get("entities", []),            # judgment field
        "raw_text": raw_text,
    }
    return parsed
