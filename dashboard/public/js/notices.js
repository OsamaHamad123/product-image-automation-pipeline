/*
 * لقطة · «التنبيهات»: the bell in the sidebar (next to «روح لـ…») and in «حسابي» on the phone, and its panel
 * (resources/views/layouts/laqta.blade.php loads it after js/layout.js). A panel, not a page.
 *
 * - What goes in (newest first, at most 30, in localStorage «laqta.notices», this browser only):
 *   the run started / finished / stopped before the end / stopped on a fault / stuck, new products ready for review
 *   and server approvals that failed: from the layout's one /api/batch-status poll (Laqta.onRunStatus), compared with
 *   the last state this browser saw (kept with the list, so two tabs or a page change do not repeat an event);
 *   and the «شو بدو منك» items of /api/system/attention (<body data-lq-attention-url>): asked at most every 5 minutes
 *   while the tab is in view, never while it is hidden; an event only when an item's key appears.
 * - The badge counts the events newer than the last time the panel was opened; opening it marks them all seen.
 *   «علّم الكل مقروء» and «امسح». Toasts stay as they are (Laqta.toast).
 * - A reviewer never gets a link to the owner's places (the run on «اليوم», /system admin tabs): the event stays,
 *   without its link (body[data-lq-role="reviewer"]).
 * - The panel is a modal dialog: Tab stays inside, Esc (or «سكّر») closes it and the focus goes back to the bell.
 * window.Laqta.notices: list, unseen, open, close, markRead, clear, checkAttention.
 */
