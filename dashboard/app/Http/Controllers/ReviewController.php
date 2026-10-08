<?php

namespace App\Http\Controllers;

use App\Services\PythonBridge;
use App\Services\QueueStats;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * المراجعة (/catalog): شاشة واحدة بوضعين، «منتج واحد» و«بالجملة» (?mode=bulk)، على هوية لقطة.
 *
 * مصدر كل رقم في الشاشة:
 * - المنتجات وصورها المرشحة واعتماداتها وأعطالها: GET /api/products-json
 *   (ProductController::reviewProducts، نفس دمج الشيت بالمرشحات الذي تقرؤه باقي الصفحات).
 * - من ينتظر قرار المراجع: صفوف الطابور بحالة ready_for_review من GET /api/review/queue-state.
 *   عددها الإجمالي من QueueStats::counters()، الدالة نفسها التي تعطي /api/batch-status قيمة ready_for_review
 *   (شارة «المراجعة» في الشريط الجانبي)، فرقم «بانتظار المراجعة» في الشاشة ورقم الشارة يأتيان من استعلام واحد.
 *   الشاشة تعدّ منتجاً «بانتظار المراجعة» فقط إذا كان صفه في الطابور ready_for_review، وصف جاهز لم يعد في الشيت
 *   يظهر في القائمة بملاحظة بدل أن يسقط من العدد.
 */
class ReviewController extends Controller
{
    public const MODES = ['single', 'bulk'];

    /**
     * قيم ?filter= المقبولة: رقاقات المراجعة، نفس المجموعة بالوضعين (core.js FILTERS). eligible: مقترحة بلا تحذير،
     * strict: مؤكدة تماماً، bg_failed: اعتمادات لم تُعزل خلفيتها.
     */
    public const FILTERS = ['all', 'proposed', 'eligible', 'strict', 'warning', 'none', 'not_found', 'failed', 'bg_failed'];

    /** حالات الطابور التي تحتاجها الشاشة؛ الصفوف المعتمدة تُعرف من رابط الشيت واعتماد resolved_products. */
    public const QUEUE_STATUSES = ['ready_for_review', 'failed', 'pending', 'processing'];

    /** أبعاد اللوحة النهائية عند غياب الإعداد (config.OUTPUT_CANVAS_SIZE في بايثون). */
    public const DEFAULT_CANVAS = 800;

    /** مهلة البحث التلقائي لمنتج لم يُبحث له بعد: التنقل السريع بالأسهم لا يطلق بحثاً مدفوعاً لكل منتج يمر عليه. */
    public const AUTO_SEARCH_DELAY_MS = 700;

    /**
     * الاعتماد بيستنى هالمدة بالصفحة قبل ما ينبعت، و«تراجع» بيرجّعه (jobs.js). ما بيضيع: لما الصفحة تختفي أو تتسكّر
     * بينبعت فوراً (fetch keepalive لنفس /api/select_image). الخادم ما بيتغيّر: بيوصله نفس الطلب بس متأخر.
     */
    public const APPROVE_UNDO_MS = 8000;

    /** منطقة الوقت اللي بتنعرض فيها الأوقات وبينحسب فيها «اليوم» (config/app.php display_timezone). */
    public static function displayTimezone(): string
    {
        $tz = (string) config('app.display_timezone', 'Asia/Dubai');
        return in_array($tz, \DateTimeZone::listIdentifiers(), true) ? $tz : 'Asia/Dubai';
    }

