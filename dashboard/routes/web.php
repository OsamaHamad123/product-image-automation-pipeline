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
Route::get('/api/rich-products', [ProductController::class, 'getRichProductsJson']);
Route::post('/api/active-learning/reset', [ApiController::class, 'resetActiveLearning']);
Route::post('/api/failures/retry', [ApiController::class, 'retryFailures']);

// خدمات البيانات الداخلية لـ AJAX
Route::get('/api/products-json', [ProductController::class, 'getProductsJson']);
Route::post('/api/clear-products-cache', [ApiController::class, 'clearProductsCache']);

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

// إدارة الخدمات والخوادم تلقائياً
Route::get('/api/system/status', [ApiController::class, 'systemStatus']);
Route::post('/api/system/start-flask', [ApiController::class, 'startFlask']);
Route::post('/api/system/stop-flask', [ApiController::class, 'stopFlask']);

