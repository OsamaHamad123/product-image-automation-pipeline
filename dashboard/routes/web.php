<?php

use Illuminate\Support\Facades\Route;
use App\Http\Controllers\ProductController;
use App\Http\Controllers\ApiController;
use App\Http\Controllers\CurationController;
use App\Http\Controllers\OverviewController;
use App\Http\Controllers\ReviewController;
use App\Http\Controllers\RunController;

// تسجيل الدخول (RequireLogin بيحوّل لهون كل زائر بلا جلسة)
Route::get('/login', [\App\Http\Controllers\LoginController::class, 'show'])->name('login');
Route::post('/login', [\App\Http\Controllers\LoginController::class, 'attempt'])->name('login.attempt');
Route::post('/logout', [\App\Http\Controllers\LoginController::class, 'logout'])->name('logout');

// صفحات لوحة التحكم
Route::get('/', [OverviewController::class, 'index'])->name('dashboard.index'); // p2-run: الرئيسية
Route::get('/catalog', [ReviewController::class, 'page'])->name('dashboard.catalog');
Route::get('/active-learning', [ProductController::class, 'activeLearning'])->name('dashboard.active_learning'); // p2-health: ← /settings?tab=auto-publish
Route::get('/errors', [ProductController::class, 'errors'])->name('dashboard.errors'); // ← /catalog?filter=failed
Route::get('/rich-catalog', [ProductController::class, 'richCatalog'])->name('dashboard.rich_catalog'); // ← /catalog
Route::get('/batch-automation', [RunController::class, 'page'])->name('dashboard.batch_automation'); // p2-run: التشغيل (?tab=review -> /catalog?mode=bulk)
Route::get('/system-diagnostics', [\App\Http\Controllers\HealthController::class, 'page'])->name('dashboard.diagnostics'); // p2-health: الصحة والتكلفة
Route::get('/settings', [\App\Http\Controllers\SettingsController::class, 'show'])->name('dashboard.settings'); // p2-health: ?tab=
Route::post('/settings', [\App\Http\Controllers\SettingsController::class, 'save'])->name('dashboard.save_settings'); // p2-health
// «تجاوز عزل الخلفية» / «رجّع عزل الخلفية» (صفحة الصحة، شاشة المراجعة، تبويب «معالجة الصور»): {method} من BG_METHODS
Route::post('/api/settings/bg-method', [\App\Http\Controllers\SettingsController::class, 'setBgMethod']);
Route::get('/rich-catalog/export', [ProductController::class, 'exportRichCatalog'])->name('dashboard.rich_catalog.export');
Route::post('/api/failures/retry', [ApiController::class, 'retryFailures']);

// خدمات البيانات الداخلية لـ AJAX
Route::get('/api/products-json', [ProductController::class, 'getProductsJson']);
Route::post('/api/clear-products-cache', [ApiController::class, 'clearProductsCache']);
Route::post('/api/system/run-diagnostics', [ProductController::class, 'runDiagnosticsJson']);
// صحة البحث وتكلفته من سجل الطابور (قراءة فقط عبر cli_bridge.ops_health)
Route::get('/api/system/ops-health', [\App\Http\Controllers\HealthController::class, 'summary']);
// رأس صفحة الصحة: «كلشي تمام» أو «شو بدو منك» (HealthAttentionController، قراءة فقط وبلا بايثون)
Route::get('/api/system/attention', [\App\Http\Controllers\HealthAttentionController::class, 'show']);
// «دقة الاقتراحات الحقيقية»: أرقام كل فئة اختيار من قرارات المراجعين (قراءة فقط عبر cli_bridge.review_stats)
Route::get('/api/system/review-lanes', [\App\Http\Controllers\HealthController::class, 'reviewLanes']);
// «فحص النشر»: بروفة النشر على صورة تجريبية (cli_bridge.publish_check) بزر صريح؛ GET يرجع آخر نتيجة محفوظة فقط
Route::post('/api/system/publish-check', [\App\Http\Controllers\HealthController::class, 'runPublishCheck']);
Route::get('/api/system/publish-check', [\App\Http\Controllers\HealthController::class, 'lastPublishCheckJson']);
// «فهرس المتاجر المحلي»: التحديث كمهمة خلفية بزر صريح (cli_bridge.local_index_refresh)؛ GET يرجع حالة البطاقة فقط
Route::get('/api/system/local-index', [\App\Http\Controllers\LocalIndexController::class, 'status']);
Route::post('/api/system/local-index/refresh', [\App\Http\Controllers\LocalIndexController::class, 'refresh']);
// «صدّر مجموعة اختبار» (القسم المتقدم بصفحة الصحة): المنتجات المراجَعة كملف واحد (cli_bridge.eval_export) بزر صريح
Route::post('/api/system/eval-export', [\App\Http\Controllers\HealthController::class, 'exportEvalSet']);
Route::get('/api/system/eval-export/{file}', [\App\Http\Controllers\HealthController::class, 'downloadEvalSet'])
    ->where('file', 'laqta_eval_set_[0-9_-]+\.zip');

