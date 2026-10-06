<?php

namespace App\Services;

/**
 * تشغيل عامل الخلفية من لوحة التحكم بطريقتين، والاحتفاظ بآخر سجلات التشغيل.
 *
 * - 'systemd': على سيرفر أوبونتو يركّب deploy/ubuntu/install.sh وحدتي laqta-run.path و laqta-run.service ويكتب في pool
 *   الـ php-fpm المتغير LAQTA_RUN_LAUNCHER=systemd. اللوحة تكتب ملف الطلب temp/run_request.json فقط، والوحدة (بمستخدم
 *   التطبيق نفسه) تنفذ الأمرين نفسيهما (--enqueue ثم --worker) داخل cgroup خاص بها: إعادة تشغيل أو ترقية php-fpm بلا
 *   إشراف لا تقتل تشغيلاً جارياً. الإيقاف والحالة كما هما (قفل temp/pipeline.lock ورقم عملية العامل).
 * - 'nohup': الطريقة القديمة (ويندوز، وأي جهاز بلا الوحدتين)؛ ApiController يشغّل العامل بنفسه.
 *
 * ملف الطلب إشارة فقط: لا يُقرأ محتواه ولا يُنفذ، والأمر ثابت في الوحدة. «الاستلام» نقل ذري (mv) من الوحدة، و«السحب»
 * بعد انتهاء المهلة نقل ذري (rename) من هنا: أحدهما فقط ينجح، فلا يبدأ تشغيل مرتين أبداً.
 */
class RunLauncher
{
    public const REQUEST_FILE = 'run_request.json';

    /** كم ثانية تنتظر اللوحة استلام الطلب قبل أن تسحبه وتعود للطريقة القديمة (الاختبارات تقصّرها). */
    public static float $waitSeconds = 10.0;

    /** عدد سجلات pipeline.log القديمة المحفوظة (pipeline.log.1 .. pipeline.log.N). */
    public const LOG_KEEP = 5;

    /** 'systemd' فقط إذا قال pool الـ php-fpm ذلك وكان النظام لينكس، وإلا 'nohup'. */
    public static function mode(?string $value = null): string
    {
        $value = $value ?? (string) (getenv('LAQTA_RUN_LAUNCHER') ?: '');
        return $value === 'systemd' && strncasecmp(PHP_OS, 'WIN', 3) !== 0 ? 'systemd' : 'nohup';
    }

    /**
     * يطلب تشغيلاً من laqta-run.path وينتظر استلامه. true = استلمته الوحدة (ستشغّل الأمرين نفسيهما)؛ false = لم يُكتب
     * الطلب أو لم يُستلم خلال المهلة، وقد سُحب بنقل ذري، فيشغّل المستدعي العامل بالطريقة القديمة (لا تشغيل مزدوج).
     *
     * @param callable|null $sleep fn(float $seconds) للاختبارات
     */
    public static function requestRun(string $tempDir, ?float $waitSeconds = null, ?callable $sleep = null): bool
    {
        $waitSeconds = $waitSeconds ?? self::$waitSeconds;
        $sleep = $sleep ?? static fn (float $s) => usleep((int) ($s * 1000000));
        $request = rtrim($tempDir, '/\\') . DIRECTORY_SEPARATOR . self::REQUEST_FILE;
        $tmp = $request . '.' . getmypid() . '.tmp';
        @unlink($request . '.cancelled');
        $body = json_encode(['requested_at' => gmdate('c'), 'trigger' => 'dashboard']);
        // اسم مؤقت ثم rename: الوحدة لا ترى ملفاً نصف مكتوب (PathExists يراقب الاسم النهائي فقط)
        if (@file_put_contents($tmp, $body) === false || !@rename($tmp, $request)) {
            @unlink($tmp);
            return false;
        }
        $deadline = microtime(true) + $waitSeconds;
        while (true) {
            clearstatcache(true, $request);
            if (!file_exists($request)) {
                return true;
            }
            if (microtime(true) >= $deadline) {
                break;
            }
            $sleep(0.1);
        }
        // لم يستلمه أحد: سحب ذري. نجح = لن تستلمه الوحدة بعد الآن. فشل لأن الملف اختفى = استلمته الوحدة للتو.
        if (@rename($request, $request . '.cancelled')) {
            return false;
        }
        clearstatcache(true, $request);
        if (!file_exists($request)) {
            return true;
        }
        @unlink($request);
        return false;
    }

    /**
     * يدوّر سجل التشغيل بدل حذفه: path.1 .. path.$keep (الأحدث 1)، والأقدم يُحذف. سجل فارغ لا يُدوّر (لا يُزاح سجل مفيد
     * بسبب تشغيل لم يكتب شيئاً). يعيد false إذا تعذر النقل (ملف مفتوح على ويندوز): المستدعي يتابع والسجل يُستبدل كما كان.
     */
    public static function rotateLog(string $path, int $keep = self::LOG_KEEP): bool
    {
        clearstatcache(true, $path);
        if (!is_file($path) || filesize($path) === 0) {
            return true;
        }
        $keep = max(1, $keep);
        // ما زاد عن العدد المحفوظ يُحذف أيضاً (بقايا عدد أكبر كان مضبوطاً قبلاً)
        foreach (glob($path . '.[0-9]*') ?: [] as $old) {
            if (preg_match('/\.(\d+)$/', $old, $m) && (int) $m[1] >= $keep) {
                @unlink($old);
            }
        }
        for ($i = $keep; $i >= 2; $i--) {
            $from = $path . '.' . ($i - 1);
            if (file_exists($from) && !@rename($from, $path . '.' . $i)) {
                return false;
            }
        }
        return @rename($path, $path . '.1');
    }
}
