<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;
use App\Services\PythonBridge;
use App\Services\QueueStats;

class ApiController extends Controller
{
    private function getPythonPath()
    {
        return PythonBridge::pythonPath();
    }

    /**
     * تنفيذ أوامر جسر بايثون مباشرة عبر سطر الأوامر (cli_bridge.py) بترميز UTF-8.
     * لا توجد أي محاولة HTTP لخادم FastAPI: وسيلة نقل واحدة وسلوك واحد.
     */
    private function runPython($action, $params = [])
    {
        return PythonBridge::run($action, $params);
    }

    /**
     * المسار الجذري لمشروع الأتمتة (المجلد الأب لمجلد لوحة التحكم).
     */
    private function automationPath(string $relative = ''): string
    {
        $base = base_path('..');
        if ($relative === '') {
            return $base;
        }
        return $base . DIRECTORY_SEPARATOR . str_replace(['/', '\\'], DIRECTORY_SEPARATOR, $relative);
    }

    /**
     * البحث عن صورة منتج معين.
     * الحالات المكتملة (success / review / not_found) تعاد بـ 200 لكي تعرض الواجهة المرشحين دائماً،
     * و provider_down تعاد بـ 503 (قابلة لإعادة المحاولة) وأي خطأ آخر بـ 500.
     */
    public function search(Request $request)
    {
        $result = $this->runPython('search', $request->all());
        return response()->json($result, PythonBridge::httpStatus($result));
    }

    /**
     * اعتماد صورة معينة وتحديث الشيت.
     * expected_state (ما رأته الصفحة: حالة صف الطابور ووقت تحديثه والصورة المعتمدة) و replace (تأكيد المراجع الصريح
     * باستبدال ما تغيّر) يمران إلى الجسر ضمن الطلب؛ replace قيمة منطقية فقط.
     */
    public function selectImage(Request $request)
    {
        $params = $request->all();
        if ($request->has('replace')) {
            $params['replace'] = $request->boolean('replace');
        }
        $result = $this->runPython('select_image', $params);

        if (($result['status'] ?? '') === 'success') {
            // تفريغ كاش الكتالوج ليعاد قراءته بالشيت المحدث
            ProductController::forgetProductCaches();
        }
        return response()->json($result, ($result['status'] ?? '') === 'success' ? 200 : 500);
    }

    /**
     * رفض واستبعاد صورة وتسجيل سبب الرفض (مع إعادة بحث اختيارية research=true).
     */
    public function rejectImage(Request $request)
    {
        $result = $this->runPython('reject_image', $request->all());

        if (!PythonBridge::isError($result)) {
            ProductController::forgetProductCaches();
        }
        return response()->json($result, PythonBridge::httpStatus($result));
    }

    /**
     * رفع صورة يدوياً ومعالجتها بالكامل
     */
    public function uploadManualImage(Request $request)
    {
        if (!$request->hasFile('file')) {
            return response()->json(['error' => 'No file uploaded'], 400);
        }

        $file = $request->file('file');
        
        // حفظ مؤقت للملف المرفوع في مجلد temp التابع للأوتوميشن ليتعامل معه البايثون
        $tempDir = $this->automationPath('temp');
        if (!file_exists($tempDir)) {
            mkdir($tempDir, 0777, true);
        }
        
        // اسم ملف آمن بحروف ASCII فقط (لا نستخدم اسم الملف الأصلي في المسار)
        $extension = strtolower(preg_replace('/[^A-Za-z0-9]/', '', (string) $file->getClientOriginalExtension())) ?: 'png';
        $safeName = 'manual_' . time() . '_' . bin2hex(random_bytes(6)) . '.' . $extension;
        $targetPath = $tempDir . DIRECTORY_SEPARATOR . $safeName;
        $file->move($tempDir, $safeName);

        // اللوحة البيضاء ثابتة (88%)؛ لا نمرر خيارات الهامش أو اللون أو التكبير لأنها لم تعد مستخدمة
        $params = $request->only([
            'row_number', 'product_name', 'brand', 'barcode', 'sku_key',
            'size', 'product_name_ar', 'brand_ar', 'category',
            'target_width', 'target_height', 'enhance', 'search_decision'
        ]);
        // ما رأته الصفحة (expected_state) يصل نصاً JSON من نموذج الرفع، و replace تأكيد صريح بالاستبدال (قيمة منطقية)؛
        // يقرؤهما cli_bridge._stale_refusal
        $expected = $request->input('expected_state');
        if (is_string($expected)) {
            $decoded = json_decode($expected, true);
            $expected = is_array($decoded) ? $decoded : null;
        }
        if (is_array($expected)) {
            $params['expected_state'] = array_intersect_key($expected, array_flip(['queue_status', 'queue_updated_at', 'approved_url']));
        }
        if ($request->has('replace')) {
            $params['replace'] = $request->boolean('replace');
        }
        $params['file_path'] = $targetPath;

        $result = $this->runPython('upload_manual_image', $params);
        
        // التأكد من مسح الملف المؤقت في حال عدم مسحه بالبايثون
        if (file_exists($targetPath)) {
            @unlink($targetPath);
        }

        if (isset($result['status']) && $result['status'] === 'success') {
            ProductController::forgetProductCaches();
            return response()->json($result, 200);
        }
        return response()->json($result, 500);
    }


