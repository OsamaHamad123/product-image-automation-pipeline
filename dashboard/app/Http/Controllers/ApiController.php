<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
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
     * اعتماد صورة معينة وتحديث الشيت
     */
    public function selectImage(Request $request)
    {
        $result = $this->runPython('select_image', $request->all());

        if (($result['status'] ?? '') === 'success') {
            // تفريغ كاش الكتالوج ليعاد قراءته بالشيت المحدث
            \Cache::forget('products_json_v1');
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
            \Cache::forget('products_json_v1');
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
            'target_width', 'target_height', 'enhance'
        ]);
        $params['file_path'] = $targetPath;

        $result = $this->runPython('upload_manual_image', $params);
        
        // التأكد من مسح الملف المؤقت في حال عدم مسحه بالبايثون
        if (file_exists($targetPath)) {
            @unlink($targetPath);
        }

        if (isset($result['status']) && $result['status'] === 'success') {
            \Cache::forget('products_json_v1');
            return response()->json($result, 200);
        }
        return response()->json($result, 500);
    }

    /**
     * قراءة سجلات التشغيل المباشر من ملف الـ Log لـ main.py
     */
    public function logs()
    {
        try {
            $logPath = $this->automationPath('temp/pipeline.log');
            if (file_exists($logPath)) {
                $content = file($logPath);
                // جلب آخر 100 سطر لتوفير الأداء
                $lines = array_slice($content, -100);
                $lines = array_map('trim', $lines);
                return response()->json(['logs' => $lines])->header('Cache-Control', 'no-store');
            }
            return response()->json(['logs' => []])->header('Cache-Control', 'no-store');
        } catch (\Exception $e) {
            return response()->json(['logs' => []])->header('Cache-Control', 'no-store');
        }
    }

    public function clearProductsCache()
    {
        \Cache::forget('products_json_v1');
        
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
            if (file_exists($lockFile)) {
                $lockContent = trim(file_get_contents($lockFile));
                $isRunning = false;
                if ($lockContent === 'STARTING') {
                    $fileAge = time() - filemtime($lockFile);
                    if ($fileAge < 300) {
                        $isRunning = true;
                    }
                } elseif (!empty($lockContent) && is_numeric($lockContent)) {
                    $pid = $lockContent;
                    if (strncasecmp(PHP_OS, 'WIN', 3) === 0) {
                        $output = shell_exec("tasklist /FI \"PID eq {$pid}\" 2>&1");
                        if (strpos($output, $pid) !== false && strpos(strtolower($output), 'python') !== false) {
                            $isRunning = true;
                        }
                    } else {
                        if (function_exists('posix_kill')) {
                            $isRunning = @posix_kill($pid, 0);
                        } else {
                            $output = shell_exec("ps -p {$pid} 2>&1");
                            if (strpos($output, $pid) !== false) {
                                $isRunning = true;
                            }
                        }
                    }
                }
                
                if ($isRunning) {
                    return response()->json(['status' => 'failed', 'error' => 'عملية الأتمتة قيد التشغيل بالفعل حالياً.'], 400);
                }
            }
            
            $configData = $request->all();
            file_put_contents($tempDir . DIRECTORY_SEPARATOR . 'run_config.json', json_encode($configData));
            
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
                $batContent .= "    \"" . $pythonPath . "\" -u \"" . $scriptPath . "\" --worker >> \"" . $logPath . "\" 2>&1\r\n";
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
                $cmd = "cd \"" . $basePath . "\" && export PYTHONUTF8=1 PYTHONIOENCODING=utf-8 && \"" . $pythonPath . "\" \"" . $scriptPath . "\" --enqueue > \"" . $logPath . "\" 2>&1 && \"" . $pythonPath . "\" -u \"" . $scriptPath . "\" --worker >> \"" . $logPath . "\" 2>&1";
                $linuxCmd = "nohup sh -c " . escapeshellarg($cmd) . " > /dev/null 2>&1 &";
                shell_exec($linuxCmd);
            }
            
            return response()->json(['status' => 'success', 'message' => 'Full automation queue worker started in background.']);
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }

    /**
     * مراقبة حالة الأتمتة في الخلفية باستخدام ملف الـ Lock للعملية (PID Lock)
     */
    public function batchStatus()
    {
        $status = 'idle';
        $total = 0;
        $processed = 0;
        $success = 0;
        $failed = 0;
        $currentProduct = "";
        
        $pauseRequested = 0;
        try {
            $stateRow = \DB::select("SELECT * FROM automation_state WHERE `key` = 'active_session' LIMIT 1");
            if (!empty($stateRow)) {
                $status = $stateRow[0]->status;
                $total = $stateRow[0]->total_items;
                $processed = $stateRow[0]->processed_items;
                $success = $stateRow[0]->success_count;
                $failed = $stateRow[0]->failed_count;
                $currentProduct = $stateRow[0]->current_product_name;
                $pauseRequested = $stateRow[0]->pause_requested;
            }
        } catch (\Exception $e) {
            // Table not loaded yet
        }
        
        $basePath = base_path('..');
        $lockFile = $basePath . DIRECTORY_SEPARATOR . 'temp' . DIRECTORY_SEPARATOR . 'pipeline.lock';
        $isRunning = false;
        if (file_exists($lockFile)) {
            $lockContent = trim(file_get_contents($lockFile));
            if ($lockContent === 'STARTING') {
                $fileAge = time() - filemtime($lockFile);
                if ($fileAge < 300) { // Keep as running during starting phase (up to 5 mins)
                    $isRunning = true;
                }
            } elseif (!empty($lockContent) && is_numeric($lockContent)) {
                $pid = $lockContent;
                if (strncasecmp(PHP_OS, 'WIN', 3) === 0) {
                    $output = shell_exec("tasklist /FI \"PID eq {$pid}\" 2>&1");
                    if (strpos($output, $pid) !== false && strpos(strtolower($output), 'python') !== false) {
                        $isRunning = true;
                    }
                } else {
                    if (function_exists('posix_kill')) {
                        $isRunning = @posix_kill($pid, 0);
                    } else {
                        $output = shell_exec("ps -p {$pid} 2>&1");
                        if (strpos($output, $pid) !== false) {
                            $isRunning = true;
                        }
                    }
                }
            }
        }
        
        // Self-healing heartbeat: If status says pre_caching but task is not running,
        // reset database to idle if it has been inactive for more than 45 seconds or if the lock file is missing.
        if (!$isRunning && ($status === 'pre_caching' || $status === 'running')) {
            $lastUpdated = isset($stateRow[0]->updated_at) ? strtotime($stateRow[0]->updated_at . ' UTC') : time();
            $diff = time() - $lastUpdated;
            if ($diff > 45 || !file_exists($lockFile)) {
                try {
                    \DB::update("UPDATE automation_state SET status = 'idle', total_items = 0, processed_items = 0, success_count = 0, failed_count = 0, current_product_name = '', pause_requested = 0 WHERE `key` = 'active_session'");
                    $status = 'idle';
                    $total = 0;
                    $processed = 0;
                    $success = 0;
                    $failed = 0;
                    $currentProduct = "";
                    $pauseRequested = 0;
                } catch (\Exception $e) {
                    // Ignore
                }
            }
        }
        
        $counters = QueueStats::counters();

        $response = [
            'is_running' => $isRunning,
            'status' => $status,
            'total' => $total,
            'current' => $processed,
            'success' => $success,
            'failed' => $failed,
            'current_product' => $currentProduct,
            'pause_requested' => $pauseRequested,
            // عدادات حقيقية من جدول automation_queue: حسب الحالة وحسب رمز الفشل
            'queue' => $counters['by_status'],
            'ready_for_review' => $counters['by_status']['ready_for_review'] ?? 0,
            'approved' => $counters['by_status']['completed'] ?? 0,
            'failed_by_code' => $counters['by_failure_code'],
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
     * إعادة تعيين حالة الأتمتة قسرياً للتخلص من الحالات المعلقة
     */
    public function resetBatch()
    {
        try {
            $basePath = base_path('..');
            $lockFile = $basePath . DIRECTORY_SEPARATOR . 'temp' . DIRECTORY_SEPARATOR . 'pipeline.lock';
            if (file_exists($lockFile)) {
                @unlink($lockFile);
            }
            
            // Clear Laravel cache
            \Cache::forget('products_json_v1');
            
            // Clear python disk cache files
            $pCache = $basePath . DIRECTORY_SEPARATOR . 'products_cache.json';
            $bCache = $basePath . DIRECTORY_SEPARATOR . 'brand_mappings_cache.json';
            if (file_exists($pCache)) {
                @unlink($pCache);
            }
            if (file_exists($bCache)) {
                @unlink($bCache);
            }
            
            // Clear database tables and reset automation state
            \DB::delete("DELETE FROM automation_queue");
            \DB::delete("DELETE FROM curation_candidates");
            \DB::update("UPDATE automation_state SET status = 'idle', total_items = 0, processed_items = 0, success_count = 0, failed_count = 0, current_product_name = '', pause_requested = 0 WHERE `key` = 'active_session'");
            
            return response()->json(['status' => 'success', 'message' => 'Automation state reset successfully.']);
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
     * إيقاف عملية الأتمتة الكلية بالخلفية فورياً
     */
    public function stopBatch()
    {
        $basePath = base_path('..');
        $lockFile = $basePath . DIRECTORY_SEPARATOR . 'temp' . DIRECTORY_SEPARATOR . 'pipeline.lock';
        $progressFile = $basePath . DIRECTORY_SEPARATOR . 'temp' . DIRECTORY_SEPARATOR . 'batch_progress.json';

        try {
            \DB::statement("DELETE FROM automation_queue");
            \DB::update("UPDATE automation_state SET status = 'idle', total_items = 0, processed_items = 0, success_count = 0, failed_count = 0, current_product_name = '', pause_requested = 0 WHERE `key` = 'active_session'");
        } catch (\Exception $e) {}

        if (file_exists($lockFile)) {
            $pid = trim(file_get_contents($lockFile));
            if (!empty($pid) && is_numeric($pid)) {
                if (strncasecmp(PHP_OS, 'WIN', 3) === 0) {
                    shell_exec("taskkill /F /PID {$pid} 2>&1");
                } else {
                    shell_exec("kill -9 {$pid} 2>&1");
                }
                @unlink($lockFile);
                if (file_exists($progressFile)) {
                    @unlink($progressFile);
                }
                return response()->json(['status' => 'success', 'message' => 'Batch automation process terminated.']);
            }
        }
        return response()->json(['status' => 'failed', 'error' => 'No active batch process found.']);
    }

    /**
     * إعادة تعيين التعلم النشط وحذف سجلات التغذية الراجعة
     */
    public function resetActiveLearning(Request $request)
    {
        try {
            $brand = $request->input('brand');
            
            if ($brand) {
                // حذف سجلات براند محدد
                \DB::delete("DELETE FROM active_learning_feedback WHERE LOWER(brand) = ?", [strtolower(trim($brand))]);
            } else {
                // حذف كافة سجلات التعلم النشط
                \DB::delete("DELETE FROM active_learning_feedback");
            }
            
            \Cache::forget('products_json_v1');
            return response()->json(['status' => 'success', 'message' => 'Active learning feedback reset successfully.']);
        } catch (\Exception $e) {
            return response()->json(['status' => 'failed', 'error' => $e->getMessage()], 500);
        }
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
            $cacheKey = 'products_json_v1';
            $products = \Cache::get($cacheKey);
            if (!$products) {
                $result = $this->runPython('get_products');
                if (isset($result['status']) && $result['status'] === 'success') {
                    $products = $result['products'];
                    \Cache::put($cacheKey, $products, 3600);
                }
            }

            if (empty($products)) {
                return response()->json(['status' => 'failed', 'error' => 'فشل تحميل قائمة المنتجات للتأكد من أرقام الصفوف.'], 500);
            }

            $productsByBarcode = [];
            foreach ($products as $p) {
                $barcode = trim($p['barcode'] ?? '');
                $altBarcode = 'ERR_' . str_replace(' ', '_', ($p['product_name'] ?? '') . '_' . ($p['brand'] ?? ''));
                
                if ($barcode) {
                    $productsByBarcode[$barcode] = $p;
                }
                $productsByBarcode[$altBarcode] = $p;
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
                if (!isset($productsByBarcode[$bClean])) {
                    continue;
                }
                $p = $productsByBarcode[$bClean];
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

            \Cache::forget($cacheKey);

            return response()->json(['status' => 'success', 'requeued' => $successCount, 'message' => "تم إعادة جدولة {$successCount} منتجات بنجاح في طابور الأتمتة."]);
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
        \Cache::forget('products_json_v1');
        $result = $this->runPython('sheet-save', $request->all());
        if (isset($result['status']) && $result['status'] === 'success') {
            return response()->json($result, 200);
        }
        return response()->json($result, 500);
    }
}
