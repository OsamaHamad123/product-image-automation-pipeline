<?php

namespace App\Http\Controllers;

use App\Services\PythonBridge;
use App\Services\QueueStats;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;

/**
 * «جهّز لقطة»: معالج التجهيز لأول مرة (GET /setup، resources/views/dashboard/setup.blade.php، public/js/setup.js).
 *
 * خمس خطوات، لكل وحدة فحص حي وتصليح واضح:
 *   1. sheet    ملف الاعتماد موجود، رابط الشيت وتبويبه محفوظين، والشيت بينقرا وفيه عمودي اسم المنتج والماركة. الحفظ
 *               والتحقق نفس /api/sheet/save (ApiController::sheetParams)، والقراءة نفس جسر sheet-preview.
 *   2. keys     Serper و Gemini وعزل الخلفية (PhotoRoom أو remove.bg، أو طريقة محلية بلا مفتاح) و Cloudinary: محفوظ أو لا
 *               (SettingsController::keyRows) وزر فحص هو نفس فحص الاتصالات (ProductController::runDiagnosticsJson). قيمة
 *               المفتاح ما بتنعرض ولا بتنكتب بأي مكان: المفاتيح بتنحط من حقول الإعدادات نفسها.
 *   3. brands   تبويب Brands Mapping موجود وكم ماركة فيه (جسر setup_brands، قراءة بس)، ورابط «عبّي جدول الماركات».
 *   4. publish  «فحص النشر» نفسه (HealthController، publish_check): الخطوة بتقرأ آخر نتيجة محفوظة.
 *   5. run      أول تشغيل صغير لـ 5 صفوف (POST /api/run-all نفسه بفلتر صفوف) وبعده رابط للمراجعة.
 *
 * التقدّم بينحفظ بـ system_settings.setup_wizard (JSON): نتيجة فحص الشيت والماركات، الخطوات المتخطاة، بداية أول تشغيل،
 * و done_at. الباقي بينحسب كل مرة من الملفات والإعدادات (آخر فحص اتصالات، آخر فحص نشر، آخر تقرير تشغيل)، فالمعالج ما
 * بيكذب لما شي يتغيّر من مكان تاني. ما في شي بيتسكّر بسببه: الرئيسية بتعرض بطاقة «جهّز لقطة» بس لما التجهيز ناقص،
 * والتركيبة المجهّزة أصلاً (في تشغيل خلص، ملف الاعتماد والمفاتيح موجودين) ما بتشوفها أبداً.
 */
class SetupController extends Controller
{
    /** مفتاح التقدّم بـ system_settings (config.load_db_config ما بيقرأه). */
    public const STATE_KEY = 'setup_wizard';

    /** الخطوات بالترتيب: الرقم، العنوان، وسطر بيقول ليش. */
    public const STEPS = [
        'sheet' => ['n' => 1, 'title' => 'ربط الشيت', 'lead' => 'المنتجات بتنقرا من شيت Google، وروابط الصور النهائية بتنكتب فيه.'],
        'keys' => ['n' => 2, 'title' => 'المفاتيح', 'lead' => 'البحث عن الصور وقراءة الملصق وعزل الخلفية والرفع، كل وحدة بمفتاحها.'],
        'brands' => ['n' => 3, 'title' => 'جدول الماركات', 'lead' => 'جدول الماركات بيساعد البحث يعرف كل ماركة ومنافسينها، فبيقلّ الغلط.'],
        'publish' => ['n' => 4, 'title' => 'بروفة النشر', 'lead' => 'منجرّب النشر كامل على صورة تجريبية بدون ما نلمس منتجاتك.'],
        'run' => ['n' => 5, 'title' => 'أول تشغيل صغير', 'lead' => 'مندوّر على صور 5 منتجات بس، وبعدين بتراجعها بنفسك.'],
    ];

    /** حالة الخطوة -> [الكلمة، اللون]. */
    public const STATUS = [
        'ok' => ['تمام', 'success'],
        'todo' => ['لسا', 'muted'],
        'fail' => ['بدها تصليح', 'danger'],
        'skipped' => ['تخطّيتها', 'muted'],
        'running' => ['شغّال', 'info'],
    ];

    /** أعمدة الشيت اللي البحث ما بيمشي بلاها (نفس COLUMNS required بـ public/js/settings.js). */
    public const SHEET_REQUIRED = ['name' => 'اسم المنتج', 'brand' => 'الماركة'];

    /** صفوف أول تشغيل. */
    public const FIRST_RUN_ROWS = 5;

    /** نتائج تشغيل بتعني إنو التشغيل اشتغل لآخره (HealthController::RUN_OUTCOMES). */
    public const RUN_FINISHED = ['done', 'handed_over', 'stopped'];
    public const RUN_FAILED = ['failed', 'outage'];

    /** الرجوع لصفحة الإعدادات: مفاتيح وتبويب المعالجة. */
    public const KEYS_HREF = '/settings?tab=keys';
    public const BRANDS_HREF = '/batch-automation#run-brands';

    /** ملاحظة كلفة زر فحص المفاتيح (نفس فحص الاتصالات بصفحة الصحة). */
    public const KEYS_TEST_NOTE = 'بيستعمل استعلام Serper واحد وطلب PhotoRoom واحد، وما بياخد أكتر من 45 ثانية.';

