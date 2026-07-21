<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;

class ApiController extends Controller
{
    private function getPythonPath()
    {
        $winVenv = base_path('../.venv/Scripts/python.exe');
        if (file_exists($winVenv)) {
            return $winVenv;
        }
        $linuxVenv = base_path('../.venv/bin/python');
        if (file_exists($linuxVenv)) {
            return $linuxVenv;
        }
        return env('PYTHON_PATH', 'python');
    }

    private function getBridgePath()
    {
        return env('CLI_BRIDGE_PATH', base_path('../cli_bridge.py'));
    }

    private function runPython($action, $params = [])
    {
        @set_time_limit(300);
        try {
            $routes = [
                'get_products' => 'products',
                'search' => 'search',
                'select_image' => 'select-image',
                'reject_image' => 'reject-image',
                'upload_manual_image' => 'upload-manual-image',
                'batch_status' => 'batch-status',
                'batch-status' => 'batch-status',
                'sheet-preview' => 'sheet-preview',
                'sheet-save' => 'sheet-save'
            ];
            
            $endpoint = $routes[$action] ?? $action;
            $url = "http://127.0.0.1:8001/api/{$endpoint}";
            
            if ($action === 'get_products' || $action === 'batch_status' || $action === 'batch-status') {
                $response = \Illuminate\Support\Facades\Http::timeout(600)->get($url);
            } else {
                $response = \Illuminate\Support\Facades\Http::timeout(600)->post($url, $params);
            }
            
            if ($response->successful()) {
                return $response->json();
            }
            
            \Log::warning("FastAPI request failed for action: {$action}, falling back to CLI. Status: " . $response->status());
            throw new \Exception("FastAPI returned status " . $response->status());
        } catch (\Exception $e) {
            try {
                $jsonParams = json_encode($params);
                $base64Params = base64_encode($jsonParams);
                
                $pythonPath = $this->getPythonPath();
                $bridgePath = $this->getBridgePath();
                
                // بناء الأمر والتنفيذ الفوري (التراجع للـ CLI)
                $cmd = "\"{$pythonPath}\" \"{$bridgePath}\" {$action} {$base64Params} 2>&1";
                $output = shell_exec($cmd);
                
                $pos = strrpos($output, '{"status":');
                if ($pos !== false) {
                    $output = substr($output, $pos);
                }
                
                $decoded = json_decode($output, true);
                if ($decoded === null) {
                    \Log::error("Python CLI Bridge fallback failed to decode: Raw Output: " . $output);
                    return ['status' => 'failed', 'error' => 'Invalid JSON output from Python bridge', 'raw' => $output];
                }
                if (isset($decoded['status']) && $decoded['status'] === 'failed') {
                    \Log::error("Python CLI Bridge returned failure. Raw Output: " . $output);
                }
                return $decoded;
            } catch (\Exception $subEx) {
                return ['status' => 'failed', 'error' => $e->getMessage() . ' | Fallback error: ' . $subEx->getMessage()];
            }
        }
    }

    /**
     * البحث التوافقي المتوازي لمنتج معين
     */
    public function search(Request $request)
    {
        @ini_set('max_execution_time', 600);
        set_time_limit(600);
        $result = $this->runPython('search', $request->all());
        
        if (isset($result['status']) && $result['status'] === 'success') {
            return response()->json($result, 200);
        }
        return response()->json($result, 500);
    }

    /**
     * اعتماد صورة معينة وتحديث الشيت
     */
    public function selectImage(Request $request)
    {
        @ini_set('max_execution_time', 600);
        set_time_limit(600);
        $result = $this->runPython('select_image', $request->all());
        
        if (isset($result['status']) && $result['status'] === 'success') {
            // تفريغ كاش الكتالوج ليعاد قراءته بالشيت المحدث
            \Cache::forget('products_json_v1');
            return response()->json($result, 200);
        }
        return response()->json($result, 500);
    }

    /**
     * رفض واستبعاد صورة وتسجيل التغذية الراجعة
     */
    public function rejectImage(Request $request)
    {
        @ini_set('max_execution_time', 600);
        set_time_limit(600);
        $result = $this->runPython('reject_image', $request->all());
        
        if (isset($result['status']) && $result['status'] === 'success') {
            \Cache::forget('products_json_v1');
            return response()->json($result, 200);
        }
        return response()->json($result, 500);
    }

