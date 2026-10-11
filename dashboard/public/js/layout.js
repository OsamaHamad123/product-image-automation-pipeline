/*
 * لقطة · the shell's script (resources/views/layouts/laqta.blade.php loads it on every page, before the page's own
 * scripts, from this file so the browser caches it: ?v=<filemtime>).
 *
 * - The sidebar run card, the review badge, the tab title's count and the owner's run dot on the phone tab bar poll
 *   GET /api/batch-status (<body data-lq-status-url>): every 5 s, 15 s on Run and Home (they poll /api/run/live
 *   themselves), none while the tab is hidden unless «نبّهني لما يخلص» waits for a running run.
 * - Approvals running on the server (batch-status.approvals) under the run card; the session-expired banner
 *   (#lqSessionExpired) on 401 / 419.
 * - window.Laqta: normalizeRunStatus, describeRunStatus, plainNotice, refreshRunStatus, sessionExpired,
 *   sessionRestored, lastRunStatus, onRunStatus (and the `lq:run-status` document event), toast, ask, runNotice.
 * - data-lq-confirm asks before the click goes through; segmented controls send `lq:change`; «حسابي» on the phone
 *   tab bar opens the name and «خروج».
 * Everything it needs from the server is in the page: <body data-lq-*> and <meta name="csrf-token">.
 */
