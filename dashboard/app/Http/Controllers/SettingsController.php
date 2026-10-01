<?php

namespace App\Http\Controllers;

use App\Services\PythonBridge;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;

/**
 * الإعدادات (لقطة): تبويبات بروابط ?tab= — ربط الشيت، المفاتيح، النشر الآلي، معالجة الصور، متقدم.
 *
 * - المفاتيح السرية لا تُطبع أبداً ولا أي جزء منها: الصفحة تعرض «محفوظ / غير محفوظ» فقط، وحقل التغيير فارغ دائماً
 *   (للكتابة فقط)، والحقل الفارغ يُبقي المفتاح المحفوظ.
 * - كل نموذج يحفظ مفاتيح قسمه فقط (SECTIONS)، فحفظ تبويب لا يمسح مفاتيح تبويب آخر. طلب بلا قسم يحفظ قائمة
 *   المرحلة الأولى كاملة بنفس القواعد (LEGACY).
 * - النشر الآلي: مفتاح عام لا يُشغَّل إلا مع ماركة جاهزة مفعّلة (review_stats من cli_bridge، نفس حساب
 *   scripts/review_stats.py)، و«تفعيل» لا يضيف إلى AUTO_PUBLISH_BRANDS إلا ماركة جاهزة، و«إيقاف» يزيل أي مدخل.
 */
class SettingsController extends Controller
{
    public const TABS = [
        'sheet' => ['label' => 'ربط الشيت', 'hint' => 'الجدول والتبويب والأعمدة'],
        'keys' => ['label' => 'المفاتيح', 'hint' => 'Serper · Gemini · PhotoRoom · Cloudinary'],
        'auto-publish' => ['label' => 'النشر الآلي', 'hint' => 'الماركات الجاهزة'],
        'processing' => ['label' => 'معالجة الصور', 'hint' => 'المقاس وعزل الخلفية'],
        'advanced' => ['label' => 'متقدم', 'hint' => 'الرجوع للنظام القديم'],
    ];

    /** قائمة المرحلة الأولى (ProductController) كما هي: الحفظ بلا قسم يلتزم بها حرفياً. */
    public const SECRET_KEYS = [
        'photoroom_api_key', 'gemini_api_key', 'cloudinary_api_key', 'cloudinary_api_secret',
        'google_search_api_key', 'serper_api_key', 'proxy_url',
    ];

    public const TEXT_KEYS = [
        'gemini_model', 'cloudinary_cloud_name', 'google_search_cx', 'search_engine', 'auto_publish_brands',
    ];

    public const CHECKBOX_KEYS = [
        'strict_brand_match', 'auto_publish_enabled',
        // مفاتيح محرك البحث القديم v1 فقط (للتراجع المؤقت)
        'enable_gemini_pre_validation', 'filter_competitors', 'bypass_white_background_check',
    ];

    /** إعدادات معالجة الصور التي يقرؤها config.load_db_config من system_settings. */
    public const PROCESSING_TEXT_KEYS = ['output_canvas_size', 'bg_removal_method'];
    public const PROCESSING_CHECKBOX_KEYS = ['enable_image_enhancement'];

    /** نفس config.BG_REMOVAL_METHODS (و main.SUPPORTED_BG_METHODS). */
    public const BG_METHODS = ['photoroom', 'remove_bg_api', 'grabcut', 'rembg', 'none'];

    /** config.SPREADSHEET_NAME_OR_URL when nothing is set. */
    public const DEFAULT_SHEET = 'automation sheet';

    public const CANVAS_SIZES = [600, 800, 1000, 1200, 1500, 2000];
    public const CANVAS_MIN = 300;
    public const CANVAS_MAX = 4000;

    /** ما يحفظه كل نموذج: القسم لا يلمس مفتاحاً خارجه. */
    public const SECTIONS = [
        'serper' => ['tab' => 'keys', 'secret' => ['serper_api_key']],
        'gemini' => ['tab' => 'keys', 'secret' => ['gemini_api_key']],
        'photoroom' => ['tab' => 'keys', 'secret' => ['photoroom_api_key']],
        'cloudinary' => ['tab' => 'keys', 'secret' => ['cloudinary_api_key', 'cloudinary_api_secret'],
                         'text' => ['cloudinary_cloud_name']],
        'auto-publish' => ['tab' => 'auto-publish', 'checkbox' => ['auto_publish_enabled']],
        'processing' => ['tab' => 'processing', 'text' => ['output_canvas_size', 'bg_removal_method'],
                         'checkbox' => ['enable_image_enhancement']],
        'advanced' => ['tab' => 'advanced', 'secret' => ['google_search_api_key', 'proxy_url'],
                       'text' => ['search_engine', 'gemini_model', 'google_search_cx'],
                       'checkbox' => ['strict_brand_match', 'enable_gemini_pre_validation', 'filter_competitors',
                                      'bypass_white_background_check']],
    ];

