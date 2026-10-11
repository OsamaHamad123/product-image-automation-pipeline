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

    The sidebar run card and the review badge poll GET /api/batch-status every 5 s (15 s on «اليوم», which polls
    /api/run/live itself; none while the tab is hidden). Pages can reuse that one poll instead of starting their
    own: window.Laqta.onRunStatus(fn) or the `lq:run-status` document event.
    «التنبيهات» (js/notices.js): the bell next to «روح لـ…» (and in «حسابي» on the phone) opens a panel with the last
    30 events, kept in this browser; it listens to that same poll and asks /api/system/attention at most every 5 min.
    Theme: <html data-theme="light|dark"> (none = follow the system), set before the first paint by js/theme-boot.js
    from localStorage «laqta.theme»; the «فاتح / غامق / تلقائي» switch (sidebar foot, and «حسابي» on the phone) calls
    window.Laqta.setTheme. laqta.css swaps only the colour tokens.
    At 980px and below the sidebar is a bottom tab bar: «حسابي» opens the name and «خروج» (the same logout form), and
    the owner's run tab carries a status dot.
--}}
@php
    $lqNavItems = [
        ['key' => 'home', 'route' => 'dashboard.index', 'label' => 'اليوم', 'icon' => 'home'],
        ['key' => 'review', 'route' => 'dashboard.catalog', 'label' => 'المراجعة', 'icon' => 'review', 'badge' => true],
        ['key' => 'system', 'route' => 'dashboard.system', 'label' => 'النظام', 'icon' => 'settings'],
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
    // المظهر: layout.js (setTheme) بيحفظ الاختيار بـ localStorage (laqta.theme) وبيعلّم الزر المختار
    $lqThemes = ['light' => 'فاتح', 'dark' => 'غامق', 'system' => 'تلقائي'];
@endphp
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="csrf-token" content="{{ csrf_token() }}">
    <meta name="color-scheme" content="light dark">
    <meta name="theme-color" content="#0B2226">
    {{-- المظهر (فاتح / غامق / تلقائي) قبل أول رسم: سكربت صغير متزامن قبل الـ CSS، بيقرأ laqta.theme --}}
    <script src="{{ asset('js/theme-boot.js') }}?v={{ @filemtime(public_path('js/theme-boot.js')) ?: '1' }}"></script>
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
<body class="lq-body @yield('body_class')" data-lq-role="{{ $lqReviewer ? 'reviewer' : 'admin' }}" data-lq-status-url="{{ url('/api/batch-status') }}" data-lq-review-url="{{ route('dashboard.catalog') }}" data-lq-home-url="{{ route('dashboard.index') }}" data-lq-attention-url="{{ url('/api/system/attention') }}" data-lq-tz="{{ \App\Http\Controllers\ReviewController::displayTimezone() }}">
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
                {{-- «التنبيهات» (notices.js): لوحة صغيرة فيها آخر شي صار، مش صفحة؛ الرقم = الجديد من آخر مرة انفتحت --}}
                <button type="button" class="lq-notices-open" data-lq-notices-open aria-haspopup="dialog" aria-expanded="false" aria-controls="lq-notices">
                    <x-lq.icon name="bell" :size="16" />
                    <span class="lq-notices-open__label">التنبيهات</span>
                    <span class="lq-notices-open__badge" data-lq-notices-count hidden></span>
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
                            @if ($lqItem['key'] === 'home' && ! $lqReviewer)
                                {{-- نقطة حالة التشغيل على «اليوم» (فيها لوحة التشغيل) بشريط الموبايل (الكرت الجانبي مخفي هناك): للمدير بس، والسكربت بيحدّثها مع /api/batch-status --}}
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
                        <x-lq.segmented label="المظهر" :options="$lqThemes" value="system" size="sm" class="lq-theme-switch lq-segmented--ink" data-lq-theme-switch />
                        <span class="lq-sidebar__user-name" dir="ltr">{{ auth()->user()->name }}</span>
                        <button class="lq-btn lq-btn--ghost lq-btn--sm" type="submit">خروج</button>
                    </form>
                @endauth
            </div>
        </aside>

        @auth
            {{-- قائمة «حسابي» (الموبايل): «خروج» بيبعت نموذج الخروج اللي فوق (form=، POST مع CSRF) حتى وهو مخفي.
                 aria-modal: layout.js بيلف Tab بين زرّيها وEsc بيرجّع لزر «حسابي»؛ وهي مسكّرة (hidden) jump.js ما بيحسبها --}}
            <div class="lq-account-sheet" id="lq-account-sheet" role="dialog" aria-modal="true" aria-label="حسابي" data-lq-account-sheet hidden>
                <span class="lq-account-sheet__label">داخل باسم</span>
                <span class="lq-account-sheet__name" dir="ltr">{{ auth()->user()->name }}</span>
                <span class="lq-account-sheet__role">{{ $lqReviewer ? 'مراجع' : 'مدير' }}</span>
                <button class="lq-btn lq-btn--secondary lq-account-sheet__goto" type="button" data-lq-goto-open>روح لـ… (صفحة، إعداد، أو منتج)</button>
                <button class="lq-btn lq-btn--secondary lq-account-sheet__notices" type="button" data-lq-notices-open aria-haspopup="dialog" aria-expanded="false" aria-controls="lq-notices">
                    التنبيهات <span class="lq-notices-open__badge" data-lq-notices-count hidden></span>
                </button>
                <x-lq.segmented label="المظهر" :options="$lqThemes" value="system" size="sm" class="lq-theme-switch" data-lq-theme-switch />
                <button class="lq-btn lq-btn--secondary lq-account-sheet__logout" type="submit" form="lqLogoutForm">خروج</button>
            </div>
        @endauth

        {{-- «التنبيهات» (notices.js): آخر 30 شي صار (تشغيل بلّش أو خلص أو وقف، منتجات وصلت للمراجعة، اعتماد ما زبط،
             و«شو بدو منك»)، محفوظين بهالمتصفح. aria-modal: Tab بيضل جوّاها وEsc بيرجّع التركيز لزر التنبيهات --}}
        <div class="lq-notices" id="lq-notices" role="dialog" aria-modal="true" aria-labelledby="lqNoticesTitle" data-lq-notices hidden>
            <div class="lq-notices__head">
                <h2 class="lq-notices__title" id="lqNoticesTitle">التنبيهات</h2>
                <button class="lq-btn lq-btn--ghost lq-btn--sm lq-btn--icon" type="button" data-lq-notices-close aria-label="سكّر">
                    <x-lq.icon name="x" :size="16" />
                </button>
            </div>
            <ol class="lq-notices__list" data-lq-notices-list></ol>
            <p class="lq-notices__empty" data-lq-notices-empty>ما في تنبيهات هلق. لما يبلّش أو يخلص تشغيل، أو يوصل شي للمراجعة، بيبيّن هون.</p>
            <div class="lq-notices__foot">
                <button class="lq-btn lq-btn--secondary lq-btn--sm" type="button" data-lq-notices-read hidden>علّم الكل مقروء</button>
                <button class="lq-btn lq-btn--ghost lq-btn--sm" type="button" data-lq-notices-clear hidden>امسح</button>
                <span class="lq-notices__note">محفوظة على هالمتصفح بس</span>
            </div>
        </div>

        <main id="lq-main" class="lq-main @yield('lq_main_class')" tabindex="-1">
            @yield('content')
        </main>
    </div>

    <div class="lq-toast-region" data-lq-toasts role="status" aria-live="polite"></div>

    <script type="application/json" id="lqGotoIndex">@json(\App\Services\GotoIndex::entries($lqReviewer))</script>
    <script src="{{ asset('js/layout.js') }}?v={{ @filemtime(public_path('js/layout.js')) ?: '1' }}"></script>
    <script src="{{ asset('js/notices.js') }}?v={{ @filemtime(public_path('js/notices.js')) ?: '1' }}"></script>
    <script src="{{ asset('js/jump.js') }}?v={{ @filemtime(public_path('js/jump.js')) ?: '1' }}"></script>
    @yield('scripts')
    @stack('scripts')
</body>
</html>
