<?php

namespace App\Http\Controllers;

use App\Services\PythonBridge;
use Illuminate\Support\Facades\DB;

/**
 * بطاقة «فهرس المتاجر المحلي» بصفحة الصحة (public/js/health.js createLocalIndex).
 *
 * - لكل متجر بكاتالوج الفهرس (catalog_match/data/catalog_stores.json): كم صفحة منتج فيه، من متى آخر جمع ناجح لخرايطه،
 *   وحالته: تمام / ممنوع (المتجر رد 401 أو 403 أو 429 أو فحص بوت فبيتخطى 7 أيام) / ما انجمع أبداً (وجزئي أو فشل).
 *   الأرقام من جدولي catalog_products و catalog_harvests اللي بيكتبهم التحديث نفسه (catalog_match/index_refresh.py).
 * - «حدّث الفهرس هلق» (POST /api/system/local-index/refresh، بحماية CSRF متل باقي الـ POST) بيشغّل نفس التحديث اللي بيبدأه
 *   التشغيل الليلي والعامل لحالهم، كمهمة خلفية عبر جسر بايثون (cli_bridge local_index_refresh): بيرجع فوراً والتقدم
 *   بيظهر عند إعادة تحميل الصفحة (temp/local_index_refresh.json) أو بقراءة GET /api/system/local-index.
 * - كم منتج جاوب عليه الفهرس بآخر تشغيل: من temp/nightly/last_report.json (run_report: local_index).
 * فتح الصفحة ما بيقرا أي موقع ولا بيصرف بحث مدفوع: التحديث بزر صريح فقط (أو لحاله بالخلفية أول التشغيل).
 */
class LocalIndexController extends Controller
{
    public const STORES_FILE = '../catalog_match/data/catalog_stores.json';
    public const STATE_FILE = '../temp/local_index_refresh.json';
    public const LOCK_FILE = '../temp/local_index_refresh.lock';
    public const LAST_REPORT_FILE = '../temp/nightly/last_report.json';
    /** نوافذ Visit-time من robots.txt لكل متجر (index_refresh.remember_visit_windows): {store: {visit_time: [[بداية، نهاية]]}}. */
    public const VISIT_FILE = '../temp/local_index_robots.json';
    /** المتجر اللي robots.txt عنده بيحدد وقت زيارة بالتوقيت العالمي (UTC)؛ بنعرضه بتوقيت الإمارات. */
    public const VISIT_ZONE = 'Asia/Dubai';

    /** نفس catalog_match/index_refresh: BLOCKED_SKIP_S، LIVE_MARGIN_S، STARTING_GRACE_S وميزانية التحديث الافتراضية. */
    public const BLOCKED_SKIP_DAYS = 7;
    public const LIVE_MARGIN_SECONDS = 180;
    public const STARTING_GRACE_SECONDS = 90;
    public const DEFAULT_BUDGET_SECONDS = 300;

    /** حالة المتجر -> [النص، اللون]. */
    public const STATES = [
        'ok' => ['تمام', 'success'],
        'partial' => ['جزئي (بنكمّل بالتحديث الجاي)', 'warning'],
        'never' => ['ما انجمع أبداً', 'warning'],
        'blocked' => ['ممنوع', 'danger'],
        'error' => ['فشل آخر جمع', 'danger'],
        'off' => ['موقوف بالإعدادات', 'muted'],
    ];

    /** كيف انتهى آخر تحديث (index_refresh state.ended). */
    public const ENDED = [
        'done' => 'خلص',
        'budget' => 'وقف عند حد الوقت والباقي بالمرة الجاية',
        'nothing_due' => 'كل المتاجر محدّثة، ما في شي ينقرا',
        'error' => 'وقف بخطأ',
        'unavailable' => 'ما قدر يقرا الفهرس (قاعدة البيانات؟)',
    ];

    // ------------------------------------------------------------------
    // Inputs
    // ------------------------------------------------------------------

