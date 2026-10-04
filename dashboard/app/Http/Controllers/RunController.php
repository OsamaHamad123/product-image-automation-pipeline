<?php

namespace App\Http\Controllers;

use App\Services\PythonBridge;
use App\Services\QueueStats;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\DB;

/**
 * لقطة · التشغيل (the Run page) and the live run data the Home page shares with it.
 *
 * Read-only: starting, pausing, stopping and fixing a run stay in ApiController (runAll, pauseBatch,
 * resumeBatch, stopBatch, resetBatch); this controller only reads.
 *   GET /batch-automation          the page (?tab=review now opens the bulk review on /catalog)
 *   GET /api/run/live              /api/batch-status plus the current (or last) run's summary and latest rows
 *   GET /api/run/plan              «قبل ما تبدأ»: what a new run would search, skip, cost and take
 * Every count comes from QueueStats, the same functions the sidebar badge and /api/batch-status use.
 */
class RunController extends Controller
{
    /** Used when the queue has no finished run to learn from (labelled «تقدير أولي» on the page). */
    public const DEFAULT_SECONDS_PER_PRODUCT = 20.0;
    public const DEFAULT_COST_PER_PRODUCT = 0.003;

    /** Rows shown under «آخر النتائج». */
    public const RECENT_LIMIT = 8;

    public const ACTIVE_PHASES = ['starting', 'running', 'paused', 'stopping'];

    private const HISTORY_CACHE_KEY = 'laqta_run_seconds_per_product_v1';

    public function page(Request $request)
    {
        // The review grid moved to the review page (bulk mode); old links keep working.
        if ($request->query('tab') === 'review') {
            return redirect('/catalog?mode=bulk');
        }
        $live = self::snapshot();
        return view('dashboard.batch_automation', [
            'live' => $live,
            'autoPublish' => self::autoPublishState(),
            'lqReviewCount' => $live['batch']['ready_for_review'] ?? null,
        ]);
    }

    public function live()
    {
        $live = self::snapshot();
        return response()->json($live, ($live['status'] ?? '') === 'success' ? 200 : 503)
            ->header('Cache-Control', 'no-store');
    }

    /** True when the database answers; every live number depends on it. */
    public static function databaseOnline(): bool
    {
        try {
            DB::select('SELECT 1');
            return true;
        } catch (\Throwable $e) {
            return false;
        }
    }

    /**
     * /api/batch-status (the exact payload the sidebar polls), the run to describe, and its latest rows.
     * 'current' is true while that run is the one in progress.
     */
    public static function snapshot(): array
    {
        if (!self::databaseOnline()) {
            return [
                'status' => 'error',
                'error' => 'database_unavailable',
                'message' => 'قاعدة البيانات مش متاحة هلق، فما منقدر نعرف حالة التشغيل. تأكد إنو MariaDB شغّالة.',
                'generated_at' => time(),
            ];
        }
        $batch = app(ApiController::class)->batchStatus()->getData(true);
        $stateRunId = $batch['run']['run_id'] ?? null;
        $runId = QueueStats::latestRunId(is_string($stateRunId) ? $stateRunId : null);
        $rows = QueueStats::runRows($runId);
        $phase = (string) ($batch['phase'] ?? 'idle');
        $run = null;
        if ($runId !== null && is_array($rows)) {
            $run = QueueStats::runSummary($rows, self::prices());
            $run['run_id'] = $runId;
            // false when a newer run has no rows yet (it is starting, or it stopped before its first search):
            // then this summary is the run before it
            $run['is_state_run'] = $runId === $stateRunId;
            $run['current'] = $run['is_state_run'] && in_array($phase, self::ACTIVE_PHASES, true);
        }
        return [
            'status' => 'success',
            'generated_at' => time(),
            'batch' => $batch,
            'run' => $run,
            'run_readable' => $rows !== null,
            'recent' => is_array($rows) ? self::recentRows($rows) : [],
        ];
    }

    /**
     * The prices ops_health reported last (cached by the Health page), with its SerpApi Lens price
     * (source_prices.lens_serpapi, the SERPAPI_LENS_PRICE_USD setting), else the same defaults.
     */
    private static function prices(): ?array
    {
        $cached = Cache::get(HealthController::CACHE_KEY);
        if (!is_array($cached) || !is_array($cached['prices'] ?? null)) {
            return null;
        }
        $prices = $cached['prices'];
        $lens = $cached['source_prices']['lens_serpapi'] ?? null;
        if (is_numeric($lens)) {
            $prices['lens_serpapi'] = (float) $lens;
        }
        return $prices;
    }

