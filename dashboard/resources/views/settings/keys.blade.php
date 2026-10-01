{{--
    Settings · المفاتيح. $keys = SettingsController::keyRows(): per provider its purpose, whether a key is saved
    (database or environment) and the last connection check if it ran after the key changed.
    A stored key is never printed, not even in part. «تغيير» opens an empty, write-only field; an empty field keeps
    the stored key. Each provider's form saves its own section only.
--}}
@php
    $keyDots = ['success' => 'lq-dot--success', 'danger' => 'lq-dot--danger', 'warning' => 'lq-dot--warning', 'muted' => 'lq-dot--muted'];
    $keyLabels = [
        'serper_api_key' => 'مفتاح Serper الجديد',
        'gemini_api_key' => 'مفتاح Gemini الجديد',
        'photoroom_api_key' => 'مفتاح PhotoRoom الجديد',
        'cloudinary_api_key' => 'مفتاح Cloudinary الجديد (API Key)',
        'cloudinary_api_secret' => 'الرمز السري لـ Cloudinary (API Secret)',
    ];
@endphp
<section class="lq-card lq-settings-card" aria-labelledby="lq-settings-keys-title">
    <div class="lq-settings-card__head">
        <h2 class="lq-section-title" id="lq-settings-keys-title">المفاتيح</h2>
        <p class="lq-settings-card__intro">المفاتيح بتنحفظ بقاعدة البيانات وما بتنعرض هون أبداً. لتغيير مفتاح اضغط «تغيير» والصق الجديد؛ الحقل الفاضي بيخلي المفتاح المحفوظ متل ما هو.</p>
    </div>

    <ul class="lq-keys">
        @foreach ($keys as $row)
            <li class="lq-keys__row" data-key-row="{{ $row['id'] }}">
                <span class="lq-keys__name">
                    <span class="lq-keys__title">{{ $row['name'] }}</span>
                    <span class="lq-keys__use">{{ $row['use'] }}</span>
                </span>
                <span class="lq-keys__state lq-keys__state--{{ $row['tone'] }}">
                    <span class="lq-dot {{ $keyDots[$row['tone']] ?? 'lq-dot--muted' }}" aria-hidden="true"></span>
                    {{ $row['state'] }}
                    @if ($row['from_env'])
                        <span class="lq-keys__source">من ملف ‎.env</span>
                    @endif
                </span>
                @if ($row['id'] === 'google_sheet')
                    <x-lq.button variant="secondary" size="sm" class="lq-keys__change" :href="route('dashboard.settings') . '?tab=sheet'">تغيير</x-lq.button>
                @else
                    <x-lq.button variant="secondary" size="sm" class="lq-keys__change" aria-expanded="false" aria-controls="lq-key-form-{{ $row['id'] }}" data-key-toggle="{{ $row['id'] }}" :disabled="(bool) $dbError">تغيير</x-lq.button>
                    <form class="lq-keys__form" id="lq-key-form-{{ $row['id'] }}" method="POST" action="{{ route('dashboard.save_settings') }}" autocomplete="off" data-key-form="{{ $row['id'] }}" data-key-name="{{ $row['name'] }}" hidden>
                        @csrf
                        <input type="hidden" name="section" value="{{ $row['id'] }}">
                        @foreach ($row['fields'] as $field)
                            @if ($field === 'cloudinary_cloud_name')
                                <label class="lq-field">
                                    <span class="lq-field__label">اسم الحساب (Cloud name)</span>
                                    <input type="text" class="lq-input" name="cloudinary_cloud_name" dir="ltr" value="{{ $row['cloud_name'] }}" autocomplete="off" spellcheck="false">
                                </label>
                            @else
                                <label class="lq-field">
                                    <span class="lq-field__label">{{ $keyLabels[$field] ?? $field }}</span>
                                    <input type="password" class="lq-input" name="{{ $field }}" dir="ltr" value="" autocomplete="new-password" spellcheck="false" placeholder="{{ $row['saved'] ? 'فاضي = خلي المحفوظ' : 'الصق المفتاح هون' }}">
                                </label>
                                @if ($row['saved'] && !$row['from_env'])
                                    <label class="lq-check lq-keys__clear">
                                        <input type="checkbox" name="clear_{{ $field }}" value="1" data-key-clear>
                                        <span>امسح المحفوظ</span>
                                    </label>
                                @endif
                            @endif
                        @endforeach
                        <div class="lq-keys__actions">
                            <x-lq.button type="submit" size="sm" icon="check">حفظ</x-lq.button>
                            <x-lq.button variant="ghost" size="sm" data-key-cancel="{{ $row['id'] }}">إلغاء</x-lq.button>
                        </div>
                    </form>
                @endif
            </li>
        @endforeach
    </ul>
</section>
