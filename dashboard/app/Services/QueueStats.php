<?php

namespace App\Services;

use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * Real batch counters read from automation_queue: rows per status, and rows that are
 * not completed per failure_code. These replace the hard-coded dashboard tiles.
 *
 * Also the run status shown by every page (ApiController::batchStatus): the progress of
 * the current run only (rows carrying its run_id, see local_cache_db.begin_run), the run
 * phase, and the Arabic texts for it. The phase and texts are pure functions so the
 * pages only render them and the logic survives a redesign of the views.
 */
class QueueStats
{
    public static function counters(): array
    {
        $byStatus = [];
        $byCode = [];
        try {
            $rows = DB::table('automation_queue')
                ->select('status', DB::raw('COUNT(*) AS n'))
                ->groupBy('status')
                ->get();
            foreach ($rows as $r) {
                $byStatus[(string) $r->status] = (int) $r->n;
            }
            if (Schema::hasColumn('automation_queue', 'failure_code')) {
                $codes = DB::table('automation_queue')
                    ->whereNotNull('failure_code')
                    ->where('failure_code', '<>', '')
                    ->where('status', '<>', 'completed')
                    ->select('failure_code', DB::raw('COUNT(*) AS n'))
                    ->groupBy('failure_code')
                    ->get();
                foreach ($codes as $r) {
                    $byCode[(string) $r->failure_code] = (int) $r->n;
                }
            }
        } catch (\Throwable $e) {
            // automation_queue does not exist yet (fresh install before the first run)
        }
        ksort($byCode);
        return ['by_status' => $byStatus, 'by_failure_code' => $byCode];
    }

    /**
     * Counters of one run (rows whose run_id is $runId), or null without a run id or
     * before the run_id column exists.
     */
    public static function run(?string $runId): ?array
    {
        if ($runId === null || $runId === '') {
            return null;
        }
        $byStatus = [];
        try {
            $rows = DB::table('automation_queue')
                ->where('run_id', $runId)
                ->select('status', DB::raw('COUNT(*) AS n'))
                ->groupBy('status')
                ->get();
            foreach ($rows as $r) {
                $byStatus[(string) $r->status] = (int) $r->n;
            }
        } catch (\Throwable $e) {
            return null;
        }
        return self::runTotals($runId, $byStatus);
    }

    /** total / processed (ready for review + completed + failed) / per status, from rows per status. */
    public static function runTotals(?string $runId, array $byStatus): array
    {
        $n = function (string $status) use ($byStatus): int {
            return (int) ($byStatus[$status] ?? 0);
        };
        return [
            'run_id' => $runId,
            'total' => array_sum(array_map('intval', $byStatus)),
            'processed' => $n('ready_for_review') + $n('completed') + $n('failed'),
            'ready_for_review' => $n('ready_for_review'),
            'completed' => $n('completed'),
            'failed' => $n('failed'),
            'pending' => $n('pending'),
            'processing' => $n('processing'),
        ];
    }

    /**
     * What the automation is doing: starting (the enqueue reads the sheet) | running | paused |
     * stopping (a stop request waits for the products in progress) | error (the last run stopped
     * on an error or a provider outage) | review (rows wait for review) | idle (nothing waits).
     * $process is the worker lock state: starting | running | none.
     */
    public static function runPhase(string $process, string $status, int $pauseRequested, int $stopRequested,
                                    int $readyForReview): string
    {
        if ($process === 'starting') {
            return 'starting';
        }
        if ($process === 'running') {
            if ($stopRequested === 1) {
                return 'stopping';
            }
            if ($status === 'starting') {
                return 'starting';
            }
            return $pauseRequested === 1 ? 'paused' : 'running';
        }
        if (in_array($status, ['error', 'provider_down'], true)) {
            return 'error';
        }
        return $readyForReview > 0 ? 'review' : 'idle';
    }

