<?php

use Illuminate\Foundation\Application;
use Illuminate\Foundation\Configuration\Exceptions;
use Illuminate\Foundation\Configuration\Middleware;

return Application::configure(basePath: dirname(__DIR__))
    ->withRouting(
        // healthz.php: the monitor route, kept out of web.php because it switches the session middleware off
        web: [__DIR__.'/../routes/web.php', __DIR__.'/../routes/healthz.php'],
        commands: __DIR__.'/../routes/console.php',
        health: '/up',
    )
    ->withMiddleware(function (Middleware $middleware) {
        // كل صفحة وواجهة بتطلب تسجيل دخول (صفحة الدخول و/healthz مستثنيات: RequireLogin::OPEN_ROUTES)
        $middleware->web(append: \App\Http\Middleware\RequireLogin::class);
    })
    ->withExceptions(function (Exceptions $exceptions) {
        //
    })->create();