    // ------------------------------------------------------------------
    // Pages and endpoints
    // ------------------------------------------------------------------

    /** GET /setup (?step=sheet|keys|brands|publish|run|ready): بيفتح على أول خطوة ناقصة إذا ما انطلبت خطوة. */
    public function page(Request $request)
    {
        $facts = self::facts();
        $setup = self::view($facts);
        $asked = $request->query('step');
        $active = is_string($asked) && (isset(self::STEPS[$asked]) || $asked === 'ready') ? $asked : ($setup['current'] ?? 'ready');
        return view('dashboard.setup', [
            'setup' => $setup,
            'active' => $active,
            'sheet' => $facts['sheet'],
            'keysNote' => self::KEYS_TEST_NOTE,
        ]);
    }

    /** GET /api/setup/state: نفس بيانات الصفحة كـ JSON. */
    public function state()
    {
        return self::json(['status' => 'success'] + self::view(self::facts()));
    }

    /**
     * POST /api/setup/check {step, test?}: الفحص الحي لخطوة وحدة، وبيرجع الحالة كاملة. sheet: قراءة الشيت المحفوظ (جسر
     * sheet-preview)؛ keys: بلا test بيعيد الحساب بس، مع test بيشغّل فحص الاتصالات؛ brands: جسر setup_brands؛ publish:
     * آخر نتيجة «فحص النشر» (الزر نفسه بيشغّله من /api/system/publish-check)؛ run: خطة أول تشغيل (أي 5 صفوف).
     */
    public function check(Request $request)
    {
        $step = is_string($request->input('step')) ? $request->input('step') : '';
        if (!isset(self::STEPS[$step])) {
            return self::json(['status' => 'failed', 'error' => 'ما عرفنا أي خطوة بدك تفحص. حدّث الصفحة وجرّب مرة تانية.'], 422);
        }
        $facts = self::facts();
        if (!$facts['db']) {
            return self::json(['status' => 'failed', 'error' => self::DB_DOWN], 503);
        }
        $extra = [];
        $diagnostics = null;
        if ($step === 'sheet') {
            $result = self::sheetCheck($facts['sheet']);
            if (!self::remember('sheet', $result)) {
                return self::json(['status' => 'failed', 'error' => self::NOT_SAVED], 503);
            }
        } elseif ($step === 'brands') {
            $result = self::brandsCheck(PythonBridge::run('setup_brands'), $facts['sheet']);
            if (!self::remember('brands', $result)) {
                return self::json(['status' => 'failed', 'error' => self::NOT_SAVED], 503);
            }
        } elseif ($step === 'keys' && $request->boolean('test')) {
            // نفس فحص الاتصالات بصفحة الصحة (بيحفظ temp/diagnostics_last.json اللي الخطوة بتقرأ منه)
            $response = app(ProductController::class)->runDiagnosticsJson();
            $data = $response->getData(true);
            if (!is_array($data) || !is_array($data['services'] ?? null)) {
                $error = is_array($data) ? trim((string) ($data['error'] ?? '')) : '';
                return self::json(['status' => 'failed', 'error' => preg_match('/[\x{0600}-\x{06FF}]/u', $error) ? $error
                    : 'ما خلص فحص المفاتيح: الخادم ما رجّع نتيجة. جرّب مرة تانية.'], 500);
            }
            $diagnostics = $data;
        } elseif ($step === 'run') {
            $extra['plan'] = self::firstRunPlan();
        }
        return self::json(['status' => 'success'] + self::view(self::facts($diagnostics)) + $extra);
    }

    /**
     * POST /api/setup/progress {action, step?, rows?}: skip / unskip خطوة، done (خلّصت التجهيز)، reopen (رجّع افتحه)،
     * run_started (أول تشغيل بلّش بهالصفوف).
     */
    public function progress(Request $request)
    {
        $action = is_string($request->input('action')) ? $request->input('action') : '';
        $step = is_string($request->input('step')) ? $request->input('step') : '';
        $state = self::readState();
        if ($state === null) {
            return self::json(['status' => 'failed', 'error' => self::DB_DOWN], 503);
        }
        $now = time();
        switch ($action) {
            case 'skip':
            case 'unskip':
                if (!isset(self::STEPS[$step])) {
                    return self::json(['status' => 'failed', 'error' => 'ما عرفنا أي خطوة.'], 422);
                }
                $state['skipped'][$step] = $action === 'skip' ? $now : null;
                $state['skipped'] = array_filter($state['skipped']);
                break;
            case 'done':
                $state['done_at'] = $now;
                break;
            case 'reopen':
                $state['done_at'] = null;
                break;
            case 'run_started':
                $rows = QueueStats::parseRowFilter(is_string($request->input('rows')) ? $request->input('rows') : '');
                if ($rows['error'] !== '' || $rows['ranges'] === null) {
                    return self::json(['status' => 'failed', 'error' => 'الصفوف مش مفهومة.'], 422);
                }
                $state['checks']['run'] = ['status' => 'started', 'at' => $now, 'rows' => $rows['text']];
                break;
            default:
                return self::json(['status' => 'failed', 'error' => 'ما عرفنا شو بدك تعمل. حدّث الصفحة وجرّب مرة تانية.'], 422);
        }
        if (!self::writeState($state)) {
            return self::json(['status' => 'failed', 'error' => self::NOT_SAVED], 503);
        }
        return self::json(['status' => 'success'] + self::view(self::facts()));
    }