    /** مزودو تبويب المفاتيح: مفاتيح الإعداد، ومتغيرات البيئة المقابلة في config.py، وخدمة فحص الاتصالات. */
    public const PROVIDERS = [
        'serper' => ['name' => 'Serper', 'use' => 'البحث عن الصور', 'service' => 'serper',
                     'keys' => ['serper_api_key' => 'SERPER_API_KEY']],
        'gemini' => ['name' => 'Gemini', 'use' => 'قراءة الملصق', 'service' => 'gemini',
                     'keys' => ['gemini_api_key' => 'GEMINI_API_KEY']],
        'photoroom' => ['name' => 'PhotoRoom', 'use' => 'عزل الخلفية', 'service' => 'photoroom',
                        'keys' => ['photoroom_api_key' => 'PHOTOROOM_API_KEY']],
        'cloudinary' => ['name' => 'Cloudinary', 'use' => 'رفع الصور النهائية', 'service' => 'cloudinary',
                         'keys' => ['cloudinary_cloud_name' => 'CLOUDINARY_CLOUD_NAME',
                                    'cloudinary_api_key' => 'CLOUDINARY_API_KEY',
                                    'cloudinary_api_secret' => 'CLOUDINARY_API_SECRET']],
    ];

    /** مفاتيح «متقدم» السرية القديمة: تُعرض حالتها فقط. */
    public const LEGACY_SECRETS = [
        'google_search_api_key' => ['label' => 'مفاتيح Google Custom Search (قديم)', 'env' => 'GOOGLE_SEARCH_API_KEY'],
        'proxy_url' => ['label' => 'عنوان البروكسي', 'env' => 'PROXY_URL'],
    ];

    /** متغيرات البيئة السرية (نفس verify_cloud_services._SECRET_SETTINGS): تُقرأ للحجب من السجلات فقط. */
    private const SECRET_ENV = [
        'GEMINI_API_KEY', 'SERPER_API_KEY', 'GOOGLE_SEARCH_API_KEYS', 'GOOGLE_SEARCH_API_KEY', 'CLOUDINARY_API_KEY',
        'CLOUDINARY_API_SECRET', 'PHOTOROOM_API_KEY', 'REMOVE_BG_API_KEY', 'TELEGRAM_BOT_TOKEN', 'PROXY_URL',
    ];

    /** متغيرات .env الجذر غير السرية التي يجوز للصفحة قراءة قيمتها. */
    private const READABLE_ENV = [
        'SPREADSHEET_NAME_OR_URL', 'SPREADSHEET_TAB_NAME', 'CREDENTIALS_FILE', 'BG_REMOVAL_METHOD',
        'OUTPUT_CANVAS_SIZE', 'GEMINI_MODEL', 'SEARCH_ENGINE',
    ];

    public function show(Request $request)
    {
        $tab = self::tabFrom($request->query('tab'));
        $dbError = null;
        $stored = self::stored($dbError);
        $data = [
            'tab' => $tab,
            'tabs' => self::TABS,
            'dbError' => $dbError,
            'flash' => [
                'success' => session('success'),
                'error' => session('error'),
                'warnings' => (array) session('warnings', []),
            ],
        ];

        // without the database nothing but the sheet tab can be read truthfully: the page says so instead
        if ($stored === null && $tab !== 'sheet') {
            return view('dashboard.settings', $data);
        }

        switch ($tab) {
            case 'sheet':
                $data['sheet'] = self::sheetData();
                break;
            case 'keys':
                $data['keys'] = self::keyRows($stored ?? [], ProductController::lastDiagnostics());
                break;
            case 'auto-publish':
                $result = PythonBridge::run('review_stats');
                $data['autoPublish'] = self::autoPublishData($stored ?? [], $result);
                break;
            case 'processing':
                $data['processing'] = self::processingData($stored ?? []);
                break;
            case 'advanced':
                $data['advanced'] = self::advancedData($stored ?? []);
                break;
        }

        return view('dashboard.settings', $data);
    }

