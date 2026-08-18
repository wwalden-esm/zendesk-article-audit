"""
Flask web UI for the Zendesk article audit tool.
Run with: python app.py
"""

import json
import os
import queue
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request

import audit
import docx_export

load_dotenv()

app = Flask(__name__)

SCRIPT_DIR = Path(__file__).parent
RESULTS_PATH = SCRIPT_DIR / "audit-results.json"

progress_queues: dict[str, queue.Queue] = {}
cached_results: dict = {}


def _send_event(q: queue.Queue, event: str, data: dict):
    q.put(f"event: {event}\ndata: {json.dumps(data)}\n\n")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scan", methods=["POST"])
def api_scan():
    run_id = f"scan_{int(time.time())}"
    q = queue.Queue()
    progress_queues[run_id] = q

    def _run():
        global cached_results
        try:
            _send_event(q, "progress", {
                "phase": "categories", "message": "Fetching categories...",
                "current": 0, "total": 3,
            })

            def on_cat_page(page, total, batch):
                _send_event(q, "progress", {
                    "phase": "categories",
                    "message": f"Categories: page {page}, {total} found",
                    "current": 0, "total": 3,
                })

            categories = audit.fetch_categories(on_page=on_cat_page)

            _send_event(q, "progress", {
                "phase": "sections", "message": f"Fetching sections... ({len(categories)} categories loaded)",
                "current": 1, "total": 3,
            })

            def on_sec_page(page, total, batch):
                _send_event(q, "progress", {
                    "phase": "sections",
                    "message": f"Sections: page {page}, {total} found",
                    "current": 1, "total": 3,
                })

            sections = audit.fetch_sections(on_page=on_sec_page)

            _send_event(q, "progress", {
                "phase": "articles",
                "message": f"Fetching articles... ({len(sections)} sections loaded)",
                "current": 2, "total": 3,
            })

            def on_art_page(page, total, batch):
                _send_event(q, "progress", {
                    "phase": "articles",
                    "message": f"Articles: page {page}, {total} fetched so far",
                    "current": 2, "total": 3,
                })

            articles = audit.fetch_articles(on_page=on_art_page)

            _send_event(q, "progress", {
                "phase": "analyzing",
                "message": f"Analyzing {len(articles)} articles...",
                "current": 3, "total": 5,
            })

            results = audit.build_audit_results(categories, sections, articles)

            _send_event(q, "progress", {
                "phase": "releases",
                "message": "Identifying release notes (2024-2026)...",
                "current": 4, "total": 5,
            })

            cat_map = {c["id"]: c["name"] for c in categories}
            sec_map = {s["id"]: {"name": s["name"], "category_id": s.get("category_id")} for s in sections}
            release_articles = audit.identify_release_notes(
                articles, cat_map, sec_map, years=[2024, 2025, 2026]
            )

            release_by_year = {}
            for ra in release_articles:
                y = ra.get("_release_year", 0)
                release_by_year[y] = release_by_year.get(y, 0) + 1

            _send_event(q, "progress", {
                "phase": "cross_reference",
                "message": f"Cross-referencing {len(release_articles)} release notes (2024-2026) against stale articles...",
                "current": 4, "total": 5,
            })

            stale_results = [r for r in results if "stale" in r["flags"]]
            impacted = audit.cross_reference_releases(release_articles, stale_results)

            _send_event(q, "progress", {
                "phase": "cross_reference",
                "message": f"Found {impacted} stale articles impacted by releases",
                "current": 5, "total": 6,
            })

            _send_event(q, "progress", {
                "phase": "duplicates",
                "message": f"Scanning {len(articles)} articles for duplicates...",
                "current": 5, "total": 6,
            })

            def on_dup_progress(i, total):
                _send_event(q, "progress", {
                    "phase": "duplicates",
                    "message": f"Extracting keywords: {i}/{total} articles",
                    "current": 5, "total": 6,
                })

            duplicate_groups = audit.find_duplicates(articles, on_progress=on_dup_progress)

            _send_event(q, "progress", {
                "phase": "duplicates",
                "message": f"Found {len(duplicate_groups)} duplicate groups",
                "current": 6, "total": 6,
            })

            summary = audit.compute_summary(results)
            summary["release_notes_count"] = len(release_articles)
            summary["release_by_year"] = release_by_year
            summary["duplicate_groups"] = len(duplicate_groups)

            cached_results = {
                "results": results,
                "summary": summary,
                "duplicates": duplicate_groups,
            }

            with open(RESULTS_PATH, "w", encoding="utf-8") as f:
                json.dump(cached_results, f, indent=2, default=str)

            _send_event(q, "complete", {
                "message": f"Scan complete: {len(articles)} articles analyzed, {impacted} impacted by releases, {len(duplicate_groups)} duplicate groups",
                "summary": summary,
            })

        except Exception as e:
            _send_event(q, "error", {"message": str(e)})
        finally:
            q.put(None)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return jsonify({"run_id": run_id})


@app.route("/api/scan/stream/<run_id>")
def api_scan_stream(run_id):
    q = progress_queues.get(run_id)
    if not q:
        return jsonify({"error": "Unknown run"}), 404

    def generate():
        while True:
            msg = q.get()
            if msg is None:
                break
            yield msg
        progress_queues.pop(run_id, None)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/results")
def api_results():
    global cached_results
    if cached_results:
        return jsonify(cached_results)
    if RESULTS_PATH.exists():
        with open(RESULTS_PATH, encoding="utf-8") as f:
            cached_results = json.load(f)
        return jsonify(cached_results)
    return jsonify({"results": [], "summary": None})


@app.route("/api/export")
def api_export():
    global cached_results
    if not cached_results and RESULTS_PATH.exists():
        with open(RESULTS_PATH, encoding="utf-8") as f:
            cached_results = json.load(f)
    if not cached_results or not cached_results.get("results"):
        return jsonify({"error": "No scan results. Run a scan first."}), 400

    csv_data = audit.export_csv(cached_results["results"])
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=zendesk-audit.csv"},
    )


@app.route("/api/article/<int:article_id>/docx")
def api_article_docx(article_id):
    try:
        buf, filename = docx_export.export_article_docx(article_id)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if buf is None:
        return jsonify({"error": "Article not found"}), 404
    return Response(
        buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


if __name__ == "__main__":
    print("\n  Zendesk Article Audit")
    print("  http://localhost:5003\n")
    port = int(os.environ.get("PORT", 5003))
    app.run(host="127.0.0.1", port=port, debug=True)
