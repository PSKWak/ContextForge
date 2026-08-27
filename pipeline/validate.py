"""QA gates from the manual review pass, made mechanical.

Returns (errors, warnings). Non-empty `errors` blocks promotion to the
production `articles` table -- the record stays in staging, visible for
audit, but not queryable as a "ready" research record.
"""
from typing import Iterable


REQUIRED_FIELDS = ["source_id", "url", "publication", "published_date", "web_headline"]


def validate(parsed: dict, existing_source_ids: Iterable[str]) -> tuple:
    errors = []
    warnings = []

    for field in REQUIRED_FIELDS:
        if not parsed.get(field):
            errors.append(f"missing required field: {field}")

    if parsed.get("source_id") in set(existing_source_ids):
        warnings.append("source_id already exists in production -- this run will upsert, not duplicate")

    word_count = parsed.get("word_count_est") or 0
    if word_count < 20:
        errors.append(f"word_count_est={word_count} is implausibly low -- check PDF extraction quality")

    if not parsed.get("content_type"):
        errors.append("content_type not set -- this is a judgment field, supply it via --overrides")

    if not parsed.get("tags"):
        warnings.append("no tags assigned -- record will promote untagged unless overrides are supplied")

    if not parsed.get("author"):
        warnings.append("byline not detected -- verify author manually")

    if parsed.get("license_ok") and "full_text" not in (parsed.get("_verified_flags") or []):
        errors.append(
            "license_ok=True but no manual verification flag present -- "
            "do not set license_ok without an explicit legal/licensing sign-off"
        )

    for ent in parsed.get("entities", []):
        if not ent.get("name") or not ent.get("type") or not ent.get("role"):
            errors.append(f"malformed entity record: {ent}")

    return errors, warnings
