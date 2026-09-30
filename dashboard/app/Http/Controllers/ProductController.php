<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;
use App\Models\ResolvedProduct;
use App\Models\ProductFailure;
use App\Services\PythonBridge;
use App\Services\QueueStats;
use App\Services\CandidateMatcher;

class ProductController extends Controller
{
    /** مفاتيح الإعدادات السرية: لا تُطبع أبداً في الصفحة، وتُعرض آخر 4 أحرف فقط. */
    private const SECRET_SETTING_KEYS = [
        'photoroom_api_key', 'gemini_api_key', 'cloudinary_api_key', 'cloudinary_api_secret',
        'google_search_api_key', 'serper_api_key', 'proxy_url'
    ];

    private const TEXT_SETTING_KEYS = [
        'gemini_model', 'cloudinary_cloud_name', 'google_search_cx',
        'search_engine', 'auto_publish_brands'
    ];

    private const CHECKBOX_SETTING_KEYS = [
        'strict_brand_match', 'auto_publish_enabled',
        // مفاتيح محرك البحث القديم v1 فقط (للتراجع المؤقت)
        'enable_gemini_pre_validation', 'filter_competitors', 'bypass_white_background_check'
    ];

    /** نماذج Gemini المدعومة حالياً (النماذج المتقاعدة أزيلت من القائمة). */
    public const SUPPORTED_GEMINI_MODELS = [
        'gemini-3.1-flash-lite' => 'gemini-3.1-flash-lite (الافتراضي: اقتصادي ومناسب للتحقق البصري)',
        'gemini-3.5-flash' => 'gemini-3.5-flash (أدق وأبطأ وأعلى تكلفة)',
    ];

    private function getPythonPath()
    {
        return PythonBridge::pythonPath();
    }

    /**
     * تنفيذ أوامر جسر بايثون مباشرة عبر سطر الأوامر (cli_bridge.py) بترميز UTF-8.
     */
    private function runPython($action, $params = [])
    {
        return PythonBridge::run($action, $params);
    }

