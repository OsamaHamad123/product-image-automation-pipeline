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
- `verify_cloud_services.py` also checks the Serper key (one test image query). It checks Gemini with the same
  `models.get` call the worker makes at start-up, with the key in a header instead of the URL. Google Custom
  Search is reported as optional. Everything it prints is redacted: configured key values, `key=` query
  parameters and proxy credentials never appear in its output or on the diagnostics page.
- Error payloads from `cli_bridge` carry fixed messages. The exception text goes to `temp/search.log` and the
  Errors page, never into a response, because `fastapi_server` returns the same payloads over HTTP.
- `docs/walkthrough.md` is rewritten for the current system: keys, sheet columns, the Brands Mapping tab, the
  daily review flow and reject reasons, failure codes, auto-publish, and measuring accuracy on real products.

### Fixed during the live dry runs (60 real sheet rows, `scripts/smoke_live.py --dry-run`)

- Image downloads go direct first, with a browser TLS fingerprint (`curl_cffi`) when it is installed, and use the
  proxy only as a fallback after a timeout, connection error, 5xx, 403 or 429. Timeouts on rows 2-31 fell from 96
  to 9.
- Serper free plans refuse `site:` operators with HTTP 400. The provider notices this once, strips the operators
  and searches the plain query for the rest of the run. Google Custom Search stops being called for the run after
  every key answered 403.
- Retailer image URLs are cleaned to the original file: Amazon size and overlay modifiers (`._AC_SL1500_`,
  `._PIRIOFOUR…`), and noon and Carrefour resize parameters.
- A tier-1 image the verifier never read (it skipped the image, or the second call failed) is no longer
  pre-checked while the verifier is up. A failed second call marks the SKU `VERIFIER_DOWN` and blocks
  auto-publish.
- When the verifier reads ANOTHER brand on a tier-1 image, no unconfirmed tier-1 image is pre-checked for that
  SKU. Row 34 (`FRESHLY CHICKEN SHAWARMA`): "Freshly" is also an English word, and Seara, Zingo and Americana
  listings scored tier 1 on it.
- Tuna meat grade (light / white / fancy) and cut (solid / chunks / flakes) are variant axes, together with the
  sheet's abbreviations `L/MEAT`, `WT/MEAT`, `S/F OIL`, `SUNFL OIL`, `VEG OIL` and `SALT WATER`. Rows 58 and 60
  had each been given the other grade's photo although the model read the grade correctly. These phrases count
  only next to a canned-fish word, so "white cheese" or "spring water" are unaffected.
- The dry-run report always shows the pick, with its rank and reasons, even when it ranks below the top 5.

### Added after the live dry runs

Built in parallel by five work packages, each checked by an adversarial reviewer, and measured against the 60
live sheet rows (`tests/catalog_match/fixtures/live_rows_2026_09_30.json`). The offline eval is unchanged: correct
pick 100%, wrong auto-publish 0%.

- **Search text:** sheet shorthand is written out in the queries (`S/F OIL`, `SUNFL OIL`, `VEG OIL`, `WITH VEG`,
  `L/MEAT`, `WT/MEAT`). The rules are in `catalog_match/data/abbreviations.json`, each with a reason, and
  ambiguous shorthand stays as written. Brand cells lose stray punctuation (`SUPER T/` becomes `SUPER T`), and
  a brand glued in the name (`ALALALI`) is written once. 15 of the 60 live rows get a better Q1.
- **Brands that are common words** (Freshly, Family, Target, Golden Prize ...; list in
  `catalog_match/data/common_words.json`): a brand hit is full evidence only at the start of the title or the
  slug, or on the brand's official site. Otherwise the listing is capped at tier 2
  (`generic_brand_position:<field>`).
- **A UAE store's other-country section** (noon `/saudi-en/`, Lulu `/en-kw/`, talabat `/ar/kuwait/`) scores as
  other retail, not as a UAE page. It can no longer reach tier 1 or be auto-published.
- **Reviewer warnings** on the pick, shown in Arabic on the review screens and printed by `smoke_live.py`:
  - the sheet does not name the variant the image shows (new fries-cut and cheese-form axes);
  - Gemini unsure;
  - low resolution;
  - WhatsApp or screenshot export;
  - social media;
  - a store outside the UAE.
  The pick and the decision are unchanged. Fries, paratha, nuggets and shawarma are treated as frozen by
  default: a 'Frozen' listing is neither capped nor warned.