    public static function phaseText(string $phase, int $stopRequested, int $readyForReview): string
    {
        switch ($phase) {
            case 'starting':
                return $stopRequested === 1
                    ? 'طلب الإيقاف مسجل: التشغيل ما زال يقرأ الشيت، وسيتوقف العامل فور بدئه قبل معالجة أي منتج.'
                    : 'جاري قراءة الشيت وتجهيز الطابور…';
            case 'running':
                return 'جاري تحضير المرشحات…';
            case 'paused':
                return 'الأتمتة موقوفة مؤقتاً.';
            case 'stopping':
                return 'جاري الإيقاف: ينهي العامل المنتجات الجارية ثم يتوقف، وتبقى باقي الصفوف في الانتظار.';
            case 'error':
                return 'توقف التشغيل بسبب خطأ (السبب في الشريط الأحمر).';
            case 'review':
                return "انتهى التحضير: {$readyForReview} منتج بانتظار المراجعة.";
            default:
                return 'لا يوجد تشغيل حالياً، ولا توجد منتجات بانتظار المراجعة.';
        }
    }

    /** Arabic sentence per notice code written by main.py (automation_state.notice). */
    private const NOTICE_TEXT = [
        'ENQUEUE_FAILED' => 'فشل تجهيز التشغيل ولم يبدأ العامل',
        'SHEETS_UNAVAILABLE' => 'تعذر الاتصال بـ Google Sheets فتوقف التشغيل قبل معالجة أي منتج. تحقق من ملف بيانات الاعتماد والاتصال بالإنترنت.',
        'SHEET_CONFIG' => 'إعداد الشيت غير صالح فتوقف التشغيل قبل معالجة أي منتج',
        'PROVIDER_DOWN' => 'محركات البحث غير متاحة، فتوقف العامل وبقيت الصفوف المتبقية في الانتظار. أعد التشغيل لاحقاً.',
        'VERIFIER_NOT_CONFIGURED' => 'لا يوجد مفتاح Gemini: كل النتائج تذهب للمراجعة البشرية ولا يُنشر أي شيء تلقائياً.',
        'VERIFIER_NOT_CONFIGURED_CLAUDE' => 'نموذج القراءة الأساسي من Claude ولا يوجد مفتاح Anthropic: كل النتائج تذهب للمراجعة البشرية ولا يُنشر أي شيء تلقائياً.',
        'VERIFIER_UNAVAILABLE' => 'نموذج Gemini غير متاح: كل النتائج تذهب للمراجعة البشرية.',
        'VERIFIER_CHECK_FAILED' => 'تعذر فحص نموذج Gemini عند بدء العامل: كل النتائج تذهب للمراجعة البشرية.',
        'BUDGET_REACHED' => 'بلغ صرف اليوم الميزانية اليومية للبحث (DAILY_BUDGET_USD)، فتوقف العامل وبقيت الصفوف المتبقية في الانتظار. يكمل التشغيل التالي في يوم جديد أو بعد رفع الميزانية.',
        'DB_UNAVAILABLE' => 'تعذر الوصول إلى قاعدة البيانات فتوقف العامل، وبقيت الصفوف المتبقية في الانتظار.',
    ];

    /** Codes whose detail (the sheet error or the Arabic enqueue error) is shown after the sentence. */
    private const NOTICE_WITH_DETAIL = ['ENQUEUE_FAILED', 'SHEET_CONFIG'];

    /**
     * The red banner: the notice in Arabic (every ' | ' part), or a generic sentence when the run
     * stopped on an error without a notice. Empty when there is nothing to report.
     */
    public static function alertText(string $status, string $notice, bool $running): string
    {
        $parts = [];
        foreach (explode(' | ', trim($notice)) as $part) {
            $part = trim($part);
            if ($part === '') {
                continue;
            }
            if (preg_match('/^([A-Z][A-Z_]+):\s*(.*)$/su', $part, $m)) {
                [$code, $detail] = [$m[1], trim($m[2])];
                if (isset(self::NOTICE_TEXT[$code])) {
                    $text = self::NOTICE_TEXT[$code];
                    if ($detail !== '' && in_array($code, self::NOTICE_WITH_DETAIL, true)) {
                        $text .= ': ' . $detail;
                    }
                    $parts[] = $text;
                    continue;
                }
                if (preg_match('/\p{Arabic}/u', $detail)) {
                    $parts[] = $detail;          // ops_health outage messages are already Arabic
                    continue;
                }
            }
            $parts[] = $part;
        }
        if (!$parts && !$running && in_array($status, ['error', 'provider_down'], true)) {
            $parts[] = 'توقف التشغيل بسبب خطأ غير معروف؛ راجع سجل التشغيل في صفحة الأتمتة.';
        }
        return implode(' | ', $parts);
    }

