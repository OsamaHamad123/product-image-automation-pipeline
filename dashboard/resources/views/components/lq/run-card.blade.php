{{--
    Run-status mini card for the dark sidebar.
    The layout renders it with `live`: the layout script then polls /api/batch-status and fills it
    (hooks: data-lq-runcard, data-lq-run-*). Without `live` it is a static picture of one state (UI kit).
    <x-lq.run-card live />
    <x-lq.run-card state="running" text="الصفوف 62–101" count="24 / 40" :progress="60" />
    state: loading | idle | running | paused | error | unknown
--}}
@props([
    'live' => false,
    'state' => 'idle',
    'text' => null,
    'count' => null,
    'progress' => null,
    'href' => null,
])
@php
    $lqStates = [
        'loading' => ['label' => 'لحظة…', 'text' => 'جارٍ قراءة حالة التشغيل…', 'link' => 'صفحة التشغيل ←'],
        'idle' => ['label' => 'جاهز', 'text' => 'لا يوجد تشغيل الآن', 'link' => 'ابدأ تشغيلاً جديداً ←'],
        'running' => ['label' => 'يعمل', 'text' => 'جارٍ تجهيز التشغيل…', 'link' => 'عرض التفاصيل ←'],
        'paused' => ['label' => 'متوقف مؤقتاً', 'text' => 'التشغيل متوقف مؤقتاً', 'link' => 'عرض التفاصيل ←'],
        'error' => ['label' => 'توقف بعطل', 'text' => 'توقف التشغيل بسبب عطل.', 'link' => 'عرض التفاصيل ←'],
        'unknown' => ['label' => 'غير معروف', 'text' => 'تعذّر قراءة حالة التشغيل', 'link' => 'صفحة التشغيل ←'],
    ];
    $lqState = array_key_exists($state, $lqStates) ? $state : 'idle';
    $lqCopy = $lqStates[$lqState];
    $lqText = $text !== null && $text !== '' ? $text : $lqCopy['text'];
    $lqCount = $count !== null && $count !== '' ? (string) $count : null;
    $lqPct = is_numeric($progress) ? max(0, min(100, round((float) $progress))) : null;
    $lqHref = $href ?? route('dashboard.batch_automation');
@endphp
<section {{ $attributes->class(['lq-runcard']) }} data-state="{{ $lqState }}" aria-label="التشغيل الحالي" @if ($live) data-lq-runcard @endif>
    <div class="lq-runcard__head">
        <span class="lq-runcard__title" aria-hidden="true">التشغيل الحالي</span>
        <span class="lq-runcard__state"><span class="lq-runcard__dot" aria-hidden="true"></span><span data-lq-run-state @if ($live) aria-live="polite" @endif>{{ $lqCopy['label'] }}</span></span>
    </div>
    <div class="lq-runcard__text"><span data-lq-run-text>{{ $lqText }}</span><span data-lq-run-sep aria-hidden="true" @if ($lqCount === null) hidden @endif> · </span><strong data-lq-run-count @if ($lqCount === null) hidden @endif>{{ $lqCount }}</strong></div>
    <div class="lq-runcard__bar" data-lq-run-bar role="progressbar" aria-label="تقدّم التشغيل" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{{ $lqPct ?? 0 }}" @if ($lqPct === null) hidden @endif><div class="lq-runcard__fill" data-lq-run-fill style="width: {{ $lqPct ?? 0 }}%"></div></div>
    <a class="lq-runcard__link" href="{{ $lqHref }}" data-lq-run-link>{{ $lqCopy['link'] }}</a>
</section>
