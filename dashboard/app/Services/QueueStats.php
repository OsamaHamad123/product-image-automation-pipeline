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
        'VERIFIER_UNAVAILABLE' => 'نموذج Gemini غير متاح: كل النتائج تذهب للمراجعة البشرية.',
        'VERIFIER_CHECK_FAILED' => 'تعذر فحص نموذج Gemini عند بدء العامل: كل النتائج تذهب للمراجعة البشرية.',
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
}
