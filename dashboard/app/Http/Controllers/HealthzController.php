<?php

namespace App\Http\Controllers;

use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Log;

/**
 * GET /healthz: فحص صحة للمراقبة الخارجية (UptimeRobot أو Uptime Kuma)، بلا أي سر وبلا كلمة سر.
 *
 * nginx يفتح هذا المسار فقط لعنوان 127.0.0.1 ولعناوين المراقبة التي أُعطيت لـ install.sh بـ --monitor-ip
 * (deploy/ubuntu/laqta.conf)، وكل باقي اللوحة محمي بكلمة سر. الرد JSON فيه أرقام وكلمات حالة فقط (لا مسارات، لا أسماء
 * منتجات، لا نص أخطاء قاعدة البيانات)، ورمز HTTP 200 إذا كل شيء سليم و503 إذا لا:
 *
 * - database: الاتصال يرد (SELECT 1).
 * - nightly: عمر آخر ليلة ناجحة (من run_history الذي يكتبه run_report.py) تحت 26 ساعة. العمر من وقت بدء الليلة لا من
 *   نهايتها، فلا ينقلب الفحص أحمر والليلة الحالية ما زالت تعمل. «ناجحة»: خلصت (done) أو سلّمت الطابور لتشغيل آخر
 *   (handed_over) أو وصلت حدها المقصود (time_limit أو budget_reached). فشل أو انقطاع أو رصيد Serper خلص أو إيقاف
 *   من اللوحة ليس نجاحاً. لا ليلة أبداً (سيرفر جديد) = never: ليس عطلاً بعد.
 * - outbox: كتابات الشيت الميتة (DEAD) المسجلة آخر 7 أيام = صفر. الأقدم تظهر في dead فقط ولا تُبقي الفحص أحمر للأبد
 *   (تشغيلة جديدة تعيد كتابة رابطها بصف جديد، والصف الميت يبقى سجلاً).
 * - disk: المساحة الفاضية على قرص المشروع 2GB على الأقل (نفس حد scripts/server_check.py).
 *
 * المسار خارج جلسة لارافيل (routes/web.php): مراقب كل دقيقة لا يملأ storage/framework/sessions بملفات.
 */
class HealthzController extends Controller
{
    /** أقصى عمر (ساعات) لآخر ليلة ناجحة. */
    public const NIGHTLY_MAX_HOURS = 26;
    /** أقل مساحة فاضية (GB) على قرص المشروع. */
    public const MIN_FREE_GB = 2.0;
    /** نافذة الكتابات الميتة التي تُفشل الفحص (أيام). */
    public const DEAD_WINDOW_DAYS = 7;

    /**
     * عمر (ثوانٍ) آخر ليلة ناجحة من وقت بدئها. NOW() بتوقيت MariaDB نفسه الذي كتب به بايثون started_at، فلا فرق
     * توقيت بين PHP والسيرفر (كما في TIMESTAMPDIFF لـ automation_state بـ ApiController::batchStatus).
     */
    public const SQL_LAST_NIGHTLY =
        "SELECT TIMESTAMPDIFF(SECOND, COALESCE(started_at, ended_at), NOW()) AS age_s FROM run_history "
        . "WHERE run_trigger = 'nightly' AND COALESCE(started_at, ended_at) IS NOT NULL "
        . "AND (outcome IN ('done', 'handed_over') "
        . "OR (outcome = 'stopped' AND stop_reason IN ('time_limit', 'budget_reached'))) "
        . "ORDER BY COALESCE(started_at, ended_at) DESC LIMIT 1";

    /** كل الكتابات الميتة، وما سُجل منها آخر DEAD_WINDOW_DAYS أيام. */
    public const SQL_DEAD =
        "SELECT COUNT(*) AS total, COALESCE(SUM(registered_at >= NOW() - INTERVAL " . self::DEAD_WINDOW_DAYS
        . " DAY), 0) AS recent FROM sheet_updates WHERE sync_status = 'DEAD'";

    public function show()
    {
        [$code, $body] = self::evaluate(self::collect());
        return response()->json($body, $code)->header('Cache-Control', 'no-store');
    }