    /**
     * «آخر النتائج»: rows of the run that were searched or are being searched, newest first (searching on top).
     * Each row: row, name, kind, label, chip, at (epoch or null), why (secondary text or ''), href.
     */
    public static function recentRows(array $rows, int $limit = self::RECENT_LIMIT, ?int $now = null): array
    {
        $now = $now ?? time();
        $items = [];
        foreach ($rows as $row) {
            $outcome = is_array($row['outcome'] ?? null) ? $row['outcome'] : null;
            $kind = QueueStats::resultKind((string) ($row['status'] ?? ''), $row['failure_code'] ?? null,
                $outcome['decision'] ?? null);
            if ($kind === 'waiting') {
                continue;
            }
            $at = QueueStats::searchedAt($outcome);
            if ($at === null && is_numeric($row['age_s'] ?? null)) {
                $at = $now - max(0, (int) $row['age_s']);
            }
            $code = strtoupper(trim((string) ($row['failure_code'] ?? '')));
            $number = (int) ($row['row_number'] ?? 0);
            $items[] = [
                'row' => $number,
                'name' => (string) ($row['product_name'] ?? ''),
                'kind' => $kind,
                'label' => QueueStats::RESULT_KINDS[$kind]['label'],
                'chip' => QueueStats::RESULT_KINDS[$kind]['chip'],
                'at' => $kind === 'searching' ? null : $at,
                'why' => in_array($kind, ['error', 'not_found', 'requeued', 'none'], true)
                    ? (QueueStats::FAILURE_TEXT[$code] ?? '') : '',
                'href' => '/catalog?row=' . $number,
            ];
        }
        usort($items, function ($a, $b) {
            $sa = $a['kind'] === 'searching' ? 1 : 0;
            $sb = $b['kind'] === 'searching' ? 1 : 0;
            if ($sa !== $sb) {
                return $sb <=> $sa;
            }
            return [($b['at'] ?? 0), $b['row']] <=> [($a['at'] ?? 0), $a['row']];
        });
        return array_slice($items, 0, $limit);
    }

    /**
     * «قبل ما تبدأ». Query: scope=all|brand|rows, brand, rows, force (re-search products with a final image),
     * refresh=1 (read the sheet again, used when a run just ended). 503 with an Arabic message when the
     * sheet or the database cannot be read: the page says so instead of showing zeros.
     */
    public function plan(Request $request)
    {
        $text = fn (string $key) => is_string($request->query($key)) ? $request->query($key) : '';
        $scope = in_array($text('scope'), ['all', 'brand', 'rows'], true) ? $text('scope') : 'all';
        $brand = trim($text('brand'));
        $rowsText = $text('rows');
        $force = $request->boolean('force');

        $error = null;
        $sheet = ProductController::sheetRows($request->boolean('refresh'), $error);
        if ($sheet === null) {
            return response()->json([
                'status' => 'error',
                'error' => 'sheet_unavailable',
                'message' => 'ما قدرنا نقرأ الشيت هلق، فما في تقدير قبل التشغيل. فيك تبدأ عادي، والتشغيل بيقرأ الشيت بنفسه.',
            ], 503)->header('Cache-Control', 'no-store');
        }
        $queue = QueueStats::queueByRow();
        if ($queue === null) {
            return response()->json([
                'status' => 'error',
                'error' => 'database_unavailable',
                'message' => 'قاعدة البيانات مش متاحة هلق، فما منقدر نحسب شو رح ينبحث.',
            ], 503)->header('Cache-Control', 'no-store');
        }

        $plan = self::buildPlan($sheet, $queue, $scope, $brand, $rowsText, $force);
        $history = self::historySecondsPerProduct();
        $costPer = self::costPerProduct(OverviewController::opsHealth());
        $plan['estimate'] = [
            'seconds_per_product' => $history ?? self::DEFAULT_SECONDS_PER_PRODUCT,
            'cost_per_product' => $costPer ?? self::DEFAULT_COST_PER_PRODUCT,
            'time_basis' => $history !== null ? 'history' : 'default',
            'cost_basis' => $costPer !== null ? 'history' : 'default',
            'seconds' => (int) round($plan['total'] * ($history ?? self::DEFAULT_SECONDS_PER_PRODUCT)),
            'cost_usd' => round($plan['total'] * ($costPer ?? self::DEFAULT_COST_PER_PRODUCT), 4),
        ];
        $plan['brands'] = self::brandCounts($sheet);
        $plan['status'] = 'success';
        return response()->json($plan)->header('Cache-Control', 'no-store');
    }

