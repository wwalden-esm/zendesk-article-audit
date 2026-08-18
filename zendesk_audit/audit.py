"""
Zendesk Help Center article audit.

Fetches all articles, categories, and sections, then classifies each article
by staleness, engagement, and status for review.
"""

import csv
import io
import os
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

ZENDESK_SUBDOMAIN = os.environ.get("ZENDESK_SUBDOMAIN")
ZENDESK_EMAIL = os.environ.get("ZENDESK_EMAIL")
ZENDESK_API_TOKEN = os.environ.get("ZENDESK_API_TOKEN")

BASE_URL = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com" if ZENDESK_SUBDOMAIN else ""

STALE_DAYS = 365


def zendesk_auth():
    return (f"{ZENDESK_EMAIL}/token", ZENDESK_API_TOKEN)


def zendesk_request(method, path, retries=5, **kwargs):
    url = f"{BASE_URL}{path}"
    for attempt in range(retries):
        resp = getattr(requests, method)(url, auth=zendesk_auth(), **kwargs)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 2 ** attempt))
            time.sleep(wait)
            continue
        if resp.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        if resp.status_code >= 400:
            resp.raise_for_status()
        return resp
    resp.raise_for_status()


def fetch_all_paginated(path, key, on_page=None):
    items = []
    url = f"{BASE_URL}{path}{'&' if '?' in path else '?'}per_page=100"
    page = 1
    while url:
        resp = zendesk_request.__wrapped__(url) if hasattr(zendesk_request, '__wrapped__') else None
        if resp is None:
            r = requests.get(url, auth=zendesk_auth())
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 5))
                time.sleep(wait)
                continue
            if r.status_code >= 500:
                time.sleep(2)
                continue
            r.raise_for_status()
            resp = r
        data = resp.json()
        batch = data.get(key, [])
        items.extend(batch)
        if on_page:
            on_page(page, len(items), batch)
        url = data.get("next_page")
        page += 1
    return items


def fetch_categories(on_page=None):
    return fetch_all_paginated(
        "/api/v2/help_center/categories.json", "categories", on_page
    )


def fetch_sections(on_page=None):
    return fetch_all_paginated(
        "/api/v2/help_center/sections.json", "sections", on_page
    )


def fetch_articles(on_page=None):
    return fetch_all_paginated(
        "/api/v2/help_center/articles.json", "articles", on_page
    )


def score_priority(days_since_update, days_since_created, flags, vote_sum, vote_count, comment_count):
    score = 0

    if days_since_update > 730:
        score += 5
    elif days_since_update > STALE_DAYS:
        score += 3

    if "draft" in flags:
        score += 1
    if vote_count > 0 and vote_sum < 0:
        score += 2
    if vote_count == 0 and comment_count == 0 and days_since_created > 90:
        score += 2

    if score >= 7:
        return score, "critical"
    if score >= 4:
        return score, "high"
    if score >= 2:
        return score, "medium"
    return score, "low"


def classify_article(article, now=None):
    if now is None:
        now = datetime.now(timezone.utc)

    updated = article.get("updated_at", "")
    created = article.get("created_at", "")

    updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00")) if updated else now
    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00")) if created else now

    days_since_update = (now - updated_dt).days
    days_since_created = (now - created_dt).days

    flags = []
    if days_since_update > STALE_DAYS:
        flags.append("stale")
    if article.get("draft"):
        flags.append("draft")

    vote_count = article.get("vote_count", 0)
    vote_sum = article.get("vote_sum", 0)
    comment_count = article.get("comment_count", 0)

    if vote_count > 0 and vote_sum < 0:
        flags.append("negative_votes")
    if vote_count == 0 and comment_count == 0 and days_since_created > 90:
        flags.append("no_engagement")

    priority_score, priority = score_priority(
        days_since_update, days_since_created, flags,
        vote_sum, vote_count, comment_count,
    )

    return {
        "days_since_update": days_since_update,
        "days_since_created": days_since_created,
        "flags": flags,
        "priority_score": priority_score,
        "priority": priority,
    }


