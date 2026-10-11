{{--
    لقطة · الإعدادات: the six settings tabs inside «النظام» (resources/views/dashboard/system.blade.php: the page header,
    the rail and the scripts live there; GET /settings?tab= redirects to /system?tab=…). SettingsController::panelData
    gives it:
      $tab            the active tab (?tab=sheet|keys|models|auto-publish|processing|advanced)
      $dbError        set when system_settings cannot be read: the page says so and nothing can be saved
      $flash          success / error / warnings of the last save
      $fieldErrors    the same refused values next to their field (setting name => message, session field_errors):
                      the partials put $fieldAttrs('name') on the control (aria-invalid + aria-describedby) and
                      $fieldError('name') right under it; settings.js focuses the first one on load
      one of $sheet | $keys | $models | $autoPublish | $processing | $advanced for the active tab
      (resources/views/settings/*)
    Stored keys are never printed, not even in part: the keys tab shows «محفوظ / غير محفوظ» and an empty,
    write-only field; an empty field keeps the stored key. Each form saves only its own section (POST /settings), and its
    redirect lands on its card (/system?tab=…#id, SettingsController::anchorFor; the flash shows at the top and as a toast).
    public/js/settings.js: the sheet preview/save (POST /api/sheet/preview, /api/sheet/save), the key forms,
    the confirmation of the auto-publish switch, and «ما انحفظ» before a link (the rail too) leaves a changed form.
--}}
@php
    // a refused value next to its field; the @include'd partials see these closures
    $fieldErrors = $fieldErrors ?? [];
    $fieldErrorId = fn (string $key) => 'lq-settings-error-' . preg_replace('/[^A-Za-z0-9_-]+/', '-', $key);
    // on the control: aria-invalid + aria-describedby (the message of $errorKey, default its own) when refused;
    // $describedBy (space-separated ids) always
    $fieldAttrs = function (string $key, string $describedBy = '', ?string $errorKey = null) use ($fieldErrors, $fieldErrorId) {
        $ids = trim($describedBy);
        $invalid = isset($fieldErrors[$key]);
        if ($invalid) {
            $ids = trim($fieldErrorId($errorKey ?? $key) . ' ' . $ids);
        }
        $html = ($invalid ? 'aria-invalid="true" ' : '') . ($ids !== '' ? 'aria-describedby="' . e($ids) . '"' : '');
        return new \Illuminate\Support\HtmlString(trim($html));
    };
    // right under the control: the message (nothing when the value was not refused)
    $fieldError = fn (string $key) => new \Illuminate\Support\HtmlString(isset($fieldErrors[$key])
        ? '<span class="lq-field__error lq-settings-error" id="' . e($fieldErrorId($key)) . '" data-settings-field-error>'
            . e($fieldErrors[$key]) . '</span>'
        : '');
@endphp
<div class="lq-settings__content" data-settings-tab="{{ $tab }}">
    @if (!empty($flash['success']))
        <x-lq.alert variant="success" role="status" data-settings-flash="success">{{ $flash['success'] }}</x-lq.alert>
    @endif
    @if (!empty($flash['error']))
        <x-lq.alert variant="danger" data-settings-flash="danger">{{ $flash['error'] }}</x-lq.alert>
    @endif
    @foreach ($flash['warnings'] ?? [] as $warning)
        <x-lq.alert variant="warning" role="status" data-settings-flash="warning">{{ $warning }}</x-lq.alert>
    @endforeach

    @if ($dbError && $tab !== 'sheet')
        <x-lq.empty-state icon="alert" title="ما قدرنا نقرأ الإعدادات" :text="$dbError . ' بلّغ المطوّر، وحدّث الصفحة بعد ما تنصلح. ما تغيّر ولا إعداد.'">
            <x-lq.button variant="secondary" icon="refresh" :href="route('dashboard.system', ['tab' => $tab])">حدّث الصفحة</x-lq.button>
        </x-lq.empty-state>
    @else
    @switch($tab)
        @case('sheet')
            @include('settings.sheet')
            @break
        @case('keys')
            @include('settings.keys')
            @break
        @case('models')
            @include('settings.models')
            @break
        @case('auto-publish')
            @include('settings.auto_publish')
            @break
        @case('processing')
            @include('settings.processing')
            @break
        @case('advanced')
            @include('settings.advanced')
            @break
    @endswitch
    @endif
</div>