    /**
     * What run_enqueue_mode + begin_run would do for this scope (pure; mirrors main.py):
     * - in scope: the row filter (main.parse_row_filter) or the brand filter (substring, case-insensitive);
     * - skipped: a sheet link (final, or an old needs_review link) unless force;
     * - kept: a queue row of the same product already waiting for review / approved / held by a worker
     *   (local_cache_db.add_to_queue keeps those) unless force;
     * - leftovers: rows still pending in the queue from an earlier run; begin_run gives them to this run too.
     */
    public static function buildPlan(array $sheet, array $queue, string $scope, string $brand, string $rowsText,
                                     bool $force): array
    {
        $out = ['scope' => $scope, 'brand' => $brand, 'row_filter' => '', 'force' => $force, 'error' => '',
                'sheet_total' => count($sheet), 'in_scope' => 0, 'to_search' => 0, 'skipped_final' => 0,
                'skipped_review_link' => 0, 'kept_waiting' => 0, 'kept_approved' => 0, 'leftovers' => 0,
                'total' => 0];
        $ranges = null;
        if ($scope === 'rows') {
            $parsed = QueueStats::parseRowFilter($rowsText);
            $out['row_filter'] = $parsed['text'];
            if ($parsed['error'] !== '' || $parsed['ranges'] === null) {
                $out['error'] = $parsed['error'] !== '' ? $parsed['error'] : 'اكتب الصفوف اللي بدك تشغّلها، مثل 62-101.';
                return $out;
            }
            $ranges = $parsed['ranges'];
        }
        $needle = $scope === 'brand' ? mb_strtolower($brand) : '';
        if ($scope === 'brand' && $needle === '') {
            $out['error'] = 'اختار الماركة اللي بدك تشغّلها.';
            return $out;
        }
        $searched = [];
        foreach ($sheet as $prod) {
            $row = (int) ($prod['row_number'] ?? 0);
            if ($row <= 0 || !QueueStats::inRanges($row, $ranges)) {
                continue;
            }
            if ($needle !== '' && mb_strpos(mb_strtolower((string) ($prod['brand'] ?? '')), $needle) === false) {
                continue;
            }
            $out['in_scope']++;
            $link = trim((string) ($prod['existing_image_link'] ?? ''));
            $reviewLink = !empty($prod['needs_review']) || strpos($link, 'needs_review:') === 0;
            if (($link !== '' || $reviewLink) && !$force) {
                $out[$reviewLink ? 'skipped_review_link' : 'skipped_final']++;
                continue;
            }
            $q = $queue[$row] ?? null;
            if ($q !== null && !$force && self::sameProduct($q, $prod)) {
                if ($q['status'] === 'ready_for_review') {
                    $out['kept_waiting']++;
                    continue;
                }
                if ($q['status'] === 'completed' || ($q['status'] === 'processing' && !empty($q['lease_held']))) {
                    $out['kept_approved']++;
                    continue;
                }
            }
            $out['to_search']++;
            $searched[$row] = true;
        }
        foreach ($queue as $row => $q) {
            $open = $q['status'] === 'pending' || ($q['status'] === 'processing' && empty($q['lease_held']));
            if ($open && !isset($searched[(int) $row])) {
                $out['leftovers']++;
            }
        }
        $out['total'] = $out['to_search'] + $out['leftovers'];
        return $out;
    }

    /** add_to_queue keeps a row only while it holds the same product (same sku_key, or no key yet). */
    private static function sameProduct(array $q, array $prod): bool
    {
        $queued = (string) ($q['sku_key'] ?? '');
        $sheet = (string) ($prod['sku_key'] ?? '');
        return $queued === '' || $sheet === '' || $queued === $sheet;
    }

