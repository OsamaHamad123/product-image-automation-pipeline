{{--
    KPI tile: one number with its label and a short note. Becomes a link when `href` is given.
    <x-lq.kpi label="بانتظار مراجعتك" value="33" note="19 منها مقترحة وجاهزة" tone="success" dot="saffron" href="..." />
    tone (note colour): muted | success | warning | danger
    dot: teal | saffron | success | warning | danger | info | muted
--}}
@props([
    'label' => '',
    'value' => '',
    'note' => null,
    'tone' => 'muted',
    'dot' => null,
    'href' => null,
])
@php
    $lqTones = [
        'muted' => 'lq-kpi__note--muted',
        'success' => 'lq-kpi__note--success',
        'warning' => 'lq-kpi__note--warning',
        'danger' => 'lq-kpi__note--danger',
    ];
    $lqDots = [
        'teal' => 'lq-dot--teal',
        'saffron' => 'lq-dot--saffron',
        'success' => 'lq-dot--success',
        'warning' => 'lq-dot--warning',
        'danger' => 'lq-dot--danger',
        'info' => 'lq-dot--info',
        'muted' => 'lq-dot--muted',
    ];
    $lqTag = $href !== null && $href !== '' ? 'a' : 'div';
@endphp
<{{ $lqTag }} {{ $attributes->class(['lq-kpi']) }} @if ($lqTag === 'a') href="{{ $href }}" @endif>
    <span class="lq-kpi__label">
        @if ($dot && isset($lqDots[$dot]))
            <span class="lq-dot {{ $lqDots[$dot] }}" aria-hidden="true"></span>
        @endif
        {{ $label }}
    </span>
    <span class="lq-kpi__value">{{ $value }}</span>
    @if ($note !== null && $note !== '')
        <span class="lq-kpi__note {{ $lqTones[$tone] ?? $lqTones['muted'] }}">{{ $note }}</span>
    @endif
</{{ $lqTag }}>
