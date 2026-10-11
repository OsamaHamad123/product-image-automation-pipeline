<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Auth;

/**
 * «النظام» (GET /system?tab=…, resources/views/dashboard/system.blade.php): الصحة والإعدادات والتجهيز بصفحة وحدة
 * بشريط جانبي واحد (روابط، صفحة كاملة لكل قسم). الأقسام بأربع مجموعات (GROUPS):
 * - «الحالة» (للكل): status (شو بدو منك، الخدمات، آخر تشغيل، دقة الاقتراحات)، search (عمليات البحث)، logs (السجل).
 * - «الإعدادات» (للمدير): تبويبات SettingsController::TABS نفسها.
 * - «أدوات» (للمدير): publish (فحص النشر وعزل الخلفية)، index (فهرس المتاجر)، eval (مجموعة اختبار)، recut (إعادة القص).
 * - «التجهيز» (للمدير): setup (معالج «جهّز لقطة»، و?step= بيضل شغّال).
 * المراجع بيشوف «الحالة» بس: الشريط ما بيعرض غيرها، وطلب قسم للمدير بيرجّع قسم الحالة (مش صفحة 403). الكتابة
 * (POST /settings، api/setup/*، ...) بتضل ممنوعة عليه بـ ReviewerLimits متل قبل.
 * العناوين القديمة (/system-diagnostics، /settings?tab=، /setup?step=) بتحوّل لهون (302) وأسماء مساراتها باقية.
 */
class SystemController extends Controller
{
    public const DEFAULT_TAB = 'status';

    /** المجموعات بترتيب الشريط: [label, admin, tabs: tab => [label, hint]]. */
    public const GROUPS = [
        'state' => ['label' => 'الحالة', 'admin' => false, 'tabs' => [
            'status' => ['label' => 'الحالة الآن', 'hint' => 'شو بدو منك، الخدمات، آخر تشغيل'],
            'search' => ['label' => 'عمليات البحث', 'hint' => 'النتائج والمصادر والتكلفة'],
            'logs' => ['label' => 'السجل', 'hint' => 'الأتمتة، لوحة التحكم، التشغيل الليلي'],
        ]],
        'settings' => ['label' => 'الإعدادات', 'admin' => true, 'tabs' => SettingsController::TABS],
        'tools' => ['label' => 'أدوات', 'admin' => true, 'tabs' => [
            'publish' => ['label' => 'فحص النشر', 'hint' => 'بروفة النشر وعزل الخلفية'],
            'index' => ['label' => 'فهرس المتاجر', 'hint' => 'صفحات المنتجات من المتاجر نفسها'],
            'eval' => ['label' => 'مجموعة اختبار', 'hint' => 'ملف من مراجعاتك للفريق'],
            'recut' => ['label' => 'إعادة القص', 'hint' => 'صور قديمة بخلفية بيضا'],
        ]],
        'setup' => ['label' => 'التجهيز', 'admin' => true, 'tabs' => [
            'setup' => ['label' => 'جهّز لقطة', 'hint' => 'خطوات أول مرة'],
        ]],
    ];

    /** أقسام صفحة الصحة القديمة (resources/views/dashboard/diagnostics.blade.php، public/js/health.js). */
    public const HEALTH_TABS = ['status', 'search', 'logs', 'publish', 'index', 'eval', 'recut'];

    /** وين صارت كل بطاقة (#id) من صفحة الصحة القديمة: روابط /system-diagnostics#id القديمة (health.js CARD_TABS). */
    public const CARD_TABS = [
        'services' => 'status', 'last-run' => 'status', 'lanes' => 'status',
        'search-ops' => 'search', 'logs' => 'logs',
        'publish-check' => 'publish', 'local-index' => 'index', 'eval-export' => 'eval', 'reprocess' => 'recut',
    ];

    /** زر بند «شو بدو منك» (data-health-goto) -> [القسم، البطاقة]: الرابط /system?tab=…&goto=…#بطاقة (health.js GOTO). */
    public const GOTO = [
        'services' => ['status', 'services'],
        'publish-check' => ['publish', 'publish-check'],
        'bg-restore' => ['publish', 'publish-check'],
        'index-card' => ['index', 'local-index'],
        'log-nightly' => ['logs', 'logs'],
    ];

    // ------------------------------------------------------------------
    // Tabs and links
    // ------------------------------------------------------------------

    /** /system?tab=…(&…)(#…): رابط نسبي لقسم. */
    public static function url(string $tab, array $query = [], string $hash = ''): string
    {
        return '/system?' . http_build_query(['tab' => $tab] + $query, '', '&', PHP_QUERY_RFC3986)
            . ($hash !== '' ? '#' . $hash : '');
    }

