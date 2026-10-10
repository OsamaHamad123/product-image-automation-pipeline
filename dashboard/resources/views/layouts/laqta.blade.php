{{--
    لقطة · Laqta Studio — application shell (dark sidebar + main column).

    A page opts in with:
        @extends('layouts.laqta')
        @section('title', 'التشغيل')                     browser tab: "التشغيل · لقطة"
        @section('lq_nav', 'run')                        optional: home | review | run | health | settings
                                                         (by default the active item follows the current route)
        @section('lq_main_class', 'lq-main--flush')      optional: full-bleed work screens (review queue)
        @section('body_class', 'page-run')               optional: extra class on <body>
        @section('content') ... @endsection
        @push('styles') … @endpush                      page CSS in a style tag (the old @section('styles') still works)
        @push('scripts') … @endpush                     page JS in a script tag (the old @section('scripts') still works)
    Optional view data: $lqReviewCount (int) seeds the review badge before the first poll.

    Roles (users.role, ReviewerLimits): a reviewer gets no admin nav item ('admin' => true) and no run-card link;
    <body data-lq-role="reviewer"> hides any element marked data-lq-admin (pages tag their admin-only buttons with it).

    The sidebar run card and the review badge poll GET /api/batch-status every 5 s (15 s on Run and Home, which poll
    /api/run/live themselves; none while the tab is hidden). Pages can reuse that one poll instead of starting their
    own: window.Laqta.onRunStatus(fn) or the `lq:run-status` document event.
    At 980px and below the sidebar is a bottom tab bar: «حسابي» opens the name and «خروج» (the same logout form), and
    the owner's run tab carries a status dot.
--}}
@php
    $lqNavItems = [
        ['key' => 'home', 'route' => 'dashboard.index', 'label' => 'الرئيسية', 'icon' => 'home'],
        ['key' => 'review', 'route' => 'dashboard.catalog', 'label' => 'المراجعة', 'icon' => 'review', 'badge' => true],
        ['key' => 'run', 'route' => 'dashboard.batch_automation', 'label' => 'التشغيل', 'icon' => 'run', 'admin' => true],
        ['key' => 'health', 'route' => 'dashboard.diagnostics', 'label' => 'الصحة والتكلفة', 'short' => 'الصحة', 'icon' => 'health'],
        ['key' => 'settings', 'route' => 'dashboard.settings', 'label' => 'الإعدادات', 'icon' => 'settings', 'admin' => true],
    ];
    // المراجع ما بيشوف روابط صفحات المدير (ReviewerLimits::ADMIN_PAGES بيمنعها عالسيرفر كمان)
    $lqReviewer = (bool) auth()->user()?->isReviewer();
    if ($lqReviewer) {
        $lqNavItems = array_values(array_filter($lqNavItems, fn ($item) => empty($item['admin'])));
    }
    $lqActive = trim($__env->yieldContent('lq_nav'));
    if ($lqActive === '') {
        foreach ($lqNavItems as $lqItem) {
            if (request()->routeIs($lqItem['route'])) {
                $lqActive = $lqItem['key'];
            }
        }
    }
    $lqReviewSeed = isset($lqReviewCount) && is_numeric($lqReviewCount) ? max(0, (int) $lqReviewCount) : null;
    $lqCssVersion = @filemtime(public_path('css/laqta.css')) ?: '1';
@endphp
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="csrf-token" content="{{ csrf_token() }}">
    <meta name="color-scheme" content="light">
    <meta name="theme-color" content="#0B2226">
    <link rel="icon" type="image/svg+xml" href="{{ asset('favicon.svg') }}">
    <title>@hasSection('title')@yield('title') · لقطة@else لقطة · استوديو صور المنتجات@endif</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Alexandria:wght@500;600;700&family=Readex+Pro:wght@400;500;600;700&display=swap">
    <link rel="stylesheet" href="{{ asset('css/laqta.css') }}?v={{ $lqCssVersion }}">
    <style>body[data-lq-role="reviewer"] [data-lq-admin] { display: none !important; }</style>
    @yield('styles')
    @stack('styles')
