# Zendesk Article Audit

Web tool for auditing Zendesk Help Center articles. Scans all articles and surfaces staleness, engagement gaps, duplicate content, and release note impact to prioritize updates.

## Features

- **Staleness tiers** — flags articles not updated in 1, 2, or 3+ years
- **Release note cross-referencing** — compares stale articles against 3 years of release notes (2024–2026) to identify what needs updating and why
- **Duplicate detection** — finds duplicate/near-duplicate articles using Jaccard similarity on titles and content keywords, with confidence tiers
- **Word document export** — downloads any article as a `.docx` with embedded images, preserving headings, lists, tables, and callouts
- **Priority scoring** — ranks articles by staleness, engagement, draft status, and negative votes
- **CSV export** — full audit results as a downloadable CSV
- **Real-time progress** — SSE-powered scan progress with phase indicators

## Setup

### Requirements

- Python 3.10+
- Zendesk Help Center with API access

### Install dependencies

```bash
pip install flask requests beautifulsoup4 python-dotenv python-docx
```

### Configure environment

Create a `.env` file in the `zendesk_audit/` directory:

```
ZENDESK_SUBDOMAIN=yourcompany
ZENDESK_EMAIL=you@yourcompany.com
ZENDESK_API_TOKEN=your_api_token
```

The email/token pair uses Zendesk's API token authentication (`email/token`).

### Run

```bash
cd zendesk_audit
python app.py
```

Opens at [http://localhost:5003](http://localhost:5003).

## Usage

1. Click **Scan all articles** to fetch and analyze all Help Center content
2. Review the summary cards for staleness breakdown and priority distribution
3. Use filter pills to focus on specific tiers (Stale, Outdated 2yr+, Drafts, etc.)
4. Check **Potential Duplicates** for content consolidation opportunities
5. Check **Release Impact** to see which stale articles are affected by recent releases
6. Click **.docx** on any article to download it as a Word document for editing
7. Click **Re-upload** to open the QRG migration tool for pushing edits back to Zendesk

## File structure

| File | Purpose |
|------|---------|
| `app.py` | Flask server, API endpoints, SSE scan orchestration |
| `audit.py` | Core audit engine — fetching, classification, release cross-referencing, duplicate detection |
| `docx_export.py` | Article-to-Word conversion with image embedding |
| `templates/index.html` | Single-page web UI |
| `launch.bat` | Stream Deck / quick-launch shortcut |