    // ------------------------------------------------------------------------------------------------
    // Laqta Home and Run pages (package p2-run). Everything below is additive: pure functions first
    // (run under the PHP CLI by tests/test_laqta_run.py), then the few database readers they share.
    // A number that appears on both pages comes from one of these functions, never from a copy.
    // ------------------------------------------------------------------------------------------------

    /** Failure codes that mean the search ran and found no acceptable image (the product, not a service). */
    public const NOT_FOUND_CODES = ['NO_RESULTS', 'ALL_CONFLICTED', 'NO_MATCH', 'NOT_FOUND'];

    /** Rows the worker is done with; runTotals counts the same three as "processed". */
    public const DONE_STATUSES = ['ready_for_review', 'completed', 'failed'];

    /** Statuses automation_state keeps while a run should be alive. */
    public const ACTIVE_STATE_STATUSES = ['pre_caching', 'running', 'starting'];

    /**
     * Plain Arabic for every result kind and the lq chip it uses. Both pages read only this table, so a
     * product carries the same words on Home and on Run.
     */
    public const RESULT_KINDS = [
        'proposed' => ['label' => 'مقترحة', 'chip' => 'proposed', 'tone' => 'success'],
        'none' => ['label' => 'بلا اقتراح', 'chip' => 'none', 'tone' => 'muted'],
        'not_found' => ['label' => 'ما انلقت', 'chip' => 'not-found', 'tone' => 'info'],
        'error' => ['label' => 'عطل مؤقت', 'chip' => 'error', 'tone' => 'danger'],
        'requeued' => ['label' => 'رجعت للطابور', 'chip' => 'warning', 'tone' => 'warning'],
        'rejected' => ['label' => 'رجعت للطابور', 'chip' => 'none', 'tone' => 'muted'],
        'approved' => ['label' => 'معتمدة', 'chip' => 'approved', 'tone' => 'teal'],
        'searching' => ['label' => 'عم ندوّر', 'chip' => 'none', 'tone' => 'muted'],
        'waiting' => ['label' => 'بالطابور', 'chip' => 'none', 'tone' => 'muted'],
    ];

    /** Secondary explanation of a failure code (tooltips and the last-run sentence); never the main text. */
    public const FAILURE_TEXT = [
        'NO_RESULTS' => 'البحث ما رجّع ولا صورة',
        'ALL_CONFLICTED' => 'كل الصور اللي انلقت لمنتج تاني أو حجم تاني',
        'NO_MATCH' => 'ما في صورة مطابقة للمنتج',
        'NOT_FOUND' => 'ما في صورة مطابقة للمنتج',
        'SEARCH_ERROR' => 'صار خطأ أثناء البحث',
        'CANDIDATE_SAVE_FAILED' => 'ما قدرنا نحفظ الاقتراحات بقاعدة البيانات',
        'PROVIDER_DOWN' => 'مصادر البحث ما كانت متاحة',
        'DOWNLOAD_FAILED' => 'ما قدرنا ننزّل الصور من مواقعها',
        'VERIFIER_DOWN' => 'نموذج قراءة الملصق ما ردّ، فالاختيار بدّه عينك',
        // a re-verification with a working label reader found nothing better: the earlier proposals wait for review
        'RECHECK_NOT_FOUND' => 'رجعنا فحصنا بنموذج قراءة الملصق وما لقينا صورة أحسن، فالاقتراحات القديمة بتستنى عينك',
        // an approved image whose link could not be queued for the sheet: the next run writes it without a search
        'SHEET_WRITE_FAILED' => 'الصورة معتمدة بس ما قدرنا نكتب رابطها بالشيت، وبتنكتب بالتشغيل الجاي',
    ];

    /**
     * Same prices as ops_health.SERPER_COST_PER_QUERY / GEMINI_COST_PER_CALL and, for SerpApi Google Lens,
     * ops_health.SERPAPI_LENS_DEFAULT_COST; the prices ops_health reports win.
     */
    public const DEFAULT_PRICES = ['serper_per_query' => 0.001, 'gemini_per_call' => 0.001, 'lens_serpapi' => 0.015];