    public const DB_DOWN = 'قاعدة البيانات مش متاحة هلق، فالتقدّم ما بينحفظ. شغّلها وحدّث الصفحة.';
    public const NOT_SAVED = 'ما انحفظ التقدّم: قاعدة البيانات ما ردّت. جرّب كمان شوي.';

    private static function json(array $body, int $code = 200)
    {
        return response()->json($body, $code, [], JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE)
            ->header('Cache-Control', 'no-store');
    }

    // ------------------------------------------------------------------
    // Facts (files, settings, the database: no Python and nothing paid)
    // ------------------------------------------------------------------

    /**
     * كل اللي بتحتاجه الخطوات: {db, state, sheet (SettingsController::sheetData), keys (keyRows للمزودين الأربعة +
     * google_sheet), bg_method, removebg_key, publish (آخر «فحص النشر» محجوب)، last_run (آخر تقرير تشغيل)}.
     */
    public static function facts(?array $diagnostics = null): array
    {
        $stored = SettingsController::stored();
        // $diagnostics: نتيجة فحص الاتصالات اللي خلص هلق (زر «افحص المفاتيح»)، وإلا آخر نتيجة محفوظة
        $diagnostics = $diagnostics ?? ProductController::lastDiagnostics();
        $publish = null;
        try {
            $publish = HealthController::publicPublishCheck(HealthController::lastPublishCheck(), SettingsController::secretValues());
        } catch (\Throwable $e) {
            // ما في نتيجة مقروءة: الخطوة بتقول «لسا»
        }
        return [
            'db' => $stored !== null,
            'state' => self::decodeState($stored[self::STATE_KEY]['value'] ?? null),
            'sheet' => SettingsController::sheetData(),
            'keys' => SettingsController::keyRows($stored ?? [], $diagnostics),
            'bg_method' => SettingsController::currentBgMethod($stored['bg_removal_method']['value'] ?? ''),
            'removebg_key' => SettingsController::envHas('REMOVE_BG_API_KEY'),
            'publish' => $publish,
            'last_run' => HealthController::lastRunRow(),
        ];
    }

    /** التقدّم المحفوظ بشكل ثابت: {checks: {sheet, brands, run}, skipped: {step: ts}, done_at}. */
    public static function decodeState($raw): array
    {
        $data = is_string($raw) && $raw !== '' ? json_decode($raw, true) : null;
        $data = is_array($data) ? $data : [];
        $checks = [];
        foreach ((array) ($data['checks'] ?? []) as $key => $check) {
            if (in_array($key, ['sheet', 'brands', 'run'], true) && is_array($check)) {
                $checks[$key] = $check;
            }
        }
        $skipped = [];
        foreach ((array) ($data['skipped'] ?? []) as $key => $at) {
            if (isset(self::STEPS[$key]) && is_numeric($at) && (int) $at > 0) {
                $skipped[$key] = (int) $at;
            }
        }
        return ['checks' => $checks, 'skipped' => $skipped,
                'done_at' => is_numeric($data['done_at'] ?? null) && (int) $data['done_at'] > 0 ? (int) $data['done_at'] : null];
    }

    /** التقدّم من قاعدة البيانات، أو null إذا ما ردّت. */
    public static function readState(): ?array
    {
        try {
            $raw = DB::table('system_settings')->where('key', self::STATE_KEY)->value('value');
        } catch (\Throwable $e) {
            return null;
        }
        return self::decodeState($raw);
    }

    private static function writeState(array $state): bool
    {
        // {} وليس [] لما ما في شي (نفس الشكل اللي decodeState بيقرأه)
        $value = json_encode(['checks' => (object) ($state['checks'] ?? []), 'skipped' => (object) ($state['skipped'] ?? []),
                              'done_at' => $state['done_at'] ?? null], JSON_UNESCAPED_UNICODE);
        try {
            DB::table('system_settings')->updateOrInsert(['key' => self::STATE_KEY],
                ['value' => $value, 'updated_at' => now()]);
            return true;
        } catch (\Throwable $e) {
            return false;
        }
    }

    /** نتيجة فحص (sheet أو brands) بتنحفظ مع باقي التقدّم. */
    private static function remember(string $step, array $result): bool
    {
        $state = self::readState();
        if ($state === null) {
            return false;
        }
        $state['checks'][$step] = $result;
        return self::writeState($state);
    }

    /** بصمة الشيت المحفوظ (الرابط والتبويب): فحص لشيت تاني ما بيوصف الشيت الحالي. */
    public static function sheetFingerprint(array $sheet): string
    {
        return substr(sha1(trim((string) ($sheet['url'] ?? '')) . "\n" . trim((string) ($sheet['tab'] ?? ''))), 0, 16);
    }

    // ------------------------------------------------------------------
    // Live checks
    // ------------------------------------------------------------------