(function () {
    'use strict';

    var POLL_MS = 5000;
    // صفحة التشغيل والرئيسية بيسألوا /api/run/live كل 4–8 ثواني لحالهم: الشريط الجانبي بيخفّف لكل 15 ثانية هناك
    var PAGE_POLL_MS = 15000;
    // تبويب مخفي: ما في سؤال، إلا إذا المدير طلب «نبّهني لما يخلص» وفي تشغيل ماشي (كل 30 ثانية)
    var HIDDEN_POLL_MS = 30000;
    var RUNNING = { running: 1, pre_caching: 1, starting: 1, processing: 1, queued: 1, resuming: 1 };
    var PAUSED = { paused: 1, pausing: 1 };
    var FAILED = { error: 1, provider_down: 1, failed: 1 };
    var PHASES = { starting: 'running', running: 'running', paused: 'paused', stopping: 'stopping',
                   error: 'error', review: 'idle', idle: 'idle' };
    var COPY = {
        loading: { label: 'لحظة…', text: 'لحظة، عم نقرأ حالة التشغيل…', link: 'صفحة التشغيل ←' },
        idle: { label: 'جاهز', text: 'ما في تشغيل هلق', link: 'ابدأ تشغيل جديد ←' },
        running: { label: 'يعمل', text: 'عم نجهّز التشغيل…', link: 'عرض التفاصيل ←' },
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

    // نقطة التشغيل بشريط الموبايل (للمدير بس، data-lq-admin): نفس حالة الكرت الجانبي المخفي هناك
    function renderRunDot(view) {
        var dot = $('[data-lq-run-dot]');
        if (!dot) return;
        dot.setAttribute('data-state', view.state);
        dot.setAttribute('aria-label', 'حالة التشغيل: ' + view.label);
    }

    // اعتمادات بتشتغل عالخادم (approval_jobs، batch-status.approvals): بتبيّن بكل صفحة، لأنه المراجع بيقدر يطلع من
    // صفحة المراجعة وهي لسا ماشية. فشل بآخر ساعة بيضل ظاهر لحتى يعرف إنه في شي لازم ينعاد
    function renderApprovals(a) {
        var card = $('[data-lq-runcard]');
        if (!card || !isObject(a)) return;
        var line = $('[data-lq-approvals]', card);
        if (!line) {
            line = document.createElement('div');
            line.className = 'lq-runcard__approvals';
            line.setAttribute('data-lq-approvals', '');
            line.setAttribute('aria-live', 'polite');
            card.appendChild(line);
        }
        var active = toCount(a.queued) + toCount(a.running);
        // failed_open: فشلت بآخر 24 ساعة وما انتجاهلت (لوحة «اعتمادات ما زبطت» بصفحة المراجعة)؛ خادم أقدم: failed_recent
        var failed = toCount(a.failed_open !== undefined ? a.failed_open : a.failed_recent);
        line.textContent = '';
        if (active) {
            line.appendChild(document.createTextNode(active === 1 ? 'اعتماد واحد عم يشتغل عالخادم' : active + ' اعتمادات عم تشتغل عالخادم'));
        }
        if (failed) {
            if (active) line.appendChild(document.createTextNode(' · '));
            // رابط لصفحة المراجعة: هناك الأسماء والسبب و«افتح المنتج» و«تجاهل»
            var link = document.createElement('a');
            link.className = 'lq-runcard__approvals-link';
            link.href = document.body.getAttribute('data-lq-review-url') || '/catalog';
            link.textContent = failed === 1 ? 'اعتماد ما زبط: شوفه' : failed + ' اعتمادات ما زبطت: شوفها';
            line.appendChild(link);
        }
        line.classList.toggle('has-failures', failed > 0);
        setHidden(line, !(active || failed));
    }

    // انتهت الجلسة (401 من RequireLogin أو 419 CSRF): لافتة وحدة ثابتة فوق الصفحة بدل ما يفشل كل شي بصمت، والسؤال
    // كل 5 ثواني بيوقف. صفحة المراجعة (review/ui.js showSessionBanner) بتعمل نفس اللافتة (#lqSessionExpired) وبتزيد
    // عليها المنتجات اللي ما انبعتت. الرابط للصفحة نفسها: RequireLogin بيحوّل للدخول وبيرجع لهون بعده
    var sessionExpired = false;
    function showSessionExpired() {
        sessionExpired = true;
        clearTimeout(timer);
        if (document.getElementById('lqSessionExpired')) return;
        var box = document.createElement('div');
        box.className = 'lq-alert lq-alert--danger lq-alert--banner lq-session-expired';
        box.id = 'lqSessionExpired';
        box.setAttribute('role', 'alert');
        var icon = svgIcon(TOAST_ICONS.danger, 20);
        icon.setAttribute('class', 'lq-alert__icon');
        var body = document.createElement('div');
        body.className = 'lq-alert__body';
        var title = document.createElement('div');
        title.className = 'lq-alert__title';
        title.textContent = 'انتهت الجلسة. سجّل دخول من جديد لتكمّل';
        var pending = document.createElement('div');
        pending.className = 'lq-session-expired__pending';
        pending.setAttribute('data-lq-session-pending', '');
        pending.hidden = true;
        body.appendChild(title);
        body.appendChild(pending);
        var link = document.createElement('a');
        link.className = 'lq-alert__action';
        link.href = window.location.pathname + window.location.search;
        link.textContent = 'سجّل دخول';
        box.appendChild(icon);
        box.appendChild(body);
        box.appendChild(link);
        var host = document.getElementById('lq-main') || document.body;
        host.insertBefore(box, host.firstChild);
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
        var view = describeRunStatus(n);
        renderRunCard(view);
        renderRunDot(view);
        renderReviewCount(n.reviewCount);
        maybeNotifyRunEnd(n);
        try {
            document.dispatchEvent(new CustomEvent('lq:run-status', { detail: n }));
        } catch (e) { /* very old browsers: no CustomEvent constructor */ }
    }

    // تبويب مخفي: السؤال بيوقف لحتى يرجع ظاهر (visibilitychange)، إلا لو «نبّهني لما يخلص» عم يستنى تشغيل ماشي
    function watchingRunEnd() {
        return !!(noticeState && RUN_ACTIVE[noticeState]) && noticeSupported() && window.Notification.permission === 'granted';
    }

    function pollMs() {
        if (document.hidden) return HIDDEN_POLL_MS;
        return $('[data-run-page]') || $('[data-home-page]') ? PAGE_POLL_MS : POLL_MS;
    }

    function poll() {
        if (inFlight || sessionExpired) return;
        if (document.hidden && !watchingRunEnd()) return;
        inFlight = true;
        fetch(statusUrl(), { headers: { Accept: 'application/json' }, cache: 'no-store', credentials: 'same-origin' })
            .then(function (res) {
                if (res.status === 401 || res.status === 419) showSessionExpired();
                if (!res.ok) throw new Error('HTTP ' + res.status);
                return res.json();
            })
            .then(function (data) {
                var n = normalizeRunStatus(data);
                // صفحة المراجعة بتقرا «اعتمادات ما زبطت» من جديد لما يتغيّر العدّاد
                n.approvals = isObject(data && data.approvals) ? data.approvals : null;
                publish(n);
                renderApprovals(data && data.approvals);
            })
            .catch(function () {
                if (lastStatus) return;
                var view = describeRunStatus({ state: 'unknown' });
                renderRunCard(view);
                renderRunDot(view);
            })
            .then(function () {
                inFlight = false;
                if (!sessionExpired) schedule(pollMs());
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
        // options.action: { label, onClick } → زر بالإشعار (متل «تراجع»)؛ الضغط عليه بيسكّر الإشعار
        var action = null;
        if (options.action && typeof options.action.onClick === 'function') {
            action = document.createElement('button');
            action.type = 'button';
            action.className = 'lq-toast__action';
            action.textContent = String(options.action.label || '');
            el.appendChild(action);
        }
        el.appendChild(close);
        region.appendChild(el);
        var closeTimer = null;
        function dismiss() {
            clearTimeout(closeTimer);
            if (el.parentNode) el.parentNode.removeChild(el);
        }
        close.addEventListener('click', dismiss);
        if (action) {
            action.addEventListener('click', function () {
                dismiss();
                options.action.onClick();
            });
        }
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
        sessionExpired: showSessionExpired,
        // صفحة المراجعة لقت طلب نجح بعد ما انتهت الجلسة (دخول من تبويب ثاني): الشريط الجانبي بيرجع يسأل
        sessionRestored: function () {
            if (!sessionExpired) return;
            sessionExpired = false;
            var box = document.getElementById('lqSessionExpired');
            if (box && box.parentNode) box.parentNode.removeChild(box);
            schedule(0);
        },
        lastRunStatus: function () { return lastStatus; },
        onRunStatus: function (fn) {
            document.addEventListener('lq:run-status', function (e) { fn(e.detail); });
            if (lastStatus) fn(lastStatus);
        },
        toast: toast,
        ask: ask,
        runNotice: runNotice,
        // «روح لـ…» (jump.js) من قائمة «حسابي»: القائمة بتتسكّر قبل ما تنفتح النافذة، والتركيز اللي كان جوّاها
        // بيرجع لزر «حسابي» (هيك لما تتسكّر «روح لـ…» بـ Esc بيرجع لمكان ظاهر)
        closeAccount: function () {
            if (accountSheet && !accountSheet.hasAttribute('hidden')) setAccountOpen(false, inAccount(document.activeElement));
        }
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

    // «حسابي» بشريط الموبايل: بيفتح ويسكّر القائمة الصغيرة (الاسم و«روح لـ…» و«خروج»)؛ Esc أو كبسة برّاها بتسكّرها.
    // هي aria-modal: Tab بيلف بين زرارها، وEsc بيرجّع التركيز لزر «حسابي»
    var accountToggle = $('[data-lq-account-toggle]');
    var accountSheet = $('[data-lq-account-sheet]');
    function inAccount(node) {
        return !!(node && node.closest && node.closest('[data-lq-account-sheet]'));
    }
    function accountButtons() {
        var all = accountSheet.querySelectorAll ? accountSheet.querySelectorAll('button') : [];
        var out = [];
        for (var i = 0; i < all.length; i++) if (!all[i].disabled && !all[i].hasAttribute('hidden')) out.push(all[i]);
        return out;
    }
    function setAccountOpen(open, refocus) {
        accountToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        setHidden(accountSheet, !open);
        if (open) {
            var logout = $('button', accountSheet);
            if (logout) logout.focus();
        } else if (refocus) {
            accountToggle.focus();
        }
    }
    if (accountToggle && accountSheet) {
        accountToggle.addEventListener('click', function () {
            setAccountOpen(accountSheet.hasAttribute('hidden'), false);
        });
        document.addEventListener('click', function (e) {
            if (accountSheet.hasAttribute('hidden') || !e.target || !e.target.closest) return;
            if (e.target.closest('[data-lq-account-sheet]') || e.target.closest('[data-lq-account-toggle]')) return;
            setAccountOpen(false, false);
        });
        document.addEventListener('keydown', function (e) {
            if (accountSheet.hasAttribute('hidden')) return;
            if (e.key === 'Escape') {
                setAccountOpen(false, true);
            } else if (e.key === 'Tab') {
                var buttons = accountButtons();
                if (!buttons.length) return;
                var first = buttons[0];
                var last = buttons[buttons.length - 1];
                var at = document.activeElement;
                if (!inAccount(at)) {
                    first.focus();
                } else if (e.shiftKey && at === first) {
                    last.focus();
                } else if (!e.shiftKey && at === last) {
                    first.focus();
                } else {
                    return;
                }
                e.preventDefault();
            }
        });
    }

    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) schedule(0);
    });

    if (typeof fetch === 'function' && ($('[data-lq-runcard]') || $('[data-lq-review-count]'))) {
        poll();
    }
})();
