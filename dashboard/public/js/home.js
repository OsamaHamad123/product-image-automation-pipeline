/*
 * لقطة · اليوم (resources/views/dashboard/index.blade.php): the greeting, the next step, the one alert banner, the KPI
 * tiles and the sheet funnel. The run panel and the sheet tools on the same page belong to public/js/run.js.
 *
 * Two sources, both read-only:
 *   GET /api/run/live   the /api/batch-status payload (the same one the sidebar badge reads) and the run's alert.
 *                       «بانتظار مراجعتك» follows its ready_for_review. ONE poller per page:
 *                         admin     run.js owns the poll; every snapshot reaches this file as the `lq:run-live`
 *                                   document event (and LaqtaRunPage.controller.state.snapshot on mount).
 *                         reviewer  run.js is not on the page: this file polls /api/run/live itself (loop()).
 *   GET /api/overview   on load and after a run ends: the sheet funnel and its KPIs and the provider alerts.
 * describeOverview / describeHomeLive / mergeBanner / nextStep are pure (tests/test_laqta_run.py runs them under node).
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
    function describeOverview(res) {
        var data = res && res.ok && res.data && res.data.status === 'success' ? res.data : null;
        if (!data) {
            var message = 'ما قدرنا نجيب الأرقام هلق. منعيد المحاولة بعد شوي.';
            return {
                state: 'error', message: message,
                kpis: KPI_ORDER.map(function (k) { return { key: k, value: '—', note: message, tone: 'muted' }; }),
                funnel: { state: 'error', message: message },
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
            var legend = stages.map(function (s) { return { label: s.label, value: C.num(s.value) || 0, tone: s.tone }; });
            funnel = {
                state: total > 0 ? 'ok' : 'empty',
                meta: C.countText(total),
                total: total,
                // the bar is one picture (role="img"): its name says every stage with its number
                label: 'وين وصلت منتجات الشيت (' + C.countText(total) + '): ' + legend.map(function (l) {
                    return l.label + ' ' + l.value;
                }).join('، '),
                segments: stages.filter(function (s) { return C.num(s.value) > 0; }).map(function (s) {
                    return { tone: s.tone, value: s.value, pct: total > 0 ? Math.round(s.value / total * 1000) / 10 : 0 };
                }),
                legend: legend,
                notes: Array.isArray(sheet.notes) ? sheet.notes : [],
                message: total > 0 ? '' : 'الشيت فاضي: ما في منتجات نعرضها.'
            };
        } else {
            funnel = { state: 'error', message: sheet.message || 'ما قدرنا نقرأ الشيت هلق.' };
        }

        return {
            state: 'ok',
            kpis: kpis,
            funnel: funnel,
            alerts: Array.isArray(data.alerts) ? data.alerts : []
        };
    }

    /* The live parts: the review count (same as the sidebar badge), the phase and the run's alert for the banner.
     * The run itself (progress, the finished run's tiles) is drawn once, by run.js's «التشغيل الحالي» card. */
    function describeHomeLive(snapshot) {
        var b = batchOf(snapshot);
        if (!b) return { phase: null, waiting: null, banner: null };
        var phase = String(b.phase || 'idle');
        return {
            phase: phase,
            waiting: C.num(b.ready_for_review),
            banner: b.alert ? {
                title: phase === 'error' ? 'وقف التشغيل:' : 'انتبه:',
                text: C.alertText(b.alert),
                variant: phase === 'error' ? 'danger' : 'warning'
            } : null
        };
    }

    /*
     * «شو الخطوة الجاية؟»: one step, the first that applies. waiting: the review count (the sidebar badge's number);
     * brands: the count of brands missing from «جدول الماركات» (/api/run/brand-suggestions, read only when nothing waits),
     * null while unknown; notFound: the KPI. Returns null while it cannot tell yet.
     * opts.failed: the «أعطال مؤقتة» KPI (the review list's «رجّع وشغّل» re-runs them); opts.reviewer: no step that
     * leads to the owner's pages (run, brands, retry), only review work.
     */
    function nextStep(waiting, brands, notFound, phase, opts) {
        opts = opts || {};
        var w = C.num(waiting);
        if (w !== null && w > 0) {
            return { key: 'review', title: C.countText(w, 'صورة', 'صورتين', 'صور') + ' جاهزة للمراجعة.',
                     text: 'راجعها بالجملة: المقترحة بلا تحذير بتنعتمد بضغطة.', action: 'راجعها هلق', href: '/catalog?mode=bulk' };
        }
        if (w === null) return null;
        if (opts.reviewer) {
            var rnf = C.num(notFound);
            if (rnf !== null && rnf > 0) {
                return { key: 'not_found', title: C.countText(rnf) + ' ما انلقت إلها صورة.',
                         text: 'دوّر عليها بكلمات ثانية، أو حط رابط صورة، أو ارفع وحدة.', action: 'شوف اللي ما انلقت',
                         href: '/catalog?filter=not_found' };
            }
            return { key: 'done', title: 'كل شي مراجَع.', text: 'الصور الجديدة بتوصل لقائمتك لما يشتغل التشغيل الجاي.',
                     action: 'افتح المراجعة', href: '/catalog' };
        }
        var f = C.num(opts.failed);
        if (f !== null && f > 0) {
            return { key: 'failed', title: C.countText(f) + ' وقفت بعطل مؤقت.',
                     text: 'رجّعها وشغّلها من القائمة بكبسة: التشغيل بينفتح على صفوفها بس.', action: 'رجّعها وشغّلها',
                     href: '/catalog?filter=failed' };
        }
        var b = C.num(brands);
        if (b === null && brands !== false) return null;
        if (b > 0) {
            return { key: 'brands', title: C.countText(b, 'ماركة', 'ماركتين', 'ماركات') + ' ناقصة من جدول الماركات.',
                     text: 'البحث ما بيعرفها، فما بينشر صورها لحاله. ضيفها قبل التشغيل الجاي.', action: 'ضيف الماركات',
                     href: '/#run-brands' };
        }
        var nf = C.num(notFound);
        if (nf !== null && nf > 0) {
            return { key: 'not_found', title: C.countText(nf) + ' ما انلقت إلها صورة.',
                     text: 'شوفها ورجّعها للطابور، أو دوّر عليها بكلمات ثانية.', action: 'شوف اللي ما انلقت',
                     href: '/catalog?filter=not_found' };
        }
        if (C.isActive(phase)) {
            return { key: 'running', title: 'التشغيل شغّال.', text: 'الصور بتوصل لقائمة المراجعة وهو شغّال.',
                     action: 'تفاصيل التشغيل', href: '/#run-current' };
        }
        return { key: 'run', title: 'كل شي مراجَع.', text: 'ابدأ تشغيل جديد ليجيب صور للمنتجات الباقية.',
                 action: 'ابدأ تشغيل', href: '/#run-new' };
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
     * deps: fetchJson, renderOverview(view), renderLive(view), renderBanner(view), toast(text, variant) (optional:
     * without it the end of a run is not toasted here; on the admin's page run.js says it), schedule(fn, ms),
     * hidden() -> true while the tab is hidden (optional). handleLive takes a snapshot from either source.
     */
    function createController(deps) {
        var state = { lastPhase: null, overview: null, live: null, liveBanner: null, brands: null, brandsAsked: false,
                      nextSaid: undefined };

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
            var failed = state.overview ? (state.overview.kpis.filter(function (k) { return k.key === 'failed'; })[0] || {}).value : null;
            var reviewer = !!deps.reviewer;
            if (C.num(waiting) === 0 && !reviewer && !(C.num(failed) > 0) && !state.brandsAsked && deps.fetchJson) {
                state.brandsAsked = true;
                deps.fetchJson('/api/run/brand-suggestions').then(function (res) {
                    var data = res && res.ok && res.data && res.data.status === 'success' ? res.data : null;
                    state.brands = data ? (C.num(data.count) || 0) : false;
                    step();
                });
            }
            var view = nextStep(waiting, state.brands, notFound, state.live ? state.live.phase : null,
                                { failed: failed, reviewer: reviewer });
            // the box is a status (read out when it changes): every poll lands here, only a new step is drawn
            var said = JSON.stringify(view);
            if (said === state.nextSaid) return;
            state.nextSaid = said;
            deps.renderNext(view);
        }

        function loadOverview(fresh) {
            return deps.fetchJson('/api/overview' + (fresh ? '?refresh=1' : '')).then(function (res) {
                var view = describeOverview(res);
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
            var view = describeHomeLive(snapshot);
            state.live = view;
            state.liveBanner = view.banner;
            deps.renderLive(view);
            banner();
            if (view.phase !== null) {
                if (C.runJustFinished(state.lastPhase, view.phase)) {
                    // the funnel and the KPIs moved: read them again past every cache
                    loadOverview(true);
                    // the same words as the run panel: a stopped run is not «خلص»
                    var stopped = C.runUnfinished(snapshot && snapshot.run);
                    if (deps.toast) deps.toast(view.phase === 'error' ? 'وقف التشغيل بعطل.' : (stopped ? 'وقف التشغيل قبل ما يخلص.' : 'خلص التشغيل.'),
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
                bar.setAttribute('aria-label', f.label);
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
        }

        function renderLive(view) {
            if (C.num(view.waiting) === null) return;
            var counts = $$('review-count');
            for (var i = 0; i < counts.length; i++) C.setText(counts[i], view.waiting);
            C.setText(page.querySelector('[data-kpi="waiting"] [data-kpi-value]'), view.waiting);
        }

        // the header's «افتح قائمة المراجعة» goes away while the next step already leads to the same list (one action)
        function samePlace(a, b) {
            var strip = function (h) { return String(h || '').replace(/^[a-z]+:\/\/[^/]+/i, '').replace(/#.*$/, ''); };
            return strip(a) !== '' && strip(a) === strip(b);
        }

        function renderNext(view) {
            var box = $('next');
            C.setHidden(box, !view);
            var reviewLink = $('review-link');
            if (reviewLink) C.setHidden(reviewLink, !!view && samePlace(reviewLink.getAttribute('href'), view.href));
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

        // run.js on the page (the admin's run panel) owns the /api/run/live poll and says the end of a run itself
        var owner = page.hasAttribute && page.hasAttribute('data-run-page') && root.LaqtaRunPage ? root.LaqtaRunPage : null;
        var controller = createController({
            fetchJson: C.fetchJson,
            renderOverview: renderOverview,
            renderLive: renderLive,
            renderBanner: renderBanner,
            renderNext: renderNext,
            // المراجع (layout: <body data-lq-role="reviewer">): خطوات المراجعة بس
            reviewer: !!(doc.body && doc.body.getAttribute && doc.body.getAttribute('data-lq-role') === 'reviewer'),
            toast: owner ? null : C.toast,
            schedule: function (fn, ms) { return root.setTimeout(fn, ms); },
            hidden: function () { return !!doc.hidden; }
        });

        controller.loadOverview(false);
        if (owner) {
            // run.js mounted first (its script comes first): its last snapshot, then every one it reads
            var seen = owner.controller && owner.controller.state.snapshot;
            if (seen) controller.handleLive(seen);
            doc.addEventListener(owner.LIVE_EVENT, function (e) { controller.handleLive(e.detail); });
            return controller;
        }
        var initial = null;
        var island = doc.getElementById('lq-home-initial');
        try {
            initial = island ? JSON.parse(island.textContent || 'null') : null;
        } catch (e) {
            initial = null;
        }
        if (initial) controller.handleLive(initial);
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
