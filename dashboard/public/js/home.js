/*
 * لقطة · الرئيسية (resources/views/dashboard/index.blade.php).
 *
 * Two sources, both read-only:
 *   GET /api/run/live   every few seconds: the /api/batch-status payload (the same one the sidebar badge reads),
 *                       the current or last run's summary. «بانتظار مراجعتك» follows its ready_for_review.
 *   GET /api/overview   on load and after a run ends: the sheet funnel and its KPIs, auto-publish readiness,
 *                       the week's cost, provider alerts and the last connection check.
 * describeOverview / describeHomeLive / mergeBanner are pure (tests/test_laqta_run.py runs them under node).
 */
(function (root) {
    'use strict';

    var C = root.LaqtaRunCommon;
    var POLL_ACTIVE_MS = 5000;
    var POLL_IDLE_MS = 10000;
    var KPI_ORDER = ['waiting', 'published', 'not_found', 'failed'];

    function batchOf(snapshot) {
        return snapshot && snapshot.status === 'success' && snapshot.batch && typeof snapshot.batch === 'object'
            ? snapshot.batch : null;
    }

    /* Every section of /api/overview as the page shows it; a section that failed says why, never zero. */
    function describeOverview(res, nowSec) {
        var data = res && res.ok && res.data && res.data.status === 'success' ? res.data : null;
        var nowMs = (typeof nowSec === 'number' ? nowSec : Date.now() / 1000) * 1000;
        if (!data) {
            var message = 'ما قدرنا نجيب الأرقام هلق. منعيد المحاولة بعد شوي.';
            return {
                state: 'error', message: message,
                kpis: KPI_ORDER.map(function (k) { return { key: k, value: '—', note: message, tone: 'muted' }; }),
                funnel: { state: 'error', message: message },
                readiness: { state: 'error', message: message },
                cost: { state: 'error', text: '—' },
                services: { checkedText: '', items: [] },
                alerts: []
            };
        }
        var kpis = KPI_ORDER.map(function (k) {
            var kpi = (data.kpis || {})[k] || {};
            return {
                key: k,
                value: C.num(kpi.value) === null ? '—' : String(kpi.value),
                note: String(kpi.note || ''),
                tone: String(kpi.tone || 'muted'),
                source: String(kpi.source || '')
            };
        });

        var sheet = data.sheet || {};
        var funnel;
        if (sheet.status === 'ok') {
            var total = C.num(sheet.total) || 0;
            var stages = Array.isArray(sheet.stages) ? sheet.stages : [];
            funnel = {
                state: total > 0 ? 'ok' : 'empty',
                meta: C.countText(total),
                total: total,
                segments: stages.filter(function (s) { return C.num(s.value) > 0; }).map(function (s) {
                    return { tone: s.tone, value: s.value, pct: total > 0 ? Math.round(s.value / total * 1000) / 10 : 0 };
                }),
                legend: stages.map(function (s) { return { label: s.label, value: C.num(s.value) || 0, tone: s.tone }; }),
                notes: Array.isArray(sheet.notes) ? sheet.notes : [],
                message: total > 0 ? '' : 'الشيت فاضي: ما في منتجات نعرضها.'
            };
        } else {
            funnel = { state: 'error', message: sheet.message || 'ما قدرنا نقرأ الشيت هلق.' };
        }

        var r = data.readiness || {};
        var readiness = r.status === 'ok' ? {
            state: (r.brands || []).length ? 'ok' : 'empty',
            intro: String(r.intro || ''),
            brands: (r.brands || []).map(function (b) {
                return { brand: String(b.brand || ''), text: String(b.text || ''), pct: C.num(b.pct) || 0, tone: b.tone || 'teal' };
            }),
            more: C.num(r.more) || 0,
            message: 'لسا ما في مراجعات كفاية لأي ماركة. كل ما تعتمد أو ترفض اقتراح، بتقرب الماركة من النشر الآلي.'
        } : { state: 'error', message: r.message || 'ما قدرنا نحسب الجاهزية هلق.' };

        var cost = data.cost || {};
        var services = data.services || {};
        return {
            state: 'ok',
            kpis: kpis,
            funnel: funnel,
            readiness: readiness,
            cost: cost.status === 'ok' ? { state: 'ok', text: C.usdText(cost.week_usd) }
                : { state: 'error', text: '—', message: cost.message || '' },
            services: {
                checkedText: C.num(services.checked_at) !== null ? 'آخر فحص ' + C.whenText(services.checked_at, nowMs)
                    : (services.text || 'ما انعمل فحص بعد'),
                items: Array.isArray(services.items) ? services.items : []
            },
            alerts: Array.isArray(data.alerts) ? data.alerts : []
        };
    }

    /* The live parts: the review count (same as the sidebar badge) and the «آخر تشغيل» card. */
    function describeHomeLive(snapshot, nowSec) {
        var b = batchOf(snapshot);
        var nowMs = (typeof nowSec === 'number' ? nowSec : Date.now() / 1000) * 1000;
        if (!b) {
            return {
                phase: null, waiting: null,
                banner: null,
                lastRun: { mode: 'unavailable', title: 'آخر تشغيل',
                           message: (snapshot && snapshot.message) || 'تعذّر قراءة حالة التشغيل هلق.' }
            };
        }
        var phase = String(b.phase || 'idle');
        var runB = b.run && typeof b.run === 'object' ? b.run : {};
        var run = snapshot.run && typeof snapshot.run === 'object' ? snapshot.run : null;
        var lastRun;
        if (C.isActive(phase)) {
            var starting = phase === 'starting';
            var total = C.num(runB.total) || 0;
            var done = Math.min(C.num(runB.processed) || 0, total);
            var percent = total > 0 ? Math.round(done / total * 100) : 0;
            lastRun = {
                mode: 'live',
                title: 'التشغيل الحالي',
                phaseText: phase === 'paused' ? 'التشغيل متوقف مؤقتاً.'
                    : (phase === 'running' && b.current_product ? 'عم ندوّر هلق على: ' + b.current_product
                        : String(b.phase_text || '')),
                percent: starting ? null : percent,
                percentText: starting ? '—' : percent + '%',
                countsText: starting ? '' : done + ' من ' + total + ' في هذا التشغيل',
                meta: run && run.current && run.rows_label ? 'الصفوف ' + run.rows_label : ''
            };
        } else if (run && run.total > 0) {
            // a newer run stopped before its first search: this summary is the run before it
            var startFailed = phase === 'error' && run.is_state_run === false;
            lastRun = {
                mode: 'summary',
                title: startFailed ? 'التشغيل اللي قبله' : 'آخر تشغيل',
                meta: C.runMeta(run, nowMs),
                tiles: C.resultTiles(run.counts),
                explain: (startFailed ? 'آخر محاولة تشغيل وقفت قبل ما تبحث عن ولا منتج (السبب بالشريط فوق). ' : '') +
                    String(run.explain || '')
            };
        } else {
            lastRun = { mode: 'empty', title: 'آخر تشغيل', message: 'لسا ما صار ولا تشغيل. ابدأ واحد من «تشغيل جديد».' };
        }
        return {
            phase: phase,
            waiting: C.num(b.ready_for_review),
            banner: b.alert ? {
                title: phase === 'error' ? 'وقف التشغيل:' : 'انتبه:',
                text: C.alertText(b.alert),
                variant: phase === 'error' ? 'danger' : 'warning'
            } : null,
            lastRun: lastRun
        };
    }

    /*
     * «شو الخطوة الجاية؟»: one step, the first that applies. waiting: the review count (the sidebar badge's number);
     * brands: the count of brands missing from «جدول الماركات» (/api/run/brand-suggestions, read only when nothing waits),
     * null while unknown; notFound: the KPI. Returns null while it cannot tell yet.
     */
    function nextStep(waiting, brands, notFound, phase) {
        var w = C.num(waiting);
        if (w !== null && w > 0) {
            return { key: 'review', title: C.countText(w, 'صورة', 'صورتين', 'صور') + ' جاهزة للمراجعة.',
                     text: 'راجعها بالجملة: المقترحة بلا تحذير بتنعتمد بضغطة.', action: 'راجعها هلق', href: '/catalog?mode=bulk' };
        }
        if (w === null) return null;
        var b = C.num(brands);
        if (b === null && brands !== false) return null;
        if (b > 0) {
            return { key: 'brands', title: C.countText(b, 'ماركة', 'ماركتين', 'ماركات') + ' ناقصة من جدول الماركات.',
                     text: 'البحث ما بيعرفها، فما بينشر صورها لحاله. ضيفها قبل التشغيل الجاي.', action: 'ضيف الماركات',
                     href: '/batch-automation#run-brands' };
        }
        var nf = C.num(notFound);
        if (nf !== null && nf > 0) {
            return { key: 'not_found', title: C.countText(nf) + ' ما انلقت إلها صورة.',
                     text: 'شوفها ورجّعها للطابور، أو دوّر عليها بكلمات ثانية.', action: 'شوف اللي ما انلقت',
                     href: '/catalog?filter=not_found' };
        }
        if (C.isActive(phase)) {
            return { key: 'running', title: 'التشغيل شغّال.', text: 'الصور بتوصل لقائمة المراجعة وهو شغّال.',
                     action: 'تفاصيل التشغيل', href: '/batch-automation' };
        }
        return { key: 'run', title: 'كل شي مراجَع.', text: 'ابدأ تشغيل جديد ليجيب صور للمنتجات الباقية.',
                 action: 'ابدأ تشغيل', href: '/batch-automation' };
    }

    /* One banner for everything that needs attention: the run's alert first, then provider alerts (ops_health). */
    function mergeBanner(liveBanner, alerts) {
        var items = [];
        if (liveBanner) items.push(liveBanner);
        (alerts || []).forEach(function (a) {
            var title = String(a.title || '').trim();
            if (!title) return;
            if (liveBanner && liveBanner.text.indexOf(title) !== -1) return;   // the run already said it
            items.push({ title: title + ':', text: String(a.text || ''), variant: 'danger' });
        });
        if (!items.length) return { visible: false, title: '', text: '', variant: 'warning' };
        var main = items[0];
        var text = main.text;
        if (items.length > 1) {
            text += ' وكمان: ' + items.slice(1).map(function (i) { return i.title.replace(/:$/, ''); }).join('، ') + '.';
        }
        return {
            visible: true,
            title: main.title,
            text: text,
            variant: items.some(function (i) { return i.variant === 'danger'; }) ? 'danger' : 'warning'
        };
    }

    /*
     * deps: fetchJson, renderOverview(view), renderLive(view), renderBanner(view), toast(text, variant),
     * now() -> epoch seconds, schedule(fn, ms), hidden() -> true while the tab is hidden (optional).
     */
    function createController(deps) {
        var state = { lastPhase: null, overview: null, live: null, liveBanner: null, brands: null, brandsAsked: false };

        function now() {
            return deps.now ? deps.now() : Date.now() / 1000;
        }

        function banner() {
            deps.renderBanner(mergeBanner(state.liveBanner, state.overview ? state.overview.alerts : []));
            step();
        }

        // the next step; the missing brands are read (once) only when nothing waits for review
        function step() {
            if (!deps.renderNext) return;
            var waiting = state.live && C.num(state.live.waiting) !== null ? state.live.waiting
                : (state.overview ? (state.overview.kpis.filter(function (k) { return k.key === 'waiting'; })[0] || {}).value : null);
            var notFound = state.overview ? (state.overview.kpis.filter(function (k) { return k.key === 'not_found'; })[0] || {}).value : null;
            if (C.num(waiting) === 0 && !state.brandsAsked && deps.fetchJson) {
                state.brandsAsked = true;
                deps.fetchJson('/api/run/brand-suggestions').then(function (res) {
                    var data = res && res.ok && res.data && res.data.status === 'success' ? res.data : null;
                    state.brands = data ? (C.num(data.count) || 0) : false;
                    step();
                });
            }
            deps.renderNext(nextStep(waiting, state.brands, notFound, state.live ? state.live.phase : null));
        }

        function loadOverview(fresh) {
            return deps.fetchJson('/api/overview' + (fresh ? '?refresh=1' : '')).then(function (res) {
                var view = describeOverview(res, now());
                state.overview = view;
                deps.renderOverview(view);
                if (state.live && C.num(state.live.waiting) !== null) {
                    deps.renderLive(state.live);     // the live review count wins over the slower overview read
                }
                banner();
                return view;
            });
        }

        function handleLive(snapshot) {
            var view = describeHomeLive(snapshot, now());
            state.live = view;
            state.liveBanner = view.banner;
            deps.renderLive(view);
            banner();
            if (view.phase !== null) {
                if (C.runJustFinished(state.lastPhase, view.phase)) {
                    // the funnel and the KPIs moved: read them again past every cache
                    loadOverview(true);
                    // the same words as the Run page: a stopped run is not «خلص»
                    var stopped = C.runUnfinished(snapshot && snapshot.run);
                    deps.toast(view.phase === 'error' ? 'وقف التشغيل بعطل.' : (stopped ? 'وقف التشغيل قبل ما يخلص.' : 'خلص التشغيل.'),
                        view.phase === 'error' ? 'danger' : (stopped ? 'warning' : 'success'));
                }
                state.lastPhase = view.phase;
            }
            return view;
        }

        function poll() {
            return deps.fetchJson('/api/run/live').then(function (res) {
                var snap = res.data && typeof res.data === 'object' ? res.data : null;
                if (!res.ok && !(snap && snap.status)) snap = { status: 'error' };
                return handleLive(snap);
            });
        }

        // تبويب مخفي: ما في طلب، الدورة بتضل ماشية بلا شبكة؛ لما يرجع ظاهر mount بيسأل فوراً (visibilitychange)
        function loop() {
            if (deps.hidden && deps.hidden()) {
                deps.schedule(loop, state.pollMs || POLL_IDLE_MS);
                return;
            }
            poll().then(function (view) {
                state.pollMs = view && C.isActive(view.phase) ? POLL_ACTIVE_MS : POLL_IDLE_MS;
                deps.schedule(loop, state.pollMs);
            });
        }

        return { state: state, loadOverview: loadOverview, handleLive: handleLive, poll: poll, loop: loop, step: step };
    }

    // ---------------------------------------------------------------------------------------------
    // DOM
    // ---------------------------------------------------------------------------------------------

    function mount(doc) {
        var page = doc.querySelector('[data-home-page]');
        if (!page) return null;
        var $ = function (name) { return page.querySelector('[data-home="' + name + '"]'); };
        var $$ = function (name) { return page.querySelectorAll('[data-home="' + name + '"]'); };

        // Date and greeting in the viewer's own time (the server wrote its own as a fallback).
        var greetingEl = $('greeting');
        var today = new Date();
        C.setText($('date'), C.dateLine(today));
        C.setText(greetingEl, C.greeting(C.zoneHour(today), greetingEl.getAttribute('data-owner') || ''));

        function renderOverview(view) {
            view.kpis.forEach(function (k) {
                var tile = page.querySelector('[data-kpi="' + k.key + '"]');
                if (!tile) return;
                tile.setAttribute('aria-busy', 'false');
                C.setText(tile.querySelector('[data-kpi-value]'), k.value);
                var note = tile.querySelector('[data-kpi-note]');
                C.setText(note, k.note);
                ['muted', 'success', 'warning', 'danger'].forEach(function (t) {
                    note.classList.toggle('lq-kpi__note--' + t, t === k.tone);
                });
            });

            var f = view.funnel;
            var funnel = $('funnel');
            funnel.setAttribute('data-state', f.state);
            funnel.setAttribute('aria-busy', 'false');
            C.setHidden($('funnel-loading'), true);
            C.setHidden($('funnel-error'), f.state === 'ok');
            C.setText($('funnel-error-text'), f.message || '');
            C.setHidden($('funnel-body'), f.state !== 'ok');
            C.setText($('funnel-meta'), f.state === 'ok' || f.state === 'empty' ? f.meta : '');
            if (f.state === 'ok') {
                var bar = $('funnel-bar');
                C.clear(bar);
                f.segments.forEach(function (s) {
                    var seg = C.el(doc, 'span', 'lq-progress__seg lq-tone--' + s.tone);
                    seg.style.width = s.pct + '%';
                    bar.appendChild(seg);
                });
                bar.setAttribute('aria-label', 'وين وصلت منتجات الشيت: ' + f.legend.map(function (l) {
                    return l.label + ' ' + l.value;
                }).join('، '));
                var legend = $('funnel-legend');
                C.clear(legend);
                f.legend.forEach(function (l) {
                    var li = C.el(doc, 'li', 'lq-legend__item');
                    li.appendChild(C.el(doc, 'span', 'lq-legend__swatch lq-tone--' + l.tone));
                    li.appendChild(doc.createTextNode(l.label));
                    li.appendChild(C.el(doc, 'span', 'lq-legend__value', l.value));
                    legend.appendChild(li);
                });
                var notes = $('funnel-notes');
                C.clear(notes);
                f.notes.forEach(function (n) { notes.appendChild(C.el(doc, 'p', null, n)); });
                C.setHidden(notes, !f.notes.length);
            }

            var r = view.readiness;
            C.setHidden($('ready-loading'), true);
            C.setText($('ready-intro'), r.intro || '');
            C.setHidden($('ready-intro'), !r.intro);
            C.setHidden($('ready-message'), r.state === 'ok');
            C.setText($('ready-message'), r.message || '');
            var list = $('ready-list');
            C.clear(list);
            (r.brands || []).forEach(function (b) {
                var item = C.el(doc, 'li', 'lq-home-ready__item');
                var head = C.el(doc, 'div', 'lq-home-ready__head');
                var name = C.el(doc, 'bdi', 'lq-home-ready__brand', b.brand);
                name.setAttribute('dir', 'auto');
                head.appendChild(name);
                head.appendChild(C.el(doc, 'span', 'lq-home-ready__text', b.text));
                item.appendChild(head);
                var track = C.el(doc, 'div', 'lq-progress lq-progress--sm');
                track.setAttribute('role', 'progressbar');
                track.setAttribute('aria-valuemin', '0');
                track.setAttribute('aria-valuemax', '100');
                track.setAttribute('aria-valuenow', String(Math.round(b.pct)));
                track.setAttribute('aria-label', b.brand + ': ' + b.text);
                var fill = C.el(doc, 'div', 'lq-progress__bar lq-tone--' + (b.tone === 'danger' ? 'danger' : (b.tone === 'success' ? 'success' : 'teal')));
                fill.style.width = Math.max(b.pct, b.pct > 0 ? 2 : 0) + '%';
                track.appendChild(fill);
                item.appendChild(track);
                list.appendChild(item);
            });
            C.setHidden(list, !(r.brands || []).length);

            C.setText($('cost'), view.cost.text);
            C.setText($('services-checked'), view.services.checkedText);
            var services = $('services');
            C.clear(services);
            view.services.items.forEach(function (s) {
                var item = C.el(doc, 'li', 'lq-status');
                item.appendChild(C.el(doc, 'span', 'lq-dot lq-dot--' + (s.tone || 'muted')));
                var name = C.el(doc, 'span', 'lq-home-services__name', s.name);
                name.setAttribute('dir', 'ltr');
                item.appendChild(name);
                item.appendChild(C.el(doc, 'span', 'lq-status__note', s.label));
                services.appendChild(item);
            });
            C.setHidden(services, !view.services.items.length);
        }

        function renderLive(view) {
            if (C.num(view.waiting) !== null) {
                var counts = $$('review-count');
                for (var i = 0; i < counts.length; i++) C.setText(counts[i], view.waiting);
                var tile = page.querySelector('[data-kpi="waiting"] [data-kpi-value]');
                C.setText(tile, view.waiting);
            }
            var last = view.lastRun;
            var card = $('lastrun');
            card.setAttribute('data-mode', last.mode);
            card.setAttribute('aria-busy', 'false');
            C.setText($('lastrun-title'), last.title);
            C.setText($('lastrun-meta'), last.meta || '');
            C.setHidden($('lastrun-loading'), true);
            C.setHidden($('lastrun-live'), last.mode !== 'live');
            C.setHidden($('lastrun-summary'), last.mode !== 'summary');
            C.setHidden($('lastrun-message'), last.mode !== 'empty' && last.mode !== 'unavailable');
            C.setText($('lastrun-message'), last.message || '');
            if (last.mode === 'live') {
                C.setText($('live-phase'), last.phaseText);
                C.setText($('live-counts'), last.countsText);
                C.setText($('live-percent'), last.percentText);
                var bar = $('live-bar');
                bar.setAttribute('aria-valuenow', String(last.percent || 0));
                $('live-fill').style.width = (last.percent || 0) + '%';
            } else if (last.mode === 'summary') {
                var tiles = $('lastrun-tiles');
                C.clear(tiles);
                last.tiles.forEach(function (t) {
                    var tile = C.el(doc, 'div', 'lq-stat lq-stat--' + t.tone);
                    tile.appendChild(C.el(doc, 'span', 'lq-stat__value', t.value));
                    tile.appendChild(C.el(doc, 'span', 'lq-stat__label', t.label));
                    tiles.appendChild(tile);
                });
                C.setText($('lastrun-explain'), last.explain);
                C.setHidden($('lastrun-explain'), !last.explain);
            }
        }

        function renderNext(view) {
            var box = $('next');
            C.setHidden(box, !view);
            if (!view) return;
            box.setAttribute('data-step', view.key);
            C.setText($('next-title'), view.title);
            C.setText($('next-text'), view.text);
            var go = $('next-action');
            C.setText(go, view.action);
            go.setAttribute('href', view.href);
        }

        function renderBanner(view) {
            var box = $('alert');
            C.setHidden(box, !view.visible);
            if (!view.visible) return;
            box.classList.toggle('lq-alert--danger', view.variant === 'danger');
            box.classList.toggle('lq-alert--warning', view.variant !== 'danger');
            C.setText($('alert-title'), view.title);
            C.setText($('alert-text'), view.text);
        }

        var controller = createController({
            fetchJson: C.fetchJson,
            renderOverview: renderOverview,
            renderLive: renderLive,
            renderBanner: renderBanner,
            renderNext: renderNext,
            toast: C.toast,
            now: function () { return Date.now() / 1000; },
            schedule: function (fn, ms) { return root.setTimeout(fn, ms); },
            hidden: function () { return !!doc.hidden; }
        });

        var initial = null;
        var island = doc.getElementById('lq-home-initial');
        try {
            initial = island ? JSON.parse(island.textContent || 'null') : null;
        } catch (e) {
            initial = null;
        }
        if (initial) controller.handleLive(initial);
        controller.loadOverview(false);
        root.setTimeout(controller.loop, initial ? POLL_ACTIVE_MS : 0);
        doc.addEventListener('visibilitychange', function () {
            if (!doc.hidden) controller.poll();
        });
        return controller;
    }

    root.LaqtaHomePage = {
        describeOverview: describeOverview,
        describeHomeLive: describeHomeLive,
        mergeBanner: mergeBanner,
        nextStep: nextStep,
        createController: createController,
        mount: mount
    };

    var doc = root.document;
    if (doc && typeof doc.querySelector === 'function') {
        if (doc.readyState === 'loading') {
            doc.addEventListener('DOMContentLoaded', function () { mount(doc); });
        } else {
            mount(doc);
        }
    }
})(typeof window !== 'undefined' ? window : globalThis);
