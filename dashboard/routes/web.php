<?php

use Illuminate\Support\Facades\Route;
use App\Http\Controllers\ProductController;
use App\Http\Controllers\ApiController;
use App\Http\Controllers\CurationController;
use App\Http\Controllers\OverviewController;
use App\Http\Controllers\ReviewController;
use App\Http\Controllers\RunController;

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
Route::get('/rich-catalog/export', [ProductController::class, 'exportRichCatalog'])->name('dashboard.rich_catalog.export');
Route::post('/api/failures/retry', [ApiController::class, 'retryFailures']);

// خدمات البيانات الداخلية لـ AJAX
Route::get('/api/products-json', [ProductController::class, 'getProductsJson']);
Route::post('/api/clear-products-cache', [ApiController::class, 'clearProductsCache']);
Route::post('/api/system/run-diagnostics', [ProductController::class, 'runDiagnosticsJson']);
// صحة البحث وتكلفته من سجل الطابور (قراءة فقط عبر cli_bridge.ops_health)
Route::get('/api/system/ops-health', [\App\Http\Controllers\HealthController::class, 'summary']);

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

Route::view('/ui-kit', 'dashboard.ui_kit')->name('dashboard.ui_kit'); // مرجع مكوّنات هوية لقطة
