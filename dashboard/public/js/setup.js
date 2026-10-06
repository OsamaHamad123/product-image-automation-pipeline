/*
 * لقطة · «جهّز لقطة» (resources/views/dashboard/setup.blade.php, SetupController).
 *
 * Five steps, one on screen at a time (?step= follows it). The page starts from the server's state (#lq-setup-initial)
 * and checks nothing on its own: every live check is a button.
 * - ربط الشيت: «احفظ وافحص» saves through POST /api/sheet/save (the Settings validation, after the same question) and
 *   then checks; «افحص الشيت» checks the saved link only (POST /api/setup/check {step: 'sheet'}).
 * - المفاتيح: present or missing from the server; «افحص المفاتيح» is the connection check (POST /api/setup/check
 *   {step: 'keys', test: true}). Keys are typed in the Settings fields; no key value ever reaches this page.
 * - جدول الماركات: POST /api/setup/check {step: 'brands'} (read only), and a link to «عبّي جدول الماركات».
 * - بروفة النشر: «افحص النشر» is POST /api/system/publish-check, then the step reads the saved result.
 * - أول تشغيل صغير: «جهّز أول تشغيل» asks the server for the first 5 rows (POST /api/setup/check {step: 'run'}),
 *   «ابدأ أول تشغيل» is POST /api/run-all with that row filter, then GET /api/run/live every few seconds until it ends.
 * Skip, «خلّصت التجهيز» and the run start are saved with POST /api/setup/progress. When the page comes back into
 * view, GET /api/setup/state reads the state again (keys saved in another tab show at once).
 * View functions are pure (window.LaqtaSetup, used by the node tests); the DOM code sets text and attributes only.
 */