    /**
     * حفظ نموذج واحد. section يحدد المفاتيح (SECTIONS)؛ بلا section تُحفظ قائمة المرحلة الأولى كاملة.
     * حقل سري فارغ يعني "إبقاء القيمة الحالية" حتى لا يُكتب فوق المفتاح الحقيقي.
     */
    public function save(Request $request)
    {
        $section = self::field($request, 'section');
        if ($section === 'brand') {
            return $this->toggleBrand($request);
        }
        if ($section === '') {
            $spec = ['tab' => 'keys', 'secret' => self::SECRET_KEYS, 'text' => self::TEXT_KEYS,
                     'checkbox' => self::CHECKBOX_KEYS];
        } elseif (isset(self::SECTIONS[$section])) {
            $spec = self::SECTIONS[$section];
        } else {
            return self::back('keys', ['error' => 'ما عرفنا شو بدك تحفظ. حدّث الصفحة وجرّب مرة تانية.']);
        }
        $tab = $spec['tab'];
        $warnings = [];

        try {
            $changes = [];
            foreach ($spec['secret'] ?? [] as $k) {
                $val = self::field($request, $k);
                $clear = $request->boolean('clear_' . $k);
                if ($val === '' && !$clear) {
                    continue;
                }
                if (strpos($val, '••••') === 0) {
                    // قناع أعيد إرساله بالخطأ: لا نكتبه فوق المفتاح
                    continue;
                }
                $changes[$k] = $clear ? '' : $val;
            }

            foreach ($spec['text'] ?? [] as $k) {
                $val = self::field($request, $k);
                if ($k === 'search_engine' && !in_array($val, ['v2', 'v1'], true)) {
                    $val = 'v2';
                }
                if ($k === 'gemini_model' && !array_key_exists($val, ProductController::SUPPORTED_GEMINI_MODELS)) {
                    $warnings[] = "نموذج Gemini '{$val}' غير مدعوم؛ لم يتم تغيير النموذج المحفوظ.";
                    continue;
                }
                if ($k === 'auto_publish_brands') {
                    $val = implode(', ', self::brandList($val));
                }
                if ($k === 'output_canvas_size') {
                    if (!preg_match('/^\d{3,4}$/', $val) || (int) $val < self::CANVAS_MIN || (int) $val > self::CANVAS_MAX) {
                        $warnings[] = 'مقاس الصورة لازم يكون رقم بين ' . self::CANVAS_MIN . ' و' . self::CANVAS_MAX
                            . ' بكسل؛ ما تغيّر المقاس المحفوظ.';
                        continue;
                    }
                    $val = (string) (int) $val;
                }
                if ($k === 'bg_removal_method' && !in_array($val, self::BG_METHODS, true)) {
                    $warnings[] = 'طريقة عزل الخلفية هاي مش مدعومة؛ ما تغيّرت الطريقة المحفوظة.';
                    continue;
                }
                $changes[$k] = $val;
            }

            foreach ($spec['checkbox'] ?? [] as $ck) {
                $changes[$ck] = $request->has($ck) ? 'true' : 'false';
            }

            // النشر الآلي لا يُشغَّل بدون ماركة جاهزة مفعّلة (من أي نموذج، ومنه الحفظ القديم الكامل)
            if (($changes['auto_publish_enabled'] ?? null) === 'true') {
                $current = self::storedValues(['auto_publish_enabled', 'auto_publish_brands']);
                if (($current['auto_publish_enabled'] ?? '') !== 'true') {
                    $brands = $changes['auto_publish_brands'] ?? ($current['auto_publish_brands'] ?? '');
                    $why = self::autoPublishBlocker(self::brandList($brands), PythonBridge::run('review_stats'));
                    if ($why !== null) {
                        $changes['auto_publish_enabled'] = 'false';
                        $warnings[] = $why;
                    }
                }
            }

            self::write($changes);

            $message = self::savedMessage($section, $changes);
            return self::back($tab, ['success' => $message, 'warnings' => $warnings]);
        } catch (\Throwable $e) {
            return self::back($tab, ['error' => 'ما انحفظت الإعدادات: قاعدة البيانات ما ردّت. جرّب كمان شوي.']);
        }
    }

    /**
     * «تفعيل» / «إيقاف» لماركة في AUTO_PUBLISH_BRANDS. التفعيل لماركة جاهزة فقط (review_stats)، والإيقاف لأي مدخل.
     * إيقاف آخر ماركة والنشر الآلي شغّال يطفئ المفتاح العام أيضاً، والرسالة تقول ذلك.
     */
    private function toggleBrand(Request $request)
    {
        $brand = self::field($request, 'brand');
        $op = self::field($request, 'op');
        if ($brand === '' || !in_array($op, ['enable', 'disable'], true)) {
            return self::back('auto-publish', ['error' => 'ما عرفنا أي ماركة بدك تغيّر.']);
        }

        try {
            $current = self::storedValues(['auto_publish_enabled', 'auto_publish_brands']);
            $list = self::brandList($current['auto_publish_brands'] ?? '');
            $key = self::brandKey($brand);

            if ($op === 'disable') {
                $kept = array_values(array_filter($list, fn ($b) => self::brandKey($b) !== $key));
                if (count($kept) === count($list)) {
                    return self::back('auto-publish', ['error' => 'الماركة «' . $brand . '» مش مفعّلة أصلاً.']);
                }
                $changes = ['auto_publish_brands' => implode(', ', $kept)];
                $message = 'وقّفنا النشر الآلي لـ «' . $brand . '». صورها رح تستنى مراجعتك.';
                if ($kept === [] && ($current['auto_publish_enabled'] ?? '') === 'true') {
                    $changes['auto_publish_enabled'] = 'false';
                    $message .= ' وطفينا النشر الآلي كله لأنه ما ضل ولا ماركة مفعّلة.';
                }
                self::write($changes);
                return self::back('auto-publish', ['success' => $message]);
            }

            $stats = PythonBridge::run('review_stats');
            if (($stats['status'] ?? '') !== 'success') {
                return self::back('auto-publish', ['error' => 'ما قدرنا نتأكد إن الماركة جاهزة، فما فعّلناها. جرّب كمان شوي.']);
            }
            $ready = null;
            foreach ((array) ($stats['brands'] ?? []) as $row) {
                if (($row['status'] ?? '') === 'ready' && self::brandKey((string) ($row['brand'] ?? '')) === $key) {
                    $ready = trim((string) $row['brand']);
                }
            }
            if ($ready === null) {
                return self::back('auto-publish', ['error' => 'الماركة «' . $brand . '» لسا مش جاهزة، فما فعّلناها.']);
            }
            foreach ($list as $b) {
                if (self::brandKey($b) === $key) {
                    return self::back('auto-publish', ['success' => 'الماركة «' . $ready . '» مفعّلة من قبل.']);
                }
            }
            $list[] = $ready;
            self::write(['auto_publish_brands' => implode(', ', $list)]);
            $message = 'فعّلنا «' . $ready . '».';
            $message .= ($current['auto_publish_enabled'] ?? '') === 'true'
                ? ' صورها المؤكدة رح تنرفع بدون مراجعة من التشغيل الجاي.'
                : ' النشر الآلي لسا مطفأ: شغّله من المفتاح فوق لما تكون جاهز.';
            return self::back('auto-publish', ['success' => $message]);
        } catch (\Throwable $e) {
            return self::back('auto-publish', ['error' => 'ما انحفظ التغيير: قاعدة البيانات ما ردّت. جرّب كمان شوي.']);
        }
    }