    /** Every Serper endpoint is billed per answered call (ops_health.SERPER_BILLED_PROVIDERS). */
    public const SERPER_BILLED_PROVIDERS = ['serper', 'serper_web', 'serper_shopping', 'lens_serper'];

    /** A Serper query the provider answered (ops_health.ANSWERED_STATUSES): only these are billed. */
    public const ANSWERED_STATUSES = ['ok', 'empty'];

    /**
     * The result kind of one queue row from its status, failure code and the engine decision (trace outcome):
     * proposed | none | not_found | error | requeued | rejected | approved | searching | waiting.
     */
    public static function resultKind(string $status, ?string $failureCode = null, ?string $decision = null): string
    {
        $code = strtoupper(trim((string) $failureCode));
        $decision = strtoupper(trim((string) $decision));
        switch ($status) {
            case 'completed':
                return 'approved';
            case 'ready_for_review':
                // AUTO_PUBLISH that could not publish (no isolated background) waits for review with its pick
                return in_array($decision, ['REVIEW_PRESELECTED', 'AUTO_PUBLISH'], true) ? 'proposed' : 'none';
            case 'failed':
                if (in_array($code, self::NOT_FOUND_CODES, true) || ($code === '' && $decision === 'NOT_FOUND')) {
                    return 'not_found';
                }
                return 'error';
            case 'processing':
                return 'searching';
            case 'pending':
                if ($code === 'PROVIDER_DOWN') {
                    return 'requeued';
                }
                // a reviewer rejected the pick and the product waits for a new search: same words as the
                // review page (public/js/review/core.js), but not a failure of the run
                return $code === 'REJECTED' ? 'rejected' : 'waiting';
        }
        return 'waiting';
    }

    /** "منتج واحد", "منتجين", "5 منتجات", "40 منتج" (Arabic counting with western digits). */
    public static function countText(int $n, string $one = 'منتج', string $two = 'منتجين', string $few = 'منتجات'): string
    {
        if ($n === 1) {
            return $one . ' واحد';
        }
        if ($n === 2) {
            return $two;
        }
        return $n . ' ' . (($n >= 3 && $n <= 10) ? $few : $one);
    }

    /** Row numbers as short ranges: [2,3,4,9] -> "2–4، 9"; more than $maxGroups groups end with "…". */
    public static function rowsLabel(array $rows, int $maxGroups = 3): string
    {
        $nums = array_values(array_unique(array_filter(array_map('intval', $rows), fn ($n) => $n > 0)));
        sort($nums);
        if (!$nums) {
            return '';
        }
        $groups = [];
        $start = $prev = $nums[0];
        foreach (array_slice($nums, 1) as $n) {
            if ($n === $prev + 1) {
                $prev = $n;
                continue;
            }
            $groups[] = [$start, $prev];
            $start = $prev = $n;
        }
        $groups[] = [$start, $prev];
        $text = array_map(fn ($g) => $g[0] === $g[1] ? (string) $g[0] : $g[0] . '–' . $g[1], $groups);
        if (count($text) > $maxGroups) {
            return implode('، ', array_slice($text, 0, $maxGroups)) . '، …';
        }
        return implode('، ', $text);
    }