- **Reviewer decisions** go to the new table `review_decisions` (approve, reject, manual upload, from the
  batch or the catalog page), including whether the image was the engine's pre-check. The active-learning page
  and `scripts/review_stats.py` show each brand's pre-check precision with a Wilson 95% lower bound. A brand is
  ready for auto-publish at 30 or more reviewed pre-checks and a lower bound of at least 0.98 (about 189
  accepted pre-checks with no miss). The suggested `AUTO_PUBLISH_BRANDS` value is read-only.
- **Health and cost panel** on the diagnostics page: decisions, provider call outcomes, verifier calls,
  estimated cost and failure codes over 24 hours and 7 days, with a red notice when Serper credits run out or
  Gemini stops answering. Each search is stamped with `searched_at`.
- **Nightly run:** `scripts/run_nightly.py` queues the rows without a final image and works the queue with
  auto-publish forced off. `scripts/schedule_nightly.ps1` registers it in Windows Task Scheduler.

### Fixed after the live dry runs

- `setup_and_launch.ps1` and `launch_desktop.ps1` are saved with a UTF-8 BOM. Windows PowerShell 5.1 could not
  parse the Arabic text of `setup_and_launch.ps1` without it.

### Fixed in the dashboard audit (phase 1)

- **Stop and reset keep work.** Stop asks the worker to finish its current row and stop; queued rows, candidates
  and review decisions stay. Reset is now «إصلاح تشغيل عالق»: it clears a stuck lock and returns `processing` rows
  to `pending`, and never deletes a row. Each run has a `run_id` (`automation_queue`, `automation_state`), so the
  progress bar counts that run instead of the whole queue. A failed run shows a red banner instead of looking idle.
- **A late search answer can no longer publish to the wrong product.** Each search on the review page carries a
  token and is aborted when the product changes; `select_image`, `reject_image` and `upload_manual_image` refuse a
  `sku_key` that does not match the product in the request. One approval runs at a time; number keys only select,
  and modifier keys are ignored.
- **Diagnostics no longer spend Serper credit on every visit.** The connection check runs from a button, is bounded
  to 45 seconds, treats Serper as critical and keeps its last result.
- Raw sheet rows and review products have separate cache keys; the errors tab updates during a run; retry says the
  rows are queued. Bulk approve and reject send the Arabic name, Arabic brand and category, and a manual upload
  passes them on, so the product check does not depend on the stored queue row.
- Laravel migration `2026_10_02_000001` mirrors the Python schema: `run_id`, `stop_requested` and the
  `review_decisions` table.

### Added in the redesign

- **Laqta Studio UI foundation:** design tokens (`public/css/laqta.css`), `x-lq.*` Blade components, the RTL app
  shell `layouts/laqta.blade.php` and a `/ui-kit` reference page.
- **Five pages instead of eight**, all on the approved Laqta design (Arabic, RTL, desktop and phone):
  - **الرئيسية** (`/`): where the sheet's products stand, the last run with its cost, auto-publish readiness per
    brand, services from the last connection check, and this week's cost. The waiting-for-review number is the
    one the sidebar badge shows.
  - **المراجعة** (`/catalog`): one screen, product image first, for one product (Enter approves, X rejects with a
    reason, S skips, 1-5 select) or many (`?mode=bulk`: approves only the system's suggestions without a warning,
    and only the cards on screen). Approvals run in the background one at a time. Failures are the «أعطال» filter.
  - **التشغيل** (`/batch-automation`): a run over the whole sheet, a brand or chosen rows, with the number of
    products to search, those skipped, and the estimated cost and time before it starts; the live run with pause,
    a stop that deletes nothing, «إصلاح تشغيل عالق» only when it is stuck, and the latest results.
  - **الصحة والتكلفة** (`/system-diagnostics`): the on-demand connection check, searches over 24 hours or 7 days,
    cost, why products were not found, and redacted log tails.
  - **الإعدادات** (`/settings?tab=`): the sheet connection, the keys (state only, never their value), auto-publish
    per brand with «تفعيل» only for a brand at the 98% bar, image processing, and the v1 rollback.
  - `/errors`, `/rich-catalog`, `/active-learning` and `/batch-automation?tab=review` redirect to their new place.