def build_audit_results(categories, sections, articles):
    now = datetime.now(timezone.utc)

    cat_map = {c["id"]: c["name"] for c in categories}
    sec_map = {s["id"]: {"name": s["name"], "category_id": s.get("category_id")} for s in sections}

    results = []
    for article in articles:
        section_id = article.get("section_id")
        sec_info = sec_map.get(section_id, {})
        section_name = sec_info.get("name", "Unknown")
        category_id = sec_info.get("category_id")
        category_name = cat_map.get(category_id, "Unknown")

        classification = classify_article(article, now)

        results.append({
            "id": article["id"],
            "title": article.get("title", "Untitled"),
            "html_url": article.get("html_url", ""),
            "section_id": section_id,
            "section_name": section_name,
            "category_id": category_id,
            "category_name": category_name,
            "created_at": article.get("created_at", ""),
            "updated_at": article.get("updated_at", ""),
            "days_since_update": classification["days_since_update"],
            "days_since_created": classification["days_since_created"],
            "draft": article.get("draft", False),
            "outdated": article.get("outdated", False),
            "promoted": article.get("promoted", False),
            "vote_sum": article.get("vote_sum", 0),
            "vote_count": article.get("vote_count", 0),
            "comment_count": article.get("comment_count", 0),
            "label_names": article.get("label_names", []),
            "flags": classification["flags"],
            "priority_score": classification["priority_score"],
            "priority": classification["priority"],
        })

    return results


def compute_summary(results):
    total = len(results)
    stale = sum(1 for r in results if "stale" in r["flags"])
    drafts = sum(1 for r in results if "draft" in r["flags"])
    outdated = sum(1 for r in results if "outdated" in r["flags"])
    outdated_2yr = sum(1 for r in results if "outdated_2yr" in r["flags"])
    outdated_3yr = sum(1 for r in results if "outdated_3yr" in r["flags"])
    negative = sum(1 for r in results if "negative_votes" in r["flags"])
    no_engagement = sum(1 for r in results if "no_engagement" in r["flags"])

    categories = set(r["category_name"] for r in results)
    sections = set(r["section_name"] for r in results)

    age_buckets = {"< 6 months": 0, "6-12 months": 0, "1-2 years": 0, "2+ years": 0}
    for r in results:
        d = r["days_since_update"]
        if d < 180:
            age_buckets["< 6 months"] += 1
        elif d < 365:
            age_buckets["6-12 months"] += 1
        elif d < 730:
            age_buckets["1-2 years"] += 1
        else:
            age_buckets["2+ years"] += 1

    priority_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for r in results:
        priority_counts[r["priority"]] += 1

    return {
        "total": total,
        "stale": stale,
        "drafts": drafts,
        "outdated": outdated,
        "outdated_2yr": outdated_2yr,
        "outdated_3yr": outdated_3yr,
        "negative_votes": negative,
        "no_engagement": no_engagement,
        "categories": len(categories),
        "sections": len(sections),
        "age_buckets": age_buckets,
        "priority_counts": priority_counts,
    }


STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "has", "have", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "this", "that", "these", "those",
    "it", "its", "not", "no", "so", "if", "as", "we", "our", "you",
    "your", "they", "their", "all", "each", "any", "more", "new", "now",
    "also", "when", "how", "what", "which", "who", "where", "than",
    "up", "out", "about", "into", "over", "after", "before", "between",
    "under", "above", "below", "been", "being", "other", "some", "such",
    "only", "then", "just", "both", "well", "here", "there", "very",
    "via", "per", "within", "without", "during", "through", "same",
    "able", "esm", "release", "notes", "update", "updates", "updated",
    "version", "see", "page", "click", "select", "use", "using", "used",
    "added", "removed", "changed", "fixed", "issue", "issues", "bug",
    "feature", "features", "following", "please", "note", "users",
    "will", "based", "set", "add", "allow", "allows", "enabled",
    "previously", "now", "available", "support", "includes", "included",
    "option", "options", "display", "displayed", "shows", "shown",
    "when", "user", "system", "data", "information", "field", "fields",
    "2026", "2025", "2024", "2023", "2022", "2021", "2020",
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    "test", "production", "overview", "guide", "guides", "reference",
    "help", "tutorial", "video", "faq", "faqs", "quick",
}

PRODUCT_TERMS = {
    "purchase", "source", "supplier", "contract", "storeroom",
    "network", "platform", "esm", "agiloft",
}


