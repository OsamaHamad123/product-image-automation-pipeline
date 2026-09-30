# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [2.0.0] — Unreleased

This release answers the owner's report that the search "never gave correct results". An audit found the
causes: the quality gate threw away white-background packshots, an unverified "last resort" pick counted as a
success, siblings of the right product outranked it, and reviewers' rejections were never remembered. The search
core was rebuilt and wired into the queue, the dashboard actions and the sheet writes. Every claim below has a test.

### Added

- **`catalog_match/` search core** (behind the unchanged `image_search.search_best_product_image`, `SEARCH_ENGINE=v2`):
  identity parsing (brand index built from the Brands Mapping sheet, GTIN, sizes, variants), a deterministic query
  plan, Serper / Open Food Facts / Bing-HTML-fallback providers, identity-tier ranking with hard rejects, soft
  image quality, a fail-closed Gemini label reader, and D10 decision routing. v1 stays as a 30-day rollback.
- **Offline evaluation harness** (`tests/eval`, `scripts/eval_report.py`, `scripts/eval_record.py`,
  `scripts/smoke_live.py`) with a 63-SKU UAE golden set and a binding merge gate for v2.
- **Reviewer negatives:** table `rejected_images` (URL, pHash, reason code). Rejected images are excluded from
  every later search for that SKU; `reject_image` accepts `research: true` to search again at once.
- **Queue columns** `sku_key`, `payload_json` (Arabic name and brand, category, size), `worker_id`,
  `lease_until`, `failure_code`, `trace_json`. **Curation columns** `status`, `reasons_json`, `evidence_json`,
  `vlm_json`, `content_sha256`, `identity_tier`, `sku_key`, `run_id`, `page_url`. **Cache columns** `sku_key`,
  `verification_status`, `approved_by`. All migrations are idempotent (`ADD COLUMN IF NOT EXISTS`).
- **Sheet outbox safety:** identity re-check of the target row at flush time (`CONFLICT`), column resolved by
  header name, newest update per cell wins, row-by-row retry after a failed batch, `attempts`/`last_error`, `DEAD`
  after 5 attempts.
- Header synonyms for the product sheet (EAN/GTIN/UPC/Item Name/Brand Name/Size/Arabic headers) and the optional
  `Sub-brands` and `Official domains` columns in Brands Mapping.
- Worker start-up check of the configured Gemini model; the result is shown in `automation_state.notice`.
- Settings `SEARCH_ENGINE`, `SERPER_API_KEY`, `AUTO_PUBLISH_ENABLED`, `AUTO_PUBLISH_BRANDS`, `OUTPUT_CANVAS_SIZE`.

### Changed

- **Auto-publish** happens only on decision `AUTO_PUBLISH` (off by default, per-brand allow-list); the
  `clip_score` threshold is gone and cache hits are never auto-published.
- **Worker retries** only when every provider is down or the search raised; a clean "no match" is recorded once
  with its failure code. A provider outage returns the row to `pending` and is not logged as a product failure.
  The worker exits only when a real `COUNT(*)` of open tasks returns 0 (or after 5 consecutive provider outages).
- **Queue claim** is one atomic `UPDATE` with a 15-minute lease; database errors propagate instead of looking
  like an empty queue. **Enqueue** is an upsert that never resets rows in review or completed, validates the
  sheet and filters before touching the queue, and skips rows whose link is final (`FORCE_OVERWRITE_IMAGES`
  now defaults to `False`).
- **Cache** serves only `human_approved` / `auto_verified` resolutions, strictly by barcode when there is one
  (no name fallback). Existing rows become `legacy` and are not served.
- **Approve** requires `sku_key` (and the barcode when the row has one), uses the 800×800 canvas without any
  upscale, writes sheet metadata only after the Cloudinary upload succeeds, and writes `needs_review:` when the
  background could not be removed.
- **Reject** validates the reason code, supersedes the cached resolution, deletes the row's candidates and blanks
  the sheet cell only when it still holds the rejected URL.
- `cli_bridge.py` prints exactly one JSON document on stdout (UTF-8, safe on a cp1256 Windows console); logs go
  to `temp/search.log`. The search response carries `status`, `decision`, `failure_code`, `candidates` with
  status/reasons/evidence, `provider_health` and `sku_key`. Brand "alignment" through Gemini is no longer called.
