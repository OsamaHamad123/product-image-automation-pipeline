/*
 * لقطة · الإعدادات (resources/views/dashboard/settings.blade.php and resources/views/settings/*).
 *
 * - ربط الشيت: «معاينة» POSTs /api/sheet/preview and shows the resolved columns and the first rows;
 *   «حفظ الربط» asks first (it clears the cached sheet, keeps every review) and POSTs /api/sheet/save.
 * - المفاتيح: «تغيير» reveals an empty, write-only form; clearing a stored key asks first.
 * - النشر الآلي: the switch says what it will do before it saves (AUTO_PUBLISH_ENABLED).
 * - معالجة الصور: with background removal off, «رجّع عزل الخلفية (…)» POSTs /api/settings/bg-method {method}.
 * - متقدم: rolling back to the old search engine asks first.
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

    var ROLLBACK_TEXT = 'رح يرجع البحث للنظام القديم: ما بيقرأ الملصق ولا بيتأكد من الحجم والنوع، فبتكتر الاقتراحات '
        + 'الغلط. الإعدادات التانية والمراجعات ما بتتغير. نكمّل؟';

    /* A refused POST /api/settings/bg-method in plain Arabic (the server's own Arabic text when it has one). */
    function bgMethodError(res) {
        if (res && res.status === 419) return 'انتهت صلاحية الصفحة. حدّثها وجرّب مرة تانية.';
        var text = res && isObject(res.data) && typeof res.data.error === 'string' ? res.data.error.trim() : '';
        return text && /[؀-ۿ]/.test(text) ? text : 'ما انحفظ: جرّب مرة تانية.';
    }

    var api = { columnsView: columnsView, previewView: previewView, sheetError: sheetError,
        saveConfirmText: saveConfirmText, clearConfirmText: clearConfirmText, ROLLBACK_TEXT: ROLLBACK_TEXT,
        bgMethodError: bgMethodError, COLUMNS: COLUMNS };
    if (typeof window !== 'undefined') window.LaqtaSettings = api;

    // ------------------------------------------------------------------
    // DOM
    // ------------------------------------------------------------------

    if (typeof document === 'undefined' || !document.querySelector || !document.querySelector('[data-settings-page]')) {
        return;
    }

    var page = document.querySelector('[data-settings-page]');

    /* On a phone the tabs are one scrolling row: keep the open tab in view. */
    var activeTab = page.querySelector('.lq-settings__tab.is-active');
    if (activeTab && activeTab.scrollIntoView && window.matchMedia && window.matchMedia('(max-width: 980px)').matches) {
        activeTab.scrollIntoView({ block: 'nearest', inline: 'center' });
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
                    setTimeout(function () { window.location.reload(); }, 900);
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
            toggle.focus();
        }
    }
    for (var t = 0; t < toggles.length; t++) {
        toggles[t].addEventListener('click', function (e) {
            var id = e.currentTarget.getAttribute('data-key-toggle');
            openKeyForm(id, e.currentTarget.getAttribute('aria-expanded') !== 'true');
        });
    }
    var cancels = page.querySelectorAll('[data-key-cancel]');
    for (var c = 0; c < cancels.length; c++) {
        cancels[c].addEventListener('click', function (e) {
            openKeyForm(e.currentTarget.getAttribute('data-key-cancel'), false);
        });
    }

    /* No silent destructive save: clearing a stored key asks first (key forms and «متقدم»). With the page's dialog the
       submit waits for the answer, then goes again once «أكيد». */
    var forms = page.querySelectorAll('form');
    for (var f = 0; f < forms.length; f++) {
        forms[f].addEventListener('submit', function (e) {
            var form = e.currentTarget;
            if (form.__lqConfirmed) {
                form.__lqConfirmed = false;
                return;
            }
            var questions = [];
            if (form.querySelector('[data-key-clear]:checked')) questions.push(clearConfirmText(form.getAttribute('data-key-name') || ''));
            if (form.hasAttribute('data-advanced-form') && form.getAttribute('data-engine') !== 'v1') {
                var v1 = form.querySelector('[data-engine-v1]');
                if (v1 && v1.checked) questions.push(ROLLBACK_TEXT);
            }
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
                    setTimeout(function () { window.location.reload(); }, 900);
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