    /**
     * رفع صورة يدوياً ومعالجتها بالكامل
     */
    public function uploadManualImage(Request $request)
    {
        @ini_set('max_execution_time', 600);
        set_time_limit(600);
        if (!$request->hasFile('file')) {
            return response()->json(['error' => 'No file uploaded'], 400);
        }

        $file = $request->file('file');
        
        // حفظ مؤقت للملف المرفوع في مجلد temp التابع للأوتوميشن ليتعامل معه البايثون
        $tempDir = 'f:\\automation\\temp';
        if (!file_exists($tempDir)) {
            mkdir($tempDir, 0777, true);
        }
        
        $safeName = 'manual_' . time() . '_' . $file->getClientOriginalName();
        $targetPath = $tempDir . DIRECTORY_SEPARATOR . $safeName;
        $file->move($tempDir, $safeName);

        $params = $request->only([
            'row_number', 'product_name', 'brand', 'barcode',
            'target_width', 'target_height', 'padding_ratio', 'bg_color', 'upscale', 'enhance'
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
            $logPath = 'f:\\automation\\temp\\pipeline.log';
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
        
        $pCache = 'f:\\automation\\products_cache.json';
        $bCache = 'f:\\automation\\brand_mappings_cache.json';
        
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
            
            if (strncasecmp(PHP_OS, 'WIN', 3) === 0) {
                // Windows background execution using a dynamically created batch file to resolve nested quote issues
                $batContent = "@echo off\r\n";
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
                $cmd = "cd \"" . $basePath . "\" && \"" . $pythonPath . "\" \"" . $scriptPath . "\" --enqueue > \"" . $logPath . "\" 2>&1 && \"" . $pythonPath . "\" -u \"" . $scriptPath . "\" --worker >> \"" . $logPath . "\" 2>&1";
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
        
        $response = [
            'is_running' => $isRunning,
            'status' => $status,
            'total' => $total,
            'current' => $processed,
            'success' => $success,
            'failed' => $failed,
            'current_product' => $currentProduct,
            'pause_requested' => $pauseRequested
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
     * جلب الصور الخارجية وتخطي حظر الـ Hotlinking
     */
    public function imageProxy(Request $request)
    {
        $url = $request->query('url');
        if (!$url) {
            return response('Missing URL', 400);
        }
        try {
            $response = \Illuminate\Support\Facades\Http::withoutVerifying()->withHeaders([
                'User-Agent' => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            ])->timeout(10)->get($url);
            
            if ($response->successful()) {
                return response($response->body(), 200)
                    ->header('Content-Type', $response->header('Content-Type') ?: 'image/jpeg');
            }
            return response('Error fetching image', $response->status());
        } catch (\Exception $e) {
            return response('Error: ' . $e->getMessage(), 500);
        }
    }

    /**
     * حالة الاتصال المباشر والملفات
     */
    public function systemStatus()
    {
        $fastapiOnline = false;
        try {
            $fp = @fsockopen('127.0.0.1', 8001, $errno, $errstr, 0.5);
            if ($fp) {
                $fastapiOnline = true;
                fclose($fp);
            }
        } catch (\Exception $ex) {}

        return response()->json([
            'laravel_server' => 'online',
            'fastapi_server' => $fastapiOnline ? 'online' : 'offline',
            'local_cache_db' => file_exists('f:/automation/local_cache.db') ? 'active' : 'empty_cleared',
            'search_cache' => file_exists('f:/automation/search_cache.json') ? 'active' : 'missing'
        ]);
    }

    public function startFlask()
    {
        return response()->json(['status' => 'success', 'message' => 'خادم FastAPI يعمل في الخلفية على منفذ 8001 تلقائياً عبر سكربت التشغيل setup_and_launch.bat.']);
    }

    public function stopFlask()
    {
        return response()->json(['status' => 'success', 'message' => 'خادم FastAPI يعمل في الخلفية على منفذ 8001 تلقائياً عبر سكربت التشغيل setup_and_launch.bat.']);
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
     * إعادة تشغيل وضم المنتجات الفاشلة لطابور المعالجة
     */
    public function retryFailures(Request $request)
    {
        $barcodes = $request->input('barcodes', []);
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

            $successCount = 0;
            foreach ($barcodes as $b) {
                $bClean = trim($b);
                if (isset($productsByBarcode[$bClean])) {
                    $p = $productsByBarcode[$bClean];
                    $rowNum = $p['row_number'];
                    $name = $p['product_name'];
                    $brand = $p['brand'];
                    $query = $p['search_query'] ?? ($brand . ' ' . $name);
                    $barcodeVal = $p['barcode'] ?? '';

                    // 1. مسح الخطأ من جدول الفشل
                    \DB::table('product_failures')->where('barcode', $bClean)->delete();

                    // 2. إعادة إدراج الصف في طابور الأتمتة
                    \DB::statement("
                        INSERT OR REPLACE INTO automation_queue (row_number, barcode, product_name, brand, search_query, status, error_message, attempts, updated_at)
                        VALUES (?, ?, ?, ?, ?, 'pending', NULL, 0, CURRENT_TIMESTAMP)
                    ", [$rowNum, $barcodeVal, $name, $brand, $query]);

                    $successCount++;
                }
            }

            \Cache::forget($cacheKey);

            return response()->json(['status' => 'success', 'message' => "تم إعادة جدولة {$successCount} منتجات بنجاح في طابور الأتمتة."]);
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