    /**
     * فحص الشيت المحفوظ: بلا ملف اعتماد ما في شي ينفحص؛ وإلا قراءة أول صف وأول 5 صفوف عبر الجسر (sheet-preview، نفس
     * «معاينة» بالإعدادات). {status: ok | fail, at, for, error: credentials | open | empty | columns | '', headers, missing}.
     */
    public static function sheetCheck(array $sheet, ?array $preview = null, ?int $now = null): array
    {
        $out = ['status' => 'fail', 'at' => $now ?? time(), 'for' => self::sheetFingerprint($sheet), 'error' => '',
                'headers' => 0, 'missing' => []];
        if (!($sheet['credentials']['exists'] ?? false)) {
            return ['error' => 'credentials'] + $out;
        }
        if ($preview === null) {
            $preview = PythonBridge::run('sheet-preview', ['spreadsheet_url' => (string) ($sheet['url'] ?? ''),
                                                           'tab_name' => (string) ($sheet['tab'] ?? '')]);
        }
        if (($preview['status'] ?? '') !== 'success') {
            return ['error' => 'open'] + $out;
        }
        $headers = is_array($preview['headers'] ?? null) ? $preview['headers'] : [];
        if ($headers === []) {
            return ['error' => 'empty'] + $out;
        }
        $columns = is_array($preview['columns'] ?? null) ? $preview['columns'] : [];
        $missing = [];
        foreach (self::SHEET_REQUIRED as $key => $label) {
            if (!is_int($columns[$key] ?? null) || $columns[$key] < 0) {
                $missing[] = $key;
            }
        }
        return ['status' => $missing ? 'fail' : 'ok', 'error' => $missing ? 'columns' : '', 'headers' => count($headers),
                'missing' => $missing] + $out;
    }

    /** نتيجة جسر setup_brands كما بتنحفظ: {status: ok | todo | fail, at, for, found, brands, error}. */
    public static function brandsCheck(array $result, array $sheet, ?int $now = null): array
    {
        $out = ['at' => $now ?? time(), 'for' => self::sheetFingerprint($sheet)];
        if (($result['status'] ?? '') !== 'success') {
            return ['status' => 'fail', 'found' => false, 'brands' => 0, 'error' => 'open'] + $out;
        }
        $found = ($result['found'] ?? false) === true;
        $brands = $found && is_numeric($result['brands'] ?? null) ? max(0, (int) $result['brands']) : 0;
        return ['status' => $found && $brands > 0 ? 'ok' : 'todo', 'found' => $found, 'brands' => $brands, 'error' => ''] + $out;
    }

    /**
     * خطة أول تشغيل: أول FIRST_RUN_ROWS صفوف بالشيت بلا رابط صورة وما عم تستنى مراجعة، وأرقام RunController::buildPlan
     * لهالصفوف (كم رح ينبحث، وكم صف باقي بالطابور من قبل رح يمشي معهم). {status, rows, count, plan, message}.
     */
    public static function firstRunPlan(): array
    {
        $sheet = ProductController::sheetRows();
        if ($sheet === null) {
            return ['status' => 'error', 'message' => 'ما قدرنا نقرأ صفوف الشيت هلق: كمّل خطوة «ربط الشيت» وجرّب مرة تانية.'];
        }
        $queue = QueueStats::queueByRow();
        if ($queue === null) {
            return ['status' => 'error', 'message' => self::DB_DOWN];
        }
        return self::planFor($sheet, $queue);
    }

    /** firstRunPlan كدالة صرفة (الشيت والطابور برا). */
    public static function planFor(array $sheet, array $queue, int $n = self::FIRST_RUN_ROWS): array
    {
        $rows = self::firstRows($sheet, $queue, $n);
        if ($rows === []) {
            return ['status' => 'empty', 'rows' => '', 'count' => 0, 'leftovers' => 0,
                    'message' => 'كل صفوف الشيت إلها صور أو عم تستنى مراجعتك: ما في شي جديد ندوّر عليه. فيك تخلّص التجهيز.'];
        }
        $filter = self::rowFilterText($rows);
        $plan = RunController::buildPlan($sheet, $queue, 'rows', '', $filter, false);
        $count = (int) $plan['to_search'];
        $leftovers = (int) $plan['leftovers'];
        $message = 'رح ندوّر على صور ' . self::products($count) . ' من الشيت (' . (count($rows) === 1 ? 'الصف ' : 'الصفوف ')
            . $filter . ').';
        if ($leftovers > 0) {
            $message .= ' وبيمشي معهم كمان اللي باقي بالطابور من تشغيل قبل (' . self::products($leftovers) . ').';
        }
        return ['status' => 'success', 'rows' => $filter, 'count' => $count, 'leftovers' => $leftovers,
                'message' => $message];
    }

    /** أول $n صفوف (بترتيب الشيت) بلا رابط صورة، ومش عم تستنى مراجعة ولا معتمدة بالطابور. */
    public static function firstRows(array $sheet, array $queue, int $n = self::FIRST_RUN_ROWS): array
    {
        $rows = [];
        foreach ($sheet as $prod) {
            $row = (int) ($prod['row_number'] ?? 0);
            if ($row <= 0 || trim((string) ($prod['product_name'] ?? '')) === '') {
                continue;
            }
            $link = trim((string) ($prod['existing_image_link'] ?? ''));
            if ($link !== '' || !empty($prod['needs_review'])) {
                continue;
            }
            $status = (string) (($queue[$row] ?? [])['status'] ?? '');
            if (in_array($status, ['ready_for_review', 'completed', 'processing'], true)) {
                continue;
            }
            $rows[] = $row;
        }
        sort($rows);
        return array_slice(array_values(array_unique($rows)), 0, max(1, $n));
    }