(function () {
    'use strict';

    var KEY = 'laqta.notices';
    var MAX = 30;
    var ATTENTION_MS = 5 * 60 * 1000;
    var ACTIVE = { running: 1, paused: 1, stopping: 1 };
    var TONES = { success: 1, warning: 1, danger: 1, info: 1 };
    var ICONS = {
        success: 'M5 12.5 10 17.5 19 7',
        warning: 'M12 4 2.5 20h19zM12 10v4.5M12 17.5v.5',
        danger: 'M12 4 2.5 20h19zM12 10v4.5M12 17.5v.5',
        info: 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18zM12 11v5M12 7.5v.5'
    };

    var panel = document.querySelector('[data-lq-notices]');
    if (!panel) return;
    var list = panel.querySelector('[data-lq-notices-list]');
    var empty = panel.querySelector('[data-lq-notices-empty]');
    var readBtn = panel.querySelector('[data-lq-notices-read]');
    var clearBtn = panel.querySelector('[data-lq-notices-clear]');
    var closeBtn = panel.querySelector('[data-lq-notices-close]');

    function setHidden(el, hidden) {
        if (!el) return;
        if (hidden) el.setAttribute('hidden', '');
        else el.removeAttribute('hidden');
    }

    function count(v) {
        if (typeof v === 'number') return isFinite(v) && v >= 0 ? Math.floor(v) : null;
        if (typeof v === 'string' && /^\s*\d+\s*$/.test(v)) return parseInt(v, 10);
        return null;
    }

    function now() {
        return Date.now();
    }

    // ---- the list, kept per browser (blocked storage: this page only) ----

    function fresh() {
        return { events: [], seen: 0, read: 0, base: {}, keys: null, attAt: 0, n: 0 };
    }

    var memory = fresh();

    function load() {
        try {
            var raw = window.localStorage.getItem(KEY);
            if (raw === null) return memory;
            var s = JSON.parse(raw);
            if (!s || typeof s !== 'object' || !Array.isArray(s.events)) return memory;
            var out = fresh();
            out.events = s.events.filter(function (e) {
                return e && typeof e.text === 'string' && typeof e.at === 'number';
            }).slice(0, MAX);
            out.seen = typeof s.seen === 'number' ? s.seen : 0;
            out.read = typeof s.read === 'number' ? s.read : 0;
            out.base = s.base && typeof s.base === 'object' ? s.base : {};
            out.keys = Array.isArray(s.keys) ? s.keys : null;
            out.attAt = typeof s.attAt === 'number' ? s.attAt : 0;
            out.n = typeof s.n === 'number' ? s.n : 0;
            memory = out;
        } catch (e) { /* blocked or broken storage: what this page already has */ }
        return memory;
    }

    function save(s) {
        memory = s;
        try {
            window.localStorage.setItem(KEY, JSON.stringify(s));
        } catch (e) { /* blocked or full storage: this page only */ }
        render();
    }

    // one event: {id, kind, tone, text, href, admin, at, n}. «للمراجعة» and «ما زبط» add up into the newest unseen
    // one of their kind, so a run that fills the queue is one line, not one every 5 seconds
    function push(s, ev) {
        ev.at = now();
        ev.tone = TONES[ev.tone] ? ev.tone : 'info';
        var top = s.events[0];
        if (ev.merge && top && top.kind === ev.kind && top.at > s.seen) {
            top.n = (top.n || 0) + ev.n;
            top.text = ev.merge(top.n);
            top.at = ev.at;
            return;
        }
        s.n = (s.n || 0) + 1;
        s.events.unshift({ id: ev.at + '-' + s.n, kind: ev.kind, tone: ev.tone, text: ev.merge ? ev.merge(ev.n) : ev.text,
                           href: ev.href || '', admin: !!ev.admin, at: ev.at, n: ev.n || 0 });
        if (s.events.length > MAX) s.events.length = MAX;
    }

    function unseen(s) {
        var c = 0;
        for (var i = 0; i < s.events.length; i++) if (s.events[i].at > s.seen) c++;
        return c;
    }

    // ---- where to act ----

    function attr(name, fallback) {
        return (document.body && document.body.getAttribute(name)) || fallback;
    }
    function isReviewer() {
        return attr('data-lq-role', '') === 'reviewer';
    }
    function reviewUrl() {
        return attr('data-lq-review-url', '/catalog');
    }
    function runUrl() {
        return attr('data-lq-home-url', '/').replace(/\/?$/, '/') + '#run-current';
    }
    // the owner's places (SystemController::adminHref): the run panel on «اليوم», the old run page, /system's own tabs
    function ownerOnly(ev) {
        if (ev.admin) return true;
        return /(^|\/)#run-|\/batch-automation/.test(String(ev.href || ''));
    }

    // ---- from the layout's /api/batch-status poll ----

    function reviewText(n) {
        if (n === 1) return 'منتج جديد جاهز للمراجعة';
        if (n === 2) return 'منتجين جداد جاهزين للمراجعة';
        return n + (n <= 10 ? ' منتجات جديدة جاهزة' : ' منتج جديد جاهز') + ' للمراجعة';
    }
    function products(n) {
        if (n === 1) return 'منتج واحد';
        if (n === 2) return 'منتجين';
        return n + (n <= 10 ? ' منتجات' : ' منتج');
    }
    function failedText(n) {
        return n === 1 ? 'اعتماد ما زبط عالخادم: شوفه وأعده' : n + ' اعتمادات ما زبطت عالخادم: شوفها وأعدها';
    }

    function runEvent(was, n) {
        var cur = n.state;
        if (!was || was === cur) return null;
        var review = n.reviewCount > 0 ? n.reviewCount : 0;
        if (cur === 'running' && (was === 'idle' || was === 'error')) {
            return { kind: 'run-start', tone: 'info', admin: true, href: runUrl(),
                     text: 'بلّش تشغيل جديد' + (n.total > 0 ? ' (' + products(n.total) + ')' : '') };
        }
        if (cur === 'error') {
            return { kind: 'run-error', tone: 'danger', admin: true, href: runUrl(),
                     text: 'وقف التشغيل بعطل' + (n.notice ? ': ' + n.notice : '.') };
        }
        if (cur === 'stuck') {
            return { kind: 'run-stuck', tone: 'warning', admin: true, href: runUrl(),
                     text: 'التشغيل عالق' + (n.stuck ? ': ' + n.stuck : '.') };
        }
        if (cur === 'idle' && (ACTIVE[was] || was === 'stuck')) {
            var unfinished = was === 'stopping' || (n.total > 0 && n.done !== null && n.done < n.total);
            if (unfinished) {
                return { kind: 'run-stop', tone: 'warning', admin: true, href: runUrl(),
                         text: 'وقف التشغيل قبل ما يخلص' + (n.total > 0 ? ' (' + (n.done || 0) + ' من ' + n.total + ')' : '.') };
            }
            return review
                ? { kind: 'run-done', tone: 'success', href: reviewUrl(), text: 'خلص التشغيل: ' + review + ' بانتظار مراجعتك' }
                : { kind: 'run-done', tone: 'success', admin: true, href: runUrl(), text: 'خلص التشغيل. ما في شي جديد للمراجعة' };
        }
        return null;
    }

    function onStatus(n) {
        if (!n || !n.state || n.state === 'unknown' || n.state === 'loading') return;
        var s = load();
        var base = s.base;
        var changed = false;
        var ev = runEvent(base.run, n);
        if (ev) push(s, ev);
        if (base.run !== n.state) {
            base.run = n.state;
            changed = true;
        }
        var review = count(n.reviewCount);
        if (review !== null) {
            if (typeof base.review === 'number' && review > base.review) {
                push(s, { kind: 'review', tone: 'info', href: reviewUrl(), n: review - base.review, merge: reviewText });
            }
            if (base.review !== review) {
                base.review = review;
                changed = true;
            }
        }
        var a = n.approvals;
        var failed = a && typeof a === 'object' ? count(a.failed_open !== undefined ? a.failed_open : a.failed_recent) : null;
        if (failed !== null) {
            if (typeof base.failed === 'number' && failed > base.failed) {
                push(s, { kind: 'approval', tone: 'danger', href: reviewUrl(), n: failed - base.failed, merge: failedText });
            }
            if (base.failed !== failed) {
                base.failed = failed;
                changed = true;
            }
        }
        if (changed || ev) save(s);
    }

    // ---- «شو بدو منك» (/api/system/attention): every 5 minutes at most, only while the tab is in view ----

    var attTimer = null;
    var attBusy = false;

    function attentionUrl() {
        return attr('data-lq-attention-url', '/api/system/attention');
    }

    function attentionLater(ms) {
        clearTimeout(attTimer);
        attTimer = setTimeout(checkAttention, ms);
    }

    function onAttention(data) {
        if (!data || typeof data !== 'object' || !Array.isArray(data.items)) return;
        var s = load();
        var known = {};
        var had = s.keys || [];
        for (var i = 0; i < had.length; i++) known[had[i]] = true;
        var keys = [];
        for (var j = data.items.length - 1; j >= 0; j--) {
            var item = data.items[j];
            if (!item || typeof item.key !== 'string' || !item.key) continue;
            keys.unshift(item.key);
            if (known[item.key]) continue;
            var action = item.action && typeof item.action === 'object' ? item.action : {};
            push(s, { kind: 'attention', tone: item.tone === 'danger' ? 'danger' : 'warning',
                      text: String(item.title || ''), href: String(action.href || '') || '/system', admin: !!action.admin });
        }
        // the search side not read yet (pending): what is missing may only be unread, so it stays known
        if (data.pending) {
            for (var k = 0; k < had.length; k++) if (keys.indexOf(had[k]) < 0) keys.push(had[k]);
        }
        s.keys = keys;
        save(s);
    }

    function checkAttention() {
        if (document.hidden || attBusy || typeof fetch !== 'function') return;
        var s = load();
        var wait = s.attAt + ATTENTION_MS - now();
        if (wait > 0) {
            attentionLater(wait);
            return;
        }
        s.attAt = now();
        save(s);
        attBusy = true;
        fetch(attentionUrl(), { headers: { Accept: 'application/json' }, cache: 'no-store', credentials: 'same-origin' })
            .then(function (res) {
                if (!res.ok) throw new Error('HTTP ' + res.status);
                return res.json();
            })
            .then(onAttention)
            .catch(function () { /* the next look in 5 minutes */ })
            .then(function () {
                attBusy = false;
                attentionLater(ATTENTION_MS);
            });
    }

    // ---- the panel ----

    function ago(at) {
        var sec = Math.max(0, Math.round((now() - at) / 1000));
        if (sec < 60) return 'هلق';
        var min = Math.floor(sec / 60);
        if (min === 1) return 'قبل دقيقة';
        if (min === 2) return 'قبل دقيقتين';
        if (min < 60) return 'قبل ' + min + (min <= 10 ? ' دقايق' : ' دقيقة');
        var h = Math.floor(min / 60);
        if (h === 1) return 'قبل ساعة';
        if (h === 2) return 'قبل ساعتين';
        if (h < 24) return 'قبل ' + h + (h <= 10 ? ' ساعات' : ' ساعة');
        var d = Math.floor(h / 24);
        if (d === 1) return 'مبارح';
        if (d === 2) return 'قبل يومين';
        return 'قبل ' + d + (d <= 10 ? ' أيام' : ' يوم');
    }

    function icon(tone) {
        var ns = 'http://www.w3.org/2000/svg';
        var svg = document.createElementNS(ns, 'svg');
        svg.setAttribute('class', 'lq-notices__icon');
        svg.setAttribute('width', '18');
        svg.setAttribute('height', '18');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('fill', 'none');
        svg.setAttribute('stroke', 'currentColor');
        svg.setAttribute('stroke-width', '2');
        svg.setAttribute('stroke-linecap', 'round');
        svg.setAttribute('stroke-linejoin', 'round');
        svg.setAttribute('aria-hidden', 'true');
        svg.setAttribute('focusable', 'false');
        var path = document.createElementNS(ns, 'path');
        path.setAttribute('d', ICONS[tone] || ICONS.info);
        svg.appendChild(path);
        return svg;
    }

    function renderList(s) {
        if (!list) return;
        while (list.firstChild) list.removeChild(list.firstChild);
        var reviewer = isReviewer();
        for (var i = 0; i < s.events.length; i++) {
            var ev = s.events[i];
            var li = document.createElement('li');
            li.className = 'lq-notices__item lq-notices__item--' + (TONES[ev.tone] ? ev.tone : 'info') + (ev.at > s.read ? ' is-new' : '');
            li.appendChild(icon(ev.tone));
            var body = document.createElement('div');
            body.className = 'lq-notices__body';
            var link = ev.href && !(reviewer && ownerOnly(ev));
            var text = document.createElement(link ? 'a' : 'span');
            text.className = 'lq-notices__text';
            if (link) text.setAttribute('href', ev.href);
            text.textContent = ev.text;
            var when = document.createElement('span');
            when.className = 'lq-notices__when';
            when.textContent = ago(ev.at);
            body.appendChild(text);
            body.appendChild(when);
            // «جديد» بيبيّن باللون بس (is-new): قارئ الشاشة بيسمعه كلمة بعد الوقت
            if (ev.at > s.read) {
                var fresh = document.createElement('span');
                fresh.className = 'lq-sr-only';
                fresh.textContent = '، جديد';
                body.appendChild(fresh);
            }
            li.appendChild(body);
            list.appendChild(li);
        }
        setHidden(empty, s.events.length > 0);
        setHidden(readBtn, !s.events.length);
        setHidden(clearBtn, !s.events.length);
    }

    // قارئ الشاشة: تنبيه جديد بينقال مرة وحدة لما يزيد العدد (مش مع كل سؤال، ومش لما «للمراجعة» بس بيكبر رقمه)،
    // ومش لما تبويب ثاني زاده (storage)
    var said = null;
    var lastUnseen = null;
    function announce(s, c, quiet) {
        var before = lastUnseen;
        lastUnseen = c;
        if (quiet || before === null || c <= before || !s.events.length) return;
        if (!said && document.body && typeof document.createElement === 'function') {
            said = document.createElement('div');
            said.className = 'lq-sr-only';
            said.setAttribute('role', 'status');
            said.setAttribute('data-lq-notices-say', '');
            document.body.appendChild(said);
        }
        if (said) said.textContent = (c === 1 ? 'تنبيه جديد: ' : c + ' تنبيهات جديدة، آخرها: ') + s.events[0].text;
    }

    function render(quiet) {
        var s = memory;
        var c = unseen(s);
        announce(s, c, quiet === true);
        var badges = document.querySelectorAll('[data-lq-notices-count]');
        for (var i = 0; i < badges.length; i++) {
            badges[i].textContent = c > 0 ? String(c) : '';
            setHidden(badges[i], !(c > 0));
        }
        var openers = document.querySelectorAll('[data-lq-notices-open]');
        for (var j = 0; j < openers.length; j++) {
            openers[j].setAttribute('aria-label', c > 0 ? 'التنبيهات: ' + c + ' جديد' : 'التنبيهات');
        }
        if (isOpen()) renderList(s);
    }

    var opener = null;
    var openerBtn = null;

    function isOpen() {
        return !panel.hasAttribute('hidden');
    }

    function focusables() {
        var all = panel.querySelectorAll ? panel.querySelectorAll('button, a[href]') : [];
        var out = [];
        for (var i = 0; i < all.length; i++) {
            if (!all[i].disabled && !(all[i].closest && all[i].closest('[hidden]'))) out.push(all[i]);
        }
        return out;
    }

    function setExpanded(open) {
        var openers = document.querySelectorAll('[data-lq-notices-open]');
        for (var i = 0; i < openers.length; i++) openers[i].setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    function open(from) {
        // from «حسابي»: the sheet closes first, and the focus that was in it goes to «حسابي» (Laqta.closeAccount)
        if (window.Laqta && typeof window.Laqta.closeAccount === 'function') window.Laqta.closeAccount();
        opener = document.activeElement;
        openerBtn = from || null;
        var s = load();
        setHidden(panel, false);
        setExpanded(true);
        if (window.Laqta && typeof window.Laqta.syncModals === 'function') window.Laqta.syncModals();
        renderList(s);                          // what is new is marked for as long as the panel stays open
        s.seen = now();
        save(s);
        if (closeBtn) closeBtn.focus();
    }

    function close(refocus) {
        if (!isOpen()) return;
        setHidden(panel, true);
        setExpanded(false);
        // الصفحة ورا اللوحة كانت inert (layout.js syncModals): بترجع قبل ما يرجع التركيز للجرس
        if (window.Laqta && typeof window.Laqta.syncModals === 'function') window.Laqta.syncModals();
        if (!refocus) return;
        var back = opener && typeof opener.focus === 'function' && document.contains(opener)
            && !(opener.closest && opener.closest('[hidden]')) ? opener : null;
        if (!back && openerBtn && typeof openerBtn.focus === 'function') back = openerBtn;
        if (back) back.focus();
    }

    function markRead() {
        var s = load();
        s.seen = s.read = now();
        save(s);
    }

    function clear() {
        var s = load();
        s.events = [];
        s.seen = s.read = now();
        save(s);
        if (isOpen() && closeBtn) closeBtn.focus();
    }

    document.addEventListener('click', function (e) {
        var t = e.target;
        if (!t || !t.closest) return;
        var btn = t.closest('[data-lq-notices-open]');
        if (btn) {
            if (isOpen()) close(true);
            else open(btn);
            return;
        }
        if (!isOpen()) return;
        if (t.closest('[data-lq-notices-close]')) close(true);
        else if (t.closest('[data-lq-notices-read]')) markRead();
        else if (t.closest('[data-lq-notices-clear]')) clear();
        else if (t.closest('.lq-notices__text')) close(false);          // a link to the place to act
        else if (!t.closest('[data-lq-notices]')) close(false);
    });

    document.addEventListener('keydown', function (e) {
        if (!isOpen()) return;
        if (e.key === 'Escape') {
            e.preventDefault();
            close(true);
        } else if (e.key === 'Tab') {
            var items = focusables();
            if (!items.length) return;
            var first = items[0];
            var last = items[items.length - 1];
            var at = document.activeElement;
            var inside = !!(at && at.closest && at.closest('[data-lq-notices]'));
            if (!inside) first.focus();
            else if (e.shiftKey && at === first) last.focus();
            else if (!e.shiftKey && at === last) first.focus();
            else return;
            e.preventDefault();
        }
    });

    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) checkAttention();
    });

    // another tab added or read events
    if (window.addEventListener) window.addEventListener('storage', function (e) {
        if (e.key !== KEY) return;
        load();
        render(true);
    });

    if (window.Laqta && typeof window.Laqta.onRunStatus === 'function') window.Laqta.onRunStatus(onStatus);
    if (window.Laqta) {
        window.Laqta.notices = {
            list: function () { return load().events.slice(); },
            unseen: function () { return unseen(load()); },
            open: function () { open(null); },
            close: function () { close(true); },
            markRead: markRead,
            clear: clear,
            checkAttention: checkAttention
        };
    }

    load();
    render();
    checkAttention();
})();