- The sidebar run card reads the same phase, alert and stuck reason as the Run page.

### Changed in the redesign

- Approvals and manual uploads use the saved «تحسين الألوان» setting.
- The foreign-store warning covers every country domain outside the UAE (a `.ca` or `.co.uk` store), not only
  the Gulf ones; generic two-letter domains (`.io`, `.co`) do not warn.
- Dashboard sessions and cache are files (`SESSION_DRIVER=file`, `CACHE_STORE=file`), so the pages can say the
  database is down instead of failing with HTTP 500. The launcher switches an existing `dashboard/.env` once.

### Removed in the redesign

- The rich catalog page and its API (`/api/rich-products*`), the brand-estimate and `/api/logs` endpoints, the
  active-learning page and its reset, and `layouts/layout.blade.php`. The CSV export stays, in bulk review.

### Added in phase 3 (coverage and accuracy)

The second live dry run pre-selected 32 of 55 measured products, and 17 of the other 23 had no correct image
anywhere in the Google Images results. Phase 3 widens where the search looks, makes the label reader switchable,
and treats the barcode as supporting evidence. It was built in four packages, each checked by an adversarial
reviewer. The offline eval gate is unchanged: correct pick 100% on the normal and noisy-reader scenarios, wrong
auto-publish 0%.

- **Expansion round** (`catalog_match/expand.py`, `catalog_match/pages.py`). When the normal search ends with no
  confident pick, one more round runs within `EXPANSION_MAX_CALLS` paid calls (default 4):
  - a Serper web search for the product page on the brand's site and UAE stores;
  - Google Shopping through Serper;
  - visual search (Serper Lens, or SerpApi Google Lens with `SERPAPI_API_KEY`) seeded by the best near-match.

  Store pages are read without running scripts (JSON-LD, `__NEXT_DATA__`, `og:image`). New candidates go through
  the same scoring, download, quality and verifier rules. Page images are never auto-published. A pick flagged
  low-resolution gets one visual search for a larger copy of the same packshot.
- **Switchable label readers** (`catalog_match/verifiers/`):
  - `VERIFIER_PRIMARY` reads every batch (default Gemini 3.1 Flash-Lite).
  - `VERIFIER_STRONG` takes one second look at an unsure top candidate (default Gemini 3.5 Flash). Claude
    Haiku 4.5, Sonnet 5.5 and Opus 5.5 are available with `ANTHROPIC_API_KEY`.
  - The second look never turns a MISMATCH into a MATCH.
  - It stops for the month at `VERIFIER_MONTHLY_BUDGET_USD` (default $5).
  - Every reading records its model, tokens and cost (`outcome.vlm_usage`).
- **Barcode as evidence** (`GTIN_POLICY=evidence`, default; `strict` and `off` remain).
  - A matching page barcode strengthens identity only when the brand agrees.
  - A different one caps the candidate at tier 2 and needs a full verifier MATCH to be pre-checked. The reviewer
    sees «الباركود بالشيت مختلف عن باركود صفحة المتجر».
  - The barcode cache serves an approved image only when brand, name and size agree.
- **Best-resolution copy:** a near-identical copy of the pick at a higher resolution is published instead.
- **Settings:**
  - tab «نماذج التحقق»: models, prices per 1M tokens, an estimate per 100 products and the monthly budget;
  - write-only Anthropic and SerpApi keys;
  - in «متقدم», «مصادر البحث الإضافية»: the expansion round, paid calls per product, visual search, the SerpApi
    price and the barcode policy.
- **Health and cost:**
  - one cost line per label-reading model and one for SerpApi Lens;
  - names for the new Serper endpoints;
  - the Run page cost counts every Serper endpoint, SerpApi and each model's recorded spend, as `ops_health` does.
- **Measuring:**
  - `scripts/smoke_live.py --probe`: one cheap call per paid service, never a key.
  - Every dry run ends with a coverage and cost summary. `--json` saves it, and `--no-expansion` measures without
    the expansion round.
  - `scripts/compare_runs.py old.json new.json [--md] [--out]` compares two runs.
  - `scripts/eval_record.py --prefill-labels-from-db` copies the dashboard's review decisions into `labels.csv`.

