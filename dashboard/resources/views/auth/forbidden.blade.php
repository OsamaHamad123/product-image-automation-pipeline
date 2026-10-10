{{-- 403 لصفحة أو فورم المراجع ما إله فيه (ReviewerLimits). طلبات الـ API بتاخد JSON بدالها. --}}
@php($lqCssVersion = @filemtime(public_path('css/laqta.css')) ?: '1')
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light">
    <meta name="theme-color" content="#0B2226">
    <meta name="robots" content="noindex, nofollow">
    <link rel="icon" type="image/svg+xml" href="{{ asset('favicon.svg') }}">
    <title>للمدير بس · لقطة</title>
    <link rel="stylesheet" href="{{ asset('css/laqta.css') }}?v={{ $lqCssVersion }}">
    <style>
        .lq-forbidden { min-height: 100vh; display: grid; place-items: center; padding: 24px 16px; background: var(--lq-bg); }
        .lq-forbidden__box { width: 100%; max-width: 420px; }
        .lq-forbidden__actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 16px; }
    </style>
</head>
<body class="lq-body">
    <main class="lq-forbidden">
        <div class="lq-forbidden__box lq-card">
            <div class="lq-card__body">
                <h1 class="lq-card__title">هالعملية للمدير بس</h1>
                <p>حسابك حساب مراجعة: بتقدر تراجع الصور وتعتمدها وترفضها، بس هالصفحة أو هالعملية للمدير.</p>
                <div class="lq-forbidden__actions">
                    <a class="lq-btn lq-btn--primary" href="{{ route('dashboard.catalog') }}">رجوع للمراجعة</a>
                    <a class="lq-btn lq-btn--secondary" href="{{ route('dashboard.index') }}">الرئيسية</a>
                </div>
            </div>
        </div>
    </main>
</body>
</html>