    /** رابط زر بند «شو بدو منك»، أو '' لزر بلا قسم (reload). */
    public static function gotoHref(string $goto): string
    {
        if (!isset(self::GOTO[$goto])) {
            return '';
        }
        [$tab, $card] = self::GOTO[$goto];
        return self::url($tab, ['goto' => $goto], $card);
    }

    /** المجموعات اللي بيشوفها هالدور (المراجع: «الحالة» بس). */
    public static function groups(bool $reviewer): array
    {
        return array_filter(self::GROUPS, fn ($group) => !$reviewer || !$group['admin']);
    }

    /** كل الأقسام: tab => group key. */
    public static function allTabs(): array
    {
        $tabs = [];
        foreach (self::GROUPS as $key => $group) {
            foreach (array_keys($group['tabs']) as $tab) {
                $tabs[$tab] = $key;
            }
        }
        return $tabs;
    }

    /** قسم للمدير بس؟ */
    public static function isAdminTab(string $tab): bool
    {
        $group = self::allTabs()[$tab] ?? null;
        return $group !== null && self::GROUPS[$group]['admin'];
    }

    /** القسم المعروض: المطلوب إذا موجود ومسموح لهالدور، وإلا الحالة. */
    public static function tabFor($value, bool $reviewer): string
    {
        if (!is_string($value) || !isset(self::allTabs()[$value])) {
            return self::DEFAULT_TAB;
        }
        return $reviewer && self::isAdminTab($value) ? self::DEFAULT_TAB : $value;
    }

    /** رابط داخلي بيوصل لصفحة للمدير بس (قسم بـ«الإعدادات» أو «أدوات» أو «التجهيز»، أو التشغيل)؟ */
    public static function adminHref(string $href): bool
    {
        $path = (string) parse_url($href, PHP_URL_PATH);
        // the run panel and the sheet tools on «اليوم» (/#run-new, /#run-brands …) are the owner's; the old page too
        $fragment = (string) parse_url($href, PHP_URL_FRAGMENT);
        if ($path === '/batch-automation' || (($path === '/' || $path === '') && str_starts_with($fragment, 'run-'))) {
            return true;
        }
        if ($path !== '/system') {
            return false;
        }
        parse_str((string) parse_url($href, PHP_URL_QUERY), $query);
        return is_string($query['tab'] ?? null) && self::isAdminTab($query['tab']);
    }

    private static function reviewer(): bool
    {
        return (bool) Auth::user()?->isReviewer();
    }

    // ------------------------------------------------------------------
    // Pages
    // ------------------------------------------------------------------

    /** GET /system?tab=…: الصفحة مع الشريط والقسم المطلوب بس (كل قسم بياخد بياناته من صاحبه). */
    public function show(Request $request)
    {
        $reviewer = self::reviewer();
        $tab = self::tabFor($request->query('tab'), $reviewer);
        $kind = in_array($tab, self::HEALTH_TABS, true) ? 'health'
            : (isset(SettingsController::TABS[$tab]) ? 'settings' : 'setup');

        $data = match ($kind) {
            'health' => HealthController::pageData($tab),
            'settings' => SettingsController::panelData($tab),
            'setup' => SetupController::pageData($request),
        };

        // «كمّل التجهيز (N من 5)» فوق الشريط لحد ما يخلص التجهيز (للمدير بس، رابط وبس)
        $setupLink = null;
        if (!$reviewer) {
            $card = SetupController::homeCard();     // نفس شرط بطاقة الرئيسية: null لتجهيز خالص أو تركيبة مستعملة
            $setupLink = $card === null ? null : ['ok' => (int) $card['ok'], 'total' => (int) $card['total']];
        }

        return view('dashboard.system', $data + [
            'systemTab' => $tab,
            'systemKind' => $kind,
            'systemGroups' => self::groups($reviewer),
            'setupLink' => $setupLink,
        ]);
    }

    /** GET /system-diagnostics (dashboard.diagnostics) -> /system. الـ #id بيضل مع المتصفح، وhealth.js بيوديه لقسمه. */
    public function diagnostics()
    {
        return redirect()->to(url('/system'));
    }

    /** GET /settings?tab= (dashboard.settings) -> /system?tab=… (بلا tab: ربط الشيت، متل قبل). */
    public function settings(Request $request)
    {
        return redirect()->to(url(self::url(SettingsController::tabFrom($request->query('tab')))));
    }

    /** GET /setup?step= (dashboard.setup) -> /system?tab=setup&step=…. */
    public function setup(Request $request)
    {
        $step = $request->query('step');
        return redirect()->to(url(self::url('setup', is_string($step) && $step !== '' ? ['step' => $step] : [])));
    }
}