    /** [2, 3, 4, 9] -> «2-4, 9» (نفس صيغة فلتر الصفوف بصفحة التشغيل). */
    public static function rowFilterText(array $rows): string
    {
        sort($rows);
        $parts = [];
        $start = $prev = null;
        foreach ($rows as $row) {
            if ($start !== null && $row === $prev + 1) {
                $prev = $row;
                continue;
            }
            if ($start !== null) {
                $parts[] = $start === $prev ? (string) $start : $start . '-' . $prev;
            }
            $start = $prev = $row;
        }
        if ($start !== null) {
            $parts[] = $start === $prev ? (string) $start : $start . '-' . $prev;
        }
        return implode(', ', $parts);
    }

    /** «منتج واحد»، «منتجين»، «5 منتجات»، «12 منتج». */
    public static function products(int $n): string
    {
        if ($n === 1) {
            return 'منتج واحد';
        }
        if ($n === 2) {
            return 'منتجين';
        }
        return $n . ($n >= 3 && $n <= 10 ? ' منتجات' : ' منتج');
    }

    // ------------------------------------------------------------------
    // Views (pure: everything comes from facts())
    // ------------------------------------------------------------------

    /**
     * {db, done, done_at, current, ready, progress: {ok, total}, steps: [step]}. step: {key, n, title, lead, status,
     * label, tone, summary, rows: [{key, label, state, tone, fix, href, href_label}], skipped, ...خاص بالخطوة}.
     */
    public static function view(array $facts, ?int $now = null): array
    {
        $state = $facts['state'];
        $steps = [
            self::sheetStep($facts),
            self::keysStep($facts),
            self::brandsStep($facts),
            self::publishStep($facts),
            self::runStep($facts),
        ];
        $current = null;
        $ok = 0;
        foreach ($steps as &$step) {
            $skipped = isset($state['skipped'][$step['key']]);
            $step['skipped'] = $skipped && $step['status'] !== 'ok';
            if ($step['skipped']) {
                $step['status'] = 'skipped';
            }
            [$step['label'], $step['tone']] = self::STATUS[$step['status']];
            if (in_array($step['status'], ['ok', 'skipped'], true)) {
                $ok += $step['status'] === 'ok' ? 1 : 0;
            } elseif ($current === null) {
                $current = $step['key'];
            }
        }
        unset($step);
        return [
            'db' => (bool) $facts['db'],
            'done' => $state['done_at'] !== null,
            'done_at' => $state['done_at'],
            'current' => $current,
            'ready' => $current === null,
            'progress' => ['ok' => $ok, 'total' => count($steps)],
            'steps' => $steps,
        ];
    }

    private static function step(string $key, string $status, string $summary, array $rows, array $extra = []): array
    {
        return array_merge(['key' => $key, 'n' => self::STEPS[$key]['n'], 'title' => self::STEPS[$key]['title'],
                            'lead' => self::STEPS[$key]['lead'], 'status' => $status, 'summary' => $summary,
                            'rows' => $rows], $extra);
    }

    private static function row(string $key, string $label, string $state, string $tone, string $fix = '',
                                string $href = '', string $hrefLabel = ''): array
    {
        return ['key' => $key, 'label' => $label, 'state' => $state, 'tone' => $tone, 'fix' => $fix,
                'href' => $href, 'href_label' => $hrefLabel];
    }

