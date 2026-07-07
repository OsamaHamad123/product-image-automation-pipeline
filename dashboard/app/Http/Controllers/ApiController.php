<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;

class ApiController extends Controller
{
    private $flaskUrl = 'http://127.0.0.1:5000';

    /**
     * بروكسي لبدء البحث البصري التلقائي لمنتج
     */
    public function search(Request $request)
    {
        try {
            set_time_limit(120);
            $response = Http::timeout(120)->post("{$this->flaskUrl}/api/search", $request->all());
            return response()->json($response->json(), $response->status());
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }

    /**
     * بروكسي لاعتماد صورة محددة يدوياً
     */
    public function selectImage(Request $request)
    {
        try {
            set_time_limit(120);
            $response = Http::timeout(120)->post("{$this->flaskUrl}/api/select_image", $request->all());
            return response()->json($response->json(), $response->status());
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }

    /**
     * بروكسي لرفع ملف صورة يدوياً ومعالجته تلقائياً بالذكاء الاصطناعي
     */
    public function uploadManualImage(Request $request)
    {
        try {
            set_time_limit(120);
            if (!$request->hasFile('file')) {
                return response()->json(['error' => 'No file uploaded'], 400);
            }

            $file = $request->file('file');

            // تجميع جميع حقول النموذج بما فيها معايير التحجيم ولون الخلفية
            $formFields = $request->only([
                'row_number', 'product_name', 'brand', 'barcode',
                'target_width', 'target_height', 'padding_ratio', 'bg_color', 'upscale'
            ]);

            // إرسال الطلب لـ Flask مع إرفاق الملف كـ Multipart
            $response = Http::timeout(120)->attach(
                'file',
                file_get_contents($file->getPathname()),
                $file->getClientOriginalName()
            )->post("{$this->flaskUrl}/api/upload_manual_image", $formFields);

            return response()->json($response->json(), $response->status());
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }

    /**
     * بروكسي لجلب السجلات الحية لكونسول التشغيل
     */
    public function logs()
    {
        try {
            $response = Http::timeout(3)->get("{$this->flaskUrl}/api/logs");
            return response()->json($response->json(), $response->status())
                ->header('Cache-Control', 'no-store');
        } catch (\Exception $e) {
            // إرجاع مصفوفة فارغة بدلاً من خطأ كامل عند تعطل Flask
            return response()->json(['logs' => []], 200)
                ->header('Cache-Control', 'no-store');
        }
    }

    /**
     * مسح كاش قائمة المنتجات لإجبار التحديث من Google Sheets
     */
    public function clearProductsCache()
    {
        \Cache::forget('products_json_v1');
        return response()->json(['status' => 'success', 'message' => 'Products cache cleared']);
    }

    /**
     * بروكسي لتشغيل الأتمتة الكلية في الخلفية
     */
    public function runAll()
    {
        try {
            $response = Http::post("{$this->flaskUrl}/api/run_all");
            return response()->json($response->json(), $response->status());
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }

    /**
     * بروكسي لجلب حالة تشغيل الأتمتة في الخلفية
     */
    public function batchStatus()
    {
        try {
            $response = Http::get("{$this->flaskUrl}/api/batch_status");
            return response()->json($response->json(), $response->status());
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }

    /**
     * بروكسي لجلب الصور الخارجية وتخطي حظر الـ Hotlinking
     */
    public function imageProxy(Request $request)
    {
        $url = $request->query('url');
        if (!$url) {
            return response('Missing URL', 400);
        }
        try {
            set_time_limit(60);
            $response = Http::withHeaders([
                'User-Agent' => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            ])->timeout(10)->get($url);
            
            if ($response->successful()) {
                return response($response->body(), 200)
                    ->header('Content-Type', $response->header('Content-Type') ?: 'image/jpeg');
            } else {
                return response('Error fetching image', $response->status());
            }
        } catch (\Exception $e) {
            return response('Error: ' . $e->getMessage(), 500);
        }
    }

    /**
     * التحقق من حالة المنفذ 5000 (Flask) والمنفذ 8000 (Laravel) محلياً
     */
    public function systemStatus()
    {
        $flaskOnline = false;
        try {
            $response = Http::timeout(2)->get("{$this->flaskUrl}/");
            if ($response->successful()) {
                $flaskOnline = true;
            }
        } catch (\Exception $e) {
            $flaskOnline = false;
        }

        return response()->json([
            'laravel_server' => 'online',
            'flask_server' => $flaskOnline ? 'online' : 'offline',
            'local_cache_db' => file_exists(base_path('../local_cache.db')) ? 'active' : 'empty_cleared',
            'search_cache' => file_exists(base_path('../search_cache.json')) ? 'active' : 'missing'
        ]);
    }

    /**
     * تشغيل خادم Flask بالخلفية
     */
    public function startFlask()
    {
        try {
            $pythonPath = 'C:\\Users\\OsamaHamad\\AppData\\Local\\Programs\\Python\\Python314\\python.exe';
            $scriptPath = base_path('../app.py');

            if (!file_exists($pythonPath)) {
                $pythonPath = 'python';
            }

            pclose(popen("start /B {$pythonPath} {$scriptPath} > nul 2>&1", "r"));

            return response()->json(['status' => 'success', 'message' => 'Flask backend startup command sent successfully.']);
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }

    /**
     * إيقاف خادم Flask عبر إنهاء العملية المفتوحة على المنفذ 5000
     */
    public function stopFlask()
    {
        try {
            $output = [];
            exec("netstat -ano | findstr :5000", $output);
            
            $killed = 0;
            foreach ($output as $line) {
                $parts = preg_split('/\s+/', trim($line));
                $pid = end($parts);
                if (is_numeric($pid) && $pid > 0) {
                    exec("taskkill /F /PID {$pid}");
                    $killed++;
                }
            }

            return response()->json([
                'status' => 'success',
                'message' => "Flask stopped. Terminated {$killed} active process connection(s)."
            ]);
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }
}
