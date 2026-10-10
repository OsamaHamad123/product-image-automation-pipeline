<?php

namespace App\Http\Middleware;

use Closure;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Auth;
use Symfony\Component\HttpFoundation\Response;

/**
 * صلاحيات المراجع (users.role = reviewer)، بعد RequireLogin. قائمة سماح مش قائمة منع: أي طلب كتابة (غير GET/HEAD)
 * ممنوع على المراجع إلا إذا كان بـ REVIEWER_WRITES، وصفحات المدير بـ ADMIN_PAGES ممنوعة عليه حتى للقراءة.
 * كل صفحة قراءة تانية (الرئيسية، المراجعة، الصحة، الحالة) مفتوحة.
 * المدير، وطلب بلا مستخدم (جهاز المالك واختبارات سطر الأوامر: RequireLogin::trustedWithoutLogin) ما بيتقيّدوا.
 * الممنوع: 403، JSON {status:'error'} للـ API والـ AJAX، وصفحة بسيطة لغيرها.
 */
class ReviewerLimits
{
    // اللي بتطلبه شاشة المراجعة (public/js/review/*.js، ReviewController::page urls) والخروج
    public const REVIEWER_WRITES = [
        'login',                          // دخول من جديد بنفس المتصفح
        'logout',
        'api/select_image',               // اعتماد
        'api/reject_image',               // رفض (مع «دوّر من جديد»)
        'api/upload_manual_image',        // رفع صورة يدوية
        'api/review/undo-reject',         // تراجع عن الرفض
        'api/approval-jobs/*/dismiss',    // «تجاهل» اعتماد ما زبط
        'api/search',                     // البحث عن صور لمنتج من شاشة المراجعة
        'api/clear-products-cache',       // «تحديث» القائمة
        'api/review/explain-backfill',    // بتطلبه الشاشة لحالها: أسباب «بلا اقتراح» (بلا بحث وبلا تكلفة)
    ];

    // صفحات للمدير بس حتى للقراءة: الإعدادات والمفاتيح، التشغيل، التجهيز، فحص القص
    public const ADMIN_PAGES = [
        'settings',
        'active-learning',                // بتحوّل للإعدادات
        'batch-automation',
        'setup',
        'api/setup/*',
        'cutout-check',
    ];

    public const DENIED = 'هالعملية للمدير بس';

    public function handle(Request $request, Closure $next): Response
    {
        $user = Auth::user();
        if ($user === null || !$user->isReviewer() || self::allowed($request)) {
            return $next($request);
        }
        if ($request->expectsJson() || $request->ajax() || $request->is('api/*')) {
            return response()->json(['status' => 'error', 'error' => self::DENIED], 403);
        }
        return response()->view('auth.forbidden', [], 403);
    }

    public static function allowed(Request $request): bool
    {
        if ($request->isMethod('GET') || $request->isMethod('HEAD')) {
            return !$request->is(...self::ADMIN_PAGES);
        }
        return $request->is(...self::REVIEWER_WRITES);
    }
}