### Fixed after the owner's live run of 2026-10-03

`runs/2026-10-03/smoke_6.json`, sheet rows 2-61, is the baseline before phase 3: 33 of 60 pre-checked, and
every pick checked by hand shows the right product.

- **Plural brand names:** a brand name's last word now matches with or without a final 's' in scoring
  and in the label reading. `KITCHEN TREASURE` in the sheet now matches `Kitchen Treasures` in a store
  title (row 39); before, that listing scored as no-brand tier 3.
  - Only Latin words of 4+ letters, never one ending in 'ss', and only whole words.
  - Product words keep the exact rule.
  - Typos such as `INA PARAMANS` and `RIO MARIE` still need a Brands Mapping synonym.
- **desertcart country stores:** `angola.desertcart.com` (row 47) is now a store outside the UAE and gets
  the review warning. `uae.desertcart.com` and `desertcart.ae` do not.
- **Run outputs:** moved to `runs/2026-10-03/`. New `smoke_*.json`, `smoke_console*.txt`,
  `verify_output*.txt` files and new files in `runs/` stay out of git.
- **Pieces after the weight (row 16):** `MEHRAN PLAIN PARATHA 400GM 5S` is one 400 g pack of 5 pieces, not 5 packs
  of 400 g. The first query asked for `5x400g` before; it now asks for `400g`.
  - Applies only to foods sold by the piece: paratha, roti, chapati, naan, tortilla, wraps, pita, khubz, samosa,
    spring rolls, and their Arabic names.
  - `INDOMIE NOODLES 75G 5S` is still 5 packs. An explicit `2X400GM 5S` keeps its pack count of 2.

### Added: local catalog index (free retrieval from the stores' own sitemaps)

- **What it does:** UAE store product pages become a local index. While the first web query runs, the pipeline
  looks the product up in that index, at no cost.
  - Sources: Lulu, Carrefour UAE, Spinneys and talabat mart UAE. noon and Union Coop are present but switched off,
    because their page slugs often leave out the brand.
  - The index is built from each store's published sitemaps, read by `scripts/build_catalog_index.py`.
- **Search:** finds rows by every word of a brand phrase, plus rows whose page stated the product's barcode.
  - A row is dropped when it has another brand, a size, pack or variant conflict (the link alone is enough), or
    too few product words.
  - The best `LOCAL_INDEX_MAX_PAGES` pages (default 3, one per store first) are read for their main image, name
    and barcode.
- **Page cache:** what a page said is kept for `LOCAL_INDEX_PAGE_TTL_DAYS` (default 30). A timeout or refusal is
  retried after a day. A page that now redirects (sold out, delisted) gives nothing and is remembered.
- **Limits on what the index can do:**
  - Its pages are provider `local_index` and unsanctioned: they can be pre-checked for review and never
    auto-publish.
  - They never stop the web search early.
  - Their answers never hide a web-search outage.
  - The offline evaluation is unchanged.
- **Polite harvesting:** `catalog_match/sitemaps.py` follows robots.txt rules, Sitemap lines and Crawl-delay,
  says who it is, and reads gzip sitemaps and nested indexes. It refuses any document with a DOCTYPE.
  - A store that answers 401, 403 or 429, a bot-check page, or a robots.txt 5xx is reported BLOCKED and skipped.
    It is never worked around.
  - While the pipeline searches, a store that refuses three page reads is left alone for the rest of the run.
- **Commands:**
  - `--discover` looks first and writes nothing.
  - `--dry-run` counts only.
  - With no flag, the command builds the index.
  - `--prune` drops pages a store no longer lists, after a complete harvest only.
  - `--stats` prints what the index holds.
  - `--json` saves the reports.
  - Store patterns are in `catalog_match/data/catalog_stores.json`, taken from the product pages of the live runs.
    The stores' sitemap locations are found with `--discover`.
- **Tables:** `catalog_products`, `catalog_tokens` (slug and page-title words, deleted with their row) and
  `catalog_harvests`, all created by `init_db()`.
- **Settings:** `LOCAL_INDEX_ENABLED`, `LOCAL_INDEX_MAX_PAGES` and `LOCAL_INDEX_PAGE_TTL_DAYS`.
  - The dashboard «متقدم» tab has the switch and the pages-per-product field, and says what the index holds or how
    to build it.
  - The Health page names the provider «الفهرس المحلي».

