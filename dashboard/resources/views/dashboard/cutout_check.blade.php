{{--
    «فحص القص» (/cutout-check, nav: review): الصور المنشورة الأحدث، كل وحدة على الغامق والفاتح والمربعات جنب بعض (متل ما
    بتبين بالتطبيق)، مع علامات القص (خلفية بيضا، حواف فاتحة على الغامق، PhotoRoom مش متأكد، زجاج، حواف غامقة على الفاتح،
    دقة قليلة). لكل صورة: «أعد القص بـPhotoRoom» أو «جرّب القص المحلي» بيعملوا قص جديد بيستنى؛ قبل/بعد، ثم «اعتمد الجديد»
    (نسخة جديدة عبر طابور الكتابة) أو «خلّي القديم». ولا شي بيتبدّل لحاله، وكل تبديل بينسجّل وبيترجع من «آخر التبديلات».

    RecutController::page يمرر $cutoutConfig (المسارات والعلامات) بـ data-config؛ public/js/review/cutout.js بيبني الواجهة
    (بيستعمل ui.js وtheme_preview.js تبع المراجعة). الأقسام الثابتة هون هيكل تحميل بس.
--}}
@extends('layouts.laqta')

@section('title', 'فحص القص')
@section('lq_nav', 'review')
@section('body_class', 'page-cutout')

@php
    $cqVersion = static fn (string $path) => asset($path) . '?v=' . (@filemtime(public_path($path)) ?: '1');
@endphp

@push('styles')
<link rel="stylesheet" href="{{ $cqVersion('css/pages/cutout.css') }}">
@endpush

@section('content')
<div class="cq" id="cqApp" data-config="{{ json_encode($cutoutConfig, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) }}" aria-busy="true">
    <header class="lq-page-header cq__header">
        <div class="lq-page-header__text">
            <h1 class="lq-page-title">فحص القص</h1>
            <p class="lq-page-header__desc">كل صورة منشورة على الغامق والفاتح والمربعات، متل ما بتبين بالتطبيق. ولا صورة بتتبدّل لحالها: كل تبديل بكبستك، وبينسجّل وفيك ترجّعه.</p>
        </div>
        <a class="lq-link" href="{{ route('dashboard.catalog') }}">رجوع للمراجعة</a>
    </header>

    <div class="cq__filters" role="group" aria-label="فلترة حسب الملاحظة" data-cq="filters"></div>
    <p class="cq__status" role="status" aria-live="polite" data-cq="status">عم نجيب الصور…</p>

    <div class="cq__grid" data-cq="grid">
        @for ($i = 0; $i < 4; $i++)
            <div class="cq-card cq-card--skeleton" aria-hidden="true"><span class="lq-skeleton lq-skeleton--text"></span><div class="cq-stages"><span class="lq-skeleton cq-stage"></span><span class="lq-skeleton cq-stage"></span><span class="lq-skeleton cq-stage"></span></div></div>
        @endfor
    </div>
    <nav class="cq__pager" aria-label="الصفحات" data-cq="pager" hidden></nav>

    <section class="lq-card lq-card--compact cq__log" aria-labelledby="cq-log-title" data-cq="log" hidden>
        <h2 class="lq-card__title" id="cq-log-title">آخر التبديلات</h2>
        <p class="lq-card__meta">كل صورة انبدلت من هون أو من «صور قديمة بخلفية بيضا». «رجّع القديم» بيرجّع الصورة اللي كانت، بنفس الطريق.</p>
        <ul class="cq-log" data-cq="log-list"></ul>
    </section>

    <noscript>
        <x-lq.alert variant="warning" title="الصفحة بتحتاج JavaScript:">فعّل JavaScript بالمتصفح وحدّث الصفحة.</x-lq.alert>
    </noscript>
</div>
@endsection

@push('scripts')
<script src="{{ $cqVersion('js/review/theme_preview.js') }}"></script>
<script src="{{ $cqVersion('js/review/ui.js') }}"></script>
<script src="{{ $cqVersion('js/review/cutout.js') }}"></script>
@endpush
