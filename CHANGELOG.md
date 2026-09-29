# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

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
