{{--
    Toggle switch (a real checkbox with role="switch"). `label` is the accessible name; it is shown only with `show-label`.
    The words beside the track say the current state (defaults: مفعّل / مطفأ).
    <x-lq.switch name="auto_publish" label="تفعيل النشر الآلي" />
    <x-lq.switch name="bg_removal" label="عزل الخلفية" checked show-label />
    Other attributes (id, form, data-*, wire:*) go to the <input>.
--}}
@props([
    'name' => null,
    'value' => '1',
    'checked' => false,
    'disabled' => false,
    'label' => '',
    'showLabel' => false,
    'on' => 'مفعّل',
    'off' => 'مطفأ',
])
<label @class(['lq-switch', $attributes->get('class')])>
    <span class="{{ $showLabel ? 'lq-switch__label' : 'lq-sr-only' }}">{{ $label }}</span>
    <input type="checkbox" role="switch" class="lq-switch__input" @if ($name !== null && $name !== '') name="{{ $name }}" value="{{ $value }}" @endif @checked($checked) @disabled($disabled) {{ $attributes->except('class') }}>
    <span class="lq-switch__track" aria-hidden="true"><span class="lq-switch__thumb"></span></span>
    <span class="lq-switch__state" aria-hidden="true"><span class="lq-switch__on">{{ $on }}</span><span class="lq-switch__off">{{ $off }}</span></span>
</label>
