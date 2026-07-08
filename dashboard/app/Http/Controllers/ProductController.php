<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use App\Models\ResolvedProduct;
use App\Models\ProductFailure;

class ProductController extends Controller
{
    private $pythonPath = 'C:\Users\OsamaHamad\AppData\Local\Programs\Python\Python314\python.exe';
    private $bridgePath = 'f:\automation\cli_bridge.py';

    private function runPython($action, $params = [])
    {
        try {
            $jsonParams = json_encode($params);
            $base64Params = base64_encode($jsonParams);
            
            $cmd = "\"{$this->pythonPath}\" \"{$this->bridgePath}\" {$action} {$base64Params} 2>&1";
            $output = shell_exec($cmd);
            
            // Extract JSON from output if there are print statement logs before it
            $pos = strrpos($output, '{"status":');
            if ($pos !== false) {
                $output = substr($output, $pos);
            }
            
            return json_decode($output, true);
        } catch (\Exception $e) {
            return ['status' => 'failed', 'error' => $e->getMessage()];
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

            $estimatedCost = number_format($metrics['gemini_api_calls'] * 0.00015, 4);

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
    public function getProductsJson()
    {
        try {
            $cacheKey = 'products_json_v1';
            $cachedProducts = \Cache::get($cacheKey);

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

            foreach ($products as &$prod) {
                $barcode = trim($prod['barcode'] ?? '');
                
                // التحقق مما إذا كان الرابط يطلب المراجعة لتأكيدها للواجهة الأمامية
                if (strpos($prod['existing_image_link'] ?? '', 'needs_review:') !== false) {
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
}
