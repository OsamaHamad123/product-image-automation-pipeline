{{--
    لقطة · Laqta Studio — application shell (dark sidebar + main column).

    A page opts in with:
        @extends('layouts.laqta')
        @section('title', 'التشغيل')                     browser tab: "التشغيل · لقطة"
        @section('lq_nav', 'run')                        optional: home | review | run | health | settings
                                                         (by default the active item follows the current route)
        @section('lq_main_class', 'lq-main--flush')      optional: full-bleed work screens (review queue)
        @section('body_class', 'page-run')               optional: extra class on <body>
        @section('content') ... @endsection
        @push('styles') … @endpush                      page CSS in a style tag (the old @section('styles') still works)
        @push('scripts') … @endpush                     page JS in a script tag (the old @section('scripts') still works)
    Optional view data: $lqReviewCount (int) seeds the review badge before the first poll.

    The sidebar run card and the review badge poll GET /api/batch-status every 5 s. Pages can reuse that one
    poll instead of starting their own: window.Laqta.onRunStatus(fn) or the `lq:run-status` document event.
--}}
@php
    $lqNavItems = [
        ['key' => 'home', 'route' => 'dashboard.index', 'label' => 'الرئيسية', 'icon' => 'home'],
        ['key' => 'review', 'route' => 'dashboard.catalog', 'label' => 'المراجعة', 'icon' => 'review', 'badge' => true],
        ['key' => 'run', 'route' => 'dashboard.batch_automation', 'label' => 'التشغيل', 'icon' => 'run'],
        ['key' => 'health', 'route' => 'dashboard.diagnostics', 'label' => 'الصحة والتكلفة', 'short' => 'الصحة', 'icon' => 'health'],
        ['key' => 'settings', 'route' => 'dashboard.settings', 'label' => 'الإعدادات', 'icon' => 'settings'],
    ];
    $lqActive = trim($__env->yieldContent('lq_nav'));
    if ($lqActive === '') {
        foreach ($lqNavItems as $lqItem) {
            if (request()->routeIs($lqItem['route'])) {
                $lqActive = $lqItem['key'];
            }
        }
    }
    $lqReviewSeed = isset($lqReviewCount) && is_numeric($lqReviewCount) ? max(0, (int) $lqReviewCount) : null;
    $lqCssVersion = @filemtime(public_path('css/laqta.css')) ?: '1';
@endphp
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="csrf-token" content="{{ csrf_token() }}">
    <meta name="color-scheme" content="light">
    <meta name="theme-color" content="#0B2226">
    <title>@hasSection('title')@yield('title') · لقطة@else لقطة · استوديو صور المنتجات@endif</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Alexandria:wght@500;600;700&family=Readex+Pro:wght@300;400;500;600;700&display=swap">
    <link rel="stylesheet" href="{{ asset('css/laqta.css') }}?v={{ $lqCssVersion }}">
    @yield('styles')
    @stack('styles')
