<?php

use Illuminate\Support\Facades\Route;
use App\Http\Controllers\ProductController;
use App\Http\Controllers\ApiController;

// صفحات لوحة التحكم
Route::get('/', [ProductController::class, 'index'])->name('dashboard.index');
Route::get('/catalog', [ProductController::class, 'catalog'])->name('dashboard.catalog');
Route::get('/active-learning', [ProductController::class, 'activeLearning'])->name('dashboard.active_learning');
Route::get('/errors', [ProductController::class, 'errors'])->name('dashboard.errors');
Route::get('/rich-catalog', [ProductController::class, 'richCatalog'])->name('dashboard.rich_catalog');
Route::get('/batch-automation', [ProductController::class, 'batchAutomation'])->name('dashboard.batch_automation');
Route::get('/system-diagnostics', [ProductController::class, 'systemDiagnostics'])->name('dashboard.diagnostics');
Route::get('/settings', [ProductController::class, 'settings'])->name('dashboard.settings');
Route::post('/settings', [ProductController::class, 'saveSettings'])->name('dashboard.save_settings');
Route::get('/api/brand-estimate-count', [ProductController::class, 'getBrandEstimateCount']);
Route::get('/api/rich-products', [ProductController::class, 'getRichProductsJson']);
Route::post('/api/rich-products/update', [ProductController::class, 'updateRichProduct']);
Route::get('/rich-catalog/export', [ProductController::class, 'exportRichCatalog'])->name('dashboard.rich_catalog.export');
Route::post('/api/active-learning/reset', [ApiController::class, 'resetActiveLearning']);
Route::post('/api/failures/retry', [ApiController::class, 'retryFailures']);

// خدمات البيانات الداخلية لـ AJAX
Route::get('/api/products-json', [ProductController::class, 'getProductsJson']);
Route::post('/api/clear-products-cache', [ApiController::class, 'clearProductsCache']);
Route::post('/api/system/run-diagnostics', [ProductController::class, 'runDiagnosticsJson']);

// بروكسي للاتصال بالبايثون
Route::post('/api/search', [ApiController::class, 'search']);
Route::post('/api/select_image', [ApiController::class, 'selectImage']);
Route::post('/api/reject_image', [ApiController::class, 'rejectImage']);
Route::post('/api/upload_manual_image', [ApiController::class, 'uploadManualImage']);
Route::get('/api/logs', [ApiController::class, 'logs']);
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

// إدارة الخدمات والخوادم تلقائياً
Route::get('/api/system/status', [ApiController::class, 'systemStatus']);
Route::post('/api/system/start-flask', [ApiController::class, 'startFlask']);
Route::post('/api/system/stop-flask', [ApiController::class, 'stopFlask']);

// عرض سجلات الأتمتة المباشرة من السيرفر لتشخيص الأخطاء
Route::get('/api/view-pipeline-log', function() {
    $logPath = base_path('../temp/pipeline.log');
    if (file_exists($logPath)) {
        return response(file_get_contents($logPath), 200, ['Content-Type' => 'text/plain; charset=UTF-8']);
    }
    return response('Log file not found at: ' . $logPath, 404);
});

// عرض سجلات أخطاء لارافيل لتشخيص فشل الرفع والاعتماد
Route::get('/api/view-laravel-log', function() {
    $logPath = storage_path('logs/laravel.log');
    if (file_exists($logPath)) {
        $content = file($logPath);
        $lines = array_slice($content, -200);
        return response(implode("", $lines), 200, ['Content-Type' => 'text/plain; charset=UTF-8']);
    }
    return response('Log file not found at: ' . $logPath, 404);
});



