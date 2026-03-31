# papers.ecosyste.ms — Paper↔Project Link Workflow

## Overview

The `mentions` table is a simple join between `papers` and `projects` (just `paper_id` + `project_id`). The `counter_culture` gem keeps denormalized `mentions_count` up to date on both sides. All linking originates from **Chan Zuckerberg Initiative (CZI)** pre-computed data — not from text-mining papers.

---

## Database Schema

```
papers
  id, doi (indexed), openalex_id, title, publication_date,
  openalex_data (json), mentions_count, last_synced_at,
  urls (text[])

projects
  id, czi_id, ecosystem, name (indexed: ecosystem+name),
  package (json), mentions_count, last_synced_at,
  commits_data (json), readme_content, educational_commit_emails (json),
  science_score (indexed)

mentions
  id, paper_id (indexed), project_id (indexed)

exports
  id, date, bucket_name, mentions_count
```

---

## Step 1 — Seed from CZI Data (one-time import)

### Input files

| File | Format | Contents |
|---|---|---|
| `data/comm_disambiguated_dois_count.json` | JSON | `{ "SM412963": ["10.1234/a", "10.5678/b"] }` — CZI package ID → array of DOIs |
| `data/cran_df.csv` | CSV | CZI package ID → CRAN package name |
| `data/bioconductor_df.csv` | CSV | CZI package ID → Bioconductor package name |
| `data/pypi_df.csv` | CSV | CZI package ID → PyPI package name |

### Rake tasks

```bash
bundle exec rake import:cran
bundle exec rake import:bioconductor
bundle exec rake import:pypi
```

### Logic (same pattern for all three ecosystems)

```
for each row in {ecosystem}_df.csv:
  project = Project.find_or_create_by(ecosystem:, name:, czi_id:)

  for each doi in comm_disambiguated_dois_count[czi_id]:
    paper   = Paper.find_or_create_by(doi:)
    Mention.create(paper:, project:)      ← THE LINK
```

At this point papers are stubs (DOI only) and projects are stubs (ecosystem + name only). Enrichment happens in subsequent steps.

---

## Step 2 — Enrich Papers (OpenAlex)

### Source

```
GET https://api.openalex.org/works/{doi_url}?mailto=andrew@ecosyste.ms
```

### Data stored

| Field | Source |
|---|---|
| `title` | `data["title"]` |
| `publication_date` | `data["publication_date"]` |
| `openalex_id` | `data["id"]` |
| `openalex_data` | Full JSON response |
| `urls` | Extracted from PDF (see below) |

### ArXiv PDF URL extraction

For DOIs matching `10.48550/arxiv.*`:
- Fetches PDF from `openalex_data["primary_location"]["pdf_url"]`
- Extracts embedded hyperlinks via regex + PDF link annotations
- Stores in `urls` (text array)

---

## Step 3 — Enrich Projects (3 APIs)

### 3a. Package metadata — packages.ecosyste.ms

```
GET https://packages.ecosyste.ms/api/v1/registries/{registry}/packages/{name}
```

Stored in `project.package` (full JSON). Registry mapping:

| Ecosystem | Registry |
|---|---|
| `pypi` | `pypi.org` |
| `cran` | `cran.r-project.org` |
| `bioconductor` | `bioconductor.org` |

### 3b. Commit history — commits.ecosyste.ms

```
GET https://commits.ecosyste.ms/api/v1/hosts/{host}/repositories/{full_name}
```

Stored in `project.commits_data`. Also scans committer emails for `.edu` domains → `educational_commit_emails`.

### 3c. README content — archives.ecosyste.ms

```
GET https://archives.ecosyste.ms/api/v1/archives/contents?url=...&path=README.md
```

Tries in order: `README.md`, `README.rst`, `README.txt`, `readme.md`, `readme.rst`. Stored in `project.readme_content`.

---

## Step 4 — Science Score (heuristic, 0–100)

Run via:

```bash
bundle exec rake science:update_scores      # top 100 projects
bundle exec rake science:update_all_scores  # all projects
bundle exec rake science:analyze_scores     # display breakdown
```

### Scoring signals

| Signal | Points |
|---|---|
| `.edu` committer email | +20 each |
| Academic maintainer email | +8 each |
| Academic owner | +20 |
| Institutional owner | +15 |
| DOI reference in README | +10 each |
| Academic link in README | +6 each |
| `CITATION.cff` present | +15 |
| `codemeta.json` present | +12 |
| Zenodo metadata present | +10 |
| Science term in README | +2 each |
| PyPI ecosystem | −10 |
| Non-science keyword | −25 each |
| Corporate indicator | −30 each |

---

## Step 5 — Export Snapshots

```bash
EXPORT_DATE=2024-03-31 BUCKET_NAME=ecosystems-data bundle exec rake exports:record
```

Records a snapshot of `mentions_count` in the `exports` table with the date and bucket name.

---

## Full Pipeline Diagram

```
CZI Input Data
├── comm_disambiguated_dois_count.json   { czi_id → [doi, doi, ...] }
└── {ecosystem}_df.csv                  { czi_id → package name }
         │
         ▼
  rake import:{cran,pypi,bioconductor}
         │
         ├── Project.find_or_create_by(ecosystem, name, czi_id)
         ├── Paper.find_or_create_by(doi)
         └── Mention.create(paper, project)          ← link created here
                  │
                  ├── Paper enrichment
                  │     └── OpenAlex API → title, date, full metadata, PDF URLs
                  │
                  └── Project enrichment
                        ├── packages.ecosyste.ms  → package metadata
                        ├── commits.ecosyste.ms   → commit history, .edu emails
                        └── archives.ecosyste.ms  → README content
                                   │
                                   ▼
                        rake science:update_scores
                              → science_score (0–100 heuristic)
```

---

## Key Insight

Mentions are **not** discovered by mining paper text for software references. They come entirely from the **CZI pre-computed disambiguation dataset** — a mapping the Chan Zuckerberg Initiative assembled to associate open-source packages with the papers that cite or use them.
