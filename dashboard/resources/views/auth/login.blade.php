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
    <title>تسجيل الدخول · لقطة</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Alexandria:wght@500;600;700&family=Readex+Pro:wght@400;500;600;700&display=swap">
    <link rel="stylesheet" href="{{ asset('css/laqta.css') }}?v={{ $lqCssVersion }}">
    <style>
        .lq-login { min-height: 100vh; display: grid; place-items: center; padding: 24px 16px; background: var(--lq-bg); }
        .lq-login__box { width: 100%; max-width: 380px; }
        .lq-login__brand { display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 20px; color: var(--lq-text); }
        /* the sidebar paints the name white (dark background); here the page is light */
        .lq-login .lq-login__brand .lq-brand__name { font-family: Alexandria, sans-serif; font-size: 1.5rem; font-weight: 700; color: var(--lq-text); }
        .lq-login form { display: grid; gap: 16px; }
        .lq-login__remember { display: flex; align-items: center; gap: 8px; font-size: .9rem; }
    </style>
</head>
<body class="lq-body">
    <main class="lq-login">
        <div class="lq-login__box">
            <div class="lq-login__brand">
                <x-lq.logo />
                <span class="lq-brand__name">لقطة</span>
            </div>
            <div class="lq-card">
                <div class="lq-card__body">
                    <h1 class="lq-card__title" style="margin-bottom: 16px;">تسجيل الدخول</h1>
                    {{-- a failed login: both fields point at the alert (aria-describedby) and the username field gets the focus (autofocus) --}}
                    @php($loginFailed = $errors->any())
                    @if ($loginFailed)
                        <div class="lq-alert lq-alert--danger" role="alert" id="lq-login-error" style="margin-bottom: 16px;">
                            <div class="lq-alert__body">{{ $errors->first() }}</div>
                        </div>
                    @endif
                    <form method="POST" action="{{ route('login.attempt') }}">
                        @csrf
                        <label class="lq-field">
                            <span class="lq-field__label">اسم المستخدم</span>
                            <input class="lq-input" type="text" name="username" value="{{ old('username') }}"
                                   autocomplete="username" autocapitalize="none" spellcheck="false" required autofocus dir="ltr"
                                   @if ($loginFailed) aria-invalid="true" aria-describedby="lq-login-error" @endif>
                        </label>
                        <label class="lq-field">
                            <span class="lq-field__label">كلمة السر</span>
                            <input class="lq-input" type="password" name="password" autocomplete="current-password" required dir="ltr"
                                   @if ($loginFailed) aria-invalid="true" aria-describedby="lq-login-error" @endif>
                        </label>
                        <label class="lq-login__remember">
                            <input type="checkbox" name="remember" value="1">
                            تذكّرني على هالجهاز
                        </label>
                        <button class="lq-btn lq-btn--primary lq-btn--block lq-btn--lg" type="submit">دخول</button>
                    </form>
                </div>
            </div>
        </div>
    </main>
</body>
</html>
