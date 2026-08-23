"""Draft the judgment fields (content_type, tags, entities, abstract) using
a Hugging Face-hosted instruct LLM, for a human to review before they ever
touch overrides.json.

This module never writes to the database and is never called from
run_ingest.py. It only produces a *draft* file. The pipeline's core design
principle -- that content_type/tags/entities/license_ok require an explicit
human-supplied overrides.json, and validate.py blocks promotion without one
-- is unchanged. An LLM suggestion is just a faster way to get a first
draft of that file; it is not treated as verified.
"""
import json
import os
import re

from huggingface_hub import InferenceClient

DEFAULT_MODEL = "meta-llama/Llama-3.1-8B-Instruct"
VALID_ENTITY_TYPES = {"person", "organization", "work"}
VALID_ROLES = {"subject", "author", "quoted_source", "mentioned"}

SYSTEM_PROMPT = (
    "You are a research-data annotator for a news article database. "
    "Given an article's full text and a fixed controlled tag vocabulary, "
    "extract structured metadata. Output ONLY a single valid JSON object -- "
    "no markdown code fences, no commentary before or after it."
)

USER_PROMPT_TEMPLATE = """Controlled tag vocabulary (choose only from this list, do not invent new tags):
{tag_list}

Valid entity types: person, organization, work
Valid entity roles: subject (who the article is centrally about), author, quoted_source (directly quoted), mentioned (named but not quoted)

Article text:
---
{article_text}
---

Return a JSON object with exactly these keys:
{{
  "content_type": "<short snake_case string describing what kind of piece this is>",
  "abstract": "<one sentence, <=60 words, neutral summary>",
  "tags": ["<tag from the controlled vocabulary above>", ...],
  "entities": [{{"name": "<full name>", "type": "person|organization|work", "role": "subject|author|quoted_source|mentioned"}}, ...]
}}"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    # Models sometimes wrap output in ```json fences despite instructions -- strip if present.
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    return json.loads(text)


def _sanitize(draft: dict, controlled_tags: set) -> dict:
    """Drop anything the model returned that violates the controlled
    vocabulary or role/type constraints, rather than silently trusting it.
    Dropped items are reported so a human can see what was filtered.
    """
    dropped = {"tags": [], "entities": []}

    tags = draft.get("tags", [])
    clean_tags = [t for t in tags if t in controlled_tags]
    dropped["tags"] = [t for t in tags if t not in controlled_tags]

    entities = draft.get("entities", [])
    clean_entities = []
    for ent in entities:
        name, etype, role = ent.get("name"), ent.get("type"), ent.get("role")
        if not name or etype not in VALID_ENTITY_TYPES or role not in VALID_ROLES:
            dropped["entities"].append(ent)
            continue
        clean_entities.append({"name": name, "type": etype, "role": role})

    return {
        "content_type": draft.get("content_type"),
        "abstract": draft.get("abstract"),
        "tags": clean_tags,
        "entities": clean_entities,
        "license_ok": False,  # never set by the model -- always requires explicit human sign-off
        "_dropped_by_sanitizer": dropped,
        "_source": "llm_draft_unreviewed",
    }


def suggest_overrides(article_text: str, controlled_tags: list, model: str = DEFAULT_MODEL) -> dict:
    token = os.environ.get("HUGGINGFACE_API_TOKEN")
    if not token:
        raise RuntimeError(
            "HUGGINGFACE_API_TOKEN is not set. Add it to .env (see .env.example)."
        )

    client = InferenceClient(model=model, token=token)
    prompt = USER_PROMPT_TEMPLATE.format(
        tag_list=", ".join(controlled_tags),
        article_text=article_text,
    )
    response = client.chat_completion(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        max_tokens=800,
        temperature=0.1,
    )
    raw_content = response.choices[0].message.content
    draft = _extract_json(raw_content)
    return _sanitize(draft, set(controlled_tags))