    public function page(Request $request)
    {
        $mode = in_array($request->query('mode'), self::MODES, true) ? (string) $request->query('mode') : 'single';
        $filter = in_array($request->query('filter'), self::FILTERS, true) ? (string) $request->query('filter') : null;
        // ?row[]=1 يصل مصفوفة: تحويلها لنص كان يرمي «Array to string conversion» (HTTP 500)
        $rowParam = $request->query('row', '');
        $rowParam = is_string($rowParam) ? $rowParam : '';
        $row = ctype_digit($rowParam) && (int) $rowParam > 0 ? (int) $rowParam : null;
        // ?reason=no_size: قائمة «بلا اقتراح» لسبب واحد (رمز السبب كما يكتبه catalog_match.explain)
        $reasonParam = $request->query('reason', '');
        $reason = is_string($reasonParam) && preg_match('/^[a-z_]{1,40}$/', $reasonParam) ? $reasonParam : null;

        $dbOnline = self::databaseOnline();
        $readyForReview = $dbOnline ? self::readyForReview() : null;

        $config = [
            'mode' => $mode,
            // ?mode= أو ?row= بالرابط: الوضع محدد. بدونهم الصفحة بتختار (آخر وضع اختاره المراجع، أو «بالجملة» لما يكون
            // في 10 صور مقترحة بلا تحذير أو أكثر)
            'modeExplicit' => in_array($request->query('mode'), self::MODES, true) || $row !== null,
            'filter' => $filter,
            'row' => $row,
            'reason' => $reason,
            'canvas' => $dbOnline ? self::canvasSize() : self::envCanvasSize(),
            'db' => $dbOnline ? 'online' : 'offline',
            'readyForReview' => $readyForReview,
            'autoSearchDelayMs' => self::AUTO_SEARCH_DELAY_MS,
            'approveUndoMs' => self::APPROVE_UNDO_MS,
            'urls' => [
                'page' => route('dashboard.catalog'),
                'products' => url('/api/products-json'),
                'queueState' => url('/api/review/queue-state'),
                'explainBackfill' => url('/api/review/explain-backfill'),
                'undoReject' => url('/api/review/undo-reject'),
                'clearCache' => url('/api/clear-products-cache'),
                'search' => url('/api/search'),
                'select' => url('/api/select_image'),
                'reject' => url('/api/reject_image'),
                'upload' => url('/api/upload_manual_image'),
                'approvalJobs' => url('/api/approval-jobs'),
                'saveCandidates' => url('/api/v1/curation/save-candidates'),
                'selectCandidate' => url('/api/v1/curation/select-candidate'),
                'retry' => url('/api/failures/retry'),
                'imageProxy' => url('/api/image-proxy'),
                'export' => route('dashboard.rich_catalog.export'),
                'run' => route('dashboard.batch_automation'),
                // «افحص النشر» بلوحة الاعتمادات اللي ما مشيت: بطاقة «فحص النشر» بصفحة الصحة
                'publishCheck' => route('dashboard.diagnostics') . '#publish-check',
                // «تجاوز عزل الخلفية» بنفس اللوحة (رصيد أو مفتاح أو حصة PhotoRoom / remove.bg): SettingsController::setBgMethod
                'bgMethod' => url('/api/settings/bg-method'),
                // سطر «النشر التلقائي لكل الماركات المؤكدة» بوضع الجملة: أرقام فئة strict من بطاقة الصحة (HealthController::reviewLanes)
                'reviewLanes' => url('/api/system/review-lanes'),
                // «فحص القص» (RecutController): الصور المنشورة على الغامق والفاتح والمربعات، رابط تحت فلاتر القائمة
                'cutoutCheck' => route('dashboard.cutout_check'),
            ],
            // طريقة عزل الخلفية الحالية ونص تأكيد التجاوز؛ null بلا قاعدة بيانات (ما في زر لأن الحفظ رح يفشل)
            'bg' => $dbOnline ? self::bgConfig() : null,
        ];

        return view('dashboard.catalog', [
            'reviewConfig' => $config,
            'reviewDbOnline' => $dbOnline,
            // شارة «المراجعة» تبدأ بالرقم نفسه الذي يعيده /api/batch-status قبل أول استعلام
            'lqReviewCount' => $readyForReview,
        ]);
    }