    /** الخطوة 1: ملف الاعتماد، الرابط، القراءة، والأعمدة الأساسية. */
    public static function sheetStep(array $facts): array
    {
        $sheet = $facts['sheet'];
        $credentials = $sheet['credentials'] ?? ['exists' => false, 'email' => null];
        $email = (string) ($credentials['email'] ?? '');
        $rows = [];
        $failed = false;
        if (!($credentials['exists'] ?? false)) {
            $failed = true;
            $rows[] = self::row('credentials', 'ملف الاعتماد', 'مش موجود', 'danger',
                'حط ملف حساب الخدمة (credentials.json) اللي نزّلته من Google Cloud بمجلد المشروع، وبعدين اضغط «افحص الشيت».');
        } elseif ($email === '') {
            $failed = true;
            $rows[] = self::row('credentials', 'ملف الاعتماد', 'موجود بس ناقص', 'danger',
                'ما لقينا فيه عنوان حساب الخدمة: نزّل الملف من Google Cloud من جديد وحطه بمجلد المشروع.');
        } else {
            $rows[] = self::row('credentials', 'ملف الاعتماد', 'موجود', 'success');
        }

        $tab = trim((string) ($sheet['tab'] ?? ''));
        $rows[] = !empty($sheet['url_is_default'])
            ? self::row('link', 'رابط الشيت', 'ما في رابط محفوظ', 'warning', 'الصق رابط الشيت تحت واضغط «احفظ وافحص».')
            : self::row('link', 'رابط الشيت', $tab !== '' ? 'محفوظ · تبويب «' . $tab . '»' : 'محفوظ · أول تبويب', 'success');

        $check = $facts['state']['checks']['sheet'] ?? null;
        $fresh = is_array($check) && ($check['for'] ?? '') === self::sheetFingerprint($sheet);
        $read = 'todo';
        if (!($credentials['exists'] ?? false)) {
            $rows[] = self::row('read', 'قراءة الشيت', 'بتنفحص بعد ملف الاعتماد', 'muted');
        } elseif (!$fresh) {
            $rows[] = self::row('read', 'قراءة الشيت', is_array($check) ? 'ما انفحص بعد آخر تغيير' : 'لسا ما انفحص', 'muted',
                'اضغط «افحص الشيت».');
        } elseif (($check['error'] ?? '') === 'open') {
            $read = 'fail';
            $rows[] = self::row('read', 'قراءة الشيت', 'ما انفتح', 'danger',
                'تأكد من الرابط واسم التبويب، ومن إنو الشيت مشارك مع حساب الخدمة كمحرّر (Editor)'
                . ($email !== '' ? ': ' . $email : '') . '.');
        } elseif (($check['error'] ?? '') === 'empty') {
            $read = 'fail';
            $rows[] = self::row('read', 'قراءة الشيت', 'الشيت فاضي', 'danger',
                'ما في ولا عمود بأول صف: تأكد إنك اخترت تبويب المنتجات.');
        } else {
            $read = ($check['error'] ?? '') === 'columns' ? 'fail' : 'ok';
            $rows[] = self::row('read', 'قراءة الشيت', 'انفتح وانقرا (' . (int) ($check['headers'] ?? 0) . ' عمود)', 'success');
            $missing = array_values(array_intersect_key(self::SHEET_REQUIRED, array_flip((array) ($check['missing'] ?? []))));
            $rows[] = $missing
                ? self::row('columns', 'الأعمدة الأساسية', 'ناقص: ' . implode(' و', $missing), 'danger',
                    'سمّي عمود اسم المنتج «Product Name» (أو «اسم المنتج») وعمود الماركة «Brand» (أو «الماركة»)، وبعدين افحص من جديد.')
                : self::row('columns', 'الأعمدة الأساسية', 'اسم المنتج والماركة موجودين', 'success');
        }

        $status = $failed || $read === 'fail' ? 'fail' : ($read === 'ok' ? 'ok' : 'todo');
        $summary = match ($status) {
            'ok' => 'الشيت مربوط وبينقرا.',
            'fail' => 'في شي لازم ينصلّح قبل ما نقدر نقرأ الشيت.',
            default => 'احفظ رابط الشيت وافحصه.',
        };
        return self::step('sheet', $status, $summary, $rows, [
            'email' => $email,
            'url' => (string) ($sheet['url'] ?? ''),
            'url_is_default' => (bool) ($sheet['url_is_default'] ?? false),
            'tab' => $tab,
            'checked_at' => is_array($check) && is_numeric($check['at'] ?? null) ? (int) $check['at'] : null,
        ]);
    }

    /**
     * الخطوة 2: لكل مفتاح محفوظ أو لا ونتيجة آخر فحص بعد آخر تغيير (SettingsController::keyRows). عزل الخلفية حسب
     * الطريقة المضبوطة: PhotoRoom بمفتاحه، remove.bg بمفتاحه بملف .env (فحص الاتصالات ما بيفحصه: بيبين ببروفة النشر)،
     * والطرق المحلية أو «بدون عزل» ما بدها مفتاح.
     */
    public static function keysStep(array $facts): array
    {
        $byId = [];
        foreach ((array) $facts['keys'] as $row) {
            $byId[$row['id'] ?? ''] = $row;
        }
        $rows = [];
        $any = ['fail' => false, 'todo' => false];
        $add = function (string $key, string $label, ?array $source) use (&$rows, &$any) {
            if ($source === null) {
                $any['todo'] = true;
                $rows[] = self::row($key, $label, 'غير محفوظ', 'warning', 'حطّ المفتاح من الإعدادات.', self::KEYS_HREF, 'المفاتيح');
                return;
            }
            $tone = (string) ($source['tone'] ?? 'muted');
            $state = (string) ($source['state'] ?? '');
            if (empty($source['saved'])) {
                $any['todo'] = true;
                $rows[] = self::row($key, $label, 'غير محفوظ', 'warning', 'حطّ المفتاح من الإعدادات.', self::KEYS_HREF, 'المفاتيح');
            } elseif ($tone === 'danger') {
                $any['fail'] = true;
                $rows[] = self::row($key, $label, $state, 'danger',
                    'المفتاح محفوظ بس ما زبط بآخر فحص: تأكد إنك نسخته كامل وإنو في رصيد، وغيّره من الإعدادات.',
                    self::KEYS_HREF, 'المفاتيح');
            } elseif ($tone === 'success') {
                $rows[] = self::row($key, $label, $state, 'success');
            } else {
                $any['todo'] = true;
                $rows[] = self::row($key, $label, $state, 'muted', 'اضغط «افحص المفاتيح» لتتأكد إنو شغّال.');
            }
        };
        $add('serper', 'Serper · البحث عن الصور', $byId['serper'] ?? null);
        $add('gemini', 'Gemini · قراءة الملصق', $byId['gemini'] ?? null);

        $method = (string) $facts['bg_method'];
        if ($method === 'remove_bg_api') {
            $rows[] = $facts['removebg_key']
                ? self::row('bg', 'remove.bg · عزل الخلفية', 'محفوظ بملف ‎.env (بيبين شغلو ببروفة النشر)', 'success')
                : self::row('bg', 'remove.bg · عزل الخلفية', 'غير محفوظ', 'warning',
                    'مفتاح remove.bg بينحط بملف .env (REMOVE_BG_API_KEY)، أو رجّع طريقة العزل لـ PhotoRoom من «معالجة الصور».',
                    '/settings?tab=processing', 'معالجة الصور');
            $any['todo'] = $any['todo'] || !$facts['removebg_key'];
        } elseif (in_array($method, ['rembg', 'grabcut'], true)) {
            $rows[] = self::row('bg', 'عزل الخلفية', 'محلي على هالجهاز: ما بدو مفتاح', 'success');
        } elseif ($method === 'none') {
            $rows[] = self::row('bg', 'عزل الخلفية', 'متوقف: ما بدو مفتاح', 'success');
        } else {
            $add('bg', 'PhotoRoom · عزل الخلفية', $byId['photoroom'] ?? null);
        }
        $add('cloudinary', 'Cloudinary · رفع الصور النهائية', $byId['cloudinary'] ?? null);

        $status = $any['fail'] ? 'fail' : ($any['todo'] ? 'todo' : 'ok');
        $summary = match ($status) {
            'ok' => 'كل المفاتيح محفوظة واشتغلت بآخر فحص.',
            'fail' => 'في مفتاح محفوظ بس ما اشتغل بآخر فحص.',
            default => 'المفاتيح بتنحط من الإعدادات، وبعدين افحصها من هون.',
        };
        return self::step('keys', $status, $summary, $rows, ['note' => self::KEYS_TEST_NOTE]);
    }

