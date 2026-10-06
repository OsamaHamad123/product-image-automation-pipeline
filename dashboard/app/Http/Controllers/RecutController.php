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
 * - صفحة «فحص القص» (/cutout-check، public/js/review/cutout.js): الصور المنشورة على الغامق والفاتح والمربعات، مع علامات
 *   القص؛ «أعد القص بـPhotoRoom» / «جرّب القص المحلي» بيعملوا قص جديد بيستنى (ما بينرفع)، «اعتمد الجديد» بينشره كنسخة
 *   جديدة (نفس طريق الدفعة: طابور الكتابة ورابط برقم نسخة جديد)، «خلّي القديم» بيمسحه، و«رجّع القديم» بيتراجع عن تبديل.
 *   ولا شي بيتبدّل لحاله: كل تبديل بكبسة المالك وبيتسجّل.
 */
class RecutController extends Controller
{
    public const STATE_FILE = '../temp/reprocess_state.json';
    public const PREVIEW_DIR = '../temp/recut/';
    /** نفس scripts/reprocess_transparent.STALE_S: دفعة ما تحدّثت حالتها من 10 دقايق ماتت. */
    public const STALE_SECONDS = 600;
    public const TOKEN = '/^[0-9a-f]{32}$/';
    public const FLAGS = ['white', 'dark_halo', 'photoroom_unsure', 'glass', 'dark_rim', 'low_res'];
    public const DEFAULT_MAX = 20;
    public const DEFAULT_MAX_USD = 1.0;

    /** أسباب ما بلّشت الدفعة (reprocess_start). */
    public const START_REASONS = [
        'started' => 'بلّشت الدفعة بالخلفية. فيك تسكّر الصفحة، والتقدم بيبيّن هون.',
        'running' => 'في دفعة شغّالة هلق. استنى لتخلص.',
        'run_active' => 'في تشغيل للأتمتة هلق. جرّب بعد ما يخلص.',
        'bg_off' => 'عزل الخلفية متوقف بالإعدادات، فما منقدر نقص الصور. رجّعه أول من تبويب «معالجة الصور».',
        'white_output' => 'الإعدادات بتنشر الصور على خلفية بيضا، فما في شي نعيده. غيّرها لشفافة أول.',
        'unavailable' => 'ما قدرنا نبدأ الدفعة: تأكد من بيئة بايثون وجرّب مرة تانية.',
    ];

