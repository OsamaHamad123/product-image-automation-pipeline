<?php

namespace App\Http\Middleware;

use Closure;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Auth;
use Symfony\Component\HttpFoundation\Response;

/**
 * كل صفحات اللوحة وواجهاتها بتطلب تسجيل دخول (LoginController). المستثنى:
 * - صفحة الدخول نفسها، و/healthz (للمراقبة، بلا جلسة) و/up.
 * - اللوحة على جهاز المالك: APP_ENV=local والطلب من نفس الجهاز (127.0.0.1). السيرفر APP_ENV=production دايماً.
 * - اختبارات pytest اللي بتشغّل الـ kernel من سطر الأوامر (APP_ENV=testing)، إلا إذا طلبت الدخول بـ LAQTA_TEST_LOGIN=1.
 * زائر بلا دخول: طلب JSON بياخد 401، والصفحة بتتحوّل لصفحة الدخول وبترجع لنفس المكان بعده.
 */
class RequireLogin
{
    public const OPEN_ROUTES = ['login', 'login.attempt', 'healthz'];

    public function handle(Request $request, Closure $next): Response
    {
        if (Auth::check() || self::isOpen($request) || self::trustedWithoutLogin($request)) {
            return $next($request);
        }
        if ($request->expectsJson() || $request->is('api/*')) {
            return response()->json(['status' => 'error', 'error' => 'انتهت الجلسة: سجّل دخول من جديد.'], 401);
        }
        return redirect()->guest(route('login'));
    }

    private static function isOpen(Request $request): bool
    {
        return $request->routeIs(...self::OPEN_ROUTES) || $request->is('up');
    }

    private static function trustedWithoutLogin(Request $request): bool
    {
        if (app()->environment('local')) {
            return in_array($request->ip(), ['127.0.0.1', '::1'], true);
        }
        return app()->runningInConsole() && app()->environment('testing') && !env('LAQTA_TEST_LOGIN');
    }
}
