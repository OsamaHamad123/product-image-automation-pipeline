<?php

namespace App\Http\Controllers;

use Illuminate\Support\Facades\Cache;

/**
 * رأس صفحة الصحة: «كلشي تمام» أو قائمة قصيرة «شو بدو منك»، كل بند بزر لتصليحه (diagnostics.blade.php، health.js).
 *
 * المصادر نفسها اللي بتقرأها الصفحة وفحص /healthz، بلا أي مصدر جديد:
 * - انقطاع المزودين والصرف: تنبيهات ops_health (رصيد Serper، Gemini، ميزانية النموذج القوي ومفتاحه)، من الكاش بس
 *   (HealthController::CACHE_KEY): فتح الصفحة ما بيشغّل بايثون؛ health.js بيقرأ ops-health وبعدين بيسأل هون من جديد.
 * - آخر فحص للاتصالات (temp/diagnostics_last.json): خدمة أساسية ما ردّت.
 * - كتابات الشيت الميتة (DEAD) آخر 7 أيام، المساحة الفاضية، وعمر آخر ليلة ناجحة: HealthzController::collect.
 * - آخر تشغيل (temp/nightly/last_report.json): عطل، انقطاع، أو وقف عند الميزانية اليومية.
 * - آخر «فحص النشر» وعزل الخلفية المتوقف، وفهرس المتاجر المحلي (آخر جمع ناجح أقدم من أسبوعين).
 * كل بند: {key, tone, title, text, action: {label, href} أو {label, goto}}. goto بيفتح «تفاصيل متقدمة» على البطاقة
 * اللي فيها التصليح (publish-check، bg-restore، services، index-card، log-nightly) وبيحط التركيز على زرها.
 */
class HealthAttentionController extends Controller
{
    /** عمر آخر جمع ناجح للفهرس المحلي (أيام) قبل ما يصير بند: التحديث التلقائي أسبوعي. */
    public const INDEX_STALE_DAYS = 14;

    /** الخدمات الأساسية بفحص الاتصالات (HealthController::SERVICES). */
    public const CRITICAL = ['google_sheets' => 'Google Sheet', 'serper' => 'Serper', 'gemini' => 'Gemini',
                             'photoroom' => 'PhotoRoom', 'cloudinary' => 'Cloudinary'];

    /** تنبيهات ops_health -> [اللون، العنوان، النص، التصليح]. الرموز ما بتبين للمالك. */
    public const OPS_ALERTS = [
        'SERPER_CREDIT' => ['danger', 'رصيد Serper خلص أو المفتاح مرفوض',
            'البحث عن الصور واقف لحد ما تشحن رصيد Serper أو تصلّح المفتاح.', ['label' => 'المفاتيح', 'href' => '/settings?tab=keys']],
        'GEMINI_DOWN' => ['warning', 'Gemini ما عم يردّ',
            'كل النتائج رايحة لمراجعتك بدون قراءة الملصق. تأكد من مفتاح Gemini.', ['label' => 'المفاتيح', 'href' => '/settings?tab=keys']],
        'VERIFIER_BUDGET' => ['warning', 'ميزانية النموذج القوي لهالشهر خلصت',
            'المنتجات المش مؤكدة بتستنى مراجعتك بدون نظرة تانية. ارفع الميزانية أو استنى الشهر الجاي.',
            ['label' => 'نماذج التحقق', 'href' => '/settings?tab=models']],
        'VERIFIER_KEY' => ['warning', 'مفتاح نموذج التحقق الإضافي مرفوض أو مش محفوظ',
            'النموذج القوي ما عم يقرأ. ضيف مفتاحه أو وقّفه من «نماذج التحقق».', ['label' => 'المفاتيح', 'href' => '/settings?tab=keys']],
    ];

    /** GET /api/system/attention (?after=1: الصفحة سألت ops-health قبلها، فكاش ناقص يعني ما انقرا). */
    public function show(\Illuminate\Http\Request $request)
    {
        return response()->json(['status' => 'success'] + self::view(self::facts(), null, $request->boolean('after')), 200, [],
            JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE)->header('Cache-Control', 'no-store');
    }

    /** للصفحة (HealthController::page): نفس البيانات بلا ops-health إذا مش بالكاش (البطاقة بتقول «عم نتأكد…»). */
    public static function current(): array
    {
        try {
            return self::view(self::facts());
        } catch (\Throwable $e) {
            // هالرأس ما بيكون أبداً سبب إنو صفحة الصحة ما تفتح
            return self::view(['healthz' => ['db' => true]]);
        }
    }

