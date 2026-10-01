/*
 * لقطة · shared helpers for the Home and Run pages (package p2-run).
 *
 * Pure functions (formatting, run phases, the last-run tiles) plus a fetch wrapper that never throws.
 * Loaded before home.js / run.js; exposes window.LaqtaRunCommon. Text is always written with textContent.
 */
(function (root) {
    'use strict';

    var ACTIVE_PHASES = ['starting', 'running', 'paused', 'stopping'];
    var MONTHS = ['كانون الثاني', 'شباط', 'آذار', 'نيسان', 'أيار', 'حزيران', 'تموز', 'آب', 'أيلول',
        'تشرين الأول', 'تشرين الثاني', 'كانون الأول'];
    var WEEKDAYS = ['الأحد', 'الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت'];

    function isActive(phase) {
        return ACTIVE_PHASES.indexOf(phase) !== -1;
    }

    // A run ended while the page was open: from an active phase to review, idle or error.
    function runJustFinished(previous, phase) {
        return isActive(previous) && !isActive(phase);
    }

    function num(v) {
        var n = typeof v === 'string' && v.trim() !== '' ? Number(v) : v;
        return typeof n === 'number' && isFinite(n) ? n : null;
    }

    // Same words as QueueStats::countText: "منتج واحد", "منتجين", "5 منتجات", "40 منتج".
    function countText(n, one, two, few) {
        one = one || 'منتج';
        two = two || 'منتجين';
        few = few || 'منتجات';
        if (n === 1) return one + ' واحد';
        if (n === 2) return two;
        return n + ' ' + (n >= 3 && n <= 10 ? few : one);
    }

    function minutesText(m) {
        if (m <= 1) return 'دقيقة';
        if (m === 2) return 'دقيقتين';
        if (m <= 10) return m + ' دقائق';
        return m + ' دقيقة';
    }

    // "أقل من دقيقة", "6 دقائق", "21 دقيقة", "ساعة و10 دقائق".
    function durationText(seconds) {
        seconds = num(seconds);
        if (seconds === null || seconds < 0) return '';
        if (seconds < 60) return 'أقل من دقيقة';
        var minutes = Math.round(seconds / 60);
        if (minutes < 60) return minutesText(minutes);
        var hours = Math.floor(minutes / 60);
        var rest = minutes % 60;
        var h = hours === 1 ? 'ساعة' : (hours === 2 ? 'ساعتين' : hours + ' ساعات');
        return rest ? h + ' و' + minutesText(rest) : h;
    }

    function usdText(v) {
        v = num(v);
        if (v === null) return '—';
        if (v > 0 && v < 0.005) return 'أقل من $0.01';
        return '$' + v.toFixed(2);
    }

    function pad(n) {
        return (n < 10 ? '0' : '') + n;
    }

    function clockText(epoch) {
        epoch = num(epoch);
        if (epoch === null) return '';
        var d = new Date(epoch * 1000);
        return pad(d.getHours()) + ':' + pad(d.getMinutes());
    }

    function sameDay(a, b) {
        return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    }

    // "اليوم 09:40", "مبارح 21:10", "28 أيلول 09:40" in the viewer's own time zone.
    function whenText(epoch, nowMs) {
        epoch = num(epoch);
        if (epoch === null) return '';
        var d = new Date(epoch * 1000);
        var now = new Date(typeof nowMs === 'number' ? nowMs : Date.now());
        var yesterday = new Date(now.getTime());
        yesterday.setDate(now.getDate() - 1);
        var day = sameDay(d, now) ? 'اليوم' : (sameDay(d, yesterday) ? 'مبارح' : d.getDate() + ' ' + MONTHS[d.getMonth()]);
        return day + ' ' + clockText(epoch);
    }

    // "الآن", "20 ث", "3 د", "2 س", then the date.
    function agoText(epoch, nowSec) {
        epoch = num(epoch);
        if (epoch === null) return '';
        var diff = Math.max(0, Math.round((typeof nowSec === 'number' ? nowSec : Date.now() / 1000) - epoch));
        if (diff < 15) return 'الآن';
        if (diff < 60) return diff + ' ث';
        if (diff < 3600) return Math.floor(diff / 60) + ' د';
        if (diff < 86400) return Math.floor(diff / 3600) + ' س';
        return whenText(epoch, (typeof nowSec === 'number' ? nowSec : Date.now() / 1000) * 1000);
    }

    // "الأربعاء، 30 أيلول"
    function dateLine(date) {
        return WEEKDAYS[date.getDay()] + '، ' + date.getDate() + ' ' + MONTHS[date.getMonth()];
    }

    function greeting(hour, name) {
        var text = hour >= 4 && hour < 12 ? 'صباح الخير' : 'مسا الخير';
        name = typeof name === 'string' ? name.trim() : '';
        return name ? text + ' يا ' + name : text;
    }

    // The server's alert joins its sentences with " | "; show them as plain sentences.
    function alertText(text) {
        return String(text || '').split('|').map(function (part) {
            part = part.trim();
            return part && !/[.!؟?…]$/.test(part) ? part + '.' : part;
        }).filter(Boolean).join(' ');
    }

    // Main-page row filter as main.parse_row_filter reads it: Arabic digits and commas become ASCII.
    function normalizeRows(text) {
        var map = { '،': ',', '–': '-', '—': '-' };
        return String(text || '').replace(/[٠-٩۰-۹،–—]/g, function (ch) {
            if (map[ch]) return map[ch];
            var code = ch.charCodeAt(0);
            return String(code >= 0x06F0 ? code - 0x06F0 : code - 0x0660);
        }).replace(/\s+/g, ' ').trim();
    }

    /*
     * The result tiles of a run summary (QueueStats::runSummary counts): the design's four, plus «معتمدة» when
     * the run published anything. «أعطال مؤقتة» joins failed rows and rows back in the queue after an outage.
     */
    function resultTiles(counts) {
        counts = counts || {};
        var c = function (k) { return num(counts[k]) || 0; };
        var tiles = [
            { key: 'proposed', label: 'مقترحة', value: c('proposed'), tone: 'success' },
            { key: 'none', label: 'بلا اقتراح', value: c('none'), tone: 'muted' },
            { key: 'not_found', label: 'ما انلقت', value: c('not_found'), tone: 'info' },
            { key: 'failed', label: 'أعطال مؤقتة', value: c('error') + c('requeued'), tone: 'danger' }
        ];
        if (c('approved') > 0) tiles.push({ key: 'approved', label: 'معتمدة', value: c('approved'), tone: 'teal' });
        return tiles;
    }

    // A run summary (QueueStats::runSummary) that ended with rows still waiting: it stopped before it finished
    // (a stop, an outage or a crash). Rows back in the queue after an outage count as reached.
    function runUnfinished(run) {
        if (!run || typeof run !== 'object') return false;
        var requeued = num((run.counts || {}).requeued) || 0;
        return (num(run.processed) || 0) + requeued < (num(run.total) || 0);
    }

    // One line under a finished run: "اليوم 09:40 · الصفوف 2–61 · 21 دقيقة · $0.19".
    function runMeta(run, nowMs) {
        if (!run) return '';
        var parts = [];
        if (num(run.started_at) !== null) parts.push(whenText(run.started_at, nowMs));
        if (run.rows_label) parts.push('الصفوف ' + run.rows_label);
        if (num(run.duration_s) !== null && num(run.processed) > 1) parts.push(durationText(run.duration_s));
        if (num(run.cost_usd) !== null) parts.push(usdText(run.cost_usd));
        return parts.join(' · ');
    }

    function csrfToken(doc) {
        var meta = doc && doc.querySelector ? doc.querySelector('meta[name="csrf-token"]') : null;
        return meta ? (meta.getAttribute('content') || '') : '';
    }

    // fetch that never throws: resolves {ok, status, data}. data is the parsed JSON body or null.
    function fetchJson(url, options) {
        options = options || {};
        var init = {
            method: options.method || 'GET',
            headers: { Accept: 'application/json' },
            credentials: 'same-origin',
            cache: 'no-store'
        };
        if (init.method !== 'GET') {
            init.headers['Content-Type'] = 'application/json';
            init.headers['X-CSRF-TOKEN'] = csrfToken(root.document);
            init.body = JSON.stringify(options.body || {});
        }
        if (typeof root.fetch !== 'function') {
            return Promise.resolve({ ok: false, status: 0, data: null });
        }
        return root.fetch(url, init).then(function (res) {
            return res.json().then(function (data) {
                return { ok: !!res.ok, status: res.status, data: data };
            }, function () {
                return { ok: false, status: res.status, data: null };
            });
        }, function () {
            return { ok: false, status: 0, data: null };
        });
    }

    function toast(message, variant) {
        if (root.Laqta && typeof root.Laqta.toast === 'function') {
            return root.Laqta.toast(message, { variant: variant || 'info' });
        }
        return null;
    }

    // DOM helpers: every hook is a data attribute, every text goes through textContent.
    function el(doc, tag, className, text) {
        var node = doc.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    function setHidden(node, hidden) {
        if (!node) return;
        if (hidden) node.setAttribute('hidden', '');
        else node.removeAttribute('hidden');
    }

    function setText(node, text) {
        if (node && node.textContent !== String(text)) node.textContent = String(text);
    }

    function clear(node) {
        while (node && node.firstChild) node.removeChild(node.firstChild);
    }

    root.LaqtaRunCommon = {
        ACTIVE_PHASES: ACTIVE_PHASES,
        MONTHS: MONTHS,
        WEEKDAYS: WEEKDAYS,
        isActive: isActive,
        runJustFinished: runJustFinished,
        num: num,
        countText: countText,
        durationText: durationText,
        usdText: usdText,
        clockText: clockText,
        whenText: whenText,
        agoText: agoText,
        dateLine: dateLine,
        greeting: greeting,
        alertText: alertText,
        normalizeRows: normalizeRows,
        resultTiles: resultTiles,
        runUnfinished: runUnfinished,
        runMeta: runMeta,
        fetchJson: fetchJson,
        toast: toast,
        el: el,
        setHidden: setHidden,
        setText: setText,
        clear: clear
    };
})(typeof window !== 'undefined' ? window : globalThis);