    /** رموز «فحص القص» -> جملة للمالك. */
    public const ERRORS = [
        'expired' => 'القص الجديد ما عاد محفوظ (أقدم من يومين). جرّب القص مرة تانية.',
        'not_publishable' => 'القص الجديد ما انعزلت خلفيته منيح، فما منقدر ننشره. جرّب الطريقة التانية.',
        'changed_meanwhile' => 'الصورة تغيّرت من بعد ما فتحت الصفحة (اعتمدت صورة تانية). حدّث الصفحة.',
        'not_published' => 'هالصورة ما عادت منشورة. حدّث الصفحة.',
        'local_missing' => 'القص المحلي مش منزّل على هالجهاز.',
        'not_found' => 'هالتبديل مش موجود.',
        'status_undone' => 'هالتبديل انرجع من قبل.',
        'bg_removal_off' => 'عزل الخلفية متوقف بالإعدادات.',
        'photoroom_no_key' => 'مفتاح PhotoRoom مش محفوظ.',
        'photoroom_402' => 'رصيد PhotoRoom خلص أو الاشتراك موقوف.',
        'photoroom_429' => 'PhotoRoom رافض طلبات كتير هلق. جرّب بعد شوي.',
        'photoroom_timeout' => 'PhotoRoom ما ردّ بالوقت. جرّب مرة تانية.',
        'upload_failed' => 'ما قدرنا نرفع الصورة الجديدة على Cloudinary. جرّب مرة تانية.',
        'same_picture' => 'القص الجديد طلع نفس القديم بالضبط، فما في شي يتبدّل.',
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
            'white_output' => (bool) ($result['white_output'] ?? false),
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

    // ------------------------------------------------------------------
    // «فحص القص»
    // ------------------------------------------------------------------

    public function page()
    {
        return view('dashboard.cutout_check', [
            'cutoutConfig' => [
                'urls' => [
                    'gallery' => url('/api/cutout/gallery'), 'try' => url('/api/cutout/try'),
                    'apply' => url('/api/cutout/apply'), 'discard' => url('/api/cutout/discard'),
                    'undo' => url('/api/cutout/undo'), 'review' => route('dashboard.catalog'),
                    'health' => route('dashboard.diagnostics'),
                ],
                'flags' => self::FLAGS,
            ],
        ]);
    }

    /** رسالة الخطأ لرمز (أو الجملة العامة). */
    public static function errorText(string $code, string $fallback): string
    {
        if (isset(self::ERRORS[$code])) {
            return self::ERRORS[$code];
        }
        if (preg_match('/^(photoroom|removebg)_(401|403)$/', $code)) {
            return 'PhotoRoom رفض المفتاح.';
        }
        if (preg_match('/^(download_|source_|candidate_)/', $code)) {
            return 'ما قدرنا ننزّل الصورة الأصلية ولا المنشورة. جرّب بعد شوي.';
        }
        return $fallback;
    }

    private static function relay(string $action, array $params, string $fallback, int $okStatus = 200)
    {
        $headers = ['Cache-Control' => 'no-store'];
        $flags = JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE;
        try {
            $result = PythonBridge::run($action, $params);
        } catch (\Throwable $e) {
            $result = ['status' => 'error'];
        }
        $status = (string) ($result['status'] ?? 'error');
        if ($status === 'success') {
            unset($result['raw']);
            return response()->json($result, $okStatus, $headers, $flags);
        }
        $code = (string) ($result['error_code'] ?? '');
        $http = $status === 'invalid' ? 422 : ($status === 'failed' && $code !== '' ? 409 : 500);
        return response()->json(['status' => $status === 'invalid' ? 'invalid' : 'failed', 'error_code' => $code,
            'paid_calls' => (int) ($result['paid_calls'] ?? 0), 'error' => self::errorText($code, $fallback)],
            $http, $headers, $flags);
    }

    public function gallery(Request $request)
    {
        $flag = (string) $request->query('flag', '');
        if ($flag !== '' && !in_array($flag, self::FLAGS, true)) {
            $flag = '';
        }
        $page = filter_var($request->query('page', 1), FILTER_VALIDATE_INT);
        $params = ['page' => $page === false || $page < 1 ? 1 : (int) $page,
                   'measure' => $request->query('measure', '1') !== '0'];
        if ($flag !== '') {
            $params['flag'] = $flag;
        }
        return self::relay('cutout_gallery', $params, 'ما قدرنا نقرا الصور المنشورة. تأكد إنو قاعدة البيانات شغّالة.');
    }

    public function tryCut(Request $request)
    {
        $id = filter_var($request->input('id'), FILTER_VALIDATE_INT);
        $method = (string) $request->input('method', '');
        if ($id === false || $id < 1 || !in_array($method, ['photoroom', 'local'], true)) {
            return response()->json(['status' => 'invalid', 'error' => 'طلب ناقص.'], 422, ['Cache-Control' => 'no-store'],
                JSON_UNESCAPED_UNICODE);
        }
        return self::relay('recut_try', ['id' => (int) $id, 'method' => $method], 'ما زبط القص الجديد. جرّب مرة تانية.');
    }

    private static function token(Request $request): ?string
    {
        $token = (string) $request->input('token', '');
        return preg_match(self::TOKEN, $token) ? $token : null;
    }

    public function apply(Request $request)
    {
        $token = self::token($request);
        if ($token === null) {
            return response()->json(['status' => 'invalid', 'error' => 'طلب ناقص.'], 422, [], JSON_UNESCAPED_UNICODE);
        }
        return self::relay('recut_apply', ['token' => $token], 'ما قدرنا ننشر القص الجديد. القديم لسا متل ما هو.');
    }

    public function discard(Request $request)
    {
        $token = self::token($request);
        if ($token === null) {
            return response()->json(['status' => 'invalid', 'error' => 'طلب ناقص.'], 422, [], JSON_UNESCAPED_UNICODE);
        }
        return self::relay('recut_discard', ['token' => $token], 'ما قدرنا نمسح القص الجديد.');
    }

    public function undo(Request $request)
    {
        $id = filter_var($request->input('log_id'), FILTER_VALIDATE_INT);
        if ($id === false || $id < 1) {
            return response()->json(['status' => 'invalid', 'error' => 'طلب ناقص.'], 422, [], JSON_UNESCAPED_UNICODE);
        }
        return self::relay('recut_undo', ['log_id' => (int) $id], 'ما قدرنا نرجّع الصورة القديمة. جرّب مرة تانية.');
    }

    /** GET /api/cutout/preview/{token}: معاينة القص الجديد (temp/recut/<token>_preview.png)، وإلا 404. */
    public function preview(string $token)
    {
        $path = base_path(self::PREVIEW_DIR . $token . '_preview.png');
        if (!preg_match(self::TOKEN, $token) || !is_file($path)) {
            return response()->json(['status' => 'error'], 404, ['Cache-Control' => 'no-store']);
        }
        return response()->file($path, ['Content-Type' => 'image/png', 'Cache-Control' => 'no-store']);
    }
}
