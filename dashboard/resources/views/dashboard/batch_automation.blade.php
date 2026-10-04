{{--
    لقطة · التشغيل (Run board). RunController::page renders it with:
      $live         RunController::snapshot(): /api/batch-status + the current/last run summary + latest rows
      $autoPublish  RunController::autoPublishState(): the worker's auto-publish setting in one sentence
    public/js/run.js keeps it live from GET /api/run/live and fills «قبل ما تبدأ» from GET /api/run/plan.
    The review grid moved to the review page (/catalog?mode=bulk); the sheet connection moved to Settings.
--}}
@extends('layouts.laqta')

@section('title', 'التشغيل')
@section('lq_nav', 'run')
@section('body_class', 'lq-page-run')

@push('styles')
    <link rel="stylesheet" href="{{ asset('css/pages/run.css') }}?v={{ @filemtime(public_path('css/pages/run.css')) ?: '1' }}">
@endpush

@section('content')
<div class="lq-run" data-run-page>
    <x-lq.page-header title="التشغيل" description="النظام بيبحث عن صور المنتجات وبيجهّزها لمراجعتك. النتائج بتنزل على قائمة المراجعة وهو شغّال." />

    <div class="lq-alert lq-alert--danger lq-alert--banner" role="alert" data-run="alert" hidden>
        <x-lq.icon name="alert" :size="20" class="lq-alert__icon" />
        <div class="lq-alert__body"><strong class="lq-alert__title" data-run="alert-title"></strong> <span data-run="alert-text"></span></div>
        <a class="lq-alert__action" href="{{ route('dashboard.diagnostics') }}">التفاصيل</a>
    </div>

    <div class="lq-run__grid">
        {{-- New run --}}
        <section class="lq-card lq-run-new" aria-labelledby="lq-run-new-title">
            <h2 class="lq-card__title" id="lq-run-new-title">تشغيل جديد</h2>

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
                    <p class="lq-run-advanced__hint">منبحث من جديد عن كل منتجات النطاق، حتى اللي إلها صورة نهائية أو بانتظار مراجعتك. الصورة المنشورة بالشيت بتضل مكانها لحد ما تعتمد غيرها، وما بينكتب أبداً فوق صورة اعتمدها مراجع.</p>
                    <label class="lq-check"><input type="checkbox" data-run="skip-cache"><span>تجاهل النتائج المحفوظة والبحث من جديد</span></label>
                    <p class="lq-run-advanced__hint">ما منستعمل الصور اللي انلقت قبل هيك، فالبحث أبطأ وأغلى شوي.</p>
                </div>
            </details>

            <div class="lq-run-autopub" data-run="autopub" data-enabled="{{ $autoPublish['enabled'] ? 'true' : 'false' }}">
                <x-lq.icon name="shield" :size="18" class="lq-run-autopub__icon" />
                <span>{{ $autoPublish['text'] }} <a class="lq-link" href="{{ route('dashboard.settings') }}?tab=auto-publish">تغيير</a></span>
            </div>

            <div class="lq-run-new__start">
                <x-lq.button variant="primary" size="lg" icon="play" block data-run="start"><span data-run="start-text">ابدأ التشغيل</span></x-lq.button>
                <p class="lq-field__error" role="alert" data-run="start-error" hidden></p>
                <p class="lq-run-new__note" data-run="start-note" hidden>في تشغيل شغّال هلق. فيك تبدأ واحد جديد لما يخلص أو توقفه.</p>
            </div>
        </section>

        {{-- Current run --}}
        <section class="lq-card lq-run-current" aria-labelledby="lq-run-current-title" data-run="current" data-state="loading">
            <div class="lq-run-current__head">
                <div class="lq-run-current__title">
                    <h2 class="lq-card__title" id="lq-run-current-title">التشغيل الحالي</h2>
                    <span class="lq-chip lq-chip--sm lq-chip--none" data-run="chip" aria-live="polite"><span class="lq-chip__dot" aria-hidden="true"></span><span data-run="chip-text">لحظة…</span></span>
                </div>
                <span class="lq-card__meta lq-num" data-run="meta"></span>
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
                <h3 class="lq-empty__title" data-run="empty-title">ما في تشغيل هلق</h3>
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
            </div>

            <div class="lq-run-finished" data-run="finished" hidden>
                <p class="lq-run-finished__title" data-run="finished-title"></p>
                <div class="lq-run-tiles" data-run="tiles"></div>
                <p class="lq-run-finished__explain" data-run="explain" hidden></p>
                <x-lq.button variant="primary" icon="review" href="{{ route('dashboard.catalog') }}" data-run="review" hidden><span data-run="review-text">راجع النتائج</span></x-lq.button>
            </div>

            <div class="lq-alert lq-alert--warning" data-run="stuck" hidden>
                <x-lq.icon name="alert" :size="18" class="lq-alert__icon" />
                <div class="lq-alert__body">
                    <strong class="lq-alert__title">التشغيل عالق:</strong> <span data-run="stuck-reason"></span>
                    <p class="lq-run-stuck__what">«إصلاح تشغيل عالق» بيوقف أي عامل عالق، وبيمسح حالة التشغيل والتنبيه، وبيرجّع المنتجات العالقة للطابور. ما بيحذف ولا منتج جاهز للمراجعة أو معتمد أو فاشل، ولا أي اقتراح أو قرار مراجعة.</p>
                    <button type="button" class="lq-btn lq-btn--secondary lq-btn--sm" data-run="reset"><x-lq.icon name="refresh" :size="16" :stroke="2" />إصلاح تشغيل عالق</button>
                </div>
            </div>

            <div class="lq-run-recent">
                <div class="lq-run-recent__head">
                    <h3 class="lq-run-recent__title">آخر النتائج</h3>
                    <a class="lq-link" href="{{ route('dashboard.catalog') }}">افتح بالمراجعة</a>
                </div>
                <ol class="lq-run-recent__list" data-run="recent"></ol>
                <p class="lq-run-recent__empty" data-run="recent-empty" hidden>لسا ما في نتائج بهالتشغيل.</p>
            </div>
        </section>
    </div>

    <div class="lq-run__grid lq-run__grid--tools">
        {{-- One JSON file of a run for analysis (GET /api/run/export): no new search, no cost, no key --}}
        <section class="lq-card lq-run-export" aria-labelledby="run-export-title">
            <h2 class="lq-card__title" id="run-export-title">تقرير للتحليل</h2>
            <p class="lq-run-export__text">ملف واحد فيه كل صفوف التشغيل: القرار، وليش ما في اقتراح، وأفضل 8 صور مع أدلتها، والتكلفة إذا معروفة. ما بيعمل بحث جديد وما بيكلّف شي، وما فيه أي مفتاح أو كلمة سر. ابعته للمطوّر بدل ما تبعت ملفات.</p>
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
        </section>
    </div>
</div>

<script type="application/json" id="lq-run-initial">@json($live)</script>
@endsection

@push('scripts')
    <script src="{{ asset('js/run-common.js') }}?v={{ @filemtime(public_path('js/run-common.js')) ?: '1' }}"></script>
    <script src="{{ asset('js/run.js') }}?v={{ @filemtime(public_path('js/run.js')) ?: '1' }}"></script>
@endpush
