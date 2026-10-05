# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [2.0.0] — Unreleased

This release answers the owner's report that the search "never gave correct results". An audit found the
causes: the quality gate threw away white-background packshots, an unverified "last resort" pick counted as a
success, siblings of the right product outranked it, and reviewers' rejections were never remembered. The search
core was rebuilt and wired into the queue, the dashboard actions and the sheet writes. Every claim below has a test.

### Added: auto-publish earned across brands (lanes, shadow mode first)

With about 20 products per brand no brand ever reached the 30 reviewed pre-checks (189 for a 98% lower bound), so
auto-publish never ran. The evidence now also pools across brands, by the kind of pick:

- **Lanes**: `catalog_match.decide.pick_lane` puts every pick in lane `strict` (its only auto blockers are
  `auto_publish_disabled`, `auto_publish_off_for_brand` and `brand_conf_*`: every other rule passed), `unsure`
  (`preselected:tier1_unsure`, display only) or `other`, and route() writes `lane:<name>` on the pick. A pick with
  any review warning (low resolution, foreign store, a variant only one side states, ...) is `other`: lane `strict`
  is the pick with nothing left for the reviewer to check, and the lane never auto-publishes a warned pick
  (`auto_blocked:review_warning`).
- **Recorded on every review**: new nullable `review_decisions.lane` (idempotent schema migration); approve, reject and
  manual upload store the lane of the engine's pick (from the stored candidates, the catalog screen's live search via
  `search_lane`, or `strict` for an auto-published image). Picks stored before the lanes get theirs from their
  `auto_blocked:*` reasons. Undone decisions never count.
- **Lane stats**: `review_stats` (bridge, settings, `scripts/review_stats.py`) adds per lane the reviewed pre-checks,
  accepted, replaced, rejected, precision, Wilson lower bound and readiness on the brand thresholds.
- **Settings «النشر الآلي»**: a section «النشر الآلي لكل الماركات المؤكدة» with lane `strict`'s numbers
  («من 12 اقتراح بهالفئة، اعتمدت 12») and a switch the server turns on only when the lane is ready; lane `unsure`
  is shown as information. New setting `AUTO_PUBLISH_STRICT_LANE` (default off; .env, system_settings,
  run_config.json). With it on (and auto-publish on), a strict pick of a mapped brand auto-publishes without its brand
  in `AUTO_PUBLISH_BRANDS`; every other blocker stays, an unmapped brand never auto-publishes. With it on and ready,
  the main switch may open without a listed brand.
- **Health page**: card «دقة الاقتراحات الحقيقية» (accepted / reviewed and the lower bound per lane) from GET
  `/api/system/review-lanes`, cached 60 s like ops-health.
- The lane publishes exactly what `AUTO_PUBLISH_BRANDS=*` publishes (tested on the golden set); the eval with the
  defaults is unchanged (58/58, 58/58, 52/58, no wrong auto-publish).

### Added: «ماركات ناقصة من Brands Mapping», an assistant that fills the sheet's brand tab

Every product of the owner's last 40 rows said `brand_unknown`, unmapped brands stayed `brand_conf = sheet_raw`
(`auto_blocked:brand_conf_sheet_raw`) and their official sites were unknown, because the tab was filled by hand.

- **`brand_suggestions`** (bridge, read only, no paid call): the queue's brands the sheet's own mapping does not know
  (placeholders and empty cells are no brand), most rows first, with the row count, the Arabic `brand_ar` and the store
  spellings the search discovered or learned as synonym suggestions. An unreadable sheet is an error, not «all missing».
- **`brand_official_site`** (only on the click «اقترح الموقع الرسمي»): exactly one Serper web query, recorded in
  `search_spend` (run `brand-site`) when answered; up to two sites that are not a retailer, marketplace, social or stock
  site and whose host or title holds the brand's main word. Writes nothing.
- **`brand_add`** (only on a click; POST `/api/run/brand-add`, `/brand-add-all`): appends one row per brand to
  `Brands Mapping` after a fresh read, refusing a duplicate, a domain that is not a bare host and more than ten
  synonyms (checked again on the server), and drops the brand cache so the next run sees it. A site is queued in
  `system_settings.pending_harvest_domains` and indexed at the start of the next worker run behind
  `LOCAL_INDEX_ENABLED` (`brand_assistant.harvest_pending`, at most 3 sites and 120 seconds a run).
- **Run page card** with the row count, synonyms and site fields, «اقترح الموقع الرسمي» (one search) and «أضف» per
  brand, and «أضف الكل بدون مواقع» after a confirm. `tests/test_brand_assistant.py`, `tests/test_laqta_run_brands.py`.

### Added: «تراجع عن الرفض», the store page's barcode, a page's gallery in X0, and the reviewers' decisions in the export

The owner's last runs: a rejection made only to try the button could not be taken back (the image stayed excluded and
counted in the stats); none of the 120 products has a barcode in the sheet although store pages state one; X0 only
tried a page's main image (Golden Prize 185g on carrefouruae shows a twin pack, Emirates Coop its placeholder logo);
and the export never said whether the reviewer took the pre-checked pick.