def extract_topics_from_html(html_body):
    if not html_body:
        return set(), []

    soup = BeautifulSoup(html_body, "html.parser")

    headings = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "strong", "b"]):
        text = tag.get_text(strip=True)
        if len(text) > 3 and len(text) < 200:
            headings.append(text.lower())

    full_text = soup.get_text(" ", strip=True).lower()
    full_text = re.sub(r"[^a-z0-9\s\-/]", " ", full_text)
    words = full_text.split()

    keywords = set()
    for w in words:
        w = w.strip("-/")
        if len(w) >= 4 and w not in STOP_WORDS:
            keywords.add(w)

    bigrams = set()
    for i in range(len(words) - 1):
        a, b = words[i].strip("-/"), words[i + 1].strip("-/")
        if a not in STOP_WORDS and b not in STOP_WORDS and len(a) >= 3 and len(b) >= 3:
            bigrams.add(f"{a} {b}")

    return keywords | bigrams, headings


def identify_release_notes(articles, cat_map, sec_map, year=2026, years=None):
    target_years = set(years) if years else {year}
    release_articles = []
    for article in articles:
        section_id = article.get("section_id")
        sec_info = sec_map.get(section_id, {})
        category_id = sec_info.get("category_id")
        category_name = cat_map.get(category_id, "")

        if category_name != "Release Notes":
            continue

        created = article.get("created_at", "")
        updated = article.get("updated_at", "")
        date_str = updated or created
        if not date_str:
            continue

        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            if dt.year in target_years:
                article["_release_year"] = dt.year
                release_articles.append(article)
        except (ValueError, TypeError):
            continue

    return release_articles


def _extract_words(text):
    clean = re.sub(r"[^a-z0-9\s\-/]", " ", text.lower())
    return set(
        w.strip("-/")
        for w in clean.split()
        if len(w.strip("-/")) >= 4 and w.strip("-/") not in STOP_WORDS
    )


def cross_reference_releases(release_articles, stale_results):
    release_index = []
    for article in release_articles:
        keywords, headings = extract_topics_from_html(article.get("body", ""))
        title_words = _extract_words(article.get("title", ""))
        release_index.append({
            "id": article["id"],
            "title": article.get("title", ""),
            "html_url": article.get("html_url", ""),
            "year": article.get("_release_year", 0),
            "keywords": keywords | title_words,
            "headings": headings,
        })

    release_ids = {r["id"] for r in release_articles}

    for result in stale_results:
        if result["id"] in release_ids:
            result["release_matches"] = []
            continue
        if result.get("category_name") == "Release Notes":
            result["release_matches"] = []
            continue

        article_words = _extract_words(result["title"])
        section_words = _extract_words(result.get("section_name", ""))
        label_words = set()
        for label in result.get("label_names", []):
            label_words |= _extract_words(label)

        search_words = article_words | section_words | label_words

        matching_releases = []
        for rel in release_index:
            shared = search_words & rel["keywords"]

            specific_matches = shared - PRODUCT_TERMS
            product_matches = shared & PRODUCT_TERMS

            heading_matches = [
                h for h in rel["headings"]
                if any(w in h for w in article_words if len(w) >= 4 and w not in PRODUCT_TERMS)
            ]

            if not specific_matches and not heading_matches:
                continue

            score = len(specific_matches) * 2 + len(product_matches) + len(heading_matches) * 3

            if score >= 3:
                display_terms = sorted(specific_matches)[:6] + sorted(product_matches)[:2]
                matching_releases.append({
                    "release_title": rel["title"],
                    "release_url": rel["html_url"],
                    "release_year": rel["year"],
                    "matched_terms": display_terms[:8],
                    "matched_headings": heading_matches[:3],
                    "score": score,
                })

        matching_releases.sort(key=lambda x: x["score"], reverse=True)

        result["release_matches"] = matching_releases[:5]
        if matching_releases:
            result["flags"].append("outdated")
            if result["days_since_update"] >= 1095:
                result["flags"].append("outdated_3yr")
            if result["days_since_update"] >= 730:
                result["flags"].append("outdated_2yr")
            result["priority_score"] += 3
            if result["priority_score"] >= 7:
                result["priority"] = "critical"
            elif result["priority_score"] >= 4:
                result["priority"] = "high"

    impacted = sum(1 for r in stale_results if r.get("release_matches"))
    return impacted