    /**
     * حالة صفوف الطابور التي تحدد ما ينتظر المراجعة: كل صف بحالة ready_for_review أو failed أو pending أو processing
     * (رقم الصف، sku_key، الحالة، رمز السبب، الاسم كما أُدرج). ready_for_review هو رقم الشارة نفسه (QueueStats).
     */
    public function queueState(): JsonResponse
    {
        if (!self::databaseOnline()) {
            return response()->json([
                'status' => 'unavailable',
                'error' => 'قاعدة البيانات غير متاحة: لا يمكن معرفة ما ينتظر المراجعة الآن.',
            ], 503)->header('Cache-Control', 'no-store');
        }

        $counters = QueueStats::counters();
        $rows = [];
        $status = 'success';
        $explainMissing = 0;
        try {
            if (!Schema::hasTable('automation_queue')) {
                $status = 'no_queue';
            } else {
                $columns = ['row_number', 'status', 'product_name', 'brand', 'updated_at'];
                foreach (['sku_key', 'failure_code'] as $optional) {
                    if (Schema::hasColumn('automation_queue', $optional)) {
                        $columns[] = $optional;
                    }
                }
                if (Schema::hasColumn('automation_queue', 'trace_json')) {
                    $columns = array_merge($columns, self::explainColumns());
                }
                $raw = DB::table('automation_queue')
                    ->whereIn('status', self::QUEUE_STATUSES)
                    ->orderBy('row_number')
                    ->get($columns)
                    ->map(fn ($r) => (array) $r)
                    ->all();
                $explainMissing = count(array_filter($raw, fn ($r) => (int) ($r['explain_missing'] ?? 0) === 1));
                $rows = self::withSiblingExplain(array_map([self::class, 'presentQueueRow'], $raw));
            }
        } catch (\Throwable $e) {
            return response()->json([
                'status' => 'unavailable',
                'error' => 'تعذرت قراءة طابور التشغيل من قاعدة البيانات.',
            ], 503)->header('Cache-Control', 'no-store');
        }

        return response()->json([
            'status' => $status,
            // نفس القيمة التي تعيدها /api/batch-status (ApiController::batchStatus) للشارة
            'ready_for_review' => (int) ($counters['by_status']['ready_for_review'] ?? 0),
            'by_status' => $counters['by_status'],
            'rows' => $rows,
            // صفوف حُفظت قبل أن يحسب العامل سبب «بلا اقتراح»: الشاشة تطلب حسابه مرة (explainBackfill)
            'explain_missing' => $explainMissing,
            // «اليوم: اعتمدت 87، رفضت 6» لما تخلص المراجعة
            'today' => self::todayDecisions(),
        ])->header('Cache-Control', 'no-store');
    }

    /**
     * قرارات المراجعين اليوم (من منتصف الليل بتوقيت displayTimezone) من review_decisions: approved (ومعه
     * manual_upload) و rejected. null لما الجدول مش موجود أو ما انقرأ (الشاشة بتعدّ قرارات هالجلسة بدالها).
     */
    public static function todayDecisions(): ?array
    {
        try {
            if (!Schema::hasTable('review_decisions')) {
                return null;
            }
            $midnight = (new \DateTimeImmutable('today', new \DateTimeZone(self::displayTimezone())))->getTimestamp();
            $rows = DB::table('review_decisions')
                ->where('created_at', '>=', DB::raw('FROM_UNIXTIME(' . (int) $midnight . ')'))
                ->whereIn('action', ['approved', 'manual_upload', 'rejected'])
                ->selectRaw('action, COUNT(*) AS n')
                ->groupBy('action')
                ->pluck('n', 'action')
                ->all();
        } catch (\Throwable $e) {
            return null;
        }
        return [
            'approved' => (int) ($rows['approved'] ?? 0) + (int) ($rows['manual_upload'] ?? 0),
            'rejected' => (int) ($rows['rejected'] ?? 0),
        ];
    }

    /** حالات الطابور التي يُعرض سببها: بانتظار المراجعة بلا اقتراح، أو ما انلقت. */
    public const EXPLAIN_STATUSES = ['ready_for_review', 'failed'];

    /** أطول نص من سبب «بلا اقتراح» يصل الصفحة (الجملة والحقيقة والإجراء والكلمة المقترحة). */
    public const EXPLAIN_MAX_TEXT = 600;

    /**
     * سبب «بلا اقتراح» من trace_json (outcome.explain، يكتبه العامل: catalog_match.explain) لصفوف المراجعة والفشل،
     * ومعه هل الصف حُفظ قبل أن يُحسب (له outcome بلا explain). trace نفسه لا يصل الصفحة.
     */
    private static function explainColumns(): array
    {
        $in = "status IN ('" . implode("','", self::EXPLAIN_STATUSES) . "') AND JSON_VALID(trace_json)";
        return [
            DB::raw("CASE WHEN {$in} THEN JSON_EXTRACT(trace_json, '$.outcome.explain') END AS explain_json"),
            DB::raw("CASE WHEN {$in} AND JSON_CONTAINS_PATH(trace_json, 'one', '$.outcome') "
                . "AND NOT JSON_CONTAINS_PATH(trace_json, 'one', '$.outcome.explain') THEN 1 ELSE 0 END AS explain_missing"),
        ];
    }

    /** صف طابور كما تقرؤه الشاشة (بلا payload ولا trace ولا worker)، ومعه سبب «بلا اقتراح» إن وُجد. */
    public static function presentQueueRow(array $row): array
    {
        return [
            'row_number' => (int) ($row['row_number'] ?? 0),
            'sku_key' => isset($row['sku_key']) && trim((string) $row['sku_key']) !== '' ? trim((string) $row['sku_key']) : null,
            'status' => (string) ($row['status'] ?? ''),
            'failure_code' => isset($row['failure_code']) && trim((string) $row['failure_code']) !== ''
                ? trim((string) $row['failure_code']) : null,
            'product_name' => (string) ($row['product_name'] ?? ''),
            'brand' => (string) ($row['brand'] ?? ''),
            'updated_at' => isset($row['updated_at']) ? (string) $row['updated_at'] : null,
            'explain' => self::presentExplain($row['explain_json'] ?? null),
        ];
    }