    /**
     * Billable use in one trace outcome, counted as ops_health counts it: answered calls of every Serper endpoint,
     * answered SerpApi Google Lens calls, and the label readers. A reader that recorded its spend (outcome.vlm_usage:
     * tokens priced per model) is billed by that spend; an older outcome without it, by its calls at the flat price.
     */
    public static function outcomeUsage(?array $outcome): array
    {
        $serper = $serpapi = 0;
        foreach ((array) ($outcome['provider_health'] ?? []) as $item) {
            if (!is_array($item)) {
                continue;
            }
            $provider = strtolower(trim((string) ($item['provider'] ?? '')));
            $status = strtolower(trim((string) ($item['status'] ?? '')));
            if (!in_array($status, self::ANSWERED_STATUSES, true)) {
                continue;
            }
            if (in_array($provider, self::SERPER_BILLED_PROVIDERS, true)) {
                $serper++;
            } elseif ($provider === 'lens_serpapi') {
                $serpapi++;
            }
        }
        $vlm = is_numeric($outcome['vlm_calls'] ?? null) ? max(0, (int) $outcome['vlm_calls']) : 0;
        $priced = false;
        $vlmUsd = 0.0;
        foreach ((array) ($outcome['vlm_usage'] ?? []) as $use) {
            // the entries ops_health._vlm_usage keeps: a provider and a model; a negative or unreadable cost is 0
            if (!is_array($use) || trim((string) ($use['provider'] ?? '')) === '' || trim((string) ($use['model'] ?? '')) === '') {
                continue;
            }
            $priced = true;
            $usd = is_numeric($use['usd'] ?? null) ? (float) $use['usd'] : 0.0;
            $vlmUsd += $usd > 0 ? $usd : 0.0;
        }
        return ['serper_queries' => $serper, 'serpapi_calls' => $serpapi, 'vlm_calls' => $vlm,
                'unpriced_vlm_calls' => $priced ? 0 : $vlm, 'vlm_usd' => $vlmUsd];
    }

    /**
     * Estimated cost of answered Serper queries, label reads without a recorded spend (flat price per call),
     * SerpApi Lens calls and the recorded label-reader spend.
     */
    public static function usageCost(int $serperQueries, int $unpricedVlmCalls, ?array $prices = null, int $serpapiCalls = 0,
                                     float $vlmUsd = 0.0): float
    {
        $price = fn (string $k) => is_numeric($prices[$k] ?? null) && (float) $prices[$k] >= 0 ? (float) $prices[$k]
            : self::DEFAULT_PRICES[$k];
        return round($serperQueries * $price('serper_per_query') + $unpricedVlmCalls * $price('gemini_per_call')
            + $serpapiCalls * $price('lens_serpapi') + $vlmUsd, 4);
    }

    /** Epoch seconds of outcome.searched_at (ISO time with a zone, as catalog_match.facade writes it), or null. */
    public static function searchedAt(?array $outcome): ?int
    {
        $raw = is_array($outcome) ? ($outcome['searched_at'] ?? null) : null;
        if (!is_string($raw) || !preg_match('/(Z|[+-]\d{2}:?\d{2})$/', trim($raw))) {
            return null;
        }
        $ts = strtotime(trim($raw));
        return $ts === false ? null : $ts;
    }

    /**
     * Summary of one run from its queue rows. Each row: row_number, product_name, status, failure_code,
     * outcome (decoded trace outcome or null), age_s (seconds since the row changed, or null).
     * Counts per result kind, the rows label, first and last search time, duration, seconds per product,
     * the estimated cost (null when no row carries a readable outcome) and plain sentences about failures.
     */
    public static function runSummary(array $rows, ?array $prices = null, ?int $now = null): array
    {
        $now = $now ?? time();
        $counts = array_fill_keys(array_keys(self::RESULT_KINDS), 0);
        $codes = [];
        $rowsByKind = [];
        $times = [];
        $numbers = [];
        $serper = $serpapi = $vlm = $readable = $processed = 0;
        $vlmUsd = 0.0;
        foreach ($rows as $row) {
            $row = (array) $row;
            $outcome = is_array($row['outcome'] ?? null) ? $row['outcome'] : null;
            $status = (string) ($row['status'] ?? '');
            $kind = self::resultKind($status, $row['failure_code'] ?? null, $outcome['decision'] ?? null);
            $number = (int) ($row['row_number'] ?? 0);
            $counts[$kind]++;
            $numbers[] = $number;
            $rowsByKind[$kind][] = $number;
            if (in_array($kind, ['error', 'requeued', 'not_found'], true)) {
                $code = strtoupper(trim((string) ($row['failure_code'] ?? ''))) ?: 'UNKNOWN';
                $codes[$kind][$code] = ($codes[$kind][$code] ?? 0) + 1;
            }
            $done = in_array($status, self::DONE_STATUSES, true);
            $processed += $done ? 1 : 0;
            if ($done || $kind === 'requeued') {
                $at = self::searchedAt($outcome);
                if ($at === null && is_numeric($row['age_s'] ?? null)) {
                    $at = $now - max(0, (int) $row['age_s']);
                }
                if ($at !== null) {
                    $times[] = $at;
                }
            }
            if ($outcome) {
                $readable++;
                $use = self::outcomeUsage($outcome);
                $serper += $use['serper_queries'];
                $serpapi += $use['serpapi_calls'];
                $vlm += $use['unpriced_vlm_calls'];
                $vlmUsd += $use['vlm_usd'];
            }
        }
        $started = $times ? min($times) : null;
        $ended = $times ? max($times) : null;
        $span = $times ? $ended - $started : null;
        return [
            'total' => count($rows),
            'processed' => $processed,
            'counts' => $counts,
            'rows_label' => self::rowsLabel($numbers),
            'started_at' => $started,
            'ended_at' => $ended,
            'duration_s' => $span,
            'per_product_s' => count($times) >= 2 ? round($span / (count($times) - 1), 1) : null,
            'cost_usd' => $readable > 0 ? self::usageCost($serper, $vlm, $prices, $serpapi, $vlmUsd) : null,
            'explain' => self::explainFailures($counts, $codes, $rowsByKind),
        ];
    }