    /** الخطوة 3: تبويب Brands Mapping وكم ماركة فيه، من آخر فحص للشيت المحفوظ هلق. */
    public static function brandsStep(array $facts): array
    {
        $check = $facts['state']['checks']['brands'] ?? null;
        $fresh = is_array($check) && ($check['for'] ?? '') === self::sheetFingerprint($facts['sheet']);
        $link = ['href' => self::BRANDS_HREF, 'href_label' => 'عبّي جدول الماركات'];
        if (!$fresh) {
            $rows = [self::row('tab', 'تبويب «Brands Mapping»', is_array($check) ? 'ما انفحص بعد تغيير الشيت' : 'لسا ما انفحص',
                'muted', 'اضغط «افحص جدول الماركات».')];
            return self::step('brands', 'todo', 'منشوف إذا جدول الماركات موجود وكم ماركة فيه.', $rows, $link);
        }
        if (($check['status'] ?? '') === 'fail') {
            $rows = [self::row('tab', 'تبويب «Brands Mapping»', 'ما قدرنا نقرأ الشيت', 'danger',
                'كمّل خطوة «ربط الشيت» أول، وبعدين افحص من جديد.', '/setup?step=sheet', 'ربط الشيت')];
            return self::step('brands', 'fail', 'ما قدرنا نفتح الشيت لنقرأ جدول الماركات.', $rows, $link);
        }
        $brands = (int) ($check['brands'] ?? 0);
        $fill = 'افتح «عبّي جدول الماركات»: بيقترح صف لكل ماركة بالشيت من الأدلة اللي عنا، بدون أي بحث مدفوع.';
        $rows = [];
        if (!($check['found'] ?? false)) {
            $rows[] = self::row('tab', 'تبويب «Brands Mapping»', 'مش موجود', 'warning', $fill, self::BRANDS_HREF, 'عبّي جدول الماركات');
        } else {
            $rows[] = self::row('tab', 'تبويب «Brands Mapping»', 'موجود', 'success');
            $rows[] = $brands > 0
                ? self::row('count', 'الماركات', $brands === 1 ? 'ماركة وحدة' : ($brands === 2 ? 'ماركتين' : $brands . ($brands <= 10 ? ' ماركات' : ' ماركة')), 'success')
                : self::row('count', 'الماركات', 'الجدول فاضي', 'warning', $fill, self::BRANDS_HREF, 'عبّي جدول الماركات');
        }
        $ok = ($check['found'] ?? false) && $brands > 0;
        return self::step('brands', $ok ? 'ok' : 'todo', $ok ? 'جدول الماركات موجود وفيه ماركات.'
            : 'جدول الماركات لسا فاضي: عبّيه مرة وحدة من صفحة التشغيل.', $rows, $link + ['brands' => $brands]);
    }

    /** الخطوة 4: آخر «فحص النشر» (HealthController::publishCheckView): الخطوات الأربعة وشو لازم تعمل. */
    public static function publishStep(array $facts): array
    {
        $view = HealthController::publishCheckView($facts['publish']);
        $rows = [];
        foreach ($view['steps'] as $step) {
            $rows[] = self::row($step['key'], $step['title'], $step['label'], $step['tone'], $step['action']);
        }
        $status = match ($view['state']) {
            'ok', 'warn' => 'ok',
            'fail' => 'fail',
            default => 'todo',
        };
        $summary = $view['state'] === 'never' ? 'لسا ما انعمل فحص للنشر.' : $view['summary'];
        return self::step('publish', $status, $summary, $rows, ['when' => $view['when'], 'sample' => $view['sample'],
            'details_href' => '/system-diagnostics#publish-check']);
    }