    // ------------------------------------------------------------------
    // Data for each tab
    // ------------------------------------------------------------------

    public static function tabFrom($value): string
    {
        return is_string($value) && array_key_exists($value, self::TABS) ? $value : 'sheet';
    }

    /** ربط الشيت: القيم التي يستعملها بايثون فعلاً، وحساب الخدمة من ملف الاعتماد (client_email فقط). */
    public static function sheetData(): array
    {
        $env = self::envValues(['SPREADSHEET_NAME_OR_URL', 'SPREADSHEET_TAB_NAME', 'CREDENTIALS_FILE']);
        $credentials = self::credentialsInfo($env['CREDENTIALS_FILE'] ?? null);
        $url = trim((string) ($env['SPREADSHEET_NAME_OR_URL'] ?? ''));
        return [
            // config.py: SPREADSHEET_NAME_OR_URL defaults to a sheet named "automation sheet"
            'url' => $url !== '' ? $url : self::DEFAULT_SHEET,
            'url_is_default' => $url === '',
            'tab' => $env['SPREADSHEET_TAB_NAME'] ?? '',
            'credentials' => $credentials,
        ];
    }

    /** {exists, email}: عنوان حساب الخدمة فقط؛ المفتاح الخاص في الملف لا يُقرأ إلى الصفحة. */
    public static function credentialsInfo(?string $configured = null): array
    {
        $path = $configured !== null && $configured !== '' ? $configured : base_path('../credentials.json');
        if (!preg_match('#^([a-zA-Z]:[\\\\/]|/)#', $path)) {
            $path = base_path('../' . $path);
        }
        if (!is_file($path)) {
            return ['exists' => false, 'email' => null];
        }
        $doc = json_decode((string) @file_get_contents($path), true);
        $email = is_array($doc) && is_string($doc['client_email'] ?? null) ? trim($doc['client_email']) : '';
        return ['exists' => true,
                'email' => filter_var($email, FILTER_VALIDATE_EMAIL) ? $email : null];
    }