    /**
     * عرض الصفحة الرئيسية للوحة التحكم والإحصائيات.
     * كل الأرقام هنا عدادات حقيقية من قاعدة البيانات والشيت؛ لا توجد تقديرات أو ثوابت مختلقة.
     */
    public function index()
    {
        try {
            $failedRuns = ProductFailure::count();

            // عدادات الاعتماد الحقيقية من resolved_products حسب حالة التحقق
            $resolvedByStatus = [];
            try {
                if (Schema::hasColumn('resolved_products', 'verification_status')) {
                    $rows = DB::table('resolved_products')
                        ->select('verification_status', DB::raw('COUNT(*) AS n'))
                        ->groupBy('verification_status')
                        ->get();
                    foreach ($rows as $r) {
                        $resolvedByStatus[(string) ($r->verification_status ?? 'legacy')] = (int) $r->n;
                    }
                } else {
                    $resolvedByStatus['legacy'] = ResolvedProduct::count();
                }
            } catch (\Throwable $e) {
                $resolvedByStatus = [];
            }

            $queueCounters = QueueStats::counters();

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

            return view('dashboard.index', compact('resolvedByStatus', 'queueCounters', 'total', 'linked', 'review', 'errors', 'missing', 'percentage'));
        } catch (\Exception $e) {
            return view('dashboard.index', [
                'resolvedByStatus' => [],
                'queueCounters' => ['by_status' => [], 'by_failure_code' => []],
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
            
            // Bypass cache if background automation is active or pending curation
            $isAutomationActive = false;
            try {
                $stateRow = \DB::select("SELECT status FROM automation_state WHERE `key` = 'active_session' LIMIT 1");
                if (!empty($stateRow)) {
                    $status = $stateRow[0]->status;
                    if ($status === 'pre_caching' || $status === 'running' || $status === 'curation_pending') {
                        $isAutomationActive = true;
                    }
                }
            } catch (\Exception $ex) {
                // Table not loaded yet
            }
            
            $cachedProducts = ($forceRefresh || $isAutomationActive) ? null : \Cache::get($cacheKey);
            
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

            // جلب تفاصيل الكاش المحلي (الاعتمادات الملغاة superseded لا تعرض)
            $resolvedQuery = ResolvedProduct::query();
            try {
                if (Schema::hasColumn('resolved_products', 'verification_status')) {
                    $resolvedQuery->where(function ($q) {
                        $q->whereNull('verification_status')->orWhere('verification_status', '<>', 'superseded');
                    });
                }
            } catch (\Throwable $e) {
                // جدول بدون أعمدة الهوية بعد
            }
            $resolved = $resolvedQuery->orderBy('id')->get()->keyBy('barcode');

            // جلب مرشحات الصور المخزنة للفرز والاعتماد البصري.
            // الربط بالمنتج يتم عبر sku_key (وليس رقم الصف الذي يتغير عند تعديل الشيت)،
            // مع الرجوع لرقم الصف فقط للمرشحات القديمة التي لا تملك sku_key،
            // وتعرض فقط مرشحات آخر تشغيل (run_id) لكل sku_key.
            $candidateRows = [];
            try {
                $candidateRows = DB::table('curation_candidates')
                    ->orderBy('id', 'asc')
                    ->get()
                    ->map(fn ($r) => (array) $r)
                    ->all();
            } catch (\Exception $e) {
                // Table might not exist or be empty yet
            }

            $products = CandidateMatcher::attach($products, $candidateRows);

            foreach ($products as &$prod) {
                $barcode = trim($prod['barcode'] ?? '');
                if ($barcode && isset($resolved[$barcode])) {
                    $prod['cached_image'] = $resolved[$barcode]->cloudinary_url;
                    $prod['verification_status'] = $resolved[$barcode]->verification_status ?? 'legacy';
                    $prod['resolved_at'] = $resolved[$barcode]->resolved_at ? $resolved[$barcode]->resolved_at->toIso8601String() : null;
                }
            }
            unset($prod);

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

    /**
     * حساب عدد الصفوف التقديرية لبراند معين عبر كاش المنتجات
     */
    public function getBrandEstimateCount(Request $request)
    {
        try {
            $brand = mb_strtolower(trim($request->query('brand', '')));
            if (empty($brand)) {
                return response()->json(['count' => 0]);
            }

            $cacheKey = 'products_json_v1';
            $products = \Cache::get($cacheKey);

            if ($products === null) {
                $result = $this->runPython('get_products');
                if (isset($result['status']) && $result['status'] === 'success') {
                    $products = $result['products'];
                    \Cache::put($cacheKey, $products, 3600);
                }
            }

            if (empty($products)) {
                return response()->json(['count' => 0]);
            }

            $count = 0;
            foreach ($products as $prod) {
                $prodBrand = mb_strtolower(trim($prod['brand'] ?? ''));
                if (!empty($prodBrand) && mb_strpos($prodBrand, $brand) !== false) {
                    $count++;
                }
            }

            return response()->json(['count' => $count]);
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }

    /**
     * عرض صفحة تشخيصات وسجلات النظام
     */
    public function systemDiagnostics()
    {
        return view('dashboard.diagnostics');
    }

    /**
     * تشغيل فحص الخدمات السحابية واسترجاع النتيجة بصيغة JSON
     */
    public function runDiagnosticsJson()
    {
        @set_time_limit(180);
        try {
            $pythonPath = $this->getPythonPath();
            $scriptPath = base_path('../verify_cloud_services.py');
            $cmd = "\"{$pythonPath}\" \"{$scriptPath}\" --json 2>&1";
            $output = shell_exec($cmd);
            
            $pos = strpos($output, '{');
            if ($pos !== false) {
                $output = substr($output, $pos);
            }
            
            $decoded = json_decode($output, true);
            if ($decoded) {
                return response()->json($decoded);
            }
            
            return response()->json([
                'status' => 'failed',
                'error' => 'Invalid output from python diagnostic script',
                'raw_output' => $output
            ], 500);
        } catch (\Exception $e) {
            return response()->json([
                'status' => 'failed',
                'error' => $e->getMessage()
            ], 500);
        }
    }

    /**
     * إخفاء القيمة السرية: تعرض آخر 4 أحرف فقط.
     */
    public static function maskSecret(?string $value): string
    {
        $value = (string) $value;
        if ($value === '') {
            return '';
        }
        if (mb_strlen($value) <= 4) {
            return '••••';
        }
        return '••••' . mb_substr($value, -4);
    }

    /**
     * عرض صفحة إعدادات النظام ومفاتيح الـ API.
     * المفاتيح السرية لا تطبع في الصفحة أبداً؛ تعرض مقنّعة (آخر 4 أحرف) ويترك الحقل فارغاً.
     */
    public function settings()
    {
        $settingsRaw = DB::table('system_settings')->get();
        $stored = [];
        foreach ($settingsRaw as $row) {
            $stored[$row->key] = $row->value;
        }

        $settings = [];
        foreach (array_merge(self::TEXT_SETTING_KEYS, self::CHECKBOX_SETTING_KEYS) as $k) {
            $settings[$k] = $stored[$k] ?? '';
        }
        if ($settings['search_engine'] === '') {
            $settings['search_engine'] = 'v2';
        }
        if ($settings['gemini_model'] === '') {
            $settings['gemini_model'] = 'gemini-3.1-flash-lite';
        }

        $masked = [];
        foreach (self::SECRET_SETTING_KEYS as $k) {
            $masked[$k] = self::maskSecret($stored[$k] ?? '');
        }

        $geminiModels = self::SUPPORTED_GEMINI_MODELS;

        return view('dashboard.settings', compact('settings', 'masked', 'geminiModels'));
    }

    /**
     * حفظ وتحديث إعدادات النظام ومفاتيح الـ API في قاعدة البيانات.
     * حقل سري فارغ يعني "إبقاء القيمة الحالية" حتى لا يُكتب القناع فوق المفتاح الحقيقي.
     */
    public function saveSettings(Request $request)
    {
        $warnings = [];

        try {
            foreach (self::SECRET_SETTING_KEYS as $k) {
                $val = trim((string) $request->input($k, ''));
                $clear = $request->boolean('clear_' . $k);
                if ($val === '' && !$clear) {
                    continue;
                }
                if (strpos($val, '••••') === 0) {
                    // قناع أعيد إرساله بالخطأ: لا نكتبه فوق المفتاح
                    continue;
                }
                DB::table('system_settings')->updateOrInsert(
                    ['key' => $k],
                    ['value' => $clear ? '' : $val, 'updated_at' => now()]
                );
            }

            foreach (self::TEXT_SETTING_KEYS as $k) {
                $val = trim((string) $request->input($k, ''));
                if ($k === 'search_engine' && !in_array($val, ['v2', 'v1'], true)) {
                    $val = 'v2';
                }
                if ($k === 'gemini_model' && !array_key_exists($val, self::SUPPORTED_GEMINI_MODELS)) {
                    $warnings[] = "نموذج Gemini '{$val}' غير مدعوم؛ لم يتم تغيير النموذج المحفوظ.";
                    continue;
                }
                if ($k === 'auto_publish_brands') {
                    $brands = array_filter(array_map('trim', explode(',', $val)), fn ($b) => $b !== '');
                    $val = implode(', ', array_unique($brands));
                }
                DB::table('system_settings')->updateOrInsert(
                    ['key' => $k],
                    ['value' => $val, 'updated_at' => now()]
                );
            }

            foreach (self::CHECKBOX_SETTING_KEYS as $ck) {
                $val = $request->has($ck) ? 'true' : 'false';
                DB::table('system_settings')->updateOrInsert(
                    ['key' => $ck],
                    ['value' => $val, 'updated_at' => now()]
                );
            }

            $message = 'تم حفظ وتحديث الإعدادات بنجاح في قاعدة البيانات.';
            if (!empty($warnings)) {
                $message .= ' ' . implode(' ', $warnings);
            }
            return redirect()->route('dashboard.settings')->with('success', $message);
        } catch (\Exception $e) {
            return redirect()->route('dashboard.settings')->with('error', 'فشل حفظ الإعدادات: ' . $e->getMessage());
        }
    }

    /**
     * تحديث بيانات المنتج الموثقة بالبيانات الغنية المعدلة يدوياً
     */
    public function updateRichProduct(Request $request)
    {
        $request->validate([
            'barcode' => 'required',
            'product_name' => 'required|string|max:255',
            'brand' => 'required|string|max:255',
            'metadata' => 'required|array'
        ]);

        try {
            $product = \App\Models\ResolvedProduct::where('barcode', $request->input('barcode'))->first();
            if (!$product) {
                return response()->json([
                    'status' => 'failed',
                    'error' => 'المنتج غير موجود في قاعدة بيانات المنتجات الموثقة.'
                ], 404);
            }

            $product->product_name = $request->input('product_name');
            $product->brand = $request->input('brand');

            $existingMeta = $product->metadata_json ?? [];
            if (!is_array($existingMeta)) {
                $existingMeta = json_decode($product->metadata_json, true) ?? [];
            }
            
            $product->metadata_json = array_merge($existingMeta, $request->input('metadata'));
            $product->save();

            // Clear cache
            \Cache::forget('products_json_v1');

            return response()->json([
                'status' => 'success',
                'message' => 'تم تحديث بيانات المنتج الغنية بنجاح في قاعدة البيانات.'
            ]);
        } catch (\Exception $e) {
            return response()->json([
                'status' => 'failed',
                'error' => 'فشل التحديث: ' . $e->getMessage()
            ], 500);
        }
    }

    /**
     * تصدير الكتالوج الموثق بصيغة CSV أو JSON
     */
    public function exportRichCatalog(Request $request)
    {
        try {
            $format = $request->query('format', 'csv');
            $products = \App\Models\ResolvedProduct::orderBy('resolved_at', 'desc')->get();

            if ($format === 'json') {
                $exportData = [];
                foreach ($products as $p) {
                    $meta = $p->metadata_json ?? [];
                    if (!is_array($meta)) {
                        $meta = json_decode($p->metadata_json, true) ?? [];
                    }
                    $exportData[] = [
                        'barcode' => $p->barcode,
                        'product_name' => $p->product_name,
                        'brand' => $p->brand,
                        'cloudinary_url' => $p->cloudinary_url,
                        'verification_status' => $p->verification_status ?? 'legacy',
                        'resolved_at' => $p->resolved_at ? $p->resolved_at->toIso8601String() : null,
                        'metadata' => $meta
                    ];
                }
                return response()->json($exportData, 200, [
                    'Content-Disposition' => 'attachment; filename="rich_catalog_export.json"',
                    'Content-Type' => 'application/json; charset=UTF-8'
                ]);
            }

            // CSV Export
            $headers = [
                'Content-Type' => 'text/csv; charset=UTF-8',
                'Content-Disposition' => 'attachment; filename="rich_catalog_export.csv"',
                'Pragma' => 'no-cache',
                'Cache-Control' => 'must-revalidate, post-check=0, pre-check=0',
                'Expires' => '0'
            ];

            $callback = function() use ($products) {
                $file = fopen('php://output', 'w');
                // UTF-8 BOM
                fprintf($file, chr(0xEF).chr(0xBB).chr(0xBF));
                
                fputcsv($file, [
                    'الباركود (Barcode)', 
                    'اسم المنتج (Name)', 
                    'العلامة التجارية (Brand)', 
                    'رابط الصورة (Image URL)', 
                    'حالة التحقق (Verification status)',
                    'التصنيف الرئيسي (Category L1)',
                    'التصنيف الفرعي 1 (Category L2)',
                    'التصنيف الفرعي 2 (Category L3)',
                    'الوصف التسويقي (Marketing Description)',
                    'المكونات (Ingredients)',
                    'السعرات الحرارية (Calories)',
                    'الكربوهيدرات (Carbohydrates)',
                    'السكريات (Sugars)',
                    'البروتين (Protein)',
                    'الدهون (Fat)',
                    'الملح (Salt)'
                ]);

                foreach ($products as $p) {
                    $meta = $p->metadata_json ?? [];
                    if (!is_array($meta)) {
                        $meta = json_decode($p->metadata_json, true) ?? [];
                    }
                    
                    fputcsv($file, [
                        $p->barcode,
                        $p->product_name,
                        $p->brand,
                        $p->cloudinary_url,
                        $p->verification_status ?? 'legacy',
                        $meta['web_category_l1'] ?? $meta['category'] ?? '',
                        $meta['web_category_l2'] ?? '',
                        $meta['web_category_l3'] ?? '',
                        $meta['marketing_description_ar'] ?? $meta['marketing_description'] ?? '',
                        $meta['ingredients'] ?? '',
                        $meta['calories'] ?? '',
                        $meta['carbohydrates'] ?? '',
                        $meta['sugars'] ?? '',
                        $meta['protein'] ?? '',
                        $meta['fat_total'] ?? '',
                        $meta['salt'] ?? ''
                    ]);
                }
                fclose($file);
            };

            return response()->stream($callback, 200, $headers);
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }
}