    /** متاجر الكاتالوج بترتيب الملف: [{key, name, enabled}] (فاضي إذا الملف ما انقرأ). */
    public static function stores(?string $file = null): array
    {
        $file = $file ?? base_path(self::STORES_FILE);
        $raw = is_file($file) ? (string) @file_get_contents($file) : '';
        $data = json_decode(preg_replace('/^\xEF\xBB\xBF/', '', $raw), true);
        $out = [];
        foreach (is_array($data['stores'] ?? null) ? $data['stores'] : [] as $store) {
            if (is_array($store) && trim((string) ($store['key'] ?? '')) !== '') {
                $out[] = ['key' => (string) $store['key'], 'name' => (string) ($store['name'] ?? $store['key']),
                          'enabled' => (bool) ($store['enabled'] ?? true)];
            }
        }
        return $out;
    }

    /**
     * صفحات كل متجر وسجل جمعه من قاعدة البيانات: [store => {pages, last_status, last_age_s, ok_age_s}]؛ ok_age_s = من آخر
     * جمع قرا شي (ok أو partial أو empty). null إذا القاعدة أو الجداول مش متاحة (البطاقة بتقول هيك).
     */
    public static function dbRows(): ?array
    {
        try {
            $pages = DB::table('catalog_products')->select('store', DB::raw('COUNT(*) AS n'))->groupBy('store')
                ->pluck('n', 'store')->all();
            $last = DB::select('SELECT h.store, h.status, TIMESTAMPDIFF(SECOND, h.finished_at, NOW()) AS age_s '
                . 'FROM catalog_harvests h JOIN (SELECT store, MAX(id) AS id FROM catalog_harvests GROUP BY store) m '
                . 'ON m.id = h.id');
            $read = DB::select("SELECT store, TIMESTAMPDIFF(SECOND, MAX(finished_at), NOW()) AS age_s FROM catalog_harvests "
                . "WHERE status IN ('ok', 'partial', 'empty') AND finished_at IS NOT NULL GROUP BY store");
        } catch (\Throwable $e) {
            return null;
        }
        $out = [];
        $row = function (string $store) use (&$out) {
            return $out[$store] ??= ['pages' => 0, 'last_status' => '', 'last_age_s' => null, 'ok_age_s' => null];
        };
        foreach ($pages as $store => $n) {
            $row((string) $store);
            $out[(string) $store]['pages'] = (int) $n;
        }
        foreach ($last as $h) {
            $row((string) $h->store);
            $out[(string) $h->store]['last_status'] = (string) $h->status;
            $out[(string) $h->store]['last_age_s'] = $h->age_s === null ? null : max(0, (int) $h->age_s);
        }
        foreach ($read as $h) {
            $row((string) $h->store);
            $out[(string) $h->store]['ok_age_s'] = $h->age_s === null ? null : max(0, (int) $h->age_s);
        }
        return $out;
    }

    /** نوافذ الزيارة المسموحة لكل متجر: [store => [[بداية، نهاية] بدقائق UTC من منتصف الليل]] (فاضي إذا الملف ما انقرأ). */
    public static function visitWindows(?string $file = null): array
    {
        $file = $file ?? base_path(self::VISIT_FILE);
        $data = is_file($file) ? json_decode((string) @file_get_contents($file), true) : null;
        $out = [];
        foreach (is_array($data) ? $data : [] as $store => $entry) {
            $windows = [];
            foreach (is_array($entry['visit_time'] ?? null) ? $entry['visit_time'] : [] as $w) {
                if (is_array($w) && count($w) === 2 && is_numeric($w[0]) && is_numeric($w[1])
                    && (int) $w[0] >= 0 && (int) $w[0] <= 1440 && (int) $w[1] >= 0 && (int) $w[1] <= 1440 && $w[0] != $w[1]) {
                    $windows[] = [(int) $w[0], (int) $w[1]];
                }
            }
            if ($windows) {
                $out[(string) $store] = $windows;
            }
        }
        return $out;
    }