    /**
     * الحقائق من قاعدة البيانات والقرص. أي خطأ يصير null لا استثناء: الفحص يرد دائماً (503 إن لزم) ولا ينقلب 500
     * بصفحة خطأ.
     *
     * @return array{db: bool, nightly_age_s: ?int, nightly_known: bool, dead_total: ?int, dead_recent: ?int, free_gb: ?float}
     */
    public static function collect(): array
    {
        $facts = ['db' => false, 'nightly_age_s' => null, 'nightly_known' => false, 'dead_total' => null,
                  'dead_recent' => null, 'free_gb' => null];
        try {
            DB::select('SELECT 1');
            $facts['db'] = true;
        } catch (\Throwable $e) {
            Log::warning('healthz: database did not answer (' . get_class($e) . ')');
        }
        if ($facts['db']) {
            try {
                $row = DB::selectOne(self::SQL_LAST_NIGHTLY);
                $facts['nightly_age_s'] = $row && isset($row->age_s) ? (int) $row->age_s : null;
                $facts['nightly_known'] = true;
            } catch (\Throwable $e) {
                Log::warning('healthz: run_history unreadable (' . get_class($e) . ')');
            }
            try {
                $row = DB::selectOne(self::SQL_DEAD);
                $facts['dead_total'] = (int) ($row->total ?? 0);
                $facts['dead_recent'] = (int) ($row->recent ?? 0);
            } catch (\Throwable $e) {
                Log::warning('healthz: sheet_updates unreadable (' . get_class($e) . ')');
            }
        }
        $free = @disk_free_space(base_path('..'));
        $facts['free_gb'] = $free === false ? null : round($free / (1024 ** 3), 1);
        return $facts;
    }

    /**
     * [رمز HTTP، الجسم] من الحقائق (دالة صرفة تُختبر بلا قاعدة بيانات). حقيقة مجهولة (null) تُفشل فحصها: لا نقول
     * «سليم» عن شيء لم نقرأه.
     */
    public static function evaluate(array $facts, ?int $now = null): array
    {
        $db = ($facts['db'] ?? false) === true;
        $checks = ['database' => ['ok' => $db]];

        if (!($facts['nightly_known'] ?? false)) {
            $checks['nightly'] = ['ok' => false, 'status' => 'unknown', 'age_hours' => null,
                                  'max_hours' => self::NIGHTLY_MAX_HOURS];
        } elseif (!isset($facts['nightly_age_s'])) {
            $checks['nightly'] = ['ok' => true, 'status' => 'never', 'age_hours' => null,
                                  'max_hours' => self::NIGHTLY_MAX_HOURS];
        } else {
            $hours = round(max(0, (int) $facts['nightly_age_s']) / 3600, 1);
            $fresh = $hours <= self::NIGHTLY_MAX_HOURS;
            $checks['nightly'] = ['ok' => $fresh, 'status' => $fresh ? 'ok' : 'stale', 'age_hours' => $hours,
                                  'max_hours' => self::NIGHTLY_MAX_HOURS];
        }

        $known = isset($facts['dead_total'], $facts['dead_recent']);
        $checks['outbox'] = [
            'ok' => $known && (int) $facts['dead_recent'] === 0,
            'dead' => $known ? (int) $facts['dead_total'] : null,
            'dead_recent' => $known ? (int) $facts['dead_recent'] : null,
            'window_days' => self::DEAD_WINDOW_DAYS,
        ];

        $free = $facts['free_gb'] ?? null;
        $checks['disk'] = ['ok' => is_numeric($free) && (float) $free >= self::MIN_FREE_GB,
                           'free_gb' => is_numeric($free) ? (float) $free : null, 'min_gb' => self::MIN_FREE_GB];

        $ok = true;
        foreach ($checks as $check) {
            $ok = $ok && $check['ok'] === true;
        }
        return [$ok ? 200 : 503, ['ok' => $ok, 'checks' => $checks, 'checked_at' => gmdate('c', $now ?? time())]];
    }
}