    /** صفوف تبويب المفاتيح: محفوظ أو لا (قاعدة البيانات أو البيئة)، مع نتيجة آخر فحص للاتصالات إن كانت بعد آخر تغيير. */
    public static function keyRows(array $stored, ?array $diagnostics): array
    {
        $checkedAt = is_array($diagnostics) ? (strtotime((string) ($diagnostics['checked_at'] ?? '')) ?: null) : null;
        $rows = [];
        foreach (self::PROVIDERS as $id => $p) {
            $saved = true;
            $fromEnv = false;
            $changedAt = null;
            foreach ($p['keys'] as $setting => $envName) {
                // a stored value saved or cleared after the check changes the key in use: the check no longer
                // describes it (a cleared value hands over to the .env key, which that check never saw)
                $at = (int) ($stored[$setting]['updated_at'] ?? 0);
                if ($at > 0) {
                    $changedAt = max($changedAt ?? 0, $at);
                }
                $db = trim((string) ($stored[$setting]['value'] ?? ''));
                if ($db !== '') {
                    continue;
                }
                if (self::envHas($envName)) {
                    $fromEnv = true;
                    continue;
                }
                $saved = false;
            }
            $service = is_array($diagnostics) ? ($diagnostics['services'][$p['service']] ?? null) : null;
            $rows[] = array_merge(
                ['id' => $id, 'name' => $p['name'], 'use' => $p['use'], 'saved' => $saved, 'from_env' => $saved && $fromEnv,
                 'fields' => array_keys($p['keys']),
                 // the only non-secret field of the tab: the Cloudinary account name (public in every image URL)
                 'cloud_name' => $id === 'cloudinary' ? trim((string) ($stored['cloudinary_cloud_name']['value'] ?? '')) : null],
                self::keyState($saved, is_array($service) ? (string) ($service['status'] ?? '') : null, $checkedAt, $changedAt)
            );
        }

        $env = self::envValues(['CREDENTIALS_FILE']);
        $credentials = self::credentialsInfo($env['CREDENTIALS_FILE'] ?? null);
        $sheet = is_array($diagnostics) ? ($diagnostics['services']['google_sheets'] ?? null) : null;
        $status = is_array($sheet) ? (string) ($sheet['status'] ?? '') : null;
        if (!$credentials['exists']) {
            $state = ['state' => 'ملف الاعتماد مش موجود', 'tone' => 'danger'];
        } elseif ($status === 'online') {
            $state = ['state' => 'متصل', 'tone' => 'success'];
        } elseif ($status === null || $status === '') {
            $state = ['state' => 'الملف موجود · ما انفحص', 'tone' => 'muted'];
        } elseif ($status === 'disabled') {
            $state = ['state' => 'الملف موجود · مش مفعّل', 'tone' => 'muted'];
        } else {
            $state = ['state' => 'ما بيرد', 'tone' => 'danger'];
        }
        $rows[] = array_merge(['id' => 'google_sheet', 'name' => 'Google Sheet', 'use' => 'ملف الاعتماد',
                               'saved' => $credentials['exists'], 'from_env' => false, 'fields' => []], $state);
        return $rows;
    }

    /** «محفوظ · يعمل» وأخواتها. نتيجة فحص أقدم من آخر تغيير للمفتاح لا تصف المفتاح الحالي. */
    public static function keyState(bool $saved, ?string $status, ?int $checkedAt, ?int $changedAt): array
    {
        if (!$saved) {
            return ['state' => 'غير محفوظ', 'tone' => 'warning'];
        }
        if ($status === null || $status === '' || $checkedAt === null) {
            return ['state' => 'محفوظ · ما انفحص', 'tone' => 'muted'];
        }
        if ($changedAt !== null && $changedAt > $checkedAt) {
            return ['state' => 'محفوظ · ما انفحص بعد التغيير', 'tone' => 'muted'];
        }
        return match ($status) {
            'online' => ['state' => 'محفوظ · يعمل', 'tone' => 'success'],
            'disabled' => ['state' => 'محفوظ · مش مفعّل', 'tone' => 'muted'],
            default => ['state' => 'محفوظ · ما بيرد', 'tone' => 'danger'],
        };
    }

    /**
     * جدول النشر الآلي من review_stats: لكل ماركة المراجعات والدقة والحد المضمون وحالتها، مع مدخلات
     * AUTO_PUBLISH_BRANDS التي لا تظهر في الإحصاءات (تبقى ظاهرة وقابلة للإيقاف).
     */
    public static function autoPublishData(array $stored, array $stats): array
    {
        $enabled = strtolower(trim((string) ($stored['auto_publish_enabled']['value'] ?? ''))) === 'true';
        $listed = self::brandList((string) ($stored['auto_publish_brands']['value'] ?? ''));
        $listedKeys = [];
        foreach ($listed as $b) {
            $listedKeys[self::brandKey($b)] = $b;
        }
        $ok = ($stats['status'] ?? '') === 'success';
        $thresholds = (array) ($stats['thresholds'] ?? []);
        $rows = [];
        $seen = [];
        $readyListed = [];
        foreach ($ok ? (array) ($stats['brands'] ?? []) : [] as $b) {
            $name = trim((string) ($b['brand'] ?? ''));
            $key = self::brandKey($name);
            $isListed = $name !== '' && isset($listedKeys[$key]);
            $row = self::brandRow($b, $isListed);
            if ($row['ready'] && $isListed) {
                $readyListed[] = $name;
            }
            $seen[$key] = true;
            $rows[] = $row;
        }
        // مدخلات مفعّلة لا دليل لها في قرارات المراجعة (مثل '*' أو 'category:' أو ماركة أُضيفت يدوياً سابقاً)
        foreach ($listedKeys as $key => $name) {
            if (isset($seen[$key])) {
                continue;
            }
            $rows[] = self::listedOnlyRow($name, $ok);
        }

        $minBound = is_numeric($thresholds['min_lower_bound'] ?? null) ? (float) $thresholds['min_lower_bound'] : null;
        $perfect = is_numeric($thresholds['perfect_record_reviews'] ?? null) ? (int) $thresholds['perfect_record_reviews'] : null;
        return [
            'status' => $ok ? 'ok' : 'error',
            'enabled' => $enabled,
            'listed' => $listed,
            'rows' => $rows,
            'ready_listed' => $readyListed,
            'ready_count' => count(array_filter($rows, fn ($r) => $r['ready'])),
            'reviews' => $ok ? (int) ($stats['overall']['actions'] ?? 0) : null,
            'can_enable' => $ok && $readyListed !== [],
            'criterion' => self::criterionText($minBound, $perfect),
        ];
    }

