{{--
    لقطة · أقسام الصحة بصفحة «النظام» (resources/views/dashboard/system.blade.php: the page header, the rail and the
    script live there; GET /system-diagnostics redirects to /system). HealthController::pageData gives it $healthTab and
    the data of that section only; every card keeps its id, so /system?tab=…#id lands on it:
      status   «كلشي تمام» / «شو بدو منك» (#lq-health-attention-initial, $attention = HealthAttentionController::current():
               each item's fix is a link to the section that holds it, /system?tab=…&goto=…#card, and health.js puts
               the focus on its button), the services (#services: $lastDiagnostics, $services, $optional, $checkedAt,
               $allOk), the last run (#last-run, $lastRun) and «دقة الاقتراحات الحقيقية» (#lanes, read by health.js)
      search   «عمليات البحث» (#search-ops): results, providers, cost and reasons from GET /api/system/ops-health
      logs     «السجل» (#logs): the three log tabs (?log=nightly or goto=log-nightly opens the nightly one)
      publish  «فحص النشر» (#publish-check: $lastPublishCheck, $publish) with the «تجاوز عزل الخلفية» / «رجّع عزل
               الخلفية (…)» box ($bg, $bgView, $bgConfirm)
      index    «فهرس المتاجر المحلي» (#local-index, $localIndex = LocalIndexController::card())
      eval     «مجموعة اختبار من مراجعاتك» (#eval-export)
      recut    «صور قديمة بخلفية بيضا» (#reprocess) and its link to «فحص القص» (/catalog?mode=cutout)
    publish, index, eval and recut are admin sections (SystemController::GROUPS «أدوات»); a reviewer never gets them.
    Opening a section never runs the connection check (a paid Serper query and a PhotoRoom call) nor «فحص النشر» (POST
    /api/system/publish-check: it may cost one background-removal call): their buttons do.
    public/js/health.js runs the checks on click, loads «عمليات البحث» from GET /api/system/ops-health (on status too:
    provider outages feed «شو بدو منك») and the log tails from GET /api/view-pipeline-log, /api/view-laravel-log and
    /api/view-nightly-log; it tolerates the cards a section does not have.
--}}
@php
    $healthDots = ['success' => 'lq-dot--success', 'danger' => 'lq-dot--danger', 'warning' => 'lq-dot--warning', 'info' => 'lq-dot--info', 'muted' => 'lq-dot--muted'];
    $nowIcons = ['ok' => 'check', 'attention' => 'alert', 'checking' => 'refresh'];
    // the card's heading is what the service does; the provider's name stays beside it, muted
    $serviceRoles = ['google_sheets' => 'شيت المنتجات', 'serper' => 'البحث عن الصور', 'gemini' => 'قارئ الملصق',
        'photoroom' => 'عزل الخلفية', 'cloudinary' => 'رفع الصور'];
@endphp
<div class="lq-health" data-health-page data-health-tab="{{ $healthTab }}" data-settings-url="{{ route('dashboard.system') }}">
@switch ($healthTab)
    @case('status')
        {{-- «كلشي تمام» / «شو بدو منك» (HealthAttentionController): الحالة بكلمة وحدة، وكل بند بزر لتصليحه --}}
        <section class="lq-card lq-health-now" data-health="now" data-state="{{ $attention['state'] }}" aria-labelledby="lq-health-now-title">
            <div class="lq-health-now__head" role="status" aria-live="polite">
                <span class="lq-health-now__icon" aria-hidden="true"><x-lq.icon :name="$nowIcons[$attention['state']] ?? 'info'" :size="22" :stroke="2.2" /></span>
                <div class="lq-health-now__text">
                    <h2 class="lq-health-now__title" id="lq-health-now-title" data-health="now-title">{{ $attention['title'] }}</h2>
                    <p class="lq-health-now__lead" data-health="now-text">{{ $attention['text'] }}</p>
                </div>
            </div>
            <ul class="lq-health-now__items" data-health="now-items" @if ($attention['items'] === []) hidden @endif>
                @foreach ($attention['items'] as $item)
                    <li class="lq-health-todo" data-item="{{ $item['key'] }}" data-tone="{{ $item['tone'] }}">
                        <span class="lq-dot lq-dot--lg {{ $healthDots[$item['tone']] ?? 'lq-dot--muted' }}" aria-hidden="true"></span>
                        <div class="lq-health-todo__body">
                            <strong class="lq-health-todo__title" dir="auto">{{ $item['title'] }}</strong>
                            <p class="lq-health-todo__text" dir="auto">{{ $item['text'] }}</p>
                        </div>
                        @if ($item['action']['href'] !== '')
                            {{-- a link to the section that holds the fix; data-health-goto: on that section already, health.js only moves the focus --}}
                            <a class="lq-btn lq-btn--secondary lq-btn--sm lq-health-todo__fix" href="{{ url($item['action']['href']) }}" @if ($item['action']['goto'] !== '') data-health-goto="{{ $item['action']['goto'] }}" @endif @if ($item['action']['admin'] ?? false) data-lq-admin @endif>{{ $item['action']['label'] }}</a>
                        @else
                            <button type="button" class="lq-btn lq-btn--secondary lq-btn--sm lq-health-todo__fix" data-health-goto="{{ $item['action']['goto'] }}">{{ $item['action']['label'] }}</button>
                        @endif
                    </li>
                @endforeach
            </ul>
            <p class="lq-health__footnote" data-health="now-note">{{ $attention['note'] }}</p>
        </section>
        <script type="application/json" id="lq-health-attention-initial">@json($attention)</script>

        {{-- الخدمات: آخر فحص للاتصالات وزرّه (بيبين بـ«شو بدو منك» إذا خدمة أساسية ما ردّت) --}}
        <section class="lq-health__checkbar" id="services" aria-label="فحص الاتصالات">
            <p class="lq-health__checkline" data-health="checked-line">
                <span data-health="checked-text">@if ($checkedAt)آخر فحص للاتصالات: <time datetime="{{ gmdate('c', $checkedAt) }}" data-health="checked-at">{{ \App\Http\Controllers\HealthController::stamp((int) $checkedAt) }}</time>@else لسا ما انعمل فحص للاتصالات.@endif</span><span class="lq-health__warn" data-health="checked-warn" @if ($allOk !== false) hidden @endif> في خدمات أساسية ما بتردّ.</span>
                الفحص ما بيشتغل لحاله لما تفتح الصفحة.
            </p>
            <div class="lq-health__check">
                <x-lq.button variant="secondary" size="lg" icon="refresh" data-health="run-check" data-lq-admin><span data-health="run-check-label">فحص الاتصالات الآن</span></x-lq.button>
                <span class="lq-health__check-note" id="diagCheckNote">بيكلّف بحث صور واحد وطلب عزل خلفية واحد، وما بياخد أكتر من 45 ثانية. <span class="lq-subtle">(Serper · PhotoRoom)</span></span>
            </div>
        </section>

        <script type="application/json" id="lq-health-initial">@json($lastDiagnostics)</script>

        <div class="lq-health__services" data-health="services" aria-live="polite">
            @foreach ($services as $service)
                <article class="lq-health-service" id="card-{{ $service['key'] }}" data-service="{{ $service['key'] }}" data-tone="{{ $service['tone'] }}">
                    <div class="lq-health-service__head">
                        <h2 class="lq-health-service__name">{{ $serviceRoles[$service['key']] ?? $service['name'] }} @isset($serviceRoles[$service['key']])<span class="lq-subtle" dir="ltr">{{ $service['name'] }}</span>@endisset</h2>
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
            <section class="lq-card lq-card--compact lq-card--flat" id="last-run" aria-label="آخر تشغيل" data-health-last-run>
                <p class="lq-card__meta">آخر تشغيل @if ($lastRun['when'] !== '')· <time>{{ $lastRun['when'] }}</time>@endif</p>
                <h2 class="lq-card__title"><span class="lq-dot {{ $healthDots[$lastRun['tone']] ?? 'lq-dot--muted' }}" aria-hidden="true"></span> {{ $lastRun['title'] }}</h2>
                @if ($lastRun['summary'] !== '')<p class="lq-health__footnote">{{ $lastRun['summary'] }}</p>@endif
            </section>
        @endif

        {{-- «دقة الاقتراحات الحقيقية»: health.js reads /api/system/review-lanes (review_stats.lanes, cached) --}}
        <section class="lq-card lq-card--compact lq-card--flat" id="lanes" aria-label="دقة الاقتراحات الحقيقية">
            <h2 class="lq-card__title">دقة الاقتراحات الحقيقية</h2>
            <p class="lq-card__meta">من مراجعاتك للصور اللي اقترحها البحث، حسب نوع الاقتراح.</p>
            <div class="lq-health-lanes" data-health="lanes" aria-busy="true" aria-live="polite">
                <span class="lq-skeleton lq-health__skel-inline" aria-hidden="true"></span>
            </div>
        </section>
        @break

    @case('search')
        <section class="lq-health__search" id="search-ops" aria-labelledby="lq-health-search-title">
            <div class="lq-health__search-head">
                <h2 class="lq-section-title" id="lq-health-search-title">عمليات البحث</h2>
                <x-lq.segmented label="الفترة" size="sm" value="24h" :options="['24h' => 'آخر 24 ساعة', '7d' => 'آخر 7 أيام']" data-health="window" />
            </div>

            <div class="lq-health__alerts" data-health="alerts" hidden></div>

            <div class="lq-health__grid" data-health="ops" aria-busy="true">
                <section class="lq-card lq-card--flat lq-health-results" aria-labelledby="lq-health-results-title">
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
                    <section class="lq-card lq-card--flat lq-health-cost" aria-labelledby="lq-health-cost-title">
                        <h3 class="lq-card__title" id="lq-health-cost-title">التكلفة التقديرية</h3>
                        <span class="lq-health-cost__total" data-health="cost-total"><span class="lq-skeleton lq-skeleton--title" aria-hidden="true"></span></span>
                        <div class="lq-health-cost__lines" data-health="cost-lines"></div>
                        <p class="lq-health-cost__note" data-health="cost-note"></p>
                    </section>
                    <section class="lq-card lq-card--flat lq-health-reasons" aria-labelledby="lq-health-reasons-title">
                        <h3 class="lq-card__title" id="lq-health-reasons-title">أسباب ما لقينا صورة</h3>
                        <div class="lq-health-reasons__rows" data-health="reasons">
                            <span class="lq-skeleton lq-skeleton--text" aria-hidden="true"></span>
                        </div>
                    </section>
                </div>
            </div>

            <x-lq.empty-state class="lq-health__ops-empty" data-health="ops-empty" hidden icon="search"
                title="ما في عمليات بحث بآخر 7 أيام"
                text="أول ما تشغّل بحث من «اليوم»، بتبيّن هون النتائج وردود المصادر والتكلفة.">
                <x-lq.button variant="secondary" icon="play" :href="route('dashboard.index') . '#run-new'" data-lq-admin>تشغيل جديد</x-lq.button>
            </x-lq.empty-state>

            <div class="lq-alert lq-alert--danger" role="alert" data-health="ops-error" hidden>
                <x-lq.icon name="alert" :size="18" class="lq-alert__icon" />
                <div class="lq-alert__body"><strong class="lq-alert__title">ما قدرنا نقرأ سجل البحث:</strong> <span data-health="ops-error-text">ما قدرنا نوصل لبيانات النظام. جرّب بعد شوي، وإذا ضل بلّغ المطوّر.</span></div>
                <button type="button" class="lq-alert__action lq-health__retry" data-health="ops-retry">جرّب مرة تانية</button>
            </div>

            <p class="lq-health__footnote" data-health="ops-note"></p>
        </section>
        @break

    @case('logs')
        <section class="lq-card lq-card--dark lq-health-log" id="logs" aria-labelledby="lq-health-log-title">
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
        @break

    @case('publish')
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
                    <x-lq.button variant="primary" icon="play" data-health="publish-run" data-lq-admin><span data-health="publish-run-label">افحص النشر</span></x-lq.button>
                    <span class="lq-health-publish__cost" id="publishCheckNote">ممكن يكلّف طلب عزل خلفية واحد، وعادةً بيخلص بأقل من دقيقة.</span>
                    <details class="lq-health-publish__cost"><summary class="lq-link">تفاصيل</summary>ونادراً أكتر من طلب إذا ما زبط العزل من أول مرة، وقراءة ملصق وحدة (Gemini) إذا مفتاحها محفوظ. الصورة التجريبية بتنرفع على Cloudinary وبتنمسح، وبالشيت منكتب عنوان عمود الرابط نفسه فوق حاله، وما بيطول أكتر من 7 دقايق.</details>
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
                <div class="lq-health-bg__actions" data-lq-admin>
                    <button type="button" class="lq-btn lq-btn--danger lq-btn--sm" data-health="bg-skip" data-confirm="{{ $bgConfirm }}" @if ($bgView['state'] !== 'offer') hidden @endif>تجاوز عزل الخلفية</button>
                    <button type="button" class="lq-btn lq-btn--secondary lq-btn--sm" data-health="bg-restore" data-method="{{ $bgView['restore'] }}" @if ($bgView['state'] !== 'off') hidden @endif><span data-health="bg-restore-label">{{ $bgView['restore_label'] }}</span></button>
                </div>
            </div>
            <p class="lq-health__footnote" data-health="publish-notes">{{ implode(' ', $publish['notes']) }}</p>
        </section>
        <script type="application/json" id="publish-check-initial">@json($lastPublishCheck)</script>
        @break

    @case('index')
        {{-- «فهرس المتاجر المحلي» (LocalIndexController::card; the page script's createLocalIndex): per store the pages in the
             index, when its sitemaps were last read and its status (تمام / ممنوع / ما انجمع أبداً). «حدّث الفهرس هلق» starts the
             background refresh through the bridge and returns at once; the status line shows the progress on reload. --}}
        <section class="lq-card" id="local-index" aria-label="فهرس المتاجر المحلي" data-health="index-card">
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
                <x-lq.button variant="secondary" size="sm" icon="refresh" data-health="index-refresh" data-lq-admin :disabled="$localIndex['refresh']['running']"><span data-health="index-refresh-label">{{ $localIndex['refresh']['running'] ? 'عم يحدّث…' : 'حدّث الفهرس هلق' }}</span></x-lq.button>
            </div>
        </section>
        @break

    @case('eval')
        {{-- «صدّر مجموعة اختبار» (HealthController::exportEvalSet -> cli_bridge eval_export -> scripts/eval_record.py
             --from-db). الزر وحده يشغّله؛ بيرجع وين الملف ورابط تنزيله (health.js createEvalExport). --}}
        <section class="lq-card lq-health-eval" id="eval-export" aria-labelledby="lq-health-eval-title" data-health="eval-card" data-lq-admin>
                <h2 class="lq-card__title" id="lq-health-eval-title">مجموعة اختبار من مراجعاتك</h2>
                <p class="lq-card__meta">ملف واحد من مراجعاتك بتبعته للفريق ليقيسوا دقة البحث؛ ما بيكلّف شي وما فيه أي مفتاح.</p>
                <details><summary class="lq-link">تفاصيل</summary><p class="lq-field__hint">بتجمع المنتجات اللي راجعتها (اللي اعتمدتها واللي رفضتها) مع الصور اللي عرضها البحث، بنسخ صغيرة، ليقيسوا دقة البحث على منتجاتك الحقيقية. ما بتعمل أي بحث، وما بيطلع فيها أي مفتاح أو بيانات دخول.</p></details>
                <div class="lq-card__header">
                    <p class="lq-health__footnote" data-health="eval-export-status" role="status" aria-live="polite" dir="auto"></p>
                    <x-lq.button variant="secondary" size="sm" icon="upload" data-health="eval-export"><span data-health="eval-export-label">صدّر مجموعة اختبار</span></x-lq.button>
                </div>
                <details class="lq-health-service__details" data-health="eval-export-details" hidden>
                    <summary>التفاصيل التقنية</summary>
                    <p dir="ltr" data-health="eval-export-detail"></p>
                </details>
                <a class="lq-link" data-health="eval-export-link" href="#" download hidden>نزّل الملف</a>
        </section>
        @break

    @case('recut')
        {{-- «صور قديمة بخلفية بيضا» (RecutController -> cli_bridge reprocess_plan / reprocess_start ->
             scripts/reprocess_transparent.py; health.js createReprocess). «احسب» ما بيغيّر شي؛ «ابدأ» دفعة بسقف. --}}
        <section class="lq-card lq-health-reprocess" id="reprocess" aria-labelledby="reprocess-title" data-health="reprocess" data-lq-admin>
            <h2 class="lq-card__title" id="reprocess-title">صور قديمة بخلفية بيضا</h2>
            <p class="lq-card__meta">«احسب» بيقلك قديش بتكلّف نعيد قص الصور القديمة شفافة، بدون ما يغيّر شي.</p>
            <details><summary class="lq-link">تفاصيل</summary><p class="lq-field__hint">الصور اللي انتشرت قبل الخلفية الشفافة بتبين مربع أبيض بالوضع الغامق بالتطبيق. الصف اللي غيّرت صورته بإيدك ما منلمسه، وكل صورة بتنعاد بتنسجّل وفيك ترجّعها من «فحص القص».</p></details>
            <div class="lq-card__header">
                <p class="lq-health__footnote" data-health="reprocess-status" role="status" aria-live="polite" dir="auto"></p>
                <x-lq.button variant="secondary" size="sm" icon="search" data-health="reprocess-plan"><span data-health="reprocess-plan-label">احسب</span></x-lq.button>
            </div>
            <form class="lq-health-reprocess__form" data-health="reprocess-form" hidden>
                <label class="lq-field"><span class="lq-field__label">كم صورة بهالدفعة</span><input type="number" class="lq-input lq-input--sm" name="max" min="1" max="200" step="1" value="20" dir="ltr" inputmode="numeric" data-health="reprocess-max"></label>
                <label class="lq-field"><span class="lq-field__label">أقصى تكلفة (دولار)</span><input type="number" class="lq-input lq-input--sm" name="max_usd" min="0.01" max="100" step="0.01" value="1" dir="ltr" inputmode="decimal" data-health="reprocess-usd"></label>
                <x-lq.button variant="primary" size="sm" icon="play" type="submit" data-health="reprocess-start"><span data-health="reprocess-start-label">ابدأ</span></x-lq.button>
            </form>
            <a class="lq-link" href="{{ route('dashboard.catalog', ['mode' => 'cutout']) }}">افتح «فحص القص»</a>
        </section>
        @break
@endswitch
</div>