    /**
     * سبب «بلا اقتراح» كما تعرضه الصفحة، أو null: المفتاح (حروف صغيرة وشرطة سفلية فقط، يظهر في التلميح وحده) والجملة
     * العربية وقطعتاها، وملاحظات الشيت. أي حقل آخر في trace لا يصل الصفحة.
     */
    public static function presentExplain($raw): ?array
    {
        $data = is_string($raw) ? json_decode($raw, true) : $raw;
        if (!is_array($data)) {
            return null;
        }
        $code = fn ($v) => is_string($v) && preg_match('/^[a-z_]{1,40}$/', $v) ? $v : null;
        $text = fn ($v) => is_string($v) || is_numeric($v) ? mb_substr(trim((string) $v), 0, self::EXPLAIN_MAX_TEXT) : '';
        $key = $code($data['key'] ?? null);
        $sentence = $text($data['text'] ?? '');
        if ($key === null || $sentence === '') {
            return null;
        }
        $sheet = [];
        foreach (is_array($data['sheet'] ?? null) ? $data['sheet'] : [] as $issue) {
            if (!is_array($issue) || $code($issue['key'] ?? null) === null) {
                continue;
            }
            $sheet[] = array_filter([
                'key' => $issue['key'],
                'text' => $text($issue['text'] ?? ''),
                'word' => $text($issue['word'] ?? ''),
                'suggest' => $text($issue['suggest'] ?? ''),
                'known' => !empty($issue['known']),
            ], fn ($v) => $v !== '');
        }
        return [
            'key' => $key,
            'label' => $text($data['label'] ?? ''),
            'engine' => $code($data['engine'] ?? null),
            'text' => $sentence,
            'fact' => $text($data['fact'] ?? ''),
            'action' => $text($data['action'] ?? ''),
            'sheet' => $sheet,
        ];
    }

    /**
     * صف شقيق (نفس المنتج: نفس sku_key) أخذ حالته من صف المنتج الذي بُحث له، ولم يُكتب له trace: يأخذ سببه منه.
     */
    public static function withSiblingExplain(array $rows): array
    {
        $bySku = [];
        foreach ($rows as $r) {
            if ($r['explain'] !== null && $r['sku_key'] !== null && !isset($bySku[$r['sku_key'] . '|' . $r['status']])) {
                $bySku[$r['sku_key'] . '|' . $r['status']] = $r['explain'];
            }
        }
        foreach ($rows as &$r) {
            if ($r['explain'] === null && $r['sku_key'] !== null && in_array($r['status'], self::EXPLAIN_STATUSES, true)) {
                $r['explain'] = $bySku[$r['sku_key'] . '|' . $r['status']] ?? null;
            }
        }
        unset($r);
        return $rows;
    }

    /**
     * يحسب سبب «بلا اقتراح» لصفوف حُفظت قبل أن يحسبه العامل، مما حُفظ فقط (cli_bridge explain_backfill: بلا بحث وبلا
     * تكلفة، ولا يغيّر حالة أي صف ولا وقت تحديثه). الشاشة تطلبه مرة إذا قال queue-state إن صفوفاً تنقصها.
     */
    public function explainBackfill(): JsonResponse
    {
        if (!self::databaseOnline()) {
            return response()->json(['status' => 'unavailable', 'error' => 'قاعدة البيانات غير متاحة.'], 503)
                ->header('Cache-Control', 'no-store');
        }
        $result = PythonBridge::run('explain_backfill');
        if (($result['status'] ?? '') !== 'success') {
            return response()->json(['status' => 'failed', 'error' => 'ما قدرنا نحسب أسباب «بلا اقتراح» هلق.'], 500)
                ->header('Cache-Control', 'no-store');
        }
        return response()->json([
            'status' => 'success',
            'filled' => (int) ($result['filled'] ?? 0),
            'checked' => (int) ($result['checked'] ?? 0),
        ])->header('Cache-Control', 'no-store');
    }