</head>
<body class="lq-body @yield('body_class')" data-lq-role="{{ $lqReviewer ? 'reviewer' : 'admin' }}" data-lq-status-url="{{ url('/api/batch-status') }}" data-lq-review-url="{{ route('dashboard.catalog') }}" data-lq-tz="{{ \App\Http\Controllers\ReviewController::displayTimezone() }}">
    <a class="lq-skip-link" href="#lq-main">تخطَّ إلى المحتوى</a>

    <div class="lq-shell">
        <aside class="lq-sidebar" aria-label="الشريط الجانبي">
            <div class="lq-sidebar__inner">
                <a class="lq-brand" href="{{ route('dashboard.index') }}">
                    <x-lq.logo />
                    <span class="lq-brand__text">
                        <span class="lq-brand__name">لقطة</span>
                        <span class="lq-brand__tagline">استوديو صور المنتجات</span>
                    </span>
                </a>

                {{-- «روح لـ…» (jump.js): أي صفحة أو كرت أو إعداد أو منتج بكبسة، من غير صفحات زيادة --}}
                <button type="button" class="lq-goto-open" data-lq-goto-open aria-keyshortcuts="Control+K /">
                    <x-lq.icon name="search" :size="16" />
                    <span class="lq-goto-open__label">روح لـ…</span>
                    <kbd dir="ltr">Ctrl K</kbd>
                </button>

                <nav class="lq-nav" aria-label="التنقل الرئيسي">
                    @foreach ($lqNavItems as $lqItem)
                        <a href="{{ route($lqItem['route']) }}" @class(['lq-nav__item', 'is-active' => $lqActive === $lqItem['key']]) @if ($lqActive === $lqItem['key']) aria-current="page" @endif>
                            <x-lq.icon :name="$lqItem['icon']" />
                            <span class="lq-nav__label">{{ $lqItem['label'] }}</span>
                            @if (! empty($lqItem['short']))<span class="lq-nav__short" aria-hidden="true">{{ $lqItem['short'] }}</span>@endif
                            @if (! empty($lqItem['badge']))
                                <span class="lq-nav__badge" data-lq-review-count @if ($lqReviewSeed === null || $lqReviewSeed === 0) hidden @endif>{{ $lqReviewSeed }}</span>
                            @endif
                            @if ($lqItem['key'] === 'run')
                                {{-- نقطة حالة التشغيل بشريط الموبايل (الكرت الجانبي مخفي هناك): للمدير بس، والسكربت بيحدّثها مع /api/batch-status --}}
                                <span class="lq-nav__dot" data-lq-run-dot data-lq-admin data-state="loading" role="img" aria-label="حالة التشغيل: لحظة…"></span>
                            @endif
                        </a>
                    @endforeach
                    @auth
                        {{-- «حسابي»: بشريط الموبايل بس (980px ونازل)، لأنه الاسم و«خروج» تحت الشريط الجانبي بيختفوا هناك --}}
                        <button type="button" class="lq-nav__item lq-nav__account" data-lq-account-toggle aria-haspopup="dialog" aria-expanded="false" aria-controls="lq-account-sheet">
                            <x-lq.icon name="user" />
                            <span class="lq-nav__label">حسابي</span>
                        </button>
                    @endauth
                </nav>

                <div class="lq-sidebar__spacer"></div>

                <x-lq.run-card live state="loading" :link="! $lqReviewer" />

                @auth
                    <form class="lq-sidebar__user" id="lqLogoutForm" method="POST" action="{{ route('logout') }}">
                        @csrf
                        <span class="lq-sidebar__user-name" dir="ltr">{{ auth()->user()->name }}</span>
                        <button class="lq-btn lq-btn--ghost lq-btn--sm" type="submit">خروج</button>
                    </form>
                @endauth
            </div>
        </aside>

        @auth
            {{-- قائمة «حسابي» (الموبايل): «خروج» بيبعت نموذج الخروج اللي فوق (form=، POST مع CSRF) حتى وهو مخفي --}}
            <div class="lq-account-sheet" id="lq-account-sheet" role="dialog" aria-label="حسابي" data-lq-account-sheet hidden>
                <span class="lq-account-sheet__label">داخل باسم</span>
                <span class="lq-account-sheet__name" dir="ltr">{{ auth()->user()->name }}</span>
                <span class="lq-account-sheet__role">{{ $lqReviewer ? 'مراجع' : 'مدير' }}</span>
                <button class="lq-btn lq-btn--secondary lq-account-sheet__goto" type="button" data-lq-goto-open>روح لـ… (صفحة، إعداد، أو منتج)</button>
                <button class="lq-btn lq-btn--secondary lq-account-sheet__logout" type="submit" form="lqLogoutForm">خروج</button>
            </div>
        @endauth

        <main id="lq-main" class="lq-main @yield('lq_main_class')" tabindex="-1">
            @yield('content')
        </main>
    </div>

    <div class="lq-toast-region" data-lq-toasts role="status" aria-live="polite"></div>

    <script type="application/json" id="lqGotoIndex">@json(\App\Services\GotoIndex::entries($lqReviewer))</script>
    <script src="{{ asset('js/layout.js') }}?v={{ @filemtime(public_path('js/layout.js')) ?: '1' }}"></script>
    <script src="{{ asset('js/jump.js') }}?v={{ @filemtime(public_path('js/jump.js')) ?: '1' }}"></script>
    @yield('scripts')
    @stack('scripts')
</body>
</html>
