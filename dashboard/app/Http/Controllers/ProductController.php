<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use App\Models\ResolvedProduct;
use App\Models\ProductFailure;

class ProductController extends Controller
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
                
                return json_decode($output, true) ?: ['status' => 'failed', 'error' => $output];
            } catch (\Exception $subEx) {
                return ['status' => 'failed', 'error' => $e->getMessage() . ' | Fallback error: ' . $subEx->getMessage()];
            }
        }
    }

    /**
     * عرض الصفحة الرئيسية للوحة التحكم والإحصائيات
     */
    public function index()
    {
        try {
            $successfulRuns = ResolvedProduct::count();
            $failedRuns = ProductFailure::count();

            // إحصائيات مبنية على بيانات كاش SQLite مباشرة وبسرعة فائقة
            $metrics = [
                'gemini_api_calls' => ($successfulRuns * 2) + $failedRuns,
                'cloudinary_uploads' => $successfulRuns,
                'failed_runs' => $failedRuns,
                'successful_runs' => $successfulRuns,
                'semantic_cache_savings' => round($successfulRuns * 0.3)
            ];

            $geminiCost = $metrics['gemini_api_calls'] * 0.0025;
            $photoroomCost = $metrics['successful_runs'] * 0.02;
            $cloudinaryCost = $metrics['successful_runs'] * 0.002;
            $totalCostVal = $geminiCost + $photoroomCost + $cloudinaryCost;

            $estimatedCost = number_format($totalCostVal, 4);
            $metrics['gemini_cost'] = $geminiCost;
            $metrics['photoroom_cost'] = $photoroomCost;
            $metrics['cloudinary_cost'] = $cloudinaryCost;

            // الحصول على المنتجات لتعديل أرقام الإحصائيات الشاملة
            $products = [];
            $cacheKey = 'products_json_v1';
            $cachedProducts = \Cache::get($cacheKey);

            if ($cachedProducts !== null) {
                $products = $cachedProducts;
            } else {
                $result = $this->runPython('get_products');
                if (isset($result['status']) && $result['status'] === 'success') {
                    $products = $result['products'];
                    \Cache::put($cacheKey, $products, 3600);
                }
            }

            $total = count($products);
            $linked = 0;
            $review = 0;
            $errors = $failedRuns;

            foreach ($products as $p) {
                $hasLink = !empty($p['existing_image_link']) && trim($p['existing_image_link']) !== '';
                
                // التحقق من حالة المراجعة بناءً على الكلمة المفتاحية في الرابط
                $isReview = (strpos($p['existing_image_link'] ?? '', 'needs_review:') !== false) || (!empty($p['needs_review']) && $p['needs_review']);
                
                if ($hasLink && !$isReview) {
                    $linked++;
                } elseif ($isReview) {
                    $review++;
                }
            }

            $missing = max(0, $total - $linked - $review - $errors);
            $percentage = $total > 0 ? round(($linked / $total) * 100) : 0;

            return view('dashboard.index', compact('metrics', 'estimatedCost', 'total', 'linked', 'review', 'errors', 'missing', 'percentage'));
        } catch (\Exception $e) {
            return view('dashboard.index', [
                'metrics' => ['gemini_api_calls' => 0, 'cloudinary_uploads' => 0],
                'estimatedCost' => '0.0000',
                'total' => 0, 'linked' => 0, 'review' => 0, 'errors' => 0, 'missing' => 0, 'percentage' => 0,
                'error' => 'حدث خطأ أثناء تحميل الإحصائيات: ' . $e->getMessage()
            ]);
        }
    }

    /**
     * صفحة الكتالوج والفرز والاعتماد البصري
     */
    public function catalog()
    {
        return view('dashboard.catalog');
    }

    /**
     * جلب المنتجات كـ JSON مع دمج البيانات الوصفية والحالات من SQLite
     */
    public function getProductsJson(Request $request)
    {
        try {
            $cacheKey = 'products_json_v1';
            $forceRefresh = $request->query('refresh') === 'true';
            $cachedProducts = $forceRefresh ? null : \Cache::get($cacheKey);

            if ($cachedProducts !== null) {
                return response()->json([
                    'status'   => 'success',
                    'products' => $cachedProducts,
                    'cached'   => true
                ])->header('X-Cache', 'HIT');
            }

            $result = $this->runPython('get_products');
            if (!isset($result['status']) || $result['status'] !== 'success') {
                return response()->json(['error' => 'Failed to load products from Google Sheets via CLI: ' . ($result['error'] ?? 'Unknown error')], 500);
            }

            $products = $result['products'];

            // جلب تفاصيل الكاش المحلي
            $resolved = ResolvedProduct::all()->keyBy('barcode');

            // جلب مرشحات الصور المخزنة للفرز والاعتماد البصري
            $curationCandidates = [];
            try {
                $curationCandidates = \DB::table('curation_candidates')
                    ->orderBy('id', 'asc')
                    ->get()
                    ->groupBy('row_number');
            } catch (\Exception $e) {
                // Table might not exist or be empty yet
            }

            foreach ($products as &$prod) {
                $barcode = trim($prod['barcode'] ?? '');
                $rowNum = $prod['row_number'];
                
                // دمج المرشحات البصرية المخزنة
                $hasCuration = isset($curationCandidates[$rowNum]) && count($curationCandidates[$rowNum]) > 0;
                $prod['curation_candidates'] = $hasCuration 
                    ? $curationCandidates[$rowNum]->toArray() 
                    : [];

                if ($hasCuration) {
                    $prod['needs_review'] = true;
                    $selected = $curationCandidates[$rowNum]->firstWhere('is_selected', 1) ?: $curationCandidates[$rowNum]->first();
                    $prod['needs_review_url'] = $selected ? $selected->image_url : '';
                } elseif (strpos($prod['existing_image_link'] ?? '', 'needs_review:') !== false) {
                    $prod['needs_review'] = true;
                    $prod['needs_review_url'] = str_replace('needs_review:', '', $prod['existing_image_link']);
                }

                if ($barcode && isset($resolved[$barcode])) {
                    $prod['cached_image'] = $resolved[$barcode]->cloudinary_url;
                    $prod['clip_score'] = $resolved[$barcode]->clip_score;
                    $prod['resolved_at'] = $resolved[$barcode]->resolved_at ? $resolved[$barcode]->resolved_at->toIso8601String() : null;
                }
            }

            \Cache::put($cacheKey, $products, 3600);

            return response()->json([
                'status'   => 'success',
                'products' => $products,
                'cached'   => false
            ])->header('X-Cache', 'MISS');
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }

    /**
     * صفحة سجل الأخطاء والتحذيرات للأتمتة
     */
    public function errors()
    {
        try {
            $failures = \App\Models\ProductFailure::orderBy('failed_at', 'desc')->get();
            return view('dashboard.errors', compact('failures'));
        } catch (\Exception $e) {
            return view('dashboard.errors', [
                'failures' => collect([]),
                'error' => 'فشل تحميل سجل الأخطاء: ' . $e->getMessage()
            ]);
        }
    }

    /**
     * صفحة التعلم النشط والتصحيح الذاتي للأخطاء البصرية
     */
    public function activeLearning()
    {
        try {
            $feedbackLogs = \DB::select("SELECT * FROM active_learning_feedback ORDER BY timestamp DESC");
            
            // تجميع الإحصائيات حسب البراند
            $brandStats = [];
            foreach ($feedbackLogs as $log) {
                $brand = trim($log->brand);
                if (empty($brand)) continue;
                $brandKey = strtolower($brand);
                
                if (!isset($brandStats[$brandKey])) {
                    $brandStats[$brandKey] = [
                        'brand' => $brand,
                        'total' => 0,
                        'cropping' => 0,
                        'clutter' => 0,
                        'padding_ratio' => '0.85 (الافتراضي)',
                        'clutter_check' => 'عادي',
                        'cropping_alert' => false,
                        'clutter_alert' => false
                    ];
                }
                
                $brandStats[$brandKey]['total']++;
                
                $reasons = [];
                try {
                    $reasons = json_decode($log->rejection_reasons, true) ?: [];
                } catch (\Exception $ex) {}
                
                foreach ($reasons as $reason) {
                    $reasonLower = strtolower($reason);
                    if (strpos($reasonLower, 'cropping') !== false || strpos($reasonLower, 'margins') !== false) {
                        $brandStats[$brandKey]['cropping']++;
                    }
                    if (strpos($reasonLower, 'clutter') !== false || strpos($reasonLower, 'background') !== false) {
                        $brandStats[$brandKey]['clutter']++;
                    }
                }
            }
            
            // تطبيق قواعد التصحيح الذاتي ومزامنتها مع منطق البايثون
            foreach ($brandStats as $key => &$stats) {
                if ($stats['cropping'] >= 4) {
                    $stats['padding_ratio'] = '0.70 (هامش أمان واسع 30%)';
                    $stats['cropping_alert'] = true;
                } elseif ($stats['cropping'] >= 2) {
                    $stats['padding_ratio'] = '0.75 (هامش أمان متناسق 25%)';
                    $stats['cropping_alert'] = true;
                }
                
                if ($stats['clutter'] >= 2) {
                    $stats['clutter_check'] = 'صارم (فحص تداخل الخلفية مفعل)';
                    $stats['clutter_alert'] = true;
                }
            }
            
            return view('dashboard.active_learning', compact('feedbackLogs', 'brandStats'));
        } catch (\Exception $e) {
            return view('dashboard.active_learning', [
                'feedbackLogs' => [],
                'brandStats' => [],
                'error' => 'فشل تحميل بيانات التعلم النشط: ' . $e->getMessage()
            ]);
        }
    }

    /**
     * صفحة معرض المنتجات الغني وتصفح الكتالوج بالبيانات الوصفية للذكاء الاصطناعي
     */
    public function richCatalog()
    {
        return view('dashboard.rich_catalog');
    }

    /**
     * صفحة التحكم والأتمتة الجماعية
     */
    public function batchAutomation()
    {
        return view('dashboard.batch_automation');
    }

    /**
     * جلب المنتجات المكتملة ذات البيانات الوصفية كـ JSON
     */
    public function getRichProductsJson()
    {
        try {
            $resolved = \App\Models\ResolvedProduct::orderBy('resolved_at', 'desc')->get();
            return response()->json([
                'status' => 'success',
                'products' => $resolved
            ]);
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }
}