    /**
     * «تراجع عن الرفض» (POST /api/review/undo-reject {sku_key, image_url, row_number?}): رفض بالغلط أو للتجربة. الجسر
     * (cli_bridge undo_reject) يشيل صف rejected_images واحد لهالمنتج وهالصورة ويعلّم قرار الرفض بـ undone_at، فما بينحسب
     * بالإحصائيات ولا بالتعلّم، و«دوّر مرة ثانية» بيقدر يلاقي الصورة. CSRF متل كل POST. الشيت والاعتماد ما بيتغيروا.
     */
    public function undoReject(Request $request): JsonResponse
    {
        $sku = $request->input('sku_key');
        $url = $request->input('image_url');
        $row = $request->input('row_number');
        if (!is_string($sku) || trim($sku) === '' || mb_strlen(trim($sku)) > 64
            || !is_string($url) || !preg_match('#^https?://#i', trim($url)) || mb_strlen($url) > 4000
            || ($row !== null && $row !== '' && !ctype_digit((string) $row))) {
            return response()->json(['status' => 'failed', 'error' => 'ناقص المنتج أو رابط الصورة.'], 422)
                ->header('Cache-Control', 'no-store');
        }
        if (!self::databaseOnline()) {
            return response()->json(['status' => 'unavailable', 'error' => 'قاعدة البيانات غير متاحة.'], 503)
                ->header('Cache-Control', 'no-store');
        }
        $params = ['sku_key' => trim($sku), 'image_url' => trim($url)];
        if ($row !== null && $row !== '') {
            $params['row_number'] = (int) $row;
        }
        $result = PythonBridge::run('undo_reject', $params);
        $status = (string) ($result['status'] ?? '');
        if ($status === 'success') {
            ProductController::forgetProductCaches();
            return response()->json([
                'status' => 'success',
                'sku_key' => $params['sku_key'],
                'image_url' => $params['image_url'],
                'reason_code' => $result['reason_code'] ?? null,
                'decision_undone' => (bool) ($result['decision_undone'] ?? false),
                'still_rejected' => (bool) ($result['still_rejected'] ?? false),
                'candidates_restored' => (int) ($result['candidates_restored'] ?? 0),
                'message' => 'رجعت الصورة للاقتراحات. «دوّر مرة ثانية» بيقدر يلاقيها.',
            ])->header('Cache-Control', 'no-store');
        }
        if ($status === 'not_found') {
            ProductController::forgetProductCaches();
            return response()->json(['status' => 'not_found', 'error' => 'ما في رفض مسجل لهالصورة لهالمنتج.'], 404)
                ->header('Cache-Control', 'no-store');
        }
        return response()->json(['status' => 'failed', 'error' => 'ما قدرنا نتراجع عن الرفض هلق.'], 500)
            ->header('Cache-Control', 'no-store');
    }

    /** عدد صفوف الطابور الجاهزة للمراجعة: نفس حساب /api/batch-status. */
    public static function readyForReview(): int
    {
        $counters = QueueStats::counters();
        return (int) ($counters['by_status']['ready_for_review'] ?? 0);
    }

    /**
     * أبعاد اللوحة النهائية كما يستعملها النشر: system_settings.output_canvas_size (تتجاوز .env في config.py)،
     * وإلا OUTPUT_CANVAS_SIZE، وإلا 800. الاعتماد من هذه الشاشة يرسل 0×0، أي هذه اللوحة المربعة.
     */
    public static function canvasSize(): int
    {
        try {
            $value = DB::table('system_settings')->where('key', 'output_canvas_size')->value('value');
            $size = self::validCanvas($value);
            if ($size !== null) {
                return $size;
            }
        } catch (\Throwable $e) {
            // جدول الإعدادات غير موجود بعد
        }
        return self::envCanvasSize();
    }

    /** {method, previous, previous_label, off, confirm} لزر «تجاوز عزل الخلفية» بلوحة الاعتمادات، أو null. */
    public static function bgConfig(): ?array
    {
        $state = SettingsController::currentBgState();
        return $state === null ? null : $state + ['confirm' => SettingsController::BG_SKIP_CONFIRM];
    }

    public static function envCanvasSize(): int
    {
        return self::validCanvas(env('OUTPUT_CANVAS_SIZE')) ?? self::DEFAULT_CANVAS;
    }

    private static function validCanvas($value): ?int
    {
        if (!is_numeric($value)) {
            return null;
        }
        $size = (int) $value;
        return $size >= 100 && $size <= 5000 ? $size : null;
    }

    private static function databaseOnline(): bool
    {
        try {
            DB::select('SELECT 1');
            return true;
        } catch (\Throwable $e) {
            return false;
        }
    }
}