    /** الخطوة 5: أول تشغيل: بلّش؟ خلص؟ (آخر تقرير تشغيل، temp/nightly/last_report.json). */
    public static function runStep(array $facts): array
    {
        $card = HealthController::lastRunCard($facts['last_run']);
        $outcome = (string) (($facts['last_run'] ?? [])['outcome'] ?? '');
        $started = $facts['state']['checks']['run'] ?? null;
        $startedAt = is_array($started) && is_numeric($started['at'] ?? null) ? (int) $started['at'] : null;
        $runAt = strtotime((string) (($facts['last_run'] ?? [])['started_at'] ?? '')) ?: null;
        $review = ['review_href' => '/catalog?mode=bulk', 'started_rows' => is_array($started) ? (string) ($started['rows'] ?? '') : ''];
        // تقرير أقدم من أول تشغيل بلّش من هون (هامش دقيقتين: العامل بيبلّش قبل ما ينحفظ التقدّم) ما بيوصفه
        $current = $card !== null && ($startedAt === null || $runAt === null || $runAt >= $startedAt - 120);
        if ($current && in_array($outcome, self::RUN_FINISHED, true)) {
            $rows = [self::row('last', 'آخر تشغيل', $card['title'], $card['tone'], $card['summary'], '/catalog?mode=bulk', 'راجع الصور')];
            return self::step('run', 'ok', 'خلص التشغيل: راجع الصور اللي لقيناها واعتمد الصح.', $rows, $review);
        }
        if ($current && in_array($outcome, self::RUN_FAILED, true)) {
            $rows = [self::row('last', 'آخر تشغيل', $card['title'], 'danger', 'شوف شو صار بصفحة التشغيل، وبعدين جرّب أول تشغيل من هون.',
                '/batch-automation', 'صفحة التشغيل')];
            return self::step('run', 'fail', 'آخر تشغيل ما خلص منيح.', $rows, $review);
        }
        if ($startedAt !== null) {
            $rows = [self::row('last', 'أول تشغيل', 'بلّش للصفوف ' . $review['started_rows'], 'info',
                'التقدم بيبين هون وبصفحة التشغيل.', '/batch-automation', 'صفحة التشغيل')];
            return self::step('run', 'running', 'أول تشغيل بلّش: خليه يخلص وبعدين راجع الصور.', $rows, $review);
        }
        $rows = [self::row('last', 'أول تشغيل', 'لسا ما في تشغيل', 'muted', 'اضغط «جهّز أول تشغيل».')];
        return self::step('run', 'todo', 'مندوّر على صور 5 منتجات بس، لتشوف كيف بيشتغل قبل ما تشغّل الشيت كله.', $rows, $review);
    }

    // ------------------------------------------------------------------
    // The Home card
    // ------------------------------------------------------------------

    /**
     * بطاقة «جهّز لقطة» بالرئيسية، أو null: قاعدة البيانات ما ردّت (ما منعرف)، التجهيز خلص (done_at)، أو التركيبة مجهّزة
     * ومستعملة أصلاً (configured). ما بتسكّر شي: رابط وبس.
     */
    public static function homeCard(?array $facts = null): ?array
    {
        try {
            $facts = $facts ?? self::facts();
        } catch (\Throwable $e) {
            return null;
        }
        if (!$facts['db'] || $facts['state']['done_at'] !== null || self::configured($facts)) {
            return null;
        }
        $view = self::view($facts);
        if ($view['ready']) {
            return null;
        }
        $next = null;
        foreach ($view['steps'] as $step) {
            if ($step['key'] === $view['current']) {
                $next = $step;
            }
        }
        return [
            'ok' => $view['progress']['ok'],
            'total' => $view['progress']['total'],
            'next' => $next['title'] ?? '',
            'next_n' => $next['n'] ?? 1,
            'started' => $view['progress']['ok'] > 0 || $facts['state']['checks'] !== [] || $facts['state']['skipped'] !== [],
            'steps' => array_map(fn ($s) => ['n' => $s['n'], 'title' => $s['title'], 'status' => $s['status'],
                                             'tone' => $s['tone'], 'label' => $s['label']], $view['steps']),
        ];
    }

    /**
     * تركيبة مجهّزة ومستعملة: في تشغيل خلص، ملف الاعتماد موجود، ومفاتيح Serper و Gemini و Cloudinary محفوظة. هيدي
     * ما منعرض عليها المعالج تلقائياً أبداً (بيضل موجود من الإعدادات).
     */
    public static function configured(array $facts): bool
    {
        $outcome = (string) (($facts['last_run'] ?? [])['outcome'] ?? '');
        if (!in_array($outcome, self::RUN_FINISHED, true)) {
            return false;
        }
        if (!($facts['sheet']['credentials']['exists'] ?? false)) {
            return false;
        }
        $saved = [];
        foreach ((array) $facts['keys'] as $row) {
            $saved[$row['id'] ?? ''] = !empty($row['saved']);
        }
        return ($saved['serper'] ?? false) && ($saved['gemini'] ?? false) && ($saved['cloudinary'] ?? false);
    }
}
