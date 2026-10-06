{{--
    لقطة · الصحة والتكلفة (Health board). HealthController::page renders it with:
      $lastDiagnostics  the saved result of the last connection check (temp/diagnostics_last.json) or null
      $services         HealthController::serviceCards(): the five service cards from that result
      $optional         configured optional services (proxy, legacy Custom Search), one muted line
      $checkedAt        epoch of the last check or null;  $allOk  false when a critical service failed
      $lastRun          HealthController::lastRunCard(): the last run (temp/nightly/last_report.json), or null
      $localIndex       LocalIndexController::card(): the «فهرس المتاجر المحلي» card (per-store pages / last harvest / status,
                        the refresh progress line, how many products the index answered in the last run)
      $lastPublishCheck the saved result of the last «فحص النشر» (temp/publish_check_last.json, redacted) or null
      $publish          HealthController::publishCheckView(): that result as the card's summary and four step rows
      $bg, $bgView      SettingsController::currentBgState() (null without the database) and HealthController::bgSkipView():
                        the «تجاوز عزل الخلفية» / «رجّع عزل الخلفية (…)» box of the card; $bgConfirm its confirm text
    Opening the page never runs the check (a paid Serper query and a PhotoRoom call): the button does. The same goes
    for «فحص النشر» (POST /api/system/publish-check: it may cost one background-removal call).
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
                <span data-health="checked-text">@if ($checkedAt)آخر فحص للاتصالات: <time datetime="{{ gmdate('c', $checkedAt) }}" data-health="checked-at">{{ \App\Http\Controllers\HealthController::stamp((int) $checkedAt) }}</time>@else لسا ما انعمل فحص للاتصالات.@endif</span><span class="lq-health__warn" data-health="checked-warn" @if ($allOk !== false) hidden @endif> في خدمات أساسية ما بتردّ.</span>
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

    {{-- «فحص النشر»: بروفة النشر الحقيقي على صورة تجريبية (HealthController::runPublishCheck -> cli_bridge publish_check).
         الزر وحده يشغّله؛ البطاقة تبدأ من آخر نتيجة محفوظة (HealthController::publishCheckView). شاشة المراجعة تربط
         هون (#publish-check) بعد اعتماد ما مشي. --}}
    <section class="lq-card lq-health-publish" id="publish-check" aria-labelledby="publish-check-title" data-health="publish" data-tone="{{ $publish['tone'] }}">
        <div class="lq-health-publish__head">
            <div class="lq-health-publish__intro">
                <h2 class="lq-card__title" id="publish-check-title">فحص النشر</h2>
                <p class="lq-health-publish__desc">بيجرّب النشر كامل على صورة تجريبية بدون ما يلمس منتجاتك: التنزيل، عزل الخلفية، الرفع، والكتابة بالشيت.</p>
            </div>
            <div class="lq-health-publish__action">
                <x-lq.button variant="primary" icon="play" data-health="publish-run"><span data-health="publish-run-label">افحص النشر</span></x-lq.button>
                <span class="lq-health-publish__cost" id="publishCheckNote">ممكن يكلّف طلب عزل خلفية واحد (ونادراً أكتر إذا ما زبط العزل من أول مرة) وقراءة Gemini وحدة إذا مفتاحها محفوظ. الصورة التجريبية بتنرفع على Cloudinary وبتنمسح، وبالشيت منكتب عنوان عمود الرابط نفسه فوق حاله. عادةً بيخلص بأقل من دقيقة، وما بيطول أكتر من 7 دقايق.</span>
            </div>
        </div>

        <div class="lq-health-publish__summary" data-health="publish-summary-box" role="status" aria-live="polite">
            <span class="lq-dot lq-dot--lg {{ $healthDots[$publish['tone']] ?? 'lq-dot--muted' }}" data-health="publish-dot" aria-hidden="true"></span>
            <div class="lq-health-publish__summary-text">
                <strong data-health="publish-summary">{{ $publish['summary'] }}</strong>
                <span class="lq-health-publish__meta" data-health="publish-meta">@if ($publish['when'] !== '')آخر فحص للنشر: <time>{{ $publish['when'] }}</time>@if ($publish['sample'] !== '') · {{ $publish['sample'] }}@endif @endif</span>
            </div>
        </div>

        <ol class="lq-health-publish__steps" data-health="publish-steps">
            @foreach ($publish['steps'] as $step)
                <li class="lq-health-step" data-step="{{ $step['key'] }}" data-status="{{ $step['status'] }}" @if ($step['code'] !== '') title="{{ $step['code'] }}" @endif>
                    <span class="lq-health-step__icon lq-health-step__icon--{{ $step['tone'] }}" role="img" aria-label="{{ $step['label'] }}" data-step-icon><x-lq.icon :name="$step['icon']" :size="14" :stroke="2.4" /></span>
                    <div class="lq-health-step__body">
                        <div class="lq-health-step__head">
                            <span class="lq-health-step__title" data-step-title>{{ $step['title'] }}</span>
                            <span class="lq-health-step__state" data-step-state>{{ $step['label'] }}</span>
                            <span class="lq-health-step__time" data-step-time>{{ $step['time'] }}</span>
                        </div>
                        <p class="lq-health-step__detail" dir="auto" data-step-detail @if ($step['detail'] === '') hidden @endif>{{ $step['detail'] }}</p>
                        <p class="lq-health-step__todo" dir="auto" data-step-action @if ($step['action'] === '') hidden @endif>{{ $step['action'] }}</p>
                        @if ($step['key'] === 'process')
                            <p class="lq-health-step__hint">هالخطوة ممكن تكلّف طلب عزل خلفية واحد.</p>
                        @endif
                    </div>
                </li>
            @endforeach
        </ol>

        {{-- «تجاوز عزل الخلفية» (HealthController::bgSkipView, the same as bgView in health.js): offer = the processing step
             failed on PhotoRoom / remove.bg credit, key or quota; off = the method is «بدون عزل الخلفية», with a button that
             restores the previous method. Both POST /api/settings/bg-method; nothing is re-checked automatically. --}}
        <div class="lq-health-bg" data-health="bg-box" data-state="{{ $bgView['state'] }}" data-method="{{ $bg['method'] ?? '' }}" data-previous="{{ $bg['previous'] ?? '' }}" @if ($bgView['state'] === 'hidden') hidden @endif>
            <p class="lq-health-bg__text" dir="auto" data-health="bg-text">{{ $bgView['text'] }}</p>
            <div class="lq-health-bg__actions">
                <button type="button" class="lq-btn lq-btn--danger lq-btn--sm" data-health="bg-skip" data-confirm="{{ $bgConfirm }}" @if ($bgView['state'] !== 'offer') hidden @endif>تجاوز عزل الخلفية</button>
                <button type="button" class="lq-btn lq-btn--secondary lq-btn--sm" data-health="bg-restore" data-method="{{ $bgView['restore'] }}" @if ($bgView['state'] !== 'off') hidden @endif><span data-health="bg-restore-label">{{ $bgView['restore_label'] }}</span></button>
            </div>
        </div>
        <p class="lq-health__footnote" data-health="publish-notes">{{ implode(' ', $publish['notes']) }}</p>
    </section>
    <script type="application/json" id="publish-check-initial">@json($lastPublishCheck)</script>

    @if ($lastRun ?? null)
        {{-- آخر تشغيل (HealthController::lastRunCard من التقرير الذي يكتبه run_report.py بعد كل تشغيل) --}}
        <section class="lq-card lq-card--compact" aria-label="آخر تشغيل" data-health-last-run>
            <p class="lq-card__meta">آخر تشغيل @if ($lastRun['when'] !== '')· <time>{{ $lastRun['when'] }}</time>@endif</p>
            <h2 class="lq-card__title"><span class="lq-dot {{ $healthDots[$lastRun['tone']] ?? 'lq-dot--muted' }}" aria-hidden="true"></span> {{ $lastRun['title'] }}</h2>
            @if ($lastRun['summary'] !== '')<p class="lq-health__footnote">{{ $lastRun['summary'] }}</p>@endif
        </section>
    @endif

    {{-- «دقة الاقتراحات الحقيقية»: health.js reads /api/system/review-lanes (review_stats.lanes, cached) --}}
    <section class="lq-card lq-card--compact" aria-label="دقة الاقتراحات الحقيقية">
        <h2 class="lq-card__title">دقة الاقتراحات الحقيقية</h2>
        <p class="lq-card__meta">من مراجعاتك للصور اللي اقترحها البحث، حسب نوع الاقتراح.</p>
        <div class="lq-health-lanes" data-health="lanes" aria-busy="true" aria-live="polite">
            <span class="lq-skeleton lq-health__skel-inline" aria-hidden="true"></span>
        </div>
    </section>

    {{-- «فهرس المتاجر المحلي» (LocalIndexController::card; the page script's createLocalIndex): per store the pages in the
         index, when its sitemaps were last read and its status (تمام / ممنوع / ما انجمع أبداً). «حدّث الفهرس هلق» starts the
         background refresh through the bridge and returns at once; the status line shows the progress on reload. --}}
    <section class="lq-card lq-card--compact" aria-label="فهرس المتاجر المحلي" data-health="index-card">
        <div class="lq-card__header">
            <h2 class="lq-card__title">فهرس المتاجر المحلي</h2>
            <span class="lq-card__meta">{{ $localIndex['total_text'] }}</span>
        </div>
        <p class="lq-card__meta">بيدوّر على صفحات المنتجات من خرايط المتاجر نفسها بمجاني، قبل أي بحث مدفوع. بيتحدّث لحاله بالخلفية أول التشغيل إذا صار أقدم من أسبوع.</p>
        @if (!$localIndex['db'])
            <p class="lq-health__footnote">ما قدرنا نقرأ الفهرس: قاعدة البيانات مش متاحة هلق.</p>
        @endif
        <div class="lq-health-lanes" data-health="index-rows">
            @foreach ($localIndex['rows'] as $row)
                <div class="lq-health-lane" data-store="{{ $row['key'] }}" data-state="{{ $row['state'] }}">
                    <span class="lq-dot {{ $healthDots[$row['tone']] ?? 'lq-dot--muted' }}" aria-hidden="true"></span>
                    <span class="lq-health-lane__label">{{ $row['name'] }}</span>
                    <strong>{{ $row['pages_text'] }}</strong>
                    <span class="lq-health-lane__bound">{{ $row['state_text'] }}@if ($row['when'] !== '') · آخر جمع ناجح {{ $row['when'] }}@endif @if ($row['note'] !== '') · {{ $row['note'] }}@endif @if ($row['visit'] !== '') · {{ $row['visit'] }}@endif</span>
                </div>
            @endforeach
        </div>
        <p class="lq-health__footnote" data-health="index-last-run" @if ($localIndex['last_run'] === null) hidden @endif>{{ $localIndex['last_run'] }}</p>
        <div class="lq-card__header">
            <p class="lq-health__footnote" data-health="index-status" role="status" aria-live="polite">{{ $localIndex['refresh']['text'] }}</p>
            <x-lq.button variant="secondary" size="sm" icon="refresh" data-health="index-refresh" :disabled="$localIndex['refresh']['running']"><span data-health="index-refresh-label">{{ $localIndex['refresh']['running'] ? 'عم يحدّث…' : 'حدّث الفهرس هلق' }}</span></x-lq.button>
        </div>
    </section>

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
            <div class="lq-alert__body"><strong class="lq-alert__title">ما قدرنا نقرأ سجل البحث:</strong> <span data-health="ops-error-text">ما قدرنا نوصل لبيانات النظام. جرّب بعد شوي، وإذا ضل بلّغ المطوّر.</span></div>
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

    {{-- متقدم: «صدّر مجموعة اختبار» (HealthController::exportEvalSet -> cli_bridge eval_export -> scripts/eval_record.py
         --from-db). الزر وحده يشغّله؛ بيرجع وين الملف ورابط تنزيله (health.js createEvalExport). --}}
    <details class="lq-card lq-card--compact lq-health-advanced" data-health="advanced">
        <summary class="lq-health-advanced__summary">متقدم</summary>
        <div class="lq-health-advanced__body">
            <h2 class="lq-card__title">مجموعة اختبار من مراجعاتك</h2>
            <p class="lq-card__meta">بتجمع المنتجات اللي راجعتها (اللي اعتمدتها واللي رفضتها) مع الصور اللي عرضها البحث، بنسخ صغيرة، بملف واحد بتبعته للفريق ليقيسوا دقة البحث على منتجاتك الحقيقية. ما بتعمل أي بحث ولا بتكلّف شي، وما بيطلع فيها أي مفتاح أو بيانات دخول.</p>
            <div class="lq-card__header">
                <p class="lq-health__footnote" data-health="eval-export-status" role="status" aria-live="polite" dir="auto"></p>
                <x-lq.button variant="secondary" size="sm" icon="upload" data-health="eval-export"><span data-health="eval-export-label">صدّر مجموعة اختبار</span></x-lq.button>
            </div>
            <a class="lq-link" data-health="eval-export-link" href="#" download hidden>نزّل الملف</a>
        </div>
    </details>
</div>
@endsection

@push('scripts')
    <script src="{{ asset('js/health.js') }}?v={{ @filemtime(public_path('js/health.js')) ?: '1' }}"></script>
@endpush