    /**
     * Brands of the sheet for the brand select, most products first. Each count is what a run with that brand
     * takes: main.py's brand filter is a case-insensitive substring, so «Almarai» also takes «Almarai Kids», and
     * the number next to the name equals the plan's in_scope for it (one number, one rule).
     */
    public static function brandCounts(array $sheet): array
    {
        $brands = [];
        $sheetBrands = [];
        foreach ($sheet as $prod) {
            if ((int) ($prod['row_number'] ?? 0) <= 0) {
                continue;                       // buildPlan skips these too
            }
            $sheetBrands[] = mb_strtolower((string) ($prod['brand'] ?? ''));
            $name = trim((string) ($prod['brand'] ?? ''));
            if ($name !== '') {
                $brands[mb_strtolower($name)] = $brands[mb_strtolower($name)] ?? ['brand' => $name, 'count' => 0];
            }
        }
        foreach ($brands as $needle => &$item) {
            foreach ($sheetBrands as $brand) {
                if (mb_strpos($brand, $needle) !== false) {
                    $item['count']++;
                }
            }
        }
        unset($item);
        $list = array_values($brands);
        usort($list, fn ($a, $b) => [$b['count'], $a['brand']] <=> [$a['count'], $b['brand']]);
        foreach ($list as &$item) {
            $item['label'] = $item['brand'] . ' (' . QueueStats::countText($item['count']) . ')';
        }
        unset($item);
        return $list;
    }

    // ------------------------------------------------------------------
    // «تصدير تقرير للتحليل»: one JSON file of a run (scripts/export_run.py through cli_bridge 'export_run')
    // ------------------------------------------------------------------

    public const EXPORT_SCOPES = ['latest', 'review', 'run'];

    /**
     * GET /api/run/export?scope=latest|review|run[&run_id=]: downloads laqta_run_<date>_<time>.json. Read only: the
     * bridge reads the queue and what it stored (no search, no cost) and hides every configured secret; the file is
     * written in temp/exports and deleted once sent. An Arabic JSON error when it cannot be made.
     */
    public function export(Request $request)
    {
        $scope = in_array($request->query('scope'), self::EXPORT_SCOPES, true) ? (string) $request->query('scope') : 'latest';
        $runId = is_string($request->query('run_id')) ? trim((string) $request->query('run_id')) : '';
        if ($scope === 'run' && !preg_match('/^[A-Za-z0-9_.:-]{1,64}$/', $runId)) {
            return response()->json(['status' => 'error', 'message' => 'رقم التشغيل مش صحيح.'], 422)
                ->header('Cache-Control', 'no-store');
        }
        $result = PythonBridge::run('export_run', ['scope' => $scope, 'run_id' => $runId]);
        $name = (string) ($result['file'] ?? '');
        $path = base_path('../temp/exports/' . $name);
        if (($result['status'] ?? '') !== 'success' || !preg_match('/^laqta_run_[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{4}\.json$/', $name)
            || !is_file($path)) {
            return response()->json([
                'status' => 'error',
                'message' => 'ما قدرنا نجهّز التقرير هلق. تأكد إنو قاعدة البيانات شغّالة وجرّب مرة ثانية.',
            ], 500)->header('Cache-Control', 'no-store');
        }
        return response()->download($path, $name, [
            'Content-Type' => 'application/json; charset=UTF-8',
            'Cache-Control' => 'no-store',
            'X-Laqta-Rows' => (string) (int) ($result['rows'] ?? 0),
        ])->deleteFileAfterSend(true);
    }

    // ------------------------------------------------------------------
    // «جودة بيانات الشيت»: what the sheet rows lack (cli_bridge get_products: sheet_issues, catalog_match.explain)
    // ------------------------------------------------------------------

    /** The card's groups, in the order the owner fixes them, with their Arabic names. */
    public const QUALITY_GROUPS = [
        'no_size' => 'حجم ناقص',
        'no_barcode' => 'باركود ناقص أو مش صالح',
        'brand_unknown' => 'ماركة مش موجودة في Brands Mapping',
        'typo' => 'غلطة إملائية محتملة بالاسم',
        'duplicate_barcode' => 'باركود مكرر لمنتجات مختلفة',
    ];

    /** Rows listed per group (the count is always the full number). */
    public const QUALITY_ROWS = 200;