(function () {
    'use strict';

    var STATE_URL = '/api/setup/state';
    var CHECK_URL = '/api/setup/check';
    var PROGRESS_URL = '/api/setup/progress';
    var SHEET_SAVE_URL = '/api/sheet/save';
    var PUBLISH_URL = '/api/system/publish-check';
    var RUN_URL = '/api/run-all';
    var LIVE_URL = '/api/run/live';
    var LIVE_POLL_MS = 4000;
    /* polls after a start without ever seeing the run active before the step reads the state again */
    var LIVE_IDLE_POLLS = 5;
    var ACTIVE_PHASES = ['starting', 'running', 'paused', 'stopping'];
    var STEP_KEYS = ['sheet', 'keys', 'brands', 'publish', 'run'];
    var STATUS = { ok: ['تمام', 'success'], todo: ['لسا', 'muted'], fail: ['بدها تصليح', 'danger'],
        skipped: ['تخطّيتها', 'muted'], running: ['شغّال', 'info'] };
    /* QueueStats::RESULT_KINDS words for the counts of the finished first run */
    var RESULT_WORDS = [['proposed', 'مقترحة'], ['approved', 'معتمدة'], ['none', 'بلا اقتراح'], ['not_found', 'ما انلقت'],
        ['error', 'عطل مؤقت'], ['requeued', 'رجعت للطابور']];

    // ------------------------------------------------------------------
    // Views (pure)
    // ------------------------------------------------------------------

    function isObject(v) {
        return v !== null && typeof v === 'object' && !Array.isArray(v);
    }

    function count(v) {
        var n = typeof v === 'string' && v.trim() !== '' ? Number(v) : v;
        return typeof n === 'number' && isFinite(n) && n > 0 ? Math.floor(n) : 0;
    }

    function arabic(text) {
        return typeof text === 'string' && /[؀-ۿ]/.test(text);
    }

    /* The server's own Arabic message when it has one, else the fallback (419: the page expired). */
    function requestError(res, fallback) {
        if (res && res.status === 419) return 'انتهت صلاحية الصفحة. حدّثها وجرّب مرة تانية.';
        var data = res && isObject(res.data) ? res.data : null;
        var text = data && typeof data.error === 'string' ? data.error.trim() : '';
        return arabic(text) ? text : fallback;
    }

    function statusMeta(status) {
        var meta = STATUS[status] || STATUS.todo;
        return { label: meta[0], tone: meta[1] };
    }

    /* A state answer of the server (SetupController::view), or null. */
    function stateOf(data) {
        if (!isObject(data) || !Array.isArray(data.steps)) return null;
        var steps = data.steps.filter(function (s) { return isObject(s) && STEP_KEYS.indexOf(s.key) !== -1; });
        return steps.length === STEP_KEYS.length ? data : null;
    }

    function stepOf(state, key) {
        var steps = state && Array.isArray(state.steps) ? state.steps : [];
        for (var i = 0; i < steps.length; i++) {
            if (steps[i] && steps[i].key === key) return steps[i];
        }
        return null;
    }

    /* The step after / before `key` (ready after the last one). */
    function neighbour(key, delta) {
        if (key === 'ready') return delta < 0 ? STEP_KEYS[STEP_KEYS.length - 1] : 'ready';
        var i = STEP_KEYS.indexOf(key);
        if (i === -1) return STEP_KEYS[0];
        var j = i + delta;
        if (j < 0) return STEP_KEYS[0];
        return j >= STEP_KEYS.length ? 'ready' : STEP_KEYS[j];
    }

    /* The sheet button: «احفظ وافحص» when the fields differ from the saved link (an empty link field checks the
       saved one, or the default sheet name), else «افحص الشيت». */
    function sheetAction(saved, url, tab) {
        saved = isObject(saved) ? saved : {};
        url = String(url || '').trim();
        tab = String(tab || '').trim();
        var changed = url !== '' && (url !== String(saved.url || '').trim() || tab !== String(saved.tab || '').trim());
        return changed ? { kind: 'save', label: 'احفظ وافحص' } : { kind: 'check', label: 'افحص الشيت' };
    }

    /* «3 مقترحة · 1 بلا اقتراح · 1 ما انلقت» from the run summary counts. */
    function countsText(counts) {
        counts = isObject(counts) ? counts : {};
        var parts = [];
        RESULT_WORDS.forEach(function (pair) {
            var n = count(counts[pair[0]]);
            if (n > 0) parts.push(n + ' ' + pair[1]);
        });
        return parts.join(' · ');
    }

    /* The run step's live line from GET /api/run/live (RunController::snapshot): {known, active, text}. */
    function liveView(live) {
        if (!isObject(live) || live.status !== 'success') {
            return { known: false, active: false, text: 'ما قدرنا نقرأ حالة التشغيل هلق. جرّب كمان شوي.' };
        }
        var batch = isObject(live.batch) ? live.batch : {};
        var phase = String(batch.phase || 'idle');
        var run = isObject(live.run) ? live.run : null;
        if (ACTIVE_PHASES.indexOf(phase) !== -1) {
            var text = 'عم ندوّر: ' + count(run && run.processed) + ' من ' + count(run && run.total) + ' خلصوا.';
            if (phase === 'starting') text = 'عم نقرأ الشيت ونجهّز الصفوف…';
            if (phase === 'paused') text = 'التشغيل موقّف مؤقتاً. كمّله من صفحة التشغيل.';
            if (phase === 'stopping') text = 'عم يوقف التشغيل…';
            return { known: true, active: true, text: text };
        }
        var done = run ? countsText(run.counts) : '';
        return { known: true, active: false, text: done ? 'خلص التشغيل: ' + done + '.' : 'التشغيل مش شغّال هلق.' };
    }

    /* The plan box of «جهّز أول تشغيل» (SetupController::planFor): {text, canStart, rows}. */
    function planView(plan) {
        if (!isObject(plan)) return { text: 'ما قدرنا نجهّز أول تشغيل هلق. جرّب كمان شوي.', canStart: false, rows: '' };
        var text = typeof plan.message === 'string' && plan.message ? plan.message : 'ما قدرنا نجهّز أول تشغيل هلق.';
        var ok = plan.status === 'success' && typeof plan.rows === 'string' && plan.rows !== '';
        return { text: text, canStart: ok, rows: ok ? plan.rows : '' };
    }

    // ------------------------------------------------------------------
    // Controller (deps injected: fetchJson, render, renderStep, toast, confirm, schedule, isHidden, navigate)
    // ------------------------------------------------------------------

    function createSetup(deps) {
        var state = { data: stateOf(deps.initial), busy: {}, plan: null, live: null, polls: 0, sawActive: false,
            timer: null };

        function apply(res) {
            var data = res && res.ok ? stateOf(res.data) : null;
            if (data) {
                state.data = data;
                deps.render(data);
            }
            return data;
        }

        function busy(key, on) {
            state.busy[key] = on;
            if (deps.setBusy) deps.setBusy(key, on);
        }

        function post(url, body) {
            return deps.fetchJson(url, { method: 'POST', body: body || {} });
        }

        /* One live check of a step (and the state again); the step's own message on failure. */
        function check(step, extra, failText) {
            var body = { step: step };
            Object.keys(extra || {}).forEach(function (k) { body[k] = extra[k]; });
            return post(CHECK_URL, body).then(function (res) {
                var data = apply(res);
                if (!data) {
                    deps.toast(requestError(res, failText || 'ما خلص الفحص. جرّب مرة تانية.'), 'danger');
                    return null;
                }
                return res.data;
            }, function () {
                deps.toast('ما قدرنا نوصل للخادم.', 'danger');
                return null;
            });
        }

        function refresh() {
            return deps.fetchJson(STATE_URL, { method: 'GET' }).then(apply, function () { return null; });
        }

        /* «احفظ وافحص» / «افحص الشيت». values: {spreadsheet_url, tab_name}; saved: the link the server holds. */
        function sheet(values) {
            if (state.busy.sheet) return Promise.resolve(false);
            var step = stepOf(state.data, 'sheet') || {};
            var action = sheetAction({ url: step.url_is_default ? '' : step.url, tab: step.tab }, values.spreadsheet_url,
                values.tab_name);
            busy('sheet', true);
            var saving = action.kind !== 'save' ? Promise.resolve(true) : Promise.resolve(
                deps.confirm(deps.saveConfirmText ? deps.saveConfirmText(values.spreadsheet_url, values.tab_name) : '')
            ).then(function (ok) {
                if (!ok) return false;
                deps.sheetStatus('لحظة، عم نحفظ الربط…', '');
                return post(SHEET_SAVE_URL, values).then(function (res) {
                    if (res && res.ok && isObject(res.data) && res.data.status === 'success') return true;
                    var err = deps.sheetError ? deps.sheetError(res, true) : { text: requestError(res, 'ما انحفظ الربط.') };
                    deps.sheetStatus(err.text, 'danger');
                    return false;
                }, function () {
                    deps.sheetStatus('ما قدرنا نوصل للخادم.', 'danger');
                    return false;
                });
            });
            return saving.then(function (saved) {
                if (!saved) return false;
                deps.sheetStatus('لحظة، عم نفتح الشيت…', '');
                return check('sheet', {}, 'ما قدرنا نفحص الشيت.').then(function (data) {
                    var s = stepOf(state.data, 'sheet');
                    deps.sheetStatus(data && s ? s.summary : '', data && s ? statusMeta(s.status).tone : '');
                    return !!data;
                });
            }).then(function (done) {
                busy('sheet', false);
                return done;
            });
        }

        function simple(key, extra, failText, okText) {
            if (state.busy[key]) return Promise.resolve(false);
            busy(key, true);
            return check(key, extra, failText).then(function (data) {
                busy(key, false);
                var s = data ? stepOf(state.data, key) : null;
                if (s && okText) deps.toast(s.status === 'ok' ? okText : s.summary, s.status === 'ok' ? 'success' : 'warning');
                return !!data;
            });
        }

        function testKeys() {
            return simple('keys', { test: true }, 'ما خلص فحص المفاتيح.', 'كل المفاتيح اشتغلت.');
        }

        function checkBrands() {
            return simple('brands', {}, 'ما قدرنا نقرأ جدول الماركات.', 'جدول الماركات تمام.');
        }

        /* «افحص النشر»: the Health page's rehearsal, then the step reads its saved result. */
        function publish() {
            if (state.busy.publish) return Promise.resolve(false);
            busy('publish', true);
            deps.renderStep('publish', { status: 'running', summary: 'جاري الفحص… الخطوات بتمشي ورا بعض، وعادةً بيخلص بأقل من دقيقة.' });
            return post(PUBLISH_URL, {}).then(function (res) {
                var ok = res && res.ok && isObject(res.data) && isObject(res.data.result);
                if (!ok) deps.toast('ما خلص فحص النشر: ' + requestError(res, 'الخادم ما رجّع نتيجة.'), 'danger');
                return check('publish', {}, 'ما قدرنا نقرأ نتيجة فحص النشر.');
            }, function () {
                deps.toast('ما قدرنا نوصل للخادم لنفحص النشر.', 'danger');
                return check('publish', {});
            }).then(function (data) {
                busy('publish', false);
                var s = data ? stepOf(state.data, 'publish') : null;
                if (s) deps.toast(s.summary || 'خلص فحص النشر.', s.status === 'ok' ? 'success' : 'warning');
                return !!data;
            });
        }

        /* «جهّز أول تشغيل»: the first rows and what the run takes. */
        function plan() {
            if (state.busy.run) return Promise.resolve(null);
            busy('run', true);
            return check('run', {}, 'ما قدرنا نجهّز أول تشغيل.').then(function (data) {
                busy('run', false);
                state.plan = planView(data ? data.plan : null);
                deps.renderPlan(state.plan);
                return state.plan;
            });
        }

        function poll() {
            state.timer = null;
            return deps.fetchJson(LIVE_URL, { method: 'GET' }).then(function (res) {
                return liveView(res && res.data);
            }, function () {
                return liveView(null);
            }).then(function (view) {
                state.polls++;
                state.sawActive = state.sawActive || view.active;
                deps.renderLive(view);
                if (view.active || (!state.sawActive && state.polls < LIVE_IDLE_POLLS)) {
                    state.timer = deps.schedule(poll, LIVE_POLL_MS);
                    return view;
                }
                // the run is over (or never showed up): the step reads the report again
                return refresh().then(function () { return view; });
            });
        }

        function watch() {
            state.polls = 0;
            state.sawActive = false;
            if (state.timer === null) state.timer = deps.schedule(poll, 0);
        }

        /* «ابدأ أول تشغيل»: the Run page's POST with the plan's rows, then the start is saved and the run followed. */
        function startRun() {
            var p = state.plan;
            if (!p || !p.canStart || state.busy.start) return Promise.resolve(false);
            busy('start', true);
            var body = { row_filter: p.rows, brand_filter: '', forceOverwrite: false, skipCache: false };
            return post(RUN_URL, body).then(function (res) {
                var data = res && isObject(res.data) ? res.data : {};
                if (!(res && res.ok && data.status === 'success')) {
                    deps.toast(arabic(data.error) ? data.error : 'ما قدرنا نبدأ التشغيل. جرّب كمان مرة.', 'danger');
                    return false;
                }
                deps.toast('بلّش أول تشغيل. الصور بتوصل على قائمة المراجعة وهو شغّال.', 'success');
                state.plan = null;
                deps.renderPlan(null);
                return post(PROGRESS_URL, { action: 'run_started', rows: p.rows }).then(apply, function () { return null; })
                    .then(function () {
                        watch();
                        return true;
                    });
            }, function () {
                deps.toast('ما قدرنا نوصل للخادم.', 'danger');
                return false;
            }).then(function (started) {
                busy('start', false);
                return started;
            });
        }

        function progress(body, okText) {
            return post(PROGRESS_URL, body).then(function (res) {
                var data = apply(res);
                if (!data) {
                    deps.toast(requestError(res, 'ما انحفظ التقدّم. جرّب كمان شوي.'), 'danger');
                    return false;
                }
                if (okText) deps.toast(okText, 'success');
                return true;
            }, function () {
                deps.toast('ما قدرنا نوصل للخادم.', 'danger');
                return false;
            });
        }

        /* «تخطّى هالخطوة» / «رجّع هالخطوة». */
        function skip(key) {
            var s = stepOf(state.data, key);
            if (!s) return Promise.resolve(false);
            return progress({ action: s.skipped ? 'unskip' : 'skip', step: key });
        }

        /* «خلّصت التجهيز»: the Home page stops showing the card. */
        function finish() {
            return progress({ action: 'done' }, 'خلّصت التجهيز. الرئيسية ما عادت تذكّرك فيه.').then(function (ok) {
                if (ok && deps.navigate) deps.navigate('/');
                return ok;
            });
        }

        function start() {
            if (state.data) deps.render(state.data);
            var run = stepOf(state.data, 'run');
            if (run && run.status === 'running') watch();
        }

        return { start: start, refresh: refresh, sheet: sheet, testKeys: testKeys, checkBrands: checkBrands,
            publish: publish, plan: plan, startRun: startRun, poll: poll, skip: skip, finish: finish, state: state };
    }

    var api = {
        STEP_KEYS: STEP_KEYS, STATUS: STATUS, statusMeta: statusMeta, stateOf: stateOf, stepOf: stepOf,
        neighbour: neighbour, sheetAction: sheetAction, countsText: countsText, liveView: liveView, planView: planView,
        requestError: requestError, createSetup: createSetup
    };
    if (typeof window !== 'undefined') window.LaqtaSetup = api;

    // ------------------------------------------------------------------
    // DOM
    // ------------------------------------------------------------------

    if (typeof document === 'undefined' || !document.querySelector || !document.querySelector('[data-setup-page]')) {
        return;
    }

    var page = document.querySelector('[data-setup-page]');
    var DOTS = { success: 'lq-dot--success', danger: 'lq-dot--danger', warning: 'lq-dot--warning', info: 'lq-dot--info',
        muted: 'lq-dot--muted' };
    var settings = window.LaqtaSettings || {};

    function $(hook) {
        return page.querySelector('[data-setup="' + hook + '"]');
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

    function fetchJson(url, opts) {
        opts = opts || {};
        var init = { method: opts.method || 'GET', credentials: 'same-origin', cache: 'no-store',
            headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' } };
        if (init.method !== 'GET') {
            init.headers['Content-Type'] = 'application/json';
            init.headers['X-CSRF-TOKEN'] = csrf();
            init.body = JSON.stringify(opts.body || {});
        }
        return fetch(url, init).then(function (res) {
            return res.text().then(function (text) {
                var data = null;
                try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
                return { ok: res.ok, status: res.status, data: data };
            });
        });
    }

    function toast(text, variant) {
        if (window.Laqta && window.Laqta.toast) window.Laqta.toast(text, { variant: variant || 'info' });
    }

    function confirmText(text) {
        if (window.Laqta && typeof window.Laqta.ask === 'function') return window.Laqta.ask(text);
        return window.confirm(text);
    }

    function readInitial() {
        var holder = document.getElementById('lq-setup-initial');
        if (!holder) return null;
        try {
            return JSON.parse(holder.textContent || 'null');
        } catch (e) {
            return null;
        }
    }

    var sections = {};
    STEP_KEYS.forEach(function (key) {
        sections[key] = page.querySelector('[data-setup-step="' + key + '"]');
    });

    function part(section, name) {
        return section ? section.querySelector('[data-setup-part="' + name + '"]') : null;
    }

    function renderRows(holder, rows) {
        clear(holder);
        (rows || []).forEach(function (row) {
            var li = make('li', 'lq-setup-check');
            li.setAttribute('data-row', String(row.key || ''));
            li.setAttribute('data-tone', String(row.tone || 'muted'));
            var dot = make('span', 'lq-dot ' + (DOTS[row.tone] || DOTS.muted));
            dot.setAttribute('aria-hidden', 'true');
            li.appendChild(dot);
            li.appendChild(make('span', 'lq-setup-check__label', row.label));
            var st = make('span', 'lq-setup-check__state', row.state);
            st.setAttribute('dir', 'auto');
            li.appendChild(st);
            if (row.fix || row.href) {
                var fix = make('p', 'lq-setup-check__fix', row.fix || '');
                fix.setAttribute('dir', 'auto');
                if (row.href) {
                    var a = make('a', 'lq-link', row.href_label || 'افتح');
                    a.setAttribute('href', String(row.href));
                    if (row.fix) fix.appendChild(document.createTextNode(' '));
                    fix.appendChild(a);
                }
                li.appendChild(fix);
            }
            holder.appendChild(li);
        });
    }

    /* One step's status line (and its rows when given). */
    function renderStep(key, step) {
        var section = sections[key];
        if (!section) return;
        var meta = statusMeta(step.status);
        section.setAttribute('data-status', step.status);
        var box = part(section, 'status');
        if (box) box.setAttribute('data-tone', meta.tone);
        var dot = part(section, 'dot');
        if (dot) dot.className = 'lq-dot lq-dot--lg ' + (DOTS[meta.tone] || DOTS.muted);
        var label = part(section, 'label');
        if (label) label.textContent = step.label || meta.label;
        var summary = part(section, 'summary');
        if (summary) summary.textContent = step.summary || '';
        if (Array.isArray(step.rows)) renderRows(part(section, 'rows'), step.rows);
        var skipBtn = section.querySelector('[data-setup-skip]');
        if (skipBtn && step.skipped !== undefined) skipBtn.textContent = step.skipped ? 'رجّع هالخطوة' : 'تخطّى هالخطوة';
    }

    /* The check mark of a done step (the path of resources/views/components/lq/icon.blade.php 'check'). */
    function checkIcon() {
        var ns = 'http://www.w3.org/2000/svg';
        var svg = document.createElementNS(ns, 'svg');
        var attrs = { 'class': 'lq-icon', width: '14', height: '14', viewBox: '0 0 24 24', fill: 'none',
            stroke: 'currentColor', 'stroke-width': '2.6', 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
            focusable: 'false', 'aria-hidden': 'true' };
        Object.keys(attrs).forEach(function (k) { svg.setAttribute(k, attrs[k]); });
        var p = document.createElementNS(ns, 'path');
        p.setAttribute('d', 'M5 12.5 10 17.5 19 7');
        svg.appendChild(p);
        return svg;
    }

    function render(data) {
        data.steps.forEach(function (step) {
            renderStep(step.key, step);
            var tab = page.querySelector('[data-setup-tab="' + step.key + '"]');
            if (tab) {
                tab.setAttribute('data-status', step.status);
                var st = tab.querySelector('[data-setup-tab-state]');
                if (st) st.textContent = step.label;
                var n = tab.querySelector('.lq-setup-steps__n');
                if (n) {
                    clear(n);
                    if (step.status === 'ok') n.appendChild(checkIcon());
                    else n.textContent = String(step.n || '');
                }
            }
        });
        var ok = $('progress-ok');
        if (ok && data.progress) ok.textContent = String(count(data.progress.ok));
        setHidden($('done-note'), !data.done);
        var sheetStep = stepOf(data, 'sheet');
        if (sheetStep) {
            var email = $('email');
            if (email) email.textContent = sheetStep.email || '';
            setHidden($('share'), !sheetStep.email);
            var form = $('sheet-form');
            if (form) {
                form.setAttribute('data-saved-url', sheetStep.url_is_default ? '' : String(sheetStep.url || ''));
                form.setAttribute('data-saved-tab', String(sheetStep.tab || ''));
            }
            syncSheetLabel();
        }
        var runStep = stepOf(data, 'run');
        setHidden($('review-link'), !(runStep && runStep.status === 'ok'));
        var readyTitle = $('ready-title');
        if (readyTitle) readyTitle.textContent = data.ready ? 'لقطة جاهزة' : 'باقي خطوات';
        var readyText = $('ready-text');
        if (readyText) {
            readyText.textContent = data.ready
                ? 'كل الخطوات خلصت. راجع الصور اللي لقيناها، وبعدين شغّل الشيت كله من صفحة التشغيل.'
                : 'في خطوات لسا ما خلصت: فيك ترجعلها، أو تخلّص التجهيز هلق وتكمّلها بعدين من الإعدادات.';
        }
    }

    // --- which step is on screen ----------------------------------------
    var active = page.getAttribute('data-active') || 'sheet';

    function show(key, focus) {
        active = STEP_KEYS.indexOf(key) !== -1 || key === 'ready' ? key : 'sheet';
        STEP_KEYS.forEach(function (k) { setHidden(sections[k], k !== active); });
        setHidden($('ready'), active !== 'ready');
        var tabs = page.querySelectorAll('[data-setup-tab]');
        for (var i = 0; i < tabs.length; i++) {
            if (tabs[i].getAttribute('data-setup-tab') === active) tabs[i].setAttribute('aria-current', 'step');
            else tabs[i].removeAttribute('aria-current');
        }
        page.setAttribute('data-active', active);
        if (window.history && window.history.replaceState) {
            window.history.replaceState(null, '', window.location.pathname + '?step=' + encodeURIComponent(active));
        }
        if (focus) {
            var target = active === 'ready' ? $('ready') : sections[active];
            var heading = target ? target.querySelector('h2') : null;
            if (heading) {
                heading.setAttribute('tabindex', '-1');
                heading.focus();
            }
            if (target && target.scrollIntoView) target.scrollIntoView({ block: 'nearest' });
        }
    }

    var tabLinks = page.querySelectorAll('[data-setup-tab]');
    for (var t = 0; t < tabLinks.length; t++) {
        tabLinks[t].addEventListener('click', function (e) {
            e.preventDefault();
            show(e.currentTarget.getAttribute('data-setup-tab'), true);
        });
    }
    var goButtons = page.querySelectorAll('[data-setup-go]');
    for (var g = 0; g < goButtons.length; g++) {
        goButtons[g].addEventListener('click', function (e) {
            var where = e.currentTarget.getAttribute('data-setup-go');
            show(where === 'ready' ? 'ready' : neighbour(active, where === 'prev' ? -1 : 1), true);
        });
    }

    // --- busy buttons ---------------------------------------------------
    var BUTTONS = {
        sheet: ['sheet-check', 'sheet-check-label', 'لحظة…'],
        keys: ['keys-test', 'keys-test-label', 'عم نفحص… (لحد 45 ثانية)'],
        brands: ['brands-check', 'brands-check-label', 'عم نقرأ…'],
        publish: ['publish-run', 'publish-run-label', 'جاري الفحص…'],
        run: ['run-plan', 'run-plan-label', 'عم نجهّز…'],
        start: ['run-start', 'run-start-label', 'عم يبلّش…']
    };
    var idleLabels = {};

    function setBusy(key, on) {
        var spec = BUTTONS[key];
        if (!spec) return;
        var button = $(spec[0]);
        var label = $(spec[1]);
        if (label && idleLabels[key] === undefined) idleLabels[key] = label.textContent;
        if (button) {
            button.disabled = on;
            if (on) button.setAttribute('aria-busy', 'true');
            else button.removeAttribute('aria-busy');
        }
        if (label) label.textContent = on ? spec[2] : idleLabels[key];
        if (key === 'sheet' && !on) syncSheetLabel();
    }

    // --- the sheet form -------------------------------------------------
    var sheetForm = $('sheet-form');
    var sheetUrl = $('sheet-url');
    var sheetTab = $('sheet-tab');

    function sheetValues() {
        return { spreadsheet_url: sheetUrl ? sheetUrl.value.trim() : '', tab_name: sheetTab ? sheetTab.value.trim() : '' };
    }

    function syncSheetLabel() {
        if (!sheetForm) return;
        var values = sheetValues();
        var action = sheetAction({ url: sheetForm.getAttribute('data-saved-url'), tab: sheetForm.getAttribute('data-saved-tab') },
            values.spreadsheet_url, values.tab_name);
        var label = $('sheet-check-label');
        var button = $('sheet-check');
        if (label && !(button && button.disabled)) label.textContent = action.label;
        idleLabels.sheet = action.label;
    }

    function sheetStatus(text, tone) {
        var status = $('sheet-status');
        if (!status) return;
        status.textContent = text || '';
        status.setAttribute('data-tone', tone || '');
    }

    function renderPlan(view) {
        var box = $('plan');
        setHidden(box, !view);
        if (!view) return;
        $('plan-text').textContent = view.text;
        setHidden($('run-start'), !view.canStart);
    }

    function renderLive(view) {
        setHidden($('live'), false);
        $('live-text').textContent = view.text;
        setHidden($('live-spin'), !view.active);
    }

    var controller = createSetup({
        initial: readInitial(),
        fetchJson: fetchJson,
        render: render,
        renderStep: function (key, step) { renderStep(key, step); },
        renderPlan: renderPlan,
        renderLive: renderLive,
        setBusy: setBusy,
        sheetStatus: sheetStatus,
        sheetError: settings.sheetError,
        saveConfirmText: settings.saveConfirmText,
        confirm: confirmText,
        toast: toast,
        schedule: function (fn, ms) { return window.setTimeout(fn, ms); },
        navigate: function (url) { window.location.href = url; }
    });

    if (sheetUrl) sheetUrl.addEventListener('input', syncSheetLabel);
    if (sheetTab) sheetTab.addEventListener('input', syncSheetLabel);
    if (sheetForm) {
        sheetForm.addEventListener('submit', function (e) {
            e.preventDefault();
            controller.sheet(sheetValues());
        });
    }
    var bind = function (hook, fn) {
        var el = $(hook);
        if (el) el.addEventListener('click', fn);
    };
    bind('sheet-check', function () { controller.sheet(sheetValues()); });
    bind('keys-test', function () { controller.testKeys(); });
    bind('brands-check', function () { controller.checkBrands(); });
    bind('publish-run', function () { controller.publish(); });
    bind('run-plan', function () { controller.plan(); });
    bind('run-start', function () { controller.startRun(); });
    bind('finish', function () { controller.finish(); });
    var skips = page.querySelectorAll('[data-setup-skip]');
    for (var k = 0; k < skips.length; k++) {
        skips[k].addEventListener('click', function (e) { controller.skip(e.currentTarget.getAttribute('data-setup-skip')); });
    }
    bind('copy', function () {
        var text = ($('email') || {}).textContent || '';
        var done = function () { toast('انتسخ عنوان حساب الخدمة.', 'success'); };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text.trim()).then(done, function () { toast('انسخه يدوياً: ' + text, 'info'); });
        } else {
            toast('انسخه يدوياً: ' + text, 'info');
        }
    });
    // back into view: keys saved in another tab, a run that ended meanwhile
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) controller.refresh();
    });

    show(active, false);
    controller.start();
})();
