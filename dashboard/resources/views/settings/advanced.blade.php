{{--
    Settings · متقدم. $advanced = SettingsController::advancedData(): the search engine (v2, or v1 as a temporary
    rollback), strict brand matching, the v1-only switches and the legacy Custom Search / proxy settings. The label
    reading models moved to the «نماذج التحقق» tab (settings/models.blade.php).
    Secret fields are write-only (empty = keep the stored value) and never show a stored value.
    A second form (section=sources, $advanced['sources'] = SettingsController::sourcesData()) controls the expansion
    round of catalog_match/expand.py, visual search and the barcode policy; it reads no key, it only says whether the
    SerpApi key is saved (the key itself is on the «المفاتيح» tab).
--}}
@php $adv = $advanced; $src = $advanced['sources']; @endphp
<form method="POST" action="{{ route('dashboard.save_settings') }}" class="lq-card lq-settings-card" aria-labelledby="lq-settings-sources-title" autocomplete="off" data-sources-form>
    @csrf
    <input type="hidden" name="section" value="sources">
    <div class="lq-settings-card__head">
        <h2 class="lq-section-title" id="lq-settings-sources-title">مصادر البحث الإضافية</h2>
        <p class="lq-settings-card__intro">إذا ما لقى البحث العادي صورة أكيدة للمنتج، النظام بيعمل جولة تانية: صفحات المتاجر الإماراتية وموقع الماركة، Google Shopping، والبحث بالصورة (بيلاقي نفس الصورة بدقة أعلى). كل اللي بيلاقيه بيمر بنفس الفحص، وصور الصفحات ما بتنشر تلقائياً أبداً: بتستنى مراجعتك.</p>
    </div>

    <div class="lq-settings-switch-row">
        <x-lq.switch name="expansion_enabled" value="true" label="الجولة الإضافية" show-label :checked="$src['enabled']" :disabled="(bool) $dbError" />
        <span class="lq-field__hint">بتشتغل بس للمنتجات اللي ما انحسمت، ولصورة مختارة دقتها قليلة (بتدوّر على نسخة أكبر منها).</span>
    </div>

    <label class="lq-field">
        <span class="lq-field__label">حد الطلبات المدفوعة لكل منتج</span>
        <input type="number" class="lq-input lq-settings-field__control" name="expansion_max_calls" min="0" max="{{ \App\Http\Controllers\SettingsController::EXPANSION_MAX_CALLS_LIMIT }}" step="1" value="{{ $src['max_calls'] }}" dir="ltr" inputmode="numeric" @disabled((bool) $dbError)>
        <span class="lq-field__hint">كل بحث (Serper أو SerpApi) طلب واحد؛ فتح صفحات المتاجر مجاني. 0 = الجولة موقفة.</span>
    </label>

    <fieldset class="lq-settings-fieldset lq-settings-fieldset--boxed" @disabled((bool) $dbError)>
        <legend class="lq-field__label">البحث بالصورة</legend>
        <label class="lq-check lq-settings-choice">
            <input type="radio" name="visual_search" value="auto" @checked($src['visual'] === 'auto')>
            <span>تلقائي: Serper أولاً، وSerpApi (Google Lens) إذا مفتاحه محفوظ (موصى به)</span>
        </label>
        <label class="lq-check lq-settings-choice">
            <input type="radio" name="visual_search" value="serper" @checked($src['visual'] === 'serper')>
            <span>Serper بس: الأرخص، إذا خطتك بتدعم البحث بالصورة</span>
        </label>
        <label class="lq-check lq-settings-choice">
            <input type="radio" name="visual_search" value="serpapi" @checked($src['visual'] === 'serpapi')>
            <span>SerpApi بس: Google Lens، أدق بالصور الكبيرة وأغلى</span>
        </label>
        <label class="lq-check lq-settings-choice">
            <input type="radio" name="visual_search" value="off" @checked($src['visual'] === 'off')>
            <span>موقف: بلا بحث بالصورة</span>
        </label>
        <p class="lq-settings-card__note" data-serpapi-key>مفتاح SerpApi: {{ $src['serpapi_saved'] ? 'محفوظ' : 'غير محفوظ' }} · بيتضاف من تبويب <a class="lq-link" href="{{ route('dashboard.settings') }}?tab=keys">المفاتيح</a>.</p>
        @if ($src['visual'] === 'serpapi' && !$src['serpapi_saved'])
            <x-lq.alert variant="warning" title="مفتاح SerpApi مش محفوظ:">البحث بالصورة موقف فعلياً لحد ما تضيف المفتاح، أو تختار «تلقائي».</x-lq.alert>
        @endif
        <label class="lq-field">
            <span class="lq-field__label">سعر بحث SerpApi الواحد (دولار)</span>
            <input type="number" class="lq-input lq-settings-field__control" name="serpapi_lens_price_usd" min="0" max="1" step="0.001" value="{{ $src['serpapi_price'] }}" dir="ltr" inputmode="decimal">
            <span class="lq-field__hint">تقديري، للتكلفة بصفحة الصحة. عدّله حسب خطتك. ما بينطلب أكتر من بحث SerpApi واحد لكل منتج.</span>
        </label>
    </fieldset>

    <fieldset class="lq-settings-fieldset lq-settings-fieldset--boxed" @disabled((bool) $dbError)>
        <legend class="lq-field__label">الباركود</legend>
        <label class="lq-check lq-settings-choice">
            <input type="radio" name="gtin_policy" value="evidence" @checked($src['gtin_policy'] === 'evidence')>
            <span>دليل مساعد (موصى به): بيقوّي الصورة بس إذا الماركة متطابقة؛ وإذا اختلف، الصورة بتحتاج تطابق كامل بالماركة والاسم والحجم، وبتوصلك للمراجعة مع تنبيه إن الباركود مختلف. مناسب لأن باركود الشيت ممكن يكون غلط.</span>
        </label>
        <label class="lq-check lq-settings-choice">
            <input type="radio" name="gtin_policy" value="strict" @checked($src['gtin_policy'] === 'strict')>
            <span>صارم: صفحة بباركود مختلف بتنرفض. استعمله بس إذا باركودات الشيت مضمونة.</span>
        </label>
        <label class="lq-check lq-settings-choice">
            <input type="radio" name="gtin_policy" value="off" @checked($src['gtin_policy'] === 'off')>
            <span>تجاهل الباركود: الماركة والاسم والحجم بس.</span>
        </label>
    </fieldset>

    <div class="lq-settings-card__actions">
        <x-lq.button type="submit" icon="check" :disabled="(bool) $dbError">حفظ</x-lq.button>
    </div>