    /** نص المعيار من عتبات local_cache_db (لا أرقام ثابتة في الصفحة). */
    public static function criterionText(?float $minBound, ?int $perfect): string
    {
        if ($minBound === null) {
            return 'المعيار: الحد الأدنى المضمون لدقة الاقتراح (فاصل ويلسون 95%) لازم يوصل المطلوب قبل ما تصير الماركة جاهزة.';
        }
        $text = 'المعيار: الحد الأدنى المضمون لدقة الاقتراح (فاصل ويلسون 95%) لازم يوصل ' . self::pct($minBound) . '.';
        if ($perfect !== null && $perfect > 0) {
            $text .= ' يعني تقريباً ' . $perfect . ' اقتراح معتمد بدون ولا غلطة.';
        }
        return $text . ' لما توصل ماركة، بيطلع زر «تفعيل» جنبها.';
    }

    /** صف ماركة واحدة من review_stats بنص عربي بسيط (لا رموز حالة كنص رئيسي). */
    public static function brandRow(array $b, bool $listed): array
    {
        $name = trim((string) ($b['brand'] ?? ''));
        $prechecked = (int) ($b['prechecked'] ?? 0);
        $status = (string) ($b['status'] ?? 'needs_reviews');
        $needed = is_numeric($b['reviews_needed'] ?? null) ? (int) $b['reviews_needed'] : null;
        $ready = $status === 'ready';
        if ($ready) {
            [$chip, $tone] = $listed ? ['مفعّلة', 'approved'] : ['جاهزة', 'proposed'];
        } elseif ($status === 'low_precision') {
            [$chip, $tone] = ['دقة أقل من المطلوب', 'error'];
        } elseif ($name === '' || $needed === null) {
            [$chip, $tone] = ['ما بتنفع للنشر الآلي', 'none'];
        } else {
            [$chip, $tone] = ['تحتاج ' . max(1, $needed - $prechecked) . ' مراجعة', 'none'];
        }
        $action = null;
        if ($listed) {
            $action = 'disable';
        } elseif ($ready) {
            $action = 'enable';
        }
        return [
            'brand' => $name,
            'label' => $name !== '' ? $name : '(بلا ماركة)',
            'reviews' => $prechecked,
            'precision' => self::pct($b['precision'] ?? null),
            'lower_bound' => self::pct($b['lower_bound'] ?? null),
            'chip' => $chip,
            'tone' => $tone,
            'ready' => $ready,
            'listed' => $listed,
            'unproven' => $listed && !$ready,
            'action' => $action,
            'status_code' => $status,
        ];
    }

    /**
     * مدخل مفعّل في AUTO_PUBLISH_BRANDS بلا مراجعات مسجلة. إذا ما انقرت الإحصاءات (الجسر ما ردّ) ما منعرف دليله:
     * بيضل ظاهر وقابل للإيقاف، بس ما منقول «بدون مراجعات» ولا منحذّر إنه بلا دليل.
     */
    public static function listedOnlyRow(string $entry, bool $statsOk): array
    {
        $label = $entry;
        if ($entry === '*') {
            $label = 'كل الماركات (*)';
        } elseif (stripos($entry, 'category:') === 0) {
            $label = 'فئة ' . trim(substr($entry, strlen('category:')));
        }
        return [
            'brand' => $entry, 'label' => $label, 'reviews' => $statsOk ? 0 : null, 'precision' => '—',
            'lower_bound' => '—', 'chip' => $statsOk ? 'مفعّلة بدون مراجعات' : 'مفعّلة',
            'tone' => $statsOk ? 'warning' : 'none', 'ready' => false,
            'listed' => true, 'unproven' => $statsOk, 'action' => 'disable', 'status_code' => 'listed',
        ];
    }

    /** سبب منع تشغيل النشر الآلي، أو null إذا في ماركة جاهزة مفعّلة. */
    public static function autoPublishBlocker(array $brands, array $stats): ?string
    {
        if (($stats['status'] ?? '') !== 'success') {
            return 'ما قدرنا نتأكد من جاهزية الماركات، فالنشر الآلي ضل مطفأ.';
        }
        $ready = [];
        foreach ((array) ($stats['brands'] ?? []) as $b) {
            if (($b['status'] ?? '') === 'ready') {
                $ready[self::brandKey((string) ($b['brand'] ?? ''))] = true;
            }
        }
        foreach ($brands as $b) {
            if (isset($ready[self::brandKey($b)])) {
                return null;
            }
        }
        return 'النشر الآلي ضل مطفأ: لازم تفعّل ماركة جاهزة وحدة على الأقل قبل ما تشغّله.';
    }

