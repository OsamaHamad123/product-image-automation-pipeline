{{--
    لقطة · «النظام»: الصحة والإعدادات والتجهيز بصفحة وحدة بشريط جانبي واحد (روابط ?tab=، صفحة كاملة لكل قسم).
    SystemController::show renders it with:
      $systemTab      the section on screen (SystemController::tabFor: a reviewer asking for an admin section gets status)
      $systemKind     health | settings | setup: whose partial fills the panel and which script the page loads
      $systemGroups   SystemController::groups(): the rail, {key: {label, admin, tabs: {tab: {label, hint}}}}; a reviewer
                      gets «الحالة» only (status, search, logs)
      $setupLink      {ok, total} while setup is unfinished (SetupController::homeCard, admin only): «كمّل التجهيز (N من 5)»
                      at the top of the rail; null otherwise
      and the data of the partial: HealthController::pageData (diagnostics.blade.php), SettingsController::panelData
      (settings.blade.php, the six settings tabs) or SetupController::pageData (setup.blade.php, ?step= kept).
    Scripts: health.js on the health sections, settings.js on the settings tabs (its unsaved-change guard asks before a
    rail link leaves a changed form), settings.js + setup.js on the setup tab (setup.js reads window.LaqtaSettings).
    settings.css carries the rail, so it is on every section.
--}}
@extends('layouts.laqta')

@section('title', 'النظام')
@section('lq_nav', 'system')
@section('body_class', 'lq-page-system')

@push('styles')
    <link rel="stylesheet" href="{{ asset('css/pages/settings.css') }}?v={{ @filemtime(public_path('css/pages/settings.css')) ?: '1' }}">
    @if ($systemKind === 'health')
        <link rel="stylesheet" href="{{ asset('css/pages/health.css') }}?v={{ @filemtime(public_path('css/pages/health.css')) ?: '1' }}">
    @elseif ($systemKind === 'setup')
        <link rel="stylesheet" href="{{ asset('css/pages/setup.css') }}?v={{ @filemtime(public_path('css/pages/setup.css')) ?: '1' }}">
    @endif
@endpush

@section('content')
<div class="lq-settings lq-system" data-system-page data-tab="{{ $systemTab }}" @if ($systemKind === 'settings') data-settings-page @endif>
    <x-lq.page-header title="النظام" description="حالة لقطة وإعداداتها وأدواتها بمكان واحد: اختار قسم من الشريط." />

    <div class="lq-settings__body">
        <nav class="lq-settings__tabs lq-system__rail" aria-label="أقسام النظام">
            @if ($setupLink)
                <a class="lq-system__setup" href="{{ route('dashboard.system', ['tab' => 'setup']) }}" data-system-setup-link>
                    <x-lq.icon name="sparkle" :size="16" :stroke="2" />كمّل التجهيز ({{ $setupLink['ok'] }} من {{ $setupLink['total'] }})
                </a>
            @endif
            @foreach ($systemGroups as $groupKey => $group)
                <div class="lq-system__group" role="group" aria-labelledby="lq-system-group-{{ $groupKey }}" data-system-group="{{ $groupKey }}">
                    <span class="lq-system__group-label" id="lq-system-group-{{ $groupKey }}">{{ $group['label'] }}</span>
                    @foreach ($group['tabs'] as $key => $item)
                        <a href="{{ route('dashboard.system', ['tab' => $key]) }}" @class(['lq-settings__tab', 'is-active' => $systemTab === $key]) data-system-tab="{{ $key }}" @if ($systemTab === $key) aria-current="page" @endif>
                            <span class="lq-settings__tab-label">{{ $item['label'] }}</span>
                            <span class="lq-settings__tab-hint">{{ $item['hint'] }}</span>
                        </a>
                    @endforeach
                </div>
            @endforeach
        </nav>

        <div class="lq-settings__panel" id="lq-system-panel">
            @switch ($systemKind)
                @case('health')
                    @include('dashboard.diagnostics')
                    @break
                @case('settings')
                    @include('dashboard.settings')
                    @break
                @case('setup')
                    @include('dashboard.setup')
                    @break
            @endswitch
        </div>
    </div>
</div>
@endsection

@push('scripts')
    @if ($systemKind === 'health')
        <script src="{{ asset('js/health.js') }}?v={{ @filemtime(public_path('js/health.js')) ?: '1' }}"></script>
    @else
        <script src="{{ asset('js/settings.js') }}?v={{ @filemtime(public_path('js/settings.js')) ?: '1' }}"></script>
        @if ($systemKind === 'setup')
            <script src="{{ asset('js/setup.js') }}?v={{ @filemtime(public_path('js/setup.js')) ?: '1' }}"></script>
        @endif
    @endif
@endpush
