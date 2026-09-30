{{--
    Page header: the page's single <h1>, an optional line above it and a description below, actions on the other side.
    <x-lq.page-header title="التشغيل" description="النظام بيبحث عن صور المنتجات وبيجهّزها لمراجعتك.">
        <x-slot:actions><x-lq.button icon="play">ابدأ التشغيل</x-lq.button></x-slot:actions>
    </x-lq.page-header>
--}}
@props([
    'title' => '',
    'eyebrow' => null,
    'description' => null,
])
<header {{ $attributes->class(['lq-page-header']) }}>
    <div class="lq-page-header__text">
        @if ($eyebrow !== null && $eyebrow !== '')
            <span class="lq-page-header__eyebrow">{{ $eyebrow }}</span>
        @endif
        <h1 class="lq-page-title">{{ $title }}</h1>
        @if ($description !== null && $description !== '')
            <p class="lq-page-header__desc">{{ $description }}</p>
        @endif
        {{ $slot }}
    </div>
    @if (isset($actions) && ! $actions->isEmpty())
        <div class="lq-page-header__actions">{{ $actions }}</div>
    @endif
</header>
