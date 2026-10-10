{{--
    Settings · نماذج التحقق. $models = SettingsController::modelsData(): the label reader for every product
    (verifier_primary), the strong second look (verifier_strong, or 'off'), its monthly budget
    (verifier_monthly_budget_usd) with this month's spend from verifier_spend, and the editable prices per 1M tokens
    (model_prices) with the estimated cost per 100 products, and the reading of abbreviated sheet names
    (query_normalizer: gemini | off, catalog_match/normalizer.py). Saving accepts only the supported list
    (SettingsController::VERIFIER_MODELS = catalog_match/verifiers/registry.SUPPORTED_MODELS). No key is read here:
    a missing Gemini / Anthropic key for a chosen model is only said.
--}}
@php $md = $models; @endphp
<form method="POST" action="{{ route('dashboard.save_settings') }}" id="lq-settings-models" class="lq-card lq-settings-card" aria-labelledby="lq-settings-models-title" autocomplete="off" data-models-form>
    @csrf
    <input type="hidden" name="section" value="models">
    <div class="lq-settings-card__head">
        <h2 class="lq-section-title" id="lq-settings-models-title">نماذج التحقق</h2>
        <p class="lq-settings-card__intro">النموذج الأساسي بيقرأ ملصق كل صورة مرشحة. إذا ما كان متأكد من الصورة الأقرب، النموذج القوي بياخد عليها نظرة تانية بدقة أعلى، مرة وحدة بالكتير لكل منتج وضمن ميزانية شهرية. القرار دايماً بقواعد النظام: النظرة التانية ما بتقدر تقلب صورة مرفوضة لمقبولة.</p>
    </div>

    @foreach ($md['warnings'] as $warning)
        <x-lq.alert variant="warning">{{ $warning }}</x-lq.alert>
    @endforeach

    <div class="lq-settings-form__grid">
        <label class="lq-field">
            <span class="lq-field__label">النموذج الأساسي</span>
            <select name="verifier_primary" class="lq-select lq-settings-field__control" @disabled((bool) $dbError)>
                @foreach ($md['rows'] as $row)
                    <option value="{{ $row['id'] }}" @selected($md['primary'] === $row['id'])>{{ $row['label'] }} · {{ $row['per100_primary_text'] }} لكل 100 منتج</option>
                @endforeach
                @if (!$md['primary_supported'])
                    <option value="{{ $md['primary'] }}" selected>{{ $md['primary'] }} (المضبوط حالياً)</option>
                @endif
            </select>
            @if (!$md['primary_supported'])
                <span class="lq-field__error">النموذج المضبوط (<bdi dir="ltr">{{ $md['primary'] }}</bdi>) مش من القائمة المدعومة؛ اختار واحد منها واحفظ.</span>
            @else
                <span class="lq-field__hint">بيقرأ لحد 4 صور مرشحة بالمرة لكل منتج.</span>
            @endif
        </label>

        <label class="lq-field">
            <span class="lq-field__label">النموذج القوي (النظرة التانية)</span>
            <select name="verifier_strong" class="lq-select lq-settings-field__control" @disabled((bool) $dbError)>
                @foreach ($md['rows'] as $row)
                    <option value="{{ $row['id'] }}" @selected(!$md['strong_off'] && $md['strong'] === $row['id'])>{{ $row['label'] }} · {{ $row['per100_strong_text'] }} لكل 100 منتج</option>
                @endforeach
                @if (!$md['strong_supported'])
                    <option value="{{ $md['strong'] }}" selected>{{ $md['strong'] }} (المضبوط حالياً)</option>
                @endif
                <option value="off" @selected($md['strong_off'])>إيقاف النموذج القوي (بلا نظرة تانية)</option>
            </select>
            @if (!$md['strong_supported'])
                <span class="lq-field__error">النموذج المضبوط (<bdi dir="ltr">{{ $md['strong'] }}</bdi>) مش من القائمة المدعومة؛ اختار واحد منها أو أوقفه واحفظ.</span>
            @else
                <span class="lq-field__hint">بيقرأ صورة وحدة بس، لما الأساسي ما يقدر يقرأ الماركة أو الحجم أو النوع. السعر جنب كل نموذج حد أعلى.</span>
            @endif
        </label>
    </div>

    <label class="lq-field">
        <span class="lq-field__label">قراءة أسماء الشيت المختصرة</span>
        <select name="query_normalizer" class="lq-select lq-settings-field__control" @disabled((bool) $dbError)>
            <option value="gemini" @selected($md['normalizer_on'])>شغّالة · Gemini 3.1 Flash-Lite · حوالي {{ $md['normalizer_per100_text'] }} لكل 100 منتج</option>
            <option value="off" @selected(!$md['normalizer_on'])>موقّفة (البحث بكلمات الشيت بس)</option>
        </select>
        @if ($md['normalizer_on'] && !$md['normalizer_key_saved'])
            <span class="lq-field__error">بتحتاج مفتاح Gemini وهو مش محفوظ: ضيفه من «المفاتيح». لحد ما تضيفه، البحث بيكمل بكلمات الشيت بس.</span>
        @else
            <span class="lq-field__hint">بتفك اختصارات أسماء الشيت (مثلاً LGT بتصير Light) لتكتب كلمات بحث أوضح، مرة وحدة لكل منتج وبتنحفظ. ما بتقرر شي: الصورة بتنقبل بس إذا طابقت اسم الشيت نفسه. وإذا ما لقينا الماركة وجربنا الماركة اللي خمّنتها، الصورة اللي بتطلع بتستنى مراجعتك دايماً.</span>
        @endif
    </label>

    <label class="lq-field">
        <span class="lq-field__label">الميزانية الشهرية للنموذج القوي (دولار)</span>
        <input type="number" class="lq-input lq-settings-field__control lq-models__budget" name="verifier_monthly_budget_usd" min="0" max="{{ \App\Http\Controllers\SettingsController::VERIFIER_BUDGET_MAX }}" step="0.01" value="{{ $md['budget'] }}" dir="ltr" inputmode="decimal" @disabled((bool) $dbError)>
        <span class="lq-field__hint">لما تخلص، المنتجات المش مؤكدة بتستنى مراجعتك بدون نظرة تانية لأول الشهر الجاي. 0 يعني بلا نظرة تانية.</span>
    </label>
    @if ($md['spend'] === null)
        <p class="lq-settings-card__note">ما قدرنا نقرأ صرف هالشهر من قاعدة البيانات.</p>
    @else
        <p class="lq-models__spend" data-models-spend>
            صرف النموذج القوي هالشهر: <bdi dir="ltr">{{ $md['spend']['strong'] }}</bdi> من <bdi dir="ltr">{{ $md['budget_text'] }}</bdi>
            · الباقي <bdi dir="ltr">{{ $md['spend']['left'] }}</bdi>
            · كل نماذج القراءة: <bdi dir="ltr">{{ $md['spend']['total'] }}</bdi>
        </p>
        @if ($md['spend']['exhausted'] && !$md['strong_off'])
            <x-lq.alert variant="warning" title="ميزانية هالشهر خلصت:">النموذج القوي موقف لأول الشهر الجاي، إلا إذا رفعت الميزانية.</x-lq.alert>
        @endif
    @endif

    <fieldset class="lq-settings-fieldset lq-settings-fieldset--boxed" @disabled((bool) $dbError)>
        <legend class="lq-field__label">الأسعار (دولار لكل مليون token)</legend>
        <span class="lq-field__hint">أسعار تقديرية بتنحسب فيها التكلفة والميزانية. عدّلها إذا تغيّرت أسعار خطتك. «لكل 100 منتج» تقدير: الأساسي قراءة وحدة لكل منتج، والقوي نظرة تانية لكل منتج (حد أعلى، لأنه ما بيشتغل إلا لما الأساسي مش متأكد).</span>
        <div class="lq-table-wrap lq-models__table">
            <table class="lq-table">
                <thead>
                    <tr>
                        <th scope="col">النموذج</th>
                        <th scope="col">الإدخال</th>
                        <th scope="col">الإخراج</th>
                        <th scope="col">لكل 100 منتج (أساسي)</th>
                        <th scope="col">لكل 100 منتج (قوي)</th>
                    </tr>
                </thead>
                <tbody>
                    @foreach ($md['rows'] as $row)
                        <tr>
                            <td class="lq-table__strong lq-models__name">
                                <bdi dir="ltr">{{ $row['label'] }}</bdi>
                                @if (!$row['key_saved'])
                                    <x-lq.chip status="none" size="sm" :dot="false" label="بلا مفتاح" />
                                @endif
                            </td>
                            <td data-label="الإدخال">
                                <input type="number" class="lq-input lq-input--sm lq-models__price" name="price_input[{{ $row['slug'] }}]" value="{{ $row['input'] }}" min="0" max="{{ \App\Http\Controllers\SettingsController::VERIFIER_PRICE_MAX }}" step="0.01" dir="ltr" inputmode="decimal" aria-label="سعر الإدخال لـ {{ $row['label'] }}">
                            </td>
                            <td data-label="الإخراج">
                                <input type="number" class="lq-input lq-input--sm lq-models__price" name="price_output[{{ $row['slug'] }}]" value="{{ $row['output'] }}" min="0" max="{{ \App\Http\Controllers\SettingsController::VERIFIER_PRICE_MAX }}" step="0.01" dir="ltr" inputmode="decimal" aria-label="سعر الإخراج لـ {{ $row['label'] }}">
                            </td>
                            <td class="lq-table__num" data-label="أساسي"><bdi dir="ltr">{{ $row['per100_primary_text'] }}</bdi></td>
                            <td class="lq-table__num" data-label="قوي"><bdi dir="ltr">{{ $row['per100_strong_text'] }}</bdi></td>
                        </tr>
                    @endforeach
                </tbody>
            </table>
        </div>
    </fieldset>

    <p class="lq-settings-card__note">نماذج Claude بتحتاج مفتاح Anthropic، ونماذج Gemini مفتاح Gemini: ضيفهم من تبويب <a class="lq-link" href="{{ route('dashboard.settings') }}?tab=keys">المفاتيح</a>. التكلفة الفعلية لكل نموذج بصفحة <a class="lq-link" href="{{ route('dashboard.diagnostics') }}">الصحة والتكلفة</a>.</p>

    <div class="lq-settings-card__actions">
        <x-lq.button type="submit" icon="check" :disabled="(bool) $dbError">حفظ</x-lq.button>
    </div>
</form>
