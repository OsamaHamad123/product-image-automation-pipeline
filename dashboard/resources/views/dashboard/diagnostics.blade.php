{{--
    لقطة · الصحة والتكلفة (Health board). HealthController::page renders it with:
      $lastDiagnostics  the saved result of the last connection check (temp/diagnostics_last.json) or null
      $services         HealthController::serviceCards(): the five service cards from that result
      $optional         configured optional services (proxy, legacy Custom Search), one muted line
      $checkedAt        epoch of the last check or null;  $allOk  false when a critical service failed
      $lastRun          HealthController::lastRunCard(): the last run (temp/nightly/last_report.json), or null
    Opening the page never runs the check (a paid Serper query and a PhotoRoom call): the button does.
    public/js/health.js runs the check on click, loads «عمليات البحث» from GET /api/system/ops-health and
    the log tails from GET /api/view-pipeline-log, /api/view-laravel-log and /api/view-nightly-log.
--}}
@extends('layouts.laqta')

@section('title', 'الصحة والتكلفة')
@section('lq_nav', 'health')
@section('body_class', 'lq-page-health')

@push('styles')
    <link rel="stylesheet" href="{{ asset('css/pages/health.css') }}?v={{ @filemtime(public_path('css/pages/health.css')) ?: '1' }}">
@endpush

@php
    $healthDots = ['success' => 'lq-dot--success', 'danger' => 'lq-dot--danger', 'warning' => 'lq-dot--warning', 'muted' => 'lq-dot--muted'];
@endphp

