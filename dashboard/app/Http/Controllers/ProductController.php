<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;
use App\Models\ResolvedProduct;
use App\Services\PythonBridge;
use App\Services\CandidateMatcher;

class ProductController extends Controller
{
    /** نماذج Gemini المدعومة حالياً (النماذج المتقاعدة أزيلت من القائمة). */
    public const SUPPORTED_GEMINI_MODELS = [
        'gemini-3.1-flash-lite' => 'gemini-3.1-flash-lite (الافتراضي: اقتصادي ومناسب للتحقق البصري)',
        'gemini-3.5-flash' => 'gemini-3.5-flash (أدق وأبطأ وأعلى تكلفة)',
    ];

    private function getPythonPath()
    {
        return PythonBridge::pythonPath();
    }

    /** كاش صفوف الشيت الخام كما يعيدها get_products بلا أي دمج (الرئيسية، خطة التشغيل، إعادة محاولة الأخطاء). */
    public const SHEET_ROWS_CACHE_KEY = 'sheet_rows_v2';

    /** كاش منتجات المراجعة: صفوف الشيت مدمجة بالمرشحات المخزنة والاعتمادات (الكتالوج، المراجعة الجماعية، الرئيسية). */
    public const REVIEW_PRODUCTS_CACHE_KEY = 'review_products_v2';

    private const PRODUCTS_CACHE_SECONDS = 3600;

    /**
     * بصمة بيانات المراجعة في قاعدة البيانات. العامل (main.py) يكتب المرشحات والاعتمادات مباشرة دون المرور
     * بلارافيل، فأي إدراج أو حذف أو تغيير اختيار أو اعتماد أو إلغاء اعتماد يغيّر البصمة (كل تعديل على
     * resolved_products يحدّث resolved_at)، ويُعاد الدمج من قاعدة البيانات في الطلب التالي بدون إعادة قراءة الشيت.
     * أخطاء المنتجات (product_failures) منها أيضاً: العامل يسجل الفشل دون أي كتابة في الشيت.
     */
    public const REVIEW_VERSION_SQL = "SELECT "
        . "(SELECT CONCAT_WS(':', COUNT(*), COALESCE(MAX(id), 0), COALESCE(SUM(id * is_selected), 0)) "
        . "FROM curation_candidates) AS candidates, "
        . "(SELECT CONCAT_WS(':', COUNT(*), COALESCE(MAX(id), 0), COALESCE(SUM(UNIX_TIMESTAMP(resolved_at)), 0), "
        . "COALESCE(SUM(verification_status = 'superseded'), 0)) FROM resolved_products) AS resolved, "
        . "(SELECT CONCAT_WS(':', COUNT(*), COALESCE(SUM(CRC32(barcode)), 0), COALESCE(SUM(UNIX_TIMESTAMP(failed_at)), 0)) "
        . "FROM product_failures) AS failures";

    /** آخر نتيجة لفحص الاتصالات يحفظها verify_cloud_services.py --json. */
    private const DIAGNOSTICS_RESULT_FILE = '../temp/diagnostics_last.json';

    /** مهلة فحص الاتصالات: مهلة بايثون الإجمالية، ثم حد أقصى يُنهى بعده الفحص (خادم PHP المدمج يخدم طلباً واحداً). */
    private const DIAGNOSTICS_DEADLINE_SECONDS = 30;
    private const DIAGNOSTICS_KILL_SECONDS = 45;

    /**
     * تفريغ كاش صفوف الشيت وكاش منتجات المراجعة معاً. يُستدعى بعد كل كتابة من لوحة التحكم:
     * اعتماد، رفض، رفع يدوي، إعادة محاولة، حفظ إعدادات الشيت، زر تفريغ الكاش.
     */
    public static function forgetProductCaches(): void
    {
        \Cache::forget(self::SHEET_ROWS_CACHE_KEY);
        \Cache::forget(self::REVIEW_PRODUCTS_CACHE_KEY);
    }

    /**
     * بصمة ملف كاش الشيت لدى بايثون (products_cache.json). بايثون يحذف الملف بعد كل كتابة في الشيت
     * (العامل، عامل المزامنة، الاعتماد، حفظ إعدادات الشيت) ويعيد إنشاءه عند القراءة التالية، فتغيّر البصمة
     * يعني أن الصفوف المخزنة هنا قديمة حتى لو لم تمر الكتابة عبر لارافيل. null: لا يوجد كاش صالح لدى بايثون.
     */
    private static function sheetStamp(): ?string
    {
        $path = base_path('../products_cache.json');
        clearstatcache(true, $path);
        if (!is_file($path)) {
            return null;
        }
        return filemtime($path) . ':' . filesize($path);
    }