- **Undo a rejection**: `local_cache_db.undo_rejection` removes one `rejected_images` row (sku_key + image URL), marks
  the matching `review_decisions` row with the new nullable `undone_at` (and gives a WRONG_BRAND rejection back to the
  store spelling it counted against, the new `learned_alias`), and puts the excluded candidate back among the
  suggestions. `get_review_decisions`, `review_stats` and the learned brand sources skip undone rows. Bridge action
  `undo_reject`, POST `/api/review/undo-reject` (CSRF like the other POSTs); the review screen lists the product's
  rejected images with «تراجع عن الرفض» after the confirm «ترجع هالصورة للاقتراحات؟».
- **Barcode from the store page**: candidate evidence carries `page_gtin` (the page's GTIN when checksum-valid and
  globally unique). An approval or auto-publish of that image for a sheet row without a valid barcode stores it in
  `resolved_products.page_gtin` / `page_gtin_url`; the review card shows «الباركود من صفحة المتجر: …» with a copy
  button; `scripts/export_barcodes.py` writes a CSV (row, name, brand, page_gtin, source page, approved,
  `duplicate_gtin` when two approved products got the same GTIN). The sheet is never written.
- **A page's gallery in X0**: when a recovered page's main image is no good for the SKU (the picture that failed, by
  address or bytes, or read as wrong), up to 2 more images of its own product gallery (JSON-LD / embedded product
  JSON; never og:image duplicates or recommendation carousels) are offered as `page` candidates marked `page_gallery`,
  within X0's one verifier call, and pre-checked only on a MATCH. `MAX_IMAGES_PER_PAGE` stays 1. A replay cassette
  without the gallery image's download leaves it out (no miss). The review marks it «صورة ثانية من معرض صفحة المتجر».
- **Review outcomes in the export**: each row has `review` (latest decision, image, domain, was_preselected, label
  reader, reason, time), `approval` (the published link) and `page_gtin`; `summary.review` counts the decisions and
  splits the pre-checked picks into accepted / replaced / rejected / pending, also by the pick's warning set. An old
  database gives an empty block. bg_skipped is not stored per approval, so it is not exported per row.

### Added: faster runs («كم منتج بيشتغل بنفس الوقت»), stage timings, a hedged Serper request and a slow-host breaker

The owner's three exports (151 rows) showed 100 rows taking 18.3 minutes with 3 rows in parallel, about 33 s a row:
Serper answered in 2.2 s at the median but 6.2 s at p90 and timed out 7 times in 176 calls; the expansion round ran
on 40% of the rows (119 extra calls), 3 of them rows whose brand is «GENERIC / NO BRAND» that can never be picked; and
29 image downloads timed out, mostly the same two hosts again and again (shops.ae, shinjukuhalalfood.com).

- **Stage timings in every row**: `pipeline.find_product_image` measures the wall time of retrieval, fetch, quality,
  verify, the expansion round (only when it ran) and the total with `time.monotonic`; `facade.outcome_summary`
  stores it as `trace.outcome.timings` (ms) next to `provider_health`. `scripts/export_run.py` exports it per row as
  `timings` and adds `summary.timings` (rows, and p50 / p90 / total seconds per stage). A row saved before this has
  `timings: {}` and is left out of the summary, so old exports and traces stay readable.
- **`WORKER_CONCURRENCY`** (default 5, clamped to 1..8; it was a hard-coded 3): read like the other worker settings
  (`system_settings.worker_concurrency` through `config.load_db_config`, else `.env`, through
  `catalog_match.settings.worker_concurrency`). Settings → «متقدم» → «سرعة التشغيل» has the field «كم منتج بيشتغل بنفس
  الوقت» (1..8, checked on the server) with the hint that more is faster but spends the search quota faster. Rate
  limiting was checked and is already process-wide: every provider name owns one token bucket that all worker threads
  and all per-row provider objects share, so five workers cannot exceed a provider's per-minute limit.
- **Hedged Serper request** (`SERPER_HEDGE_AFTER_S`, default 4.5, 0 = off): a Serper images / web / shopping request
  that has not answered after that long is sent once more and the first HTTP 200 answer is used; the late one is
  closed and ignored. A fast failure is not hedged; the duplicate takes a token from the shared bucket without waiting.
  Visual search is never hedged. The per-request timeout of these endpoints is now 10 s (was 15 s). The duplicate is
  one more Serper credit and is counted wherever credits are: the call records `hedges` (`provider_health`, `hedged` +
  `hedges` in the run export), and `spend_from_outcome`, the health counters, the dashboard's run cost and the dry-run
  cost count `1 + hedges` for an answered call. Hedging is off whenever a cassette is installed (record, replay, fill),
  so a recording never gains a request it does not hold and a replayed call is never counted twice.
- **Slow-host breaker** (`catalog_match.fetch.HostBreaker`, one per process, thread-safe, cleared at the start of each
  run): a host whose downloads ended in `timeout` or `connection_error` twice within 15 minutes (three for a UAE
  retailer of `trusted_domains.json`) is skipped for the next 15 minutes: its candidates come back at once as
  `host_slow`, counted in `reject_counts` as `download:host_slow`. A host that answers (403, 404, 5xx, not an image)
  never counts, and a download of it that comes back forgets its earlier failures. A candidate whose page or image
  host is a UAE retailer is always downloaded (`HostBreaker.exempt`): an image CDN such as `m.media-amazon.com` is
  shared by every listing of its store, so pausing it would drop the store for the rest of the run. The breaker is
  off under a cassette. `download_host_slow` has Arabic sentences in the review screen and
  the publish check.
