{{--
    لقطة · «جهّز لقطة» (first-run setup wizard). SetupController::page renders it with:
      $setup      SetupController::view(): {db, done, current, ready, progress {ok, total}, steps: [{key, n, title, lead, status,
                  label, tone, summary, rows: [{key, label, state, tone, fix, href, href_label}], skipped, ...}]}
      $active     the step on screen (?step=, else the first one that is not done)
      $sheet      SettingsController::sheetData(): the saved sheet link and tab, and the service account e-mail
      $keysNote   what «افحص المفاتيح» costs (the same connection check as the Health page)
    Opening the page checks nothing: every live check is a button (POST /api/setup/check). The sheet is saved through
    POST /api/sheet/save (ApiController::sheetParams), keys in the Settings fields, the rehearsal is POST
    /api/system/publish-check and the first run POST /api/run-all with a row filter. public/js/setup.js; settings.js
    is loaded first for its sheet wording (window.LaqtaSettings).
--}}
@extends('layouts.laqta')

@section('title', 'جهّز لقطة')
@section('lq_nav', 'settings')
@section('body_class', 'lq-page-setup')

@push('styles')
    <link rel="stylesheet" href="{{ asset('css/pages/setup.css') }}?v={{ @filemtime(public_path('css/pages/setup.css')) ?: '1' }}">
@endpush

@php
    $setupDots = ['success' => 'lq-dot--success', 'danger' => 'lq-dot--danger', 'warning' => 'lq-dot--warning', 'info' => 'lq-dot--info', 'muted' => 'lq-dot--muted'];
    $setupTotal = count($setup['steps']);
@endphp

