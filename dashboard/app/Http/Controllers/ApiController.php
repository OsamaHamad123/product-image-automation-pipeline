<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;

class ApiController extends Controller
{
    private function getPythonPath()
    {
        $localVenv = base_path('../.venv/Scripts/python.exe');
        if (file_exists($localVenv)) {
            return $localVenv;
        }
        return env('PYTHON_PATH', 'python');
    }

    private function getBridgePath()
    {
        return env('CLI_BRIDGE_PATH', base_path('../cli_bridge.py'));
    }

    private function runPython($action, $params = [])
    {
        try {
            $routes = [
                'get_products' => 'products',
                'search' => 'search',
                'select_image' => 'select-image',
                'reject_image' => 'reject-image',
                'upload_manual_image' => 'upload-manual-image',
                'batch_status' => 'batch-status',
                'batch-status' => 'batch-status'
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
            $scriptPath = 'f:\\automation\\main.py';
            $logPath = 'f:\\automation\\temp\\pipeline.log';
            $lockFile = 'f:\\automation\\temp\\pipeline.lock';
            
            $tempDir = 'f:\\automation\\temp';
            if (!file_exists($tempDir)) {
                mkdir($tempDir, 0777, true);
            }
            
            $configData = $request->all();
            file_put_contents($tempDir . DIRECTORY_SEPARATOR . 'run_config.json', json_encode($configData));
            
            file_put_contents($lockFile, 'STARTING');

            if (file_exists($logPath)) {
                @unlink($logPath);
            }

            $pythonPath = $this->getPythonPath();
            
            // تشغيل تهيئة الطابور مع ضمان المسار الرئيسي للمشروع
            $enqueueCmd = "cd /d f:\\automation && \"{$pythonPath}\" \"{$scriptPath}\" --enqueue 2>&1";
            shell_exec($enqueueCmd);

            // تشغيل بايثون بالخلفية كـ Worker مفصول تماماً لتفادي قتل العملية بعد انتهاء الطلب
            $cmd = "cmd /c cd /d f:\\automation && \"{$pythonPath}\" -u \"{$scriptPath}\" --worker > \"{$logPath}\" 2>&1";
            
            try {
                $WshShell = new \COM("WScript.Shell");
                $WshShell->Run($cmd, 0, false);
            } catch (\Exception $ex) {
                // تراجع تشغيلي في حال عدم تفعيل COM
                $popenCmd = "start /B \"\" cmd /c cd /d f:\\automation && \"{$pythonPath}\" -u \"{$scriptPath}\" --worker > \"{$logPath}\" 2>&1";
                pclose(popen($popenCmd, "r"));
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
        $lockFile = 'f:\\automation\\temp\\pipeline.lock';
        $isRunning = false;
        
        if (file_exists($lockFile)) {
            $pid = trim(file_get_contents($lockFile));
            if ($pid === 'STARTING') {
                // تصفية أوتوماتيكية للأقفال العالقة لأكثر من 30 ثانية
                if (time() - filemtime($lockFile) > 30) {
                    @unlink($lockFile);
                    $isRunning = false;
                } else {
                    $isRunning = true;
                }
            } elseif (!empty($pid) && is_numeric($pid)) {
                $output = shell_exec("tasklist /FI \"PID eq {$pid}\" 2>&1");
                if (strpos($output, $pid) !== false && strpos(strtolower($output), 'python') !== false) {
                    $isRunning = true;
                } else {
                    @unlink($lockFile);
                }
            }
        }
        
        $total = 0;
        $pending = 0;
        $processing = 0;
        $completed = 0;
        $failed = 0;
        $currentProduct = "";
        
        try {
            $rows = \DB::select("SELECT status, COUNT(*) as cnt FROM automation_queue GROUP BY status");
            foreach ($rows as $row) {
                $status = $row->status;
                $cnt = $row->cnt;
                if ($status === 'pending') $pending = $cnt;
                elseif ($status === 'processing') $processing = $cnt;
                elseif ($status === 'completed') $completed = $cnt;
                elseif ($status === 'failed') $failed = $cnt;
                $total += $cnt;
            }
            
            $activeRow = \DB::select("SELECT product_name FROM automation_queue WHERE status = 'processing' LIMIT 1");
            if (!empty($activeRow)) {
                $currentProduct = $activeRow[0]->product_name;
            }
        } catch (\Exception $e) {
            // Queue table not created yet or database locked
        }
        
        $response = [
            'is_running' => $isRunning,
            'total' => $total,
            'current' => $completed + $failed + ($processing > 0 ? 1 : 0),
            'success' => $completed,
            'failed' => $failed,
            'current_product' => $currentProduct
        ];
        
        return response()->json($response)->header('Cache-Control', 'no-store');
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
            $response = \Illuminate\Support\Facades\Http::withHeaders([
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
        $lockFile = 'f:\\automation\\temp\\pipeline.lock';
        $progressFile = 'f:\\automation\\temp\\batch_progress.json';
        
        try {
            \DB::statement("DELETE FROM automation_queue");
        } catch (\Exception $e) {}

        if (file_exists($lockFile)) {
            $pid = trim(file_get_contents($lockFile));
            if (!empty($pid) && is_numeric($pid)) {
                shell_exec("taskkill /F /PID {$pid} 2>&1");
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
}