    /**
     * صفوف الشيت الخام (get_products) من الكاش، أو من بايثون عند غيابها أو تغيّر بصمة الشيت.
     * تعيد null عند فشل القراءة، وسبب الفشل في $error.
     */
    public static function sheetRows(bool $refresh = false, ?string &$error = null): ?array
    {
        $stamp = self::sheetStamp();
        if (!$refresh && $stamp !== null) {
            $cached = \Cache::get(self::SHEET_ROWS_CACHE_KEY);
            if (is_array($cached) && ($cached['stamp'] ?? null) === $stamp && is_array($cached['rows'] ?? null)) {
                return $cached['rows'];
            }
        }

        $result = PythonBridge::run('get_products');
        if (($result['status'] ?? '') !== 'success' || !is_array($result['products'] ?? null)) {
            $error = (string) ($result['error'] ?? 'Unknown error');
            return null;
        }
        // tab: اسم التبويب اللي انقرت منه الصفوف (cli_bridge get_products sheet_tab)، لسطر صفحة التشغيل بلا طلب جديد
        \Cache::put(self::SHEET_ROWS_CACHE_KEY, ['stamp' => self::sheetStamp(), 'rows' => $result['products'],
            'tab' => is_string($result['sheet_tab'] ?? null) ? $result['sheet_tab'] : null], self::PRODUCTS_CACHE_SECONDS);
        return $result['products'];
    }

    /** بصمة بيانات المراجعة الحالية، أو null إذا تعذر حسابها (عندها لا يُخدم كاش المراجعة). */
    private static function reviewDataVersion(): ?string
    {
        try {
            $row = DB::selectOne(self::REVIEW_VERSION_SQL);
            return $row ? ((string) $row->candidates . '|' . (string) $row->resolved . '|' . (string) $row->failures) : null;
        } catch (\Throwable $e) {
            return null;
        }
    }

