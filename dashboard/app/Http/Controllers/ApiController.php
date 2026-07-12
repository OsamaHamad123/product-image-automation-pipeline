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
            
            // التأكد من تهيئة مجلد temp
            $tempDir = 'f:\\automation\\temp';
            if (!file_exists($tempDir)) {
                mkdir($tempDir, 0777, true);
            }
            
            // حفظ خيارات التشغيل الجماعي لتتخطى إعدادات بايثون الافتراضية
            $configData = $request->all();
            file_put_contents($tempDir . DIRECTORY_SEPARATOR . 'run_config.json', json_encode($configData));
            
            // مسح سجل اللوغ القديم لبدء جلسة نظيفة
            if (file_exists($logPath)) {
                @unlink($logPath);
            }

            // تشغيل بايثون غير متزامن بالخلفية وإعادة توجيه المخرجات لملف Log
            $cmd = "start /B \"\" \"{$this->pythonPath}\" -u \"{$scriptPath}\" > \"{$logPath}\" 2>&1";
            pclose(popen($cmd, "r"));
            
            return response()->json(['status' => 'success', 'message' => 'Full automation pipeline started in background.']);
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
            if (!empty($pid) && is_numeric($pid)) {
                $output = shell_exec("tasklist /FI \"PID eq {$pid}\" 2>&1");
                if (strpos($output, $pid) !== false) {
                    $isRunning = true;
                } else {
                    @unlink($lockFile);
                }
            }
        }
        
        return response()->json(['is_running' => $isRunning]);
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
        return response()->json([
            'laravel_server' => 'online',
            'flask_server' => 'integrated_cli',
            'local_cache_db' => file_exists('f:/automation/local_cache.db') ? 'active' : 'empty_cleared',
            'search_cache' => file_exists('f:/automation/search_cache.json') ? 'active' : 'missing'
        ]);
    }

    public function startFlask()
    {
        return response()->json(['status' => 'success', 'message' => 'Integrated CLI Mode active. No Flask server required.']);
    }

    public function stopFlask()
    {
        return response()->json(['status' => 'success', 'message' => 'Integrated CLI Mode active. No Flask server required.']);
    }
}
