{{--
    لقطة · الرئيسية (Overview board). OverviewController::index renders it with:
      $live       RunController::snapshot(): /api/batch-status + the current/last run (the sidebar reads the same)
      $greeting, $dateLine, $ownerName   server-time fallbacks; public/js/home.js rewrites them in local time
    home.js keeps the live parts fresh from GET /api/run/live and fills the rest from GET /api/overview.
    «بانتظار مراجعتك» is the sidebar badge's number (QueueStats::counters ready_for_review): one source.
--}}
@php
    $homeWaiting = ($live['status'] ?? '') === 'success' && is_numeric($live['batch']['ready_for_review'] ?? null)
        ? (int) $live['batch']['ready_for_review'] : null;
@endphp
@extends('layouts.laqta')

@section('title', 'الرئيسية')
@section('lq_nav', 'home')
@section('body_class', 'lq-page-home')

@push('styles')
    <link rel="stylesheet" href="{{ asset('css/pages/home.css') }}?v={{ @filemtime(public_path('css/pages/home.css')) ?: '1' }}">
@endpush

@section('content')
<div class="lq-home" data-home-page>
    <header class="lq-page-header">
        <div class="lq-page-header__text">
            <span class="lq-page-header__eyebrow" data-home="date">{{ $dateLine }}</span>
            <h1 class="lq-page-title lq-home__title" data-home="greeting" data-owner="{{ $ownerName }}">{{ $greeting }}</h1>
        </div>
        <div class="lq-page-header__actions lq-home__actions">
            <x-lq.button variant="secondary" icon="play" href="{{ route('dashboard.batch_automation') }}">تشغيل جديد</x-lq.button>
            <a class="lq-btn lq-btn--primary" href="{{ route('dashboard.catalog') }}" data-home="review-link">افتح قائمة المراجعة<span class="lq-btn__count lq-num" data-home="review-count">{{ $homeWaiting ?? '—' }}</span></a>
        </div>
    </header>

    <div class="lq-alert lq-alert--danger lq-alert--banner" role="alert" data-home="alert" hidden>
        <x-lq.icon name="alert" :size="20" class="lq-alert__icon" />
        <div class="lq-alert__body"><strong class="lq-alert__title" data-home="alert-title"></strong> <span data-home="alert-text"></span></div>
        <a class="lq-alert__action" href="{{ route('dashboard.diagnostics') }}">التفاصيل</a>
    </div>

    <div class="lq-grid lq-grid--4 lq-home__kpis">
        @foreach (\App\Http\Controllers\OverviewController::KPI_TILES as $kpiKey => $kpi)
            <a class="lq-kpi" href="{{ url($kpi['href']) }}" data-kpi="{{ $kpiKey }}" aria-busy="true">
                <span class="lq-kpi__label"><span class="lq-dot lq-dot--{{ $kpi['dot'] }}" aria-hidden="true"></span>{{ $kpi['label'] }}</span>
                <span class="lq-kpi__value" data-kpi-value>@if ($kpiKey === 'waiting' && $homeWaiting !== null){{ $homeWaiting }}@else<span class="lq-skeleton lq-home__skel-value" aria-hidden="true"></span>@endif</span>
                <span class="lq-kpi__note lq-kpi__note--muted" data-kpi-note><span class="lq-skeleton lq-skeleton--short" aria-hidden="true"></span></span>
            </a>
        @endforeach
    </div>

    <section class="lq-card" aria-labelledby="lq-home-funnel-title" data-home="funnel" data-state="loading" aria-busy="true">
        <div class="lq-card__header">
            <h2 class="lq-card__title" id="lq-home-funnel-title">وين وصلت منتجات الشيت</h2>
            <span class="lq-card__meta lq-num" data-home="funnel-meta"></span>
        </div>
        <div class="lq-stack lq-stack--sm" data-home="funnel-loading" aria-hidden="true">
            <span class="lq-skeleton lq-home__skel-bar"></span>
            <span class="lq-skeleton lq-skeleton--short"></span>
        </div>
        <div class="lq-alert lq-alert--neutral" data-home="funnel-error" hidden>
            <x-lq.icon name="info" :size="18" class="lq-alert__icon" />
            <div class="lq-alert__body" data-home="funnel-error-text"></div>
        </div>
        <div class="lq-home-funnel__body" data-home="funnel-body" hidden>
            <div class="lq-progress lq-progress--xl lq-progress--stacked" role="img" aria-label="وين وصلت منتجات الشيت" data-home="funnel-bar"></div>
            <ul class="lq-legend lq-home-funnel__legend" data-home="funnel-legend"></ul>
            <div class="lq-home-funnel__notes" data-home="funnel-notes" hidden></div>
        </div>
    </section>

    <div class="lq-home__grid">
        <section class="lq-card" aria-labelledby="lq-home-lastrun-title" data-home="lastrun" data-mode="loading" aria-busy="true">
            <div class="lq-card__header">
                <h2 class="lq-card__title" id="lq-home-lastrun-title" data-home="lastrun-title">آخر تشغيل</h2>
                <span class="lq-card__meta lq-num" data-home="lastrun-meta"></span>
            </div>
            <div class="lq-home-tiles" data-home="lastrun-loading" aria-hidden="true">
                @for ($i = 0; $i < 4; $i++)
                    <span class="lq-skeleton lq-home__skel-tile"></span>
                @endfor
            </div>
            <div class="lq-home-lastrun__live" data-home="lastrun-live" hidden>
                <div class="lq-progress-block__head">
                    <span class="lq-num" data-home="live-counts"></span>
                    <span class="lq-progress-block__meta" data-home="live-percent"></span>
                </div>
                <div class="lq-progress lq-progress--lg" role="progressbar" aria-label="تقدّم التشغيل الحالي" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" data-home="live-bar"><div class="lq-progress__bar" data-home="live-fill"></div></div>
                <p class="lq-home-lastrun__phase" data-home="live-phase"></p>
                <a class="lq-link" href="{{ route('dashboard.batch_automation') }}">تفاصيل التشغيل ←</a>
            </div>
            <div class="lq-home-lastrun__summary" data-home="lastrun-summary" hidden>
                <div class="lq-home-tiles" data-home="lastrun-tiles"></div>
                <p class="lq-home-lastrun__explain" data-home="lastrun-explain" hidden></p>
            </div>
            <p class="lq-home__message" data-home="lastrun-message" hidden></p>
        </section>

        <section class="lq-card lq-home-ready" aria-labelledby="lq-home-ready-title">
            <div class="lq-card__header">
                <h2 class="lq-card__title" id="lq-home-ready-title">جاهزية النشر الآلي</h2>
                <a class="lq-link lq-home-ready__link" href="{{ route('dashboard.settings') }}?tab=auto-publish">الإعدادات</a>
            </div>
            <p class="lq-home-ready__intro" data-home="ready-intro" hidden></p>
            <div class="lq-stack lq-stack--sm" data-home="ready-loading" aria-hidden="true">
                <span class="lq-skeleton lq-skeleton--text"></span>
                <span class="lq-skeleton lq-skeleton--short"></span>
                <span class="lq-skeleton lq-skeleton--text"></span>
            </div>
            <p class="lq-home__message" data-home="ready-message" hidden></p>
            <ul class="lq-home-ready__list" data-home="ready-list" hidden></ul>
        </section>
    </div>

    <section class="lq-card lq-home-services" aria-labelledby="lq-home-services-title">
        <h2 class="lq-home-services__title" id="lq-home-services-title">الخدمات</h2>
        <ul class="lq-home-services__list" data-home="services" hidden></ul>
        <a class="lq-home-services__checked" href="{{ route('dashboard.diagnostics') }}" data-home="services-checked"><span class="lq-skeleton lq-home__skel-inline" aria-hidden="true"></span></a>
        <span class="lq-home-services__cost">تكلفة هالأسبوع <strong class="lq-num" data-home="cost"><span class="lq-skeleton lq-home__skel-inline" aria-hidden="true"></span></strong></span>
    </section>
</div>

<script type="application/json" id="lq-home-initial">@json($live)</script>
@endsection

@push('scripts')
    <script src="{{ asset('js/run-common.js') }}?v={{ @filemtime(public_path('js/run-common.js')) ?: '1' }}"></script>
    <script src="{{ asset('js/home.js') }}?v={{ @filemtime(public_path('js/home.js')) ?: '1' }}"></script>
@endpush