// جسر بايثون (cli_bridge.py مباشرة، بدون خادم FastAPI)
Route::post('/api/search', [ApiController::class, 'search']);
Route::post('/api/select_image', [ApiController::class, 'selectImage']);
Route::post('/api/reject_image', [ApiController::class, 'rejectImage']);
Route::post('/api/upload_manual_image', [ApiController::class, 'uploadManualImage']);
Route::get('/api/image-proxy', [ApiController::class, 'imageProxy']);
Route::post('/api/run_all', [ApiController::class, 'runAll']);
Route::post('/api/run-all', [ApiController::class, 'runAll']);
Route::post('/api/stop-batch', [ApiController::class, 'stopBatch']);
Route::post('/api/stop_batch', [ApiController::class, 'stopBatch']);
Route::get('/api/batch_status', [ApiController::class, 'batchStatus']);
Route::get('/api/batch-status', [ApiController::class, 'batchStatus']);
Route::post('/api/batch/pause', [ApiController::class, 'pauseBatch']);
Route::post('/api/batch/resume', [ApiController::class, 'resumeBatch']);
Route::post('/api/batch/reset', [ApiController::class, 'resetBatch']);
Route::post('/api/sheet/preview', [ApiController::class, 'previewSheet']);
Route::post('/api/sheet/save', [ApiController::class, 'saveSheetConfig']);

// المراجعة البشرية للمرشحين (الرفض يعيد البحث فعلياً عبر cli_bridge.reject_image)
Route::post('/api/v1/curation/reject', [CurationController::class, 'rejectAndReSearch']);
Route::post('/api/v1/curation/select-candidate', [CurationController::class, 'selectCandidate']);
Route::post('/api/v1/curation/save-candidates', [CurationController::class, 'saveCandidates']);

// حزمة المراجعة (P2 review): من ينتظر المراجعة حسب حالة صف الطابور، بنفس عدّ /api/batch-status
Route::get('/api/review/queue-state', [ReviewController::class, 'queueState']);
// سبب «بلا اقتراح» لصفوف حُفظت قبل أن يحسبه العامل: يُحسب مرة مما حُفظ (بلا بحث وبلا تكلفة)
Route::post('/api/review/explain-backfill', [ReviewController::class, 'explainBackfill']);
// «تراجع عن الرفض»: يشيل رفض صورة لمنتج (cli_bridge.undo_reject) فترجع للاقتراحات ولا تنحسب بالإحصائيات
Route::post('/api/review/undo-reject', [ReviewController::class, 'undoReject']);

// حالة جسر بايثون وقاعدة البيانات
Route::get('/api/system/status', [ApiController::class, 'systemStatus']);

