<?php

namespace App\Http\Controllers;

use App\Services\PythonBridge;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Cache;
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
 * - «النشر الآلي لكل الماركات المؤكدة» (auto_publish_strict_lane): شغّال افتراضياً (STRICT_LANE_DEFAULT، موافقة المالك)
 *   ومفتاحه بيتشغّل وبيطفى بأي وقت؛ بايثون (catalog_match.decide) ما بينشر منه شي قبل ما تكون فئة strict جاهزة
 *   (review_stats.lanes، نفس عتبات الماركة)، وما بيحتاج المفتاح العام (هداك لجدول الماركات).
 */
class SettingsController extends Controller
{
    public const TABS = [
        'sheet' => ['label' => 'ربط الشيت', 'hint' => 'الجدول والتبويب والأعمدة'],
        'keys' => ['label' => 'المفاتيح', 'hint' => 'Serper · Gemini · Claude · PhotoRoom · Cloudinary'],
        'models' => ['label' => 'نماذج التحقق', 'hint' => 'مين بيقرأ الملصق وبكم'],
        'auto-publish' => ['label' => 'النشر الآلي', 'hint' => 'الماركات الجاهزة'],
        'processing' => ['label' => 'معالجة الصور', 'hint' => 'المقاس وعزل الخلفية'],
        'advanced' => ['label' => 'متقدم', 'hint' => 'مصادر البحث والسرعة'],
    ];

    /** النشر الآلي لكل الماركات المؤكدة (AUTO_PUBLISH_STRICT_LANE): نموذجه الخاص (section=strict-lane). */
    public const STRICT_LANE_KEY = 'auto_publish_strict_lane';
    /** قيمته لما المالك ما حفظ شي (نفس catalog_match.settings.DEFAULTS و config.py): شغّال. */
    public const STRICT_LANE_DEFAULT = true;

    /** قائمة المرحلة الأولى (ProductController) كما هي: الحفظ بلا قسم يلتزم بها حرفياً. */
    public const SECRET_KEYS = [
        'photoroom_api_key', 'gemini_api_key', 'cloudinary_api_key', 'cloudinary_api_secret',
        'google_search_api_key', 'serper_api_key', 'proxy_url',
    ];

    public const TEXT_KEYS = [
        'gemini_model', 'cloudinary_cloud_name', 'google_search_cx', 'auto_publish_brands',
    ];

    public const CHECKBOX_KEYS = [
        'strict_brand_match', 'auto_publish_enabled',
    ];

    /** إعدادات معالجة الصور التي يقرؤها config.load_db_config من system_settings. */
    public const PROCESSING_TEXT_KEYS = ['output_canvas_size', 'bg_removal_method', 'output_background'];

    /**
     * خلفية لوحة النشر (output_background، config.OUTPUT_BACKGROUNDS): شفافة (الافتراضي، PNG للتطبيق بالوضع الغامق
     * والفاتح، والنسخة البيضا برابط) أو بيضا (اللوحة المعتمة متل قبل).
     */
    public const OUTPUT_BACKGROUNDS = ['transparent' => 'خلفية شفافة (للتطبيق: وضع غامق وفاتح)', 'white' => 'خلفية بيضا'];
    public const PROCESSING_CHECKBOX_KEYS = ['enable_image_enhancement'];

    /** نفس config.BG_FALLBACKS: «لما يخلص رصيد خدمة العزل» (system_settings.bg_fallback). local هو الافتراضي. */
    public const BG_FALLBACKS = ['local', 'off'];

    /** نفس config.BG_REMOVAL_METHODS (و main.SUPPORTED_BG_METHODS). */
    public const BG_METHODS = ['photoroom', 'remove_bg_api', 'grabcut', 'rembg', 'none'];

    /** اسم كل طريقة عزل للمالك (زر «رجّع عزل الخلفية (…)»)؛ نفس BG_METHOD_LABELS في health.js و review/app.js. */
    public const BG_METHOD_LABELS = ['photoroom' => 'PhotoRoom', 'remove_bg_api' => 'remove.bg', 'grabcut' => 'GrabCut',
                                     'rembg' => 'rembg', 'none' => 'بدون عزل الخلفية'];

    /**
     * «تجاوز عزل الخلفية»: الطريقة اللي كانت قبل ما يوقف المالك عزل الخلفية (bg_removal_method = none)، بيرجعها زر
     * «رجّع عزل الخلفية». config.load_db_config ما بيقرأ هالمفتاح: هو للوحة بس.
     */
    public const BG_PREVIOUS_KEY = 'bg_removal_method_previous';

    /** تأكيد «تجاوز عزل الخلفية» (صفحة الصحة وشاشة المراجعة): شو بيصير بعده. */
    public const BG_SKIP_CONFIRM = 'تجاوز عزل الخلفية؟ الصور اللي بتعتمدها من هلق (والنشر التلقائي من التشغيل الجاي) '
        . 'بتنتشر متل ما هي على لوحة بيضا بدون عزل خلفيتها: صورة خلفيتها مش بيضا بتبين خلفيتها بالشيت. ما بنعيد أي فحص '
        . 'لحالنا، وفيك ترجّع عزل الخلفية من صفحة الصحة أو من الإعدادات (تبويب «معالجة الصور») بأي وقت.';

    /** طرق العزل المحلية المنزّلة (cli_bridge bg_methods، image_processor.local_methods_available): 6 ساعات. */
    public const LOCAL_METHODS_CACHE_KEY = 'laqta_bg_local_methods_v1';
    public const LOCAL_METHODS_CACHE_SECONDS = 21600;

    /** config.SPREADSHEET_NAME_OR_URL when nothing is set. */
    public const DEFAULT_SHEET = 'automation sheet';

    public const CANVAS_SIZES = [600, 800, 1000, 1200, 1500, 2000];

    /** جولة البحث الإضافية (catalog_match/expand.py) وسياسة الباركود: نفس القيم التي يقبلها config.py. */
    public const VISUAL_SEARCH_MODES = ['auto', 'off', 'serper', 'serpapi'];
    public const GTIN_POLICIES = ['evidence', 'strict', 'off'];
    public const EXPANSION_MAX_CALLS_LIMIT = 8;
    /** كم منتج بيشتغل بنفس الوقت (config.WORKER_CONCURRENCY، catalog_match/settings.py worker_concurrency). */
    public const WORKER_CONCURRENCY_MIN = 1;
    public const WORKER_CONCURRENCY_MAX = 8;
    public const WORKER_CONCURRENCY_DEFAULT = 5;
    /** صفحات الفهرس المحلي اللي بتنقرا لكل منتج (catalog_match/settings.py LOCAL_INDEX_MAX_PAGES_LIMIT). */
    public const LOCAL_INDEX_MAX_PAGES_LIMIT = 8;
    public const CANVAS_MIN = 300;
    public const CANVAS_MAX = 4000;

    /** ما يحفظه كل نموذج: القسم لا يلمس مفتاحاً خارجه. */
    public const SECTIONS = [
        'serper' => ['tab' => 'keys', 'secret' => ['serper_api_key']],
        'gemini' => ['tab' => 'keys', 'secret' => ['gemini_api_key']],
        'photoroom' => ['tab' => 'keys', 'secret' => ['photoroom_api_key']],
        'cloudinary' => ['tab' => 'keys', 'secret' => ['cloudinary_api_key', 'cloudinary_api_secret'],
                         'text' => ['cloudinary_cloud_name']],
        'anthropic' => ['tab' => 'keys', 'secret' => ['anthropic_api_key']],
        'serpapi' => ['tab' => 'keys', 'secret' => ['serpapi_api_key']],
        'auto-publish' => ['tab' => 'auto-publish', 'checkbox' => ['auto_publish_enabled']],
        'processing' => ['tab' => 'processing', 'text' => ['output_canvas_size', 'bg_removal_method', 'output_background'],
                         'checkbox' => ['enable_image_enhancement']],
        // مصادر البحث الإضافية وسياسة الباركود (المرحلة الثالثة): بتبويب «متقدم»، نموذج منفصل
        'sources' => ['tab' => 'advanced', 'text' => ['expansion_max_calls', 'visual_search', 'serpapi_lens_price_usd',
                                                     'gtin_policy', 'local_index_max_pages'],
                      'checkbox' => ['expansion_enabled', 'local_index_enabled']],
        // سرعة التشغيل (حزمة السرعة): كم منتج بيشتغل بنفس الوقت، بتبويب «متقدم»، نموذج منفصل
        'speed' => ['tab' => 'advanced', 'text' => ['worker_concurrency']],
        // نموذج Gemini صار بتبويب «نماذج التحقق» (saveModels): «متقدم» ما بيكتبه
        'advanced' => ['tab' => 'advanced', 'secret' => ['google_search_api_key', 'proxy_url'],
                       'text' => ['google_search_cx'],
                       'checkbox' => ['strict_brand_match']],
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
        // اختيارية: بلا مفتاح ما في تحذير، بس الميزة ما بتشتغل
        'anthropic' => ['name' => 'Anthropic (Claude)', 'use' => 'نموذج تحقق إضافي', 'service' => 'anthropic',
                        'keys' => ['anthropic_api_key' => 'ANTHROPIC_API_KEY'], 'optional' => true],
        'serpapi' => ['name' => 'SerpApi', 'use' => 'البحث بالصورة', 'service' => 'serpapi',
                      'keys' => ['serpapi_api_key' => 'SERPAPI_API_KEY'], 'optional' => true],
    ];

