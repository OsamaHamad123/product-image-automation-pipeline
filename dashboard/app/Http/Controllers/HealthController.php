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
    /** «دقة الاقتراحات الحقيقية»: review_stats.lanes، بنفس تخزين ops_health المؤقت. */
    public const LANES_CACHE_KEY = 'review_lanes_v3';
    public const LANES = ['strict', 'unsure', 'other'];

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
        $publish = self::publicPublishCheck(self::lastPublishCheck(), SettingsController::secretValues());
        $bg = SettingsController::currentBgState();
        return view('dashboard.diagnostics', [
            'lastDiagnostics' => self::publicResult($last),
            'services' => self::serviceCards($last, self::serviceContext()),
            'optional' => self::optionalServices($last),
            'checkedAt' => self::checkedAt($last),
            'allOk' => is_array($last) ? ($last['all_ok'] ?? null) : null,
            'lastRun' => self::lastRunCard(self::lastRunRow()),
            'localIndex' => LocalIndexController::card(),
            'lastPublishCheck' => $publish,
            'publish' => self::publishCheckView($publish),
            'bg' => $bg,
            'bgView' => self::bgSkipView($publish, $bg),
            'bgConfirm' => SettingsController::BG_SKIP_CONFIRM,
        ]);
    }

    // ------------------------------------------------------------------
    // «تجاوز عزل الخلفية» on the «فحص النشر» card: when the processing step failed on PhotoRoom / remove.bg credit, key
    // or quota (publish_check.BG_SKIP_CODE_RE), a button sets bg_removal_method = none through POST
    // /api/settings/bg-method; with the method none the card says background removal is off and offers to restore the
    // previous method. The same view in public/js/health.js (bgView).
    // ------------------------------------------------------------------

    /** نفس publish_check.BG_SKIP_CODE_RE و BG_SKIP_RE في health.js و review/core.js. */
    public const BG_SKIP_PATTERN = '/^(photoroom|removebg)_(no_key|401|402|403|429)$/';
    public const BG_PROVIDERS = ['photoroom' => 'PhotoRoom', 'removebg' => 'remove.bg'];

    /** «رصيد PhotoRoom خلص أو الاشتراك موقوف» لرمز ينفع معه التجاوز، وإلا ''. */
    public static function bgProblem(string $code): string
    {
        if (!preg_match(self::BG_SKIP_PATTERN, $code, $m)) {
            return '';
        }
        $name = self::BG_PROVIDERS[$m[1]];
        return match ($m[2]) {
            '402' => 'رصيد ' . $name . ' خلص أو الاشتراك موقوف',
            '429' => $name . ' رافض طلبات كتير هلق',
            'no_key' => 'مفتاح ' . $name . ' مش محفوظ',
            default => $name . ' رفض المفتاح',
        };
    }

    /**
     * {state: hidden | offer | off, text, restore, restore_label}: offer = آخر فحص فشل بخطوة العزل برمز رصيد / مفتاح /
     * حصة والطريقة مش none؛ off = عزل الخلفية متوقف (زر «رجّع عزل الخلفية (…)»). $bg = SettingsController::bgState()
     * أو null (قاعدة البيانات ما ردّت: ولا زر، لأنو الحفظ رح يفشل).
     */
    public static function bgSkipView(?array $result, ?array $bg): array
    {
        $hidden = ['state' => 'hidden', 'text' => '', 'restore' => '', 'restore_label' => ''];
        if (!is_array($bg) || !is_string($bg['method'] ?? null) || $bg['method'] === '') {
            return $hidden;
        }
        if ($bg['method'] === 'none') {
            $previous = (string) ($bg['previous'] ?? '');
            $previous = isset(SettingsController::BG_METHOD_LABELS[$previous]) && $previous !== 'none' ? $previous : 'photoroom';
            return ['state' => 'off',
                    'text' => 'عزل الخلفية متوقف: الصور اللي بتعتمدها بتنتشر متل ما هي على لوحة بيضا، بدون أي طلب عزل مدفوع.',
                    'restore' => $previous,
                    'restore_label' => 'رجّع عزل الخلفية (' . SettingsController::BG_METHOD_LABELS[$previous] . ')'];
        }
        foreach ((array) ($result['steps'] ?? []) as $step) {
            if (is_array($step) && ($step['key'] ?? '') === 'process' && ($step['status'] ?? '') === 'fail') {
                $problem = self::bgProblem((string) ($step['code'] ?? ''));
                if ($problem !== '') {
                    return ['state' => 'offer',
                            'text' => $problem . '، فكل اعتماد رح يفشل بنفس الشكل لحد ما ينحل. فيك تتجاوز عزل الخلفية هلق: '
                                . 'الصور بتنتشر متل ما هي على لوحة بيضا.',
                            'restore' => '', 'restore_label' => ''];
                }
            }
        }
        return $hidden;
    }

    // ------------------------------------------------------------------
    // «فحص النشر»: the publish chain rehearsed on a test image (publish_check.py through the bridge action
    // 'publish_check'). Only the button runs it (it may cost one background-removal call); opening the page shows
    // the last saved result (temp/publish_check_last.json) and calls nothing.
    // ------------------------------------------------------------------

    /** آخر نتيجة يحفظها publish_check.py. */
    public const PUBLISH_CHECK_FILE = '../temp/publish_check_last.json';

    /**
     * حد أقصى يُنهى بعده الفحص: مهل خطوات بايثون (publish_check.STEP_TIMEOUTS) مجموعها 380 ثانية، وفوقها هامش
     * لاختيار الصورة وبدء بايثون. خادم PHP المدمج يخدم طلباً واحداً في كل مرة، فالفحص لا يبقى معلقاً بلا حد.
     */
    public const PUBLISH_CHECK_KILL_SECONDS = 420;

    public const PUBLISH_CHECK_STATUSES = ['ok', 'warn', 'fail', 'skipped'];
    public const PUBLISH_CHECK_STEPS = ['download', 'process', 'upload', 'sheet'];

    /** آخر نتيجة لفحص النشر (temp/publish_check_last.json) أو null. */
    public static function lastPublishCheck(?string $file = null): ?array
    {
        $file = $file ?? base_path(self::PUBLISH_CHECK_FILE);
        $decoded = is_file($file) ? json_decode((string) @file_get_contents($file), true) : null;
        return is_array($decoded) && is_array($decoded['steps'] ?? null) ? $decoded : null;
    }

    /**
     * ما تعرضه الصفحة من نتيجة فحص النشر فقط: الحالة العامة والملخص والوقت وكل خطوة {key, status, ms, title_ar,
     * detail_ar, action_ar, code}، بعد حجب القيم السرية مرة ثانية (بايثون يحجبها أولاً). أي حقل آخر لا يمر.
     */
    public static function publicPublishCheck(?array $result, array $secrets = []): ?array
    {
        if (!is_array($result) || !is_array($result['steps'] ?? null)) {
            return null;
        }
        $text = fn ($value, int $max = 1200) => self::redact(mb_substr(is_scalar($value) ? (string) $value : '', 0, $max), $secrets);
        $steps = [];
        foreach ($result['steps'] as $step) {
            if (!is_array($step) || !in_array($step['key'] ?? null, self::PUBLISH_CHECK_STEPS, true)) {
                continue;
            }
            $status = (string) ($step['status'] ?? '');
            $steps[] = [
                'key' => (string) $step['key'],
                'status' => in_array($status, self::PUBLISH_CHECK_STATUSES, true) ? $status : 'fail',
                'ms' => is_numeric($step['ms'] ?? null) ? max(0, (int) $step['ms']) : 0,
                'title_ar' => $text($step['title_ar'] ?? '', 120),
                'detail_ar' => $text($step['detail_ar'] ?? ''),
                'action_ar' => $text($step['action_ar'] ?? '', 400),
                'code' => $text($step['code'] ?? '', 120),
            ];
        }
        $overall = (string) ($result['overall'] ?? '');
        $sample = is_array($result['sample'] ?? null) ? $result['sample'] : [];
        return [
            'ok' => (bool) ($result['ok'] ?? false),
            'overall' => in_array($overall, ['ok', 'warn', 'fail'], true) ? $overall : 'fail',
            'started_at' => is_string($result['started_at'] ?? null) ? $result['started_at'] : null,
            'finished_at' => is_string($result['finished_at'] ?? null) ? $result['finished_at'] : null,
            'duration_ms' => is_numeric($result['duration_ms'] ?? null) ? max(0, (int) $result['duration_ms']) : 0,
            'summary_ar' => $text($result['summary_ar'] ?? ''),
            'failed_step' => in_array($result['failed_step'] ?? null, self::PUBLISH_CHECK_STEPS, true) ? $result['failed_step'] : null,
            'sheet_tab' => isset($result['sheet_tab']) && is_scalar($result['sheet_tab']) ? $text($result['sheet_tab'], 200) : null,
            'sample' => [
                'kind' => ($sample['kind'] ?? '') === 'review' ? 'review' : 'bundled',
                'product_name' => $text($sample['product_name'] ?? '', 300),
                'row_number' => is_numeric($sample['row_number'] ?? null) ? (int) $sample['row_number'] : null,
            ],
            'run_active' => (bool) ($result['run_active'] ?? false),
            'notes' => array_values(array_map(fn ($n) => $text($n, 600),
                array_filter((array) ($result['notes'] ?? []), 'is_scalar'))),
            'steps' => $steps,
        ];
    }

    /** حالة الخطوة -> [الكلمة، اللون، الأيقونة] (نفس PUBLISH_STATUS في public/js/health.js). */
    public const PUBLISH_STEP_STATUS = [
        'ok' => ['نجحت', 'success', 'check'],
        'warn' => ['فيها ملاحظة', 'warning', 'exclamation'],
        'fail' => ['ما زبطت', 'danger', 'x'],
        'skipped' => ['ما انفحصت', 'muted', 'minus'],
        'idle' => ['لسا ما انفحصت', 'muted', 'minus'],
    ];
    public const PUBLISH_STEP_TITLES = ['download' => 'تنزيل الصورة', 'process' => 'عزل الخلفية والمعالجة',
                                        'upload' => 'الرفع على Cloudinary', 'sheet' => 'الكتابة بالشيت'];
    public const PUBLISH_OVERALL = ['ok' => 'success', 'warn' => 'warning', 'fail' => 'danger'];

    /** «1.2 ث» من ميلي ثانية (أعشار مقربة بالعدد الصحيح، مثل seconds() في health.js). */
    public static function stepSeconds(int $ms): string
    {
        $tenths = (int) round(max(0, $ms) / 100);
        return intdiv($tenths, 10) . '.' . ($tenths % 10) . ' ث';
    }

    /**
     * بطاقة «فحص النشر» من آخر نتيجة (publicPublishCheck): {state: never | ok | warn | fail, tone, summary, sample,
     * notes, steps: [{key, status, label, tone, icon, title, time, detail, action, code}], when}. لا نتيجة: الخطوات
     * الأربع «لسا ما انفحصت». نفس publishView في health.js (والوقت هناك نسبي: «اليوم 09:12»).
     */
    public static function publishCheckView(?array $result): array
    {
        $byKey = [];
        foreach ((array) ($result['steps'] ?? []) as $step) {
            if (is_array($step) && isset($step['key'])) {
                $byKey[$step['key']] = $step;
            }
        }
        $steps = [];
        foreach (self::PUBLISH_STEP_TITLES as $key => $title) {
            $step = $byKey[$key] ?? null;
            $status = is_array($step) && isset(self::PUBLISH_STEP_STATUS[$step['status'] ?? '']) ? $step['status'] : 'idle';
            [$label, $tone, $icon] = self::PUBLISH_STEP_STATUS[$status];
            $ran = in_array($status, ['ok', 'warn', 'fail'], true);
            $steps[] = [
                'key' => $key, 'status' => $status, 'label' => $label, 'tone' => $tone, 'icon' => $icon,
                'title' => is_array($step) && ($step['title_ar'] ?? '') !== '' ? (string) $step['title_ar'] : $title,
                'time' => $ran ? self::stepSeconds((int) ($step['ms'] ?? 0)) : '',
                'detail' => is_array($step) ? (string) ($step['detail_ar'] ?? '') : '',
                'action' => $ran && $status !== 'ok' ? (string) ($step['action_ar'] ?? '') : '',
                'code' => is_array($step) ? (string) ($step['code'] ?? '') : '',
            ];
        }
        if (!is_array($result)) {
            return ['state' => 'never', 'tone' => 'muted', 'summary' => 'لسا ما انعمل فحص للنشر.', 'sample' => '',
                    'notes' => [], 'steps' => $steps, 'when' => ''];
        }
        $overall = (string) ($result['overall'] ?? 'fail');
        $sample = is_array($result['sample'] ?? null) ? $result['sample'] : [];
        $name = trim((string) ($sample['product_name'] ?? ''));
        $row = $sample['row_number'] ?? null;
        $sampleText = ($sample['kind'] ?? '') === 'review' && $name !== ''
            ? 'الصورة: ' . $name . (is_int($row) ? ' (صف ' . $row . ')' : '')
            : 'الصورة: الصورة التجريبية';
        $finished = strtotime((string) ($result['finished_at'] ?? ''));
        return [
            'state' => isset(self::PUBLISH_OVERALL[$overall]) ? $overall : 'fail',
            'tone' => self::PUBLISH_OVERALL[$overall] ?? 'danger',
            'summary' => (string) ($result['summary_ar'] ?? ''),
            'sample' => $sampleText,
            'notes' => array_values(array_map('strval', (array) ($result['notes'] ?? []))),
            'steps' => $steps,
            'when' => $finished ? date('Y-m-d H:i', $finished) : '',
        ];
    }

    /** GET /api/system/publish-check: آخر نتيجة محفوظة (بلا أي فحص جديد). */
    public function lastPublishCheckJson()
    {
        $result = self::publicPublishCheck(self::lastPublishCheck(), SettingsController::secretValues());
        return response()->json(['status' => 'success', 'result' => $result], 200, [], JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE)
            ->header('Cache-Control', 'no-store');
    }

    /**
     * POST /api/system/publish-check: يشغّل «فحص النشر» (cli_bridge.py publish_check) ويرجع نتيجته. مثل فحص
     * الاتصالات: المخرجات إلى ملف مؤقت لا إلى أنبوب (قراءة الأنابيب بلا انتظار لا تعمل على ويندوز)، وحد أقصى
     * PUBLISH_CHECK_KILL_SECONDS يُنهى بعده الفحص.
     */
    public function runPublishCheck()
    {
        @set_time_limit(self::PUBLISH_CHECK_KILL_SECONDS + 30);
        $outFile = null;
        try {
            putenv('PYTHONUTF8=1');
            putenv('PYTHONIOENCODING=utf-8');
            $command = [PythonBridge::pythonPath(), PythonBridge::bridgePath(), 'publish_check', base64_encode('{}')];
            $outFile = tempnam(sys_get_temp_dir(), 'pubcheck');
            $nullDevice = strncasecmp(PHP_OS, 'WIN', 3) === 0 ? 'NUL' : '/dev/null';
            $process = proc_open($command, [
                0 => ['file', $nullDevice, 'r'],
                1 => ['file', $outFile, 'w'],
                2 => ['file', $nullDevice, 'w'],
            ], $pipes, base_path('..'));
            if (!is_resource($process)) {
                throw new \RuntimeException('proc_open failed');
            }

            $timedOut = false;
            $killAt = microtime(true) + self::PUBLISH_CHECK_KILL_SECONDS;
            while (proc_get_status($process)['running']) {
                if (microtime(true) >= $killAt) {
                    proc_terminate($process, 9);
                    $timedOut = true;
                    break;
                }
                usleep(250000);
            }
            proc_close($process);

            if ($timedOut) {
                return self::publishCheckError('فحص النشر أخد أكتر من ' . intdiv(self::PUBLISH_CHECK_KILL_SECONDS, 60)
                    . ' دقايق فوقفناه. في خدمة ما بترد: جرّب «فحص الاتصالات الآن» لتعرف أي وحدة.', 504);
            }
            $decoded = PythonBridge::decodeOutput((string) @file_get_contents($outFile));
            $result = is_array($decoded) ? self::publicPublishCheck($decoded, SettingsController::secretValues()) : null;
            if ($result !== null) {
                return response()->json(['status' => 'success', 'result' => $result], 200, [],
                    JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE)->header('Cache-Control', 'no-store');
            }
            $error = is_array($decoded) ? trim((string) ($decoded['error'] ?? '')) : '';
            return self::publishCheckError(preg_match('/[\x{0600}-\x{06FF}]/u', $error) ? $error
                : 'فحص النشر ما رجّع نتيجة: تأكد من بيئة بايثون ومكتباتها ثم أعد الفحص.', 500);
        } catch (\Throwable $e) {
            \Illuminate\Support\Facades\Log::error('publish check failed to start: ' . $e->getMessage());
            return self::publishCheckError('ما قدرنا نشغّل فحص النشر: تأكد من بيئة بايثون ثم أعد الفحص.', 500);
        } finally {
            if ($outFile && file_exists($outFile)) {
                @unlink($outFile);
            }
        }
    }

    private static function publishCheckError(string $message, int $code)
    {
        return response()->json(['status' => 'failed', 'error' => $message], $code, [],
            JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE)->header('Cache-Control', 'no-store');
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
        // صور انعزلت بطريقة محلية (rembg) لأن رصيد مزوّد العزل خلص: run_report.BG_FALLBACK_TEXT
        if (is_numeric($report['bg_fallback'] ?? null) && (int) $report['bg_fallback'] > 0) {
            $parts[] = 'انعزل بطريقة محلية لأن رصيد مزوّد العزل خلص ' . (int) $report['bg_fallback'];
        }
        // صور نُشرت تلقائياً بدون عزل الخلفية (المالك أوقفه بالإعدادات): run_report.BG_SKIPPED_TEXT
        if (is_numeric($report['bg_skipped'] ?? null) && (int) $report['bg_skipped'] > 0) {
            $parts[] = 'انتشر بدون عزل الخلفية ' . (int) $report['bg_skipped'];
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

    /**
     * GET /api/system/review-lanes: لكل فئة اختيار (catalog_match.decide.pick_lane) الاقتراحات المراجعة والمعتمد منها
     * والحد المضمون وكم اعتماداً بقي لتجهز (more_needed)، من review_stats (نفس أرقام تبويب «النشر الآلي»)، مخزّنة
     * CACHE_SECONDS متل ops-health. شاشة المراجعة بالجملة تقرأ منها سطر التقدم نحو النشر الآلي (بلا نداء جديد).
     */
    public function reviewLanes(Request $request)
    {
        if (!$request->boolean('refresh')) {
            $cached = Cache::get(self::LANES_CACHE_KEY);
            if (is_array($cached)) {
                return response()->json($cached)->header('Cache-Control', 'no-store');
            }
        }
        $result = PythonBridge::run('review_stats');
        if (($result['status'] ?? '') !== 'success') {
            return response()->json(['status' => 'error', 'error' => 'جسر بايثون أو قاعدة البيانات ما ردّ.'], 500)
                ->header('Cache-Control', 'no-store');
        }
        $body = self::lanesPayload($result);
        Cache::put(self::LANES_CACHE_KEY, $body, self::CACHE_SECONDS);
        return response()->json($body)->header('Cache-Control', 'no-store');
    }

    /** حالات جاهزية الفئة (local_cache_db.brand_status)؛ أي شي تاني من الجسر بينقرأ needs_reviews. */
    public const LANE_STATUSES = ['ready', 'needs_reviews', 'low_precision'];

    /**
     * الأرقام اللي بتحتاجها البطاقة وسطر التقدم بشاشة المراجعة بس (لا أسماء ماركات ولا روابط)، للقراءة فقط. لكل فئة:
     * prechecked (الاقتراحات المراجعة)، accepted (المعتمد منها)، lower_bound (حد ويلسون 95% الأدنى، null بلا مراجعات)،
     * status (ready | needs_reviews | low_precision)، ready، reviews_needed (عدد الاقتراحات المراجعة الكلي اللي بتجهز
     * عنده الفئة لو انقبل كل الجاي، local_cache_db.reviews_needed؛ null = ما بتوصل بحد معقول) و more_needed (الباقي منه:
     * اعتمادات متتالية بلا رفض). فئة strict كمان: enabled (مفتاح «النشر الآلي لكل الماركات المؤكدة» متل ما بيقرأه
     * العامل، null لما الجسر ما قاله) و publishing (enabled وجاهزة: عم ينشر لحاله).
     */
    public static function lanesPayload(array $stats): array
    {
        $lanes = [];
        foreach (self::LANES as $lane) {
            $row = (array) (((array) ($stats['lanes'] ?? []))[$lane] ?? []);
            $status = in_array($row['status'] ?? null, self::LANE_STATUSES, true) ? $row['status'] : 'needs_reviews';
            $lanes[$lane] = [
                'prechecked' => (int) ($row['prechecked'] ?? 0),
                'accepted' => (int) ($row['accepted'] ?? 0),
                'lower_bound' => is_numeric($row['lower_bound'] ?? null) ? (float) $row['lower_bound'] : null,
                'status' => $status,
                'ready' => $status === 'ready',
                'reviews_needed' => is_numeric($row['reviews_needed'] ?? null) ? max(0, (int) $row['reviews_needed']) : null,
                // كم اعتماداً متتالياً بلا رفض بعد لتجهز الفئة (local_cache_db.more_needed)؛ null = الدقة أقل من العتبة
                'more_needed' => is_numeric($row['more_needed'] ?? null) ? max(0, (int) $row['more_needed']) : null,
            ];
        }
        $switch = array_key_exists('strict_lane_enabled', $stats) ? (bool) $stats['strict_lane_enabled'] : null;
        $lanes['strict']['enabled'] = $switch;
        $lanes['strict']['publishing'] = $switch === true && $lanes['strict']['ready'];
        return ['status' => 'success', 'lanes' => $lanes,
                'unlaned' => (int) ($stats['unlaned_prechecked'] ?? 0)];
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