    /** الحقائق من الملفات والقاعدة والكاش (بلا بايثون وبلا أي طلب مدفوع). */
    public static function facts(): array
    {
        $ops = null;
        try {
            $cached = Cache::get(HealthController::CACHE_KEY);
            $ops = is_array($cached) && ($cached['status'] ?? '') === 'success' ? $cached : null;
        } catch (\Throwable $e) {
            // ما في كاش: البحث بينقرا من health.js
        }
        return [
            'healthz' => HealthzController::collect(),
            'ops' => $ops,
            'diagnostics' => ProductController::lastDiagnostics(),
            'publish' => HealthController::publicPublishCheck(HealthController::lastPublishCheck(), SettingsController::secretValues()),
            'bg' => SettingsController::currentBgState(),
            'last_run' => HealthController::lastRunRow(),
            'index' => LocalIndexController::card(),
            'index_rows' => LocalIndexController::dbRows(),
        ];
    }

    /**
     * {state: ok | attention | checking, title, text, items: [...], note, pending}. pending: ops-health ما انقرا لسا
     * (الصفحة بتسأل من جديد بعده). $after: الصفحة سألت، فكاش ناقص يعني ما قدرنا نقرأ سجل البحث.
     */
    public static function view(array $facts, ?int $now = null, bool $after = false): array
    {
        $now = $now ?? time();
        $items = [];
        $healthz = (array) ($facts['healthz'] ?? []);
        $db = ($healthz['db'] ?? false) === true;
        if (!$db) {
            $items[] = self::item('db', 'danger', 'قاعدة البيانات ما بتردّ',
                'المراجعة والتشغيل واقفين لحد ما ترجع. شغّلها (أو أعد تشغيل الجهاز)، وإذا ضلت واقفة بلّغ المطوّر.',
                ['label' => 'حدّث الصفحة', 'goto' => 'reload']);
        }

        foreach ((array) (($facts['ops'] ?? [])['alerts'] ?? []) as $alert) {
            $code = is_array($alert) ? (string) ($alert['code'] ?? '') : '';
            if (isset(self::OPS_ALERTS[$code])) {
                [$tone, $title, $text, $action] = self::OPS_ALERTS[$code];
                $items[] = self::item('ops_' . strtolower($code), $tone, $title, $text, $action);
            }
        }

        $down = self::servicesDown($facts['diagnostics'] ?? null);
        if ($down !== []) {
            $at = strtotime((string) (($facts['diagnostics'] ?? [])['checked_at'] ?? '')) ?: null;
            $items[] = self::item('services', 'danger',
                self::names($down) . (count($down) === 1 ? ' ما ردّ' : ' ما ردّوا') . ' بآخر فحص للاتصالات',
                'تأكد من المفاتيح ومن الإنترنت، وبعدين افحص من جديد.' . ($at ? ' آخر فحص: ' . HealthController::stamp($at) . '.' : ''),
                ['label' => 'افحص الاتصالات الآن', 'goto' => 'services']);
        }

        $dead = $healthz['dead_recent'] ?? null;
        if (is_int($dead) && $dead > 0) {
            $items[] = self::item('outbox', 'danger',
                $dead === 1 ? 'رابط صورة واحد ما وصل للشيت'
                    : ($dead === 2 ? 'رابطين' : $dead . ($dead <= 10 ? ' روابط' : ' رابط')) . ' ما وصلوا للشيت',
                'الكتابة بالشيت ما زبطت بعد كل المحاولات بآخر ' . HealthzController::DEAD_WINDOW_DAYS . ' أيام. '
                . 'تشغيل جديد بيرجع يكتب روابط الصور المعتمدة بدون ما يدوّر من جديد.',
                ['label' => 'صفحة التشغيل', 'href' => '/batch-automation']);
        }

        // كتابات رفضها الشيت لأنه الصف أو العمود تغيّر بعد ما انجدولت (صفوف انزاحت، تبويب انرجع من نسخة، عمود انمسح):
        // ما بتنكتب لحالها فوق شي ما بنعرفه، فكانت تضل ساكتة. روابط الصور المعتمدة بيرجع يكتبها التشغيل الجاي
        $conflicts = $healthz['conflict_recent'] ?? null;
        if (is_int($conflicts) && $conflicts > 0) {
            $items[] = self::item('outbox-conflict', 'warning',
                ($conflicts === 1 ? 'كتابة وحدة' : $conflicts . ' كتابات') . ' بالشيت ما انكتبت لأنه الشيت تغيّر',
                'الصف أو العمود ما عاد متل ما كان وقت الاعتماد (صفوف انزاحت، تبويب انرجع من نسخة، أو عمود انمسح)، '
                . 'فما كتبنا فوق شي ما منعرفه. التشغيل الجاي بيرجع يكتب روابط الصور المعتمدة لحاله؛ الأوصاف والتصنيفات '
                . 'بتنكتب لما تنعتمد الصورة من جديد.',
                ['label' => 'صفحة التشغيل', 'href' => '/batch-automation']);
        }

        $free = $healthz['free_gb'] ?? null;
        if (is_numeric($free) && (float) $free < HealthzController::MIN_FREE_GB) {
            $items[] = self::item('disk', 'danger', 'المساحة الفاضية عالجهاز قليلة (' . self::gb((float) $free) . ')',
                'لازم ' . self::gb(HealthzController::MIN_FREE_GB) . ' عالأقل ليضل التشغيل والصور شغّالين. التشغيل الليلي بينظّف '
                . 'الصور القديمة لحاله؛ إذا ضلت قليلة فضّي مساحة عالجهاز أو كبّر القرص.',
                ['label' => 'سجل التشغيل الليلي', 'goto' => 'log-nightly']);
        }

        $run = $facts['last_run'] ?? null;
        $outcome = is_array($run) ? (string) ($run['outcome'] ?? '') : '';
        if (in_array($outcome, ['failed', 'outage'], true)) {
            $card = HealthController::lastRunCard($run);
            $items[] = self::item('last_run', 'danger', 'آخر تشغيل ما خلص منيح',
                $card['title'] . ($card['when'] !== '' ? ' (' . $card['when'] . ')' : '') . '. شوف شو صار وشغّل من جديد.',
                ['label' => 'صفحة التشغيل', 'href' => '/batch-automation']);
        } elseif ($outcome === 'stopped' && ($run['stop_reason'] ?? '') === 'budget_reached') {
            $items[] = self::item('spend', 'warning', 'آخر تشغيل وقف لأنو صرف اليوم وصل للميزانية اليومية',
                'المنتجات الباقية بتستنى بالطابور وبتكمّل بتشغيل بكرا لحالها.',
                ['label' => 'صفحة التشغيل', 'href' => '/batch-automation']);
        }

        if (($healthz['nightly_known'] ?? false) && is_int($healthz['nightly_age_s'] ?? null)
            && $healthz['nightly_age_s'] > HealthzController::NIGHTLY_MAX_HOURS * 3600) {
            $hours = (int) round($healthz['nightly_age_s'] / 3600);
            $items[] = self::item('nightly', 'warning', 'التشغيل الليلي ما اشتغل من ' . self::hours($hours),
                'آخر ليلة خلصت منيح كانت قبل ' . self::hours($hours) . '. شوف سجل التشغيل الليلي ليش، أو شغّل من صفحة التشغيل.',
                ['label' => 'سجل التشغيل الليلي', 'goto' => 'log-nightly']);
        }

        $publish = $facts['publish'] ?? null;          // HealthController::publicPublishCheck (محجوب)
        if (is_array($publish) && ($publish['overall'] ?? '') === 'fail') {
            $items[] = self::item('publish', 'danger', 'آخر فحص للنشر ما زبط',
                (string) ($publish['summary_ar'] ?? '') !== '' ? (string) $publish['summary_ar']
                    : 'في خطوة بالنشر ما زبطت: شوف شو لازم تعمل.',
                ['label' => 'افحص النشر', 'goto' => 'publish-check']);
        }

        $bg = $facts['bg'] ?? null;
        if (is_array($bg) && ($bg['method'] ?? '') === 'none') {
            $items[] = self::item('bg_off', 'warning', 'عزل الخلفية متوقف',
                'الصور اللي بتعتمدها بتنتشر متل ما هي على لوحة بيضا، بدون عزل خلفيتها.',
                ['label' => 'رجّع عزل الخلفية', 'goto' => 'bg-restore']);
        }

        $stale = self::indexStaleDays($facts['index'] ?? null, $facts['index_rows'] ?? null);
        if ($stale !== null) {
            $items[] = self::item('index', 'info', 'فهرس المتاجر صار قديم',
                'آخر جمع ناجح من ' . $stale . ' يوم: حدّثه ليلاقي صفحات المنتجات الجديدة بمجاني قبل أي بحث مدفوع.',
                ['label' => 'حدّث الفهرس هلق', 'goto' => 'index-card']);
        }

        $pending = $db && !is_array($facts['ops'] ?? null) && !$after;
        $note = $db && !is_array($facts['ops'] ?? null) && $after
            ? 'ما قدرنا نقرأ سجل البحث هلق، فممكن في شي عن مصادر البحث ما بيبين هون.' : '';
        if ($items !== []) {
            $count = count($items);
            return ['state' => 'attention', 'title' => 'شو بدو منك',
                    'text' => ($count === 1 ? 'في شغلة وحدة' : ($count === 2 ? 'في شغلتين' : 'في ' . $count . ($count <= 10 ? ' شغلات' : ' شغلة')))
                        . ' لازم تنتبهلها. كل وحدة إلها زر لتصليحها.',
                    'items' => $items, 'note' => $pending ? 'عم نتأكد من سجل البحث كمان…' : $note, 'pending' => $pending];
        }
        if ($pending) {
            return ['state' => 'checking', 'title' => 'عم نتأكد…', 'text' => 'لحظة، عم نقرأ سجل البحث.', 'items' => [],
                    'note' => '', 'pending' => true];
        }
        return ['state' => 'ok', 'title' => 'كلشي تمام', 'text' => 'ما في شي بدو منك هلق. التفاصيل تحت إذا حابب تشوف.',
                'items' => [], 'note' => $note, 'pending' => false];
    }

