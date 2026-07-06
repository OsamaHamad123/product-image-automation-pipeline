<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;
use App\Models\ResolvedProduct;
use App\Models\ProductFailure;

class ProductController extends Controller
{
    private $flaskUrl = 'http://127.0.0.1:5000';

    /**
     * عرض الصفحة الرئيسية للوحة التحكم والإحصائيات
     */
    public function index()
    {
        try {
            // جلب إحصائيات الاستهلاك من الفلاسك
            $metricsResponse = Http::get("{$this->flaskUrl}/api/metrics");
            $metrics = $metricsResponse->successful() ? $metricsResponse->json() : [
                'gemini_api_calls' => 0,
                'cloudinary_uploads' => 0,
                'failed_runs' => 0,
                'successful_runs' => 0,
                'semantic_cache_savings' => 0
            ];

            // حساب التكلفة التقديرية للـ Gemini
            $estimatedCost = number_format($metrics['gemini_api_calls'] * 0.00015, 4);

            // جلب المنتجات لحساب الإحصائيات
            $productsResponse = Http::get("{$this->flaskUrl}/api/products");
            $products = $productsResponse->successful() ? $productsResponse->json()['products'] : [];

            $total = count($products);
            $linked = 0;
            $review = 0;
            $errors = ProductFailure::count();

            foreach ($products as $p) {
                $hasLink = !empty($p['existing_image_link']) && trim($p['existing_image_link']) !== '';
                $isReview = !empty($p['needs_review']) && $p['needs_review'];
                
                if ($hasLink) {
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
                'error' => 'تعذر الاتصال بـ Flask API: ' . $e->getMessage()
            ]);
        }
    }

    /**
     * صفحة الكتالوج والفرز والاعتماد البصري
     */
    public function catalog()
    {
        // عرض واجهة الفرز والاعتماد البصري
        return view('dashboard.catalog');
    }

    /**
     * جلب المنتجات كـ JSON مع دمج البيانات الوصفية والحالات من SQLite
     */
    public function getProductsJson()
    {
        try {
            $response = Http::get("{$this->flaskUrl}/api/products");
            if (!$response->successful()) {
                return response()->json(['error' => 'Failed to load products from Flask'], 500);
            }

            $products = $response->json()['products'];

            // جلب الأخطاء والمدخلات المعتمدة من SQLite
            $failures = ProductFailure::all()->keyBy('barcode');
            $resolved = ResolvedProduct::all()->keyBy('barcode');

            foreach ($products as &$prod) {
                $barcode = trim($prod['barcode'] ?? '');
                $altBarcode = str_replace(' ', '_', 'ERR_' . ($prod['product_name'] ?? '') . '_' . ($prod['brand'] ?? ''));

                // دمج الأخطاء
                if ($barcode && isset($failures[$barcode])) {
                    $prod['has_error'] = true;
                    $prod['error_message'] = $failures[$barcode]->error_message;
                } elseif (isset($failures[$altBarcode])) {
                    $prod['has_error'] = true;
                    $prod['error_message'] = $failures[$altBarcode]->error_message;
                } else {
                    $prod['has_error'] = false;
                    $prod['error_message'] = '';
                }

                // دمج تفاصيل الكاش
                if ($barcode && isset($resolved[$barcode])) {
                    $prod['cached_image'] = $resolved[$barcode]->cloudinary_url;
                }
            }

            return response()->json([
                'status' => 'success',
                'products' => $products
            ]);
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }
}
