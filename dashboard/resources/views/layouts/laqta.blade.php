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
        ['key' => 'health', 'route' => 'dashboard.diagnostics', 'label' => 'الصحة والتكلفة', 'icon' => 'health'],
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
<body class="lq-body @yield('body_class')" data-lq-status-url="{{ url('/api/batch-status') }}">
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
        var COPY = {
            loading: { label: 'لحظة…', text: 'جارٍ قراءة حالة التشغيل…', link: 'صفحة التشغيل ←' },
            idle: { label: 'جاهز', text: 'لا يوجد تشغيل الآن', link: 'ابدأ تشغيلاً جديداً ←' },
            running: { label: 'يعمل', text: 'جارٍ تجهيز التشغيل…', link: 'عرض التفاصيل ←' },
            paused: { label: 'متوقف مؤقتاً', text: 'التشغيل متوقف مؤقتاً', link: 'عرض التفاصيل ←' },
            error: { label: 'توقف بعطل', text: 'توقف التشغيل بسبب عطل.', link: 'عرض التفاصيل ←' },
            unknown: { label: 'غير معروف', text: 'تعذّر قراءة حالة التشغيل', link: 'صفحة التشغيل ←' }
        };
        // Worker notices are "CODE: message | CODE: message"; show plain Arabic instead of the codes.
        var NOTICE_TEXT = {
            SHEETS_UNAVAILABLE: 'تعذّر الاتصال بـ Google Sheet.',
            SHEET_CONFIG: 'إعدادات ربط الشيت تحتاج مراجعة.',
            PROVIDER_DOWN: 'مصادر البحث غير متاحة. المنتجات الباقية رجعت للطابور.',
            SERPER_CREDIT: 'رصيد Serper انتهى أو المفتاح مرفوض.',
            GEMINI_DOWN: 'Gemini لا يستجيب.'
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

            var state = 'idle';
            if (FAILED[status]) state = 'error';
            else if (PAUSED[status]) state = 'paused';
            else if (RUNNING[status] || runningFlag) state = pauseFlag ? 'paused' : 'running';

            return {
                state: state,
                status: status || 'idle',
                total: total,
                done: done,
                rows: rowsLabel(run),
                notice: plainNotice(notice),
                product: toText(firstOf(both, ['current_product', 'current_product_name'])),
                reviewCount: review
            };
        }

        function describeRunStatus(n) {
            var state = n && COPY[n.state] ? n.state : 'unknown';
            var copy = COPY[state];
            var out = { state: state, label: copy.label, text: copy.text, count: '', pct: null, link: copy.link };
            if (state === 'running' || state === 'paused') {
                if (n.total && n.total > 0) {
                    var done = Math.min(n.done || 0, n.total);
                    out.text = n.rows ? 'الصفوف ' + n.rows : 'المنتجات';
                    out.count = done + ' / ' + n.total;
                    out.pct = Math.round(done / n.total * 100);
                }
            } else if (state === 'error') {
                out.text = n.notice || (n.status === 'provider_down' ? NOTICE_TEXT.PROVIDER_DOWN : copy.text);
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
        }

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
            toast: toast
        };

        // No silent destructive action: anything with data-lq-confirm asks first (capture phase, so it runs
        // before the element's own click handlers and before a form submits).
        document.addEventListener('click', function (e) {
            var target = e.target && e.target.closest ? e.target.closest('[data-lq-confirm]') : null;
            if (!target || target.disabled) return;
            if (!window.confirm(target.getAttribute('data-lq-confirm'))) {
                e.preventDefault();
                e.stopPropagation();
            }
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
