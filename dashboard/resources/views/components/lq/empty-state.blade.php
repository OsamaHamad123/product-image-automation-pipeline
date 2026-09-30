{{--
    Empty state: what is missing, in one plain sentence, and what to do next (slot = action buttons).
    <x-lq.empty-state icon="review" title="ما في منتجات بانتظار مراجعتك" text="كل الصور انعتمدت. شغّل بحثاً جديداً لتجيب نتائج جديدة.">
        <x-lq.button variant="secondary" icon="play" href="...">تشغيل جديد</x-lq.button>
    </x-lq.empty-state>
    plain: no dashed frame (inside a card)
--}}
@props([
    'title' => '',
    'text' => null,
    'icon' => 'search',
    'plain' => false,
    'level' => 3,
])
@php
    $lqLevel = in_array((int) $level, [2, 3, 4], true) ? (int) $level : 3;
@endphp
<div {{ $attributes->class(['lq-empty', $plain ? 'lq-empty--plain' : '']) }}>
    @if ($icon)
        <span class="lq-empty__icon"><x-lq.icon :name="$icon" :size="24" /></span>
    @endif
    <h{{ $lqLevel }} class="lq-empty__title">{{ $title }}</h{{ $lqLevel }}>
    @if ($text !== null && $text !== '')
        <p class="lq-empty__text">{{ $text }}</p>
    @endif
    @unless ($slot->isEmpty())
        <div class="lq-empty__actions">{{ $slot }}</div>
    @endunless
</div>