    /**
     * GET /api/run/sheet-quality: from the sheet rows already cached (never a Google read on page load); refresh=1
     * reads them again through the bridge (its own products cache, else the sheet). not_loaded: no cached rows yet;
     * stale: rows cached by a version that did not check them.
     */
    public function sheetQuality(Request $request)
    {
        $error = null;
        $rows = $request->boolean('refresh') ? ProductController::sheetRows(true, $error) : self::cachedSheetRows();
        if ($rows === null) {
            return response()->json($request->boolean('refresh')
                ? ['status' => 'error', 'message' => 'ما قدرنا نقرأ الشيت هلق. جرّب بعد شوي.']
                : ['status' => 'not_loaded'], $request->boolean('refresh') ? 503 : 200)->header('Cache-Control', 'no-store');
        }
        return response()->json(self::sheetQualitySummary($rows))->header('Cache-Control', 'no-store');
    }

    /**
     * The sheet rows ProductController::sheetRows cached, while products_cache.json (python's own cache of the sheet,
     * deleted after every sheet write) is still the file they were read from; null otherwise. Never calls the bridge
     * or Google: the same check sheetRows makes before serving its cache.
     */
    public static function cachedSheetRows(): ?array
    {
        $path = base_path('../products_cache.json');
        clearstatcache(true, $path);
        if (!is_file($path)) {
            return null;
        }
        $cached = Cache::get(ProductController::SHEET_ROWS_CACHE_KEY);
        $stamp = filemtime($path) . ':' . filesize($path);
        return is_array($cached) && ($cached['stamp'] ?? null) === $stamp && is_array($cached['rows'] ?? null)
            ? $cached['rows'] : null;
    }

    /**
     * {status, total, checked, groups: [{key, label, count, rows: [{row, name, text, href}], brands?}]} from the
     * sheet rows' sheet_issues; status 'stale' when no row was checked (rows cached before this version).
     */
    public static function sheetQualitySummary(array $sheet, int $limit = self::QUALITY_ROWS): array
    {
        $groups = [];
        foreach (self::QUALITY_GROUPS as $key => $label) {
            $groups[$key] = ['key' => $key, 'label' => $label, 'count' => 0, 'rows' => []];
        }
        $brands = [];
        $checked = 0;
        foreach ($sheet as $prod) {
            if (!is_array($prod) || !is_array($prod['sheet_issues'] ?? null)) {
                continue;
            }
            $checked++;
            $row = (int) ($prod['row_number'] ?? 0);
            $seen = [];
            foreach ($prod['sheet_issues'] as $issue) {
                $key = is_array($issue) ? (string) ($issue['key'] ?? '') : '';
                if (!isset($groups[$key])) {
                    continue;
                }
                if ($key === 'brand_unknown' && empty($issue['empty'])) {
                    $brand = trim((string) ($issue['brand'] ?? ''));
                    $brands[$brand] = ($brands[$brand] ?? 0) + 1;
                }
                $text = mb_substr(trim((string) ($issue['text'] ?? '')), 0, 300);
                if (isset($seen[$key])) {
                    // one line per row and group: a second typo joins the first
                    $i = $seen[$key];
                    if ($i !== null && $text !== '') {
                        $groups[$key]['rows'][$i]['text'] .= '، ' . $text;
                    }
                    continue;
                }
                $groups[$key]['count']++;
                $seen[$key] = null;
                if (count($groups[$key]['rows']) < $limit) {
                    $groups[$key]['rows'][] = ['row' => $row, 'name' => (string) ($prod['product_name'] ?? ''),
                                               'text' => $text, 'href' => '/catalog?row=' . $row];
                    $seen[$key] = count($groups[$key]['rows']) - 1;
                }
            }
        }
        if ($brands) {
            arsort($brands);
            $groups['brand_unknown']['brands'] = array_map(fn ($b, $n) => ['brand' => $b, 'count' => $n],
                array_keys($brands), array_values($brands));
        }
        return [
            'status' => $checked ? 'success' : 'stale',
            'total' => count($sheet),
            'checked' => $checked,
            'groups' => array_values($groups),
        ];
    }

    /** Cost of one search over the last 7 days (ops_health), or null with too little history. */
    public static function costPerProduct(?array $opsHealth): ?float
    {
        $week = $opsHealth['windows']['7d'] ?? null;
        $searches = (int) ($week['searches'] ?? 0);
        $total = $week['cost_usd']['total'] ?? null;
        if (!is_array($week) || $searches < 3 || !is_numeric($total)) {
            return null;
        }
        return round((float) $total / $searches, 5);
    }