@section('content')
<div class="lq-setup" data-setup-page data-active="{{ $active }}">
    <header class="lq-page-header">
        <div class="lq-page-header__text">
            <span class="lq-page-header__eyebrow">التجهيز لأول مرة</span>
            <h1 class="lq-page-title">جهّز لقطة</h1>
            <p class="lq-page-header__desc">{{ $setupTotal }} خطوات قصيرة لتتأكد إنو كلشي جاهز قبل ما تشغّل الشيت كله. التقدّم بينحفظ: فيك تطلع وترجع تكمّل من وين ما وقفت.</p>
        </div>
        <div class="lq-page-header__actions">
            <span class="lq-setup__progress" data-setup="progress"><strong class="lq-num" data-setup="progress-ok">{{ $setup['progress']['ok'] }}</strong> من {{ $setupTotal }} خطوات تمام</span>
        </div>
    </header>

    @unless ($setup['db'])
        <x-lq.alert variant="danger" title="قاعدة البيانات مش متاحة:">التقدّم ما بينحفظ والفحوصات ما بتمشي هلق. شغّل قاعدة البيانات وحدّث الصفحة.</x-lq.alert>
    @endunless
    <div class="lq-alert lq-alert--success" role="status" data-setup="done-note" @unless ($setup['done']) hidden @endunless>
        <x-lq.icon name="check" :size="18" class="lq-alert__icon" />
        <div class="lq-alert__body"><strong class="lq-alert__title">خلّصت التجهيز:</strong> الرئيسية ما عادت تذكّرك فيه. فيك ترجع لهون من الإعدادات بأي وقت.</div>
    </div>

    <ol class="lq-setup-steps" data-setup="stepper" aria-label="خطوات التجهيز">
        @foreach ($setup['steps'] as $step)
            <li class="lq-setup-steps__item">
                <a class="lq-setup-steps__link" href="{{ route('dashboard.setup') }}?step={{ $step['key'] }}" data-setup-tab="{{ $step['key'] }}" data-status="{{ $step['status'] }}" @if ($step['key'] === $active) aria-current="step" @endif>
                    <span class="lq-setup-steps__n" aria-hidden="true">@if ($step['status'] === 'ok')<x-lq.icon name="check" :size="14" :stroke="2.6" />@else{{ $step['n'] }}@endif</span>
                    <span class="lq-setup-steps__text">
                        <span class="lq-setup-steps__title">{{ $step['title'] }}</span>
                        <span class="lq-setup-steps__state" data-setup-tab-state>{{ $step['label'] }}</span>
                    </span>
                </a>
            </li>
        @endforeach
    </ol>

    @foreach ($setup['steps'] as $step)
        <section class="lq-card lq-setup-step" id="setup-{{ $step['key'] }}" data-setup-step="{{ $step['key'] }}" data-status="{{ $step['status'] }}" aria-labelledby="setup-{{ $step['key'] }}-title" @if ($step['key'] !== $active) hidden @endif>
            <div class="lq-setup-step__head">
                <span class="lq-setup-step__count">الخطوة {{ $step['n'] }} من {{ $setupTotal }}</span>
                <h2 class="lq-setup-step__title" id="setup-{{ $step['key'] }}-title">{{ $step['title'] }}</h2>
                <p class="lq-setup-step__lead">{{ $step['lead'] }}</p>
            </div>

            <div class="lq-setup-status" data-setup-part="status" data-tone="{{ $step['tone'] }}" role="status" aria-live="polite">
                <span class="lq-dot lq-dot--lg {{ $setupDots[$step['tone']] ?? 'lq-dot--muted' }}" data-setup-part="dot" aria-hidden="true"></span>
                <span class="lq-setup-status__label" data-setup-part="label">{{ $step['label'] }}</span>
                <strong class="lq-setup-status__summary" data-setup-part="summary">{{ $step['summary'] }}</strong>
            </div>

            <ul class="lq-setup-checks" data-setup-part="rows">
                @foreach ($step['rows'] as $row)
                    <li class="lq-setup-check" data-row="{{ $row['key'] }}" data-tone="{{ $row['tone'] }}">
                        <span class="lq-dot {{ $setupDots[$row['tone']] ?? 'lq-dot--muted' }}" aria-hidden="true"></span>
                        <span class="lq-setup-check__label">{{ $row['label'] }}</span>
                        <span class="lq-setup-check__state" dir="auto">{{ $row['state'] }}</span>
                        @if ($row['fix'] !== '' || $row['href'] !== '')
                            <p class="lq-setup-check__fix" dir="auto">{{ $row['fix'] }}@if ($row['href'] !== '') <a class="lq-link" href="{{ url($row['href']) }}">{{ $row['href_label'] }}</a>@endif</p>
                        @endif
                    </li>
                @endforeach
            </ul>

            <div class="lq-setup-step__body">
                @switch($step['key'])
                    @case('sheet')
                        <div class="lq-setup-share" data-setup="share" @if ($step['email'] === '') hidden @endif>
                            <span class="lq-setup-share__text">شارك الشيت مع هالحساب كمحرّر (Editor) ليقدر يقرأ ويكتب الروابط:</span>
                            <span class="lq-setup-share__row">
                                <code class="lq-setup-share__email" dir="ltr" data-setup="email">{{ $step['email'] }}</code>
                                <x-lq.button variant="ghost" size="sm" icon="link" data-setup="copy">نسخ</x-lq.button>
                            </span>
                        </div>
                        <form class="lq-setup-form" data-setup="sheet-form" autocomplete="off" novalidate data-saved-url="{{ $step['url_is_default'] ? '' : $step['url'] }}" data-saved-tab="{{ $step['tab'] }}">
                            <label class="lq-field lq-setup-form__wide">
                                <span class="lq-field__label">رابط الشيت أو اسمه</span>
                                <input type="text" class="lq-input" name="spreadsheet_url" dir="auto" value="{{ $step['url_is_default'] ? '' : $step['url'] }}" placeholder="https://docs.google.com/spreadsheets/d/…" data-setup="sheet-url">
                            </label>
                            <label class="lq-field">
                                <span class="lq-field__label">اسم التبويب</span>
                                <input type="text" class="lq-input" name="tab_name" dir="auto" value="{{ $step['tab'] }}" placeholder="فاضي = أول تبويب" data-setup="sheet-tab">
                            </label>
                        </form>
                        <div class="lq-setup-actions">
                            <x-lq.button variant="primary" icon="search" data-setup="sheet-check" :disabled="!$setup['db']"><span data-setup="sheet-check-label">افحص الشيت</span></x-lq.button>
                            <a class="lq-link" href="{{ route('dashboard.settings') }}?tab=sheet">الإعدادات · ربط الشيت</a>
                        </div>
                        <p class="lq-setup-note" data-setup="sheet-status" role="status" aria-live="polite"></p>
                        @break

                    @case('keys')
                        <div class="lq-setup-actions">
                            <x-lq.button variant="primary" icon="refresh" data-setup="keys-test" :disabled="!$setup['db']"><span data-setup="keys-test-label">افحص المفاتيح</span></x-lq.button>
                            <x-lq.button variant="secondary" icon="settings" :href="route('dashboard.settings') . '?tab=keys'">حطّ المفاتيح بالإعدادات</x-lq.button>
                        </div>
                        <p class="lq-setup-note">{{ $keysNote }} قيمة المفتاح ما بتنعرض هون أبداً: بنقول بس إذا محفوظ وإذا اشتغل.</p>
                        @break

                    @case('brands')
                        <div class="lq-setup-actions">
                            <x-lq.button variant="primary" icon="search" data-setup="brands-check" :disabled="!$setup['db']"><span data-setup="brands-check-label">افحص جدول الماركات</span></x-lq.button>
                            <x-lq.button variant="secondary" icon="store" :href="url($step['href'])">عبّي جدول الماركات</x-lq.button>
                        </div>
                        <p class="lq-setup-note">الفحص بيقرأ التبويب بس: ما بيكتب بالشيت وما بيكلّف شي. «عبّي جدول الماركات» بصفحة التشغيل بيقترح صف لكل ماركة ناقصة، وما بينكتب شي قبل ما تعتمده.</p>
                        @break

                    @case('publish')
                        <div class="lq-setup-actions">
                            <x-lq.button variant="primary" icon="play" data-setup="publish-run" :disabled="!$setup['db']"><span data-setup="publish-run-label">افحص النشر</span></x-lq.button>
                            <a class="lq-link" href="{{ url($step['details_href']) }}">التفاصيل بصفحة الصحة</a>
                        </div>
                        <p class="lq-setup-note" data-setup="publish-meta">@if ($step['when'] !== '')آخر فحص للنشر: {{ $step['when'] }}@if ($step['sample'] !== '') · {{ $step['sample'] }}@endif. @endif ممكن يكلّف طلب عزل خلفية واحد وقراءة Gemini وحدة. الصورة التجريبية بتنرفع على Cloudinary وبتنمسح، وبالشيت منكتب عنوان عمود الرابط نفسه فوق حاله. عادةً بيخلص بأقل من دقيقة.</p>
                        @break

                    @case('run')
                        <div class="lq-setup-plan" data-setup="plan" hidden>
                            <p class="lq-setup-plan__text" dir="auto" data-setup="plan-text"></p>
                            <x-lq.button variant="primary" icon="play" data-setup="run-start" hidden><span data-setup="run-start-label">ابدأ أول تشغيل</span></x-lq.button>
                        </div>
                        <div class="lq-setup-live" data-setup="live" role="status" aria-live="polite" hidden>
                            <span class="lq-spinner" data-setup="live-spin" aria-hidden="true"></span>
                            <span data-setup="live-text"></span>
                        </div>
                        <div class="lq-setup-actions">
                            <x-lq.button variant="secondary" icon="search" data-setup="run-plan" :disabled="!$setup['db']"><span data-setup="run-plan-label">جهّز أول تشغيل</span></x-lq.button>
                            <a class="lq-btn lq-btn--primary" href="{{ url($step['review_href']) }}" data-setup="review-link" @if ($step['status'] !== 'ok') hidden @endif><x-lq.icon name="review" :size="18" :stroke="2" />راجع الصور</a>
                        </div>
                        <p class="lq-setup-note">بيدوّر على صور 5 منتجات بس من الشيت (اللي ما إلها صور). الصور بتستنى مراجعتك وما بتنزل الشيت لحالها قبل ما تعتمدها.</p>
                        @break
                @endswitch
            </div>

            <footer class="lq-setup-step__nav">
                @if ($step['n'] > 1)
                    <button type="button" class="lq-btn lq-btn--ghost" data-setup-go="prev">السابقة</button>
                @endif
                <button type="button" class="lq-btn lq-btn--ghost lq-setup-step__skip" data-setup-skip="{{ $step['key'] }}" @disabled(!$setup['db'])>{{ $step['skipped'] ? 'رجّع هالخطوة' : 'تخطّى هالخطوة' }}</button>
                @if ($step['n'] < $setupTotal)
                    <button type="button" class="lq-btn lq-btn--secondary" data-setup-go="next">التالية<x-lq.icon name="arrow-left" :size="16" :stroke="2" /></button>
                @else
                    <button type="button" class="lq-btn lq-btn--secondary" data-setup-go="ready">النهاية<x-lq.icon name="arrow-left" :size="16" :stroke="2" /></button>
                @endif
            </footer>
        </section>
    @endforeach

    <section class="lq-card lq-setup-ready" data-setup="ready" aria-labelledby="setup-ready-title" @if ($active !== 'ready') hidden @endif>
        <span class="lq-setup-ready__icon" aria-hidden="true"><x-lq.icon name="check" :size="22" :stroke="2.4" /></span>
        <h2 class="lq-setup-step__title" id="setup-ready-title" data-setup="ready-title">{{ $setup['ready'] ? 'لقطة جاهزة' : 'باقي خطوات' }}</h2>
        <p class="lq-setup-step__lead" data-setup="ready-text">{{ $setup['ready'] ? 'كل الخطوات خلصت. راجع الصور اللي لقيناها، وبعدين شغّل الشيت كله من صفحة التشغيل.' : 'في خطوات لسا ما خلصت: فيك ترجعلها، أو تخلّص التجهيز هلق وتكمّلها بعدين من الإعدادات.' }}</p>
        <div class="lq-setup-actions">
            <x-lq.button variant="primary" icon="check" data-setup="finish" :disabled="!$setup['db']">خلّصت التجهيز</x-lq.button>
            <x-lq.button variant="secondary" icon="review" :href="route('dashboard.catalog') . '?mode=bulk'">راجع الصور</x-lq.button>
        </div>
    </section>

    <p class="lq-setup__later">بدك تكمّل بعدين؟ التقدّم محفوظ، والرئيسية بتذكّرك لحد ما تخلّص. <a class="lq-link" href="{{ route('dashboard.index') }}">رجوع للرئيسية</a></p>
</div>

<script type="application/json" id="lq-setup-initial">@json($setup)</script>
@endsection

@push('scripts')
    <script src="{{ asset('js/settings.js') }}?v={{ @filemtime(public_path('js/settings.js')) ?: '1' }}"></script>
    <script src="{{ asset('js/setup.js') }}?v={{ @filemtime(public_path('js/setup.js')) ?: '1' }}"></script>
@endpush