    /**
     * منتجات المراجعة: صفوف الشيت مدمجة بالمرشحات المخزنة وحالة الاعتماد. تُخدم من الكاش ما دامت بصمة قاعدة
     * البيانات وبصمة الشيت لم تتغيرا، وإلا يُعاد الدمج (وصفوف الشيت نفسها من كاشها الخاص).
     * تعيد null عند فشل قراءة الشيت، وسبب الفشل في $error.
     */
    public static function reviewProducts(bool $refresh = false, ?string &$error = null, ?bool &$fromCache = null): ?array
    {
        $fromCache = false;
        // البصمة تُحسب قبل قراءة البيانات: كتابة أثناء الدمج تجعل الطلب التالي يعيد الدمج ولا تُفقد
        $version = self::reviewDataVersion();
        $stamp = self::sheetStamp();
        if (!$refresh && $version !== null && $stamp !== null) {
            $cached = \Cache::get(self::REVIEW_PRODUCTS_CACHE_KEY);
            if (is_array($cached) && ($cached['version'] ?? null) === $version
                && ($cached['stamp'] ?? null) === $stamp && is_array($cached['products'] ?? null)) {
                $fromCache = true;
                return $cached['products'];
            }
        }

        $products = self::sheetRows($refresh, $error);
        if ($products === null) {
            return null;
        }

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
        $resolvedRows = $resolvedQuery->orderBy('id')->get();
        $resolved = $resolvedRows->keyBy('barcode');
        // منتج بلا باركود يُعرف اعتماده بـ sku_key (بصمة البراند|الاسم|الحجم): رابطه في الشيت قد يكون ما زال في طابور
        // الكتابة، فبدونه يظهر «ما انبحث» وقد يبدأ له بحث تلقائي مدفوع
        $resolvedBySku = [];
        foreach ($resolvedRows as $row) {
            $rowSku = trim((string) ($row->sku_key ?? ''));
            if ($rowSku !== '') {
                $resolvedBySku[$rowSku] = $row;     // ordered by id: the latest approval of the key wins
            }
        }

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

        // أخطاء المنتجات من قاعدة البيانات في كل دمج: العامل يسجل الفشل في product_failures دون أي كتابة في الشيت،
        // فقيمة has_error المحفوظة مع صفوف الشيت (من get_products) تبقى قديمة حتى الكتابة التالية في الشيت
        $failures = null;
        try {
            $failures = DB::table('product_failures')->pluck('error_message', 'barcode')->all();
        } catch (\Throwable $e) {
            // الجدول غير موجود بعد: تبقى قيم get_products
        }

        // الصور التي رفضها المراجعون لكل منتج (rejected_images بـ sku_key): شاشة المراجعة تعرضها مع «تراجع عن الرفض»
        $rejectedBySku = [];
        try {
            foreach (DB::table('rejected_images')->orderBy('id')->get(['sku_key', 'original_url', 'reason_code', 'created_at']) as $r) {
                $rejSku = trim((string) ($r->sku_key ?? ''));
                $rejUrl = trim((string) ($r->original_url ?? ''));
                if ($rejSku !== '' && $rejUrl !== '') {
                    $rejectedBySku[$rejSku][$rejUrl] = ['url' => $rejUrl, 'reason_code' => (string) ($r->reason_code ?? ''),
                                                        'at' => $r->created_at ? (string) $r->created_at : null];
                }
            }
        } catch (\Throwable $e) {
            // الجدول غير موجود بعد: لا صور مرفوضة
        }

        foreach ($products as &$prod) {
            $barcode = trim($prod['barcode'] ?? '');
            $sku = trim((string) ($prod['sku_key'] ?? ''));
            // المفتاح بلا باركود (get_products alt_sku_key): صف كُتب له باركود بعد اعتماده («باركودات من صفحات
            // المتاجر» أو بالإيد) يبقى اعتماده ورفضه محفوظين بمفتاحه القديم
            $alt = trim((string) ($prod['alt_sku_key'] ?? ''));
            $alt = $alt !== $sku ? $alt : '';
            $hit = null;
            if ($barcode && isset($resolved[$barcode])) {
                $hit = $resolved[$barcode];
            } elseif ($barcode === '' && $sku !== '' && isset($resolvedBySku[$sku])) {
                $hit = $resolvedBySku[$sku];
            } elseif ($alt !== '' && isset($resolvedBySku[$alt])) {
                $hit = $resolvedBySku[$alt];
            }
            $prod['rejected_images'] = $sku !== ''
                ? array_values(array_merge($rejectedBySku[$alt] ?? [], $rejectedBySku[$sku] ?? [])) : [];
            if ($hit !== null) {
                $prod['cached_image'] = $hit->cloudinary_url;
                $prod['verification_status'] = $hit->verification_status ?? 'legacy';
                $prod['resolved_at'] = $hit->resolved_at ? $hit->resolved_at->toIso8601String() : null;
                // «الباركود من صفحة المتجر» للصف بلا باركود (يُحفظ مع الاعتماد، لا يُكتب في الشيت)
                $prod['page_gtin'] = isset($hit->page_gtin) && $hit->page_gtin !== '' ? (string) $hit->page_gtin : null;
            }
            if (is_array($failures)) {
                // نفس مفاتيح get_products في cli_bridge.py: الباركود، أو ERR_<الاسم>_<البراند>
                $altKey = 'ERR_' . str_replace(' ', '_', ($prod['product_name'] ?? '') . '_' . ($prod['brand'] ?? ''));
                $failureKey = ($barcode !== '' && array_key_exists($barcode, $failures)) ? $barcode
                    : (array_key_exists($altKey, $failures) ? $altKey : null);
                $prod['has_error'] = $failureKey !== null;
                $prod['error_message'] = $failureKey !== null ? (string) ($failures[$failureKey] ?? '') : '';
            }
        }
        unset($prod);
        // الصورة الحالية بالشيت تُعرض عبر /api/image-proxy: مضيفها (أي رابط كتبه المالك بالشيت) مسموح للوكيل
        \App\Services\ImageProxy::rememberHosts(array_column($products, 'existing_image_link'));

        \Cache::put(self::REVIEW_PRODUCTS_CACHE_KEY,
            ['version' => $version, 'stamp' => self::sheetStamp(), 'products' => $products],
            self::PRODUCTS_CACHE_SECONDS);
        return $products;
    }