    public function clearProductsCache()
    {
        ProductController::forgetProductCaches();

        $pCache = $this->automationPath('products_cache.json');
        $bCache = $this->automationPath('brand_mappings_cache.json');
        
        if (file_exists($pCache)) {
            @unlink($pCache);
        }
        if (file_exists($bCache)) {
            @unlink($bCache);
        }
        
        return response()->json(['status' => 'success', 'message' => 'Products cache cleared']);
    }

    /**
     * تشغيل الأتمتة الكلية بالخلفية بدون خادم Flask
     */
    public function runAll(Request $request)
    {
        try {
            $basePath = base_path('..');
            $scriptPath = $basePath . DIRECTORY_SEPARATOR . 'main.py';
            $tempDir = $basePath . DIRECTORY_SEPARATOR . 'temp';
            $logPath = $tempDir . DIRECTORY_SEPARATOR . 'pipeline.log';
            $lockFile = $tempDir . DIRECTORY_SEPARATOR . 'pipeline.lock';
            
            if (!file_exists($tempDir)) {
                mkdir($tempDir, 0777, true);
            }

            // Check if already running or starting
            if ($this->pipelineProcess()['state'] !== 'none') {
                return response()->json(['status' => 'failed', 'error' => 'عملية الأتمتة قيد التشغيل بالفعل حالياً.'], 400);
            }

            $configData = $request->all();
            file_put_contents($tempDir . DIRECTORY_SEPARATOR . 'run_config.json', json_encode($configData));

            // تشغيل جديد: حالة 'starting' بلا أرقام التشغيل السابق، ويُلغى طلب إيقاف أو إيقاف مؤقت قديم.
            // يتم قبل كتابة قفل 'STARTING': زر الإيقاف لا يظهر إلا مع القفل، فطلب إيقاف يصل بعده (أثناء قراءة
            // الشيت) لا يمسحه هذا التجهيز ويلتزم به العامل. لو كُتب القفل أولاً لمسح التجهيز (1-3 ثوانٍ) طلب إيقاف
            // ضُغط خلالها، وقالت اللوحة «سُجل طلب الإيقاف» ثم استمر التشغيل.
            $prepared = $this->runPython('run_control', ['op' => 'start']);
            if (($prepared['status'] ?? '') !== 'success') {
                return response()->json(['status' => 'failed', 'error' => $prepared['error'] ?? 'تعذر تجهيز التشغيل؛ لم يبدأ أي تشغيل.'], 500);
            }
            // تشغيل بدأ من مكان آخر (التشغيل الليلي أو تبويب آخر) أثناء التجهيز
            if ($this->pipelineProcess()['state'] !== 'none') {
                return response()->json(['status' => 'failed', 'error' => 'عملية الأتمتة قيد التشغيل بالفعل حالياً.'], 400);
            }

            file_put_contents($lockFile, 'STARTING');

            if (file_exists($logPath)) {
                @unlink($logPath);
            }

            $pythonPath = $this->getPythonPath();

            // ترميز UTF-8 إلزامي للعامل بالخلفية (أسماء عربية وسجلات بدون UnicodeEncodeError)
            putenv('PYTHONUTF8=1');
            putenv('PYTHONIOENCODING=utf-8');
            
            if (strncasecmp(PHP_OS, 'WIN', 3) === 0) {
                // Windows background execution using a dynamically created batch file to resolve nested quote issues
                $batContent = "@echo off\r\n";
                $batContent .= "set PYTHONUTF8=1\r\n";
                $batContent .= "set PYTHONIOENCODING=utf-8\r\n";
                $batContent .= "cd /d \"" . $basePath . "\"\r\n";
                $batContent .= "\"" . $pythonPath . "\" \"" . $scriptPath . "\" --enqueue > \"" . $logPath . "\" 2>&1\r\n";
                $batContent .= "if %errorlevel% equ 0 (\r\n";
                $batContent .= "    \"" . $pythonPath . "\" -u \"" . $scriptPath . "\" --worker --trigger=dashboard >> \"" . $logPath . "\" 2>&1\r\n";
                $batContent .= ")\r\n";
                
                $batFile = $tempDir . DIRECTORY_SEPARATOR . 'run_pipeline.bat';
                file_put_contents($batFile, $batContent);
                
                $cmd = "cmd /c \"" . $batFile . "\"";
                try {
                    if (class_exists('COM')) {
                        $WshShell = new \COM("WScript.Shell");
                        $WshShell->Run($cmd, 0, false);
                    } else {
                        throw new \Exception("COM class is not loaded");
                    }
                } catch (\Throwable $ex) {
                    $popenCmd = "start /B \"\" {$cmd}";
                    pclose(popen($popenCmd, "r"));
                }
            } else {
                // Linux background execution
                $cmd = "cd \"" . $basePath . "\" && export PYTHONUTF8=1 PYTHONIOENCODING=utf-8 && \"" . $pythonPath . "\" \"" . $scriptPath . "\" --enqueue > \"" . $logPath . "\" 2>&1 && \"" . $pythonPath . "\" -u \"" . $scriptPath . "\" --worker --trigger=dashboard >> \"" . $logPath . "\" 2>&1";
                $linuxCmd = "nohup sh -c " . escapeshellarg($cmd) . " > /dev/null 2>&1 &";
                shell_exec($linuxCmd);
            }
            
            return response()->json(['status' => 'success', 'message' => 'Full automation queue worker started in background.']);
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }

    /** حكم بايثون على القفل يُحفظ هذه المدة لاستعلامات الحالة المتكررة (الشريط الجانبي يسأل كل بضع ثوانٍ). */
    private const LOCK_STATE_CACHE_S = 10;
    private const LOCK_STATE_CACHE_KEY = 'lq_pipeline_lock_state';

    /**
     * حالة عامل الخلفية من ملف القفل temp/pipeline.lock: starting = كتبت لوحة التحكم 'STARTING' والإدراج يقرأ الشيت
     * (حتى 5 دقائق)، running = عامل حي، none = لا قفل أو قفل متروك. الحكم على قفل يحمل رقم عملية يأتي من بايثون
     * (cli_bridge lock_state = main.lock_verdict: نبض القفل وهوية العملية واسم الجهاز)، فلا توجد قاعدة ثانية هنا
     * تختلف عنها. verified: هوية العملية مؤكدة (سطر الأوامر ووقت البدء)، ووحدها تسمح بإنهائها. بايثون لم يرد:
     * running بلا verified (لا تشغيل ثانٍ ولا إنهاء أي عملية). $fresh=false (batchStatus) يقبل حكماً عمره حتى
     * LOCK_STATE_CACHE_S لمحتوى القفل نفسه.
     */
    private function pipelineProcess(bool $fresh = true): array
    {
        $lockFile = $this->automationPath('temp/pipeline.lock');
        $process = ['state' => 'none', 'pid' => null, 'verified' => false, 'reason' => '', 'role' => null,
                    'lock' => $lockFile, 'lock_exists' => file_exists($lockFile)];
        if (!$process['lock_exists']) {
            return $process;
        }
        $lockContent = trim((string) @file_get_contents($lockFile));
        if ($lockContent === 'STARTING') {
            if (time() - filemtime($lockFile) < 300) {
                $process['state'] = 'starting';
            }
            return $process;
        }
        $verdict = $fresh ? $this->lockVerdict($lockFile) : $this->cachedLockVerdict($lockFile, $lockContent);
        $state = (string) ($verdict['state'] ?? 'running');
        $process['state'] = in_array($state, ['none', 'starting', 'running'], true) ? $state : 'running';
        $process['pid'] = is_numeric($verdict['pid'] ?? null) && (int) $verdict['pid'] > 1 ? (string) (int) $verdict['pid'] : null;
        $process['verified'] = ($verdict['verified'] ?? false) === true && $process['pid'] !== null;
        $process['reason'] = (string) ($verdict['reason'] ?? '');
        $process['role'] = is_string($verdict['role'] ?? null) ? $verdict['role'] : null;
        return $process;
    }

    /** حكم بايثون على القفل (cli_bridge lock_state)؛ بلا رد صالح: قفل حي غير مؤكد (لا يُحذف ولا تُنهى عمليته). */
    private function lockVerdict(string $lockFile): array
    {
        $result = $this->runPython('lock_state', ['lock' => $lockFile]);
        if (($result['status'] ?? '') !== 'success') {
            return ['state' => 'running', 'pid' => null, 'verified' => false, 'reason' => 'lock_state_unavailable'];
        }
        return $result;
    }

    /** lockVerdict محفوظ لمحتوى القفل نفسه حتى LOCK_STATE_CACHE_S (مفتاح واحد في الكاش، لا مفتاح لكل نبضة). */
    private function cachedLockVerdict(string $lockFile, string $lockContent): array
    {
        $key = md5($lockFile . "\n" . $lockContent);
        $cached = Cache::get(self::LOCK_STATE_CACHE_KEY);
        if (is_array($cached) && ($cached['key'] ?? null) === $key && is_array($cached['verdict'] ?? null)
            && time() - (int) ($cached['at'] ?? 0) < self::LOCK_STATE_CACHE_S) {
            return $cached['verdict'];
        }
        $verdict = $this->lockVerdict($lockFile);
        Cache::put(self::LOCK_STATE_CACHE_KEY, ['key' => $key, 'at' => time(), 'verdict' => $verdict],
            self::LOCK_STATE_CACHE_S);
        return $verdict;
    }

    private function processAlive(string $pid): bool
    {
        if (strncasecmp(PHP_OS, 'WIN', 3) === 0) {
            $output = (string) shell_exec("tasklist /FI \"PID eq {$pid}\" 2>&1");
            return strpos($output, $pid) !== false && strpos(strtolower($output), 'python') !== false;
        }
        if (function_exists('posix_kill')) {
            if (!@posix_kill((int) $pid, 0)) {
                return false;
            }
            // عملية أُنهيت ولم يحصدها أبوها بعد (zombie) ليست حية؛ وإلا عُدّ العامل المُنهى «تعذر إنهاؤه»
            // فبقيت صفوفه في «قيد المعالجة» وبقي طلب إيقاف معلق وقفل بلا عامل
            $stat = @file_get_contents("/proc/{$pid}/stat");
            return !(is_string($stat) && preg_match('/\)\s+Z\s/', $stat));
        }
        $output = (string) shell_exec("ps -p {$pid} 2>&1");
        return strpos($output, $pid) !== false;
    }

    /**
     * يُنهي عامل الخلفية الحي ويعيد وصفه لإجراء run_control:
     * starting (الإدراج يقرأ الشيت ولا PID بعد) | running (بقي حياً، أو لم تتأكد هويته فلم يُنهَ) | killed | none.
     * لا يُنهي أبداً عملية لم يؤكد بايثون هويتها (verified): رقم عملية أُعيد استخدامه قد يكون أي برنامج آخر.
     */
    private function terminateWorker(array $process): string
    {
        if ($process['state'] === 'starting') {
            return 'starting';
        }
        if ($process['state'] !== 'running') {
            return 'none';
        }
        if (($process['verified'] ?? false) !== true || $process['pid'] === null) {
            return 'running';
        }
        if (strncasecmp(PHP_OS, 'WIN', 3) === 0) {
            shell_exec("taskkill /F /PID {$process['pid']} 2>&1");
        } else {
            shell_exec("kill -9 {$process['pid']} 2>&1");
        }
        // الإنهاء قد يأخذ لحظة (taskkill، أو أب يحصد العملية متأخراً): حتى 1.5 ثانية قبل الحكم بأنه بقي حياً
        for ($i = 0; $i < 5; $i++) {
            usleep(300000);
            if (!$this->processAlive($process['pid'])) {
                return 'killed';
            }
        }
        return 'running';
    }

    /**
     * «إيقاف» و«إصلاح تشغيل عالق» لعامل حي: يُنتظر العامل حتى هذه المدة بعد طلب الإيقاف (ينهي المنتجات الجارية، ويكتب
     * تقرير التشغيل وصفه في سجل التشغيلات، ويخرج التشغيل الليلي برمز 3)، ثم فقط يُنهى قسراً.
     */
    public static $stopWaitSeconds = 90;

    /**
     * يوقف عامل الخلفية بأمان ويعيد وصفه لإجراء run_control:
     * starting | none كما في terminateWorker؛ لعامل حي: يُسجل طلب إيقاف (run_control stop / running) ثم يُنتظر حتى
     * stopWaitSeconds: exited = توقف بنفسه وكتب تقريره؛ وإلا terminateWorker: killed (أُنهي؛ run_control يكتب تقرير
     * «توقف» بدلاً منه) أو running (بقي حياً أو لم تتأكد هويته؛ طلب الإيقاف يبقى ويلتزم به بين المنتجات).
     * $requested يعود برد run_control على طلب الإيقاف (null إن لم يُطلب).
     */
    private function stopWorker(array $process, ?array &$requested = null): string
    {
        $requested = null;
        if ($process['state'] !== 'running') {
            return $this->terminateWorker($process);
        }
        $requested = $this->runPython('run_control', ['op' => 'stop', 'worker' => 'running']);
        if (($requested['status'] ?? '') === 'success') {
            $wait = max(0, (int) self::$stopWaitSeconds);
            $limit = (int) ini_get('max_execution_time');
            if ($limit !== 0 && $limit < $wait + 120) {
                @set_time_limit($wait + 120);
            }
            $deadline = microtime(true) + $wait;
            while (true) {
                if ($this->workerLeft($process)) {
                    return 'exited';
                }
                if (microtime(true) >= $deadline) {
                    break;
                }
                usleep(500000);
            }
        }
        // طلب الإيقاف لم يُسجل (قاعدة البيانات لا ترد) أو لم يلتزم به العامل في المهلة
        return $this->terminateWorker($process);
    }

    /** خرج العامل: القفل لم يعد يحمل رقم عمليته، أو عمليته انتهت. */
    private function workerLeft(array $process): bool
    {
        clearstatcache();
        $content = trim((string) @file_get_contents($process['lock']));
        if ($content === '') {
            return !file_exists($process['lock']);
        }
        $data = json_decode($content, true);
        $lockPid = ctype_digit($content) ? $content
            : (is_array($data) && is_numeric($data['pid'] ?? null) ? (string) (int) $data['pid'] : null);
        if ($process['pid'] === null) {
            return $lockPid === null;
        }
        return $lockPid !== $process['pid'] || !$this->processAlive($process['pid']);
    }

    private function removeRunFiles(array $process): void
    {
        foreach ([$process['lock'], $this->automationPath('temp/batch_progress.json')] as $path) {
            if (file_exists($path)) {
                @unlink($path);
            }
        }
    }

    /**
     * مراقبة حالة الأتمتة في الخلفية باستخدام ملف الـ Lock للعملية (PID Lock).
     * phase / phase_text / alert تحسبها QueueStats لكل الصفحات؛ run هو تقدم التشغيل الحالي فقط
     * (صفوف run_id)، و queue عدادات الطابور كله.
     */
    public function batchStatus()
    {
        $state = null;
        try {
            $stateRow = \DB::select("SELECT *, TIMESTAMPDIFF(SECOND, updated_at, NOW()) AS lq_age_s FROM automation_state WHERE `key` = 'active_session' LIMIT 1");
            $state = $stateRow[0] ?? null;
        } catch (\Exception $e) {
            // Table not loaded yet
        }
        $status = (string) ($state->status ?? 'idle');
        $currentProduct = (string) ($state->current_product_name ?? '');
        $pauseRequested = (int) ($state->pause_requested ?? 0);
        $stopRequested = (int) ($state->stop_requested ?? 0);
        $notice = (string) ($state->notice ?? '');

        $process = $this->pipelineProcess(false);
        $isRunning = $process['state'] !== 'none';
        $counters = QueueStats::counters();
        $readyForReview = (int) ($counters['by_status']['ready_for_review'] ?? 0);

        // Self-healing heartbeat: If status says a run is active but no worker is running,
        // settle it (review or idle) if it has been inactive for more than 45 seconds or if the lock file is missing.
        // طلب الإيقاف المؤقت لا يُلغى هنا: لو أخطأ الحكم بأن العامل متوقف لاستأنف عاملٌ موقوف مؤقتاً العمل بصمت؛
        // التشغيل التالي يلغيه (prepare_run، resume_automation)
        if (!$isRunning && in_array($status, ['pre_caching', 'running', 'starting'], true)) {
            $lastUpdated = isset($state->updated_at) ? strtotime($state->updated_at . ' UTC') : time();
            $diff = time() - $lastUpdated;
            if ($diff > 45 || !$process['lock_exists']) {
                $settled = $readyForReview > 0 ? 'curation_pending' : 'idle';
                try {
                    \DB::update("UPDATE automation_state SET status = ?, total_items = 0, processed_items = 0, success_count = 0, failed_count = 0, current_product_name = '' WHERE `key` = 'active_session'", [$settled]);
                    $status = $settled;
                    $currentProduct = "";
                } catch (\Exception $e) {
                    // Ignore
                }
            }
        }

        // تقدم التشغيل الحالي فقط؛ بدون run_id (عامل شُغل يدوياً) أرقام العامل كما كتبها
        $run = QueueStats::run($state->run_id ?? null);
        $success = $run ? $run['ready_for_review'] + $run['completed'] : (int) ($state->success_count ?? 0);
        if ($run === null) {
            $run = QueueStats::runTotals(null, []);
            $run['total'] = (int) ($state->total_items ?? 0);
            $run['processed'] = (int) ($state->processed_items ?? 0);
            $run['failed'] = (int) ($state->failed_count ?? 0);
        }
        $phase = QueueStats::runPhase($process['state'], $status, $pauseRequested, $stopRequested, $readyForReview);
        $processingRows = (int) ($counters['by_status']['processing'] ?? 0);
        // العامل حي ولا يبحث الآن: ينتظر موعد إعادة محاولة بعد انقطاع المزودين (ليس عالقاً)
        $retryWaitS = ($phase === 'running' && $processingRows === 0) ? QueueStats::retryWaitS() : null;

        $response = [
            'is_running' => $isRunning,
            // starting | running | paused | stopping | error | review | idle
            'phase' => $phase,
            'phase_text' => QueueStats::phaseText($phase, $stopRequested, $readyForReview,
                (int) ($counters['by_status']['pending'] ?? 0), $retryWaitS),
            // ثوانٍ حتى المحاولة التالية التي ينتظرها العامل (null = لا ينتظر)
            'retry_wait_s' => $retryWaitS,
            // الشريط الأحمر: خطأ التشغيل أو التنبيه بالعربية (فارغ إن لم يوجد)
            'alert' => QueueStats::alertText($status, $notice, $isRunning),
            'status' => $status,
            'stop_requested' => $stopRequested,
            'run' => $run,
            'total' => $run['total'],
            'current' => $run['processed'],
            'success' => $success,
            'failed' => $run['failed'],
            'current_product' => $currentProduct,
            'pause_requested' => $pauseRequested,
            // عدادات حقيقية من جدول automation_queue: حسب الحالة وحسب رمز الفشل
            'queue' => $counters['by_status'],
            'ready_for_review' => $readyForReview,
            'approved' => $counters['by_status']['completed'] ?? 0,
            'failed_by_code' => $counters['by_failure_code'],
            // حالة المحقق/المزودين كما يكتبها العامل (مثلاً نموذج Gemini غير متاح)
            'notice' => $notice,
            // صفحة التشغيل (إضافة فقط): حالة قفل العامل، وسبب عرض «إصلاح تشغيل عالق» ('' = التشغيل غير عالق)
            'worker' => $process['state'],
            'state_age_s' => isset($state->lq_age_s) ? (int) $state->lq_age_s : null,
            'stuck' => QueueStats::stuckReason($phase, $process['state'], $status, $processingRows,
                isset($state->lq_age_s) ? (int) $state->lq_age_s : null, $pauseRequested, $retryWaitS),
        ];

        return response()->json($response)->header('Cache-Control', 'no-store');
    }

    /**
     * إيقاف الأتمتة مؤقتاً
     */
    public function pauseBatch()
    {
        try {
            \DB::update("UPDATE automation_state SET pause_requested = 1 WHERE `key` = 'active_session'");
            return response()->json(['status' => 'success', 'message' => 'Automation paused.']);
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }

    /**
     * استئناف الأتمتة
     */
    public function resumeBatch()
    {
        try {
            \DB::update("UPDATE automation_state SET pause_requested = 0 WHERE `key` = 'active_session'");
            return response()->json(['status' => 'success', 'message' => 'Automation resumed.']);
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }

    /**
     * «إصلاح تشغيل عالق»: يمسح حالة التشغيل العالقة فقط ولا يحذف أي عمل مراجعة.
     * يوقف عاملاً ما زال حياً كما يفعل زر الإيقاف (stopWorker: طلب إيقاف ثم انتظار، والإنهاء فقط بعد المهلة)، ثم
     * run_control reset: الصفوف في 'processing' تعود إلى 'pending'، ويُمسح التقدم والتنبيه والإيقاف المؤقت. يُحذف ملف
     * القفل وملف التقدم إلا لعامل بقي حياً (لا يُفتح الباب لعامل ثانٍ فوقه).
     * لا يحذف أي صف من automation_queue ولا curation_candidates ولا review_decisions ولا rejected_images
     * ولا resolved_products. لا يوجد زر «تفريغ الطابور»: الإدراج التالي يحدّث الصفوف من الشيت (Upsert).
     */
    public function resetBatch()
    {
        try {
            $basePath = base_path('..');
            $process = $this->pipelineProcess();
            $worker = $this->stopWorker($process);

            // Clear Laravel cache
            ProductController::forgetProductCaches();

            // Clear python disk cache files
            $pCache = $basePath . DIRECTORY_SEPARATOR . 'products_cache.json';
            $bCache = $basePath . DIRECTORY_SEPARATOR . 'brand_mappings_cache.json';
            if (file_exists($pCache)) {
                @unlink($pCache);
            }
            if (file_exists($bCache)) {
                @unlink($bCache);
            }

            // run_control قبل حذف القفل: تقرير «توقف» لعامل أُنهي يُبنى من قفله
            $result = $this->runPython('run_control', ['op' => 'reset', 'worker' => $worker]);
            if (in_array($worker, ['killed', 'none', 'starting'], true)) {
                $this->removeRunFiles($process);
            }
            if (($result['status'] ?? '') !== 'success') {
                return response()->json(['status' => 'failed', 'error' => $result['error'] ?? 'تعذر إصلاح حالة التشغيل.'], 500);
            }
            return response()->json($result + ['worker' => $worker]);
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }

    /**
     * جلب الصور الخارجية وتخطي حظر الـ Hotlinking.
     * يقبل http/https فقط ولا يعيد إلا محتوى صورة نقطية (raster) بنوع image/* صحيح،
     * حتى لا تُخدم صفحة HTML أو SVG من نطاق لوحة التحكم.
     */
    public function imageProxy(Request $request)
    {
        $url = (string) $request->query('url', '');
        if ($url === '') {
            return response('Missing URL', 400);
        }
        $scheme = strtolower((string) parse_url($url, PHP_URL_SCHEME));
        if (!in_array($scheme, ['http', 'https'], true)) {
            return response('Unsupported URL scheme', 400);
        }
        try {
            $response = \Illuminate\Support\Facades\Http::withoutVerifying()->withHeaders([
                'User-Agent' => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept' => 'image/webp,image/png,image/jpeg,image/*;q=0.8'
            ])->timeout(10)->get($url);

            if (!$response->successful()) {
                return response('Error fetching image', $response->status());
            }
            $body = $response->body();
            $info = @getimagesizefromstring($body);
            $mime = is_array($info) ? ($info['mime'] ?? '') : '';
            if ($mime === '' || strpos($mime, 'image/') !== 0 || stripos($mime, 'svg') !== false) {
                return response('Upstream content is not a raster image', 415);
            }
            return response($body, 200)
                ->header('Content-Type', $mime)
                ->header('X-Content-Type-Options', 'nosniff')
                ->header('Cache-Control', 'private, max-age=3600');
        } catch (\Exception $e) {
            return response('Error: ' . $e->getMessage(), 502);
        }
    }

    /**
     * حالة جسر بايثون وقاعدة البيانات (لا يوجد خادم FastAPI: كل الإجراءات عبر cli_bridge.py)
     */
    public function systemStatus()
    {
        $dbOnline = false;
        try {
            DB::select('SELECT 1');
            $dbOnline = true;
        } catch (\Throwable $e) {
            $dbOnline = false;
        }

        $pythonPath = PythonBridge::pythonPath();
        $pythonFound = file_exists($pythonPath) || !preg_match('/[\\\\\/]/', $pythonPath);

        return response()->json([
            'laravel_server' => 'online',
            'transport' => 'cli',
            'python_bridge' => file_exists(PythonBridge::bridgePath()) ? 'present' : 'missing',
            'python_path' => $pythonFound ? 'configured' : 'missing',
            'database' => $dbOnline ? 'online' : 'offline',
            'products_cache' => file_exists($this->automationPath('products_cache.json')) ? 'active' : 'empty'
        ]);
    }

    /**
     * «إيقاف التشغيل» بأمان، ولا يُحذف أي صف:
     * - الإدراج ما زال يقرأ الشيت (القفل 'STARTING'، لا PID بعد): يُسجل طلب إيقاف يلتزم به العامل فور بدئه.
     * - عامل حي: يُسجل طلب إيقاف ويُنتظر العامل حتى stopWaitSeconds: ينهي المنتجات الجارية ويكتب تقرير التشغيل بنفسه
     *   (exited؛ التشغيل الليلي يخرج برمز 3). لم يتوقف: يُنهى إن أكد بايثون هويته (killed) ويكتب run_control تقرير
     *   «توقف» بدلاً منه وتعود صفوفه قيد المعالجة إلى الانتظار؛ وإلا يبقى طلب الإيقاف (running).
     * - لا تشغيل: قفل قديم يُحذف والصفوف العالقة في 'processing' تعود إلى الانتظار.
     * كل صف آخر (جاهز للمراجعة، معتمد، فاشل، في الانتظار) يبقى كما هو. الرسالة العربية من run_control.
     */
    public function stopBatch()
    {
        $process = $this->pipelineProcess();
        $worker = $this->stopWorker($process);
        // run_control قبل حذف القفل: تقرير «توقف» لعامل أُنهي يُبنى من قفله
        $result = $this->runPython('run_control', ['op' => 'stop', 'worker' => $worker]);
        if (in_array($worker, ['killed', 'none'], true)) {
            $this->removeRunFiles($process);
        }
        if (($result['status'] ?? '') !== 'success') {
            return response()->json(['status' => 'failed', 'error' => $result['error'] ?? 'تعذر إيقاف التشغيل.'], 500);
        }
        return response()->json($result + ['worker' => $worker]);
    }


    /**
     * إعادة تشغيل وضم المنتجات الفاشلة لطابور المعالجة.
     * يستخدم upsert متوافق مع MariaDB داخل معاملة واحدة، ولا يحذف سجل الخطأ إلا بعد نجاح الإدراج.
     * barcodes: قائمة مفاتيح الأخطاء، أو all=true لإعادة جدولة كل الأخطاء المسجلة.
     */
    public function retryFailures(Request $request)
    {
        $barcodes = $request->input('barcodes', []);
        if (!is_array($barcodes)) {
            $barcodes = [$barcodes];
        }
        if (empty($barcodes) && $request->boolean('all')) {
            try {
                $barcodes = DB::table('product_failures')->pluck('barcode')->all();
            } catch (\Throwable $e) {
                return response()->json(['status' => 'failed', 'error' => $e->getMessage()], 500);
            }
        }
        if (empty($barcodes)) {
            return response()->json(['status' => 'failed', 'error' => 'لم يتم تحديد أي رموز أخطاء لإعادة المحاولة.'], 400);
        }

        try {
            // صفوف الشيت الخام فقط (أرقام الصفوف والهوية)؛ لا تُكتب في كاش منتجات المراجعة
            $products = ProductController::sheetRows();

            if (empty($products)) {
                return response()->json(['status' => 'failed', 'error' => 'فشل تحميل قائمة المنتجات للتأكد من أرقام الصفوف.'], 500);
            }

            $productsByBarcode = [];
            $productsBySku = [];
            foreach ($products as $p) {
                $barcode = trim($p['barcode'] ?? '');
                $altBarcode = 'ERR_' . str_replace(' ', '_', ($p['product_name'] ?? '') . '_' . ($p['brand'] ?? ''));

                if ($barcode) {
                    $productsByBarcode[$barcode] = $p;
                }
                $productsByBarcode[$altBarcode] = $p;
                $sku = trim((string) ($p['sku_key'] ?? ''));
                if ($sku !== '') {
                    $productsBySku[$sku] = $p;
                }
            }
            // منتج كل سجل فشل بـ sku_key أولاً (local_cache_db.save_product_failure): مفتاح ERR_..#<sku_key> لحجم آخر
            // بنفس الاسم والبراند لا يطابقه أي مفتاح عرض، ومفتاح العرض نفسه قد يحمله منتج آخر بنفس الاسم والبراند
            $failureSku = [];
            try {
                $failureSku = DB::table('product_failures')
                    ->whereIn('barcode', array_values(array_unique(array_map(fn ($b) => trim((string) $b), $barcodes))))
                    ->whereNotNull('sku_key')
                    ->pluck('sku_key', 'barcode')->all();
            } catch (\Throwable $e) {
                $failureSku = [];      // قبل عمود sku_key: المطابقة بمفتاح العرض كما كانت
            }

            $hasSkuKey = Schema::hasColumn('automation_queue', 'sku_key');
            $hasPayload = Schema::hasColumn('automation_queue', 'payload_json');
            $hasFailureCode = Schema::hasColumn('automation_queue', 'failure_code');
            $hasTrace = Schema::hasColumn('automation_queue', 'trace_json');
            $now = now();

            $rows = [];
            $failureKeys = [];
            foreach ($barcodes as $b) {
                $bClean = trim((string) $b);
                $sku = trim((string) ($failureSku[$bClean] ?? ''));
                $p = ($sku !== '' && isset($productsBySku[$sku])) ? $productsBySku[$sku] : null;
                if ($p === null && isset($productsByBarcode[$bClean])) {
                    $byKey = $productsByBarcode[$bClean];
                    $rowSku = trim((string) ($byKey['sku_key'] ?? ''));
                    // صف بمفتاح العرض نفسه لمنتج آخر (حجم آخر بنفس الاسم والبراند) ليس صاحب هذا السجل
                    if ($sku === '' || $rowSku === '' || $rowSku === $sku) {
                        $p = $byKey;
                    }
                }
                if ($p === null) {
                    continue;
                }
                $row = [
                    'row_number' => (int) $p['row_number'],
                    'barcode' => $p['barcode'] ?? '',
                    'product_name' => $p['product_name'] ?? '',
                    'brand' => $p['brand'] ?? '',
                    'search_query' => $p['search_query'] ?? trim(($p['brand'] ?? '') . ' ' . ($p['product_name'] ?? '')),
                    'status' => 'pending',
                    'error_message' => null,
                    'attempts' => 0,
                    'updated_at' => $now,
                ];
                if ($hasSkuKey) {
                    $row['sku_key'] = $p['sku_key'] ?? null;
                }
                if ($hasPayload) {
                    $row['payload_json'] = json_encode([
                        'name' => $p['product_name'] ?? '',
                        'name_ar' => $p['product_name_ar'] ?? '',
                        'brand' => $p['brand'] ?? '',
                        'brand_ar' => $p['brand_ar'] ?? '',
                        'barcode' => $p['barcode'] ?? '',
                        'category' => $p['category'] ?? '',
                        'sub_category' => $p['sub_category'] ?? '',
                        'origin' => $p['origin'] ?? '',
                        'size' => $p['size'] ?? '',
                    ], JSON_UNESCAPED_UNICODE);
                }
                if ($hasFailureCode) {
                    $row['failure_code'] = null;
                }
                if ($hasTrace) {
                    $row['trace_json'] = null;
                }
                // صف واحد لكل رقم صف (آخر تكرار يفوز)
                $rows[$row['row_number']] = $row;
                $failureKeys[] = $bClean;
            }

            if (!empty($rows)) {
                $rows = array_values($rows);
                $updateColumns = array_values(array_diff(array_keys($rows[0]), ['row_number']));
                DB::transaction(function () use ($rows, $updateColumns, $failureKeys) {
                    // 1. إعادة إدراج الصفوف في طابور الأتمتة (INSERT ... ON DUPLICATE KEY UPDATE)
                    DB::table('automation_queue')->upsert($rows, ['row_number'], $updateColumns);
                    // 2. مسح الخطأ من جدول الفشل فقط بعد نجاح الإدراج
                    DB::table('product_failures')->whereIn('barcode', $failureKeys)->delete();
                });
            }
            $successCount = count($rows);
            $notFound = count(array_unique(array_map(fn ($b) => trim((string) $b), $barcodes))) - count(array_unique($failureKeys));

            ProductController::forgetProductCaches();

            // إعادة المحاولة تضيف الصفوف للطابور فقط ولا تشغّل العامل: الرسالة تقول ذلك صراحة
            $notFoundText = $notFound > 0
                ? " لم يُعثر في الشيت على بعض المنتجات المحددة (العدد: {$notFound}) فبقيت في سجل الأخطاء."
                : '';
            if ($successCount === 0) {
                return response()->json(['status' => 'failed', 'requeued' => 0, 'not_found' => $notFound,
                    'error' => 'لم يُضف أي منتج إلى طابور الأتمتة.' . $notFoundText], 422);
            }
            $message = "أُضيفت المنتجات إلى طابور الأتمتة (العدد: {$successCount})، ولا تبدأ معالجتها من هنا: "
                . "شغّل التشغيل من صفحة «التشغيل» لمعالجتها. إن كان تشغيل جارٍ الآن فسيعالجها قبل أن ينتهي."
                . $notFoundText;

            return response()->json(['status' => 'success', 'requeued' => $successCount, 'not_found' => $notFound, 'message' => $message]);
        } catch (\Exception $e) {
            return response()->json(['status' => 'failed', 'error' => $e->getMessage()], 500);
        }
    }

    /**
     * معاينة أول 5 صفوف من ملف Google Sheet المستهدف
     */
    public function previewSheet(Request $request)
    {
        $result = $this->runPython('sheet-preview', $request->all());
        if (isset($result['status']) && $result['status'] === 'success') {
            return response()->json($result, 200);
        }
        return response()->json($result, 500);
    }

    /**
     * حفظ الإعدادات وتصفير كاش الشيت القديم
     */
    public function saveSheetConfig(Request $request)
    {
        ProductController::forgetProductCaches();
        $result = $this->runPython('sheet-save', $request->all());
        if (isset($result['status']) && $result['status'] === 'success') {
            return response()->json($result, 200);
        }
        return response()->json($result, 500);
    }
}
