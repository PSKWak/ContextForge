"""Supabase Postgres access. Direct psycopg2 connection (not the REST
client) so the staging -> production promotion can run as one real
transaction across articles / entities / tags / quotes / full text.
All statements are parameterized -- no string-built SQL, since this
pipeline may one day run against externally-sourced articles.
"""
import os
import json
import psycopg2
import psycopg2.extras


def get_conn():
    dsn = os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise RuntimeError(
            "SUPABASE_DB_URL is not set. Copy .env.example to .env, fill in "
            "your Supabase project's direct Postgres connection string, and "
            "make sure it's loaded (run_ingest.py calls load_dotenv() for you)."
        )
    return psycopg2.connect(dsn)


def log_extraction_eval(conn, source_id: str, draft_file: str, result: dict) -> None:
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO extraction_eval_log (
                source_id, draft_file, content_type_match,
                tags_precision, tags_recall, tags_f1,
                entities_precision, entities_recall, entities_f1,
                entities_name_only_f1, raw_result
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                source_id,
                draft_file,
                result["content_type_match"],
                result["tags"]["precision"],
                result["tags"]["recall"],
                result["tags"]["f1"],
                result["entities_name_and_role"]["precision"],
                result["entities_name_and_role"]["recall"],
                result["entities_name_and_role"]["f1"],
                result["entities_name_only"]["f1"],
                psycopg2.extras.Json(result),
            ),
        )


def get_existing_source_ids(conn) -> set:
    with conn.cursor() as cur:
        cur.execute("SELECT source_id FROM articles")
        return {row[0] for row in cur.fetchall()}


def upsert_staging(conn, parsed: dict) -> None:
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO staging_articles (
                source_id, publication, web_section, print_section, print_page,
                web_headline, print_headline, dek, url, published_date,
                content_type, word_count_est, audio_duration_sec,
                paywall_status, ingestion_method, abstract, license_ok,
                raw_payload
            ) VALUES (
                %(source_id)s, %(publication)s, %(web_section)s, %(print_section)s, %(print_page)s,
                %(web_headline)s, %(print_headline)s, %(dek)s, %(url)s, %(published_date)s,
                %(content_type)s, %(word_count_est)s, %(audio_duration_sec)s,
                %(paywall_status)s, %(ingestion_method)s, %(abstract)s, %(license_ok)s,
                %(raw_payload)s
            )
            ON CONFLICT (source_id) DO UPDATE SET
                web_headline = EXCLUDED.web_headline,
                content_type = EXCLUDED.content_type,
                abstract = EXCLUDED.abstract,
                raw_payload = EXCLUDED.raw_payload,
                created_at = now()
            """,
            {
                **parsed,
                "raw_payload": psycopg2.extras.Json(
                    {k: v for k, v in parsed.items() if k != "raw_text"}
                ),
            },
        )


def _get_or_create_entity(cur, name: str, entity_type: str) -> int:
    cur.execute(
        "SELECT entity_id FROM entities WHERE entity_name = %s AND entity_type = %s",
        (name, entity_type),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO entities (entity_name, entity_type) VALUES (%s, %s) RETURNING entity_id",
        (name, entity_type),
    )
    return cur.fetchone()[0]


def _get_tag_id(cur, tag_slug: str):
    cur.execute("SELECT tag_id FROM tags WHERE tag_slug = %s", (tag_slug,))
    row = cur.fetchone()
    return row[0] if row else None


def promote_to_production(conn, parsed: dict) -> int:
    """Atomic: article row + entities + tags + quotes + full-text row,
    or nothing at all. Raises and rolls back on any failure."""
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO articles (
                    source_id, publication, web_section, print_section, print_page,
                    web_headline, print_headline, dek, url, published_date,
                    content_type, word_count_est, audio_duration_sec,
                    paywall_status, ingestion_method, abstract, license_ok
                ) VALUES (
                    %(source_id)s, %(publication)s, %(web_section)s, %(print_section)s, %(print_page)s,
                    %(web_headline)s, %(print_headline)s, %(dek)s, %(url)s, %(published_date)s,
                    %(content_type)s, %(word_count_est)s, %(audio_duration_sec)s,
                    %(paywall_status)s, %(ingestion_method)s, %(abstract)s, %(license_ok)s
                )
                ON CONFLICT (source_id) DO UPDATE SET
                    web_headline = EXCLUDED.web_headline,
                    abstract = EXCLUDED.abstract,
                    updated_at = now()
                RETURNING article_id
                """,
                parsed,
            )
            article_id = cur.fetchone()[0]

            if parsed.get("author"):
                author_id = _get_or_create_entity(cur, parsed["author"], "person")
                cur.execute(
                    """INSERT INTO article_entities (article_id, entity_id, role)
                       VALUES (%s, %s, 'author') ON CONFLICT DO NOTHING""",
                    (article_id, author_id),
                )

            for ent in parsed.get("entities", []):
                entity_id = _get_or_create_entity(cur, ent["name"], ent["type"])
                cur.execute(
                    """INSERT INTO article_entities (article_id, entity_id, role)
                       VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                    (article_id, entity_id, ent["role"]),
                )

            for tag_slug in parsed.get("tags", []):
                tag_id = _get_tag_id(cur, tag_slug)
                if tag_id is None:
                    raise ValueError(
                        f"tag '{tag_slug}' is not in the controlled vocabulary (tags table) -- "
                        "add it to schema.sql's seed list first, don't free-text a new tag here"
                    )
                cur.execute(
                    """INSERT INTO article_tags (article_id, tag_id)
                       VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                    (article_id, tag_id),
                )

            for quote_text in parsed.get("quotes", []):
                cur.execute(
                    """INSERT INTO article_quotes (article_id, entity_id, quote_text)
                       VALUES (%s, NULL, %s)
                       ON CONFLICT (article_id, quote_text) DO NOTHING""",
                    (article_id, quote_text),
                )

            if parsed.get("raw_text"):
                cur.execute(
                    """
                    INSERT INTO articles_fulltext_restricted (article_id, full_text, source_document, license_ok)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (article_id) DO UPDATE SET
                        full_text = EXCLUDED.full_text,
                        license_ok = EXCLUDED.license_ok
                    """,
                    (article_id, parsed["raw_text"], parsed.get("source_document"), parsed.get("license_ok", False)),
                )

            return article_id
