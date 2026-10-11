<?php

namespace App\Http\Controllers;

use App\Services\PythonBridge;
use App\Services\QueueStats;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Cache;

/**
 * لقطة · اليوم (the Today page: the old Home and Run pages on one page, resources/views/dashboard/index.blade.php).
 *
 *   GET /               the page; the live parts come from /api/run/live (RunController::snapshot). An admin also gets
 *                       the run panel and the sheet tools (RunController::autoPublishState / sheetTab); a reviewer
 *                       gets neither (ReviewerLimits blocks their writes anyway).
 *   GET /api/overview   the slower parts: the sheet funnel and the KPIs built on it, and the provider alerts
 *                       (cli_bridge ops_health, shared cache with the Health page) the one alert banner merges.
 *                       Auto-publish readiness lives in Settings › النشر الآلي, the services and the week's cost on
 *                       Health: this page no longer reads them.
 *
 * One number, one source (each KPI names its source in the JSON):
 * - «بانتظار مراجعتك» is QueueStats::counters() ready_for_review: the sidebar badge and /api/batch-status.
 * - «منشورة», «ما انلقت», «أعطال مؤقتة» are stages of the one sheet funnel (funnel()), so the KPI tiles and the
 *   funnel bar can never disagree. A section that cannot be read says so; it is never shown as zero.
 */
class OverviewController extends Controller
{
    /** Levantine month names (the design writes "30 أيلول"). */
    public const MONTHS = ['كانون الثاني', 'شباط', 'آذار', 'نيسان', 'أيار', 'حزيران', 'تموز', 'آب', 'أيلول',
                           'تشرين الأول', 'تشرين الثاني', 'كانون الأول'];
    public const WEEKDAYS = ['الأحد', 'الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت'];

    /** Funnel stages in display order: label and the lq tone of the bar segment. */
    public const STAGES = [
        'published' => ['label' => 'منشورة', 'tone' => 'teal'],
        'review' => ['label' => 'بانتظار مراجعتك', 'tone' => 'warning'],
        'review_link' => ['label' => 'صور قديمة بالشيت بانتظار مراجعة', 'tone' => 'warning'],
        'not_found' => ['label' => 'ما انلقت', 'tone' => 'info'],
        'failed' => ['label' => 'أعطال مؤقتة', 'tone' => 'danger'],
        'not_searched' => ['label' => 'لسا ما انبحث عنها', 'tone' => 'empty'],
    ];

    /** What an ops_health alert means for the owner (the alert codes stay out of the text). */
    public const ALERT_HINTS = [
        'SERPER_CREDIT' => 'البحث عن الصور واقف لحد ما تشحن رصيد Serper أو تصلّح المفتاح.',
        'GEMINI_DOWN' => 'كل النتائج رايحة لمراجعتك لحد ما يرجع Gemini يرد.',
    ];

    public function index()
    {
        $live = RunController::snapshot();
        $admin = !(bool) auth()->user()?->isReviewer();
        // the greeting and the date line in the shop's time zone (home.js rewrites them in the same zone)
        $now = now()->setTimezone(ReviewController::displayTimezone());
        $owner = trim((string) env('LAQTA_OWNER_NAME', ''));
        return view('dashboard.index', [
            'live' => $live,
            'ownerName' => $owner,
            'greeting' => self::greeting((int) $now->format('G'), $owner),
            'dateLine' => self::dateLine((int) $now->format('w'), (int) $now->format('j'), (int) $now->format('n')),
            'lqReviewCount' => $live['batch']['ready_for_review'] ?? null,
            // «جهّز لقطة» بس لما التجهيز ناقص (null لتركيبة مجهّزة أو لما قاعدة البيانات ما بترد)
            'setupCard' => SetupController::homeCard(),
            // the run panel and the sheet tools: the owner's only (a reviewer's page renders neither)
            'todayAdmin' => $admin,
            'autoPublish' => $admin ? RunController::autoPublishState() : null,
            'sheetTab' => $admin ? RunController::sheetTab() : null,
        ]);
    }

    public function data(Request $request)
    {
        return response()->json(self::overview($request->boolean('refresh')))->header('Cache-Control', 'no-store');
    }

    public static function greeting(int $hour, string $name = ''): string
    {
        $text = ($hour >= 4 && $hour < 12) ? 'صباح الخير' : 'مسا الخير';
        return $name !== '' ? $text . ' يا ' . $name : $text;
    }