@section('content')
<div class="lq-health" data-health-page data-settings-url="{{ route('dashboard.settings') }}">
    <header class="lq-page-header lq-health__header">
        <div class="lq-page-header__text">
            <h1 class="lq-page-title">الصحة والتكلفة</h1>
            <p class="lq-page-header__desc" data-health="checked-line">
                <span data-health="checked-text">@if ($checkedAt)آخر فحص للاتصالات: <time datetime="{{ gmdate('c', $checkedAt) }}" data-health="checked-at">{{ date('Y-m-d H:i', $checkedAt) }}</time>@else لسا ما انعمل فحص للاتصالات.@endif</span><span class="lq-health__warn" data-health="checked-warn" @if ($allOk !== false) hidden @endif> في خدمات أساسية ما بتردّ.</span>
                الفحص ما بيشتغل لحاله لما تفتح الصفحة.
            </p>
        </div>
        <div class="lq-health__check">
            <x-lq.button variant="secondary" size="lg" icon="refresh" data-health="run-check"><span data-health="run-check-label">فحص الاتصالات الآن</span></x-lq.button>
            <span class="lq-health__check-note" id="diagCheckNote">بيستخدم استعلام Serper واحد وطلب PhotoRoom واحد، وما بياخد أكتر من 45 ثانية.</span>
        </div>
    </header>

    <script type="application/json" id="lq-health-initial">@json($lastDiagnostics)</script>

    <div class="lq-health__services" data-health="services" aria-live="polite">
        @foreach ($services as $service)
            <article class="lq-health-service" id="card-{{ $service['key'] }}" data-service="{{ $service['key'] }}" data-tone="{{ $service['tone'] }}">
                <div class="lq-health-service__head">
                    <h2 class="lq-health-service__name">{{ $service['name'] }}</h2>
                    <span class="lq-dot lq-dot--lg {{ $healthDots[$service['tone']] ?? 'lq-dot--muted' }}" data-service-dot aria-hidden="true"></span>
                </div>
                <span class="lq-health-service__state" data-service-state>{{ $service['state'] }}</span>
                <span class="lq-health-service__note" data-service-note>{{ $service['note'] }}</span>
                <details class="lq-health-service__details" data-service-details @if ($service['details'] === '') hidden @endif>
                    <summary>التفاصيل</summary>
                    <p dir="auto" data-service-details-text>{{ $service['details'] }}</p>
                </details>
            </article>
        @endforeach
    </div>
    <p class="lq-health__optional" data-health="optional" @if ($optional === []) hidden @endif>
        خدمات اختيارية:
        @foreach ($optional as $item)
            <span class="lq-health__optional-item"><span class="lq-dot {{ $healthDots[$item['tone']] ?? 'lq-dot--muted' }}" aria-hidden="true"></span>{{ $item['name'] }} {{ $item['state'] }}</span>
        @endforeach
    </p>

    @if ($lastRun ?? null)
        {{-- آخر تشغيل (HealthController::lastRunCard من التقرير الذي يكتبه run_report.py بعد كل تشغيل) --}}
        <section class="lq-card lq-card--compact" aria-label="آخر تشغيل" data-health-last-run>
            <p class="lq-card__meta">آخر تشغيل @if ($lastRun['when'] !== '')· <time>{{ $lastRun['when'] }}</time>@endif</p>
            <h2 class="lq-card__title"><span class="lq-dot {{ $healthDots[$lastRun['tone']] ?? 'lq-dot--muted' }}" aria-hidden="true"></span> {{ $lastRun['title'] }}</h2>
            @if ($lastRun['summary'] !== '')<p class="lq-health__footnote">{{ $lastRun['summary'] }}</p>@endif
        </section>
    @endif

    <section class="lq-health__search" aria-labelledby="lq-health-search-title">
        <div class="lq-health__search-head">
            <h2 class="lq-section-title" id="lq-health-search-title">عمليات البحث</h2>
            <x-lq.segmented label="الفترة" size="sm" value="24h" :options="['24h' => 'آخر 24 ساعة', '7d' => 'آخر 7 أيام']" data-health="window" />
        </div>

        <div class="lq-health__alerts" data-health="alerts" hidden></div>

        <div class="lq-health__grid" data-health="ops" aria-busy="true">
            <section class="lq-card lq-health-results" aria-labelledby="lq-health-results-title">
                <div class="lq-card__header">
                    <h3 class="lq-card__title" id="lq-health-results-title">النتائج</h3>
                    <span class="lq-card__meta" data-health="results-total"><span class="lq-skeleton lq-health__skel-inline" aria-hidden="true"></span></span>
                </div>
                <div class="lq-health-results__rows" data-health="decisions">
                    @for ($i = 0; $i < 4; $i++)
                        <span class="lq-skeleton lq-skeleton--text" aria-hidden="true"></span>
                    @endfor
                </div>
                <div class="lq-health-providers">
                    <h4 class="lq-health-providers__title">ردود المصادر</h4>
                    <div class="lq-health-providers__rows" data-health="providers">
                        <span class="lq-skeleton lq-skeleton--short" aria-hidden="true"></span>
                    </div>
                </div>
            </section>

            <div class="lq-health__side">
                <section class="lq-card lq-health-cost" aria-labelledby="lq-health-cost-title">
                    <h3 class="lq-card__title" id="lq-health-cost-title">التكلفة التقديرية</h3>
                    <span class="lq-health-cost__total" data-health="cost-total"><span class="lq-skeleton lq-skeleton--title" aria-hidden="true"></span></span>
                    <div class="lq-health-cost__lines" data-health="cost-lines"></div>
                    <p class="lq-health-cost__note" data-health="cost-note"></p>
                </section>
                <section class="lq-card lq-health-reasons" aria-labelledby="lq-health-reasons-title">
                    <h3 class="lq-card__title" id="lq-health-reasons-title">أسباب ما لقينا صورة</h3>
                    <div class="lq-health-reasons__rows" data-health="reasons">
                        <span class="lq-skeleton lq-skeleton--text" aria-hidden="true"></span>
                    </div>
                </section>
            </div>
        </div>

        <x-lq.empty-state class="lq-health__ops-empty" data-health="ops-empty" hidden icon="search"
            title="ما في عمليات بحث بآخر 7 أيام"
            text="أول ما تشغّل بحث من صفحة التشغيل، بتبيّن هون النتائج وردود المصادر والتكلفة.">
            <x-lq.button variant="secondary" icon="play" :href="route('dashboard.batch_automation')">صفحة التشغيل</x-lq.button>
        </x-lq.empty-state>

        <div class="lq-alert lq-alert--danger" role="alert" data-health="ops-error" hidden>
            <x-lq.icon name="alert" :size="18" class="lq-alert__icon" />
            <div class="lq-alert__body"><strong class="lq-alert__title">ما قدرنا نقرأ سجل البحث:</strong> <span data-health="ops-error-text">جسر بايثون أو قاعدة البيانات ما ردّ.</span></div>
            <button type="button" class="lq-alert__action lq-health__retry" data-health="ops-retry">جرّب مرة تانية</button>
        </div>

        <p class="lq-health__footnote" data-health="ops-note"></p>
    </section>

    <section class="lq-card lq-card--dark lq-health-log" aria-labelledby="lq-health-log-title">
        <div class="lq-health-log__head">
            <h2 class="lq-card__title" id="lq-health-log-title">السجل</h2>
            <div class="lq-health-log__tabs" role="tablist" aria-label="نوع السجل">
                <button type="button" role="tab" class="lq-health-log__tab" id="tab-pipeline" data-log-tab="pipeline" aria-selected="true" aria-controls="lq-health-log-body">الأتمتة</button>
                <button type="button" role="tab" class="lq-health-log__tab" id="tab-laravel" data-log-tab="laravel" aria-selected="false" aria-controls="lq-health-log-body" tabindex="-1">لوحة التحكم</button>
                <button type="button" role="tab" class="lq-health-log__tab" id="tab-nightly" data-log-tab="nightly" aria-selected="false" aria-controls="lq-health-log-body" tabindex="-1">التشغيل الليلي</button>
            </div>
        </div>
        <div class="lq-health-log__body" id="lq-health-log-body" role="tabpanel" aria-labelledby="tab-pipeline" tabindex="0" data-health="log-body">
            <pre class="lq-log" data-health="log" hidden></pre>
            <p class="lq-health-log__empty" data-health="log-empty">لحظة، عم نقرأ السجل…</p>
        </div>
        <span class="lq-health-log__meta" data-health="log-meta"></span>
    </section>
</div>
@endsection

@push('scripts')
    <script src="{{ asset('js/health.js') }}?v={{ @filemtime(public_path('js/health.js')) ?: '1' }}"></script>
@endpush