### Added: search accuracy (brand spellings, page recovery)

Taken from the 27 rows of `runs/2026-10-03/smoke_6.json` that had no pick. In 10 of them the sheet's brand
was written differently from the stores. In several more, the right store page was found, but the picture
Google filed under it was another product.

- **Brand discovery** (`catalog_match/brand_discovery.py`)
  - When the brand is not in Brands Mapping and no listing writes it the sheet's way, the opening words of
    each listing are compared with the sheet brand.
  - **Typo:** one word one letter away, or two letters swapped, never on its first or last letter.
    `RIO MARIE` matches Rio Mare, `INA PARAMANS` matches Ina Paarman's, `BARTS TRADITON` matches Barts
    Tradition. American never matches Americana.
  - **Abbreviation:** only when the sheet brand is written with `/`, `.` or a one-letter word.
    `SUP/T`, `SUPER T/` and `SUPER/T` all match Super Tasty.
  - The store spelling counts only when:
    - the same listing also names the product type;
    - it comes from a UAE store or from two different sites;
    - it is not a known other brand.
  - Once found, the store spelling is used everywhere:
    - one more query is sent, written with it;
    - scoring accepts it;
    - the label reader is told about it.
  - What a discovered spelling never does:
    - it never makes a mapped brand, so nothing auto-publishes on it;
    - a pick supported only by the store spelling carries the new review warning `brand_spelling`.
- **Page recovery (X0)**, a free first step of the expansion round.
  - When the page names the right product (tier 1 or 2, the brand, no size conflict) but its picture failed,
    the page is read for its own main image. A failed picture means any of:
    - it could not be downloaded;
    - it is a thumbnail or a banner;
    - the label reader saw another brand, several products, or not a front packshot.
  - At most 3 pages, never a social network or stock-photo site.
  - Their images are verified with one call. When that gives a pick, no paid call is made.
  - Rows 13 (Ansar Gallery, Yumway) and 36 (Tradeling, Green Farm) were this case.

### Fixed while integrating phase 3

- A barcode-conflict MATCH that cannot be pre-checked no longer skips the second verifier call on the next
  candidates.
- `verify_cloud_services.py` masks the Anthropic and SerpApi keys like the others.
- The worker's start-up check reads the configured primary model. A Claude primary without an Anthropic key is
  reported as «every result goes to review» instead of probing Gemini.
- The review screens and the reader-outage messages say «نموذج القراءة» instead of Gemini, because the primary
  reader can be a Claude model.
- `.gitignore` keeps ignoring HTML dumps but lets the offline test fixtures under `tests/catalog_match/fixtures`
  be added.

### Removed

- `verification_layer/` (87 modules) and the 23 test files that only exercised it or asserted nothing
  (`tests/test_report_*.py`, `test_verification_pipeline.py`, `test_validate_blade_js.py`); the pHash/BK-tree
  test survives as `tests/test_phash_bktree.py`.
- `celery_config.py`, `distributed_lock.py`, `catalog_dedup.py`, `self_healing.py`, `google_drive.py`.
- The fabricated `/api/dashboard-enterprise-metrics` endpoint, the verification router and the hand-rolled Redis
  payloads in `fastapi_server.py`; CLIP embedding and "active learning" JSON logging on approval.
- Settings `AUTO_APPROVE_THRESHOLD`, `IGNORE_UNIT_CLASH`, `USE_FALLBACK_SEARCH`, `SEARCH_CACHE_*`,
  `MAX_PARALLEL_DOWNLOADS`, `DRIVE_FOLDER_ID`.
- The "Next-Gen Frontiers Telemetry" panel on the diagnostics page. Its figures, such as "Accuracy 98.4%",
  were hard-coded and described deleted `verification_layer` modules.
- The per-brand "self-correction rules" on the active-learning page (padding ratio 0.70/0.75, strict clutter
  check). The v2 engine never applied them. The page now shows real review statistics.
- `scripts/diagnose_search.py`, which probed the retired Google CSE/Bing/DDG scrapers, and
  `scripts/verify_upgrades.py`, which tested deleted modules and wrote a test link to row 9999 of the live sheet.
  Use `scripts/smoke_live.py --dry-run` instead.
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
