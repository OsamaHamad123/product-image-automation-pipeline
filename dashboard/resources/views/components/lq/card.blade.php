{{--
    White surface with an optional header (title, meta, actions slot) and footer slot.
    <x-lq.card title="آخر تشغيل" meta="اليوم 09:40 · 21 دقيقة">
        <x-slot:actions><a class="lq-link" href="#">التفاصيل</a></x-slot:actions>
        ...
    </x-lq.card>
    variant: default | muted | dark    padding: md | compact | flush    as: section | div | article | aside
--}}
@props([
    'title' => null,
    'meta' => null,
    'variant' => 'default',
    'padding' => 'md',
    'as' => 'section',
    'level' => 2,
])
@php
    $lqTag = in_array($as, ['section', 'div', 'article', 'aside'], true) ? $as : 'section';
    $lqLevel = in_array((int) $level, [2, 3, 4], true) ? (int) $level : 2;
    $lqVariants = ['default' => '', 'muted' => 'lq-card--muted', 'dark' => 'lq-card--dark'];
    $lqPaddings = ['md' => '', 'compact' => 'lq-card--compact', 'flush' => 'lq-card--flush'];
    $lqHasTitle = $title !== null && $title !== '';
    $lqHasMeta = $meta !== null && $meta !== '';
    $lqHasActions = isset($actions) && ! $actions->isEmpty();
@endphp
<{{ $lqTag }} {{ $attributes->class(['lq-card', $lqVariants[$variant] ?? '', $lqPaddings[$padding] ?? '']) }}>
@if ($lqHasTitle || $lqHasMeta || $lqHasActions)
    <div class="lq-card__header">
        @if ($lqHasTitle)
            <h{{ $lqLevel }} class="lq-card__title">{{ $title }}</h{{ $lqLevel }}>
        @endif
        @if ($lqHasMeta || $lqHasActions)
            <div class="lq-card__actions">
                @if ($lqHasMeta)
                    <span class="lq-card__meta">{{ $meta }}</span>
                @endif
                @if ($lqHasActions)
                    {{ $actions }}
                @endif
            </div>
        @endif
    </div>
@endif
{{ $slot }}
@isset($footer)
    <div class="lq-card__footer">{{ $footer }}</div>
@endisset
</{{ $lqTag }}>
