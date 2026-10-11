{{--
    لقطة · اليوم (Today): the Home board and the Run board on one page. OverviewController::index renders it with:
      $live       RunController::snapshot(): /api/batch-status + the current/last run summary + latest rows
      $greeting, $dateLine, $ownerName   server-time fallbacks; public/js/home.js rewrites them in local time
      $todayAdmin   false for a reviewer: no run panel and no sheet tools are rendered (ReviewerLimits blocks their writes)
      $autoPublish  RunController::autoPublishState() (admin only): the worker's auto-publish setting in one sentence
      $sheetTab     RunController::sheetTab() (admin only): «التشغيل بيقرأ من تبويب «X»»
    Order: header · «جهّز لقطة» (setup unfinished) · next step + one alert banner · KPIs · «التشغيل» · funnel · «أدوات الشيت».

    One poll of GET /api/run/live per page:
      admin     public/js/run.js owns it (and «قبل ما تبدأ» from GET /api/run/plan, the tools' reads on their first
                tab show). It sends every snapshot as the `lq:run-live` document event; home.js only listens.
      reviewer  run.js is not loaded; home.js polls /api/run/live itself (the review count and the next step).
    home.js fills the KPIs, the funnel and the provider alerts from GET /api/overview and draws the one banner
    (the run's alert merged with the provider alerts: mergeBanner).
    «بانتظار مراجعتك» is the sidebar badge's number (QueueStats::counters ready_for_review): one source.
    /batch-automation redirects here (RunController::page), keeping its query (?scope=rows&rows=…) and the #fragment.
--}}
@php
    $homeWaiting = ($live['status'] ?? '') === 'success' && is_numeric($live['batch']['ready_for_review'] ?? null)
        ? (int) $live['batch']['ready_for_review'] : null;
    $todayAdmin = $todayAdmin ?? true;
    $todayTools = [
        'run-brands' => 'ماركات ناقصة',
        'run-barcodes' => 'باركودات',
        'run-quality' => 'جودة بيانات الشيت',
        'run-export' => 'تقرير للتحليل',
    ];
@endphp
@extends('layouts.laqta')

@section('title', 'اليوم')
@section('lq_nav', 'home')
@section('body_class', 'lq-page-home')

@push('styles')
    <link rel="stylesheet" href="{{ asset('css/pages/home.css') }}?v={{ @filemtime(public_path('css/pages/home.css')) ?: '1' }}">
    @if ($todayAdmin)
        <link rel="stylesheet" href="{{ asset('css/pages/run.css') }}?v={{ @filemtime(public_path('css/pages/run.css')) ?: '1' }}">
    @endif
@endpush

@section('content')
<div class="lq-home" data-home-page @if ($todayAdmin) data-run-page @endif>
    <header class="lq-page-header">
        <div class="lq-page-header__text">
            <span class="lq-page-header__eyebrow" data-home="date">{{ $dateLine }}</span>
            <h1 class="lq-page-title lq-home__title" data-home="greeting" data-owner="{{ $ownerName }}">{{ $greeting }}</h1>
        </div>
        <div class="lq-page-header__actions lq-home__actions">
            <a class="lq-btn lq-btn--secondary" href="{{ route('dashboard.catalog') }}?mode=bulk" data-home="review-link">افتح قائمة المراجعة<span class="lq-btn__count lq-num" data-home="review-count">{{ $homeWaiting ?? '—' }}</span></a>
        </div>
    </header>

    {{-- «جهّز لقطة»: بس لما التجهيز ناقص (SetupController::homeCard، null لتركيبة مجهّزة أو خالصة). وهو ظاهر، هو الزر الأساسي --}}
    @if ($setupCard ?? null)
        @include('setup.home_card')
    @endif

    {{-- الانتباه: خطوة وحدة (home.js nextStep) وشريط تنبيه واحد (تنبيه التشغيل + تنبيهات الخدمات: mergeBanner) --}}
    <div class="lq-alert lq-alert--info lq-home-next" data-home="next" role="status" hidden>
        <x-lq.icon name="arrow-left" :size="20" class="lq-alert__icon" />
        <div class="lq-alert__body"><strong class="lq-alert__title" data-home="next-title"></strong> <span data-home="next-text"></span></div>
        <a class="lq-btn {{ ($setupCard ?? null) ? 'lq-btn--secondary' : 'lq-btn--primary' }} lq-btn--sm lq-home-next__go" data-home="next-action" href="{{ route('dashboard.catalog') }}"></a>
    </div>

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

    @if ($todayAdmin)
    {{-- «التشغيل»: تشغيل جديد + التشغيل الحالي جنب بعض (عالموبايل فوق بعض، والحالي أول وهو شغّال: run.css) --}}
    <section class="lq-today-run" aria-labelledby="lq-today-run-title">
        <h2 class="lq-section-title" id="lq-today-run-title">التشغيل</h2>
        <div class="lq-run__grid">
            {{-- New run --}}
            <section class="lq-card lq-run-new" id="run-new" aria-labelledby="lq-run-new-title">
                <h3 class="lq-card__title" id="lq-run-new-title">تشغيل جديد</h3>

                <x-lq.segmented label="النطاق" :options="['all' => 'كل الشيت', 'brand' => 'ماركة', 'rows' => 'صفوف محددة']" value="all" fill data-run="scope" />

                <label class="lq-field" data-run="brand-field" hidden>
                    <span class="lq-field__label">الماركة</span>
                    <select class="lq-select" data-run="brand">
                        <option value="">لحظة، عم نقرأ الماركات…</option>
                    </select>
                </label>

                <div class="lq-field" data-run="rows-field" hidden>
                    <label class="lq-field__label" for="lq-run-rows">الصفوف</label>
                    <input class="lq-input lq-run-new__rows" id="lq-run-rows" type="text" dir="ltr" inputmode="numeric" autocomplete="off" placeholder="62-101" aria-describedby="lq-run-rows-hint" data-run="rows">
                    <span class="lq-field__hint" id="lq-run-rows-hint">نطاق مثل 62-101، أو أرقام مفصولة بفواصل مثل 5, 8, 12.</span>
                    <span class="lq-field__error" role="alert" data-run="rows-error" hidden></span>
                </div>

                <div class="lq-run-plan" data-run="plan" data-state="loading" aria-live="polite" aria-busy="true">
                    <span class="lq-run-plan__eyebrow">قبل ما تبدأ</span>
                    <span class="lq-run-plan__skeleton" data-run="plan-skeleton" aria-hidden="true">
                        <span class="lq-skeleton lq-skeleton--title"></span>
                        <span class="lq-skeleton lq-skeleton--text"></span>
                    </span>
                    <span class="lq-run-plan__headline" data-run="plan-headline" hidden></span>
                    <span class="lq-run-plan__detail" data-run="plan-detail" hidden></span>
                </div>

                <details class="lq-run-advanced">
                    <summary>خيارات متقدمة</summary>
                    <div class="lq-run-advanced__body">
                        <label class="lq-check"><input type="checkbox" data-run="force"><span>إعادة البحث حتى للمنتجات اللي إلها صورة نهائية</span></label>
                        <p class="lq-run-advanced__hint">الصورة المنشورة بتضل مكانها لحد ما تعتمد غيرها، وما بينكتب أبداً فوق صورة اعتمدها مراجع.</p>
                        <label class="lq-check"><input type="checkbox" data-run="skip-cache"><span>تجاهل النتائج المحفوظة والبحث من جديد</span></label>
                        <p class="lq-run-advanced__hint">ما منستعمل الصور اللي انلقت قبل هيك، فالبحث أبطأ وأغلى شوي.</p>
                    </div>
                </details>

                <div class="lq-run-autopub" data-run="autopub" data-enabled="{{ $autoPublish['enabled'] ? 'true' : 'false' }}">
                    <x-lq.icon name="shield" :size="18" class="lq-run-autopub__icon" />
                    <span>{{ $autoPublish['text'] }} <a class="lq-link" href="{{ route('dashboard.settings') }}?tab=auto-publish">تغيير</a></span>
                </div>

                {{-- The tab the run reads (RunController::sheetTab, never a Google read): with no tab set it is the first tab --}}
                <div class="lq-run-tab" data-run="sheet-tab" data-suspicious="{{ $sheetTab['suspicious'] ? 'true' : 'false' }}">
                    <x-lq.icon name="grid" :size="16" class="lq-run-tab__icon" />
                    <span><bdi>{{ $sheetTab['text'] }}</bdi>@if ($sheetTab['suspicious']) — إذا مش تبويب منتجاتك، اختار التبويب الصح من الإعدادات@endif <a class="lq-link" href="{{ route('dashboard.settings') }}?tab=sheet">تغيير</a></span>
                </div>

                <div class="lq-run-new__start">
                    <x-lq.button variant="primary" size="lg" icon="play" block data-run="start"><span data-run="start-text">ابدأ التشغيل</span></x-lq.button>
                    <p class="lq-field__error" role="alert" data-run="start-error" hidden></p>
                    <p class="lq-run-new__note" data-run="start-note" hidden>في تشغيل شغّال هلق. فيك تبدأ واحد جديد لما يخلص أو توقفه.</p>
                </div>
            </section>

            {{-- Current run (when idle: the last run's summary and tiles) --}}
            <section class="lq-card lq-run-current" id="run-current" aria-labelledby="lq-run-current-title" data-run="current" data-state="loading">
                <div class="lq-run-current__head">
                    <div class="lq-run-current__title">
                        <h3 class="lq-card__title" id="lq-run-current-title">التشغيل الحالي</h3>
                        <span class="lq-chip lq-chip--sm lq-chip--none" data-run="chip" aria-live="polite"><span class="lq-chip__dot" aria-hidden="true"></span><span data-run="chip-text">لحظة…</span></span>
                    </div>
                    <span class="lq-card__meta lq-num" data-run="meta"></span>
                    {{-- قارئ الشاشة: التقدّم بينقال كل ربع (25٪، 50٪، 75٪)، مش مع كل سؤال (run.js sayProgress) --}}
                    <p class="lq-sr-only" role="status" data-run="progress-say"></p>
                </div>

                <div class="lq-stack lq-stack--sm" data-run="loading" aria-hidden="true">
                    <span class="lq-skeleton lq-skeleton--title"></span>
                    <span class="lq-skeleton lq-skeleton--text"></span>
                    <span class="lq-skeleton lq-skeleton--short"></span>
                </div>

                <div class="lq-alert lq-alert--danger" role="alert" data-run="unavailable" hidden>
                    <x-lq.icon name="alert" :size="18" class="lq-alert__icon" />
                    <div class="lq-alert__body"><strong class="lq-alert__title">ما منعرف حالة التشغيل:</strong> <span data-run="unavailable-text"></span></div>
                </div>

                <div class="lq-empty lq-empty--plain lq-run-empty" data-run="empty" hidden>
                    <span class="lq-empty__icon"><x-lq.icon name="run" :size="24" /></span>
                    <h4 class="lq-empty__title" data-run="empty-title">ما في تشغيل هلق</h4>
                    <p class="lq-empty__text" data-run="empty-text"></p>
                </div>

                <div class="lq-run-progress" data-run="progress" hidden>
                    <div class="lq-run-progress__head">
                        <span class="lq-run-progress__big lq-num"><span data-run="done">—</span> <span class="lq-run-progress__of" data-run="of"></span></span>
                        <span class="lq-run-progress__remaining" data-run="remaining"></span>
                    </div>
                    <div class="lq-progress lq-progress--lg lq-progress--stacked" role="progressbar" aria-label="تقدّم التشغيل" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" data-run="bar"></div>
                    <ul class="lq-legend" data-run="legend"></ul>
                    <p class="lq-run-progress__phase" data-run="phase-text" hidden></p>
                    <div class="lq-run-controls">
                        <button type="button" class="lq-btn lq-btn--secondary lq-btn--sm" data-run="pause" data-action="pause">
                            <x-lq.icon name="pause" :size="16" :stroke="2" data-run="pause-icon-pause" />
                            <x-lq.icon name="play" :size="16" :stroke="2" data-run="pause-icon-play" hidden />
                            <span data-run="pause-text">إيقاف مؤقت</span>
                        </button>
                        <button type="button" class="lq-btn lq-btn--danger lq-btn--sm" data-run="stop">
                            <x-lq.icon name="stop" :size="16" :stroke="2" />
                            <span data-run="stop-text">إيقاف</span>
                        </button>
                        <span class="lq-run-controls__note">الإيقاف ما بيحذف شي: المنتج اللي قيد البحث بيرجع للطابور.</span>
                    </div>
                    {{-- ما خلص التشغيل، بس في صور جاهزة: المراجعة بتبلّش هلق (run.js: progress.reviewNow) --}}
                    <div class="lq-run-handoff">
                        <a class="lq-btn lq-btn--primary lq-btn--sm" href="{{ route('dashboard.catalog') }}?mode=bulk" data-run="review-now" hidden><x-lq.icon name="review" :size="16" :stroke="2" /><span data-run="review-now-text">راجع الجاهز هلق</span></a>
                        <button type="button" class="lq-btn lq-btn--ghost lq-btn--sm" data-run="notify" hidden>نبّهني لما يخلص</button>
                    </div>
                </div>

                <div class="lq-run-finished" data-run="finished" hidden>
                    <p class="lq-run-finished__title" data-run="finished-title"></p>
                    <div class="lq-run-tiles" data-run="tiles"></div>
                    <p class="lq-run-finished__explain" data-run="explain" hidden></p>
                    <x-lq.button variant="primary" icon="review" href="{{ route('dashboard.catalog') }}?mode=bulk" data-run="review" hidden><span data-run="review-text">راجع النتائج</span></x-lq.button>
                </div>

                <div class="lq-alert lq-alert--warning" data-run="stuck" hidden>
                    <x-lq.icon name="alert" :size="18" class="lq-alert__icon" />
                    <div class="lq-alert__body">
                        <strong class="lq-alert__title">التشغيل عالق:</strong> <span data-run="stuck-reason"></span>
                        <p class="lq-run-stuck__what">«إصلاح تشغيل عالق» بيرجّع المنتجات العالقة للطابور، وما بيحذف ولا منتج ولا قرار مراجعة.</p>
                        <details><summary class="lq-link">تفاصيل</summary><p class="lq-field__hint">بيوقف أي عامل عالق، وبيمسح حالة التشغيل والتنبيه، وبيرجّع المنتجات العالقة للطابور. ما بيحذف ولا منتج جاهز للمراجعة أو معتمد أو فاشل، ولا أي اقتراح أو قرار مراجعة.</p></details>
                        <button type="button" class="lq-btn lq-btn--secondary lq-btn--sm" data-run="reset"><x-lq.icon name="refresh" :size="16" :stroke="2" />إصلاح تشغيل عالق</button>
                    </div>
                </div>

                <div class="lq-run-recent">
                    <div class="lq-run-recent__head">
                        <h4 class="lq-card__title">آخر النتائج</h4>
                        <a class="lq-link" href="{{ route('dashboard.catalog') }}">افتح بالمراجعة</a>
                    </div>
                    <ol class="lq-run-recent__list" data-run="recent"></ol>
                    <p class="lq-run-recent__empty" data-run="recent-empty" hidden>لسا ما في نتائج بهالتشغيل.</p>
                </div>
            </section>
        </div>
    </section>
    @endif

    <section class="lq-card lq-card--flat" aria-labelledby="lq-home-funnel-title" data-home="funnel" data-state="loading" aria-busy="true">
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

    @if ($todayAdmin)
    {{-- «أدوات الشيت»: كرت واحد بتبويبات (role=tablist، run.js: tabStep وselectTool)، أداة وحدة ظاهرة بالمرة.
         كل لوحة إلها id الكرت القديم (#run-brands …): الروابط و«روح لـ…» بتختار تبويبها. بيانات كل أداة بتنقرا أول ما يبيّن تبويبها --}}
    <section class="lq-card lq-today-tools" id="run-tools" aria-labelledby="lq-today-tools-title" data-run="tools">
        <h2 class="lq-card__title" id="lq-today-tools-title">أدوات الشيت</h2>
        <div class="lq-today-tools__tabs" role="tablist" aria-label="أدوات الشيت">
            @foreach ($todayTools as $toolId => $toolLabel)
                <button type="button" role="tab" class="lq-today-tools__tab" id="{{ $toolId }}-tab" aria-controls="{{ $toolId }}" aria-selected="{{ $loop->first ? 'true' : 'false' }}" @unless ($loop->first) tabindex="-1" @endunless data-run-tool-tab>{{ $toolLabel }}</button>
            @endforeach
        </div>

        {{-- Brands the queue names that Brands Mapping has no row for: read from the queue and the sheet (GET /api/run/brand-suggestions);
             the buttons write only when clicked (POST /api/run/brand-add, /brand-add-all) and «اقترح الموقع الرسمي» costs one search --}}
        <div class="lq-today-tools__panel" id="run-brands" role="tabpanel" aria-labelledby="run-brands-tab" tabindex="0" data-run="brands" data-state="loading">
            <div class="lq-run-brands__head">
                <h3 class="lq-card__title" id="run-brands-title" data-run="brands-title">ماركات ناقصة من جدول الماركات</h3>
                <button type="button" class="lq-btn lq-btn--ghost lq-btn--sm" data-run="brands-refresh">
                    <x-lq.icon name="refresh" :size="16" :stroke="2" />
                    <span>حدّث القائمة</span>
                </button>
            </div>
            <p class="lq-run-brands__lead" data-run="brands-lead" aria-live="polite">لحظة، عم نقرأ الماركات…</p>
            <div class="lq-run-brands__list" data-run="brands-list"></div>
            <div class="lq-run-brands__foot" data-run="brands-foot" hidden>
                <button type="button" class="lq-btn lq-btn--secondary" data-run="brands-add-all">أضف الكل بدون مواقع</button>
                <span class="lq-field__hint">بيكتب كل ماركة بمرادفاتها بس، بدون موقع رسمي، بعد ما تأكّد.</span>
            </div>
            <p class="lq-field__error" role="alert" data-run="brands-error" hidden></p>
            <p class="lq-run-brands__done" role="status" data-run="brands-done" hidden></p>

            {{-- «عبّي جدول الماركات»: one proposal for every missing brand from evidence only (GET /api/run/brand-bulk, no
                 search, no cost); strong ones ticked, weak ones need a tick, none without evidence can be ticked.
                 «اعتمد المحدد» writes the ticked brands' own proposals (POST /api/run/brand-bulk-add), «تراجع» removes one
                 the assistant wrote (POST /api/run/brand-undo), «دوّر عالمواقع الرسمية» costs up to 10 searches --}}
            <div class="lq-run-bulk" data-run="bulk" data-state="idle">
                <div class="lq-run-brands__foot">
                    <button type="button" class="lq-btn lq-btn--primary" data-run="bulk-open">عبّي جدول الماركات</button>
                    <span class="lq-field__hint">اقتراح لكل الماركات الناقصة مرة وحدة، وما بيصرف أي بحث.</span>
                </div>
                <details><summary class="lq-link">تفاصيل</summary><p class="lq-field__hint">الاقتراح من الأدلة اللي عنا بس (المراجعات، فهرس المتاجر، المواقع اللي لقيناها)، وما بينكتب شي قبل ما تعتمده.</p></details>
                <div class="lq-run-bulk__panel" data-run="bulk-panel" hidden>
                    <p class="lq-run-brands__lead" data-run="bulk-lead" aria-live="polite"></p>
                    <div class="lq-run-brands__foot" data-run="bulk-tools" hidden>
                        <button type="button" class="lq-btn lq-btn--ghost lq-btn--sm" data-run="bulk-sites">دوّر عالمواقع الرسمية</button>
                        <span class="lq-field__hint" data-run="bulk-sites-note"></span>
                    </div>
                    <div class="lq-run-barcodes__list lq-run-bulk__list" data-run="bulk-list"></div>
                    <div class="lq-run-brands__foot" data-run="bulk-foot" hidden>
                        <button type="button" class="lq-btn lq-btn--secondary" data-run="bulk-approve">اعتمد المحدد</button>
                        <span class="lq-field__hint" data-run="bulk-count"></span>
                    </div>
                    <div class="lq-run-bulk__added-list" data-run="bulk-added" hidden></div>
                    <p class="lq-field__error" role="alert" data-run="bulk-error" hidden></p>
                    <p class="lq-run-brands__done" role="status" data-run="bulk-done" hidden></p>
                </div>
            </div>
        </div>

        {{-- Barcodes the store pages stated for approved rows with an empty barcode cell: read from the approvals and the
             cached sheet rows (GET /api/run/barcode-suggestions); written only by the button, after a confirm, through the
             identity-checked sheet outbox (POST /api/run/barcode-write), never over a cell that holds a barcode --}}
        <div class="lq-today-tools__panel" id="run-barcodes" role="tabpanel" aria-labelledby="run-barcodes-tab" tabindex="0" data-run="barcodes" data-state="loading" hidden>
            <div class="lq-run-brands__head">
                <h3 class="lq-card__title" id="run-barcodes-title" data-run="barcodes-title">باركودات لقيناها من صفحات المتاجر</h3>
                <button type="button" class="lq-btn lq-btn--ghost lq-btn--sm" data-run="barcodes-refresh">
                    <x-lq.icon name="refresh" :size="16" :stroke="2" />
                    <span>حدّث القائمة</span>
                </button>
            </div>
            <p class="lq-run-brands__lead" data-run="barcodes-lead" aria-live="polite">لحظة، عم نقرأ الباركودات…</p>
            <div class="lq-run-barcodes__list" data-run="barcodes-list"></div>
            <div class="lq-run-barcodes__list lq-run-barcodes__apart" data-run="barcodes-apart" hidden></div>
            <div class="lq-run-brands__foot" data-run="barcodes-foot" hidden>
                <button type="button" class="lq-btn lq-btn--secondary" data-run="barcodes-write">اكتب الباركودات المختارة بالشيت</button>
                <span class="lq-field__hint" data-run="barcodes-count"></span>
            </div>
            <p class="lq-field__error" role="alert" data-run="barcodes-error" hidden></p>
            <p class="lq-run-brands__done" role="status" data-run="barcodes-done" hidden></p>
        </div>

        {{-- Sheet data quality: from the sheet rows already cached (GET /api/run/sheet-quality) --}}
        <div class="lq-today-tools__panel" id="run-quality" role="tabpanel" aria-labelledby="run-quality-tab" tabindex="0" data-run="quality" data-state="loading" hidden>
            <div class="lq-run-quality__head">
                <h3 class="lq-card__title" id="run-quality-title">جودة بيانات الشيت</h3>
                <button type="button" class="lq-btn lq-btn--ghost lq-btn--sm" data-run="quality-refresh">
                    <x-lq.icon name="refresh" :size="16" :stroke="2" />
                    <span>اقرأ الشيت من جديد</span>
                </button>
            </div>
            <p class="lq-run-quality__lead" data-run="quality-lead" aria-live="polite">لحظة، عم نقرأ صفوف الشيت…</p>
            <div class="lq-run-quality__groups" data-run="quality-groups"></div>
        </div>

        {{-- One JSON file of a run for analysis (GET /api/run/export): no new search, no cost, no key --}}
        <div class="lq-today-tools__panel" id="run-export" role="tabpanel" aria-labelledby="run-export-tab" tabindex="0" hidden>
            <h3 class="lq-card__title" id="run-export-title">تقرير للتحليل</h3>
            <p class="lq-run-export__text">ملف واحد بتبعته للمطوّر بدل ما تبعت ملفات؛ ما بيكلّف شي وما فيه أي مفتاح أو كلمة سر.</p>
            <details><summary class="lq-link">تفاصيل</summary><p class="lq-field__hint">فيه كل صفوف التشغيل: القرار، وليش ما في اقتراح، وأفضل 8 صور مع أدلتها، والتكلفة إذا معروفة. ما بيعمل بحث جديد.</p></details>
            <label class="lq-field">
                <span class="lq-field__label">شو بدك تصدّر؟</span>
                <select class="lq-select" data-run="export-scope">
                    <option value="latest">آخر تشغيل</option>
                    <option value="review">كل المنتجات اللي بانتظار المراجعة</option>
                </select>
            </label>
            <div class="lq-run-export__actions">
                <button type="button" class="lq-btn lq-btn--secondary" data-run="export">
                    <span data-run="export-text">تصدير تقرير للتحليل</span>
                </button>
            </div>
            <p class="lq-field__error" role="alert" data-run="export-error" hidden></p>
            <p class="lq-run-export__done" role="status" data-run="export-done" hidden></p>
        </div>
    </section>
    @endif
</div>

<script type="application/json" id="lq-home-initial">@json($live)</script>
@endsection

@push('scripts')
    <script src="{{ asset('js/run-common.js') }}?v={{ @filemtime(public_path('js/run-common.js')) ?: '1' }}"></script>
    @if ($todayAdmin)
        <script src="{{ asset('js/run.js') }}?v={{ @filemtime(public_path('js/run.js')) ?: '1' }}"></script>
    @endif
    <script src="{{ asset('js/home.js') }}?v={{ @filemtime(public_path('js/home.js')) ?: '1' }}"></script>
@endpush