    /**
     * «بيسمح بالقراءة بين 08:00 و12:45 بتوقيت الإمارات» من نافذة Visit-time بتوقيت UTC (04:00-08:45)؛ فاضي بدون نافذة.
     * نافذة بتعدّي منتصف الليل بتنكتب متل ما هي بعد التحويل («بين 22:00 و02:00»).
     */
    public static function visitText(array $windows): string
    {
        $parts = [];
        $utc = new \DateTimeZone('UTC');
        $zone = new \DateTimeZone(self::VISIT_ZONE);
        $clock = function (int $minutes) use ($utc, $zone): string {
            return (new \DateTimeImmutable('2026-01-01 00:00', $utc))->modify('+' . $minutes . ' minutes')
                ->setTimezone($zone)->format('H:i');
        };
        foreach ($windows as $w) {
            if (((int) $w[1] - (int) $w[0]) % 1440 !== 0) {      // a full-day window (0000-2400) restricts nothing
                $parts[] = $clock((int) $w[0]) . ' و' . $clock((int) $w[1]);
            }
        }
        return $parts ? 'بيسمح بالقراءة بين ' . implode(' وبين ', $parts) . ' بتوقيت الإمارات' : '';
    }

    /**
     * تقدم التحديث: {running, state: ملف temp/local_index_refresh.json}. شغّال إذا القفل حي (ملف القفل انلمس خلال
     * ميزانيته + هامش، index_refresh.lock_is_live) أو إذا المهمة انطلبت من الزر وللحين ما أخدت القفل (state=starting).
     */
    public static function refreshState(?string $stateFile = null, ?string $lockFile = null, ?int $now = null): array
    {
        $now = $now ?? time();
        $stateFile = $stateFile ?? base_path(self::STATE_FILE);
        $lockFile = $lockFile ?? base_path(self::LOCK_FILE);
        $state = is_file($stateFile) ? json_decode((string) @file_get_contents($stateFile), true) : null;
        $state = is_array($state) ? $state : [];
        $running = false;
        $touched = is_file($lockFile) ? @filemtime($lockFile) : false;
        if ($touched !== false) {
            $lock = json_decode((string) @file_get_contents($lockFile), true);
            $budget = is_array($lock) && is_numeric($lock['budget_s'] ?? null) ? (float) $lock['budget_s']
                : self::DEFAULT_BUDGET_SECONDS;
            $running = $now - $touched < $budget + self::LIVE_MARGIN_SECONDS;
        }
        if (!$running && ($state['state'] ?? '') === 'starting') {
            $running = $now - (int) ($state['updated_at'] ?? 0) < self::STARTING_GRACE_SECONDS;
        }
        return ['running' => $running, 'state' => $state];
    }

    // ------------------------------------------------------------------
    // Words
    // ------------------------------------------------------------------

    /** «من 3 أيام»، «من ساعة»، «من يومين»، «هلق» (نفس صيغ ageText بـ health.js). */
    public static function ageText(?int $seconds): string
    {
        if ($seconds === null) {
            return '';
        }
        $seconds = max(0, $seconds);
        if ($seconds < 60) {
            return 'هلق';
        }
        foreach ([[3600, 'دقيقة', 'دقيقتين', 'دقايق', 60], [86400, 'ساعة', 'ساعتين', 'ساعات', 3600]] as [$limit, $one, $two, $few, $unit]) {
            if ($seconds < $limit) {
                return 'من ' . self::count((int) round($seconds / $unit), $one, $two, $few);
            }
        }
        return 'من ' . self::count((int) round($seconds / 86400), 'يوم', 'يومين', 'أيام');
    }

    /** «ساعة»، «ساعتين»، «5 ساعات»، «12 ساعة». */
    private static function count(int $n, string $one, string $two, string $few): string
    {
        if ($n <= 1) {
            return $one;
        }
        if ($n === 2) {
            return $two;
        }
        return $n . ' ' . ($n <= 10 ? $few : $one);
    }

    /** «صفحة منتج واحدة»، «صفحتين»، «5 صفحات»، «1200 صفحة». */
    public static function pagesText(int $n): string
    {
        if ($n === 0) {
            return 'ما في صفحات';
        }
        if ($n === 1) {
            return 'صفحة منتج وحدة';
        }
        if ($n === 2) {
            return 'صفحتين';
        }
        return number_format($n, 0, '.', '') . ($n <= 10 ? ' صفحات' : ' صفحة');
    }

    // ------------------------------------------------------------------
    // The card
    // ------------------------------------------------------------------

