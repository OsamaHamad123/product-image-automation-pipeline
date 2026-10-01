{{--
    The Laqta mark: a camera frame closing in on a saffron point. Decorative; the wordmark next to it carries the name.
    <x-lq.logo />  <x-lq.logo :size="96" />
--}}
@props([
    'size' => 38,
])
@php
    $lqLogoSize = is_numeric($size) ? 0 + $size : 38;
@endphp
<svg {{ $attributes->class(['lq-brand__mark']) }} width="{{ $lqLogoSize }}" height="{{ $lqLogoSize }}" viewBox="0 0 36 36" aria-hidden="true" focusable="false"><rect width="36" height="36" rx="10" fill="#0F766E"></rect><path d="M10 14v-4h4M22 10h4v4M26 22v4h-4M14 26h-4v-4" stroke="#FFFFFF" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"></path><circle cx="18" cy="18" r="3.2" fill="#F59E0B"></circle></svg>
