{{--
    Small tinted result tile (the Overview "last run" row).
    <x-lq.stat tone="success" value="32" label="مقترحة" />
    tone: success | warning | danger | info | teal | muted
--}}
@props([
    'label' => '',
    'value' => '',
    'tone' => 'muted',
])
@php
    $lqTones = [
        'success' => 'lq-stat--success',
        'warning' => 'lq-stat--warning',
        'danger' => 'lq-stat--danger',
        'info' => 'lq-stat--info',
        'teal' => 'lq-stat--teal',
        'muted' => 'lq-stat--muted',
    ];
@endphp
<div {{ $attributes->class(['lq-stat', $lqTones[$tone] ?? $lqTones['muted']]) }}>
    <span class="lq-stat__value">{{ $value }}</span>
    <span class="lq-stat__label">{{ $label }}</span>
</div>