- An empty sheet brand stays empty (the first-word brand guess is deleted); a missing name column or a missing
  configured tab is an error instead of a silent positional or first-tab fallback.
- Redis write-behind is used only while `sync_worker.py` keeps its heartbeat key alive; sync_worker never drops a
  key before a successful write and leaves unknown payloads in place.
- `fastapi_server.py` is a thin development wrapper over the `cli_bridge` actions and is not launched.
- `.env` values no longer override variables already set in the environment.

### Removed

- `verification_layer/` (87 modules) and the 23 test files that only exercised it or asserted nothing
  (`tests/test_report_*.py`, `test_verification_pipeline.py`, `test_validate_blade_js.py`); the pHash/BK-tree
  test survives as `tests/test_phash_bktree.py`.
- `celery_config.py`, `distributed_lock.py`, `catalog_dedup.py`, `self_healing.py`, `google_drive.py`.
- The fabricated `/api/dashboard-enterprise-metrics` endpoint, the verification router and the hand-rolled Redis
  payloads in `fastapi_server.py`; CLIP embedding and "active learning" JSON logging on approval.
- Settings `AUTO_APPROVE_THRESHOLD`, `IGNORE_UNIT_CLASH`, `USE_FALLBACK_SEARCH`, `SEARCH_CACHE_*`,
  `MAX_PARALLEL_DOWNLOADS`, `DRIVE_FOLDER_ID`.
- The v1 "visual duplicate" shortcut that answered a search with another product's Cloudinary image when the
  pHash was within 5 bits. Flavour and size variants share packaging, so it handed out the wrong variant. The
  shared BK-tree (1.0.0 fix) is still built and filled with every saved image's hash; nothing substitutes answers
  from it.

### Corrections to 1.0.0

The 1.0.0 entry described components that never ran in production: no Celery worker was ever started, Google
Drive upload and the Redis GPU lock were unused, the RRF "hybrid search" and SSRF-safe proxy in
`verification_layer/` were not on the search path (their output was discarded), and the FastAPI service could not
start because of a missing import. The "Gemini Vision verification" passed images on errors. These are removed
or replaced above.

## [1.0.0] — 2026-09-29

First public release.

### Added

- **Pipeline:** reads SKUs from Google Sheets, searches several image sources, verifies candidates with Gemini
  Vision and a quality gate, removes the background, normalises to 800×800 on white, drops near-duplicates,
  uploads to Cloudinary or Google Drive, and writes the link back to the sheet.
- **Services:** a FastAPI service (REST plus server-sent-events progress), a queue worker, Celery workers on
  separate crawl, GPU and de-duplication queues, a Redis write-behind sync worker that retries on HTTP 429, and a
  Laravel 11 curation dashboard.
- **Near-duplicate detection** with DCT perceptual hashing indexed in a BK-tree (`image_dedup_bktree.py`).
- **Hybrid search** that merges image sources with Reciprocal Rank Fusion (`verification_layer/`).
- **SSRF-safe image proxy** that refuses internal-network addresses.
- **Distributed GPU lock** on Redis, with a local fallback when Redis is unavailable.
- **Pluggable background removal:** PhotoRoom or remove.bg in the cloud, or rembg or Bria RMBG locally.

### Changed

- Repository tidied: tests moved to `tests/`, and scripts and docs have their own folders.
- All secrets are read from the environment. Logs, caches and the local database are no longer committed.

### Fixed

- Near-duplicate detection now has something to compare against:
  - The BK-tree is built from the hashes stored in MariaDB. Before, `build_bktree_from_db()` returned an empty tree.
  - Every accepted image's hash is saved with the product and added to the tree.
  - The perceptual hash no longer spends a bit on overall brightness, which was 1 for nearly every image.
- Two verification modules that failed to import (missing `typing` names).
- Three `async` tests were collected but never awaited. They now run under `pytest-asyncio`.

### CI

- GitHub Actions runs the whole suite on every push and pull request, against a MariaDB service container.
- CodeQL scans the Python code and the workflows. Dependabot keeps the GitHub Actions and the dashboard's
  Composer and npm dependencies current.

[1.0.0]: https://github.com/OsamaHamad123/product-image-automation-pipeline/releases/tag/v1.0.0
