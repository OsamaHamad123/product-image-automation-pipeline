{{--
    Settings · معالجة الصور. $processing = SettingsController::processingData(): the saved output size, background
    removal method and colour enhancement (system_settings keys that config.load_db_config reads before every run).
--}}
@php $pr = $processing; @endphp
<form method="POST" action="{{ route('dashboard.save_settings') }}" class="lq-card lq-settings-card" aria-labelledby="lq-settings-processing-title">
    @csrf
    <input type="hidden" name="section" value="processing">
    <div class="lq-settings-card__head">
        <h2 class="lq-section-title" id="lq-settings-processing-title">معالجة الصور</h2>
        <p class="lq-settings-card__intro">هاي الإعدادات بتنطبق على الصور اللي بتنعالج من التشغيل الجاي. الصور المنشورة من قبل ما بتتغير.</p>
    </div>

    <label class="lq-field">
        <span class="lq-field__label">مقاس الصورة النهائية</span>
        <select name="output_canvas_size" class="lq-select lq-settings-field__control" @disabled((bool) $dbError)>
            @foreach ($pr['sizes'] as $size)
                <option value="{{ $size }}" @selected($size === $pr['size'])>{{ $size }} × {{ $size }} بكسل{{ $size === 800 ? ' (الافتراضي)' : '' }}</option>
            @endforeach
        </select>
        <span class="lq-field__hint">لوحة مربعة بيضا، والمنتج كامل بيعبّي 88% منها بالنص.</span>
    </label>

    <fieldset class="lq-settings-fieldset" @disabled((bool) $dbError)>
        <legend class="lq-field__label">عزل الخلفية</legend>
        @foreach ($pr['methods'] as $value => $label)
            <label class="lq-check lq-settings-choice">
                <input type="radio" name="bg_removal_method" value="{{ $value }}" @checked($pr['method'] === $value)>
                <span>{{ $label }}</span>
            </label>
        @endforeach
        <span class="lq-field__hint">إذا فشل العزل، الصورة ما بتنزل الشيت بصمت: بتستنى مراجعتك.</span>
    </fieldset>

    <div class="lq-settings-switch-row">
        <x-lq.switch name="enable_image_enhancement" value="true" label="تحسين الألوان" show-label :checked="$pr['enhance']" :disabled="(bool) $dbError" />
        <span class="lq-field__hint">مطفأ بالعادة لأنه ممكن يبهّت الألوان أو يزيدها عن الحقيقة.</span>
    </div>

    <div class="lq-settings-card__actions">
        <x-lq.button type="submit" icon="check" :disabled="(bool) $dbError">حفظ</x-lq.button>
    </div>
</form>