// حزمة الصحة والإعدادات (P2 health): آخر أسطر السجلين؛ ملف غير موجود حالة عادية (exists=false) وليس 404
Route::get('/api/view-pipeline-log', [\App\Http\Controllers\HealthController::class, 'pipelineLog']);
Route::get('/api/view-laravel-log', [\App\Http\Controllers\HealthController::class, 'laravelLog']);
// حزمة التشغيل الليلي (P4b): سجل الليلة الأخيرة أو ?date=YYYY-MM-DD من temp/nightly (التاريخ فقط، لا مسار)
Route::get('/api/view-nightly-log', [\App\Http\Controllers\HealthController::class, 'nightlyLog']);
// p2-run (الرئيسية والتشغيل): بيانات للقراءة فقط؛ التشغيل والإيقاف يبقيان في ApiController
Route::get('/api/overview', [OverviewController::class, 'data']);
Route::get('/api/run/live', [RunController::class, 'live']);
Route::get('/api/run/plan', [RunController::class, 'plan']);
// «تصدير تقرير للتحليل» (ملف JSON واحد لتشغيل، بلا بحث وبلا تكلفة) و«جودة بيانات الشيت» (من كاش صفوف الشيت)
Route::get('/api/run/export', [RunController::class, 'export']);
Route::get('/api/run/sheet-quality', [RunController::class, 'sheetQuality']);
// «ماركات ناقصة من Brands Mapping»: القائمة قراءة فقط؛ «اقترح الموقع الرسمي» استعلام بحث واحد؛ «أضف» يكتب شيت المالك (CSRF)
Route::get('/api/run/brand-suggestions', [RunController::class, 'brandSuggestions']);
Route::post('/api/run/brand-official-site', [RunController::class, 'brandOfficialSite']);
Route::post('/api/run/brand-add', [RunController::class, 'brandAdd']);
Route::post('/api/run/brand-add-all', [RunController::class, 'brandAddAll']);
// «عبّي جدول الماركات»: القائمة قراءة فقط؛ «دوّر عالمواقع الرسمية» لحد 10 بحث؛ «اعتمد المحدد» و«تراجع» يكتبوا شيت المالك (CSRF)
Route::get('/api/run/brand-bulk', [RunController::class, 'brandBulk']);
Route::post('/api/run/brand-bulk-sites', [RunController::class, 'brandBulkSites']);
Route::post('/api/run/brand-bulk-add', [RunController::class, 'brandBulkAdd']);
Route::post('/api/run/brand-undo', [RunController::class, 'brandUndo']);
// «باركودات من صفحات المتاجر»: القائمة قراءة فقط؛ «اكتب الباركودات المختارة» يجدول كتابة مُتحقق منها بعمود الباركود (CSRF)
Route::get('/api/run/barcode-suggestions', [RunController::class, 'barcodeSuggestions']);
Route::post('/api/run/barcode-write', [RunController::class, 'barcodeWrite']);

// «أعد القص» (RecutController): بطاقة «صور قديمة بخلفية بيضا» بالقسم المتقدم بصفحة الصحة (التجربة ما بتغيّر شي؛ «ابدأ»
// دفعة بسقف عدد وتكلفة بالخلفية)، وصفحة «فحص القص» (كل تبديل بكبسة المالك وبيتسجّل للتراجع)
Route::post('/api/system/reprocess/plan', [\App\Http\Controllers\RecutController::class, 'plan']);
Route::post('/api/system/reprocess/start', [\App\Http\Controllers\RecutController::class, 'start']);
Route::get('/api/system/reprocess', [\App\Http\Controllers\RecutController::class, 'batchStatus']);
Route::get('/cutout-check', [\App\Http\Controllers\RecutController::class, 'page'])->name('dashboard.cutout_check');
Route::get('/api/cutout/gallery', [\App\Http\Controllers\RecutController::class, 'gallery']);
Route::post('/api/cutout/try', [\App\Http\Controllers\RecutController::class, 'tryCut']);
Route::post('/api/cutout/apply', [\App\Http\Controllers\RecutController::class, 'apply']);
Route::post('/api/cutout/discard', [\App\Http\Controllers\RecutController::class, 'discard']);
Route::post('/api/cutout/undo', [\App\Http\Controllers\RecutController::class, 'undo']);
Route::get('/api/cutout/preview/{token}', [\App\Http\Controllers\RecutController::class, 'preview'])
    ->where('token', '[0-9a-f]{32}');
// «جهّز لقطة»: معالج التجهيز لأول مرة (SetupController). فتح الصفحة ما بيشغّل أي فحص: كل فحص بزر صريح، والتقدّم بـ system_settings
Route::get('/setup', [\App\Http\Controllers\SetupController::class, 'page'])->name('dashboard.setup');
Route::get('/api/setup/state', [\App\Http\Controllers\SetupController::class, 'state']);
Route::post('/api/setup/check', [\App\Http\Controllers\SetupController::class, 'check']);
Route::post('/api/setup/progress', [\App\Http\Controllers\SetupController::class, 'progress']);

Route::view('/ui-kit', 'dashboard.ui_kit')->name('dashboard.ui_kit'); // مرجع مكوّنات هوية لقطة
