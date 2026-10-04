<?php

namespace App\Http\Controllers;

use App\Services\PythonBridge;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Cache;

/**
 * الصحة والتكلفة (لقطة).
 *
 * - بطاقات الخدمات من آخر نتيجة محفوظة لفحص الاتصالات (temp/diagnostics_last.json). فتح الصفحة لا يشغّل الفحص
 *   أبداً (استعلام Serper مدفوع + طلب PhotoRoom)؛ الفحص بزر صريح فقط (ProductController::runDiagnosticsJson).
 * - «عمليات البحث»: التجميع في بايثون (ops_health.summarize، مغطى بـ pytest) عبر إجراء cli_bridge للقراءة فقط
 *   'ops_health'، فاللوحة وتنبيه العامل يتشاركان نفس القواعد. النتيجة تُخزن دقيقة واحدة؛ ?refresh=1 يعيد القراءة.
 * - «السجل»: آخر أسطر سجل الأتمتة وسجل لوحة التحكم وسجل التشغيل الليلي (temp/nightly). ملف غير موجود حالة
 *   عادية (exists=false) وليس 404، والقيم السرية المحفوظة تُحجب من الأسطر قبل إرسالها.
 */
class HealthController extends Controller
{
    public const CACHE_KEY = 'ops_health_v1';
    public const CACHE_SECONDS = 60;

    /** آخر أسطر السجل التي تُرسل للصفحة، وأقصى ما يُقرأ من نهاية الملف. */
    public const LOG_LINES = 200;
    public const LOG_TAIL_BYTES = 131072;

    /** خدمات اللوحة بترتيب التصميم، وما تفعله كل خدمة (الملاحظة حين لا يوجد ما هو أدق). */
    public const SERVICES = [
        'google_sheets' => ['name' => 'Google Sheet', 'note' => 'ملف المنتجات'],
        'serper' => ['name' => 'Serper', 'note' => 'المصدر الأساسي للصور'],
        'gemini' => ['name' => 'Gemini', 'note' => 'قراءة الملصق'],
        'photoroom' => ['name' => 'PhotoRoom', 'note' => 'عزل الخلفية'],
        'cloudinary' => ['name' => 'Cloudinary', 'note' => 'رفع الصور النهائية'],
    ];

    /** خدمات اختيارية: تظهر في سطر واحد تحت البطاقات فقط إذا كانت مضبوطة. */
    public const OPTIONAL_SERVICES = [
        'proxy' => 'البروكسي',
        'google_search' => 'Google Custom Search (قديم)',
    ];

    public function page()
    {
        $last = ProductController::lastDiagnostics();
        return view('dashboard.diagnostics', [
            'lastDiagnostics' => self::publicResult($last),
            'services' => self::serviceCards($last, self::serviceContext()),
            'optional' => self::optionalServices($last),
            'checkedAt' => self::checkedAt($last),
            'allOk' => is_array($last) ? ($last['all_ok'] ?? null) : null,
            'lastRun' => self::lastRunCard(self::lastRunRow()),
        ]);
    }

    // ------------------------------------------------------------------
    // Last run (package P4b): temp/nightly/last_report.json, which run_report.py writes after every run (the
    // same report as the run_history row; this page reads no table directly)
    // ------------------------------------------------------------------

    /** النتيجة -> [النص، اللون] (run_report.OUTCOME_TEXT بلا رموز). */
    public const RUN_OUTCOMES = [
        'done' => ['خلص', 'success'],
        'skipped' => ['ما بلّش لأنو في تشغيل تاني شغّال', 'muted'],
        'handed_over' => ['سلّم الطابور لتشغيل تاني بعد انقطاع', 'warning'],
        'stopped' => ['وقف قبل ما يخلص الطابور', 'warning'],
        'outage' => ['انقطاع', 'danger'],
        'failed' => ['فشل', 'danger'],
    ];
    public const RUN_TRIGGERS = ['nightly' => 'التشغيل الليلي', 'dashboard' => 'تشغيل من اللوحة', 'manual' => 'تشغيل يدوي'];