    /** "الأربعاء، 30 أيلول" from the weekday (0 = Sunday), the day and the month (1-12). */
    public static function dateLine(int $weekday, int $day, int $month): string
    {
        return (self::WEEKDAYS[$weekday] ?? '') . '، ' . $day . ' ' . (self::MONTHS[$month - 1] ?? '');
    }

    /** Everything /api/overview returns. $refresh reads the sheet and ops_health again. */
    public static function overview(bool $refresh = false): array
    {
        $out = ['status' => 'success', 'generated_at' => time()];

        $dbOnline = RunController::databaseOnline();
        $counters = $dbOnline ? QueueStats::counters() : null;
        $split = $dbOnline ? QueueStats::waitingSplit() : null;
        $queue = $dbOnline ? QueueStats::queueByRow() : null;
        $out['review'] = ($counters !== null && $split !== null)
            ? ['status' => 'ok', 'waiting' => (int) ($counters['by_status']['ready_for_review'] ?? 0)] + $split
            : ['status' => 'error', 'message' => 'قاعدة البيانات مش متاحة هلق.'];

        $error = null;
        $sheet = ProductController::sheetRows($refresh, $error);
        if ($sheet === null) {
            $out['sheet'] = ['status' => 'error', 'message' => 'ما قدرنا نقرأ الشيت هلق، فما منقدر نعرض وين وصلت المنتجات.'];
        } elseif ($queue === null) {
            $out['sheet'] = ['status' => 'error', 'message' => 'قاعدة البيانات مش متاحة هلق، فما منقدر نعرض وين وصلت المنتجات.'];
        } else {
            $out['sheet'] = ['status' => 'ok'] + self::funnel($sheet, $queue);
        }
        $out['kpis'] = self::kpis($out['review'], $out['sheet']);

        $ops = self::opsHealth($refresh);
        $out['alerts'] = $ops !== null ? self::alerts($ops['alerts'] ?? []) : [];
        return $out;
    }

    /**
     * The sheet split into stages that sum to the sheet total. A product is in exactly one stage, checked in
     * this order: waiting for review in the queue; a final image in the sheet (or approved in the queue); an old
     * needs_review link in the sheet; not found; a temporary failure (failed, or back in the queue after a
     * provider outage); otherwise not searched yet. Notes explain when the review stage differs from the badge.
     */
    public static function funnel(array $sheet, array $queue): array
    {
        $counts = array_fill_keys(array_keys(self::STAGES), 0);
        $inSheet = [];
        foreach ($sheet as $prod) {
            $row = (int) ($prod['row_number'] ?? 0);
            $inSheet[$row] = true;
            $q = $queue[$row] ?? null;
            $link = trim((string) ($prod['existing_image_link'] ?? ''));
            $reviewLink = !empty($prod['needs_review']) || strpos($link, 'needs_review:') === 0;
            $kind = $q ? QueueStats::resultKind($q['status'], $q['failure_code'] ?? null, $q['decision'] ?? null) : 'waiting';
            if ($q && $q['status'] === 'ready_for_review') {
                $stage = 'review';
            } elseif (($link !== '' && !$reviewLink) || $kind === 'approved') {
                $stage = 'published';
            } elseif ($reviewLink) {
                $stage = 'review_link';
            } elseif ($kind === 'not_found') {
                $stage = 'not_found';
            } elseif (in_array($kind, ['error', 'requeued'], true)) {
                $stage = 'failed';
            } else {
                $stage = 'not_searched';
            }
            $counts[$stage]++;
        }
        $orphans = 0;
        foreach ($queue as $row => $q) {
            if ($q['status'] === 'ready_for_review' && !isset($inSheet[(int) $row])) {
                $orphans++;
            }
        }
        $stages = [];
        foreach (self::STAGES as $key => $meta) {
            if ($key === 'review_link' && $counts[$key] === 0) {
                continue;
            }
            $stages[] = ['key' => $key, 'label' => $meta['label'], 'value' => $counts[$key], 'tone' => $meta['tone']];
        }
        $notes = [];
        if ($orphans > 0) {
            $notes[] = QueueStats::countText($orphans) . ($orphans === 1
                ? ' بانتظار المراجعة صفّه مش موجود بالشيت هلق، فمحسوب بعدّاد المراجعة وبرّا هالشريط.'
                : ' بانتظار المراجعة صفوفها مش موجودة بالشيت هلق، فمحسوبة بعدّاد المراجعة وبرّا هالشريط.');
        }
        return ['total' => count($sheet), 'counts' => $counts, 'stages' => $stages, 'orphans' => $orphans,
                'notes' => $notes];
    }