    /** مفاتيح سرية خارج قائمة المرحلة الأولى (SECRET_KEYS): تُحفظ من نموذجها فقط وتُحجب من السجلات. */
    public const EXTRA_SECRET_KEYS = ['anthropic_api_key', 'serpapi_api_key'];

    /**
     * نماذج قراءة الملصق المدعومة (نفس catalog_match/verifiers/registry.SUPPORTED_MODELS) وأسعارها التقديرية
     * بالدولار لكل مليون token (نفس pricing.DEFAULT_PRICES). المالك بيعدّل الأسعار من تبويب «نماذج التحقق».
     */
    public const VERIFIER_MODELS = [
        'gemini:gemini-3.1-flash-lite' => ['label' => 'Gemini 3.1 Flash-Lite', 'provider' => 'gemini', 'input' => 0.25, 'output' => 1.50],
        'gemini:gemini-3.5-flash' => ['label' => 'Gemini 3.5 Flash', 'provider' => 'gemini', 'input' => 0.50, 'output' => 3.00],
        'claude:claude-haiku-4-5' => ['label' => 'Claude Haiku 4.5', 'provider' => 'claude', 'input' => 1.00, 'output' => 5.00],
        'claude:claude-sonnet-5-5' => ['label' => 'Claude Sonnet 5.5', 'provider' => 'claude', 'input' => 2.00, 'output' => 10.00],
        'claude:claude-opus-5-5' => ['label' => 'Claude Opus 5.5', 'provider' => 'claude', 'input' => 4.00, 'output' => 20.00],
    ];
    public const VERIFIER_DEFAULT_PRIMARY_MODEL = 'gemini-3.1-flash-lite';
    public const VERIFIER_DEFAULT_STRONG = 'gemini:gemini-3.5-flash';
    public const VERIFIER_DEFAULT_BUDGET = 5.0;
    public const VERIFIER_BUDGET_MAX = 1000;
    public const VERIFIER_PRICE_MAX = 1000;

    /** تقدير tokens لكل منتج: نفس ثوابت catalog_match/verifiers/pricing.py (اختبار بايثون بيقارن الاثنين). */
    public const VERIFIER_ESTIMATE = [
        'primary_images' => 4, 'primary_long_side' => 1024, 'strong_long_side' => 1568, 'prompt_tokens' => 800,
        'gemini_image_tokens' => 1120, 'output_per_image' => 120,
        'output_base' => ['gemini' => 400, 'claude' => 600, 'claude-haiku' => 100],
    ];

    /**
     * قارئ أسماء الشيت المختصرة (catalog_match/normalizer.py، QUERY_NORMALIZER): gemini أو off، الافتراضي gemini، وأي قيمة
     * غيرهم بتنقرا off. تقدير tokens لكل منتج نفس normalizer.estimate_tokens لسطر مرجعي (اختبار بايثون بيقارن الاثنين).
     */
    public const QUERY_NORMALIZER_MODES = ['gemini', 'off'];
    public const QUERY_NORMALIZER_DEFAULT = 'gemini';
    public const NORMALIZER_ESTIMATE = ['model' => 'gemini:gemini-3.1-flash-lite', 'input_tokens' => 368, 'output_tokens' => 120];

    /** مفتاح كل مزود نماذج: الإعداد في system_settings ومتغير البيئة المقابل. */
    public const VERIFIER_KEYS = [
        'gemini' => ['setting' => 'gemini_api_key', 'env' => 'GEMINI_API_KEY', 'name' => 'Gemini'],
        'claude' => ['setting' => 'anthropic_api_key', 'env' => 'ANTHROPIC_API_KEY', 'name' => 'Anthropic'],
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
        'ANTHROPIC_API_KEY', 'SERPAPI_API_KEY',
    ];