    /**
     * آخر تقرير تشغيل (temp/nightly/last_report.json، يُكتب حتى عندما لا ترد قاعدة البيانات) بشكل صف run_history:
     * {run_trigger, started_at, outcome, stop_reason, attempts, report_json, ...counts}، أو null إن لم يوجد.
     */
    public static function lastRunRow(?string $file = null): ?array
    {
        $file = $file ?? base_path('../temp/nightly/last_report.json');
        $report = is_file($file) ? json_decode((string) @file_get_contents($file), true) : null;
        if (!is_array($report)) {
            return null;
        }
        $counts = is_array($report['counts'] ?? null) ? $report['counts'] : [];
        return ['run_trigger' => $report['trigger'] ?? null, 'started_at' => $report['started_at'] ?? null,
                'outcome' => $report['outcome'] ?? null, 'stop_reason' => $report['stop_reason'] ?? null,
                'attempts' => $report['attempts'] ?? 1, 'report_json' => $report] + $counts;
    }

    /**
     * بطاقة «آخر تشغيل»: {title, tone, when, summary} من صف run_history (أو التقرير)، أو null.
     * الأرقام كما حُفظت عند نهاية التشغيل؛ السبب من reason_text الذي كتبه run_report.py.
     */
    public static function lastRunCard(?array $row): ?array
    {
        if (!$row) {
            return null;
        }
        $report = $row['report_json'] ?? null;
        $report = is_string($report) ? json_decode($report, true) : $report;
        $report = is_array($report) ? $report : [];
        [$label, $tone] = self::RUN_OUTCOMES[(string) ($row['outcome'] ?? '')] ?? ['انتهى', 'muted'];
        $trigger = self::RUN_TRIGGERS[(string) ($row['run_trigger'] ?? '')] ?? 'تشغيل';
        $reason = trim((string) ($report['reason_text'] ?? ($row['stop_reason'] ?? '')));
        $title = $trigger . ': ' . $label . ($reason !== '' && ($row['outcome'] ?? '') !== 'skipped' ? ' (' . $reason . ')' : '');
        $started = strtotime((string) ($row['started_at'] ?? ''));
        $parts = [];
        foreach (['ready_for_review' => 'بانتظار المراجعة', 'auto_published' => 'انتشر تلقائياً',
                  'not_found' => 'ما انلقت', 'failed' => 'فشل', 'pending_left' => 'بقي بالانتظار'] as $key => $text) {
            if (is_numeric($row[$key] ?? null) && ((int) $row[$key] > 0 || $key === 'ready_for_review')) {
                $parts[] = $text . ' ' . (int) $row[$key];
            }
        }
        // آخر «محاولة» في handed_over هي التشغيل الآخر الذي تولى الطابور، لا إعادة تشغيل
        $retries = (int) ($row['attempts'] ?? 1) - 1 - (($row['outcome'] ?? '') === 'handed_over' ? 1 : 0);
        if ($retries > 0) {
            $parts[] = 'انعاد التشغيل ' . ($retries === 1 ? 'مرة' : ($retries === 2 ? 'مرتين' : $retries . ' مرات'))
                . ' بعد انقطاع';
        }
        return [
            'title' => $title,
            'tone' => $tone,
            'when' => $started ? date('Y-m-d H:i', $started) : '',
            'summary' => $parts ? implode(' · ', $parts) : (($row['outcome'] ?? '') === 'skipped' ? '' : 'الأرقام مش متاحة.'),
        ];
    }

    public function summary(Request $request)
    {
        if (!$request->boolean('refresh')) {
            $cached = Cache::get(self::CACHE_KEY);
            if (is_array($cached)) {
                return response()->json($cached)->header('Cache-Control', 'no-store');
            }
        }

        $result = PythonBridge::run('ops_health');
        if (($result['status'] ?? '') !== 'success') {
            // Only the fixed message: the raw bridge output stays in the Laravel log.
            return response()->json([
                'status' => 'error',
                'error' => (string) ($result['error'] ?? 'ops_health failed'),
            ], 500)->header('Cache-Control', 'no-store');
        }
        Cache::put(self::CACHE_KEY, $result, self::CACHE_SECONDS);
        return response()->json($result)->header('Cache-Control', 'no-store');
    }

    /** آخر أسطر سجل الأتمتة (temp/pipeline.log الذي يكتبه التشغيل). */
    public function pipelineLog()
    {
        return self::logResponse('pipeline', base_path('../temp/pipeline.log'));
    }

