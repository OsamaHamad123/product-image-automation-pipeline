<?php

namespace App\Http\Controllers;

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

    /** قيم ?filter= المقبولة (رقاقات قائمة المراجعة). */
    public const FILTERS = ['all', 'proposed', 'warning', 'none', 'not_found', 'failed'];

    /** حالات الطابور التي تحتاجها الشاشة؛ الصفوف المعتمدة تُعرف من رابط الشيت واعتماد resolved_products. */
    public const QUEUE_STATUSES = ['ready_for_review', 'failed', 'pending', 'processing'];

    /** أبعاد اللوحة النهائية عند غياب الإعداد (config.OUTPUT_CANVAS_SIZE في بايثون). */
    public const DEFAULT_CANVAS = 800;

    /** مهلة البحث التلقائي لمنتج لم يُبحث له بعد: التنقل السريع بالأسهم لا يطلق بحثاً مدفوعاً لكل منتج يمر عليه. */
    public const AUTO_SEARCH_DELAY_MS = 700;

    public function page(Request $request)
    {
        $mode = in_array($request->query('mode'), self::MODES, true) ? (string) $request->query('mode') : 'single';
        $filter = in_array($request->query('filter'), self::FILTERS, true) ? (string) $request->query('filter') : null;
        // ?row[]=1 يصل مصفوفة: تحويلها لنص كان يرمي «Array to string conversion» (HTTP 500)
        $rowParam = $request->query('row', '');
        $rowParam = is_string($rowParam) ? $rowParam : '';
        $row = ctype_digit($rowParam) && (int) $rowParam > 0 ? (int) $rowParam : null;

        $dbOnline = self::databaseOnline();
        $readyForReview = $dbOnline ? self::readyForReview() : null;

        $config = [
            'mode' => $mode,
            'filter' => $filter,
            'row' => $row,
            'canvas' => $dbOnline ? self::canvasSize() : self::envCanvasSize(),
            'db' => $dbOnline ? 'online' : 'offline',
            'readyForReview' => $readyForReview,
            'autoSearchDelayMs' => self::AUTO_SEARCH_DELAY_MS,
            'urls' => [
                'page' => route('dashboard.catalog'),
                'products' => url('/api/products-json'),
                'queueState' => url('/api/review/queue-state'),
                'clearCache' => url('/api/clear-products-cache'),
                'search' => url('/api/search'),
                'select' => url('/api/select_image'),
                'reject' => url('/api/reject_image'),
                'upload' => url('/api/upload_manual_image'),
                'saveCandidates' => url('/api/v1/curation/save-candidates'),
                'selectCandidate' => url('/api/v1/curation/select-candidate'),
                'retry' => url('/api/failures/retry'),
                'imageProxy' => url('/api/image-proxy'),
                'export' => route('dashboard.rich_catalog.export'),
                'run' => route('dashboard.batch_automation'),
            ],
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
                $rows = DB::table('automation_queue')
                    ->whereIn('status', self::QUEUE_STATUSES)
                    ->orderBy('row_number')
                    ->get($columns)
                    ->map(fn ($r) => self::presentQueueRow((array) $r))
                    ->all();
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
        ])->header('Cache-Control', 'no-store');
    }

    /** صف طابور كما تقرؤه الشاشة (بلا payload ولا trace ولا worker). */
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
        ];
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
