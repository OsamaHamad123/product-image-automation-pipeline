{{--
    المراجعة (/catalog, nav: review): شاشة واحدة بوضعين على هوية لقطة.
      منتج واحد (Main): الشريط الجانبي | مساحة العمل (صورة المنتج أولاً) | قائمة المراجعة على الطرف.
      بالجملة (BulkReview): ?mode=bulk — بطاقات المنتجات المنتظرة، واعتماد المقترحة بلا تحذير بالخلفية.
    روابط تستعملها الصفحات الأخرى: /catalog?row=N (افتح منتجاً)، /catalog?filter=failed (الأعطال)، /catalog?mode=bulk.

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
@endpush

@section('content')
    @include('review.shell', ['config' => $reviewConfig, 'dbOnline' => $reviewDbOnline])
@endsection

@push('scripts')
{{-- «غامق / فاتح / مربعات» خلف الصورة المنشورة (single.js بيستدعيه وقت الرسم) --}}
<script src="{{ $rvVersion('js/review/theme_preview.js') }}"></script>
@foreach (['core', 'ui', 'jobs', 'single', 'bulk', 'app'] as $rvScript)
<script src="{{ $rvVersion('js/review/' . $rvScript . '.js') }}"></script>
@endforeach
@endpush