    public static function processingData(array $stored): array
    {
        $env = self::envValues(['BG_REMOVAL_METHOD', 'OUTPUT_CANVAS_SIZE']);
        $size = trim((string) ($stored['output_canvas_size']['value'] ?? ''));
        if (!preg_match('/^\d+$/', $size)) {
            $size = preg_match('/^\d+$/', (string) ($env['OUTPUT_CANVAS_SIZE'] ?? '')) ? $env['OUTPUT_CANVAS_SIZE'] : '800';
        }
        $method = strtolower(trim((string) ($stored['bg_removal_method']['value'] ?? '')));
        if (!in_array($method, self::BG_METHODS, true)) {
            $fromEnv = strtolower(trim((string) ($env['BG_REMOVAL_METHOD'] ?? '')));
            $method = $fromEnv !== '' ? $fromEnv : 'photoroom';
        }
        $sizes = self::CANVAS_SIZES;
        if (!in_array((int) $size, $sizes, true)) {
            $sizes[] = (int) $size;
            sort($sizes);
        }
        $methods = ['photoroom' => 'PhotoRoom: عزل الخلفية سحابياً (الافتراضي)',
                    'none' => 'بدون عزل: الصورة بتنحط متل ما هي وبتستنى مراجعتك'];
        if (!isset($methods[$method])) {
            $methods[$method] = $method . ' (المضبوط حالياً)';
        }
        return [
            'size' => (int) $size,
            'sizes' => $sizes,
            'method' => $method,
            'methods' => $methods,
            'enhance' => strtolower(trim((string) ($stored['enable_image_enhancement']['value'] ?? ''))) === 'true',
        ];
    }

    public static function advancedData(array $stored): array
    {
        $value = fn (string $k) => trim((string) ($stored[$k]['value'] ?? ''));
        $engine = $value('search_engine') === 'v1' ? 'v1' : 'v2';
        $model = $value('gemini_model') !== '' ? $value('gemini_model') : 'gemini-3.1-flash-lite';
        $secrets = [];
        foreach (self::LEGACY_SECRETS as $k => $meta) {
            $secrets[$k] = ['label' => $meta['label'], 'saved' => $value($k) !== '' || self::envHas($meta['env'])];
        }
        $strict = $value('strict_brand_match');
        return [
            'engine' => $engine,
            'model' => $model,
            'models' => ProductController::SUPPORTED_GEMINI_MODELS,
            'model_supported' => array_key_exists($model, ProductController::SUPPORTED_GEMINI_MODELS),
            'cx' => $value('google_search_cx'),
            'strict' => $strict === '' ? true : $strict === 'true',
            'v1' => [
                'enable_gemini_pre_validation' => $value('enable_gemini_pre_validation') === 'true',
                'filter_competitors' => $value('filter_competitors') === 'true',
                'bypass_white_background_check' => $value('bypass_white_background_check') === 'true',
            ],
            'secrets' => $secrets,
        ];
    }

    // ------------------------------------------------------------------
    // Storage helpers
    // ------------------------------------------------------------------

    /** كل system_settings: key => {value, updated_at}، أو null مع سبب إذا تعذرت القراءة. */
    public static function stored(?string &$error = null): ?array
    {
        try {
            $out = [];
            foreach (DB::table('system_settings')->get() as $row) {
                $out[$row->key] = ['value' => (string) ($row->value ?? ''),
                                   'updated_at' => $row->updated_at ? (strtotime((string) $row->updated_at) ?: null) : null];
            }
            return $out;
        } catch (\Throwable $e) {
            $error = 'قاعدة البيانات مش متاحة، فالإعدادات ما بتنقرا ولا بتنحفظ هلق.';
            return null;
        }
    }

    /** قيم مفاتيح محددة (ترفع أخطاء قاعدة البيانات). */
    private static function storedValues(array $keys): array
    {
        return DB::table('system_settings')->whereIn('key', $keys)->pluck('value', 'key')
            ->map(fn ($v) => (string) $v)->all();
    }

    /** القيم السرية المحفوظة (لإخفائها من السجلات فقط؛ لا تُمرر إلى أي صفحة). */
    public static function secretValues(): array
    {
        try {
            $stored = DB::table('system_settings')->whereIn('key', self::SECRET_KEYS)->pluck('value')->all();
        } catch (\Throwable $e) {
            $stored = [];
        }
        return self::secretList($stored);
    }

    /**
     * كل ما يجب حجبه من السجلات، مثل verify_cloud_services._secret_values: القيم المحفوظة ومتغيرات البيئة السرية
     * (بيئة العملية و.env الجذر)، وكل مفتاح من قائمة مفصولة بفواصل (مفاتيح Custom Search)، واسم المستخدم وكلمة
     * السر من رابط البروكسي. الأطول أولاً حتى لا يبقى جزء من قيمة أطول. للحجب فقط: لا تُمرر إلى أي صفحة.
     */
    public static function secretList(array $stored): array
    {
        $raw = array_values($stored);
        $file = self::rootEnvFile();
        foreach (self::SECRET_ENV as $name) {
            $raw[] = getenv($name);
            $raw[] = $file[$name] ?? '';
        }
        $out = [];
        foreach ($raw as $value) {
            $value = is_scalar($value) ? trim((string) $value) : '';
            if ($value === '') {
                continue;
            }
            $out[] = $value;
            foreach (explode(',', $value) as $part) {
                $out[] = trim($part);
            }
            $url = @parse_url($value);
            if (is_array($url) && isset($url['host'])) {
                foreach (['user', 'pass'] as $part) {
                    if (isset($url[$part])) {
                        $out[] = $url[$part];
                        $out[] = rawurldecode($url[$part]);
                    }
                }
            }
        }
        $out = array_values(array_unique(array_filter($out, fn ($v) => mb_strlen($v) >= 6)));
        usort($out, fn ($a, $b) => mb_strlen($b) <=> mb_strlen($a));
        return $out;
    }

