{{--
    Button, or a link styled as a button when `href` is given.
    <x-lq.button variant="primary" icon="check" kbd="Enter">اعتماد ونشر</x-lq.button>
    <x-lq.button variant="secondary" href="{{ route('dashboard.batch_automation') }}" icon="play">تشغيل جديد</x-lq.button>
    <x-lq.button variant="danger-solid" confirm="رح تنحذف 3 صور. متأكد؟">حذف الصور</x-lq.button>
    <x-lq.button variant="ghost" icon="refresh" label="تحديث" />   icon-only: `label` becomes the aria-label
    variant: primary | secondary | danger | danger-solid | soft | ghost    size: sm | md | lg
    `confirm` asks before the click goes through (handled by the layout script): no silent destructive button.
--}}
@props([
    'variant' => 'primary',
    'size' => 'md',
    'href' => null,
    'type' => 'button',
    'icon' => null,
    'iconEnd' => null,
    'kbd' => null,
    'count' => null,
    'label' => null,
    'disabled' => false,
    'loading' => false,
    'block' => false,
    'confirm' => null,
])
@php
    $lqVariants = [
        'primary' => 'lq-btn--primary',
        'secondary' => 'lq-btn--secondary',
        'danger' => 'lq-btn--danger',
        'danger-solid' => 'lq-btn--danger-solid',
        'soft' => 'lq-btn--soft',
        'ghost' => 'lq-btn--ghost',
    ];
    $lqSizes = ['sm' => 'lq-btn--sm', 'md' => '', 'lg' => 'lq-btn--lg'];
    $lqIconOnly = $label !== null && $label !== '' && $slot->isEmpty();
    $lqInactive = (bool) $disabled || (bool) $loading;
    $lqIconSize = $size === 'sm' ? 16 : 18;
    $lqClasses = array_filter([
        'lq-btn',
        $lqVariants[$variant] ?? $lqVariants['primary'],
        $lqSizes[$size] ?? '',
        $lqIconOnly ? 'lq-btn--icon' : '',
        $block ? 'lq-btn--block' : '',
        $loading ? 'is-loading' : '',
    ]);
    $lqType = in_array($type, ['button', 'submit', 'reset'], true) ? $type : 'button';
    $lqKbd = $kbd !== null && $kbd !== '' ? (string) $kbd : null;
@endphp
@if ($href !== null)
<a {{ $attributes->class($lqClasses) }} @if ($lqInactive) role="link" aria-disabled="true" @else href="{{ $href }}" @endif @if ($lqIconOnly) aria-label="{{ $label }}" title="{{ $label }}" @endif @if ($lqKbd !== null) aria-keyshortcuts="{{ $lqKbd }}" @endif @if ($confirm) data-lq-confirm="{{ $confirm }}" @endif @if ($loading) aria-busy="true" @endif>
@else
<button type="{{ $lqType }}" {{ $attributes->class($lqClasses) }} @disabled($lqInactive) @if ($lqIconOnly) aria-label="{{ $label }}" title="{{ $label }}" @endif @if ($lqKbd !== null) aria-keyshortcuts="{{ $lqKbd }}" @endif @if ($confirm) data-lq-confirm="{{ $confirm }}" @endif @if ($loading) aria-busy="true" @endif>
@endif
@if ($loading)
    <span class="lq-spinner" aria-hidden="true"></span>
@elseif ($icon)
    <x-lq.icon :name="$icon" :size="$lqIconSize" :stroke="2" />
@endif
{{ $slot }}
@if ($count !== null && $count !== '')
    <span class="lq-btn__count">{{ $count }}</span>
@endif
@if ($iconEnd)
    <x-lq.icon :name="$iconEnd" :size="$lqIconSize" :stroke="2" />
@endif
@if ($lqKbd !== null)
    <kbd class="lq-kbd" aria-hidden="true">{{ $lqKbd }}</kbd>
@endif
@if ($href !== null)
</a>
@else
</button>
@endif