    /** آخر أسطر سجل لوحة التحكم (storage/logs/laravel.log). */
    public function laravelLog()
    {
        return self::logResponse('laravel', storage_path('logs/laravel.log'));
    }

    /**
     * آخر أسطر سجل التشغيل الليلي (temp/nightly/nightly_YYYY-MM-DD.log الذي يكتبه scripts/run_nightly.py):
     * أحدث ليلة، أو ?date=YYYY-MM-DD. لا يُقبل اسم ملف ولا مسار من الطلب: التاريخ فقط بصيغة ثابتة.
     */
    public function nightlyLog(Request $request)
    {
        // القيمة كما وصلت: ?date[]=x مصفوفة، وتحويلها إلى نص كان خطأ 500 بدل 400
        [$code, $body] = self::nightlyPayload(base_path('../temp/nightly'), $request->query('date', ''),
            SettingsController::secretValues());
        return response()->json($body, $code, [], JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE)
            ->header('Cache-Control', 'no-store');
    }

    // ------------------------------------------------------------------
    // Service cards
    // ------------------------------------------------------------------

    /**
     * ما تحتاجه الصفحة من آخر نتيجة فقط: الوقت، all_ok، وحالة كل خدمة وتفاصيلها (بلا raw_logs).
     */
    public static function publicResult(?array $last): ?array
    {
        if (!is_array($last)) {
            return null;
        }
        $services = [];
        foreach ((array) ($last['services'] ?? []) as $key => $service) {
            if (!is_string($key) || !is_array($service)) {
                continue;
            }
            $services[$key] = [
                'status' => (string) ($service['status'] ?? ''),
                'is_critical' => (bool) ($service['is_critical'] ?? false),
                'details' => mb_substr(mb_convert_encoding((string) ($service['details'] ?? ''), 'UTF-8', 'UTF-8'), 0, 2000),
            ];
        }
        return [
            'checked_at' => is_string($last['checked_at'] ?? null) ? $last['checked_at'] : null,
            'all_ok' => isset($last['all_ok']) ? (bool) $last['all_ok'] : null,
            'services' => $services,
        ];
    }

    /** وقت آخر فحص (epoch) أو null. */
    public static function checkedAt(?array $last): ?int
    {
        if (!is_array($last)) {
            return null;
        }
        return strtotime((string) ($last['checked_at'] ?? '')) ?: null;
    }

    /**
     * بطاقات الخدمات الخمس من آخر نتيجة: {key, name, state, tone, note, details}.
     * لا نتيجة محفوظة: «لسا ما انفحص» لكل خدمة، وليس «يعمل».
     */
    public static function serviceCards(?array $last, array $context = []): array
    {
        $cards = [];
        foreach (self::SERVICES as $key => $meta) {
            $service = is_array($last) ? ($last['services'][$key] ?? null) : null;
            $status = is_array($service) ? (string) ($service['status'] ?? '') : '';
            if (!is_array($last)) {
                [$state, $tone] = ['لسا ما انفحص', 'muted'];
            } elseif (!is_array($service) || $status === '') {
                [$state, $tone] = ['ما انفحص', 'muted'];
            } else {
                [$state, $tone] = match ($status) {
                    'online' => [$key === 'google_sheets' ? 'متصل' : 'يعمل', 'success'],
                    'disabled' => ['مش مفعّل', 'muted'],
                    default => ['ما بيرد', 'danger'],
                };
            }
            $details = $tone === 'danger' && is_array($service) ? trim((string) ($service['details'] ?? '')) : '';
            $cards[] = [
                'key' => $key,
                'name' => $meta['name'],
                'state' => $state,
                'tone' => $tone,
                'note' => self::serviceNote($key, $context) ?? $meta['note'],
                'details' => mb_substr($details, 0, 2000),
            ];
        }
        return $cards;
    }

    /** ملاحظة أدق من السياق إن وُجد: تبويب الشيت وعدد صفوف آخر قراءة، ونموذج Gemini. */
    public static function serviceNote(string $key, array $context): ?string
    {
        if ($key === 'google_sheets') {
            $parts = [];
            $tab = trim((string) ($context['sheet_tab'] ?? ''));
            if ($tab !== '') {
                $parts[] = 'تبويب ' . $tab;
            }
            if (is_int($context['sheet_rows'] ?? null)) {
                $parts[] = self::rows($context['sheet_rows']) . ' بآخر قراءة';
            }
            return $parts ? implode(' · ', $parts) : null;
        }
        if ($key === 'gemini') {
            $model = trim((string) ($context['gemini_model'] ?? ''));
            return $model !== '' ? $model : null;
        }
        return null;
    }