    /** متغيرات .env الجذر غير السرية التي يجوز للصفحة قراءة قيمتها. */
    private const READABLE_ENV = [
        'SPREADSHEET_NAME_OR_URL', 'SPREADSHEET_TAB_NAME', 'CREDENTIALS_FILE', 'BG_REMOVAL_METHOD', 'BG_FALLBACK',
        'OUTPUT_CANVAS_SIZE', 'OUTPUT_BACKGROUND', 'GEMINI_MODEL',
        'VERIFIER_PRIMARY', 'VERIFIER_STRONG', 'VERIFIER_MONTHLY_BUDGET_USD', 'MODEL_PRICES',
        'EXPANSION_ENABLED', 'EXPANSION_MAX_CALLS', 'VISUAL_SEARCH', 'SERPAPI_LENS_PRICE_USD', 'GTIN_POLICY',
        'LOCAL_INDEX_ENABLED', 'LOCAL_INDEX_MAX_PAGES', 'WORKER_CONCURRENCY', 'QUERY_NORMALIZER',
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
            case 'models':
                $data['models'] = self::modelsData($stored ?? [], self::verifierSpend());
                break;
            case 'auto-publish':
                $result = PythonBridge::run('review_stats');
                $data['autoPublish'] = self::autoPublishData($stored ?? [], $result);
                break;
            case 'processing':
                $data['processing'] = self::processingData($stored ?? [], self::localMethods());
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
        if ($section === 'models') {
            return $this->saveModels($request);
        }
        if ($section === 'strict-lane') {
            return $this->toggleStrictLane($request);
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
                if ($k === 'expansion_max_calls') {
                    if (!preg_match('/^\d{1,2}$/', $val) || (int) $val > self::EXPANSION_MAX_CALLS_LIMIT) {
                        $warnings[] = 'عدد الطلبات الإضافية لازم يكون رقم من 0 لـ ' . self::EXPANSION_MAX_CALLS_LIMIT
                            . '؛ ما تغيّر الرقم المحفوظ.';
                        continue;
                    }
                    $val = (string) (int) $val;
                }
                if ($k === 'worker_concurrency') {
                    if ($val === '') {
                        continue;   // حقل غايب (نموذج أقدم): الرقم المحفوظ بيضل
                    }
                    if (!preg_match('/^\d{1,2}$/', $val) || (int) $val < self::WORKER_CONCURRENCY_MIN
                        || (int) $val > self::WORKER_CONCURRENCY_MAX) {
                        $warnings[] = 'عدد المنتجات بنفس الوقت لازم يكون رقم من ' . self::WORKER_CONCURRENCY_MIN . ' لـ '
                            . self::WORKER_CONCURRENCY_MAX . '؛ ما تغيّر الرقم المحفوظ.';
                        continue;
                    }
                    $val = (string) (int) $val;
                }
                if ($k === 'local_index_max_pages') {
                    if ($val === '') {
                        continue;   // حقل غايب (نموذج أقدم): الرقم المحفوظ بيضل
                    }
                    if (!preg_match('/^\d{1,2}$/', $val) || (int) $val > self::LOCAL_INDEX_MAX_PAGES_LIMIT) {
                        $warnings[] = 'عدد صفحات الفهرس المحلي لازم يكون رقم من 0 لـ ' . self::LOCAL_INDEX_MAX_PAGES_LIMIT
                            . '؛ ما تغيّر الرقم المحفوظ.';
                        continue;
                    }
                    $val = (string) (int) $val;
                }
                if ($k === 'visual_search' && !in_array($val, self::VISUAL_SEARCH_MODES, true)) {
                    $warnings[] = 'طريقة البحث المرئي هاي مش مدعومة؛ ما تغيّرت المحفوظة.';
                    continue;
                }
                if ($k === 'serpapi_lens_price_usd') {
                    if ($val === '') {
                        continue;
                    }
                    if (!is_numeric($val) || (float) $val < 0 || (float) $val > 1) {
                        $warnings[] = 'سعر بحث SerpApi لازم يكون رقم بين 0 و1 دولار؛ ما تغيّر السعر المحفوظ.';
                        continue;
                    }
                    $val = (string) (float) $val;
                }
                if ($k === 'gtin_policy' && !in_array($val, self::GTIN_POLICIES, true)) {
                    $warnings[] = 'سياسة الباركود هاي مش مدعومة؛ ما تغيّرت المحفوظة.';
                    continue;
                }
                if ($k === 'output_background') {
                    if ($val === '') {
                        continue;   // حقل غايب (نموذج أقدم): الخلفية المحفوظة بتضل
                    }
                    if (!array_key_exists($val, self::OUTPUT_BACKGROUNDS)) {
                        $warnings[] = 'خلفية الصورة هاي مش مدعومة؛ ما تغيّرت المحفوظة.';
                        continue;
                    }
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

            // «لما يخلص رصيد خدمة العزل: جرّب طريقة محلية مجانية» (bg_fallback = local | off): المفتاح بيظهر بالنموذج بس لما في
            // طريقة محلية منزّلة، وحقل bg_fallback_shown بيقول إنه انعرض. بدونه (نموذج أقدم، أو ما في طريقة محلية) القيمة
            // المحفوظة بتضل متل ما هي بدل ما يطفيها حفظ ما شاف المفتاح.
            if ($section === 'processing' && $request->has('bg_fallback_shown')) {
                $changes['bg_fallback'] = $request->has('bg_fallback') ? 'local' : 'off';
            }

            // «بدون عزل الخلفية» من النموذج نفسه: الطريقة القديمة بتنحفظ لزر «رجّع عزل الخلفية» (متل setBgMethod)
            if (($changes['bg_removal_method'] ?? null) === 'none') {
                $current = self::currentBgMethod(self::storedValues(['bg_removal_method'])['bg_removal_method'] ?? '');
                if ($current !== 'none') {
                    $changes[self::BG_PREVIOUS_KEY] = $current;
                }
            }

            // النشر الآلي لا يُشغَّل بدون ماركة جاهزة مفعّلة (من أي نموذج، ومنه الحفظ القديم الكامل)
            if (($changes['auto_publish_enabled'] ?? null) === 'true') {
                $current = self::storedValues(['auto_publish_enabled', 'auto_publish_brands', self::STRICT_LANE_KEY]);
                if (($current['auto_publish_enabled'] ?? '') !== 'true') {
                    $brands = $changes['auto_publish_brands'] ?? ($current['auto_publish_brands'] ?? '');
                    $why = self::autoPublishBlocker(self::brandList($brands), PythonBridge::run('review_stats'),
                        self::strictLaneOn($current[self::STRICT_LANE_KEY] ?? null));
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
     * POST /api/settings/bg-method {method}: «تجاوز عزل الخلفية» (method = none) و«رجّع عزل الخلفية» (الطريقة القديمة)
     * من صفحة الصحة وشاشة المراجعة وتبويب «معالجة الصور». method لازم يكون من BG_METHODS (غيره 422). الإيقاف بيحفظ
     * الطريقة الحالية بـ BG_PREVIOUS_KEY؛ ما بيعيد أي فحص لحاله. الاعتماد (جسر جديد لكل طلب) بيشوف التغيير فوراً،
     * والعامل من تشغيله الجاي. الرد: {status, method, previous, previous_label, changed, message}.
     */
    public function setBgMethod(Request $request)
    {
        $method = $request->input('method');
        $method = is_string($method) ? strtolower(trim($method)) : '';
        if (!in_array($method, self::BG_METHODS, true)) {
            return self::jsonResponse(['status' => 'failed',
                'error' => 'طريقة عزل الخلفية هاي مش مدعومة. حدّث الصفحة وجرّب مرة تانية.'], 422);
        }
        try {
            $stored = self::storedValues(['bg_removal_method', self::BG_PREVIOUS_KEY]);
            $current = self::currentBgMethod($stored['bg_removal_method'] ?? '');
            $changes = [];
            if ($method !== $current) {
                $changes['bg_removal_method'] = $method;
                if ($method === 'none') {
                    $changes[self::BG_PREVIOUS_KEY] = $current;
                }
            }
            self::write($changes);
            $previous = $changes[self::BG_PREVIOUS_KEY] ?? ($stored[self::BG_PREVIOUS_KEY] ?? '');
        } catch (\Throwable $e) {
            return self::jsonResponse(['status' => 'failed',
                'error' => 'ما انحفظ: قاعدة البيانات ما ردّت. جرّب كمان شوي.'], 503);
        }
        $state = self::bgState(['bg_removal_method' => ['value' => $method], self::BG_PREVIOUS_KEY => ['value' => $previous]]);
        $message = $method === 'none'
            ? 'عزل الخلفية متوقف: الصور اللي بتعتمدها من هلق بتنتشر متل ما هي على لوحة بيضا. اضغط «افحص النشر» مرة تانية لتتأكد إنو النشر صار يمشي.'
            : 'رجع عزل الخلفية بـ ' . self::BG_METHOD_LABELS[$method] . '. اضغط «افحص النشر» مرة تانية لتتأكد إنه شغّال.';
        return self::jsonResponse(['status' => 'success', 'method' => $state['method'], 'previous' => $state['previous'],
            'previous_label' => $state['previous_label'], 'changed' => $changes !== [], 'message' => $message], 200);
    }

    private static function jsonResponse(array $body, int $code)
    {
        return response()->json($body, $code, [], JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE)
            ->header('Cache-Control', 'no-store');
    }

    /**
     * «النشر الآلي لكل الماركات المؤكدة» (auto_publish_strict_lane): التشغيل والإطفاء بأي وقت. التشغيل آمن قبل ما تجهز
     * الفئة: catalog_match.decide ما بينشر منها شي لحتى تثبت مراجعاتها دقتها (نفس عتبات الماركة)، والرسالة بتقول كم
     * اعتماد بقي (review_stats). config.load_db_config بيقرأ المفتاح، والقيمة المحفوظة بتغلب الافتراضي.
     */
    private function toggleStrictLane(Request $request)
    {
        $on = $request->has(self::STRICT_LANE_KEY);
        try {
            self::write([self::STRICT_LANE_KEY => $on ? 'true' : 'false']);
        } catch (\Throwable $e) {
            return self::back('auto-publish', ['error' => 'ما انحفظ التغيير: قاعدة البيانات ما ردّت. جرّب كمان شوي.']);
        }
        if (!$on) {
            return self::back('auto-publish', ['success' => 'وقّفنا النشر الآلي لكل الماركات المؤكدة: كل اقتراحاته رح '
                . 'تستنى مراجعتك. بس الماركات المفعّلة بالجدول بتنرفع بدون مراجعة. بترجّعه من نفس المفتاح وقت ما بدك.']);
        }
        return self::back('auto-publish', ['success' => self::strictLaneOnMessage(PythonBridge::run('review_stats'))]);
    }

    /** رسالة تشغيل «النشر الآلي لكل الماركات المؤكدة»: شغّال، ومن إيمتى بينشر لحاله (review_stats.lanes.strict). */
    public static function strictLaneOnMessage(array $stats): string
    {
        $head = 'النشر الآلي لكل الماركات المؤكدة شغّال.';
        $strict = (array) (((array) ($stats['lanes'] ?? []))['strict'] ?? []);
        if (($stats['status'] ?? '') !== 'success') {
            return $head . ' ما بينشر شي لحاله قبل ما تثبت دقته بمراجعاتك (ما قدرنا نحسبها هلق).';
        }
        if (($strict['status'] ?? '') === 'ready') {
            return $head . ' فئته جاهزة: الاقتراح المؤكد تماماً لماركة موجودة بـ Brands Mapping رح ينرفع بدون مراجعتك من التشغيل الجاي.';
        }
        $more = self::laneMoreNeeded($strict);
        return $more === null
            ? $head . ' بس دقة هالاقتراحات هلق أقل من المطلوب، فما رح ينشر شي لحاله لحتى تتحسن بمراجعاتك.'
            : $head . ' بس ما رح ينشر شي لحاله قبل ما تعتمد ' . $more . ' اقتراح مؤكد كمان بدون ولا رفض.';
    }

    /** المفتاح متل ما بيقرأه بايثون: القيمة المحفوظة، وإلا (ما في صف أو خلية فاضية) الافتراضي STRICT_LANE_DEFAULT. */
    public static function strictLaneOn($value): bool
    {
        $text = strtolower(trim((string) ($value ?? '')));
        if ($text === '') {
            return self::STRICT_LANE_DEFAULT;
        }
        return in_array($text, ['1', 'true', 'yes', 'on'], true);
    }

    /** كم اعتماد متتالي بلا رفض بقي لتجهز فئة (more_needed، وإلا reviews_needed ناقص المراجَع)؛ null = الدقة أقل من العتبة. */
    public static function laneMoreNeeded(array $lane): ?int
    {
        if (is_numeric($lane['more_needed'] ?? null)) {
            return max(1, (int) $lane['more_needed']);
        }
        if (is_numeric($lane['reviews_needed'] ?? null)) {
            return max(1, (int) $lane['reviews_needed'] - (int) ($lane['prechecked'] ?? 0));
        }
        return null;
    }

    /** سبب منع تشغيل النشر الآلي لكل الماركات المؤكدة، أو null لما تكون فئة strict جاهزة. */
    public static function strictLaneBlocker(array $stats): ?string
    {
        if (($stats['status'] ?? '') !== 'success') {
            return 'ما قدرنا نتأكد إن الفئة جاهزة، فما شغّلناها. جرّب كمان شوي.';
        }
        $strict = (array) (((array) ($stats['lanes'] ?? []))['strict'] ?? []);
        if (($strict['status'] ?? '') !== 'ready') {
            return 'الاقتراحات المؤكدة لسا مش جاهزة: دقتها ما ثبتت بمراجعات كافية، فما شغّلنا النشر الآلي لكل الماركات المؤكدة.';
        }
        return null;
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
            $current = self::storedValues(['auto_publish_enabled', 'auto_publish_brands', self::STRICT_LANE_KEY]);
            $list = self::brandList($current['auto_publish_brands'] ?? '');
            $key = self::brandKey($brand);

            if ($op === 'disable') {
                $kept = array_values(array_filter($list, fn ($b) => self::brandKey($b) !== $key));
                if (count($kept) === count($list)) {
                    return self::back('auto-publish', ['error' => 'الماركة «' . $brand . '» مش مفعّلة أصلاً.']);
                }
                $changes = ['auto_publish_brands' => implode(', ', $kept)];
                $message = 'وقّفنا النشر الآلي لـ «' . $brand . '». صورها رح تستنى مراجعتك.';
                if ($kept === [] && ($current['auto_publish_enabled'] ?? '') === 'true'
                        && !self::strictLaneOn($current[self::STRICT_LANE_KEY] ?? null)) {
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
            $state = self::keyState($saved, is_array($service) ? (string) ($service['status'] ?? '') : null, $checkedAt, $changedAt);
            if (!$saved && !empty($p['optional'])) {
                $state = ['state' => 'مش مضبوط · اختياري', 'tone' => 'muted'];
            }
            $rows[] = array_merge(
                ['id' => $id, 'name' => $p['name'], 'use' => $p['use'], 'saved' => $saved, 'from_env' => $saved && $fromEnv,
                 'fields' => array_keys($p['keys']),
                 // the only non-secret field of the tab: the Cloudinary account name (public in every image URL)
                 'cloud_name' => $id === 'cloudinary' ? trim((string) ($stored['cloudinary_cloud_name']['value'] ?? '')) : null],
                $state
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
     * جدول النشر الآلي من review_stats: لكل ماركة المراجعات والدقة وأقل دقة متوقعة (الحد الأدنى لويلسون) وحالتها، مع مدخلات
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
        $lane = self::laneData($stored, $stats);
        return [
            'status' => $ok ? 'ok' : 'error',
            'enabled' => $enabled,
            'listed' => $listed,
            'rows' => $rows,
            'ready_listed' => $readyListed,
            'ready_count' => count(array_filter($rows, fn ($r) => $r['ready'])),
            'reviews' => $ok ? (int) ($stats['overall']['actions'] ?? 0) : null,
            // the main switch opens with a ready brand switched on, or with the strict lane switched on and ready
            'can_enable' => $ok && ($readyListed !== [] || ($lane['enabled'] && $lane['ready'])),
            'criterion' => self::criterionText($minBound, $perfect),
            'lane' => $lane,
        ];
    }

    /**
     * «النشر الآلي لكل الماركات المؤكدة» من review_stats.lanes: أرقام فئة strict (اقتراح عدّى كل قواعد النشر الآلي إلا
     * إعداد الماركة) وحالتها، ومفتاحها (auto_publish_strict_lane، شغّال افتراضياً) بيتشغّل وبيطفى بأي وقت؛ وفئة unsure
     * للعلم بس. state: جملة وحدة بتقول إذا شغّال، ومن إيمتى بينشر لحاله (كم اعتماد بقي)؛ how: كيف بيطفى أو بيرجع.
     */
    public static function laneData(array $stored, array $stats): array
    {
        $on = self::strictLaneOn($stored[self::STRICT_LANE_KEY]['value'] ?? null);
        $ok = ($stats['status'] ?? '') === 'success' && is_array($stats['lanes'] ?? null);
        $strict = $ok ? (array) ($stats['lanes']['strict'] ?? []) : [];
        $unsure = $ok ? (array) ($stats['lanes']['unsure'] ?? []) : [];
        $n = (int) ($strict['prechecked'] ?? 0);
        $accepted = (int) ($strict['accepted'] ?? 0);
        $status = (string) ($strict['status'] ?? 'needs_reviews');
        $ready = $ok && $status === 'ready';
        $needed = is_numeric($strict['reviews_needed'] ?? null) ? (int) $strict['reviews_needed'] : null;

        if (!$ok) {
            [$chip, $tone] = ['ما منعرف هلق', 'none'];
        } elseif ($ready) {
            [$chip, $tone] = $on ? ['شغّال', 'approved'] : ['جاهزة', 'proposed'];
        } elseif ($status === 'low_precision') {
            [$chip, $tone] = ['دقة أقل من المطلوب', 'error'];
        } elseif ($needed === null) {
            [$chip, $tone] = ['بعيدة عن المطلوب', 'none'];
        } else {
            [$chip, $tone] = ['تحتاج ' . max(1, $needed - $n) . ' مراجعة', 'none'];
        }
        $text = $n === 0 ? 'لسا ما راجعت ولا اقتراح بهالفئة.' : 'من ' . $n . ' اقتراح بهالفئة، اعتمدت ' . $accepted . '.';
        $details = [];
        if ($n > 0) {
            $details[] = 'أقل دقة متوقعة ' . self::pct($strict['lower_bound'] ?? null);
            if ((int) ($strict['replaced'] ?? 0) > 0) {
                $details[] = 'بدّلت ' . (int) $strict['replaced'];
            }
            if ((int) ($strict['rejected'] ?? 0) > 0) {
                $details[] = 'رفضت ' . (int) $strict['rejected'];
            }
        }
        $un = (int) ($unsure['prechecked'] ?? 0);
        $unsureText = 'الملصق مش واضح بس الاسم مطابق: '
            . ($un === 0 ? 'لسا ما راجعت ولا اقتراح من هالنوع.' : 'اعتمدت ' . (int) ($unsure['accepted'] ?? 0) . ' من ' . $un . '.')
            . ' للعلم بس: هالنوع ما بينتشر آلياً.';
        $more = $ok && !$ready ? self::laneMoreNeeded($strict) : null;
        if (!$on) {
            $state = 'مطفي: كل الاقتراحات المؤكدة بتستنى مراجعتك قبل ما توصل الشيت.';
            $how = 'لترجّعه: علّم المفتاح فوق واضغط «حفظ».';
        } else {
            if (!$ok) {
                $state = 'شغّال، بس ما بينشر شي لحاله قبل ما تثبت دقته بمراجعاتك (ما قدرنا نحسبها هلق).';
            } elseif ($ready) {
                $state = 'شغّال وعم ينشر: الاقتراح المؤكد تماماً لماركة موجودة بـ Brands Mapping بينرفع وبينكتب بالشيت بدون مراجعتك.';
            } elseif ($more === null) {
                $state = 'شغّال، بس دقة هالاقتراحات هلق أقل من المطلوب، فما رح ينشر شي لحاله لحتى تتحسن بمراجعاتك.';
            } else {
                $state = 'شغّال، بس لسا ما بينشر لحاله: بيبلّش ينشر بعد ما تعتمد ' . $more
                    . ' اقتراح مؤكد كمان بدون ولا رفض. لحد هداك الوقت كل شي بيستنى مراجعتك.';
            }
            $how = 'لتطفيه: شيل علامة المفتاح فوق واضغط «حفظ»، وبعدها كل اقتراحاته بتستنى مراجعتك.';
        }
        return [
            'status' => $ok ? 'ok' : 'error',
            'enabled' => $on,
            'ready' => $ready,
            // التشغيل آمن بأي وقت: بايثون ما بينشر من الفئة شي قبل ما تجهز (decide.strict_lane_readiness)
            'can_enable' => true,
            'publishing' => $on && $ready,
            'more_needed' => $more,
            'state' => $state,
            'how' => $how,
            'reviews' => $n,
            'accepted' => $accepted,
            'text' => $text,
            'detail' => implode(' · ', $details),
            'chip' => $chip,
            'tone' => $tone,
            'unsure_text' => $ok ? $unsureText : '',
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

    /**
     * سبب منع تشغيل النشر الآلي، أو null إذا في ماركة جاهزة مفعّلة (أو النشر الآلي لكل الماركات المؤكدة شغّال وفئته
     * لسا جاهزة).
     */
    public static function autoPublishBlocker(array $brands, array $stats, bool $strictLane = false): ?string
    {
        if (($stats['status'] ?? '') !== 'success') {
            return 'ما قدرنا نتأكد من جاهزية الماركات، فالنشر الآلي ضل مطفأ.';
        }
        if ($strictLane && self::strictLaneBlocker($stats) === null) {
            return null;
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

    /**
     * تبويب «معالجة الصور». $local: طرق العزل المحلية المنزّلة ({grabcut, rembg}: true / false / null = ما منعرف)،
     * من localMethods(). GrabCut و rembg بينذكروا وبينختاروا بس إذا منزّلين (لا وعد بشي مش موجود)، و remove.bg بس إذا
     * مفتاحه بالبيئة أو هو المضبوط. «بدون عزل الخلفية» بتنشر الصورة متل ما هي (main.publish_image: bg_skipped).
     */
    public static function processingData(array $stored, ?array $local = null): array
    {
        $env = self::envValues(['OUTPUT_CANVAS_SIZE']);
        $size = trim((string) ($stored['output_canvas_size']['value'] ?? ''));
        if (!preg_match('/^\d+$/', $size)) {
            $size = preg_match('/^\d+$/', (string) ($env['OUTPUT_CANVAS_SIZE'] ?? '')) ? $env['OUTPUT_CANVAS_SIZE'] : '800';
        }
        $method = self::currentBgMethod($stored['bg_removal_method']['value'] ?? '');
        $sizes = self::CANVAS_SIZES;
        if (!in_array((int) $size, $sizes, true)) {
            $sizes[] = (int) $size;
            sort($sizes);
        }
        $local = ['grabcut' => $local['grabcut'] ?? null, 'rembg' => $local['rembg'] ?? null];
        $methods = ['photoroom' => 'PhotoRoom: عزل الخلفية سحابياً (الافتراضي، من رصيد PhotoRoom)'];
        if ($method === 'remove_bg_api' || self::envHas('REMOVE_BG_API_KEY')) {
            $methods['remove_bg_api'] = 'remove.bg: عزل الخلفية سحابياً (من رصيد remove.bg)';
        }
        if ($local['grabcut'] === true || $method === 'grabcut') {
            $methods['grabcut'] = 'GrabCut: عزل محلي مجاني على هالجهاز (أقل دقة)';
        }
        if ($local['rembg'] === true || $method === 'rembg') {
            $methods['rembg'] = 'rembg: عزل محلي مجاني على هالجهاز';
        }
        $methods['none'] = 'بدون عزل الخلفية: الصورة متل ما هي على لوحة بيضا وبتنتشر مباشرة';
        if (!isset($methods[$method])) {
            $methods[$method] = $method . ' (المضبوط حالياً)';
        }
        $free = array_values(array_filter(['grabcut' => 'GrabCut', 'rembg' => 'rembg'],
            fn ($k) => $local[$k] === true, ARRAY_FILTER_USE_KEY));
        $hint = 'إذا فشل العزل، الصورة ما بتنزل الشيت بصمت: بتستنى مراجعتك. «بدون عزل الخلفية» بتنشر الصورة متل ما هي '
            . 'بدون أي طلب مدفوع، فصورة خلفيتها مش بيضا بتبين خلفيتها بالشيت.';
        if ($free) {
            $hint .= ' ' . implode(' و', $free) . (count($free) > 1 ? ' طرق محلية مجانية منزّلة' : ' طريقة محلية مجانية منزّلة')
                . ' على هالجهاز، بس دقتها أقل من PhotoRoom وفحص القص ممكن يرفض صور أكتر.';
        }
        return [
            'size' => (int) $size,
            'sizes' => $sizes,
            'method' => $method,
            'methods' => $methods,
            'hint' => $hint,
            // مفتاح العزل المحلي البديل بيظهر بس لما rembg منزّلة (البديل التلقائي rembg بموديل BiRefNet فقط، GrabCut ما بيدخل
            // أبداً؛ لا وعد بشي مش موجود)؛ إطفاؤه محفوظ بـ 'off'
            'fallback' => ['show' => $local['rembg'] === true, 'on' => self::currentBgFallback($stored['bg_fallback']['value'] ?? '') === 'local'],
            'bg' => self::bgState($stored),
            'enhance' => strtolower(trim((string) ($stored['enable_image_enhancement']['value'] ?? ''))) === 'true',
            'background' => self::currentBackground($stored['output_background']['value'] ?? ''),
            'backgrounds' => self::OUTPUT_BACKGROUNDS,
        ];
    }

    /** الخلفية اللي بايثون رح يستعملها: المحفوظة إذا مدعومة، وإلا OUTPUT_BACKGROUND من البيئة، وإلا شفافة. */
    public static function currentBackground($stored): string
    {
        foreach ([$stored, self::envValues(['OUTPUT_BACKGROUND'])['OUTPUT_BACKGROUND'] ?? ''] as $value) {
            $value = strtolower(trim((string) $value));
            if (array_key_exists($value, self::OUTPUT_BACKGROUNDS)) {
                return $value;
            }
        }
        return 'transparent';
    }

    /**
     * الطريقة اللي بايثون رح يستعملها (config.load_db_config): المحفوظة إذا مدعومة، وإلا BG_REMOVAL_METHOD من البيئة،
     * وإلا photoroom.
     */
    public static function currentBgMethod($stored): string
    {
        $method = strtolower(trim(is_scalar($stored) ? (string) $stored : ''));
        if (in_array($method, self::BG_METHODS, true)) {
            return $method;
        }
        $fromEnv = strtolower(trim((string) (self::envValues(['BG_REMOVAL_METHOD'])['BG_REMOVAL_METHOD'] ?? '')));
        return $fromEnv !== '' ? $fromEnv : 'photoroom';
    }

    /** القيمة اللي بايثون رح يستعملها (config.load_db_config): المحفوظة إذا مدعومة، وإلا BG_FALLBACK من البيئة، وإلا local. */
    public static function currentBgFallback($stored): string
    {
        $value = strtolower(trim(is_scalar($stored) ? (string) $stored : ''));
        if (in_array($value, self::BG_FALLBACKS, true)) {
            return $value;
        }
        $fromEnv = strtolower(trim((string) (self::envValues(['BG_FALLBACK'])['BG_FALLBACK'] ?? '')));
        return in_array($fromEnv, self::BG_FALLBACKS, true) ? $fromEnv : 'local';
    }

    /**
     * حالة «تجاوز عزل الخلفية» من system_settings (stored() أو قيم بنفس الشكل): {method, off, previous,
     * previous_label}. previous: الطريقة اللي بيرجعها «رجّع عزل الخلفية» (المحفوظة قبل الإيقاف، وإلا PhotoRoom).
     */
    public static function bgState(array $stored): array
    {
        $method = self::currentBgMethod($stored['bg_removal_method']['value'] ?? '');
        $previous = strtolower(trim((string) ($stored[self::BG_PREVIOUS_KEY]['value'] ?? '')));
        if (!in_array($previous, self::BG_METHODS, true) || $previous === 'none') {
            $previous = 'photoroom';
        }
        return ['method' => $method, 'off' => $method === 'none', 'previous' => $previous,
                'previous_label' => self::BG_METHOD_LABELS[$previous]];
    }

    /** bgState من قاعدة البيانات، أو null إذا ما ردّت (البطاقات ما بتعرض زر ما بتقدر تحفظه). */
    public static function currentBgState(): ?array
    {
        try {
            $values = self::storedValues(['bg_removal_method', self::BG_PREVIOUS_KEY]);
        } catch (\Throwable $e) {
            return null;
        }
        return self::bgState(array_map(fn ($v) => ['value' => $v], $values));
    }

    /**
     * طرق العزل المحلية المنزّلة على هالجهاز ({grabcut, rembg}) بنفس قرار image_processor (cli_bridge bg_methods)،
     * مخزّنة 6 ساعات؛ الجسر ما ردّ: {null, null} (ما منذكر ولا وحدة) وما بينخزن.
     */
    public static function localMethods(): array
    {
        $cached = Cache::get(self::LOCAL_METHODS_CACHE_KEY);
        if (is_array($cached)) {
            return $cached;
        }
        $result = PythonBridge::run('bg_methods');
        $local = is_array($result['local'] ?? null) ? $result['local'] : null;
        if (($result['status'] ?? '') !== 'success' || $local === null) {
            return ['grabcut' => null, 'rembg' => null];
        }
        $out = ['grabcut' => ($local['grabcut'] ?? null) === true, 'rembg' => ($local['rembg'] ?? null) === true];
        Cache::put(self::LOCAL_METHODS_CACHE_KEY, $out, self::LOCAL_METHODS_CACHE_SECONDS);
        return $out;
    }

    public static function advancedData(array $stored): array
    {
        $value = fn (string $k) => trim((string) ($stored[$k]['value'] ?? ''));
        $model = $value('gemini_model') !== '' ? $value('gemini_model') : 'gemini-3.1-flash-lite';
        $secrets = [];
        foreach (self::LEGACY_SECRETS as $k => $meta) {
            $secrets[$k] = ['label' => $meta['label'], 'saved' => $value($k) !== '' || self::envHas($meta['env'])];
        }
        $strict = $value('strict_brand_match');
        return [
            'model' => $model,
            'models' => ProductController::SUPPORTED_GEMINI_MODELS,
            'model_supported' => array_key_exists($model, ProductController::SUPPORTED_GEMINI_MODELS),
            'cx' => $value('google_search_cx'),
            'strict' => $strict === '' ? true : $strict === 'true',
            'secrets' => $secrets,
            'sources' => self::sourcesData($stored),
            'speed' => self::speedData($stored),
            'local_index' => self::localIndexStats(),
        ];
    }

    /**
     * «السرعة»: كم منتج بيشتغل بنفس الوقت كما يقرؤه config.py (المحفوظ، وإلا .env، وإلا 5)، ضمن 1..8.
     */
    public static function speedData(array $stored): array
    {
        $env = self::envValues(['WORKER_CONCURRENCY']);
        $v = trim((string) ($stored['worker_concurrency']['value'] ?? ''));
        if ($v === '') {
            $v = trim((string) ($env['WORKER_CONCURRENCY'] ?? ''));
        }
        $n = preg_match('/^-?\d+$/', $v) ? (int) $v : self::WORKER_CONCURRENCY_DEFAULT;
        return [
            'workers' => min(self::WORKER_CONCURRENCY_MAX, max(self::WORKER_CONCURRENCY_MIN, $n)),
        ];
    }

    /**
     * ما يحمله الفهرس المحلي (scripts/build_catalog_index.py): عدد صفحات المنتجات لكل متجر وآخر جمع. null إذا الجدول
     * لسا ما انعمل أو قاعدة البيانات مش متاحة (الصفحة بتقول كيف ينبني).
     */
    public static function localIndexStats(): ?array
    {
        try {
            $stores = DB::table('catalog_products')->select('store', DB::raw('COUNT(*) AS products'))
                ->groupBy('store')->orderBy('store')->pluck('products', 'store')->map(fn ($n) => (int) $n)->all();
            if ($stores === []) {
                return null;
            }
            $last = DB::table('catalog_harvests')->max('finished_at');
        } catch (\Throwable $e) {
            return null;
        }
        return ['products' => array_sum($stores), 'stores' => $stores, 'last' => $last ? (string) $last : ''];
    }

    /**
     * «مصادر البحث الإضافية»: القيم كما يقرؤها config.py (المحفوظ، وإلا .env، وإلا الافتراضي). لا مفتاح هنا:
     * مفتاح SerpApi بتبويب «المفاتيح»، والصفحة بتقول بس إذا هو محفوظ.
     */
    public static function sourcesData(array $stored): array
    {
        $env = self::envValues(['EXPANSION_ENABLED', 'EXPANSION_MAX_CALLS', 'VISUAL_SEARCH', 'SERPAPI_LENS_PRICE_USD',
                                'GTIN_POLICY', 'LOCAL_INDEX_ENABLED', 'LOCAL_INDEX_MAX_PAGES']);
        $pick = function (string $k, string $envName, string $default) use ($stored, $env): string {
            $v = trim((string) ($stored[$k]['value'] ?? ''));
            if ($v === '') {
                $v = trim((string) ($env[$envName] ?? ''));
            }
            return $v !== '' ? strtolower($v) : $default;
        };
        $enabled = $pick('expansion_enabled', 'EXPANSION_ENABLED', 'true');
        $calls = $pick('expansion_max_calls', 'EXPANSION_MAX_CALLS', '4');
        $visual = $pick('visual_search', 'VISUAL_SEARCH', 'auto');
        $price = $pick('serpapi_lens_price_usd', 'SERPAPI_LENS_PRICE_USD', '0.015');
        $policy = $pick('gtin_policy', 'GTIN_POLICY', 'evidence');
        $indexOn = $pick('local_index_enabled', 'LOCAL_INDEX_ENABLED', 'true');
        $indexPages = $pick('local_index_max_pages', 'LOCAL_INDEX_MAX_PAGES', '3');
        return [
            'enabled' => in_array($enabled, ['1', 'true', 'yes', 'on'], true),
            'max_calls' => ctype_digit($calls) ? min((int) $calls, self::EXPANSION_MAX_CALLS_LIMIT) : 4,
            'visual' => in_array($visual, self::VISUAL_SEARCH_MODES, true) ? $visual : 'auto',
            'serpapi_price' => is_numeric($price) ? (float) $price : 0.015,
            'serpapi_saved' => trim((string) ($stored['serpapi_api_key']['value'] ?? '')) !== ''
                || self::envHas('SERPAPI_API_KEY'),
            'gtin_policy' => in_array($policy, self::GTIN_POLICIES, true) ? $policy : 'evidence',
            'local_index_enabled' => in_array($indexOn, ['1', 'true', 'yes', 'on'], true),
            'local_index_max_pages' => ctype_digit($indexPages)
                ? min((int) $indexPages, self::LOCAL_INDEX_MAX_PAGES_LIMIT) : 3,
        ];
    }

    // ------------------------------------------------------------------
    // نماذج التحقق (حزمة المحقق، P3): الأساسي والقوي والميزانية والأسعار
    // ------------------------------------------------------------------

    /**
     * تبويب «نماذج التحقق»: الاختيار المحفوظ (أو .env أو الافتراضي كما يقرؤه config.py)، صف لكل نموذج مدعوم بسعره
     * وتقدير تكلفته لكل 100 منتج، صرف الشهر من verifier_spend، وتحذيرات المفاتيح الناقصة، وقارئ أسماء الشيت المختصرة
     * (query_normalizer) مع تكلفته التقديرية. لا مفتاح يُقرأ هنا: بس محفوظ أو لا.
     */
    public static function modelsData(array $stored, ?array $spend): array
    {
        $value = fn (string $k) => trim((string) ($stored[$k]['value'] ?? ''));
        $env = self::envValues(['GEMINI_MODEL', 'VERIFIER_PRIMARY', 'VERIFIER_STRONG', 'VERIFIER_MONTHLY_BUDGET_USD',
                                'MODEL_PRICES', 'QUERY_NORMALIZER']);
        $gemini = $value('gemini_model') !== '' ? $value('gemini_model')
            : (trim((string) ($env['GEMINI_MODEL'] ?? '')) ?: self::VERIFIER_DEFAULT_PRIMARY_MODEL);
        $primary = strtolower($value('verifier_primary') ?: trim((string) ($env['VERIFIER_PRIMARY'] ?? '')));
        $primary = $primary !== '' ? $primary : 'gemini:' . strtolower($gemini);
        $strong = strtolower($value('verifier_strong') ?: trim((string) ($env['VERIFIER_STRONG'] ?? '')));
        $strong = $strong !== '' ? $strong : self::VERIFIER_DEFAULT_STRONG;
        // '0' (no second look) is a value, not "unset": PHP's ?: would treat it as empty and show the $5 default
        $budgetText = $value('verifier_monthly_budget_usd') !== '' ? $value('verifier_monthly_budget_usd')
            : trim((string) ($env['VERIFIER_MONTHLY_BUDGET_USD'] ?? ''));
        $budget = is_numeric($budgetText) && (float) $budgetText >= 0 ? (float) $budgetText : self::VERIFIER_DEFAULT_BUDGET;
        $prices = self::verifierPrices($value('model_prices') !== '' ? $value('model_prices') : (string) ($env['MODEL_PRICES'] ?? ''));

        $keySaved = [];
        foreach (self::VERIFIER_KEYS as $provider => $k) {
            $keySaved[$provider] = $value($k['setting']) !== '' || self::envHas($k['env']);
        }

        $rows = [];
        foreach (self::VERIFIER_MODELS as $id => $m) {
            $primary100 = self::estimatePer100($id, 'primary', $prices);
            $strong100 = self::estimatePer100($id, 'strong', $prices);
            $rows[] = [
                'id' => $id,
                'slug' => self::modelSlug($id),
                'label' => $m['label'],
                'provider' => $m['provider'],
                'input' => self::plainNumber($prices[$id]['input']),
                'output' => self::plainNumber($prices[$id]['output']),
                'per100_primary' => $primary100,
                'per100_strong' => $strong100,
                'per100_primary_text' => self::money($primary100),
                'per100_strong_text' => self::money($strong100),
                'key_saved' => $keySaved[$m['provider']] ?? false,
            ];
        }

        $warnings = [];
        $strongOff = $strong === 'off';
        foreach ([['primary', $primary], ['strong', $strongOff ? null : $strong]] as [$role, $id]) {
            if ($id === null || !isset(self::VERIFIER_MODELS[$id])) {
                continue;
            }
            $provider = self::VERIFIER_MODELS[$id]['provider'];
            if (!$keySaved[$provider]) {
                $name = self::VERIFIER_KEYS[$provider]['name'];
                $warnings[] = $role === 'primary'
                    ? 'النموذج الأساسي (' . self::VERIFIER_MODELS[$id]['label'] . ') بيحتاج مفتاح ' . $name . '، وهو مش محفوظ: الصور رح تروح لمراجعتك بدون قراءة الملصق. ضيف المفتاح من «المفاتيح».'
                    : 'النموذج القوي (' . self::VERIFIER_MODELS[$id]['label'] . ') بيحتاج مفتاح ' . $name . '، وهو مش محفوظ: ما رح ياخد نظرة تانية. ضيف المفتاح من «المفاتيح» أو وقّفه.';
            }
        }

        $strongSpent = is_array($spend) ? (float) ($spend['strong_usd'] ?? 0) : null;
        $normalizer = strtolower($value('query_normalizer') ?: trim((string) ($env['QUERY_NORMALIZER'] ?? '')));
        $normalizer = $normalizer === '' ? self::QUERY_NORMALIZER_DEFAULT
            : (in_array($normalizer, self::QUERY_NORMALIZER_MODES, true) ? $normalizer : 'off');
        $normalizer100 = self::normalizerPer100($prices);
        return [
            'normalizer' => $normalizer,
            'normalizer_on' => $normalizer === 'gemini',
            'normalizer_key_saved' => $keySaved['gemini'] ?? false,
            'normalizer_per100' => $normalizer100,
            'normalizer_per100_text' => self::money($normalizer100),
            'primary' => $primary,
            'primary_supported' => isset(self::VERIFIER_MODELS[$primary]),
            'strong' => $strong,
            'strong_off' => $strongOff,
            'strong_supported' => $strongOff || isset(self::VERIFIER_MODELS[$strong]),
            'budget' => self::plainNumber($budget),
            'budget_text' => self::money($budget),
            'rows' => $rows,
            'warnings' => $warnings,
            'spend' => $spend === null ? null : [
                'month' => (string) ($spend['month'] ?? gmdate('Y-m')),
                'strong' => self::money($strongSpent ?? 0.0),
                'total' => self::money((float) ($spend['total_usd'] ?? 0)),
                'left' => self::money(max(0.0, $budget - ($strongSpent ?? 0.0))),
                // a budget of 0 is «no second look» (the field's hint), not a month that ran out
                'exhausted' => $strongSpent !== null && $budget > 0 && $strongSpent >= $budget,
            ],
        ];
    }

    /** الأسعار الافتراضية مع المدخلات الصالحة من model_prices (JSON)؛ مدخل تالف يُتجاهل كما في pricing.load_prices. */
    public static function verifierPrices(string $json): array
    {
        $prices = [];
        foreach (self::VERIFIER_MODELS as $id => $m) {
            $prices[$id] = ['input' => (float) $m['input'], 'output' => (float) $m['output']];
        }
        $doc = trim($json) !== '' ? json_decode($json, true) : null;
        if (!is_array($doc)) {
            return $prices;
        }
        foreach ($doc as $id => $entry) {
            $id = is_string($id) ? strtolower(trim($id)) : '';
            if (!isset($prices[$id]) || !is_array($entry)) {
                continue;
            }
            $in = $entry['input'] ?? null;
            $out = $entry['output'] ?? null;
            if (self::validPrice($in) && self::validPrice($out)) {
                $prices[$id] = ['input' => (float) $in, 'output' => (float) $out];
            }
        }
        return $prices;
    }

    private static function validPrice($value): bool
    {
        return is_numeric($value) && (float) $value >= 0 && (float) $value <= self::VERIFIER_PRICE_MAX;
    }

    /** دولار لكل 100 منتج (نفس pricing.estimate_per_100): الأساسي قراءة 4 صور، والقوي نظرة تانية لكل منتج (حد أعلى). */
    public static function estimatePer100(string $id, string $role, array $prices): float
    {
        [$provider, $model] = array_pad(explode(':', $id, 2), 2, '');
        $e = self::VERIFIER_ESTIMATE;
        $strong = $role === 'strong';
        $n = $strong ? 1 : $e['primary_images'];
        $long = $strong ? $e['strong_long_side'] : $e['primary_long_side'];
        $imageTokens = $provider === 'claude' ? (int) ceil($long * $long / 750) : $e['gemini_image_tokens'];
        $in = $e['prompt_tokens'] + $n * $imageTokens;
        $base = $provider === 'claude'
            ? (str_contains($model, 'haiku') ? $e['output_base']['claude-haiku'] : $e['output_base']['claude'])
            : $e['output_base']['gemini'];
        $out = $base + $e['output_per_image'] * $n;
        $price = $prices[$id] ?? ['input' => 0.0, 'output' => 0.0];
        $call = round(($in * $price['input'] + $out * $price['output']) / 1e6, 6);
        return round(100 * $call, 2);
    }

    /** دولار لكل 100 منتج لقارئ أسماء الشيت (نفس 100 * normalizer.estimate_usd): قراءة وحدة لكل منتج جديد، والمحفوظة ببلاش. */
    public static function normalizerPer100(array $prices): float
    {
        $e = self::NORMALIZER_ESTIMATE;
        $price = $prices[$e['model']] ?? ['input' => 0.0, 'output' => 0.0];
        $call = round(($e['input_tokens'] * $price['input'] + $e['output_tokens'] * $price['output']) / 1e6, 6);
        return round(100 * $call, 3);
    }

    /** اسم حقل السعر لكل نموذج (المعرّف فيه ':' و'.'). */
    public static function modelSlug(string $id): string
    {
        return str_replace([':', '.'], ['__', '_'], $id);
    }

    public static function money(float $value): string
    {
        return '$' . number_format($value, $value > 0 && $value < 0.1 ? 3 : 2, '.', '');
    }

    /** رقم لحقل إدخال: '0.25'، '10'، بلا أصفار زايدة. */
    public static function plainNumber(float $value): string
    {
        $text = rtrim(rtrim(number_format($value, 4, '.', ''), '0'), '.');
        return $text === '' || $text === '-0' ? '0' : $text;
    }

    /** صرف الشهر الحالي (UTC) من verifier_spend، أو null إذا ما انقرا (جدول ناقص = لسا ما في صرف). */
    public static function verifierSpend(): ?array
    {
        $month = gmdate('Y-m');
        try {
            if (!\Illuminate\Support\Facades\Schema::hasTable('verifier_spend')) {
                return ['month' => $month, 'strong_usd' => 0.0, 'total_usd' => 0.0];
            }
            $rows = DB::table('verifier_spend')->where('month', $month)->get(['role', 'usd']);
        } catch (\Throwable $e) {
            return null;
        }
        $strong = 0.0;
        $total = 0.0;
        foreach ($rows as $row) {
            $usd = (float) $row->usd;
            $total += $usd;
            if ($row->role === 'strong') {
                $strong += $usd;
            }
        }
        return ['month' => $month, 'strong_usd' => round($strong, 4), 'total_usd' => round($total, 4)];
    }

    /**
     * حفظ «نماذج التحقق»: النموذج الأساسي والقوي (أو 'off') من القائمة المدعومة فقط، الميزانية الشهرية، والأسعار،
     * وقارئ أسماء الشيت المختصرة (gemini أو off).
     * اختيار غير مدعوم أو رقم غير صالح ما بيتغير (مع تحذير)، والباقي بينحفظ. الأساسي Gemini بيحدّث gemini_model كمان
     * حتى فحص بدء العامل وصفحة الصحة يشوفوا نفس النموذج.
     */
    private function saveModels(Request $request)
    {
        $warnings = [];
        $changes = [];
        $primary = strtolower(self::field($request, 'verifier_primary'));
        if (isset(self::VERIFIER_MODELS[$primary])) {
            $changes['verifier_primary'] = $primary;
            if (str_starts_with($primary, 'gemini:')) {
                $changes['gemini_model'] = substr($primary, strlen('gemini:'));
            }
        } else {
            $warnings[] = 'النموذج الأساسي اللي اخترته مش من القائمة المدعومة؛ ما تغيّر.';
        }
        $strong = strtolower(self::field($request, 'verifier_strong'));
        if ($strong === 'off' || isset(self::VERIFIER_MODELS[$strong])) {
            $changes['verifier_strong'] = $strong;
        } else {
            $warnings[] = 'النموذج القوي اللي اخترته مش من القائمة المدعومة؛ ما تغيّر.';
        }
        // قارئ أسماء الشيت: gemini أو off بس؛ نموذج قديم بلا هالحقل ما بيغيّره
        $normalizer = $request->input('query_normalizer');
        if ($normalizer !== null) {
            $normalizer = is_scalar($normalizer) ? strtolower(trim((string) $normalizer)) : '';
            if (in_array($normalizer, self::QUERY_NORMALIZER_MODES, true)) {
                $changes['query_normalizer'] = $normalizer;
            } else {
                $warnings[] = 'اختيار قراءة الأسماء المختصرة مش مفهوم؛ ما تغيّر.';
            }
        }
        $budget = self::field($request, 'verifier_monthly_budget_usd');
        if (preg_match('/^\d{1,4}(\.\d{1,2})?$/', $budget) && (float) $budget <= self::VERIFIER_BUDGET_MAX) {
            $changes['verifier_monthly_budget_usd'] = self::plainNumber((float) $budget);
        } else {
            $warnings[] = 'الميزانية لازم تكون رقم بين 0 و' . self::VERIFIER_BUDGET_MAX . ' دولار؛ ما تغيّرت الميزانية.';
        }
        $in = $request->input('price_input');
        $out = $request->input('price_output');
        if (is_array($in) || is_array($out)) {
            $prices = [];
            $valid = is_array($in) && is_array($out);
            foreach (self::VERIFIER_MODELS as $id => $m) {
                $slug = self::modelSlug($id);
                $pi = $valid && is_scalar($in[$slug] ?? null) ? trim((string) $in[$slug]) : null;
                $po = $valid && is_scalar($out[$slug] ?? null) ? trim((string) $out[$slug]) : null;
                if (!self::validPrice($pi) || !self::validPrice($po)) {
                    $valid = false;
                    break;
                }
                $prices[$id] = ['input' => round((float) $pi, 4), 'output' => round((float) $po, 4)];
            }
            if ($valid) {
                $changes['model_prices'] = json_encode($prices, JSON_UNESCAPED_SLASHES);
            } else {
                $warnings[] = 'الأسعار لازم تكون أرقام بين 0 و' . self::VERIFIER_PRICE_MAX . '؛ ما تغيّرت الأسعار.';
            }
        }

        try {
            self::write($changes);
        } catch (\Throwable $e) {
            return self::back('models', ['error' => 'ما انحفظت الإعدادات: قاعدة البيانات ما ردّت. جرّب كمان شوي.']);
        }
        $message = $changes === [] ? 'ما تغيّر شي.' : 'انحفظت نماذج التحقق، وبتنطبق من البحث الجاي.';
        return self::back('models', ['success' => $message, 'warnings' => $warnings]);
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
            $stored = DB::table('system_settings')
                ->whereIn('key', array_merge(self::SECRET_KEYS, self::EXTRA_SECRET_KEYS))->pluck('value')->all();
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
            'serper', 'gemini', 'photoroom', 'cloudinary', 'anthropic', 'serpapi' => $changes === []
                ? 'ما تغيّر شي: الحقول الفاضية بتخلي المفتاح المحفوظ متل ما هو.'
                : 'انحفظ. افحص الاتصالات من صفحة الصحة لتتأكد إنه شغّال.',
            'auto-publish' => ($changes['auto_publish_enabled'] ?? '') === 'true'
                ? 'النشر الآلي شغّال للماركات المفعّلة.'
                : 'النشر الآلي مطفأ: كل النتائج رح تستنى مراجعتك.',
            'processing' => 'انحفظت إعدادات معالجة الصور، وبتنطبق من التشغيل الجاي.',
            'sources' => ($changes['expansion_enabled'] ?? '') === 'true'
                ? 'انحفظ. الجولة الإضافية بتشتغل للمنتجات اللي ما انحسمت، من التشغيل الجاي.'
                : 'انحفظ. الجولة الإضافية مطفأة: البحث بيضل على صور جوجل بس.',
            'speed' => 'انحفظ. العدد الجديد بيشتغل من التشغيل الجاي.',
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
