/*
 * لقطة · الإعدادات (resources/views/dashboard/settings.blade.php and resources/views/settings/*).
 *
 * - ربط الشيت: «معاينة» POSTs /api/sheet/preview and shows the resolved columns and the first rows;
 *   «حفظ الربط» asks first (it clears the cached sheet, keeps every review) and POSTs /api/sheet/save.
 * - المفاتيح: «تغيير» reveals an empty, write-only form; clearing a stored key asks first.
 * - النشر الآلي: the switch says what it will do before it saves (AUTO_PUBLISH_ENABLED).
 * - معالجة الصور: with background removal off, «رجّع عزل الخلفية (…)» POSTs /api/settings/bg-method {method}.
 * - Unsaved changes: a changed card shows «ما انحفظ»; saving another card or leaving the page asks first. A save
 *   lands back on its card (the redirect's #id) or where the page was (the JSON saves keep the scroll position).
 * - A refused value: the first field the server marked (aria-invalid, its message under it) gets the focus.
 * - On a phone the tab row keeps the open tab in view and fades the edge that hides more tabs.
 * View helpers are pure (window.LaqtaSettings, used by the node tests); the DOM code sets text only.
 */
(function () {
    'use strict';

    var PREVIEW_URL = '/api/sheet/preview';

    /* A question before a change: the page's own dialog (Laqta.ask) when it is there, answered later; without it
       (an old page, the node tests) window.confirm, answered at once (askNow()). ask(texts, then): then(true) after
       every question was answered «أكيد», then(false) otherwise. */
    function askNow() {
        return !(window.Laqta && typeof window.Laqta.ask === 'function');
    }

    function ask(texts, then) {
        texts = texts.filter(Boolean);
        if (askNow()) {
            then(texts.every(function (t) { return window.confirm(t); }));
            return;
        }
        var chain = Promise.resolve(true);
        texts.forEach(function (t) {
            chain = chain.then(function (ok) { return ok ? window.Laqta.ask(t) : false; });
        });
        chain.then(then);
    }
    var SAVE_URL = '/api/sheet/save';
    var BG_METHOD_URL = '/api/settings/bg-method';

    /* google_sheets.COLUMN_SYNONYMS keys in the order the owner thinks of them. */
    var COLUMNS = [
        { key: 'name', label: 'اسم المنتج', required: true },
        { key: 'brand', label: 'الماركة', required: true },
        { key: 'barcode', label: 'الباركود' },
        { key: 'size', label: 'الحجم' },
        { key: 'link', label: 'رابط الصورة', created: true },
        { key: 'name_ar', label: 'الاسم بالعربي' },
        { key: 'brand_ar', label: 'الماركة بالعربي' },
        { key: 'category', label: 'الفئة' },
        { key: 'origin', label: 'بلد المنشأ' }
    ];

    function isObject(v) {
        return v !== null && typeof v === 'object' && !Array.isArray(v);
    }

    /* Each logical column: found in which sheet column, or what happens without it. */
    function columnsView(columns, headers) {
        columns = isObject(columns) ? columns : {};
        headers = Array.isArray(headers) ? headers : [];
        return COLUMNS.map(function (c) {
            var idx = typeof columns[c.key] === 'number' ? columns[c.key] : -1;
            if (idx >= 0) {
                return { key: c.key, label: c.label, found: true, tone: 'success',
                    text: String(headers[idx] !== undefined && headers[idx] !== '' ? headers[idx] : 'عمود ' + (idx + 1)) };
            }
            if (c.created) return { key: c.key, label: c.label, found: false, tone: 'info', text: 'رح ينضاف عمود Drive Image Link' };
            if (c.required) return { key: c.key, label: c.label, found: false, tone: 'danger', text: 'ما لقيناه: البحث بيحتاجه' };
            return { key: c.key, label: c.label, found: false, tone: 'muted', text: 'مش موجود' };
        });
    }

    function previewView(data) {
        if (!isObject(data) || data.status !== 'success') return null;
        var headers = Array.isArray(data.headers) ? data.headers.map(function (h) { return String(h); }) : [];
        var rows = Array.isArray(data.rows) ? data.rows.slice(0, 5).map(function (r) {
            return Array.isArray(r) ? r.map(function (v) { return v === null || v === undefined ? '' : String(v); }) : [];
        }) : [];
        var missing = columnsView(data.columns, headers).filter(function (c) { return c.tone === 'danger'; });
        return {
            empty: headers.length === 0,
            headers: headers,
            rows: rows,
            columns: columnsView(data.columns, headers),
            status: headers.length === 0 ? 'الشيت فاضي: ما في ولا عمود بأول صف.'
                : (missing.length ? 'انفتح الشيت، بس في أعمدة أساسية ناقصة.' : 'انفتح الشيت وكل الأعمدة الأساسية موجودة.'),
            tone: headers.length === 0 || missing.length ? 'warning' : 'success'
        };
    }

    /* Plain Arabic for a failed preview/save; the bridge's English text stays in the tooltip. */
    function sheetError(res, saving) {
        if (res && res.status === 419) return { text: 'انتهت صلاحية الصفحة. حدّثها وجرّب مرة تانية.', detail: '' };
        var data = res && isObject(res.data) ? res.data : {};
        var detail = typeof data.error === 'string' ? data.error : '';
        if (data.error_code === 'invalid_sheet') {
            return { text: 'الرابط أو اسم التبويب مش مقبول: حط رابط شيت Google (بيبلّش بـ https://docs.google.com/spreadsheets/d/) '
                + 'أو اسم الشيت، بدون علامات تنصيص ولا سطر جديد.', detail: detail };
        }
        if (/required/i.test(detail)) return { text: 'اكتب رابط الشيت أو اسمه أول.', detail: detail };
        if (saving) return { text: 'ما انحفظ الربط. تأكد إن ملف .env بمجلد المشروع قابل للكتابة وجرّب مرة تانية.', detail: detail };
        return { text: 'ما قدرنا نفتح الشيت. تأكد من الرابط أو الاسم، ومن إنه مشارك مع حساب الخدمة.', detail: detail };
    }

    function saveConfirmText(url, tab) {
        return 'رح نربط الشيت «' + url + '»' + (tab ? ' (تبويب «' + tab + '»)' : ' (أول تبويب)')
            + ' ونفضّي النسخة المحفوظة مؤقتاً من الشيت القديم. المراجعات والصور المعتمدة بقاعدة البيانات ما بتتأثر، '
            + 'والتشغيل الجاي بيقرأ من الشيت الجديد. نكمّل؟';
    }

    function clearConfirmText(name) {
        return 'رح ينمسح ' + (name ? 'مفتاح ' + name + ' ' : '') + 'المحفوظ بقاعدة البيانات. بعدها النظام بيستعمل المفتاح '
            + 'من ملف .env إذا في، وإلا الخدمة بتوقف لحتى تحط مفتاح جديد. متأكد؟';
    }

    /* A refused POST /api/settings/bg-method in plain Arabic (the server's own Arabic text when it has one). */
    function bgMethodError(res) {
        if (res && res.status === 419) return 'انتهت صلاحية الصفحة. حدّثها وجرّب مرة تانية.';
        var text = res && isObject(res.data) && typeof res.data.error === 'string' ? res.data.error.trim() : '';
        return text && /[؀-ۿ]/.test(text) ? text : 'ما انحفظ: جرّب مرة تانية.';
    }

    /* A form's fields as one text (hidden inputs and buttons left out): «ما انحفظ» when it differs from the page load. */
    function formState(form) {
        var els = form && form.elements ? form.elements : [];
        var out = [];
        for (var i = 0; i < els.length; i++) {
            var el = els[i];
            if (!el || !el.name || el.type === 'hidden' || el.type === 'submit' || el.type === 'button') continue;
            out.push(el.name + '=' + (el.type === 'checkbox' || el.type === 'radio' ? (el.checked ? '1' : '0') : String(el.value)));
        }
        return out.join('&');
    }

    function dirtyText(titles) {
        return 'في تغييرات ما انحفظت بـ"' + titles.join('" و"') + '"';
    }

    var api = { columnsView: columnsView, previewView: previewView, sheetError: sheetError,
        saveConfirmText: saveConfirmText, clearConfirmText: clearConfirmText,
        bgMethodError: bgMethodError, formState: formState, dirtyText: dirtyText, COLUMNS: COLUMNS };
    if (typeof window !== 'undefined') window.LaqtaSettings = api;

    // ------------------------------------------------------------------
    // DOM
    // ------------------------------------------------------------------

    if (typeof document === 'undefined' || !document.querySelector || !document.querySelector('[data-settings-page]')) {
        return;
    }

    var page = document.querySelector('[data-settings-page]');

    var reduceMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

    /* On a phone the tabs are one scrolling row: the open tab is scrolled into the middle of the row (the row only, so
       a landing on #card keeps its place), and the edge with more tabs behind it fades (data-more = start | end | both,
       settings.css). In RTL scrollLeft runs from 0 at the start to negative at the end: its size is the distance. */
    var tabsNav = page.querySelector('.lq-settings__tabs');
    var activeTab = page.querySelector('.lq-settings__tab.is-active');

    function markTabsOverflow() {
        if (!tabsNav) return;
        var max = (tabsNav.scrollWidth || 0) - (tabsNav.clientWidth || 0);
        var from = Math.abs(tabsNav.scrollLeft || 0);
        var start = max > 1 && from > 1;
        var end = max > 1 && from < max - 1;
        var more = start && end ? 'both' : (start ? 'start' : (end ? 'end' : ''));
        if (more) tabsNav.setAttribute('data-more', more);
        else tabsNav.removeAttribute('data-more');
    }

    if (tabsNav && activeTab && activeTab.getBoundingClientRect && window.matchMedia
        && window.matchMedia('(max-width: 980px)').matches) {
        var navBox = tabsNav.getBoundingClientRect();
        var tabBox = activeTab.getBoundingClientRect();
        tabsNav.scrollLeft += (tabBox.left + tabBox.width / 2) - (navBox.left + navBox.width / 2);
    }
    if (tabsNav && typeof tabsNav.addEventListener === 'function') {
        markTabsOverflow();
        tabsNav.addEventListener('scroll', markTabsOverflow, { passive: true });
        if (typeof window.addEventListener === 'function') window.addEventListener('resize', markTabsOverflow);
    }

    function toast(text, variant) {
        if (window.Laqta && window.Laqta.toast) window.Laqta.toast(text, { variant: variant || 'info' });
    }

    function setHidden(el, hidden) {
        if (!el) return;
        if (hidden) el.setAttribute('hidden', '');
        else el.removeAttribute('hidden');
    }

    function clear(el) {
        while (el && el.firstChild) el.removeChild(el.firstChild);
    }

    function make(tag, className, text) {
        var el = document.createElement(tag);
        if (className) el.className = className;
        if (text !== undefined && text !== null) el.textContent = String(text);
        return el;
    }

    function csrf() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    function postJson(url, body) {
        return fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRF-TOKEN': csrf(),
                'X-Requested-With': 'XMLHttpRequest' },
            body: JSON.stringify(body)
        }).then(function (res) {
            return res.text().then(function (text) {
                var data = null;
                try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
                return { ok: res.ok, status: res.status, data: data };
            });
        });
    }

    function setBusy(button, busy) {
        if (!button) return;
        button.disabled = busy;
        if (busy) button.setAttribute('aria-busy', 'true');
        else button.removeAttribute('aria-busy');
    }

    // --- وين كنت بالصفحة ---------------------------------------------------
    /* After a save the page loads again where it was: a form's redirect lands on its card (?tab=…#id,
       SettingsController::anchorFor) and the JSON saves (sheet, bg-method) keep the scroll position. */
    var SCROLL_KEY = 'laqtaSettingsScroll';
    var leaving = false;            // a save or a confirmed link is leaving the page: no «ما انحفظ» question

    function here() {
        return window.location.pathname + window.location.search;
    }

    function reloadKeepingPlace() {
        leaving = true;
        try {
            window.sessionStorage.setItem(SCROLL_KEY, JSON.stringify({ url: here(), y: window.scrollY || window.pageYOffset || 0 }));
        } catch (e) { /* no storage (private mode): the browser's own scroll restore */ }
        window.location.reload();
    }

    try {
        var kept = window.sessionStorage ? JSON.parse(window.sessionStorage.getItem(SCROLL_KEY) || 'null') : null;
        if (kept) window.sessionStorage.removeItem(SCROLL_KEY);
        if (kept && kept.url === here() && typeof kept.y === 'number' && window.scrollTo) window.scrollTo(0, kept.y);
    } catch (e) { /* nothing kept */ }

    // landed on the saved card: the result (at the top of the page, out of view now) comes as a toast too
    if (window.location && window.location.hash && document.getElementById && document.getElementById(window.location.hash.slice(1))) {
        Array.prototype.forEach.call(page.querySelectorAll('[data-settings-flash]'), function (alert) {
            toast(alert.textContent.replace(/\s+/g, ' ').trim(), alert.getAttribute('data-settings-flash'));
        });
    }

    // a refused value: its message sits under the field (data-settings-field-error, the field's aria-describedby);
    // the first such field gets the focus and comes into view (no smooth scroll with reduced motion)
    var firstError = page.querySelector('[data-settings-field-error]');
    if (firstError) {
        var errorFields = firstError.id ? page.querySelectorAll('[aria-describedby~="' + firstError.id + '"]') : [];
        var errorField = null;
        for (var ef = 0; ef < errorFields.length && !errorField; ef++) {
            if (!errorFields[ef].disabled && (errorFields[ef].type !== 'radio' || errorFields[ef].checked)) errorField = errorFields[ef];
        }
        if (!errorField && errorFields.length && !errorFields[0].disabled) errorField = errorFields[0];
        if (errorField && typeof errorField.focus === 'function') {
            try { errorField.focus({ preventScroll: true }); } catch (e) { errorField.focus(); }
        }
        var errorTarget = errorField || firstError;
        if (errorTarget.scrollIntoView) errorTarget.scrollIntoView({ block: 'center', behavior: reduceMotion ? 'auto' : 'smooth' });
    }

    // --- ربط الشيت ---------------------------------------------------------
    var sheetForm = page.querySelector('[data-sheet-form]');
    if (sheetForm) {
        var urlInput = sheetForm.querySelector('[data-sheet-url]');
        var tabInput = sheetForm.querySelector('[data-sheet-tab]');
        var previewBtn = sheetForm.querySelector('[data-sheet-preview]');
        var saveBtn = sheetForm.querySelector('[data-sheet-save]');
        var status = sheetForm.querySelector('[data-sheet-status]');
        var result = page.querySelector('[data-sheet-result]');

        var showStatus = function (text, tone, detail) {
            status.textContent = text || '';
            status.className = 'lq-settings-form__status' + (tone ? ' lq-settings-form__status--' + tone : '');
            status.title = detail || '';
        };

        var values = function () {
            return { spreadsheet_url: urlInput.value.trim(), tab_name: tabInput.value.trim() };
        };

        var renderPreview = function (view) {
            var columns = result.querySelector('[data-sheet-columns]');
            var head = result.querySelector('[data-sheet-head]');
            var rows = result.querySelector('[data-sheet-rows]');
            clear(columns);
            clear(head);
            clear(rows);
            view.columns.forEach(function (c) {
                var li = make('li', 'lq-settings-columns__item lq-settings-columns__item--' + c.tone);
                li.appendChild(make('span', 'lq-settings-columns__label', c.label));
                li.appendChild(make('span', 'lq-settings-columns__value', c.text));
                columns.appendChild(li);
            });
            view.headers.forEach(function (h, i) {
                var th = make('th', '', h || 'عمود ' + (i + 1));
                th.setAttribute('scope', 'col');
                head.appendChild(th);
            });
            view.rows.forEach(function (r) {
                var tr = make('tr');
                view.headers.forEach(function (_, i) {
                    var td = make('td', '', r[i] || '');
                    td.setAttribute('dir', 'auto');
                    tr.appendChild(td);
                });
                rows.appendChild(tr);
            });
            setHidden(result, false);
        };

        previewBtn.addEventListener('click', function () {
            var body = values();
            if (!body.spreadsheet_url) {
                showStatus('اكتب رابط الشيت أو اسمه أول.', 'danger');
                urlInput.focus();
                return;
            }
            setBusy(previewBtn, true);
            setHidden(result, true);
            showStatus('لحظة، عم نفتح الشيت…', '');
            postJson(PREVIEW_URL, body).then(function (res) {
                var view = res.ok ? previewView(res.data) : null;
                if (!view) {
                    var err = sheetError(res, false);
                    showStatus(err.text, 'danger', err.detail);
                    return;
                }
                showStatus(view.status, view.tone);
                if (!view.empty) renderPreview(view);
            }, function () {
                showStatus('ما قدرنا نوصل للخادم.', 'danger');
            }).then(function () { setBusy(previewBtn, false); });
        });

        saveBtn.addEventListener('click', function () {
            var body = values();
            if (!body.spreadsheet_url) {
                showStatus('اكتب رابط الشيت أو اسمه أول.', 'danger');
                urlInput.focus();
                return;
            }
            ask([saveConfirmText(body.spreadsheet_url, body.tab_name)], function (ok) {
                if (ok) saveSheet(body);
            });
        });

        var saveSheet = function (body) {
            setBusy(saveBtn, true);
            showStatus('لحظة، عم نحفظ الربط…', '');
            postJson(SAVE_URL, body).then(function (res) {
                if (res.ok && res.data && res.data.status === 'success') {
                    showStatus('انحفظ الربط. التشغيل الجاي بيقرأ من الشيت الجديد.', 'success');
                    toast('انحفظ ربط الشيت.', 'success');
                    setTimeout(reloadKeepingPlace, 900);
                    return;
                }
                var err = sheetError(res, true);
                showStatus(err.text, 'danger', err.detail);
                setBusy(saveBtn, false);
            }, function () {
                showStatus('ما قدرنا نوصل للخادم.', 'danger');
                setBusy(saveBtn, false);
            });
        };

        sheetForm.addEventListener('submit', function (e) { e.preventDefault(); });
    }

    var copyBtn = page.querySelector('[data-sheet-copy]');
    var email = page.querySelector('[data-sheet-email]');
    if (copyBtn && email) {
        copyBtn.addEventListener('click', function () {
            var text = email.textContent.trim();
            var done = function () { toast('انتسخ عنوان حساب الخدمة.', 'success'); };
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(done, function () { toast('انسخه يدوياً: ' + text, 'info'); });
            } else {
                toast('انسخه يدوياً: ' + text, 'info');
            }
        });
    }

    // --- المفاتيح -----------------------------------------------------------
    var toggles = page.querySelectorAll('[data-key-toggle]');
    function openKeyForm(id, open) {
        var form = page.querySelector('[data-key-form="' + id + '"]');
        var toggle = page.querySelector('[data-key-toggle="' + id + '"]');
        if (!form || !toggle) return;
        setHidden(form, !open);
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) {
            var first = form.querySelector('input[type="password"], input[type="text"]');
            if (first) first.focus();
        } else {
            var inputs = form.querySelectorAll('input[type="password"]');
            for (var i = 0; i < inputs.length; i++) inputs[i].value = '';
            var clears = form.querySelectorAll('[data-key-clear]');
            for (var k = 0; k < clears.length; k++) clears[k].checked = false;
            refreshDirty(form);                       // «إلغاء» رجّع الحقول متل ما كانت: ما في شي ما انحفظ
            toggle.focus();
        }
    }
    for (var t = 0; t < toggles.length; t++) {
        toggles[t].addEventListener('click', function (e) {
            var id = e.currentTarget.getAttribute('data-key-toggle');
            openKeyForm(id, e.currentTarget.getAttribute('aria-expanded') !== 'true');
        });
    }
    // ?tab=keys#lq-key-‹id› (من «شو بدو منك» بالصحة أو «روح لـ…»): خانة هالمفتاح مفتوحة وجاهزة للصق
    function openKeyFromHash() {
        var m = /^#lq-key-([a-z_]+)$/.exec(window.location && window.location.hash || '');
        var toggle = m ? page.querySelector('[data-key-toggle="' + m[1] + '"]') : null;
        if (toggle && !toggle.disabled && toggle.getAttribute('aria-expanded') !== 'true') openKeyForm(m[1], true);
    }
    openKeyFromHash();
    if (typeof window.addEventListener === 'function') window.addEventListener('hashchange', openKeyFromHash);
    var cancels = page.querySelectorAll('[data-key-cancel]');
    for (var c = 0; c < cancels.length; c++) {
        cancels[c].addEventListener('click', function (e) {
            openKeyForm(e.currentTarget.getAttribute('data-key-cancel'), false);
        });
    }

    /* No silent destructive save: clearing a stored key asks first (key forms and «متقدم»). With the page's dialog the
       submit waits for the answer, then goes again once «أكيد». */
    var forms = page.querySelectorAll('form');

    // --- تغييرات ما انحفظت ----------------------------------------------------
    /* Each form saves its own card only, and the page loads again after it: a change left in another card would be
       lost without a word. A changed card shows «ما انحفظ»; saving another card, a link out of the page and leaving
       the page ask first («في تغييرات ما انحفظت بـ"…"»). The auto-publish switches save themselves: not tracked. */
    var tracked = [];

    function cardOf(form) {
        return (form.closest && (form.closest('[data-key-row]') || form.closest('.lq-settings-card'))) || form;
    }

    function titleElOf(form) {
        return cardOf(form).querySelector('.lq-keys__title') || cardOf(form).querySelector('.lq-section-title');
    }

    function entryOf(form) {
        for (var i = 0; i < tracked.length; i++) if (tracked[i].form === form) return tracked[i];
        return null;
    }

    function refreshDirty(form) {
        var entry = entryOf(form);
        if (!entry) return;
        var dirty = formState(form) !== entry.initial;
        if (dirty === entry.dirty) return;
        entry.dirty = dirty;
        if (!entry.badge && dirty) {
            var titleEl = titleElOf(form);
            if (titleEl && titleEl.appendChild) {
                entry.badge = make('span', 'lq-settings-dirty', 'ما انحفظ');
                entry.badge.setAttribute('data-settings-dirty', '');
                titleEl.appendChild(entry.badge);
            }
        }
        setHidden(entry.badge, !dirty);
    }

    function dirtyTitles(except) {
        return tracked.filter(function (e) { return e.dirty && e.form !== except; }).map(function (e) { return e.title; });
    }

    /* «في تغييرات ما انحفظت بـ"…"»: then(true) to go on without them, then(false) to stay. */
    function askLeave(titles, then) {
        if (askNow()) {
            then(window.confirm(dirtyText(titles) + '.\nإذا كمّلت بتروح هالتغييرات. نكمّل؟'));
            return;
        }
        window.Laqta.ask({ title: dirtyText(titles), text: 'إذا كمّلت بتروح هالتغييرات. لتحفظها، ارجع واضغط «حفظ» بكرتها.',
            confirmText: 'كمّل بلا حفظ', cancelText: 'لا، رجوع' }).then(then);
    }

    Array.prototype.forEach.call(forms, function (form) {
        if (form.hasAttribute && form.hasAttribute('data-autopub-form')) return;
        var titleEl = titleElOf(form);
        tracked.push({ form: form, title: titleEl ? titleEl.textContent.trim() : '', initial: formState(form), dirty: false, badge: null });
        var check = function () { refreshDirty(form); };
        form.addEventListener('input', check);
        form.addEventListener('change', check);
    });

    // saving one card while another has changes (capture: before the card's own submit handlers)
    page.addEventListener('submit', function (e) {
        var form = e.target;
        if (!form || form.__lqDirtyOk) return;
        var others = dirtyTitles(form);
        if (!others.length) return;
        var now = askNow();
        if (!now) {
            e.preventDefault();
            e.stopPropagation();
        }
        askLeave(others, function (ok) {
            if (!ok) {
                if (now) {
                    e.preventDefault();
                    e.stopPropagation();
                }
                // an auto-publish switch submits itself on change: put it back as it is saved
                var sw = form.querySelector('[data-autopub-switch]');
                if (sw) sw.checked = !sw.checked;
                return;
            }
            form.__lqDirtyOk = true;                 // stays: the key-clear question may submit it again
            if (now) return;
            if (form.requestSubmit) {
                form.requestSubmit();
            } else {
                leaving = true;                      // form.submit() fires no submit event
                form.submit();
            }
        });
    }, true);

    if (typeof document.addEventListener === 'function') {
        // a submit that goes through (nobody stopped it) leaves the page: no question on the way out
        document.addEventListener('submit', function (e) {
            if (!e.defaultPrevented) leaving = true;
        });
        // a link out of the page (the tabs, the side bar) asks in the page's own dialog
        document.addEventListener('click', function (e) {
            if (e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
            var link = e.target && e.target.closest ? e.target.closest('a[href]') : null;
            if (!link || link.target === '_blank' || link.hasAttribute('download')) return;
            var href = link.getAttribute('href') || '';
            if (href === '' || href.charAt(0) === '#') return;
            var titles = dirtyTitles(null);
            if (!titles.length) return;
            e.preventDefault();
            askLeave(titles, function (ok) {
                if (!ok) return;
                leaving = true;
                window.location.href = link.href;
            });
        });
    }
    if (typeof window.addEventListener === 'function') {
        // reload, close, back: the browser's own question (it shows its own text)
        window.addEventListener('beforeunload', function (e) {
            if (leaving || !dirtyTitles(null).length) return undefined;
            e.preventDefault();
            e.returnValue = dirtyText(dirtyTitles(null));
            return e.returnValue;
        });
    }

    for (var f = 0; f < forms.length; f++) {
        forms[f].addEventListener('submit', function (e) {
            var form = e.currentTarget;
            if (form.__lqConfirmed) {
                form.__lqConfirmed = false;
                return;
            }
            var questions = [];
            if (form.querySelector('[data-key-clear]:checked')) questions.push(clearConfirmText(form.getAttribute('data-key-name') || ''));
            if (!questions.length) return;
            var now = askNow();
            if (!now) e.preventDefault();            // the dialog answers later: the submit goes again after «أكيد»
            ask(questions, function (ok) {
                if (!ok) {
                    if (now) e.preventDefault();
                    return;
                }
                if (now) return;                      // window.confirm said yes: the submit goes on as it is
                form.__lqConfirmed = true;
                if (form.requestSubmit) form.requestSubmit();
                else form.submit();
            });
        });
    }

    // --- معالجة الصور: «رجّع عزل الخلفية (…)» لما يكون عزل الخلفية متوقف ------------------------------
    var bgRestore = page.querySelector('[data-bg-restore]');
    if (bgRestore) {
        bgRestore.addEventListener('click', function () {
            setBusy(bgRestore, true);
            postJson(BG_METHOD_URL, { method: bgRestore.getAttribute('data-bg-restore') }).then(function (res) {
                if (res && res.ok && isObject(res.data) && res.data.status === 'success') {
                    toast(String(res.data.message || 'رجع عزل الخلفية.'), 'success');
                    setTimeout(reloadKeepingPlace, 900);
                    return;
                }
                toast(bgMethodError(res), 'danger');
                setBusy(bgRestore, false);
            }, function () {
                toast('ما قدرنا نوصل للخادم.', 'danger');
                setBusy(bgRestore, false);
            });
        });
    }

    // --- النشر الآلي ---------------------------------------------------------
    // the main switch and «النشر الآلي لكل الماركات المؤكدة» (the strict lane): each asks, then saves its own form
    Array.prototype.forEach.call(page.querySelectorAll('[data-autopub-form]'), function (apForm) {
        var apSwitch = apForm.querySelector('[data-autopub-switch]');
        var apSave = apForm.querySelector('[data-autopub-save]');
        setHidden(apSave, true);
        if (apSwitch) {
            // the switch saves itself right after «أكيد» (no «حفظ» button); «لا» puts it back
            apSwitch.addEventListener('change', function () {
                var text = apSwitch.checked ? apSwitch.getAttribute('data-confirm-on') : apSwitch.getAttribute('data-confirm-off');
                var submit = function () {
                    apSwitch.disabled = false;
                    if (apForm.requestSubmit) apForm.requestSubmit();
                    else apForm.submit();
                };
                if (!text) {
                    submit();
                    return;
                }
                ask([text], function (ok) {
                    if (ok) submit();
                    else apSwitch.checked = !apSwitch.checked;
                });
            });
        }
    });
})();