- **Expansion**: a row with no usable brand (a placeholder such as «GENERIC / NO BRAND», or an empty cell with no brand
  in the name) no longer runs the round, since none of its listings can reach tier 1 or 2. The round now also stops
  as soon as it has a winner: after X1 + X2 their finds go through the normal stages at once, and a pick the label
  reader read as MATCH spares the visual searches and X5 (a weaker, UNSURE pick does not stop it).

### Added: «تجاوز عزل الخلفية», the tab the run reads, and 'N X M PCS' multipacks

«فحص النشر» showed the owner that PhotoRoom's credit had run out (`photoroom_402`): every approval failed and nothing
reached the sheet, and choosing «بدون عزل» in Settings did not help (approvals refused it as «الخلفية لم تُعزل»).

- **Publishing without background removal when the owner chooses it**: with the Settings method `none`
  (`processing_profile.skips_background`) and a canvas the method `none` returned, `main.publish_image` publishes it
  clean on every path (approval, manual upload, the worker's auto-publish, the sequential mode) and returns
  `bg_skipped=True`; the approval response carries `bg_skipped` («انتشرت بدون عزل الخلفية» on the review screen) and
  the run report counts them (`bg_skipped`, a Telegram line and the Health page's last-run card). An isolation that
  failed under any other method (`photoroom_402`, an unisolated canvas) is refused / `needs_review:` as before.
- **«تجاوز عزل الخلفية»**: `POST /api/settings/bg-method {method}` (method from `BG_METHODS`, 422 otherwise; CSRF like
  every POST) stores the previous method in `system_settings.bg_removal_method_previous`. The «فحص النشر» card offers
  the button, after a confirm that says what it does, when the processing step failed on PhotoRoom / remove.bg
  credit, key or quota (`publish_check.BG_SKIP_CODE_RE`), and «رجّع عزل الخلفية (<previous>)» while removal is off;
  nothing is re-checked automatically. The review screen's failed-approvals panel says what happened for every
  `photoroom_*` / `removebg_*` code («رصيد PhotoRoom خلص…») and offers the same skip; «أعد المحاولة» then publishes.
- **Settings → «معالجة الصور»**: «بدون عزل الخلفية: الصورة متل ما هي على لوحة بيضا وبتنتشر مباشرة» with a hint that a
  picture whose background is not white shows it; GrabCut / rembg are offered and named as free local methods only
  when installed (`image_processor.local_methods_available`, bridge action `bg_methods`, cached 6 h); the tab says
  «عزل الخلفية متوقف» with the restore button. `publish_check` step 2 with `none` is ✅ («عزل الخلفية متوقف بالإعدادات:
  الصورة بتنتشر متل ما هي», no paid call); its credit / key / quota failures say «… أو اضغط «تجاوز عزل الخلفية»».
- **The tab the sheet is written to**: the sheet step warns (⚠️) when the tab looks like a backup or a proposals tab
  («منتجات جديدة مقترحة 2», «Copy of Products», «New Products», مقترح/اقتراح/نسخة/احتياط, case-insensitive) with
  «النشر رح يكتب بتبويب «X» — إذا مش تبويب منتجاتك، اختار التبويب الصح من الإعدادات (تبويب «الشيت»)», and says the
  first tab is used when no tab is set. The Run page shows «التشغيل بيقرأ من تبويب «X»» from the configured tab, the
  last sheet read (`get_products` now returns `sheet_tab`) or the last check, never a new Google read.
- **Sizes**: `'5X170PCS'` / `'5 x 170 PCS'` / `'3 x 200 sheets'` / `"10 x 20's"` parse as a count of M with
  `pack_count` N (like `'16X25G'`), only when the text states no measured size; `'170PCS'`, `'PARATHA 5S 400GM'` and
  `'30 pcs'` are unchanged. 'FINE FACIAL TISSUE CLASSIC 5X170PCS' gets size and pack (no more «الحجم ناقص»).

### Added: «فحص النشر» on the Health page (a publish rehearsal)

The owner's 11 bulk approvals failed with no visible reason, and «فحص الاتصالات» only asks each service whether it
answers. The new check runs the real publish chain on a test image, step by step, without touching any product.

- **Four steps, each with its own timeout** (`publish_check.py`, bridge action `publish_check`, `POST
  /api/system/publish-check`, `scripts/publish_check.py [--json] [--no-save]`): the image of the first pre-selected
  candidate waiting for review (candidate store by its sha256, then the real `_download_bytes`, which now reports
  whether it went direct or through the proxy; the bundled `assets/selftest/publish_check_sample.png` when nothing
  waits for review), background removal with the current processing profile, an upload with the publish uploader
  to `laqta_selftest/publish_check` (fixed name, overwritten) that is deleted right after, and the products tab
  opened as publishing opens it, where the link column's header cell is read and the same value written back
  (`values_batch_update`, RAW, like the outbox); a header that cannot be read for sure is not written.
- **Each step says ✅ / ⚠️ / ❌ with its time and what to do in Arabic** («البروكسي ما بيرد: افحصه بصفحة الإعدادات أو
  شيله», «مفتاح Cloudinary مرفوض: حدّثه بالإعدادات», «حساب الخدمة ما عنده صلاحية تعديل على الشيت: شارك الشيت معه
  كمحرر»); a step that needs a failed one is «ما انفحصت». The tab title and the outbox backlog are shown. The codes
  stay in the tooltips, every text passes `verify_cloud_services._redact`, and the last result is kept in
  `temp/publish_check_last.json`. No product row, queue row, setting or lock is touched; a running worker gets a
  note only. It may cost one background-removal call (and one Gemini read).
- The review screen's failed-approvals panel links to the check («افحص النشر» → `/system-diagnostics#publish-check`).

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

### Added: learning from review decisions

Every review now teaches the search, beyond the reviewed product (`catalog_match/learning.py`). Nothing
learned ever auto-publishes.

- **Store spellings of a brand**
  - The review warning is now `brand_spelling:<spelling>`. The review screen sends a picture's warnings with
    the decision (`candidate_warnings`).
  - Approving such a pick records the spelling for the sheet brand (table `learned_brand_aliases`). A
    `WRONG_BRAND` rejection of one counts against it. The spelling is used while approvals outnumber those
    rejections.
  - From then on the sheet brand resolves to the spelling with the new `brand_conf` `learned`:
    - the first query writes it;
    - scoring and the label reader accept it;
    - no discovery query is needed.
- **A brand's sources**
  - A site the reviewers approved a brand's images from at least twice, with no identity rejection of an image
    from it for that brand, is a learned source. It is read from `review_decisions`. Social networks and stock
    sites never count.
  - For that brand only, a learned source:
    - gets UAE-retailer trust (`reviewed_source`);
    - gets a place in the `site:` query after the official sites.
- **The sheet always wins**
  - A spelling is never learned when Brands Mapping maps the sheet brand or the spelling (also as another
    entry's synonym).
  - A mapped brand only gains learned sources.
  - The merge happens in `google_sheets.get_brand_mappings`, so the worker, the dashboard search and
    `smoke_live` all see it. Without a database the mappings are unchanged.
- **Never an auto-publish:** `decide.py` blocks auto-publish for `brand_conf` `learned` (`brand_conf_learned`)
  and for a pick whose trust is only a learned source (`reviewed_source`).
- **`smoke_live` without the sheet**
  - `--rows-file` reads the products from a CSV or an earlier `--json` run.
  - `--brands-file` reads a Brands Mapping CSV.
  - The 60 products of the live run and the suggested mapping are committed under `runs/2026-10-03/`, so a
    machine with the keys but no `credentials.json` can measure the same products again.

### Fixed in the review of the four additions (before their first live run)

- **Learning from reviews**
  - A learned spelling stays with the sheet brand it was taught for. It never makes another product's brand
    a competitor (a `SUPER T/` product still finds and accepts Super Tasty once `SUP/T` is taught), and it is
    never matched at the start of another product's name.
  - Learned sites alone never give an unmapped sheet brand an identity: it stays `sheet_raw`, brand discovery
    still runs for it, and the sheet's name rule still wins (brand cell `NESTLE`, product `NIDO ...`).
  - The `brand_spelling` warning stays on a pick whose brand evidence is only a learned spelling, so a
    `WRONG_BRAND` rejection still counts against one mistaken approval.
  - A site is learned only from two different approved products, counted per brand as the search resolves
    it (two sheet spellings of one mapped brand count together; a rejection under either counts).
  - Listed UAE retailers, structured sources and stores outside the UAE are never learned: a listed retailer
    keeps its own trust, so learning never blocks its auto-publish, and a foreign store never gets UAE trust.
  - A larger copy of the winning picture from a learned source never replaces an `AUTO_PUBLISH` winner.
  - `smoke_live --rows-file` reads a `#` row column and `45.0` row numbers, and stops when the file has no
    product name column.
- **Local catalog index**
  - An image a page we read ourselves gave evidence to is never sanctioned, even when a search API also
    returns it: the index can pre-check an image for review, never make it auto-publishable, and it never
    stops the web search early.
  - A web-search outage is `PROVIDER_DOWN` even when the index (or Open Food Facts) answered, so the product
    is searched again; with no web search provider at all nothing was searched.
  - The index never cancels the relaxed queries R1/R2.
  - Pages known to be dead, pages that failed in the last day and hosts left alone take no read slot. The
    reads hold the first search step at most 8 s. Timeouts count toward leaving a store alone. A transient
    failure keeps the image and barcode an earlier read stored. The words indexed for a URL are the product's
    own slug (the segment before `/p/<id>`), not its department.
- **Brand discovery and page recovery (X0)**
  - The corrected query is not sent when a listing is already tier 1 in the store spelling, and the early stop
    uses the corrected brand.
  - A site's UAE section (Tradeling `/ae-en/`) counts as a UAE store. An abbreviation two brands could expand
    to is never guessed. Stock sites, social networks and listings without a page never vouch for a spelling,
    and the 7-letter minimum applies to the sheet brand too.
  - X0 reads one page once whatever its tracking parameters, only listings that name the brand in their
    title, page title or own slug (where a brand stands), and its one verifier call reads the recovered
    images first. When that call gets no answer, no paid call is made.
- **Sitemap harvester**
  - robots.txt is read as RFC 9309 says (longest rule wins, `*` and `$`, our own group before `*`, a byte-order
    mark, a decimal Crawl-delay). A store asking for a longer Crawl-delay than 60 s is skipped, never read
    faster than it asks.
  - An HTML page is a block only at every starting point before anything was read; later it is one failed
    sitemap. `/sitemap_index.xml` is asked only when `/sitemap.xml` gave nothing, and a guessed location that
    is missing is not a failure (so `--prune` can run).
  - A `Sitemap:` line on another host is reported, and read once its host is listed in the store's new
    `sitemap_hosts`. A redirect off the store or to its home page is a failed sitemap.
  - A broken file (corrupt gzip, an unknown or UTF-16 encoding) is one failed sitemap; an unexpected error is
    that store's `error` and the next store is still harvested; the `--json` file is always written.
  - An exact `--max-urls` with nothing left is a complete harvest; `--discover` reads every nested index.
  - A bad pattern or a byte-order mark in the stores file gives a clear message.

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

### Fixed: approvals that published nothing and said only «ما انعتمدت»

The owner's bulk approval of 11 pre-selected products on 2026-10-04 failed for all 11 with no reason, and nothing
reached the sheet.

- **The publish-time download goes direct first.** When the verified bytes are not in the candidate store, the
  approval downloads the picture again. With `PROXY_URL` set, that download went through the proxy only, so a slow
  or dead proxy failed every approval, while the search itself goes direct and uses the proxy only as a fallback.
  It now does the same: direct first, then the proxy after a timeout, a connection error, a 5xx, a 403 or a 429.
- **The candidate store is read from the project folder** for a relative `CANDIDATE_STORE_DIR`, as
  `catalog_match.fetch` writes it, whatever the process's working directory.
- **Every image code an approval can fail with has an Arabic sentence** on the review screen (`source_changed`,
  `download_*`, `not_image`, `source_missing`…); the code stays in the tooltip. They all showed «ما انعتمدت.».
- **A failed approval is written to the Errors log** with its code and row, so the cause can be read later.

### Fixed from the owner's second run export of 2026-10-04 (31 rows, 9 no-pick)

`laqta_run_2026-10-04_1933.json` was run on the release above: 22 of 31 pre-selected. Four of the nine no-pick rows
had the right picture. Re-routing the export offline gives 26 (rows 3, 5, 9, 15), loses none and auto-publishes
nothing; the first export keeps its 74.

- **Sharjah Co-op is a listed UAE retailer** (rows 3, 15). `sharjahcoop.ae` is in `uae_retailers`, and its store
  names ('Sharjah Co-operative Society', 'Sharjah Coop'…) are dropped from titles before brand matching. Its
  pictures were right in rows 8, 10, 12, 14 and 16 but counted as a generic site, so a lone Sharjah Co-op listing
  was never corroboration. The expansion round's second store group now also covers it (no extra query); other
  co-op sites are not listed.
- **A close size is UNSURE, not MISMATCH** (row 9). Every store and the label say 840 g for 'AL TAGHZIAH CHICKEN
  LUNCHEON MEAT 850G'. A label whose only 'no' is a size within the existing tolerance (but not equal) is UNSURE,
  and a pick carries the warning `size_close:840g/850g` («صحّح الشيت»). The size parser reads the EU estimated
  sign ('840ge', '840 g ℮').
- **A 'no' the reader's own text cannot support is set aside** (row 5). A size 'no' with no size read at all, or a
  variant 'no' whose printed words are exactly the sheet's description words, becomes UNSURE (never MATCH) and is
  recorded as `vlm:flag_overruled:size|variant`. A brand 'no' is never set aside; such a reading comes after any
  reading the reader left UNSURE itself, gets no strong second look and seeds no visual search.
- **'H/S' is hot & spicy on a meat product** ('ZWAN CHICKEN LUNCHEON MEAT H/S 340GM'): read as flavour chili by the
  variant lexicon and written out in the queries, context-bound so a towel's 'H/S' stays as written.
- **New warning `listing_silent:<axis>=<value>`**, the reverse of `sheet_silent`: the sheet states a marked variant
  (hot & spicy) that neither the store page nor the label shows. On the first export it flags exactly the three
  H/S rows (66, 67, 70), whose picks were plain cans with no warning and could have been bulk-approved.
- Eval gate unchanged: 58/58, 58/58, 52/58; preselect precision 100%, 100%, 96.3%; 0 wrong auto-publish.

### Fixed from the owner's run export of 2026-10-04 (32 no-pick rows)

The owner sent `laqta_run_2026-10-04_1619.json` (100 real rows: 68 pre-selected, 32 «بلا اقتراح»). In about ten of
the 32 rows the right picture was already among the candidates; a rule that was too strict for real store
listings kept it from being pre-checked. Re-routing that export offline with the new rules gives 74 pre-selected
(rows 28, 62, 71, 73, 76, 83), loses none and auto-publishes nothing.

- **The label confirms the brand, a stray picture no longer blocks it** (rows 76, 83). When the label reader read
  another brand on a tier-1 candidate (a related product's picture on the same store page), the tier-1 fallback
  was off for the whole product. It now still takes a candidate whose own label prints the target brand
  (`verify.brand_confirmed`); a candidate without that reading stays blocked (the 'Freshly' protection).
- **Tier-2 fallback, `preselected:tier2_corroborated`** (rows 62, 71, 73). An UNSURE reading (most often the net
  size is not legible on the front) on a tier-2 candidate is pre-checked when its label carries the identity
  (brand confirmed, front packshot, no size or variant read as different, the variant read as 'yes' when the
  sheet or the label states one), its listing text states the size (never only the image file name), its own
  listing has no variant doubt or differing barcode, and a second store domain reads the same, or its page is
  trusted (UAE retailer, official, structured). Among several, a trusted page and a sharp picture come first.
  Never auto-published.
- **One unit of a multipack** (rows 15, 50, 51). Stores show one pack of 'MEHRAN PLAIN PARATHA 2X400GM' or one
  can of a 3x185g pack. A reading whose only disagreement is that pack, with the per-unit size printed, is UNSURE
  instead of MISMATCH, and a pick carries the new warning `multipack_unit_image`
  («الصورة لعبوة وحدة، والمنتج باكيت من أكثر من حبة»). '2X400GM 5S' now keeps its 5 pieces.
- **No-brand cells** (rows 95–97). 'GENERIC / NO BRAND', 'N/A', '-', 'بدون ماركة'… are read as an empty brand
  cell: nothing is searched or matched as a brand, and the reason is the new `no_brand`, not "add the brand to
  Brands Mapping". sku_keys do not move.
- **A product word in the brand cell** (rows 27–28). 'AMERICAN LIGHT' before 'MEAT TUNA' is matched as 'AMERICAN'
  ('LIGHT MEAT' is the tuna grade), so a label reading 'American' is the brand. Only unmapped brands, never down
  to a too-short brand; the cell, the key and the display are unchanged. New sheet note `brand_has_product_word`
  (also for 'SQ SALITED', which is not trimmed).
- **Store spellings are kept** (rows 49–52). The search did find 'Super Tasty' for 'SUPER T/' and 'SUP/T', but the
  trace and the export dropped it, so row 49's reason wrongly said no store writes the brand. The trace keeps
  `discovered_brands`, the export and the stored reason read it, and a spelling an earlier row proved writes the
  first queries of a later row with the same or a sibling brand cell.
- **Size unit typo** (row 4). 'BATO FRENCH FRIES 900 MM' raises the sheet note `size_unit_typo` («غالبًا قصدك
  900 GM»); a real cut width ('9MM 1KG') raises nothing.
- `scripts/reroute_export.py`: re-routes a run export offline with the current rules and prints the rows whose
  decision changes (an approximation: recorded tiers, no page titles; no network, no database).
- Eval gate unchanged: 58/58, 58/58, 52/58; preselect precision 100%, 100%, 96.3%; 0 wrong auto-publish.

### Added after phase 4: why a product has no pick, a run export, sheet data quality

The owner's first dashboard run after phase 4 (100 real rows) left 32 products «بلا اقتراح»: the search found
candidates but none was confident enough to pre-select, and the review screen showed an empty panel with no reason.

- Every product without a pick says why in one plain Arabic sentence, with the concrete fact and what to do. The
  causes covered are: label reader unsure, brand on no page, only weak or social listings, conflicting sizes or
  packs, search or label reader down, and sheet gaps (no size, no barcode, brand not in Brands Mapping, a likely
  typo with the suggested spelling). The reason key shows only in a tooltip.
- Such a product opens on its candidate images, best-ranked first and nothing pre-selected, each with its warnings
  and a one-line «لماذا لم تُختر». Enter approves only after an explicit pick (1-9 or a click).
- The bulk card shows the reason, and the review list filters by reason (chips, or a `?reason=` link).
- The worker stores the reason with each result. Rows saved before this release get theirs from stored data the
  first time the review screen opens, without touching `updated_at` and never over a newer result.
- `catalog_match/explain.py` is the shared home of the no-pick reasons; `scripts/smoke_live.py` uses it and its
  output is unchanged.
- `scripts/export_run.py` and the Run page button «تصدير تقرير للتحليل» write one JSON file
  (`laqta_run_<date>_<time>.json`) for the latest run, a run id, or everything awaiting review: rows, decisions,
  reasons, the top 8 candidates with their evidence, cost when known, the git commit and the settings without keys.
  It uses the smoke_live JSON format (compare_runs reads it), redacts every secret, and holds no image or HTML. No
  search is run and nothing is spent.
- The Run page card «جودة بيانات الشيت» counts and lists rows with no size, no barcode, a brand not in Brands
  Mapping, a likely typo (with its correction) or a barcode shared by different products; each row opens in review.
  It reads the cached sheet rows only.
- No search decision changed (eval gate 58/58, 58/58, 52/58; 0 wrong auto-publish).

### Phase 4: reliable automation from the sheet row to the published link

Phase 3 made the search pick the right image more often. Phase 4 makes every later step as careful: the
sheet write, the cut-out, the publish, the queue, the nightly run, the review screen, and a way to replay a live
run offline. Eight packages were built in parallel, each was reviewed adversarially, and every confirmed defect
was fixed with a regression test that fails when the fix is reverted. The eval gate is unchanged throughout
(58/58, 58/58, 52/58 correct picks; 0 wrong auto-publish).

#### Sheet writes (outbox)

- Every queued write has an outcome (`google_sheets.outbox_outcomes`, `outbox_summary`): PENDING, SYNCED,
  SUPERSEDED, CONFLICT, DEAD or SKIPPED_OUT_OF_BOUNDS, per cell, with an outcome hook into the Laravel error log.
- Writes are ordered by a sequence that never goes below the outbox's highest one, so an older value never lands
  over a newer one, also after a Windows clock step back, an identity change (a barcode added later) or a row that
  moved twice.
- A write moves to another row only on a valid barcode or a complete identity (name, size and brand, a blank
  matching only a blank) and never over a different value in the target cell; otherwise it is a reported CONFLICT.
- The row identity includes the brand; forwarding a Redis payload twice inserts nothing new; product fields from
  sheet cells stay on one log line.
- `SheetTransientError` (Google busy or unreachable) is reported as such by every caller; the sync worker
  forwards Redis payloads before it opens the sheet.

#### Image processing and the cut-out check

- Every cut-out goes through a quality check (`assess_cutout`, `ProcessResult.quality_flags`): edge_clipped,
  opaque_backdrop, opaque_fill, alpha_haze, kept_shadow, second_object, too_small_on_canvas, upscaled. A flagged
  cut-out is retried only with what can fix its flag (one table, `_FLAG_REMEDY`), and no paid call is spent on a
  result that can never publish.
- Printed cartons are not photo backdrops; two-packs are one product; several small stray pieces add up to a
  second object; clear bottles stay one object; a thin part cut by the Gemini box is detected and the full frame
  retried; a kept shadow is flagged.
- Upscaling blocks only above 3x; between 2x and 3x the image publishes with the note `upscaled`
  (`ProcessResult.quality_notes`).
- `WHITE_SOURCE_MODE` (off | log | on, default log) uses a source already on white without a paid call; shadows,
  reflections and white product parts make a source ineligible, and log mode analyses a small copy (~0.1 s).
- Uploads are verified (`cloudinary_storage.upload_product_image`: bytes, size and MD5 of what Cloudinary stored);
  the publish-time re-download sends the original fetch's Accept and Referer headers; `PHOTOROOM_CROP` defaults to
  off and no longer doubles PhotoRoom calls.

#### Publishing and approvals

- One publish path (`main.publish_image`) and one processing profile for auto-publish, approvals and manual
  uploads. A per-product lock (`sku_publish_lock`) is held until the approval record and the queue status are
  written: of two reviewers approving at once the second is refused, and the database and the sheet always agree.
- Contracts with the review screen: C1 (an approval carries the state the page saw; `already_approved`,
  `state_changed`, `busy`; `replace` only after an explicit confirmation; an image another reviewer rejected is
  refused and never offered for replacement), C2 (a rejection excludes only the rejected image and keeps the
  product in review while candidates remain; a re-search saves its candidates on the server), C3 (the sheet
  outcome `written | pending | conflict | unknown`, read only from this request's own link writes).
- Products that share a barcode, or a name key with another Arabic variant, are separate products everywhere
  (approvals, rejections, candidates, queue status, sibling rows).
- A rejection voids only the approval of the rejected image (also under the pre-barcode key) and clears its
  Cloudinary link from the sheet; the worker never publishes a pick rejected while it was processed.
- Duplicate images across products are detected by pHash; a colour signature keeps label-colour variants of the
  same bottle from counting as duplicates.
- Human approvals name the cut-out check's flags in Arabic; presentation-only flags can be published after an
  explicit «انشرها رغم ذلك…»; a failed background removal is never published.

#### Queue and scheduling

- New queue columns (`next_attempt_at`, `fail_count`, `down_count`, `reverify_count`, `priority`, `task_kind`,
  `review_only`, `requeue_reason`, `brand_fp`, `alt_sku_key`, `searched_at`) and a batched enqueue.
- NOT_FOUND rows are retried after 3, 7 and 30 days; PROVIDER_DOWN backs off 10 then 20 minutes, then parks for
  12 hours; a Serper 429 backs off instead of stopping the run.
- The enqueue reconciles the sheet: an approved image missing from its row is written again without a search
  (relink), an edited row or a cleared link goes back to review, a lost write is retried, a brand-mapping or
  local-index change reopens the rows it can help.
- Daily budget (`DAILY_BUDGET_USD`) from a spend ledger (`search_spend`) that also counts dashboard searches and
  re-searches after a rejection; a Serper credit stop (`SERPER_CREDIT_STOP_SEARCHES`).
- A reviewer's decision outlives a stale relink or recheck; rows waiting for review stay there when a run stops
  early; per-size failure records are kept, shown and retried for their own row.

#### Nightly run, worker lock and reports

- The worker lock is JSON (pid, host, start time, role, command line) with a heartbeat; a lock whose process
  identity verifies is never aged out, a failed process check never frees it, and the dashboard reads the lock
  state from Python's rule and kills only a confirmed worker.
- Stop asks the worker to stop and waits up to 90 seconds; a run that had to be killed still gets a `stopped`
  report.
- Every run writes a report (`run_report.py`): `run_history` table, `temp/nightly/last_report.json`, a Telegram
  message, and the Health page's «آخر تشغيل» card and nightly log viewer. Report texts are redacted.
- Exit codes: 0 done / skipped / handed over, 1 failed, 2 outage, 3 stopped. Only a database or Google that does
  not answer is an outage and is retried after 15 and 60 minutes; the night stays inside Task Scheduler's time
  limit; a crash is a failure and Ctrl+C a stop.

#### Review screen

- Every candidate shows its own warnings; size or variant that nothing confirmed keeps a pick out of bulk
  approval; «why this image» chips come from the engine's evidence; the preview is labelled as the source before
  background removal.
- A «الخلفية لم تُعزل» chip (`?filter=bg_failed`) for approvals whose cut-out failed; products ordered by
  confidence and grouped by brand; bulk mode keys (arrows, Space, A, Shift+A).

#### Search accuracy

- Sheet names are read the way the stores write them, for parsing and queries only (glued sizes split, compounds
  and typos fixed from `catalog_match/data/sheet_spellings.json`); the sku_key still comes from the raw name.
- A protein variant axis (beef, chicken, mutton, lamb, fish) for luncheon meat, masala, burgers and sausages;
  `mm` is a unit word; Q1 no longer repeats a mapped misspelling; the label reader retries a timeout once.
- A discovered brand spelling is reused within the run and asked of the local index; page recovery never picks
  the failed picture again; new failure code `SOCIAL_ONLY` with the social posts' links.

#### Record and replay

- `scripts/smoke_live.py --record <folder>` records every answer of a live dry run (searches, pages, downloads,
  label readings, local index, spend) into a cassette; `scripts/replay_run.py` replays it offline and compares the
  decisions; `--fill-misses` tops up what a newer version asks.
- No API key, Google engine id (cx) or proxy password is ever stored, wherever an answer echoes it; a failing
  cassette write never changes the live run; a missing or damaged answer is a reported miss; `--strict` fails on
  any miss, crash or refused connection.

#### Fixed in the reviews of the eight packages (before their first live run)

Every package was reviewed adversarially; each confirmed defect below was reproduced first and has a test that
fails without its fix.

- Sheet writes: a write never relocates onto another product's row on a blank brand or size; an older value never
  wins after an identity change or a row that moved twice.
- Cut-out check: good transparent printed cartons, two-packs, tight crops and 300-500 px web images are no longer
  sent to review after paid retries (on a synthetic packshot corpus: transparent PNGs flagged 4/7 -> 0/7, two-packs
  7/10 -> 0/10 with paid calls 23 -> 5, damaged images caught 2/8 -> 8/8).
- Publishing: two simultaneous approvals both succeeding; one product's image written into another product's row
  when they share a barcode; a stale rejection voiding a fresh approval; an approval of an image another reviewer
  had just rejected; a worker publishing a pick rejected while it was processed; an auto-publish whose background
  removal failed leaving its row 'processing'.
- Queue: a stale relink writing back a rejected image; review rows emptied by a recheck or by an early stop; legacy
  PROVIDER_DOWN rows never claimed; one size's failure record deleting or shadowing another's; dashboard searches
  outside the daily budget; the outbox reconcile reading only the newest 500 writes.
- Nightly run and lock: a live worker judged stale after 24 hours and a failed process check freeing a live lock
  (two workers at once); configuration errors retried as outages; Stop killing a run without a report; a crash
  reported as done; the night overrunning Task Scheduler's limit.
- Review screen: a quiet reload moving the approval guard; replace not re-checked on the server; approvals of
  pictures that never rendered or of the next product on a quick second key press; Shift+A taking cards never
  shown; «publish anyway» claiming a background was removed; select_image forwarding every field of the request.
- Search: the alternative key drifting for rows given a barcode; promo codes (B2G1, S4 L) read as sizes; correct
  meat masalas rejected; another product type outranking the right one; a brand's official page losing tier 1; a
  brand spelling proved by one row lent to another without evidence; a multipack's own picture rejected.
- Record and replay: keys and the Google engine id stored through response URLs and large bodies; answers lost on
  Unicode line separators or a missing blob; a crashed row counted as complete; a local proxy letting traffic out.
- Google CSE transport errors no longer print the key and the engine id in the console and the log file.

#### Known limits after phase 4

- Two different products that share one barcode still share one stored approval record (`resolved_products` is
  keyed by sku_key); their sheet rows, candidates and queue rows are kept apart.
- The Windows process probe and the PowerShell launchers are tested with recorded outputs only (no Windows here).
- Approving a picture from a live search on the review screen (no stored candidate) checks earlier rejections by
  its URL only; the search itself already excludes rejected pHashes.

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
