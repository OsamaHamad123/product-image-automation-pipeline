{{--
    Filter pill with a count (review queue and bulk review). It is a toggle button: the page's script flips aria-pressed.
    <x-lq.filter :active="true" count="33">الكل</x-lq.filter>
--}}
@props([
    'active' => false,
    'count' => null,
])
<button type="button" {{ $attributes->class(['lq-filter']) }} aria-pressed="{{ $active ? 'true' : 'false' }}">{{ $slot }}@if ($count !== null && $count !== '')<span class="lq-filter__count">{{ $count }}</span>@endif</button>
