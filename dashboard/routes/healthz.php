<?php

use App\Http\Controllers\HealthzController;
use Illuminate\Cookie\Middleware\AddQueuedCookiesToResponse;
use Illuminate\Cookie\Middleware\EncryptCookies;
use Illuminate\Foundation\Http\Middleware\ValidateCsrfToken;
use Illuminate\Session\Middleware\StartSession;
use Illuminate\Support\Facades\Route;
use Illuminate\View\Middleware\ShareErrorsFromSession;

// فحص الصحة للمراقبة الخارجية (UptimeRobot / Uptime Kuma): JSON بلا أسرار، 200 سليم و503 غير سليم. nginx يسمح له فقط
// لـ 127.0.0.1 وعناوين --monitor-ip (deploy/ubuntu/laqta.conf). بلا جلسة ولا كوكيز: مراقب كل دقيقة لا يملأ ملفات الجلسات.
//
// ملف مستقل (bootstrap/app.php يحمّله بعد routes/web.php) لأن routes/web.php لا يُعطّل أي وسيط حماية لأي مسار فيه
// (اختبارات اللوحة تتأكد من ذلك، ومسار ui-kit يبقى آخر مسار فيه). هذا مسار GET وحيد للقراءة فقط.
Route::get('/healthz', [HealthzController::class, 'show'])->name('healthz')
    ->withoutMiddleware([
        EncryptCookies::class,
        AddQueuedCookiesToResponse::class,
        StartSession::class,
        ShareErrorsFromSession::class,
        ValidateCsrfToken::class,
    ]);
