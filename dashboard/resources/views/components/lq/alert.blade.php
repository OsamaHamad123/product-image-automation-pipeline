{{--
    Inline alert or page banner. The title is written in bold before the text (include its colon).
    <x-lq.alert variant="warning" title="تأكد قبل الاعتماد:">الصورة لبطاطا «رفيعة» والشيت ما حدد النوع.</x-lq.alert>
    <x-lq.alert variant="danger" banner title="PhotoRoom بطيء اليوم:" action-href="..." action-label="التفاصيل">...</x-lq.alert>
    variant: info | warning | danger | success | neutral    icon: any x-lq.icon name (defaults per variant)
    A danger alert gets role="alert" unless `role` is given; pass role="" to drop it.
--}}
@props([
    'variant' => 'info',
    'title' => null,
    'icon' => null,
    'banner' => false,
    'actionHref' => null,
    'actionLabel' => null,
    'role' => null,
])
@php
    $lqAlerts = [
        'info' => ['lq-alert--info', 'info'],
        'warning' => ['lq-alert--warning', 'alert'],
        'danger' => ['lq-alert--danger', 'alert'],
        'success' => ['lq-alert--success', 'check'],
        'neutral' => ['lq-alert--neutral', 'shield'],
    ];
    $lqAlert = $lqAlerts[$variant] ?? $lqAlerts['info'];
    $lqRole = $role !== null ? (string) $role : ($variant === 'danger' ? 'alert' : '');
@endphp
<div {{ $attributes->class(['lq-alert', $lqAlert[0], $banner ? 'lq-alert--banner' : '']) }} @if ($lqRole !== '') role="{{ $lqRole }}" @endif>
    <x-lq.icon :name="$icon ?: $lqAlert[1]" :size="$banner ? 20 : 18" class="lq-alert__icon" />
    <div class="lq-alert__body">
        @if ($title !== null && $title !== '')
            <strong class="lq-alert__title">{{ $title }}</strong>
        @endif
        {{ $slot }}
    </div>
    @if ($actionHref && $actionLabel)
        <a class="lq-alert__action" href="{{ $actionHref }}">{{ $actionLabel }}</a>
    @endif
</div>