    private static function write(array $changes): void
    {
        foreach ($changes as $k => $val) {
            DB::table('system_settings')->updateOrInsert(['key' => $k], ['value' => $val, 'updated_at' => now()]);
        }
    }

    private static function back(string $tab, array $flash)
    {
        $response = redirect()->to(route('dashboard.settings') . '?tab=' . urlencode(self::tabFrom($tab)));
        foreach ($flash as $key => $value) {
            if ($value !== null && $value !== [] && $value !== '') {
                $response = $response->with($key, $value);
            }
        }
        return $response;
    }

    private static function savedMessage(string $section, array $changes): string
    {
        return match ($section) {
            'serper', 'gemini', 'photoroom', 'cloudinary' => $changes === []
                ? 'ما تغيّر شي: الحقول الفاضية بتخلي المفتاح المحفوظ متل ما هو.'
                : 'انحفظ. افحص الاتصالات من صفحة الصحة لتتأكد إنه شغّال.',
            'auto-publish' => ($changes['auto_publish_enabled'] ?? '') === 'true'
                ? 'النشر الآلي شغّال للماركات المفعّلة.'
                : 'النشر الآلي مطفأ: كل النتائج رح تستنى مراجعتك.',
            'processing' => 'انحفظت إعدادات معالجة الصور، وبتنطبق من التشغيل الجاي.',
            default => 'انحفظت الإعدادات.',
        };
    }

    // ------------------------------------------------------------------
    // Small pure helpers
    // ------------------------------------------------------------------

    /** A form field as trimmed text; an array or other non-text value (a crafted request) is ''. */
    private static function field(Request $request, string $key): string
    {
        $value = $request->input($key, '');
        return is_scalar($value) ? trim((string) $value) : '';
    }

    /** AUTO_PUBLISH_BRANDS كما يقرؤه config.py: مفصولة بفواصل، بلا فراغات ولا تكرار (نفس تطبيع المرحلة الأولى). */
    public static function brandList(string $value): array
    {
        $brands = array_filter(array_map('trim', explode(',', $value)), fn ($b) => $b !== '');
        return array_values(array_unique($brands));
    }

    /** مقارنة أسماء الماركات: حروف صغيرة ومسافات موحدة. */
    public static function brandKey(string $brand): string
    {
        return preg_replace('/\s+/u', ' ', mb_strtolower(trim($brand)));
    }

    public static function pct($value): string
    {
        if (!is_numeric($value)) {
            return '—';
        }
        return (string) (int) floor((float) $value * 100 + 1e-9) . '%';
    }

    /**
     * قيم متغيرات بيئة غير سرية كما يراها بايثون: بيئة العملية أولاً (config.py يستعمل setdefault)، ثم .env الجذر.
     */
    public static function envValues(array $names): array
    {
        $names = array_values(array_intersect($names, self::READABLE_ENV));
        $file = self::rootEnvFile();
        $out = [];
        foreach ($names as $name) {
            $value = getenv($name);
            if ($value === false || $value === '') {
                $value = $file[$name] ?? null;
            }
            if ($value !== null && $value !== false) {
                $out[$name] = (string) $value;
            }
        }
        return $out;
    }

    /** هل للمتغير قيمة غير فارغة (بيئة العملية أو .env الجذر)؟ لا تُعاد القيمة نفسها أبداً. */
    public static function envHas(string $name): bool
    {
        $value = getenv($name);
        if ($value !== false && trim((string) $value) !== '') {
            return true;
        }
        return trim((string) (self::rootEnvFile()[$name] ?? '')) !== '';
    }

    /** .env الجذر بنفس قراءة config._load_env (سطر KEY=VALUE، وتُنزع علامات الاقتباس). */
    private static function rootEnvFile(): array
    {
        static $cache = null;
        if ($cache !== null) {
            return $cache;
        }
        $cache = [];
        $path = base_path('../.env');
        if (!is_file($path)) {
            return $cache;
        }
        foreach (preg_split('/\r?\n/', (string) @file_get_contents($path)) as $line) {
            $line = trim($line);
            if ($line === '' || $line[0] === '#' || strpos($line, '=') === false) {
                continue;
            }
            [$key, $val] = explode('=', $line, 2);
            $cache[trim($key)] = trim(trim($val), "\"'");
        }
        return $cache;
    }
}