    private static function item(string $key, string $tone, string $title, string $text, array $action): array
    {
        return ['key' => $key, 'tone' => $tone, 'title' => $title, 'text' => $text,
                'action' => ['label' => (string) $action['label'], 'href' => (string) ($action['href'] ?? ''),
                             'goto' => (string) ($action['goto'] ?? '')]];
    }

    /** الخدمات الأساسية اللي ما ردّت بآخر فحص (أسماؤها). */
    public static function servicesDown(?array $diagnostics): array
    {
        $down = [];
        foreach (self::CRITICAL as $key => $name) {
            $status = (string) ((($diagnostics ?? [])['services'][$key] ?? [])['status'] ?? '');
            if ($status !== '' && $status !== 'online' && $status !== 'disabled') {
                $down[] = $name;
            }
        }
        return $down;
    }

    /** «Gemini»، «Gemini و Cloudinary»، «Serper و Gemini و Cloudinary». */
    public static function names(array $names): string
    {
        return implode(' و', $names);
    }

    /** «1.4 GB». */
    public static function gb(float $value): string
    {
        return rtrim(rtrim(number_format($value, 1, '.', ''), '0'), '.') . ' GB';
    }

    /** «ساعة»، «ساعتين»، «5 ساعات»، «30 ساعة». */
    public static function hours(int $n): string
    {
        if ($n <= 1) {
            return 'ساعة';
        }
        if ($n === 2) {
            return 'ساعتين';
        }
        return $n . ($n <= 10 ? ' ساعات' : ' ساعة');
    }

    /**
     * أيام من آخر جمع ناجح لأحدث متجر مفعّل، إذا صار أقدم من INDEX_STALE_DAYS، وإلا null (ولا متجر انجمع أبداً: التشغيل
     * بيعمله لحاله، مش بند). $card: LocalIndexController::card()، $rows: dbRows().
     */
    public static function indexStaleDays(?array $card, ?array $rows): ?int
    {
        if (!is_array($card) || !is_array($rows) || !($card['db'] ?? false) || !empty($card['refresh']['running'])) {
            return null;
        }
        $freshest = null;
        foreach ((array) ($card['rows'] ?? []) as $row) {
            if (($row['state'] ?? '') === 'off') {
                continue;
            }
            $age = $rows[$row['key'] ?? '']['ok_age_s'] ?? null;
            if (is_int($age)) {
                $freshest = $freshest === null ? $age : min($freshest, $age);
            }
        }
        if ($freshest === null || $freshest <= self::INDEX_STALE_DAYS * 86400) {
            return null;
        }
        return intdiv($freshest, 86400);
    }
}