def _jaccard(set_a, set_b):
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


def find_duplicates(articles, on_progress=None):
    article_data = []
    total = len(articles)
    for i, article in enumerate(articles):
        title = article.get("title", "")
        title_words = _extract_words(title)
        body = article.get("body", "")
        content_keywords, _ = extract_topics_from_html(body) if body else (set(), [])
        article_data.append({
            "id": article["id"],
            "title": title,
            "html_url": article.get("html_url", ""),
            "section_id": article.get("section_id"),
            "title_words": title_words,
            "content_keywords": content_keywords,
        })
        if on_progress and i % 50 == 0:
            on_progress(i, total)

    pairs = []
    n = len(article_data)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = article_data[i], article_data[j]

            if len(a["title_words"]) < 2 and len(b["title_words"]) < 2:
                continue

            title_sim = _jaccard(a["title_words"], b["title_words"])
            content_sim = _jaccard(a["content_keywords"], b["content_keywords"])

            if title_sim >= 0.7 or (title_sim >= 0.4 and content_sim >= 0.3) or content_sim >= 0.5:
                confidence = "high" if title_sim >= 0.7 or content_sim >= 0.5 else "medium"
                pairs.append({
                    "article_a": {"id": a["id"], "title": a["title"], "html_url": a["html_url"]},
                    "article_b": {"id": b["id"], "title": b["title"], "html_url": b["html_url"]},
                    "title_similarity": round(title_sim, 2),
                    "content_similarity": round(content_sim, 2),
                    "confidence": confidence,
                })

    parent = list(range(n))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    id_to_idx = {ad["id"]: idx for idx, ad in enumerate(article_data)}
    for pair in pairs:
        idx_a = id_to_idx[pair["article_a"]["id"]]
        idx_b = id_to_idx[pair["article_b"]["id"]]
        union(idx_a, idx_b)

    groups_map = {}
    for idx in range(n):
        root = find(idx)
        if root not in groups_map:
            groups_map[root] = []
        groups_map[root].append(idx)

    groups = []
    for indices in groups_map.values():
        if len(indices) < 2:
            continue
        members = []
        for idx in indices:
            ad = article_data[idx]
            members.append({
                "id": ad["id"],
                "title": ad["title"],
                "html_url": ad["html_url"],
            })
        group_pairs = [
            p for p in pairs
            if p["article_a"]["id"] in {m["id"] for m in members}
            and p["article_b"]["id"] in {m["id"] for m in members}
        ]
        best_confidence = "high" if any(p["confidence"] == "high" for p in group_pairs) else "medium"
        max_title_sim = max((p["title_similarity"] for p in group_pairs), default=0)
        max_content_sim = max((p["content_similarity"] for p in group_pairs), default=0)
        groups.append({
            "members": members,
            "confidence": best_confidence,
            "max_title_similarity": max_title_sim,
            "max_content_similarity": max_content_sim,
            "pair_count": len(group_pairs),
        })

    groups.sort(key=lambda g: (0 if g["confidence"] == "high" else 1, -g["max_title_similarity"]))
    return groups


def export_csv(results):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Article ID", "Title", "URL", "Category", "Section",
        "Created", "Last Updated", "Days Since Update",
        "Priority", "Priority Score",
        "Draft", "Outdated", "Promoted",
        "Votes (Sum)", "Votes (Count)", "Comments",
        "Labels", "Flags", "Related Release Notes",
    ])
    for r in results:
        release_titles = [
            m["release_title"] for m in r.get("release_matches", [])
        ]
        writer.writerow([
            r["id"],
            r["title"],
            r["html_url"],
            r["category_name"],
            r["section_name"],
            r["created_at"][:10] if r["created_at"] else "",
            r["updated_at"][:10] if r["updated_at"] else "",
            r["days_since_update"],
            r["priority"],
            r["priority_score"],
            r["draft"],
            r["outdated"],
            r["promoted"],
            r["vote_sum"],
            r["vote_count"],
            r["comment_count"],
            "|".join(r["label_names"]),
            "|".join(r["flags"]),
            "|".join(release_titles),
        ])
    return output.getvalue()
