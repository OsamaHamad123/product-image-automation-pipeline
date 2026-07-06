<?php

use Illuminate\Support\Facades\Route;
use App\Http\Controllers\ProductController;
use App\Http\Controllers\ApiController;

// صفحات لوحة التحكم
Route::get('/', [ProductController::class, 'index'])->name('dashboard.index');
Route::get('/catalog', [ProductController::class, 'catalog'])->name('dashboard.catalog');

// خدمات البيانات الداخلية لـ AJAX
Route::get('/api/products-json', [ProductController::class, 'getProductsJson']);

// بروكسي للاتصال بالبايثون
Route::post('/api/search', [ApiController::class, 'search']);
Route::post('/api/select_image', [ApiController::class, 'selectImage']);
Route::post('/api/upload_manual_image', [ApiController::class, 'uploadManualImage']);
Route::get('/api/logs', [ApiController::class, 'logs']);
Route::post('/api/run_all', [ApiController::class, 'runAll']);
Route::post('/api/run-all', [ApiController::class, 'runAll']);
Route::get('/api/batch_status', [ApiController::class, 'batchStatus']);
Route::get('/api/batch-status', [ApiController::class, 'batchStatus']);