    /** Plain sentences about what went wrong in a run and what happens to those products next ('' when nothing ran). */
    public static function explainFailures(array $counts, array $codes = [], array $rowsByKind = []): string
    {
        $reason = function (array $byCode): string {
            arsort($byCode);
            $texts = [];
            foreach (array_keys($byCode) as $code) {
                $text = self::FAILURE_TEXT[$code] ?? null;
                if ($text !== null && !in_array($text, $texts, true)) {
                    $texts[] = $text;
                }
            }
            return implode(' أو ', array_slice($texts, 0, 2));
        };
        $where = function (string $kind) use ($rowsByKind): string {
            $rows = array_unique($rowsByKind[$kind] ?? []);
            $label = self::rowsLabel($rows);
            return $label === '' ? '' : (count($rows) === 1 ? ' (الصف ' : ' (الصفوف ') . $label . ')';
        };
        $parts = [];
        $requeued = (int) ($counts['requeued'] ?? 0);
        $errors = (int) ($counts['error'] ?? 0);
        $notFound = (int) ($counts['not_found'] ?? 0);
        if ($requeued > 0) {
            $parts[] = self::countText($requeued) . $where('requeued')
                . ($requeued === 1 ? ' رجع للطابور' : ' رجعت للطابور')
                . ' لأن مصادر البحث ما كانت متاحة وقتها، وبتنعاد لحالها بالتشغيل الجاي.';
        }
        if ($errors > 0) {
            $why = $reason($codes['error'] ?? []);
            $parts[] = 'الأعطال المؤقتة: ' . self::countText($errors) . $where('error')
                . ($why !== '' ? '، السبب: ' . $why . '.' : '.')
                . ' بتنعاد لما تشغّل ' . ($errors === 1 ? 'هالصف' : 'هالصفوف') . ' مرة تانية، وما انحذف شي.';
        }
        if ($notFound > 0) {
            $parts[] = self::countText($notFound) . $where('not_found')
                . ($notFound === 1 ? ' ما لقيناله' : ' ما لقينالها')
                . ' صورة مناسبة؛ جرّب تبحث بكلمات تانية من صفحة المراجعة.';
        }
        $left = (int) ($counts['waiting'] ?? 0) + (int) ($counts['searching'] ?? 0);
        if ($left > 0) {
            $parts[] = self::countText($left) . ' لسا ما انبحث ' . ($left === 1 ? 'عنه' : 'عنها')
                . ' بهالتشغيل، وبتضل بالطابور لحد التشغيل الجاي.';
        }
        if ($parts) {
            return implode(' ', $parts);
        }
        $done = (int) ($counts['proposed'] ?? 0) + (int) ($counts['none'] ?? 0) + (int) ($counts['approved'] ?? 0);
        return $done > 0 ? 'كل المنتجات انبحث عنها بدون أعطال.' : '';
    }

