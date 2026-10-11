{{--
    «روح على»: a row of in-page links to the cards of a long page, so a card is one click away instead of a scroll
    (and, on Health, instead of opening «تفاصيل متقدمة» first: health.js opens it for any #id inside it).
    <x-lq.jump-links label="روح على" :links="[['#services', 'الخدمات'], ['#eval-export', 'مجموعة اختبار', true]]" />
    A link's third item true = admin only (data-lq-admin: a reviewer's page hides it).
--}}
@props([
    'links' => [],
    'label' => 'روح على',
])
<nav {{ $attributes->class(['lq-jump']) }} aria-label="{{ $label }}">
    <span class="lq-jump__label" aria-hidden="true">{{ $label }}:</span>
    <ul class="lq-jump__list">
        @foreach ($links as $lqLink)
            <li @if (! empty($lqLink[2])) data-lq-admin @endif><a class="lq-jump__link" href="{{ $lqLink[0] }}">{{ $lqLink[1] }}</a></li>
        @endforeach
    </ul>
</nav>
