{{--
    Settings · معالجة الصور. $processing = SettingsController::processingData(): the saved output size, background
    removal method and colour enhancement (system_settings keys that config.load_db_config reads before every run),
    the methods this machine can run (GrabCut / rembg only when installed: cli_bridge bg_methods), and bg: the
    «تجاوز عزل الخلفية» state (SettingsController::bgState). With the method «none» the tab says background removal
    is off and offers «رجّع عزل الخلفية (…)», which POSTs /api/settings/bg-method (public/js/settings.js).
    fallback: the switch «لما يخلص رصيد خدمة العزل: جرّب طريقة محلية مجانية» (system_settings.bg_fallback = local | off,
    config.BG_FALLBACK) is shown only when rembg is installed (the fallback is rembg with the BiRefNet model; GrabCut never runs by itself); its hidden bg_fallback_shown marks that it was
    on the form, so a save without it leaves the stored value alone.
--}}
@php $pr = $processing; @endphp
<form method="POST" action="{{ route('dashboard.save_settings') }}" class="lq-card lq-settings-card" aria-labelledby="lq-settings-processing-title">
    @csrf
    <input type="hidden" name="section" value="processing">
    <div class="lq-settings-card__head">
        <h2 class="lq-section-title" id="lq-settings-processing-title">معالجة الصور</h2>
        <p class="lq-settings-card__intro">هاي الإعدادات بتنطبق على الصور اللي بتنعالج من التشغيل الجاي. الصور المنشورة من قبل ما بتتغير.</p>
    </div>

    @if ($pr['bg']['off'])
        <div class="lq-alert lq-alert--warning lq-settings-bgoff" role="status" data-bg-off>
            <x-lq.icon name="alert" :size="18" class="lq-alert__icon" />
            <div class="lq-alert__body">
                <strong class="lq-alert__title">عزل الخلفية متوقف:</strong>
                الصور اللي بتعتمدها بتنتشر متل ما هي على لوحة بيضا، بدون أي طلب عزل مدفوع.
                <div class="lq-settings-bgoff__actions">
                    <button type="button" class="lq-btn lq-btn--secondary lq-btn--sm" data-bg-restore="{{ $pr['bg']['previous'] }}" @disabled((bool) $dbError)>رجّع عزل الخلفية ({{ $pr['bg']['previous_label'] }})</button>
                </div>
            </div>
        </div>
    @endif

    <label class="lq-field">
        <span class="lq-field__label">مقاس الصورة النهائية</span>
        <select name="output_canvas_size" class="lq-select lq-settings-field__control" @disabled((bool) $dbError)>
            @foreach ($pr['sizes'] as $size)
                <option value="{{ $size }}" @selected($size === $pr['size'])>{{ $size }} × {{ $size }} بكسل{{ $size === 800 ? ' (الافتراضي)' : '' }}</option>
            @endforeach
        </select>
        <span class="lq-field__hint">لوحة مربعة، والمنتج كامل بيعبّي 88% منها بالنص. هاد أصغر مقاس: إذا الصورة الأصلية كبيرة ومفصّلة بتنتشر أكبر (لحد 2048 بكسل) لتبين حادة على شاشة الموبايل.</span>
    </label>

    <label class="lq-field">
        <span class="lq-field__label">خلفية الصورة</span>
        <select name="output_background" class="lq-select lq-settings-field__control" @disabled((bool) $dbError)>
            @foreach ($pr['backgrounds'] as $value => $label)
                <option value="{{ $value }}" @selected($pr['background'] === $value)>{{ $label }}</option>
            @endforeach
        </select>
        <span class="lq-field__hint">الشفافة بلا ظل: التطبيق بيعرضها على الغامق والفاتح، والنسخة البيضا بتنطلب برابط.</span>
    </label>

    <fieldset class="lq-settings-fieldset" @disabled((bool) $dbError)>
        <legend class="lq-field__label">عزل الخلفية</legend>
        @foreach ($pr['methods'] as $value => $label)
            <label class="lq-check lq-settings-choice">
                <input type="radio" name="bg_removal_method" value="{{ $value }}" @checked($pr['method'] === $value)>
                <span>{{ $label }}</span>
            </label>
        @endforeach
        <span class="lq-field__hint" data-bg-hint>{{ $pr['hint'] }}</span>
    </fieldset>

    @if ($pr['fallback']['show'])
        <div class="lq-settings-switch-row" data-bg-fallback>
            <input type="hidden" name="bg_fallback_shown" value="1">
            <x-lq.switch name="bg_fallback" value="local" label="لما يخلص رصيد خدمة العزل: جرّب طريقة محلية مجانية" show-label :checked="$pr['fallback']['on']" :disabled="(bool) $dbError" />
            <span class="lq-field__hint">إذا رصيد PhotoRoom أو remove.bg (أو مفتاحه أو حصته) خلص، الصورة بتنعزل على هالجهاز بدل ما يفشل الاعتماد: عزل محلي بموديل BiRefNet (لازم يكون منزّل)، وبتمر على نفس فحص القص. ممكن يكون أقل دقة من PhotoRoom، وإذا ما نجح بتضل الصورة بانتظار مراجعتك. ما بتنتشر صورة بدون عزل إلا إذا اخترت أنت «بدون عزل الخلفية»، وGrabCut ما بيشتغل تلقائياً أبداً.</span>
        </div>
    @endif

    <div class="lq-settings-switch-row">
        <x-lq.switch name="enable_image_enhancement" value="true" label="تحسين الألوان" show-label :checked="$pr['enhance']" :disabled="(bool) $dbError" />
        <span class="lq-field__hint">مطفأ بالعادة لأنه ممكن يبهّت الألوان أو يزيدها عن الحقيقة.</span>
    </div>

    <div class="lq-settings-card__actions">
        <x-lq.button type="submit" icon="check" :disabled="(bool) $dbError">حفظ</x-lq.button>
    </div>
</form>
