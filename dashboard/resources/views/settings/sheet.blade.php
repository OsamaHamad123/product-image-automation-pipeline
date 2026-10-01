{{--
    Settings · ربط الشيت. $sheet = SettingsController::sheetData(): the sheet URL/name and tab the Python side
    actually reads (process environment, then the project .env) and the service account e-mail (client_email only).
    Preview and save go through POST /api/sheet/preview and /api/sheet/save (ApiController) from settings.js.
--}}
<section class="lq-card lq-settings-card" aria-labelledby="lq-settings-sheet-title" data-sheet>
    <div class="lq-settings-card__head">
        <h2 class="lq-section-title" id="lq-settings-sheet-title">ربط الشيت</h2>
        <p class="lq-settings-card__intro">المنتجات بتنقرا من هاد الشيت، وروابط الصور النهائية بتنكتب فيه.</p>
    </div>

    @if ($sheet['credentials']['exists'] && $sheet['credentials']['email'])
        <div class="lq-settings-share">
            <span class="lq-settings-share__text">شارك الشيت مع هاد الحساب كمحرّر (Editor) ليقدر يقرأ ويكتب الروابط:</span>
            <span class="lq-settings-share__row">
                <code class="lq-settings-share__email" dir="ltr" data-sheet-email>{{ $sheet['credentials']['email'] }}</code>
                <x-lq.button variant="ghost" size="sm" icon="link" data-sheet-copy>نسخ</x-lq.button>
            </span>
        </div>
    @elseif ($sheet['credentials']['exists'])
        <x-lq.alert variant="warning" title="ملف الاعتماد موجود بس ناقص:">ما لقينا فيه عنوان حساب الخدمة. نزّل الملف من Google Cloud من جديد.</x-lq.alert>
    @else
        <x-lq.alert variant="danger" title="ملف الاعتماد مش موجود:">حط ملف حساب الخدمة (credentials.json) بمجلد المشروع، وبعدين شارك الشيت مع الحساب اللي فيه.</x-lq.alert>
    @endif

    <form class="lq-settings-form" data-sheet-form autocomplete="off" novalidate>
        <div class="lq-settings-form__grid">
            <label class="lq-field lq-settings-form__wide">
                <span class="lq-field__label">رابط الشيت أو اسمه</span>
                <input type="text" class="lq-input" name="spreadsheet_url" dir="auto" value="{{ $sheet['url'] }}" placeholder="https://docs.google.com/spreadsheets/d/…" data-sheet-url required>
                @if ($sheet['url_is_default'])
                    <span class="lq-field__hint">ما في رابط محفوظ: النظام بيفتح شيت اسمه «<bdi dir="auto">{{ $sheet['url'] }}</bdi>» من حساب الخدمة.</span>
                @endif
            </label>
            <label class="lq-field">
                <span class="lq-field__label">اسم التبويب</span>
                <input type="text" class="lq-input" name="tab_name" dir="auto" value="{{ $sheet['tab'] }}" placeholder="فاضي = أول تبويب" data-sheet-tab>
            </label>
        </div>
        <div class="lq-settings-form__actions">
            <x-lq.button variant="secondary" icon="search" data-sheet-preview>معاينة</x-lq.button>
            <x-lq.button variant="primary" icon="check" data-sheet-save>حفظ الربط</x-lq.button>
        </div>
        <p class="lq-settings-form__status" role="status" aria-live="polite" data-sheet-status></p>
    </form>

    <div class="lq-settings-preview" data-sheet-result hidden>
        <h3 class="lq-settings-preview__title">الأعمدة</h3>
        <ul class="lq-settings-columns" data-sheet-columns></ul>
        <h3 class="lq-settings-preview__title">أول 5 صفوف</h3>
        <div class="lq-table-wrap">
            <table class="lq-table lq-settings-preview__table">
                <thead><tr data-sheet-head></tr></thead>
                <tbody data-sheet-rows></tbody>
            </table>
        </div>
    </div>
</section>