    /**
     * Why the run needs «إصلاح تشغيل عالق», in plain Arabic, or '' when it does not. $worker is the lock state
     * (starting | running | none) and $stateAgeS the seconds since automation_state last changed.
     */
    public static function stuckReason(string $phase, string $worker, string $status, int $processingRows,
                                       ?int $stateAgeS, int $pauseRequested = 0): string
    {
        if ($phase === 'error') {
            return 'آخر تشغيل وقف بعطل وضلّت حالته معلّقة.';
        }
        if ($worker === 'none' && $processingRows > 0) {
            $stuck = $processingRows === 1 ? ' عالق' : ($processingRows === 2 ? ' عالقين' : ' عالقة');
            return self::countText($processingRows) . $stuck . ' على «عم ندوّر» وما في عامل شغّال '
                . ($processingRows === 1 ? 'يكمّله.' : 'يكمّلها.');
        }
        if ($worker === 'none' && in_array($status, self::ACTIVE_STATE_STATUSES, true)) {
            return 'الحالة بتقول إنو في تشغيل، بس ما في عامل شغّال بالخلفية.';
        }
        if ($worker === 'running' && $phase === 'running' && $pauseRequested !== 1 && $stateAgeS !== null
            && $stateAgeS > 600) {
            return 'العامل شغّال بس ما تقدّم ولا منتج من ' . intdiv($stateAgeS, 60) . ' دقيقة.';
        }
        if ($worker === 'starting' && $phase === 'starting' && $stateAgeS !== null && $stateAgeS > 180) {
            return 'تجهيز التشغيل طوّل أكتر من 3 دقايق وما بلّش البحث.';
        }
        return '';
    }

    /**
     * main.parse_row_filter in PHP, for the run plan: '2-5, 9' -> [[2,5],[9,9]]; '' -> null.
     * Arabic-Indic digits and the Arabic comma are read as ASCII first (the page sends the ASCII text).
     * Returns ['ranges' => list|null, 'text' => normalised text, 'error' => Arabic message or ''].
     */
    public static function parseRowFilter(?string $text): array
    {
        $text = self::normalizeRowFilter($text);
        if ($text === '') {
            return ['ranges' => null, 'text' => '', 'error' => ''];
        }
        $bad = ['ranges' => null, 'text' => $text, 'error' => 'اكتب أرقام صفوف أو نطاقات، مثل 62-101 أو 5, 8, 12.'];
        $int = '/^\s*\+?\d+\s*$/';
        $ranges = [];
        foreach (explode(',', $text) as $part) {
            $part = trim($part);
            if ($part === '') {
                continue;
            }
            if (strpos($part, '-') !== false) {
                [$a, $b] = explode('-', $part, 2);
                if (!preg_match($int, $a) || !preg_match($int, $b)) {
                    return $bad;
                }
                if ((int) $b < (int) $a) {
                    return ['ranges' => null, 'text' => $text, 'error' => "النطاق {$part} مقلوب: اكتب الرقم الأصغر أولاً."];
                }
                $ranges[] = [(int) $a, (int) $b];
            } elseif (preg_match($int, $part)) {
                $ranges[] = [(int) $part, (int) $part];
            } else {
                return $bad;
            }
        }
        return $ranges ? ['ranges' => $ranges, 'text' => $text, 'error' => ''] : $bad;
    }

    public static function normalizeRowFilter(?string $text): string
    {
        $map = ['،' => ',', '–' => '-', '—' => '-'];
        foreach (['٠١٢٣٤٥٦٧٨٩', '۰۱۲۳۴۵۶۷۸۹'] as $digits) {
            foreach (mb_str_split($digits) as $i => $digit) {
                $map[$digit] = (string) $i;
            }
        }
        return trim(preg_replace('/\s+/u', ' ', strtr((string) $text, $map)));
    }

    public static function inRanges(int $row, ?array $ranges): bool
    {
        if ($ranges === null) {
            return true;
        }
        foreach ($ranges as [$a, $b]) {
            if ($row >= $a && $row <= $b) {
                return true;
            }
        }
        return false;
    }

    // -- database readers: each returns null when the queue cannot be read, never a silent zero ----------

    private static function decodeOutcome($raw): ?array
    {
        if (is_string($raw) && $raw !== '') {
            $decoded = json_decode($raw, true);
            return is_array($decoded) ? $decoded : null;
        }
        return is_array($raw) ? $raw : null;
    }