    /** حالة متجر واحد من سجل جمعه ($h: صف dbRows أو null). */
    public static function storeState(bool $enabled, ?array $h): string
    {
        if (!$enabled) {
            return 'off';
        }
        $status = (string) ($h['last_status'] ?? '');
        if ($status === '') {
            return 'never';
        }
        if ($status === 'blocked') {
            return 'blocked';
        }
        return $status === 'ok' || $status === 'empty' ? 'ok' : ($status === 'partial' ? 'partial' : 'error');
    }

    /** سطر التقدم: {running, text}. */
    public static function progressView(array $refresh, array $stores): array
    {
        $state = $refresh['state'];
        $names = [];
        foreach ($stores as $store) {
            $names[$store['key']] = $store['name'];
        }
        if ($refresh['running']) {
            $current = (string) ($state['current'] ?? '');
            $total = is_array($state['plan'] ?? null) ? count($state['plan']) : 0;
            $done = is_array($state['results'] ?? null) ? count($state['results']) : 0;
            if (($state['state'] ?? '') === 'starting' || $total === 0) {
                return ['running' => true, 'text' => 'عم يبلّش تحديث الفهرس…'];
            }
            $where = $current !== '' ? ': ' . ($names[$current] ?? $current) . ' (' . min($total, $done + 1) . ' من ' . $total . ')' : '';
            return ['running' => true, 'text' => 'عم نحدّث الفهرس بالخلفية' . $where];
        }
        $finished = is_numeric($state['finished_at'] ?? null) ? (int) $state['finished_at'] : null;
        $ended = (string) ($state['ended'] ?? '');
        if ($finished === null || $ended === '') {
            return ['running' => false, 'text' => ''];
        }
        $results = is_array($state['results'] ?? null) ? $state['results'] : [];
        $waiting = count(array_filter($results, fn ($r) => ($r['status'] ?? '') === 'outside_visit_time'));
        $read = count($results) - $waiting;
        $text = 'آخر تحديث ' . self::ageText(max(0, time() - $finished)) . ': ' . (self::ENDED[$ended] ?? 'انتهى');
        $said = [];
        if ($read > 0) {
            $said[] = 'قرينا ' . ($read === 1 ? 'متجر واحد' : ($read === 2 ? 'متجرين' : $read . ($read <= 10 ? ' متاجر' : ' متجر')));
        }
        if ($waiting > 0) {
            $said[] = ($waiting === 1 ? 'متجر واحد' : ($waiting === 2 ? 'متجرين' : $waiting . ($waiting <= 10 ? ' متاجر' : ' متجر')))
                . ' برا وقت الزيارة المسموح (بنعيد المحاولة بالتحديث الجاي)';
        }
        return ['running' => false, 'text' => $text . ($said ? ' (' . implode('، ', $said) . ')' : '')];
    }

    /** «الفهرس جاوب على 14 من 58 منتج بآخر تشغيل»، أو null إذا التقرير ما سجّل هالرقم (تشغيل قبل هالتحديث). */
    public static function lastRunText(?array $report): ?string
    {
        $li = is_array($report['local_index'] ?? null) ? $report['local_index'] : null;
        if ($li === null || !is_numeric($li['answered'] ?? null)) {
            return null;
        }
        $answered = (int) $li['answered'];
        $searched = is_numeric($report['counts']['searched'] ?? null) ? (int) $report['counts']['searched'] : (int) ($li['asked'] ?? 0);
        if ($answered === 0) {
            return 'الفهرس ما جاوب على أي منتج بآخر تشغيل.';
        }
        return 'الفهرس جاوب على ' . $answered . ($searched >= $answered && $searched > 0 ? ' من ' . $searched : '')
            . ' منتج بآخر تشغيل (مجاناً، قبل أي بحث مدفوع).';
    }

