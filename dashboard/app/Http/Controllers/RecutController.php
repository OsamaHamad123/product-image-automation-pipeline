<?php

namespace App\Http\Controllers;

use App\Services\PythonBridge;
use Illuminate\Http\Request;

/**
 * «أعد القص» (recut.py بالمشروع):
 *
 * - بطاقة «صور قديمة بخلفية بيضا» بالقسم المتقدم بصفحة الصحة (public/js/health.js createReprocess):
 *   POST /api/system/reprocess/plan  التجربة (cli_bridge reprocess_plan): كم صورة أصلها لسا أبيض وبالشيت وقديش بتكلّف.
 *                                     ما بتغيّر شي.
 *   POST /api/system/reprocess/start «ابدأ» بدفعة بسقف (عدد صور وتكلفة) كعملية خلفية (cli_bridge reprocess_start).
 *   GET  /api/system/reprocess       حالة آخر دفعة من temp/reprocess_state.json (بلا جسر).
 */
class RecutController extends Controller
{
    public const STATE_FILE = '../temp/reprocess_state.json';
    /** نفس scripts/reprocess_transparent.STALE_S: دفعة ما تحدّثت حالتها من 10 دقايق ماتت. */
    public const STALE_SECONDS = 600;
    public const DEFAULT_MAX = 20;
    public const DEFAULT_MAX_USD = 1.0;

    /** أسباب ما بلّشت الدفعة (reprocess_start). */
    public const START_REASONS = [
        'started' => 'بلّشت الدفعة بالخلفية. فيك تسكّر الصفحة، والتقدم بيبيّن هون.',
        'running' => 'في دفعة شغّالة هلق. استنى لتخلص.',
        'run_active' => 'في تشغيل للأتمتة هلق. جرّب بعد ما يخلص.',
        'bg_off' => 'عزل الخلفية متوقف بالإعدادات، فما منقدر نقص الصور. رجّعه أول من تبويب «معالجة الصور».',
        'unavailable' => 'ما قدرنا نبدأ الدفعة: تأكد من بيئة بايثون وجرّب مرة تانية.',
    ];

    // ------------------------------------------------------------------
    // Health card «صور قديمة بخلفية بيضا»
    // ------------------------------------------------------------------

    /** حالة آخر دفعة: {state, running, done, planned, spent_usd, ...} أو {state: none}. */
    public static function batchState(?string $file = null, ?int $now = null): array
    {
        $file = $file ?? base_path(self::STATE_FILE);
        $data = is_file($file) ? json_decode((string) @file_get_contents($file), true) : null;
        if (!is_array($data) || !is_string($data['state'] ?? null)) {
            return ['state' => 'none', 'running' => false];
        }
        $now = $now ?? time();
        $fresh = $now - (float) ($data['updated_at'] ?? 0) < self::STALE_SECONDS;
        $out = ['state' => $data['state'], 'running' => in_array($data['state'], ['starting', 'running'], true) && $fresh];
        foreach (['planned', 'done', 'skipped', 'needs_look', 'busy', 'queued', 'calls', 'max'] as $key) {
            $out[$key] = (int) ($data[$key] ?? 0);
        }
        foreach (['spent_usd', 'max_usd'] as $key) {
            $out[$key] = round((float) ($data[$key] ?? 0), 4);
        }
        foreach (['started_at', 'finished_at', 'updated_at'] as $key) {
            $out[$key] = isset($data[$key]) && is_numeric($data[$key]) ? (int) $data[$key] : null;
        }
        $out['last_error'] = is_string($data['last_error'] ?? null) ? $data['last_error'] : '';
        $out['last_item'] = is_string($data['last_item'] ?? null) ? $data['last_item'] : '';
        $out['current'] = is_string($data['current'] ?? null) ? $data['current'] : '';
        if (!$out['running'] && in_array($out['state'], ['starting', 'running'], true)) {
            $out['state'] = 'stale';
        }
        return $out;
    }

    public function batchStatus()
    {
        return response()->json(['status' => 'success', 'batch' => self::batchState()], 200, ['Cache-Control' => 'no-store'],
            JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE);
    }

    public function plan()
    {
        $headers = ['Cache-Control' => 'no-store'];
        $flags = JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE;
        try {
            $result = PythonBridge::run('reprocess_plan', []);
        } catch (\Throwable $e) {
            $result = ['status' => 'error'];
        }
        if (($result['status'] ?? '') !== 'success' || !is_array($result['plan'] ?? null)) {
            return response()->json(['status' => 'failed',
                'error' => 'ما قدرنا نعدّ الصور: تأكد إنو الشيت وقاعدة البيانات شغّالين وجرّب مرة تانية.'], 500, $headers, $flags);
        }
        $plan = $result['plan'];
        $estimate = is_array($plan['estimate'] ?? null) ? $plan['estimate'] : [];
        return response()->json([
            'status' => 'success',
            'plan' => [
                'todo' => (int) ($plan['todo'] ?? 0), 'transparent' => (int) ($plan['transparent'] ?? 0),
                'not_in_sheet' => (int) ($plan['not_in_sheet'] ?? 0), 'skipped_before' => (int) ($plan['skipped_before'] ?? 0),
                'probe_failed' => (int) ($plan['probe_failed'] ?? 0), 'pictures' => (int) ($plan['pictures'] ?? 0),
                'method' => (string) ($plan['method'] ?? ''),
                'calls' => (int) ($estimate['calls'] ?? 0), 'price' => (float) ($estimate['price'] ?? 0),
                'usd' => (float) ($estimate['usd'] ?? 0), 'worst_usd' => (float) ($estimate['worst_usd'] ?? 0),
            ],
            'bg_off' => (bool) ($result['bg_off'] ?? false),
            'batch_max' => (int) ($result['batch_max'] ?? 200),
            'batch' => self::batchState(),
        ], 200, $headers, $flags);
    }

    public function start(Request $request)
    {
        $headers = ['Cache-Control' => 'no-store'];
        $flags = JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE;
        $max = filter_var($request->input('max', self::DEFAULT_MAX), FILTER_VALIDATE_INT);
        $usd = filter_var($request->input('max_usd', self::DEFAULT_MAX_USD), FILTER_VALIDATE_FLOAT);
        if ($max === false || $max < 1 || $max > 5000 || $usd === false || $usd <= 0 || $usd > 100) {
            return response()->json(['status' => 'invalid',
                'error' => 'اكتب عدد صور بين 1 و5000، وأقصى تكلفة أكبر من صفر ولحد 100 دولار.'], 422, $headers, $flags);
        }
        try {
            $result = PythonBridge::run('reprocess_start', ['max' => (int) $max, 'max_usd' => (float) $usd]);
        } catch (\Throwable $e) {
            $result = ['status' => 'error'];
        }
        if (($result['status'] ?? '') === 'invalid') {
            $limit = (int) ($result['batch_max'] ?? 200);
            return response()->json(['status' => 'invalid',
                'error' => "الدفعة الوحدة لحد {$limit} صورة، وأقصى تكلفة لحد 100 دولار."], 422, $headers, $flags);
        }
        if (($result['status'] ?? '') !== 'success') {
            return response()->json(['status' => 'failed', 'error' => self::START_REASONS['unavailable']], 500, $headers, $flags);
        }
        $reason = (string) ($result['reason'] ?? 'unavailable');
        return response()->json(['status' => 'success', 'started' => (bool) ($result['started'] ?? false),
            'reason' => $reason, 'message' => self::START_REASONS[$reason] ?? self::START_REASONS['unavailable'],
            'batch' => self::batchState()], 200, $headers, $flags);
    }
}
