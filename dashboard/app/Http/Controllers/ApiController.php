<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;

class ApiController extends Controller
{
    private $pythonPath = 'C:\Users\OsamaHamad\AppData\Local\Programs\Python\Python314\python.exe';
    private $bridgePath = 'f:\automation\cli_bridge.py';

    /**
     * تشغيل سكربت Python ببارامترات مشفرة كـ Base64 لضمان الحماية
     */
    private function runPython($action, $params = [])
    {
        try {
            $jsonParams = json_encode($params);
            $base64Params = base64_encode($jsonParams);
            
            // بناء الأمر والتنفيذ الفوري
            $cmd = "\"{$this->pythonPath}\" \"{$this->bridgePath}\" {$action} {$base64Params} 2>&1";
            $output = shell_exec($cmd);
            
            $decoded = json_decode($output, true);
            if ($decoded === null) {
                \Log::error("Python CLI Bridge failed to decode: Raw Output: " . $output);
                return ['status' => 'failed', 'error' => 'Invalid JSON output from Python bridge', 'raw' => $output];
            }
            return $decoded;
        } catch (\Exception $e) {
            return ['status' => 'failed', 'error' => $e->getMessage()];
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
            'target_width', 'target_height', 'padding_ratio', 'bg_color', 'upscale'
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

    /**
     * مسح الكاش
     */
    public function clearProductsCache()
    {
        \Cache::forget('products_json_v1');
        return response()->json(['status' => 'success', 'message' => 'Products cache cleared']);
    }

    /**
     * تشغيل الأتمتة الكلية بالخلفية بدون خادم Flask
     */
    public function runAll()
    {
        try {
            $scriptPath = 'f:\\automation\\main.py';
            $logPath = 'f:\\automation\\temp\\pipeline.log';
            
            // التأكد من تهيئة مجلد temp
            $tempDir = 'f:\\automation\\temp';
            if (!file_exists($tempDir)) {
                mkdir($tempDir, 0777, true);
            }
            
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