    /** «صف واحد»، «صفين»، «5 صفوف»، «100 صف» (نفس plural في public/js/health.js). */
    public static function rows(int $n): string
    {
        if ($n === 1) {
            return 'صف واحد';
        }
        if ($n === 2) {
            return 'صفين';
        }
        return $n . ($n >= 3 && $n <= 10 ? ' صفوف' : ' صف');
    }

    /** الخدمات الاختيارية المضبوطة فقط (ليست «مش مفعّل»): [{name, state, tone}]. */
    public static function optionalServices(?array $last): array
    {
        $out = [];
        foreach (self::OPTIONAL_SERVICES as $key => $name) {
            $service = is_array($last) ? ($last['services'][$key] ?? null) : null;
            $status = is_array($service) ? (string) ($service['status'] ?? '') : '';
            if ($status === '' || $status === 'disabled') {
                continue;
            }
            $out[] = ['name' => $name, 'state' => $status === 'online' ? 'يعمل' : 'ما بيرد',
                      'tone' => $status === 'online' ? 'success' : 'warning'];
        }
        return $out;
    }

    /** سياق الملاحظات بلا أي استدعاء لبايثون: الإعدادات المحفوظة وكاش صفوف الشيت إن وُجد. */
    private static function serviceContext(): array
    {
        $context = [];
        $env = SettingsController::envValues(['SPREADSHEET_TAB_NAME', 'GEMINI_MODEL']);
        $context['sheet_tab'] = $env['SPREADSHEET_TAB_NAME'] ?? '';
        $stored = SettingsController::stored() ?? [];
        $model = trim((string) ($stored['gemini_model']['value'] ?? ''));
        $context['gemini_model'] = $model !== '' ? $model : ($env['GEMINI_MODEL'] ?? 'gemini-3.1-flash-lite');
        try {
            $cached = Cache::get(ProductController::SHEET_ROWS_CACHE_KEY);
            if (is_array($cached) && is_array($cached['rows'] ?? null)) {
                $context['sheet_rows'] = count($cached['rows']);
            }
        } catch (\Throwable $e) {
            // cache store unavailable: no row count
        }
        return $context;
    }

    // ------------------------------------------------------------------
    // Logs
    // ------------------------------------------------------------------

    private static function logResponse(string $kind, string $path)
    {
        [$code, $body] = self::logPayload($kind, $path, SettingsController::secretValues());
        return response()->json($body, $code, [], JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE)
            ->header('Cache-Control', 'no-store');
    }

    /**
     * [HTTP code, body] of a log tail. A missing file is a normal state (200, exists=false), never a 404;
     * stored secret values are masked in every line.
     */
    public static function logPayload(string $kind, string $path, array $secrets = []): array
    {
        clearstatcache(true, $path);
        if (!is_file($path)) {
            return [200, ['status' => 'success', 'kind' => $kind, 'exists' => false, 'lines' => [], 'updated_at' => null]];
        }
        $lines = self::tailLines($path, self::LOG_LINES, self::LOG_TAIL_BYTES);
        if ($lines === null) {
            return [500, ['status' => 'error', 'kind' => $kind, 'exists' => true, 'error' => 'ما قدرنا نقرأ ملف السجل.']];
        }
        $lines = array_map(fn ($line) => self::redact($line, $secrets), $lines);
        return [200, ['status' => 'success', 'kind' => $kind, 'exists' => true, 'lines' => $lines,
                      'updated_at' => @filemtime($path) ?: null]];
    }

    /** تواريخ سجلات التشغيل الليلي الموجودة في $dir (الأحدث أولاً)، من أسماء الملفات بالصيغة الثابتة فقط. */
    public static function nightlyDates(string $dir): array
    {
        $dates = [];
        foreach (@scandir($dir) ?: [] as $name) {
            if (preg_match('/^nightly_(\d{4}-\d{2}-\d{2})\.log$/', $name, $m) && is_file($dir . DIRECTORY_SEPARATOR . $name)) {
                $dates[] = $m[1];
            }
        }
        rsort($dates);
        return $dates;
    }