    /** Rows of one run with their decoded trace outcome ([] without a run id, null when unreadable). */
    public static function runRows(?string $runId): ?array
    {
        if ($runId === null || $runId === '') {
            return [];
        }
        try {
            $rows = DB::table('automation_queue')
                ->where('run_id', $runId)
                ->select('row_number', 'product_name', 'brand', 'status', 'failure_code',
                    DB::raw("JSON_EXTRACT(trace_json, '$.outcome') AS outcome_json"),
                    DB::raw('TIMESTAMPDIFF(SECOND, updated_at, NOW()) AS age_s'))
                ->get();
        } catch (\Throwable $e) {
            return null;
        }
        $out = [];
        foreach ($rows as $r) {
            $out[] = [
                'row_number' => (int) $r->row_number,
                'product_name' => (string) ($r->product_name ?? ''),
                'brand' => (string) ($r->brand ?? ''),
                'status' => (string) ($r->status ?? ''),
                'failure_code' => $r->failure_code !== null ? (string) $r->failure_code : null,
                'outcome' => self::decodeOutcome($r->outcome_json ?? null),
                'age_s' => $r->age_s !== null ? (int) $r->age_s : null,
            ];
        }
        return $out;
    }

    /** The run to describe: the current one (automation_state.run_id), else the queue's most recent run. */
    public static function latestRunId(?string $stateRunId): ?string
    {
        if ($stateRunId !== null && $stateRunId !== '') {
            return $stateRunId;
        }
        try {
            // the run that searched last (outcome.searched_at, written by the worker only). Not updated_at: every
            // review decision bumps it, so reviewing an old product made its old run «آخر تشغيل».
            $row = DB::table('automation_queue')->whereNotNull('run_id')->where('run_id', '<>', '')
                ->groupBy('run_id')
                ->select('run_id',
                    DB::raw("MAX(JSON_UNQUOTE(JSON_EXTRACT(trace_json, '$.outcome.searched_at'))) AS last_search"),
                    DB::raw('MAX(updated_at) AS last_update'))
                ->orderByDesc('last_search')->orderByDesc('last_update')->first();
            return $row ? (string) $row->run_id : null;
        } catch (\Throwable $e) {
            return null;
        }
    }

    /** Rows waiting for review split by the engine decision: ['proposed' => n, 'none' => n], or null. */
    public static function waitingSplit(): ?array
    {
        try {
            $rows = DB::table('automation_queue')
                ->where('status', 'ready_for_review')
                ->select(DB::raw("JSON_UNQUOTE(JSON_EXTRACT(trace_json, '$.outcome.decision')) AS decision"),
                    DB::raw('COUNT(*) AS n'))
                ->groupBy('decision')
                ->get();
        } catch (\Throwable $e) {
            return null;
        }
        $split = ['proposed' => 0, 'none' => 0];
        foreach ($rows as $r) {
            $split[self::resultKind('ready_for_review', null, $r->decision ?? null)] += (int) $r->n;
        }
        return $split;
    }

    /** Every queue row by row number (status, failure code, decision, sku_key, live lease), or null. */
    public static function queueByRow(): ?array
    {
        try {
            $rows = DB::table('automation_queue')
                ->select('row_number', 'status', 'failure_code', 'sku_key',
                    DB::raw("JSON_UNQUOTE(JSON_EXTRACT(trace_json, '$.outcome.decision')) AS decision"),
                    DB::raw('(lease_until IS NOT NULL AND lease_until >= NOW()) AS lease_held'))
                ->get();
        } catch (\Throwable $e) {
            return null;
        }
        $out = [];
        foreach ($rows as $r) {
            $out[(int) $r->row_number] = [
                'status' => (string) ($r->status ?? ''),
                'failure_code' => $r->failure_code !== null ? (string) $r->failure_code : null,
                'decision' => $r->decision !== null ? (string) $r->decision : null,
                'sku_key' => $r->sku_key !== null ? (string) $r->sku_key : null,
                'lease_held' => (bool) ($r->lease_held ?? false),
            ];
        }
        return $out;
    }
}