    /**
     * The four KPI tiles (label, dot, link, and the one source of each number). The page and /api/overview
     * both read this list.
     */
    public const KPI_TILES = [
        'waiting' => ['label' => 'بانتظار مراجعتك', 'dot' => 'saffron', 'href' => '/catalog?mode=bulk',
                      'source' => 'automation_queue ready_for_review (QueueStats::counters, same as /api/batch-status)'],
        'published' => ['label' => 'منشورة بالشيت', 'dot' => 'teal', 'href' => '/catalog',
                        'source' => 'sheet funnel stage published (OverviewController::funnel)'],
        'not_found' => ['label' => 'ما انلقت إلها صورة', 'dot' => 'info', 'href' => '/catalog?filter=not_found',
                        'source' => 'sheet funnel stage not_found (OverviewController::funnel)'],
        'failed' => ['label' => 'أعطال مؤقتة', 'dot' => 'danger', 'href' => '/catalog?filter=failed',
                     'source' => 'sheet funnel stage failed (OverviewController::funnel)'],
    ];

    /** The four tiles. value null = its source could not be read (the page shows «—» and the reason). */
    public static function kpis(array $review, array $sheet): array
    {
        $reviewOk = ($review['status'] ?? '') === 'ok';
        $sheetOk = ($sheet['status'] ?? '') === 'ok';
        $counts = $sheetOk ? $sheet['counts'] : [];
        $waiting = $reviewOk ? (int) $review['waiting'] : null;
        $proposed = $reviewOk ? (int) ($review['proposed'] ?? 0) : 0;
        $published = $sheetOk ? (int) $counts['published'] : null;
        $notFound = $sheetOk ? (int) $counts['not_found'] : null;
        $failed = $sheetOk ? (int) $counts['failed'] : null;
        $sheetMissing = $sheetOk ? '' : ($sheet['message'] ?? 'ما قدرنا نقرأ الشيت هلق.');
        $values = [
            'waiting' => [
                'value' => $waiting,
                'note' => !$reviewOk ? ($review['message'] ?? '')
                    : ($proposed > 0 ? $proposed . ' منها مقترحة وجاهزة'
                        : ($waiting > 0 ? 'بدها تختار الصورة بنفسك' : 'ما في شي بيستناك هلق')),
                'tone' => $reviewOk && $proposed > 0 ? 'success' : 'muted',
            ],
            'published' => [
                'value' => $published,
                'note' => $sheetOk ? 'من أصل ' . QueueStats::countText((int) $sheet['total']) . ' بالشيت' : $sheetMissing,
                'tone' => 'muted',
            ],
            'not_found' => [
                'value' => $notFound,
                // with nothing searched yet «كل اللي انبحث عنه انلقى» would be true only in words
                'note' => $sheetOk ? ($notFound > 0 ? 'بدها بحث بكلمات تانية'
                    : ((int) $sheet['total'] - (int) ($counts['not_searched'] ?? 0) > 0 ? 'كل اللي انبحث عنه انلقى'
                        : 'لسا ما انبحث عن شي')) : $sheetMissing,
                'tone' => 'muted',
            ],
            'failed' => [
                'value' => $failed,
                'note' => $sheetOk ? ($failed > 0 ? 'بتنعاد لما تشغّل صفوفها من جديد' : 'ما في أعطال') : $sheetMissing,
                'tone' => $sheetOk && $failed > 0 ? 'danger' : 'muted',
            ],
        ];
        $out = [];
        foreach (self::KPI_TILES as $key => $tile) {
            $out[$key] = $tile + $values[$key];
        }
        return $out;
    }

    /** cli_bridge ops_health through the Health page's cache (same key and lifetime), or null when it fails. */
    public static function opsHealth(bool $refresh = false): ?array
    {
        if (!$refresh) {
            $cached = Cache::get(HealthController::CACHE_KEY);
            if (is_array($cached)) {
                return $cached;
            }
        }
        $result = PythonBridge::run('ops_health');
        if (($result['status'] ?? '') !== 'success') {
            return null;
        }
        Cache::put(HealthController::CACHE_KEY, $result, HealthController::CACHE_SECONDS);
        return $result;
    }

    /** ops_health alerts as banner lines: the Arabic message as the title and what it means as the text. */
    public static function alerts(array $alerts): array
    {
        $out = [];
        foreach ($alerts as $a) {
            $message = trim((string) ($a['message'] ?? ''));
            if ($message === '') {
                continue;
            }
            $out[] = ['title' => $message, 'text' => self::ALERT_HINTS[(string) ($a['code'] ?? '')] ?? ''];
        }
        return $out;
    }
}