    /**
     * [HTTP code, body] لسجل ليلة واحدة: $date فارغ = أحدث ليلة. أي تاريخ ليس نصاً بصيغة YYYY-MM-DD (ومنه ?date[]=x) يُرفض (400)،
     * والملف المقروء يجب أن يبقى داخل $dir بعد حل الروابط (لا path traversal). لا سجل بعد: exists=false (200).
     * يضيف dates (الليالي المتاحة) و date (الليلة المعروضة).
     */
    public static function nightlyPayload(string $dir, $date, array $secrets = []): array
    {
        if ($date === null) {
            $date = '';
        }
        if (!is_string($date)) {
            return [400, ['status' => 'error', 'kind' => 'nightly', 'error' => 'تاريخ السجل غير صالح.']];
        }
        $date = trim($date);
        $dates = self::nightlyDates($dir);
        if ($date !== '' && !preg_match('/^\d{4}-\d{2}-\d{2}$/', $date)) {
            return [400, ['status' => 'error', 'kind' => 'nightly', 'error' => 'تاريخ السجل غير صالح.']];
        }
        $date = $date !== '' ? $date : ($dates[0] ?? '');
        if ($date === '') {
            return [200, ['status' => 'success', 'kind' => 'nightly', 'exists' => false, 'lines' => [],
                          'updated_at' => null, 'dates' => [], 'date' => null]];
        }
        $path = $dir . DIRECTORY_SEPARATOR . 'nightly_' . $date . '.log';
        $realDir = realpath($dir);
        $realPath = realpath($path);
        if ($realPath !== false && ($realDir === false || strpos($realPath, $realDir . DIRECTORY_SEPARATOR) !== 0)) {
            return [400, ['status' => 'error', 'kind' => 'nightly', 'error' => 'تاريخ السجل غير صالح.']];
        }
        [$code, $body] = self::logPayload('nightly', $path, $secrets);
        return [$code, $body + ['dates' => $dates, 'date' => $date]];
    }

    /** آخر $max سطر غير فارغ من نهاية الملف (بلا قراءة الملف كله)، أو null إذا تعذرت القراءة. */
    public static function tailLines(string $path, int $max, int $bytes): ?array
    {
        $size = @filesize($path);
        $handle = @fopen($path, 'rb');
        if ($size === false || $handle === false) {
            return null;
        }
        $start = max(0, $size - $bytes);
        if ($start > 0) {
            fseek($handle, $start);
        }
        $chunk = (string) stream_get_contents($handle);
        fclose($handle);
        $lines = preg_split('/\r\n|\n|\r/', $chunk);
        if ($start > 0 && count($lines) > 1) {
            array_shift($lines);            // the first line was cut by the seek
        }
        $lines = array_values(array_filter(array_map('rtrim', $lines), fn ($l) => trim($l) !== ''));
        return array_slice($lines, -$max);
    }

    /**
     * يحجب القيم السرية (SettingsController::secretList)، وبيانات الدخول داخل الروابط (user:pass@host)، وقيمة أي
     * معامل key= أو api_key= في رابط (مثل ?key= في روابط Google)، بنفس قواعد حجب فحص الاتصالات في بايثون.
     * سطر فيه بايتات ليست UTF-8 يُصلح أولاً (تُستبدل بـ ?) حتى لا يختفي السطر كله.
     */
    public static function redact(string $line, array $secrets): string
    {
        $line = mb_convert_encoding($line, 'UTF-8', 'UTF-8');
        usort($secrets, fn ($a, $b) => mb_strlen((string) $b) <=> mb_strlen((string) $a));
        foreach ($secrets as $secret) {
            $secret = (string) $secret;
            if (mb_strlen($secret) >= 6) {
                $line = str_replace($secret, '[محجوب]', $line);
            }
        }
        $line = preg_replace('#(://)[^/\s:@]+:[^/\s@]+@#u', '$1[محجوب]@', $line);
        $line = $line === null ? null : preg_replace('/(?<![\w-])((?:api[_-]?)?key=)[^&\s\'"]+/iu', '$1[محجوب]', $line);
        return $line ?? '[سطر ما قدرنا نعرضه]';
    }
}