    /**
     * Seconds per product from finished runs in the queue (outcome.searched_at of each run's rows):
     * the median over runs with at least 3 searched rows of (last - first) / (rows - 1). Null without history.
     */
    public static function historySecondsPerProduct(): ?float
    {
        $cached = Cache::get(self::HISTORY_CACHE_KEY);
        if (is_array($cached)) {
            return $cached['value'];
        }
        try {
            $rows = DB::table('automation_queue')
                ->whereNotNull('run_id')
                ->whereIn('status', QueueStats::DONE_STATUSES)
                ->whereNotNull('trace_json')
                ->select('run_id', DB::raw("JSON_UNQUOTE(JSON_EXTRACT(trace_json, '$.outcome.searched_at')) AS searched_at"))
                ->orderByDesc('id')
                ->limit(5000)
                ->get();
        } catch (\Throwable $e) {
            return null;
        }
        $byRun = [];
        foreach ($rows as $r) {
            $at = QueueStats::searchedAt(['searched_at' => $r->searched_at]);
            if ($at !== null) {
                $byRun[(string) $r->run_id][] = $at;
            }
        }
        $value = self::medianRate($byRun);
        Cache::put(self::HISTORY_CACHE_KEY, ['value' => $value], 300);
        return $value;
    }

    /** Median of (last - first) / (n - 1) over runs with at least 3 timestamps and a positive span. */
    public static function medianRate(array $timesByRun): ?float
    {
        $rates = [];
        foreach ($timesByRun as $times) {
            $times = array_values(array_filter((array) $times, 'is_numeric'));
            if (count($times) < 3) {
                continue;
            }
            $span = max($times) - min($times);
            if ($span > 0) {
                $rates[] = $span / (count($times) - 1);
            }
        }
        if (!$rates) {
            return null;
        }
        sort($rates);
        $mid = intdiv(count($rates), 2);
        $median = count($rates) % 2 ? $rates[$mid] : ($rates[$mid - 1] + $rates[$mid]) / 2;
        return round($median, 1);
    }

    /**
     * Auto-publish as the worker will read it (system_settings, the same keys the settings page saves):
     * enabled, the allowed brands, and one plain sentence. 'known' is false when the settings cannot be read.
     */
    public static function autoPublishState(): array
    {
        try {
            $rows = DB::table('system_settings')->whereIn('key', ['auto_publish_enabled', 'auto_publish_brands'])
                ->pluck('value', 'key')->all();
        } catch (\Throwable $e) {
            return ['known' => false, 'enabled' => false, 'brands' => [],
                    'text' => 'ما قدرنا نقرأ إعداد النشر الآلي هلق.'];
        }
        $enabled = in_array(strtolower(trim((string) ($rows['auto_publish_enabled'] ?? ''))), ['1', 'true', 'yes', 'on'], true);
        $brands = array_values(array_filter(array_map('trim', explode(',', (string) ($rows['auto_publish_brands'] ?? ''))),
            fn ($b) => $b !== ''));
        return ['known' => true, 'enabled' => $enabled, 'brands' => $brands,
                'text' => self::autoPublishText($enabled, $brands)];
    }

    public static function autoPublishText(bool $enabled, array $brands): string
    {
        if (!$enabled) {
            return 'النشر الآلي مطفأ: كل النتائج بتستنى مراجعتك قبل ما توصل الشيت.';
        }
        if (!$brands) {
            return 'النشر الآلي مفعّل بس ما في ولا ماركة مسموحة، فكل النتائج بتستنى مراجعتك.';
        }
        if (in_array('*', $brands, true)) {
            return 'النشر الآلي شغّال لكل الماركات: الاقتراح المؤكد تماماً بينزل عالشيت لحاله، والباقي بيستنى مراجعتك.';
        }
        $names = array_map(fn ($b) => stripos($b, 'category:') === 0 ? 'فئة ' . trim(substr($b, 9)) : $b, $brands);
        $shown = implode('، ', array_slice($names, 0, 3)) . (count($names) > 3 ? ' و' . (count($names) - 3) . ' غيرها' : '');
        return 'النشر الآلي شغّال لـ ' . $shown . ': الاقتراح المؤكد تماماً لهدول بينزل عالشيت لحاله، والباقي بيستنى مراجعتك.';
    }
}
