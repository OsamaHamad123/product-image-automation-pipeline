{{--
    «جهّز لقطة» on the Home page while setup is incomplete. $setupCard = SetupController::homeCard(): {ok, total, next,
    next_n, started, steps: [{n, title, status, tone, label}]}; the Home page passes null for a finished or configured
    install, or when the database does not answer. A link only: nothing on the dashboard waits for it.
--}}
@php
    $setupCardDots = ['success' => 'lq-dot--success', 'danger' => 'lq-dot--danger', 'info' => 'lq-dot--info', 'muted' => 'lq-dot--muted'];
@endphp
<section class="lq-card lq-home-setup" aria-labelledby="lq-home-setup-title" data-home-setup>
    <div class="lq-home-setup__text">
        <h2 class="lq-card__title" id="lq-home-setup-title">جهّز لقطة</h2>
        <p class="lq-home-setup__lead">{{ $setupCard['ok'] }} من {{ $setupCard['total'] }} خطوات تمام. الخطوة الجاية: «{{ $setupCard['next'] }}». التقدّم محفوظ، كمّل من وين ما وقفت.</p>
        <ol class="lq-home-setup__steps" aria-label="خطوات التجهيز">
            @foreach ($setupCard['steps'] as $setupStep)
                <li class="lq-home-setup__step" data-status="{{ $setupStep['status'] }}"><span class="lq-dot {{ $setupCardDots[$setupStep['tone']] ?? 'lq-dot--muted' }}" aria-hidden="true"></span>{{ $setupStep['title'] }}<span class="lq-sr-only">: {{ $setupStep['label'] }}</span></li>
            @endforeach
        </ol>
    </div>
    <a class="lq-btn lq-btn--primary lq-home-setup__go" href="{{ route('dashboard.setup') }}">{{ $setupCard['started'] ? 'كمّل التجهيز' : 'ابدأ التجهيز' }}<x-lq.icon name="arrow-left" :size="18" :stroke="2" /></a>
</section>