</head>
<body class="lq-body @yield('body_class')" data-lq-status-url="{{ url('/api/batch-status') }}" data-lq-review-url="{{ route('dashboard.catalog') }}" data-lq-tz="{{ \App\Http\Controllers\ReviewController::displayTimezone() }}">
    <a class="lq-skip-link" href="#lq-main">تخطَّ إلى المحتوى</a>

    <div class="lq-shell">
        <aside class="lq-sidebar" aria-label="الشريط الجانبي">
            <div class="lq-sidebar__inner">
                <a class="lq-brand" href="{{ route('dashboard.index') }}">
                    <x-lq.logo />
                    <span class="lq-brand__text">
                        <span class="lq-brand__name">لقطة</span>
                        <span class="lq-brand__tagline">استوديو صور المنتجات</span>
                    </span>
                </a>

                <nav class="lq-nav" aria-label="التنقل الرئيسي">
                    @foreach ($lqNavItems as $lqItem)
                        <a href="{{ route($lqItem['route']) }}" @class(['lq-nav__item', 'is-active' => $lqActive === $lqItem['key']]) @if ($lqActive === $lqItem['key']) aria-current="page" @endif>
                            <x-lq.icon :name="$lqItem['icon']" />
                            <span class="lq-nav__label">{{ $lqItem['label'] }}</span>
                            @if (! empty($lqItem['short']))<span class="lq-nav__short" aria-hidden="true">{{ $lqItem['short'] }}</span>@endif
                            @if (! empty($lqItem['badge']))
                                <span class="lq-nav__badge" data-lq-review-count @if ($lqReviewSeed === null || $lqReviewSeed === 0) hidden @endif>{{ $lqReviewSeed }}</span>
                            @endif
                        </a>
                    @endforeach
                </nav>

                <div class="lq-sidebar__spacer"></div>

                <x-lq.run-card live state="loading" />
            </div>
        </aside>

        <main id="lq-main" class="lq-main @yield('lq_main_class')" tabindex="-1">
            @yield('content')
        </main>
    </div>

    <div class="lq-toast-region" data-lq-toasts role="status" aria-live="polite"></div>

    <script>
    (function () {
        'use strict';

        var POLL_MS = 5000;
        var RUNNING = { running: 1, pre_caching: 1, starting: 1, processing: 1, queued: 1, resuming: 1 };
        var PAUSED = { paused: 1, pausing: 1 };
        var FAILED = { error: 1, provider_down: 1, failed: 1 };
        var PHASES = { starting: 'running', running: 'running', paused: 'paused', stopping: 'stopping',
                       error: 'error', review: 'idle', idle: 'idle' };
        var COPY = {
            loading: { label: 'لحظة…', text: 'جارٍ قراءة حالة التشغيل…', link: 'صفحة التشغيل ←' },
            idle: { label: 'جاهز', text: 'لا يوجد تشغيل الآن', link: 'ابدأ تشغيلاً جديداً ←' },
            running: { label: 'يعمل', text: 'جارٍ تجهيز التشغيل…', link: 'عرض التفاصيل ←' },
            paused: { label: 'متوقف مؤقتاً', text: 'التشغيل متوقف مؤقتاً', link: 'عرض التفاصيل ←' },
            stopping: { label: 'عم يوقف', text: 'بيكمّل المنتج الحالي وبيوقف', link: 'عرض التفاصيل ←' },
            error: { label: 'توقف بعطل', text: 'توقف التشغيل بسبب عطل.', link: 'عرض التفاصيل ←' },
            stuck: { label: 'عالق', text: 'التشغيل عالق', link: 'صفحة التشغيل ←' },
            unknown: { label: 'غير معروف', text: 'تعذّر قراءة حالة التشغيل', link: 'صفحة التشغيل ←' }
        };
        // Worker notices are "CODE: message | CODE: message"; show plain Arabic instead of the codes.
        var NOTICE_TEXT = {
            SHEETS_UNAVAILABLE: 'تعذّر الاتصال بـ Google Sheet.',
            SHEET_CONFIG: 'إعدادات ربط الشيت تحتاج مراجعة.',
            PROVIDER_DOWN: 'مصادر البحث غير متاحة. المنتجات الباقية رجعت للطابور.',
            SERPER_CREDIT: 'رصيد Serper انتهى أو المفتاح مرفوض.',
            GEMINI_DOWN: 'Gemini لا يستجيب.',
            BUDGET_REACHED: 'بلغ صرف اليوم الميزانية اليومية للبحث، فتوقف التشغيل. المنتجات الباقية في الطابور.',
            DB_UNAVAILABLE: 'تعذّر الوصول إلى قاعدة البيانات، فتوقف التشغيل. المنتجات الباقية في الطابور.'
        };

        function isObject(v) {
            return v !== null && typeof v === 'object' && !Array.isArray(v);
        }

        function firstOf(sources, keys) {
            for (var i = 0; i < sources.length; i++) {
                var src = sources[i];
                if (!isObject(src)) continue;
                for (var j = 0; j < keys.length; j++) {
                    var v = src[keys[j]];
                    if (v !== undefined && v !== null && v !== '') return v;
                }
            }
            return undefined;
        }

        function toCount(v) {
            if (typeof v === 'number') return isFinite(v) && v >= 0 ? Math.floor(v) : null;
            if (typeof v === 'string' && /^\s*\d+(\.\d+)?\s*$/.test(v)) return Math.floor(parseFloat(v));
            return null;
        }

        function toText(v) {
            if (typeof v === 'string') return v.trim();
            if (typeof v === 'number' && isFinite(v)) return String(v);
            return '';
        }

        function isTrue(v) {
            return v === true || v === 1 || v === '1' || v === 'true';
        }

        function rowsLabel(run) {
            var rows = firstOf([run], ['rows', 'range', 'row_range']);
            if (typeof rows === 'string') return rows.trim();
            var from = toCount(isObject(rows) ? firstOf([rows], ['from', 'start', 'first']) : firstOf([run], ['row_from', 'from_row', 'start_row']));
            var to = toCount(isObject(rows) ? firstOf([rows], ['to', 'end', 'last']) : firstOf([run], ['row_to', 'to_row', 'end_row']));
            if (from !== null && to !== null) return from === to ? String(from) : from + '–' + to;
            return '';
        }

        function plainNotice(value) {
            var text = toText(value);
            if (!text) return '';
            var seen = {};
            return text.split('|').map(function (part) {
                part = part.trim();
                var m = /^([A-Z][A-Z0-9_]{2,})\s*:\s*([\s\S]*)$/.exec(part);
                if (!m) return part;
                if (NOTICE_TEXT[m[1]]) return NOTICE_TEXT[m[1]];
                return /[؀-ۿ]/.test(m[2]) ? m[2].trim() : '';
            }).filter(function (part) {
                if (!part || seen[part]) return false;
                seen[part] = true;
                return true;
            }).join(' ');
        }

        // Reads the current /api/batch-status shape (flat fields) and a future one with a `run` object.
        function normalizeRunStatus(data) {
            var root = isObject(data) ? data : {};
            var run = isObject(root.run) ? root.run : {};
            var both = [run, root];
            var status = toText(firstOf(both, ['status', 'state'])).toLowerCase();
            var total = toCount(firstOf(both, ['total', 'total_items', 'total_rows']));
            var done = toCount(firstOf(both, ['processed', 'current', 'processed_items', 'done']));
            var runningFlag = isTrue(firstOf(both, ['is_running', 'running']));
            var pauseFlag = isTrue(firstOf(both, ['pause_requested', 'paused']));
            var notice = firstOf(both, ['notice', 'message']);
            var review = toCount(firstOf([root.counts, run.counts, root, root.queue, run], ['ready_for_review', 'review_count']));

            // /api/batch-status says what the Run page says: its phase, the Arabic alert and why a run is stuck
            var phase = toText(firstOf(both, ['phase'])).toLowerCase();
            var stuck = toText(firstOf(both, ['stuck']));
            var alert = toText(firstOf(both, ['alert']));

            var state = 'idle';
            if (stuck) state = 'stuck';
            else if (PHASES[phase]) state = PHASES[phase];
            else if (FAILED[status]) state = 'error';
            else if (PAUSED[status]) state = 'paused';
            else if (RUNNING[status] || runningFlag) state = pauseFlag ? 'paused' : 'running';

            return {
                state: state,
                status: status || 'idle',
                total: total,
                done: done,
                rows: rowsLabel(run),
                notice: alert || plainNotice(notice),
                stuck: stuck,
                product: toText(firstOf(both, ['current_product', 'current_product_name'])),
                reviewCount: review
            };
        }

        function describeRunStatus(n) {
            var state = n && COPY[n.state] ? n.state : 'unknown';
            var copy = COPY[state];
            var out = { state: state, label: copy.label, text: copy.text, count: '', pct: null, link: copy.link };
            if (state === 'running' || state === 'paused' || state === 'stopping') {
                if (n.total && n.total > 0) {
                    var done = Math.min(n.done || 0, n.total);
                    out.text = n.rows ? 'الصفوف ' + n.rows : 'المنتجات';
                    out.count = done + ' / ' + n.total;
                    out.pct = Math.round(done / n.total * 100);
                }
            } else if (state === 'error') {
                out.text = n.notice || (n.status === 'provider_down' ? NOTICE_TEXT.PROVIDER_DOWN : copy.text);
            } else if (state === 'stuck') {
                out.text = n.stuck || copy.text;
            }
            return out;
        }

        function $(sel, root) {
            return (root || document).querySelector(sel);
        }

        function setHidden(el, hidden) {
            if (!el) return;
            if (hidden) el.setAttribute('hidden', '');
            else el.removeAttribute('hidden');
        }

        function renderRunCard(view) {
            var card = $('[data-lq-runcard]');
            if (!card) return;
            card.setAttribute('data-state', view.state);
            var label = $('[data-lq-run-state]', card);
            if (label && label.textContent !== view.label) label.textContent = view.label;
            var text = $('[data-lq-run-text]', card);
            if (text) text.textContent = view.text;
            var count = $('[data-lq-run-count]', card);
            if (count) count.textContent = view.count;
            setHidden(count, !view.count);
            setHidden($('[data-lq-run-sep]', card), !view.count);
            var bar = $('[data-lq-run-bar]', card);
            setHidden(bar, view.pct === null);
            if (bar && view.pct !== null) {
                bar.setAttribute('aria-valuenow', String(view.pct));
                var fill = $('[data-lq-run-fill]', bar);
                if (fill) fill.style.width = view.pct + '%';
            }
            var link = $('[data-lq-run-link]', card);
            if (link) link.textContent = view.link;
        }

        function renderReviewCount(count) {
            if (count === null || count === undefined) return;
            var badges = document.querySelectorAll('[data-lq-review-count]');
            for (var i = 0; i < badges.length; i++) {
                badges[i].textContent = count > 0 ? String(count) : '';
                setHidden(badges[i], !(count > 0));
            }
            // the tab says it too: «(12) المراجعة · لقطة»
            var base = document.title.replace(/^\(\d+\)\s*/, '');
            document.title = count > 0 ? '(' + count + ') ' + base : base;
        }

        // «نبّهني لما يخلص»: a browser notification when a run ends while this tab is in the background. Permission
        // is asked only from the owner's own click (Laqta.runNotice.request, the Run page's button).
        var RUN_ACTIVE = { running: 1, paused: 1, stopping: 1 };
        var noticeState = null;
        function noticeSupported() {
            return typeof window.Notification === 'function';
        }
        function maybeNotifyRunEnd(n) {
            var was = noticeState;
            noticeState = n.state;
            if (!was || !RUN_ACTIVE[was] || RUN_ACTIVE[n.state] || n.state === 'unknown') return;
            if (!noticeSupported() || window.Notification.permission !== 'granted' || !document.hidden) return;
            try {
                var note = new window.Notification(n.state === 'error' ? 'وقف التشغيل بعطل' : 'خلص التشغيل', {
                    body: n.reviewCount > 0 ? n.reviewCount + ' بانتظار مراجعتك.' : 'ما في شي جديد بانتظار مراجعتك.',
                    tag: 'laqta-run'
                });
                note.onclick = function () {
                    window.focus();
                    window.location.href = document.body.getAttribute('data-lq-review-url') || '/catalog';
                };
            } catch (e) { /* a browser that refuses page notifications: the toast on the page still says it */ }
        }
        var runNotice = {
            supported: noticeSupported,
            permission: function () { return noticeSupported() ? window.Notification.permission : 'denied'; },
            request: function () {
                if (!noticeSupported()) return Promise.resolve('denied');
                try {
                    var asked = window.Notification.requestPermission();
                    return asked && typeof asked.then === 'function' ? asked : Promise.resolve(window.Notification.permission);
                } catch (e) {
                    return Promise.resolve('denied');
                }
            }
        };

        var lastStatus = null;
        var timer = null;
        var inFlight = false;

        function statusUrl() {
            var body = document.body;
            return (body && body.getAttribute('data-lq-status-url')) || '/api/batch-status';
        }

        function schedule(ms) {
            clearTimeout(timer);
            timer = setTimeout(poll, ms);
        }

        function publish(n) {
            lastStatus = n;
            renderRunCard(describeRunStatus(n));
            renderReviewCount(n.reviewCount);
            maybeNotifyRunEnd(n);
            try {
                document.dispatchEvent(new CustomEvent('lq:run-status', { detail: n }));
            } catch (e) { /* very old browsers: no CustomEvent constructor */ }
        }

        function poll() {
            if (document.hidden || inFlight) return;
            inFlight = true;
            fetch(statusUrl(), { headers: { Accept: 'application/json' }, cache: 'no-store', credentials: 'same-origin' })
                .then(function (res) {
                    if (!res.ok) throw new Error('HTTP ' + res.status);
                    return res.json();
                })
                .then(function (data) {
                    publish(normalizeRunStatus(data));
                })
                .catch(function () {
                    if (!lastStatus) renderRunCard(describeRunStatus({ state: 'unknown' }));
                })
                .then(function () {
                    inFlight = false;
                    schedule(POLL_MS);
                });
        }

        // Toasts: Laqta.toast('تم الاعتماد', { variant: 'success' }). Text only (textContent), never HTML.
        var TOAST_ICONS = {
            success: 'M5 12.5 10 17.5 19 7',
            warning: 'M12 4 2.5 20h19zM12 10v4.5M12 17.5v.5',
            danger: 'M12 4 2.5 20h19zM12 10v4.5M12 17.5v.5',
            info: 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18zM12 11v5M12 7.5v.5'
        };

        function svgIcon(d, size) {
            var ns = 'http://www.w3.org/2000/svg';
            var svg = document.createElementNS(ns, 'svg');
            svg.setAttribute('width', String(size));
            svg.setAttribute('height', String(size));
            svg.setAttribute('viewBox', '0 0 24 24');
            svg.setAttribute('fill', 'none');
            svg.setAttribute('stroke', 'currentColor');
            svg.setAttribute('stroke-width', '2');
            svg.setAttribute('stroke-linecap', 'round');
            svg.setAttribute('stroke-linejoin', 'round');
            svg.setAttribute('aria-hidden', 'true');
            svg.setAttribute('focusable', 'false');
            var path = document.createElementNS(ns, 'path');
            path.setAttribute('d', d);
            svg.appendChild(path);
            return svg;
        }

        function toast(message, options) {
            var region = $('[data-lq-toasts]');
            if (!region) return null;
            options = options || {};
            var variant = options.variant === 'loading' || TOAST_ICONS[options.variant] ? options.variant : 'info';
            var el = document.createElement('div');
            el.className = 'lq-toast lq-toast--' + (variant === 'loading' ? 'info' : variant);
            var icon;
            if (variant === 'loading') {
                icon = document.createElement('span');
                icon.className = 'lq-spinner';
                icon.setAttribute('aria-hidden', 'true');
            } else {
                icon = svgIcon(TOAST_ICONS[variant], 18);
                icon.setAttribute('class', 'lq-toast__icon');
            }
            var text = document.createElement('span');
            text.className = 'lq-toast__text';
            text.textContent = String(message);
            var close = document.createElement('button');
            close.type = 'button';
            close.className = 'lq-toast__close';
            close.setAttribute('aria-label', 'إغلاق');
            close.appendChild(svgIcon('M6 6l12 12M18 6 6 18', 16));
            el.appendChild(icon);
            el.appendChild(text);
            el.appendChild(close);
            region.appendChild(el);
            var closeTimer = null;
            function dismiss() {
                clearTimeout(closeTimer);
                if (el.parentNode) el.parentNode.removeChild(el);
            }
            close.addEventListener('click', dismiss);
            var timeout = typeof options.timeout === 'number' ? options.timeout : (variant === 'loading' ? 0 : 6000);
            if (timeout > 0) closeTimer = setTimeout(dismiss, timeout);
            return {
                close: dismiss,
                update: function (newMessage) { text.textContent = String(newMessage); }
            };
        }

        // Questions in the page instead of window.confirm: Laqta.ask(text | {title, text, confirmText, cancelText,
        // danger}) resolves true / false. A plain text is split into its question (the last line ending in «؟») and
        // what it explains. Enter confirms (not a held key, not in the first 400 ms), Esc cancels, Tab stays inside,
        // and the focus goes back to where it was.
        var activeAsk = null;

        function askParts(opts) {
            if (typeof opts !== 'string') return opts || {};
            var lines = opts.split('\n');
            var last = lines.length ? lines[lines.length - 1].trim() : '';
            if (lines.length > 1 && /[؟?]$/.test(last)) return { title: last, text: lines.slice(0, -1).join('\n').trim() };
            return opts.length <= 140 && lines.length === 1 ? { title: opts } : { title: 'متأكد؟', text: opts };
        }

        function ask(opts) {
            opts = askParts(opts);
            if (activeAsk) activeAsk.finish(false);
            var opener = document.activeElement;
            return new Promise(function (resolve) {
                var back = document.createElement('div');
                back.className = 'lq-dialog-backdrop';
                var box = document.createElement('div');
                box.className = 'lq-dialog';
                box.setAttribute('role', 'alertdialog');
                box.setAttribute('aria-modal', 'true');
                box.setAttribute('aria-labelledby', 'lqAskTitle');
                var title = document.createElement('h2');
                title.className = 'lq-dialog__title';
                title.id = 'lqAskTitle';
                title.textContent = String(opts.title || '');
                box.appendChild(title);
                if (opts.text) {
                    var text = document.createElement('p');
                    text.className = 'lq-dialog__text';
                    text.id = 'lqAskText';
                    text.textContent = String(opts.text);
                    box.setAttribute('aria-describedby', 'lqAskText');
                    box.appendChild(text);
                }
                var actions = document.createElement('div');
                actions.className = 'lq-dialog__actions';
                var yes = document.createElement('button');
                yes.type = 'button';
                yes.id = 'lqAskConfirm';
                yes.className = 'lq-btn ' + (opts.danger ? 'lq-btn--danger-solid' : 'lq-btn--primary');
                yes.textContent = opts.confirmText || 'أكيد';
                var no = document.createElement('button');
                no.type = 'button';
                no.id = 'lqAskCancel';
                no.className = 'lq-btn lq-btn--secondary';
                no.textContent = opts.cancelText || 'لا، رجوع';
                actions.appendChild(yes);
                actions.appendChild(no);
                box.appendChild(actions);
                back.appendChild(box);
                var dlg = { openedAt: Date.now() };
                function onKey(e) {
                    if (activeAsk !== dlg) return;
                    if (e.key === 'Escape') {
                        e.preventDefault();
                        dlg.finish(false);
                    } else if (e.key === 'Enter') {
                        e.preventDefault();
                        if (document.activeElement === no) dlg.finish(false);
                        else if (!e.repeat && Date.now() - dlg.openedAt >= 400) dlg.finish(true);
                    } else if (e.key === 'Tab') {
                        e.preventDefault();
                        (document.activeElement === yes ? no : yes).focus();
                    }
                    e.stopPropagation();
                }
                dlg.finish = function (ok) {
                    if (activeAsk !== dlg) return;
                    activeAsk = null;
                    document.removeEventListener('keydown', onKey, true);
                    if (back.parentNode) back.parentNode.removeChild(back);
                    if (opener && typeof opener.focus === 'function' && document.contains(opener)) opener.focus();
                    resolve(!!ok);
                };
                yes.addEventListener('click', function () { dlg.finish(true); });
                no.addEventListener('click', function () { dlg.finish(false); });
                back.addEventListener('click', function (e) { if (e.target === back) dlg.finish(false); });
                activeAsk = dlg;
                document.addEventListener('keydown', onKey, true);
                document.body.appendChild(back);
                yes.focus();
            });
        }

        window.Laqta = {
            normalizeRunStatus: normalizeRunStatus,
            describeRunStatus: describeRunStatus,
            plainNotice: plainNotice,
            refreshRunStatus: function () { schedule(0); },
            lastRunStatus: function () { return lastStatus; },
            onRunStatus: function (fn) {
                document.addEventListener('lq:run-status', function (e) { fn(e.detail); });
                if (lastStatus) fn(lastStatus);
            },
            toast: toast,
            ask: ask,
            runNotice: runNotice
        };

        // No silent destructive action: anything with data-lq-confirm asks first (capture phase, so it runs
        // before the element's own click handlers and before a form submits). After «أكيد» the same element is
        // clicked again and goes through.
        document.addEventListener('click', function (e) {
            var target = e.target && e.target.closest ? e.target.closest('[data-lq-confirm]') : null;
            if (!target || target.disabled) return;
            if (target.__lqConfirmed) {
                target.__lqConfirmed = false;
                return;
            }
            e.preventDefault();
            e.stopPropagation();
            ask(target.getAttribute('data-lq-confirm')).then(function (ok) {
                if (!ok) return;
                target.__lqConfirmed = true;
                target.click();
            });
        }, true);

        // Segmented controls in button mode keep exactly one pressed item and announce the change.
        document.addEventListener('click', function (e) {
            var item = e.target && e.target.closest ? e.target.closest('[data-lq-segmented] .lq-segmented__item') : null;
            if (!item || item.disabled) return;
            var group = item.closest('[data-lq-segmented]');
            var items = group.querySelectorAll('.lq-segmented__item');
            for (var i = 0; i < items.length; i++) {
                items[i].setAttribute('aria-pressed', items[i] === item ? 'true' : 'false');
            }
            group.dispatchEvent(new CustomEvent('lq:change', { bubbles: true, detail: { value: item.getAttribute('data-value') } }));
        });

        document.addEventListener('visibilitychange', function () {
            if (!document.hidden) schedule(0);
        });

        if (typeof fetch === 'function' && ($('[data-lq-runcard]') || $('[data-lq-review-count]'))) {
            poll();
        }
    })();
    </script>
    @yield('scripts')
    @stack('scripts')
</body>
</html>
