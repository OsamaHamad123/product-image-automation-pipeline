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
            'sheetTab' => self::sheetTab(),
            'lqReviewCount' => $live['batch']['ready_for_review'] ?? null,
        ]);
    }

    // ------------------------------------------------------------------
    // «التشغيل بيقرأ من تبويب «X»»: with no SPREADSHEET_TAB_NAME the run reads and writes the FIRST tab, and on
    // 2026-10-05 a proposals tab another tool created («منتجات جديدة مقترحة 2») had become the first tab. The page never
    // asks Google: the configured name, else the tab of the last sheet read (ProductController::sheetRows cache) or of the
    // last «فحص النشر», else «أول تبويب بالشيت».
    // ------------------------------------------------------------------

    /** نفس publish_check._BACKUP_* (اسم نسخة احتياطية أو تبويب اقتراحات). */
    public const TAB_WARN_PREFIXES = ['backup', 'copy', 'archive', 'propos', 'suggest'];
    public const TAB_WARN_WORDS = ['old', 'bak'];
    public const TAB_WARN_PHRASES = ['new products', 'copy of'];
    public const TAB_WARN_AR = ['نسخة', 'نسخه', 'احتياط', 'قديم', 'أرشيف', 'ارشيف', 'مقترح', 'اقتراح'];

    /** {title, configured, text, suspicious}: السطر اللي بيقول من أي تبويب بيقرأ التشغيل. */
    public static function sheetTab(?string $configured = null, ?string $known = null): array
    {
        $configured = trim($configured ?? (string) (SettingsController::envValues(['SPREADSHEET_TAB_NAME'])['SPREADSHEET_TAB_NAME'] ?? ''));
        $title = $configured !== '' ? $configured : trim($known ?? (string) self::knownFirstTab());
        if ($title === '') {
            return ['title' => '', 'configured' => false, 'text' => 'التشغيل بيقرأ من أول تبويب بالشيت', 'suspicious' => false];
        }
        return ['title' => $title, 'configured' => $configured !== '',
                'text' => 'التشغيل بيقرأ من تبويب «' . $title . '»' . ($configured !== '' ? '' : ' (أول تبويب بالشيت)'),
                'suspicious' => self::looksLikeBackupTab($title)];
    }

    /** اسم أول تبويب كما عرفناه بلا طلب: كاش آخر قراءة للشيت، وإلا آخر «فحص النشر»؛ وإلا null. */
    public static function knownFirstTab(): ?string
    {
        try {
            $cached = Cache::get(ProductController::SHEET_ROWS_CACHE_KEY);
            if (is_array($cached) && is_string($cached['tab'] ?? null) && trim($cached['tab']) !== '') {
                return trim($cached['tab']);
            }
        } catch (\Throwable $e) {
        }
        $check = HealthController::lastPublishCheck();
        $tab = is_array($check) && is_scalar($check['sheet_tab'] ?? null) ? trim((string) $check['sheet_tab']) : '';
        return $tab !== '' ? $tab : null;
    }

    /** publish_check._looks_like_backup بنفس القواعد (بلا حالة أحرف). */
    public static function looksLikeBackupTab(string $title): bool
    {
        $text = class_exists(\Normalizer::class) ? (string) \Normalizer::normalize($title, \Normalizer::FORM_KC) : $title;
        $text = trim((string) preg_replace('/[^\p{L}\p{N}]+/u', ' ', mb_strtolower($text, 'UTF-8')));
        $words = $text === '' ? [] : explode(' ', $text);
        foreach ($words as $word) {
            if (in_array($word, self::TAB_WARN_WORDS, true)) {
                return true;
            }
            foreach (self::TAB_WARN_PREFIXES as $prefix) {
                if (str_starts_with($word, $prefix)) {
                    return true;
                }
            }
        }
        foreach (array_merge(self::TAB_WARN_PHRASES, self::TAB_WARN_AR) as $part) {
            if (str_contains($text, $part) || str_contains($title, $part)) {
                return true;
            }
        }
        return false;
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
    // «جودة بيانات الشيت»: what the sheet rows lack (each cached row's sheet_issues, from catalog_match.explain)
    // ------------------------------------------------------------------

    /**
     * The card's groups, in the order the owner fixes them, with their Arabic names. 'no_brand' (a brand cell that
     * says the product has none, 'GENERIC / NO BRAND') is the owner's own answer, not a gap: it has no group.
     */
    public const QUALITY_GROUPS = [
        'no_size' => 'حجم ناقص',
        'size_unit_typo' => 'وحدة الحجم غلط (MM بدل GM)',
        'no_barcode' => 'باركود ناقص أو مش صالح',
        'brand_unknown' => 'ماركة مش موجودة في Brands Mapping',
        'brand_has_product_word' => 'كلمة من اسم المنتج بعمود الماركة',
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

    // ------------------------------------------------------------------
    // «ماركات ناقصة من Brands Mapping»: the brands the queue names that the sheet has no entry for
    // ------------------------------------------------------------------

    public const BRAND_MAX_CHARS = 100;
    public const BRAND_SYNONYM_MAX = 10;
    public const BRAND_SYNONYM_CHARS = 80;
    public const BRAND_DOMAINS_MAX = 3;
    public const BRAND_ADD_ALL_MAX = 200;
    public const BRAND_ADDED_TEXT = 'انضافت الماركة. التشغيل الجاي بيعرفها.';
    private const BRAND_DOMAIN_PATTERN = '/^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59})$/';

    /** What each refusal of the bridge or of this controller says (code => Arabic text, Levantine). */
    public const BRAND_ERRORS = [
        'invalid_brand' => 'اسم الماركة مش صالح: اكتبه بسطر واحد (لحد 100 حرف) وبدون «بدون ماركة».',
        'invalid_synonym' => 'مرادف مش صالح: كل مرادف لحد 80 حرف وبدون فاصلة جواته.',
        'too_many_synonyms' => 'أكثر شي 10 مرادفات للماركة.',
        'invalid_domain' => 'الموقع لازم يكون اسم نطاق بس (متل almarai.com) بدون http ولا مسار.',
        'blocked_domain' => 'هاد متجر أو موقع تواصل، مش موقع الماركة الرسمي.',
        'too_many_brands' => 'عدد الماركات مش صحيح: من وحدة لحد 200 بالمرة.',
        'duplicate' => 'هالماركة موجودة أصلاً بـ Brands Mapping.',
    ];

    /**
     * GET /api/run/brand-suggestions: {status, count, rows, brands: [{brand, rows, brand_ar, synonyms, official_domain}]}.
     * Read only: the bridge (brand_suggestions) reads the queue and the Brands Mapping sheet; no search, no cost, no write.
     */
    public function brandSuggestions(): \Illuminate\Http\JsonResponse
    {
        if (!self::databaseOnline()) {
            return response()->json(['status' => 'unavailable', 'message' => 'قاعدة البيانات مش شغّالة هلق.'], 503)
                ->header('Cache-Control', 'no-store');
        }
        $result = PythonBridge::run('brand_suggestions');
        if (($result['status'] ?? '') !== 'success' || !is_array($result['brands'] ?? null)) {
            return response()->json(['status' => 'error', 'message' => 'ما قدرنا نقرأ الماركات الناقصة هلق. جرّب بعد شوي.'], 502)
                ->header('Cache-Control', 'no-store');
        }
        $brands = [];
        foreach ($result['brands'] as $b) {
            $name = is_array($b) && is_string($b['brand'] ?? null) ? trim($b['brand']) : '';
            if ($name === '') {
                continue;
            }
            $synonyms = array_values(array_filter(array_map(
                fn ($x) => is_string($x) ? trim($x) : '', is_array($b['synonyms'] ?? null) ? $b['synonyms'] : []
            ), fn ($x) => $x !== ''));
            $brands[] = [
                'brand' => $name,
                'rows' => max(0, (int) ($b['rows'] ?? 0)),
                'brand_ar' => is_string($b['brand_ar'] ?? null) ? trim($b['brand_ar']) : '',
                'synonyms' => array_slice($synonyms, 0, self::BRAND_SYNONYM_MAX),
                'official_domain' => '',
            ];
        }
        return response()->json(['status' => 'success', 'count' => count($brands), 'rows' => (int) ($result['rows'] ?? 0),
                                 'brands' => $brands])->header('Cache-Control', 'no-store');
    }

    /**
     * POST /api/run/brand-official-site {brand}: «اقترح الموقع الرسمي». ONE Serper web query (cli_bridge
     * brand_official_site, recorded in the spend ledger) and up to two sites that are not a store or a social network.
     * Writes nothing; CSRF like every POST.
     */
    public function brandOfficialSite(Request $request): \Illuminate\Http\JsonResponse
    {
        $brand = self::cleanBrandName($request->input('brand'));
        if ($brand === null) {
            return self::brandError('invalid_brand', 422);
        }
        $result = PythonBridge::run('brand_official_site', ['brand' => $brand]);
        $status = (string) ($result['status'] ?? '');
        if ($status === 'invalid') {
            return self::brandError((string) ($result['code'] ?? 'invalid_brand'), 422);
        }
        if ($status === 'unavailable') {
            return response()->json(['status' => 'unavailable', 'message' => 'ما في مفتاح بحث (Serper) مضبوط بالإعدادات.'], 503)
                ->header('Cache-Control', 'no-store');
        }
        if ($status !== 'success' || !is_array($result['candidates'] ?? null)) {
            return response()->json(['status' => 'error', 'message' => 'ما قدرنا نبحث هلق. ما انحسب شي عليك. جرّب بعد شوي.'], 502)
                ->header('Cache-Control', 'no-store');
        }
        $candidates = [];
        foreach (array_slice($result['candidates'], 0, 2) as $c) {
            $domain = is_array($c) && is_string($c['domain'] ?? null) ? strtolower(trim($c['domain'])) : '';
            if (!preg_match(self::BRAND_DOMAIN_PATTERN, $domain)) {
                continue;
            }
            $candidates[] = ['title' => is_string($c['title'] ?? null) ? mb_substr(trim($c['title']), 0, 200) : '', 'domain' => $domain];
        }
        return response()->json([
            'status' => 'success',
            'brand' => $brand,
            'candidates' => $candidates,
            'message' => $candidates ? '' : 'ما لقينا موقع رسمي واضح. اكتبه بإيدك إذا بتعرفه.',
        ])->header('Cache-Control', 'no-store');
    }

    /**
     * POST /api/run/brand-add {brand, synonyms[], official_domains[]}: يضيف صف واحد لورقة Brands Mapping (cli_bridge
     * brand_add) بعد ما نتحقق هون: اسم غير فاضي، لحد 10 مرادفات، وكل موقع اسم نطاق بس. يكتب شيت المالك: بس من زر صريح.
     * CSRF متل كل POST.
     */
    public function brandAdd(Request $request): \Illuminate\Http\JsonResponse
    {
        $item = self::brandItem($request->input('brand'), $request->input('synonyms'), $request->input('official_domains'));
        if (is_string($item)) {
            return self::brandError($item, 422);
        }
        return self::brandAdded(PythonBridge::run('brand_add', $item));
    }

    /**
     * POST /api/run/brand-add-all {items: [{brand, synonyms[]}]}: «أضف الكل بدون مواقع». صف لكل ماركة بالمرادفات بس
     * (أي موقع بالطلب بيتجاهل)، بطلب كتابة واحد. الماركة اللي صارت موجودة بتتخطى.
     */
    public function brandAddAll(Request $request): \Illuminate\Http\JsonResponse
    {
        $items = $request->input('items');
        if (!is_array($items) || count($items) < 1 || count($items) > self::BRAND_ADD_ALL_MAX) {
            return self::brandError('too_many_brands', 422);
        }
        $clean = [];
        foreach ($items as $entry) {
            $entry = is_array($entry) ? $entry : [];
            $item = self::brandItem($entry['brand'] ?? null, $entry['synonyms'] ?? null, []);
            if (is_string($item)) {
                return self::brandError($item, 422);
            }
            $clean[] = $item;
        }
        return self::brandAdded(PythonBridge::run('brand_add', ['items' => $clean]));
    }

    /** One brand request, checked: the bridge's {brand, synonyms, official_domains}, or the error code as a string. */
    public static function brandItem($brand, $synonyms, $domains)
    {
        $name = self::cleanBrandName($brand);
        if ($name === null) {
            return 'invalid_brand';
        }
        $syn = [];
        foreach (self::splitList($synonyms) as $text) {
            $text = trim(preg_replace('/\s+/u', ' ', $text));
            if ($text === '') {
                continue;
            }
            if (mb_strlen($text) > self::BRAND_SYNONYM_CHARS || preg_match('/[\x00-\x1F\x7F]/u', $text)) {
                return 'invalid_synonym';
            }
            $syn[mb_strtolower($text)] ??= $text;      // the first spelling of a repeated one stays
        }
        unset($syn[mb_strtolower($name)]);
        if (count($syn) > self::BRAND_SYNONYM_MAX) {
            return 'too_many_synonyms';
        }
        $hosts = [];
        foreach (self::splitList($domains) as $text) {
            $host = strtolower(trim($text));
            if ($host === '') {
                continue;
            }
            if (!preg_match(self::BRAND_DOMAIN_PATTERN, $host)) {
                return 'invalid_domain';
            }
            $hosts[$host] = $host;
        }
        if (count($hosts) > self::BRAND_DOMAINS_MAX) {
            return 'invalid_domain';
        }
        return ['brand' => $name, 'synonyms' => array_values($syn), 'official_domains' => array_values($hosts)];
    }

    /** The brand cell: one line of 1..100 characters with no control character; null otherwise. */
    public static function cleanBrandName($value): ?string
    {
        if (!is_string($value)) {
            return null;
        }
        $name = trim(preg_replace('/\s+/u', ' ', $value));
        if ($name === '' || mb_strlen($name) > self::BRAND_MAX_CHARS || preg_match('/[\x00-\x1F\x7F]/u', $name)) {
            return null;
        }
        return $name;
    }

    /** A list given as an array or as text split on , ، ; and new lines; never anything but strings. */
    private static function splitList($value): array
    {
        if (is_string($value)) {
            $value = preg_split('/[,\x{060C};\r\n]+/u', $value) ?: [];
        }
        return is_array($value) ? array_values(array_filter($value, 'is_string')) : [];
    }

    private static function brandError(string $code, int $http): \Illuminate\Http\JsonResponse
    {
        return response()->json([
            'status' => 'error',
            'code' => $code,
            'message' => self::BRAND_ERRORS[$code] ?? 'الطلب مش صالح.',
        ], $http)->header('Cache-Control', 'no-store');
    }

    /** The answer to an add: the bridge's result as the page's JSON; the cached sheet rows are dropped on success. */
    private static function brandAdded(array $result): \Illuminate\Http\JsonResponse
    {
        $status = (string) ($result['status'] ?? '');
        if ($status === 'success' && is_array($result['added'] ?? null)) {
            ProductController::forgetProductCaches();
            return response()->json([
                'status' => 'success',
                'added' => array_values(array_filter($result['added'], 'is_string')),
                'skipped' => is_array($result['skipped'] ?? null) ? count($result['skipped']) : 0,
                'harvest_queued' => is_array($result['harvest_queued'] ?? null) ? count($result['harvest_queued']) : 0,
                'message' => self::BRAND_ADDED_TEXT,
            ])->header('Cache-Control', 'no-store');
        }
        if ($status === 'duplicate') {
            return self::brandError('duplicate', 409);
        }
        if ($status === 'invalid') {
            return self::brandError((string) ($result['code'] ?? ''), 422);
        }
        return response()->json(['status' => 'error', 'message' => 'ما قدرنا نكتب بورقة Brands Mapping هلق. ما انكتب شي. جرّب بعد شوي.'], 502)
            ->header('Cache-Control', 'no-store');
    }

    // ------------------------------------------------------------------
    // «عبّي جدول الماركات»: one proposal for every missing brand, from evidence only (cli_bridge brand_bulk_*)
    // ------------------------------------------------------------------

    public const BRAND_BULK_MAX = 500;
    public const BRAND_CONFIDENCES = ['high', 'low', 'none'];
    public const BRAND_NO_EVIDENCE_TEXT = 'ما لقينا دليل كافي';
    /** Why a ticked brand was not written (cli_bridge brand_bulk_add skipped[].reason), in plain Levantine. */
    public const BRAND_BULK_SKIP_TEXTS = [
        'no_evidence' => 'ما إلها دليل كافي',
        'gone' => 'صارت موجودة بـ Brands Mapping أو ما عادت بالشيت',
        'duplicate' => 'موجودة أصلاً بـ Brands Mapping',
        'invalid' => 'اقتراحها مش صالح للكتابة',
        'repeated' => 'مكررة بالطلب',
    ];
    public const BRAND_UNDO_ERRORS = [
        'not_ours' => 'هالماركة ما ضافها المساعد، فما منشيلها. شيلها بإيدك من الشيت إذا بدك.',
        'changed' => 'صف الماركة تغيّر بالشيت بعد ما كتبناه، فما شلناه. شيله بإيدك إذا بدك.',
        'missing' => 'الماركة مش موجودة بـ Brands Mapping أصلاً.',
        'invalid_brand' => 'اسم الماركة مش صالح.',
    ];

    /**
     * GET /api/run/brand-bulk: «عبّي جدول الماركات». {status, count, counts: {high, low, none}, sites_left, site_cap,
     * index, brands: [{brand, rows, brand_ar, synonyms, official_domain, confidence, evidence: [{kind, text}],
     * selectable, checked}]}. Read only (cli_bridge brand_bulk_suggestions): no search, no cost, no write; the official
     * site comes from the cache of an earlier search only.
     */
    public function brandBulk(): \Illuminate\Http\JsonResponse
    {
        if (!self::databaseOnline()) {
            return response()->json(['status' => 'unavailable', 'message' => 'قاعدة البيانات مش شغّالة هلق.'], 503)
                ->header('Cache-Control', 'no-store');
        }
        $result = PythonBridge::run('brand_bulk_suggestions');
        if (($result['status'] ?? '') !== 'success' || !is_array($result['brands'] ?? null)) {
            return response()->json(['status' => 'error', 'message' => 'ما قدرنا نجهّز اقتراحات الماركات هلق. جرّب بعد شوي.'], 502)
                ->header('Cache-Control', 'no-store');
        }
        $brands = array_values(array_filter(array_map([self::class, 'bulkBrand'], $result['brands'])));
        $counts = ['high' => 0, 'low' => 0, 'none' => 0];
        foreach ($brands as $b) {
            $counts[$b['confidence']]++;
        }
        return response()->json([
            'status' => 'success',
            'count' => count($brands),
            'counts' => $counts,
            'sites_left' => max(0, (int) ($result['sites_left'] ?? 0)),
            'site_cap' => max(0, (int) ($result['site_cap'] ?? 0)),
            'index' => (bool) ($result['index'] ?? false),
            'brands' => $brands,
        ])->header('Cache-Control', 'no-store');
    }

    /** One proposal as the page may show it, or null; a brand without evidence is never selectable nor ticked. */
    public static function bulkBrand($b): ?array
    {
        $name = is_array($b) ? self::cleanBrandName($b['brand'] ?? null) : null;
        if ($name === null) {
            return null;
        }
        $confidence = in_array($b['confidence'] ?? null, self::BRAND_CONFIDENCES, true) ? $b['confidence'] : 'none';
        $synonyms = array_values(array_filter(array_map(
            fn ($x) => is_string($x) ? mb_substr(trim($x), 0, self::BRAND_SYNONYM_CHARS) : '',
            is_array($b['synonyms'] ?? null) ? $b['synonyms'] : []
        ), fn ($x) => $x !== ''));
        $domain = is_string($b['official_domain'] ?? null) ? strtolower(trim($b['official_domain'])) : '';
        $evidence = [];
        foreach (is_array($b['evidence'] ?? null) ? $b['evidence'] : [] as $e) {
            $text = is_array($e) && is_string($e['text'] ?? null) ? mb_substr(trim($e['text']), 0, 300) : '';
            if ($text !== '') {
                $evidence[] = ['kind' => is_string($e['kind'] ?? null) ? $e['kind'] : '', 'text' => $text];
            }
        }
        if ($confidence === 'none' || $evidence === []) {
            $confidence = 'none';
            $evidence = [['kind' => 'none', 'text' => self::BRAND_NO_EVIDENCE_TEXT]];
        }
        return [
            'brand' => $name,
            'rows' => max(0, (int) ($b['rows'] ?? 0)),
            'brand_ar' => is_string($b['brand_ar'] ?? null) ? trim($b['brand_ar']) : '',
            'synonyms' => array_slice($synonyms, 0, self::BRAND_SYNONYM_MAX),
            'official_domain' => preg_match(self::BRAND_DOMAIN_PATTERN, $domain) ? $domain : '',
            'confidence' => $confidence,
            'evidence' => $evidence,
            'selectable' => $confidence !== 'none',
            'checked' => $confidence === 'high',
        ];
    }

    /**
     * POST /api/run/brand-bulk-sites: «دوّر عالمواقع الرسمية». Up to site_cap brands never searched before, ONE Serper
     * query each (cli_bridge brand_bulk_sites, recorded in the spend ledger), cached; writes nothing to the sheet.
     */
    public function brandBulkSites(): \Illuminate\Http\JsonResponse
    {
        $result = PythonBridge::run('brand_bulk_sites');
        $status = (string) ($result['status'] ?? '');
        if ($status === 'unavailable') {
            return response()->json(['status' => 'unavailable', 'message' => 'ما في مفتاح بحث (Serper) مضبوط بالإعدادات.'], 503)
                ->header('Cache-Control', 'no-store');
        }
        if ($status !== 'success') {
            return response()->json(['status' => 'error', 'message' => 'ما قدرنا نبحث هلق. جرّب بعد شوي.'], 502)
                ->header('Cache-Control', 'no-store');
        }
        $searched = max(0, (int) ($result['searched'] ?? 0));
        $found = max(0, (int) ($result['found'] ?? 0));
        $left = max(0, (int) ($result['left'] ?? 0));
        $message = $searched === 0
            ? 'ما في ماركات لسا ما دوّرنا على موقعها.'
            : 'دوّرنا على موقع ' . $searched . ' ماركة (' . $searched . ' بحث)، ولقينا موقع واضح لـ ' . $found . '.'
                . ($left > 0 ? ' ضل ' . $left . ': اضغط مرة تانية إذا بدك.' : '');
        return response()->json(['status' => 'success', 'searched' => $searched, 'found' => $found, 'left' => $left,
                                 'message' => $message])->header('Cache-Control', 'no-store');
    }

    /**
     * POST /api/run/brand-bulk-add {brands: [name]}: «اعتمد المحدد». Only the names go to the bridge
     * (brand_bulk_add), which writes each one's own proposal (never a brand without evidence) in batches, skips what
     * is already there and logs what it wrote for «تراجع». Writes the owner's sheet: only from the click. CSRF.
     */
    public function brandBulkAdd(Request $request): \Illuminate\Http\JsonResponse
    {
        $brands = $request->input('brands');
        if (!is_array($brands) || count($brands) < 1 || count($brands) > self::BRAND_BULK_MAX) {
            return response()->json(['status' => 'error', 'code' => 'too_many_brands',
                                     'message' => 'اختار ماركة وحدة عالأقل، ولحد ' . self::BRAND_BULK_MAX . ' بالمرة.'], 422)
                ->header('Cache-Control', 'no-store');
        }
        $clean = [];
        foreach ($brands as $b) {
            $name = self::cleanBrandName($b);
            if ($name === null) {
                return self::brandError('invalid_brand', 422);
            }
            $clean[mb_strtolower($name)] ??= $name;
        }
        $result = PythonBridge::run('brand_bulk_add', ['brands' => array_values($clean)]);
        $status = (string) ($result['status'] ?? '');
        $added = is_array($result['added'] ?? null) ? array_values(array_filter($result['added'], 'is_string')) : [];
        if ($added !== []) {
            ProductController::forgetProductCaches();
        }
        if ($status === 'invalid') {
            return self::brandError((string) ($result['code'] ?? ''), 422);
        }
        if ($status !== 'success') {
            $message = 'ما قدرنا نكتب بورقة Brands Mapping هلق. جرّب بعد شوي.';
            if ($added !== []) {
                $message .= ' انكتبت ' . count($added) . ' قبل ما يوقف، والباقي ما انكتب.';
            }
            return response()->json(['status' => 'error', 'added' => $added, 'message' => $message], 502)
                ->header('Cache-Control', 'no-store');
        }
        $skipped = [];
        foreach (is_array($result['skipped'] ?? null) ? $result['skipped'] : [] as $s) {
            $reason = is_array($s) && is_string($s['reason'] ?? null) ? $s['reason'] : '';
            $name = is_array($s) && is_string($s['brand'] ?? null) ? trim($s['brand']) : '';
            if ($name !== '') {
                $skipped[] = ['brand' => $name, 'reason' => self::BRAND_BULK_SKIP_TEXTS[$reason] ?? 'ما انكتبت'];
            }
        }
        $message = $added === [] ? 'ما انكتب شي جديد.' : 'انضافت ' . count($added) . ' ماركة لـ Brands Mapping. التشغيل الجاي بيعرفها.';
        if ($skipped !== []) {
            $message .= ' وما انكتبت ' . count($skipped) . '.';
        }
        return response()->json(['status' => 'success', 'added' => $added, 'skipped' => $skipped,
                                 'harvest_queued' => is_array($result['harvest_queued'] ?? null) ? count($result['harvest_queued']) : 0,
                                 'message' => $message])->header('Cache-Control', 'no-store');
    }

    /**
     * POST /api/run/brand-undo {brand}: «تراجع» for a brand «عبّي جدول الماركات» wrote: its row leaves Brands Mapping
     * only while it is exactly what the assistant wrote (cli_bridge brand_undo). CSRF.
     */
    public function brandUndo(Request $request): \Illuminate\Http\JsonResponse
    {
        $brand = self::cleanBrandName($request->input('brand'));
        if ($brand === null) {
            return response()->json(['status' => 'error', 'code' => 'invalid_brand',
                                     'message' => self::BRAND_UNDO_ERRORS['invalid_brand']], 422)->header('Cache-Control', 'no-store');
        }
        $result = PythonBridge::run('brand_undo', ['brand' => $brand]);
        $status = (string) ($result['status'] ?? '');
        if ($status === 'success') {
            ProductController::forgetProductCaches();
            return response()->json(['status' => 'success', 'brand' => $brand,
                                     'message' => 'شلنا «' . $brand . '» من Brands Mapping. التشغيل الجاي ما بيعرفها.'])
                ->header('Cache-Control', 'no-store');
        }
        if ($status === 'invalid') {
            $code = (string) ($result['code'] ?? '');
            return response()->json(['status' => 'error', 'code' => $code,
                                     'message' => self::BRAND_UNDO_ERRORS[$code] ?? 'ما قدرنا نتراجع عنها.'], 409)
                ->header('Cache-Control', 'no-store');
        }
        return response()->json(['status' => 'error', 'message' => 'ما قدرنا نغيّر ورقة Brands Mapping هلق. ما تغيّر شي. جرّب بعد شوي.'], 502)
            ->header('Cache-Control', 'no-store');
    }

    // ------------------------------------------------------------------
    // «باركودات من صفحات المتاجر»: the barcode a store page stated for an approved row whose barcode cell is empty
    // ------------------------------------------------------------------

    public const BARCODE_WRITE_MAX = 300;
    public const BARCODE_SKIP_TEXTS = [
        'bad_checksum' => 'رقم التحقق بالباركود غلط',
        'gtin_mismatch' => 'الباركود مش نفس اللي انحفظ مع الاعتماد',
        'duplicate' => 'نفس الباركود لأكتر من صف',
        'cell_not_empty' => 'خلية الباركود مش فاضية',
        'row_changed' => 'الصف تغيّر أو انتقل بالشيت',
        'repeated' => 'الصف مكرر بالطلب',
        'sheet_refused' => 'الشيت رفض الكتابة',
    ];
    public const BARCODE_ERRORS = [
        'bad_items' => 'اختار صف واحد عالأقل، ولحد 300 صف بالمرة.',
        'bad_gtin' => 'في باركود مش أرقام بس (من 8 لـ 14 رقم).',
        'no_barcode_column' => 'الشيت ما فيه عمود باركود (Barcode أو EAN أو GTIN). ما كتبنا شي، وما منضيف أعمدة للشيت.',
    ];

    /**
     * GET /api/run/barcode-suggestions: {status, count, rows, duplicates, filled}, each {row, sku_key, name, brand, gtin,
     * domain, duplicate, sheet_barcode}. Read only: the bridge (barcode_suggestions) reads the approvals and the cached
     * sheet rows; nothing is searched or written.
     */
    public function barcodeSuggestions(): \Illuminate\Http\JsonResponse
    {
        if (!self::databaseOnline()) {
            return response()->json(['status' => 'unavailable', 'message' => 'قاعدة البيانات مش شغّالة هلق.'], 503)
                ->header('Cache-Control', 'no-store');
        }
        $result = PythonBridge::run('barcode_suggestions');
        if (($result['status'] ?? '') !== 'success' || !is_array($result['rows'] ?? null)) {
            return response()->json(['status' => 'error', 'message' => 'ما قدرنا نقرأ الباركودات هلق. جرّب بعد شوي.'], 502)
                ->header('Cache-Control', 'no-store');
        }
        $rows = self::barcodeRows($result['rows']);
        $duplicates = self::barcodeRows($result['duplicates'] ?? []);
        $filled = self::barcodeRows($result['filled'] ?? []);
        return response()->json([
            'status' => 'success',
            'count' => count($rows) + count($duplicates) + count($filled),
            'rows' => $rows,
            'duplicates' => $duplicates,
            'filled' => $filled,
        ])->header('Cache-Control', 'no-store');
    }

    /** The bridge's suggestion rows, checked and trimmed to what the card shows. */
    private static function barcodeRows($list): array
    {
        $out = [];
        foreach (is_array($list) ? $list : [] as $r) {
            if (!is_array($r)) {
                continue;
            }
            $row = filter_var($r['row'] ?? null, FILTER_VALIDATE_INT);
            $sku = is_string($r['sku_key'] ?? null) ? trim($r['sku_key']) : '';
            $gtin = is_string($r['gtin'] ?? null) ? trim($r['gtin']) : '';
            if ($row === false || $row < 2 || $sku === '' || strlen($sku) > 64 || !preg_match('/^\d{8,14}$/', $gtin)) {
                continue;
            }
            $text = fn ($v, $max) => is_string($v) ? mb_substr(trim($v), 0, $max) : '';
            $out[] = [
                'row' => $row,
                'sku_key' => $sku,
                'name' => $text($r['name'] ?? null, 300),
                'brand' => $text($r['brand'] ?? null, 120),
                'gtin' => $gtin,
                'domain' => $text($r['domain'] ?? null, 253),
                'duplicate' => (bool) ($r['duplicate'] ?? false),
                'sheet_barcode' => $text($r['sheet_barcode'] ?? null, 60),
            ];
        }
        return $out;
    }

    /**
     * POST /api/run/barcode-write {items: [{row, sku_key, gtin}]}: «اكتب الباركودات المختارة بالشيت». Checked here (1 to
     * 300 rows, each a sheet row, a key and 8 to 14 digits, no row twice), then the bridge (barcode_write) checks each
     * barcode again against the approval, queues one identity-checked write per row and never writes over a barcode.
     * CSRF like every POST.
     */
    public function barcodeWrite(Request $request): \Illuminate\Http\JsonResponse
    {
        $items = $request->input('items');
        if (!is_array($items) || count($items) < 1 || count($items) > self::BARCODE_WRITE_MAX) {
            return self::barcodeError('bad_items', 422);
        }
        $clean = [];
        $rows = [];
        foreach ($items as $entry) {
            $entry = is_array($entry) ? $entry : [];
            $row = filter_var($entry['row'] ?? null, FILTER_VALIDATE_INT);
            $sku = is_string($entry['sku_key'] ?? null) ? trim($entry['sku_key']) : '';
            $gtin = is_string($entry['gtin'] ?? null) ? trim($entry['gtin']) : '';
            if ($row === false || $row < 2 || $sku === '' || strlen($sku) > 64 || preg_match('/[\x00-\x1F\x7F]/', $sku)
                || isset($rows[$row])) {
                return self::barcodeError('bad_items', 422);
            }
            if (!preg_match('/^\d{8,14}$/', $gtin)) {
                return self::barcodeError('bad_gtin', 422);
            }
            $rows[$row] = true;
            $clean[] = ['row' => $row, 'sku_key' => $sku, 'gtin' => $gtin];
        }
        if (!self::databaseOnline()) {
            return response()->json(['status' => 'unavailable', 'message' => 'قاعدة البيانات مش شغّالة هلق. ما انكتب شي.'], 503)
                ->header('Cache-Control', 'no-store');
        }
        $result = PythonBridge::run('barcode_write', ['items' => $clean]);
        $status = (string) ($result['status'] ?? '');
        if ($status === 'refused' && ($result['code'] ?? '') === 'no_barcode_column') {
            return self::barcodeError('no_barcode_column', 409);
        }
        if ($status === 'invalid') {
            return self::barcodeError('bad_items', 422);
        }
        if ($status !== 'success') {
            return response()->json(['status' => 'error', 'message' => 'ما قدرنا نكتب بالشيت هلق. ما انكتب شي. جرّب بعد شوي.'], 502)
                ->header('Cache-Control', 'no-store');
        }
        $written = max(0, (int) ($result['written'] ?? 0));
        $queued = max(0, (int) ($result['queued'] ?? 0));
        $skipped = [];
        foreach (is_array($result['skipped'] ?? null) ? $result['skipped'] : [] as $s) {
            $reason = is_array($s) && is_string($s['reason'] ?? null) && isset(self::BARCODE_SKIP_TEXTS[$s['reason']])
                ? $s['reason'] : 'sheet_refused';
            $skipped[] = ['row' => is_array($s) ? (int) ($s['row'] ?? 0) : 0, 'reason' => $reason,
                          'text' => self::BARCODE_SKIP_TEXTS[$reason]];
        }
        if ($written || $queued) {
            ProductController::forgetProductCaches();
        }
        return response()->json([
            'status' => 'success',
            'written' => $written,
            'queued' => $queued,
            'skipped' => $skipped,
            'message' => self::barcodeResultText($written, $queued, $skipped),
        ])->header('Cache-Control', 'no-store');
    }

    /** 1 => «صف واحد», 2 => «صفين», 3..10 => «N صفوف», else «N صف». */
    private static function rowsText(int $n): string
    {
        return $n === 1 ? 'صف واحد' : ($n === 2 ? 'صفين' : $n . ($n >= 3 && $n <= 10 ? ' صفوف' : ' صف'));
    }

    /** «بصف واحد», «بصفين», «بـ 3 صفوف». */
    private static function inRows(int $n): string
    {
        return ($n <= 2 ? 'ب' : 'بـ ') . self::rowsText($n);
    }

    /** What a barcode write did, in the owner's words: written, queued and skipped (with why, row by row). */
    public static function barcodeResultText(int $written, int $queued, array $skipped): string
    {
        $parts = [];
        if ($written) {
            $parts[] = 'انكتب الباركود ' . self::inRows($written) . ' بالشيت.';
        }
        if ($queued) {
            $parts[] = self::rowsText($queued) . ' بالطابور: رح ينكتبوا أول ما يرد الشيت.';
        }
        if ($skipped) {
            $why = [];
            foreach ($skipped as $s) {
                $why[$s['text']][] = $s['row'];
            }
            $lines = [];
            foreach ($why as $text => $rows) {
                $lines[] = $text . ' (صف ' . implode('، ', array_slice($rows, 0, 8)) . (count($rows) > 8 ? '…' : '') . ')';
            }
            $parts[] = 'ما كتبنا ' . self::inRows(count($skipped)) . ': ' . implode('؛ ', $lines) . '.';
        }
        return $parts ? implode(' ', $parts) : 'ما انكتب شي.';
    }

    private static function barcodeError(string $code, int $http): \Illuminate\Http\JsonResponse
    {
        return response()->json([
            'status' => 'error',
            'code' => $code,
            'message' => self::BARCODE_ERRORS[$code] ?? 'الطلب مش صالح.',
        ], $http)->header('Cache-Control', 'no-store');
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
            $rows = DB::table('system_settings')
                ->whereIn('key', ['auto_publish_enabled', 'auto_publish_brands', SettingsController::STRICT_LANE_KEY])
                ->pluck('value', 'key')->all();
        } catch (\Throwable $e) {
            return ['known' => false, 'enabled' => false, 'brands' => [],
                    'text' => 'ما قدرنا نقرأ إعداد النشر الآلي هلق.'];
        }
        $enabled = in_array(strtolower(trim((string) ($rows['auto_publish_enabled'] ?? ''))), ['1', 'true', 'yes', 'on'], true);
        $brands = array_values(array_filter(array_map('trim', explode(',', (string) ($rows['auto_publish_brands'] ?? ''))),
            fn ($b) => $b !== ''));
        // the saved value, else the default (on), as config.load_db_config reads it
        $lane = SettingsController::strictLaneOn($rows[SettingsController::STRICT_LANE_KEY] ?? null);
        return ['known' => true, 'enabled' => $enabled, 'brands' => $brands, 'strict_lane' => $lane,
                'text' => self::autoPublishText($enabled, $brands, $lane)];
    }

    public static function autoPublishText(bool $enabled, array $brands, bool $strictLane = false): string
    {
        $star = $enabled && in_array('*', $brands, true);
        if ($strictLane && !$star && (!$enabled || !$brands)) {
            // «النشر الآلي لكل الماركات المؤكدة» (decide.py, lane strict): أي ماركة موجودة بـ Brands Mapping، بس بعد ما
            // تثبت مراجعات الفئة دقتها (decide.strict_lane_readiness)، وبلا ما يحتاج مفتاح جدول الماركات
            return 'النشر الآلي لكل الماركات المؤكدة شغّال: بعد ما تثبت دقته بمراجعاتك، الاقتراح المؤكد تماماً وبلا أي '
                . 'تحذير لماركة موجودة بـ Brands Mapping بينزل عالشيت لحاله. لحد هداك الوقت، وكل الباقي، بيستنى مراجعتك.';
        }
        if (!$enabled) {
            return 'النشر الآلي مطفأ: كل النتائج بتستنى مراجعتك قبل ما توصل الشيت.';
        }
        if (!$brands) {
            return 'النشر الآلي مفعّل بس ما في ولا ماركة مسموحة، فكل النتائج بتستنى مراجعتك.';
        }
        if ($star) {
            return 'النشر الآلي شغّال لكل الماركات: الاقتراح المؤكد تماماً بينزل عالشيت لحاله، والباقي بيستنى مراجعتك.';
        }
        $names = array_map(fn ($b) => stripos($b, 'category:') === 0 ? 'فئة ' . trim(substr($b, 9)) : $b, $brands);
        $shown = implode('، ', array_slice($names, 0, 3)) . (count($names) > 3 ? ' و' . (count($names) - 3) . ' غيرها' : '');
        if ($strictLane) {
            return 'النشر الآلي شغّال لـ ' . $shown . ': الاقتراح المؤكد تماماً لهدول بينزل عالشيت لحاله. والنشر الآلي لكل '
                . 'الماركات المؤكدة شغّال كمان، بس بعد ما تثبت دقته بمراجعاتك. الباقي بيستنى مراجعتك.';
        }
        return 'النشر الآلي شغّال لـ ' . $shown . ': الاقتراح المؤكد تماماً لهدول بينزل عالشيت لحاله، والباقي بيستنى مراجعتك.';
    }
}