</form>

<form method="POST" action="{{ route('dashboard.save_settings') }}" class="lq-card lq-settings-card" aria-labelledby="lq-settings-advanced-title" autocomplete="off" data-advanced-form data-engine="{{ $adv['engine'] }}">
    @csrf
    <input type="hidden" name="section" value="advanced">
    <div class="lq-settings-card__head">
        <h2 class="lq-section-title" id="lq-settings-advanced-title">متقدم</h2>
        <p class="lq-settings-card__intro">إعدادات نادراً ما بتحتاجها. غيّرها بس إذا بتعرف شو بتعمل.</p>
    </div>

    @if ($adv['engine'] === 'v1')
        <x-lq.alert variant="danger" title="النظام القديم شغّال هلق:">الصور ما عم تنفحص متل النظام الجديد. رجّع «النظام الجديد» أول ما تخلص.</x-lq.alert>
    @endif

    <fieldset class="lq-settings-fieldset" @disabled((bool) $dbError)>
        <legend class="lq-field__label">نظام البحث</legend>
        <label class="lq-check lq-settings-choice">
            <input type="radio" name="search_engine" value="v2" @checked($adv['engine'] === 'v2')>
            <span>النظام الجديد: بيقرأ الملصق وبيتأكد من الماركة والحجم والنوع (موصى به)</span>
        </label>
        <label class="lq-check lq-settings-choice">
            <input type="radio" name="search_engine" value="v1" @checked($adv['engine'] === 'v1') data-engine-v1>
            <span>النظام القديم: للرجوع المؤقت بس</span>
        </label>
        <x-lq.alert variant="warning" title="قبل ما ترجع للنظام القديم:">ما بيقرأ الملصق ولا بيتأكد من الحجم والنوع متل الجديد، فبتكتر الاقتراحات الغلط. استعمله بس إذا النظام الجديد عم يعلق، ورجّع أول ما ينحل.</x-lq.alert>
    </fieldset>

    <p class="lq-settings-card__note">نموذج قراءة الملصق (Gemini أو Claude) والنموذج القوي وميزانيته صاروا بتبويب <a class="lq-link" href="{{ route('dashboard.settings') }}?tab=models">نماذج التحقق</a>.</p>

    <div class="lq-settings-switch-row">
        <x-lq.switch name="strict_brand_match" value="true" label="مطابقة الماركة الصارمة" show-label :checked="$adv['strict']" :disabled="(bool) $dbError" />
        <span class="lq-field__hint">بيرفض الصورة إذا بيّن عليها ماركة منافسة بدون الماركة المطلوبة.</span>
    </div>

    <fieldset class="lq-settings-fieldset lq-settings-fieldset--boxed" @disabled((bool) $dbError)>
        <legend class="lq-field__label">خيارات النظام القديم (بتشتغل بس معه)</legend>
        <x-lq.switch name="enable_gemini_pre_validation" value="true" label="فحص Gemini المسبق" show-label :checked="$adv['v1']['enable_gemini_pre_validation']" />
        <x-lq.switch name="filter_competitors" value="true" label="فلترة الماركات المنافسة" show-label :checked="$adv['v1']['filter_competitors']" />
        <x-lq.switch name="bypass_white_background_check" value="true" label="تخطي فحص الخلفية البيضا" show-label :checked="$adv['v1']['bypass_white_background_check']" />
    </fieldset>

    <fieldset class="lq-settings-fieldset lq-settings-fieldset--boxed" @disabled((bool) $dbError)>
        <legend class="lq-field__label">مصادر قديمة واختيارية</legend>
        <label class="lq-field">
            <span class="lq-field__label">{{ $adv['secrets']['google_search_api_key']['label'] }} · {{ $adv['secrets']['google_search_api_key']['saved'] ? 'محفوظ' : 'غير محفوظ' }}</span>
            <input type="password" class="lq-input" name="google_search_api_key" dir="ltr" value="" autocomplete="new-password" spellcheck="false" placeholder="{{ $adv['secrets']['google_search_api_key']['saved'] ? 'فاضي = خلي المحفوظ' : 'مفاتيح مفصولة بفاصلة' }}">
            <span class="lq-field__hint">بيشتغل بس إذا كان مضبوط من قبل، وبيوقف بعد 2026-12-31.</span>
        </label>
        @if ($adv['secrets']['google_search_api_key']['saved'])
            <label class="lq-check"><input type="checkbox" name="clear_google_search_api_key" value="1" data-key-clear><span>امسح المفاتيح المحفوظة</span></label>
        @endif
        <label class="lq-field">
            <span class="lq-field__label">معرّف محرك البحث المخصص (CX)</span>
            <input type="text" class="lq-input" name="google_search_cx" dir="ltr" value="{{ $adv['cx'] }}" autocomplete="off" spellcheck="false">
        </label>
        <label class="lq-field">
            <span class="lq-field__label">{{ $adv['secrets']['proxy_url']['label'] }} · {{ $adv['secrets']['proxy_url']['saved'] ? 'محفوظ' : 'غير محفوظ' }}</span>
            <input type="password" class="lq-input" name="proxy_url" dir="ltr" value="" autocomplete="new-password" spellcheck="false" placeholder="{{ $adv['secrets']['proxy_url']['saved'] ? 'فاضي = خلي المحفوظ' : 'http://user:password@host:port' }}">
            <span class="lq-field__hint">اختياري: لتمرير طلبات Bing الاحتياطي إذا انحجبت.</span>
        </label>
        @if ($adv['secrets']['proxy_url']['saved'])
            <label class="lq-check"><input type="checkbox" name="clear_proxy_url" value="1" data-key-clear><span>امسح العنوان المحفوظ</span></label>
        @endif
    </fieldset>

    <p class="lq-settings-card__note">سعر Serper التقديري ثابت بالنظام؛ أسعار نماذج قراءة الملصق بتتعدّل من تبويب «نماذج التحقق»، والتكلفة كلها بصفحة <a class="lq-link" href="{{ route('dashboard.diagnostics') }}">الصحة والتكلفة</a>.</p>

    <div class="lq-settings-card__actions">
        <x-lq.button type="submit" icon="check" :disabled="(bool) $dbError">حفظ</x-lq.button>
    </div>
</form>