    /**
     * بيانات البطاقة: {rows[{key, name, state, state_text, tone, pages, pages_text, when, note}], total, total_text,
     * db (false = القاعدة مش متاحة), refresh {running, text}, last_run}. دالة صرفة: كل المدخلات برا.
     */
    public static function view(array $stores, ?array $db, array $refresh, ?array $lastReport, ?int $now = null,
                                array $visit = []): array
    {
        $now = $now ?? time();
        $waiting = [];
        foreach (is_array($refresh['state']['results'] ?? null) ? $refresh['state']['results'] : [] as $r) {
            if (($r['status'] ?? '') === 'outside_visit_time') {
                $waiting[(string) ($r['store'] ?? '')] = true;
            }
        }
        $rows = [];
        $total = 0;
        foreach ($stores as $store) {
            $h = $db[$store['key']] ?? null;
            $state = self::storeState($store['enabled'], $h);
            [$stateText, $tone] = self::STATES[$state];
            $pages = (int) ($h['pages'] ?? 0);
            $total += $pages;
            $note = '';
            if ($state === 'blocked') {
                $left = self::BLOCKED_SKIP_DAYS * 86400 - (int) ($h['last_age_s'] ?? 0);
                $days = (int) ceil($left / 86400);
                $note = $left > 0 ? 'المتجر رفض القراءة: ما بنسأله قبل ' . ($days <= 1 ? 'يوم' : ($days === 2 ? 'يومين' : $days . ' أيام')) . '.'
                    : 'المتجر رفض القراءة قبل أسبوع: بنجرّب مرة تانية بالتحديث الجاي.';
            } elseif ($state === 'never') {
                $note = 'بينقرا بأول تحديث.';
            } elseif ($state === 'error') {
                $note = 'بنعيد المحاولة بالتحديث الجاي.';
            }
            if (!empty($waiting[$store['key']]) && !$refresh['running']) {
                $note = trim($note . ' ما انقرا بآخر تحديث لأنو الوقت برا المسموح.');
            }
            $rows[] = ['key' => $store['key'], 'name' => $store['name'], 'state' => $state, 'state_text' => $stateText,
                       'tone' => $tone, 'pages' => $pages, 'pages_text' => $db === null ? '—' : self::pagesText($pages),
                       'when' => self::ageText($h['ok_age_s'] ?? null), 'note' => $note,
                       'visit' => self::visitText($visit[$store['key']] ?? [])];
        }
        return [
            'rows' => $rows,
            'total' => $total,
            'total_text' => $db === null ? '' : 'بالفهرس ' . self::pagesText($total),
            'db' => $db !== null,
            'refresh' => self::progressView($refresh, $stores),
            'last_run' => self::lastRunText($lastReport),
        ];
    }

    /** البطاقة من الملفات والقاعدة الحقيقية (HealthController::page و GET /api/system/local-index). */
    public static function card(): array
    {
        try {
            $report = HealthController::lastRunRow();
            $report = is_array($report['report_json'] ?? null) ? $report['report_json'] : null;
            return self::view(self::stores(), self::dbRows(), self::refreshState(), $report, null, self::visitWindows());
        } catch (\Throwable $e) {
            // this card is never a reason for the Health page not to open
            return self::view([], null, ['running' => false, 'state' => []], null);
        }
    }

    // ------------------------------------------------------------------
    // Endpoints
    // ------------------------------------------------------------------

    /** GET /api/system/local-index: البطاقة كـ JSON (الصفحة بتسأل عنه كل بضع ثواني بس لما التحديث شغّال). */
    public function status()
    {
        return response()->json(['status' => 'success', 'card' => self::card()], 200, [],
            JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE)->header('Cache-Control', 'no-store');
    }

    /**
     * POST /api/system/local-index/refresh: يبدأ التحديث كمهمة خلفية عبر الجسر (إجراء local_index_refresh) ويرجع فوراً.
     * ما بيكتب بالشيت ولا بـ Cloudinary ولا بيصرف بحث مدفوع: بس بيقرا خرايط المتاجر (robots.txt محفوظ).
     */
    public function refresh()
    {
        $headers = ['Cache-Control' => 'no-store'];
        $flags = JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE;
        try {
            $result = PythonBridge::run('local_index_refresh', []);
        } catch (\Throwable $e) {
            $result = ['status' => 'error'];
        }
        if (($result['status'] ?? '') !== 'success') {
            return response()->json(['status' => 'failed',
                'error' => 'ما قدرنا نبدأ تحديث الفهرس: تأكد من بيئة بايثون ثم جرّب مرة تانية.'], 500, $headers, $flags);
        }
        return response()->json(['status' => 'success', 'started' => (bool) ($result['started'] ?? false),
            'running' => (bool) ($result['running'] ?? false), 'message' => (string) ($result['message_ar'] ?? '')],
            200, $headers, $flags);
    }
}
