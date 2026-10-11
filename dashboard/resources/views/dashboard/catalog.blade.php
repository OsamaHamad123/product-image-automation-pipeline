{{--
    المراجعة (/catalog, nav: review): شاشة واحدة بوضعين على هوية لقطة، وللمدير وضع ثالث.
      منتج واحد (Main): الشريط الجانبي | مساحة العمل (صورة المنتج أولاً) | قائمة المراجعة على الطرف.
      بالجملة (BulkReview): ?mode=bulk — بطاقات المنتجات المنتظرة، واعتماد المقترحة بلا تحذير بالخلفية.
      فحص القص: ?mode=cutout (المدير بس، /cutout-check بيحوّل لهون) — الصور المنشورة على الغامق والفاتح والمربعات،
        public/js/review/cutout.js بيبنيه بحاوية من app.js، وcss/pages/cutout.css.
    روابط تستعملها الصفحات الأخرى: /catalog?row=N (افتح منتجاً)، /catalog?filter=failed (الأعطال)، /catalog?mode=bulk،
    /catalog?mode=cutout.

    البيانات: ReviewController::page يمرر الإعدادات (المسارات، مقاس اللوحة النهائية، حالة قاعدة البيانات) في data-config،
    والسكربتات في public/js/review/ تقرأ المنتجات من /api/products-json وحالة الطابور من /api/review/queue-state.
    الأقسام الثابتة هنا هيكل تحميل فقط؛ الواجهة الحية يبنيها app.js.
--}}
@extends('layouts.laqta')

@section('title', 'المراجعة')
@section('lq_nav', 'review')
@section('lq_main_class', 'lq-main--flush')
@section('body_class', 'page-review')

@php
    $rvVersion = static fn (string $path) => asset($path) . '?v=' . (@filemtime(public_path($path)) ?: '1');
@endphp

@push('styles')
<link rel="stylesheet" href="{{ $rvVersion('css/pages/review.css') }}">
{{-- وضع «فحص القص» (?mode=cutout، للمدير بس): cutout.js بيبني الواجهة بالحاوية اللي بيعطيها app.js --}}
<link rel="stylesheet" href="{{ $rvVersion('css/pages/cutout.css') }}">
@endpush

@section('content')
    @include('review.shell', ['config' => $reviewConfig, 'dbOnline' => $reviewDbOnline])
@endsection

@push('scripts')
{{-- «غامق / فاتح / مربعات» خلف الصورة المنشورة (single.js بيستدعيه وقت الرسم) --}}
<script src="{{ $rvVersion('js/review/theme_preview.js') }}"></script>
@foreach (['core', 'ui', 'jobs', 'single', 'bulk', 'cutout', 'app'] as $rvScript)
<script src="{{ $rvVersion('js/review/' . $rvScript . '.js') }}"></script>
@endforeach
@endpush