    /**
     * جلب المنتجات كـ JSON مع دمج البيانات الوصفية والحالات من SQLite
     */
    public function getProductsJson(Request $request)
    {
        try {
            $forceRefresh = $request->query('refresh') === 'true';

            // لا يُتجاوز الكاش أثناء التشغيل أو المراجعة: مرشحات العامل الجديدة تغيّر بصمة قاعدة البيانات
            // (يُعاد الدمج من قاعدة البيانات فقط)، وكتابته في الشيت تحذف products_cache.json (تُعاد قراءة الشيت).
            // التجاوز القديم كان يعيد قراءة الشيت كاملاً عبر بايثون مع كل تحميل للكتالوج وقت عمل المراجعين.
            $error = null;
            $fromCache = false;
            $products = self::reviewProducts($forceRefresh, $error, $fromCache);
            if ($products === null) {
                return response()->json(['error' => 'Failed to load products from Google Sheets via CLI: ' . ($error ?? 'Unknown error')], 500);
            }

            return response()->json([
                'status'   => 'success',
                'products' => $products,
                'cached'   => $fromCache
            ])->header('X-Cache', $fromCache ? 'HIT' : 'MISS');
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }

    /**
     * سجل الأخطاء صار رقاقة «أعطال» في شاشة المراجعة (ReviewController): الرابط القديم يفتحها.
     */
    public function errors()
    {
        return redirect()->route('dashboard.catalog', ['filter' => 'failed']);
    }

    /**
     * صفحة التعلم من المراجعين صارت تبويب «النشر الآلي» في الإعدادات (SettingsController): نفس review_stats،
     * ومعها التفعيل لكل ماركة جاهزة. الرابط القديم يفتح التبويب.
     */
    public function activeLearning()
    {
        return redirect()->to(route('dashboard.settings') . '?tab=auto-publish');
    }

    /**
     * معرض الكتالوج الغني أُزيل (شاشة المراجعة تعرض الصور، وتصدير CSV للمعتمدة في وضع الجملة): الرابط القديم يفتح المراجعة.
     */
    public function richCatalog()
    {
        return redirect()->route('dashboard.catalog');
    }





    /** آخر نتيجة محفوظة لفحص الاتصالات، أو null إذا لم يُجرَ فحص بعد. */
    public static function lastDiagnostics(): ?array
    {
        $path = base_path(self::DIAGNOSTICS_RESULT_FILE);
        if (!is_file($path)) {
            return null;
        }
        $decoded = json_decode((string) @file_get_contents($path), true);
        return is_array($decoded) && is_array($decoded['services'] ?? null) ? $decoded : null;
    }

    /**
     * تشغيل فحص الخدمات السحابية (بزر صريح فقط) واسترجاع النتيجة بصيغة JSON.
     * الفحوص تعمل بالتوازي بمهلة إجمالية في بايثون، ويُنهى الفحص من هنا إذا تجاوز حده الأقصى،
     * حتى لا يتوقف خادم PHP المدمج (طلب واحد في كل مرة) لدقائق.
     */
    public function runDiagnosticsJson()
    {
        @set_time_limit(self::DIAGNOSTICS_KILL_SECONDS + 15);
        $outFile = null;
        try {
            putenv('PYTHONUTF8=1');
            putenv('PYTHONIOENCODING=utf-8');
            $command = [
                $this->getPythonPath(), base_path('../verify_cloud_services.py'),
                '--json', '--deadline', (string) self::DIAGNOSTICS_DEADLINE_SECONDS,
            ];
            // المخرجات إلى ملف مؤقت لا إلى أنبوب: قراءة الأنابيب بلا انتظار لا تعمل على ويندوز
            $outFile = tempnam(sys_get_temp_dir(), 'diag');
            $nullDevice = strncasecmp(PHP_OS, 'WIN', 3) === 0 ? 'NUL' : '/dev/null';
            $process = proc_open($command, [
                0 => ['file', $nullDevice, 'r'],
                1 => ['file', $outFile, 'w'],
                2 => ['file', $nullDevice, 'w'],
            ], $pipes, base_path('..'));
            if (!is_resource($process)) {
                throw new \RuntimeException('تعذر تشغيل verify_cloud_services.py');
            }

            $timedOut = false;
            $killAt = microtime(true) + self::DIAGNOSTICS_KILL_SECONDS;
            while (proc_get_status($process)['running']) {
                if (microtime(true) >= $killAt) {
                    proc_terminate($process, 9);
                    $timedOut = true;
                    break;
                }
                usleep(200000);
            }
            proc_close($process);
            $output = (string) @file_get_contents($outFile);

            if ($timedOut) {
                return response()->json([
                    'status' => 'failed',
                    'error' => 'انتهت مهلة فحص الاتصالات (' . self::DIAGNOSTICS_KILL_SECONDS . ' ثانية) فأُوقف الفحص. '
                        . 'تحقق من الإنترنت أو البروكسي ثم أعد المحاولة.'
                ], 504);
            }

            $decoded = PythonBridge::decodeOutput($output);
            if (is_array($decoded) && is_array($decoded['services'] ?? null)) {
                return response()->json($decoded);
            }

            return response()->json([
                'status' => 'failed',
                'error' => 'لم يُرجع verify_cloud_services.py نتيجة صالحة: تحقق من بيئة بايثون ومكتباتها ثم أعد الفحص.',
                'raw_output' => mb_substr($output, -4000)
            ], 500);
        } catch (\Throwable $e) {
            return response()->json([
                'status' => 'failed',
                'error' => $e->getMessage()
            ], 500);
        } finally {
            if ($outFile && file_exists($outFile)) {
                @unlink($outFile);
            }
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

