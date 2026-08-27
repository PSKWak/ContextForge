"""The "LLM Eval" branch: score an LLM-drafted overrides file against a
human-approved one. Independent of, and never blocking, run_ingest.py's
Validation -> Staging -> Production path.

Example:
    python eval_overrides.py overrides.hockney.draft2.json overrides.hockney.json --source-id nyt_2026_06_12_hockney_style_never_wavered --log-to-db
"""
import argparse
import json

from pipeline.eval_llm import evaluate_draft


def main():
    parser = argparse.ArgumentParser(description="Score an LLM draft overrides file against a human-approved one.")
    parser.add_argument("draft_path")
    parser.add_argument("human_path")
    parser.add_argument("--source-id", help="required with --log-to-db")
    parser.add_argument("--log-to-db", action="store_true", help="append this run's metrics to extraction_eval_log")
    args = parser.parse_args()

    with open(args.draft_path, "r", encoding="utf-8") as f:
        draft = json.load(f)
    with open(args.human_path, "r", encoding="utf-8") as f:
        human = json.load(f)

    result = evaluate_draft(draft, human)

    print(f"content_type: draft={result['content_type_draft']!r}  human={result['content_type_human']!r}  match={result['content_type_match']}")
    for field in ("tags", "entities_name_and_role", "entities_name_only"):
        m = result[field]
        print(
            f"{field:26s}  P={m['precision']:.3f}  R={m['recall']:.3f}  F1={m['f1']:.3f}"
            f"  (tp={m['true_positive']} fp={m['false_positive']} fn={m['false_negative']})"
        )

    if args.log_to_db:
        if not args.source_id:
            parser.error("--log-to-db requires --source-id")
        from dotenv import load_dotenv
        load_dotenv()
        from pipeline import db

        conn = db.get_conn()
        db.log_extraction_eval(conn, args.source_id, args.draft_path, result)
        conn.close()
        print("Logged to extraction_eval_log.")


if __name__ == "__main__":
    main()
