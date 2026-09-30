{{--
    Segmented control. `options` is [value => label].
    With `name`: native radio inputs (works in a <form>, arrow keys built in, no script).
    Without `name`: toggle buttons (aria-pressed). The layout script keeps one pressed and fires a bubbling
    `lq:change` event on the group with detail.value.
    <x-lq.segmented label="النطاق" name="scope" :options="['all' => 'كل الشيت', 'brand' => 'ماركة', 'rows' => 'صفوف محددة']" value="rows" fill />
    <x-lq.segmented label="الفترة" :options="['24h' => 'آخر 24 ساعة', '7d' => 'آخر 7 أيام']" value="24h" size="sm" />
--}}
@props([
    'options' => [],
    'value' => null,
    'name' => null,
    'label' => '',
    'fill' => false,
    'size' => 'md',
])
@php
    $lqRadio = $name !== null && $name !== '';
    $lqOptions = $options instanceof \Illuminate\Support\Collection ? $options->all() : (array) $options;
@endphp
<div {{ $attributes->class(['lq-segmented', $fill ? 'lq-segmented--fill' : '', $size === 'sm' ? 'lq-segmented--sm' : '']) }} role="{{ $lqRadio ? 'radiogroup' : 'group' }}" @if ($label !== '') aria-label="{{ $label }}" @endif @unless ($lqRadio) data-lq-segmented @endunless>
    @foreach ($lqOptions as $lqValue => $lqLabel)
        @if ($lqRadio)
            <label class="lq-segmented__option"><input type="radio" name="{{ $name }}" value="{{ $lqValue }}" @checked((string) $lqValue === (string) $value)><span class="lq-segmented__item">{{ $lqLabel }}</span></label>
        @else
            <button type="button" class="lq-segmented__item" data-value="{{ $lqValue }}" aria-pressed="{{ (string) $lqValue === (string) $value ? 'true' : 'false' }}">{{ $lqLabel }}</button>
        @endif
    @endforeach
</div>
