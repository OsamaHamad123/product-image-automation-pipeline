/*
 * لقطة · «روح لـ…»: one box from any page to any page, card, setting, review list or product.
 *
 * Opens with Ctrl+K (⌘K), «/» when nothing is being typed, the sidebar's «روح لـ…» button or the phone's «حسابي» sheet.
 * The places come from App\Services\GotoIndex (the layout's #lqGotoIndex island, already without the owner's pages
 * for a reviewer). From what is typed it adds «افتح الصف N» (digits) and «دوّر بالمراجعة على …» (/catalog?q=…).
 * ↑↓ move, Enter goes, Esc closes; the focus goes back to where it was. While another open dialog (aria-modal, not
 * hidden) is up, no key is taken from it. Keys are read from e.code too, so an Arabic
 * keyboard layout works (Ctrl+K is KeyK, «/» is Slash).
 *
 * window.LaqtaGoto: { open, close, search(entries, text) } — search is pure (the node tests call it).
 */
(function (root) {
    'use strict';

    var MAX = 12;

    function norm(value) {
        var map = { 'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ة': 'ه', 'ى': 'ي' };
        return String(value || '').toLowerCase()
            .replace(/[٠-٩]/g, function (d) { return String(d.charCodeAt(0) - 0x0660); })
            .replace(/[ً-ْـ]/g, '')
            .replace(/[أإآةى]/g, function (ch) { return map[ch]; })
            .replace(/\s+/g, ' ').trim();
    }

    /* The entries for a text: each word of the text in the title, group or words; the title's own matches first. */
    function search(entries, text) {
        var q = norm(text);
        var out = [];
        var list = Array.isArray(entries) ? entries : [];
        if (!q) return list.slice(0, MAX).map(function (e) { return { title: e.title, group: e.group, href: e.href }; });
        var words = q.split(' ');
        list.forEach(function (e, i) {
            var title = norm(e.title);
            var hay = title + ' ' + norm(e.group) + ' ' + norm(e.words);
            if (!words.every(function (w) { return hay.indexOf(w) !== -1; })) return;
            var score = title.indexOf(q) === 0 ? 0 : title.indexOf(q) !== -1 ? 1 : words.every(function (w) { return title.indexOf(w) !== -1; }) ? 2 : 3;
            out.push({ score: score, i: i, entry: { title: e.title, group: e.group, href: e.href } });
        });
        out.sort(function (a, b) { return a.score - b.score || a.i - b.i; });
        var found = out.slice(0, MAX).map(function (r) { return r.entry; });
        // the product itself: a row number opens it; any text searches the review list (name, brand, barcode, SKU)
        var raw = String(text || '').trim().slice(0, 100);
        if (/^\d{1,6}$/.test(q) && parseInt(q, 10) > 0) {
            found.unshift({ title: 'افتح الصف ' + parseInt(q, 10) + ' بالمراجعة', group: 'منتج', href: '/catalog?row=' + parseInt(q, 10) });
        }
        found.push({ title: 'دوّر بالمراجعة على «' + raw + '»', group: 'منتج', href: '/catalog?q=' + encodeURIComponent(raw) });
        return found;
    }

    var doc = root.document;
    var entries = [];
    var box = null;
    var input = null;
    var list = null;
    var empty = null;
    var status = null;
    var dlg = null;
    var results = [];
    var selected = 0;
    var opener = null;

    function readEntries() {
        var island = doc.getElementById('lqGotoIndex');
        try {
            var data = island ? JSON.parse(island.textContent || '[]') : [];
            return Array.isArray(data) ? data : [];
        } catch (e) {
            return [];
        }
    }

    function el(tag, cls, text) {
        var node = doc.createElement(tag);
        if (cls) node.className = cls;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }

    function go(href) {
        close(false);
        var here = root.location.pathname + root.location.search;
        var hash = href.indexOf('#');
        // a card on this same page: only the hash changes (the page opens «تفاصيل متقدمة» on Health by itself)
        if (hash > 0 && href.slice(0, hash) === here) {
            if (root.location.hash === href.slice(hash)) {
                root.dispatchEvent(new Event('hashchange'));          // same hash again: still bring the card into view
            } else {
                root.location.hash = href.slice(hash);
            }
            return;
        }
        root.location.assign(href);
    }

    function countText(n) {
        return n === 1 ? 'نتيجة وحدة' : n === 2 ? 'نتيجتين' : n <= 10 ? n + ' نتائج' : n + ' نتيجة';
    }

    // The listbox holds options only: each group is a role="group" named after it (its heading is only seen), and
    // «ما في شي» sits outside it. The count is said (status) only when it changes, not on every key.
    function render() {
        results = search(entries, input.value);
        if (selected >= results.length) selected = Math.max(0, results.length - 1);
        while (list.firstChild) list.removeChild(list.firstChild);
        var said = results.length ? countText(results.length) : 'ما في شي بهالاسم.';
        if (status.textContent !== said) status.textContent = said;
        empty.hidden = !!results.length;
        list.hidden = !results.length;
        input.setAttribute('aria-expanded', results.length ? 'true' : 'false');
        if (!results.length) {
            input.removeAttribute('aria-activedescendant');
            return;
        }
        var options = null;
        var lastGroup = null;
        results.forEach(function (r, i) {
            if (!options || r.group !== lastGroup) {
                var section = el('li', 'lq-goto__section');
                section.setAttribute('role', 'group');
                section.setAttribute('aria-label', r.group);
                var head = el('span', 'lq-goto__group', r.group);
                head.setAttribute('aria-hidden', 'true');
                options = el('ul', 'lq-goto__options');
                options.setAttribute('role', 'none');
                section.appendChild(head);
                section.appendChild(options);
                list.appendChild(section);
                lastGroup = r.group;
            }
            var item = el('li', 'lq-goto__item');
            item.id = 'lqGotoItem' + i;
            item.setAttribute('role', 'option');
            item.setAttribute('aria-selected', i === selected ? 'true' : 'false');
            item.appendChild(el('span', 'lq-goto__title', r.title));
            item.appendChild(el('span', 'lq-goto__where', r.group));
            item.addEventListener('mousedown', function (e) { e.preventDefault(); });
            item.addEventListener('click', function () { go(r.href); });
            item.addEventListener('mousemove', function () {
                if (selected !== i) {
                    selected = i;
                    mark();
                }
            });
            options.appendChild(item);
        });
        mark();
    }

    function mark() {
        var items = list.querySelectorAll('.lq-goto__item');
        for (var i = 0; i < items.length; i++) items[i].setAttribute('aria-selected', i === selected ? 'true' : 'false');
        var on = items[selected];
        if (on) {
            input.setAttribute('aria-activedescendant', on.id);
            if (on.scrollIntoView) on.scrollIntoView({ block: 'nearest' });
        }
    }

    // Another dialog that is open (a question over the box, the zoom, the phone's «حسابي»): its keys stay its own.
    // A hidden one (the «حسابي» sheet while it is closed) does not count.
    function openModal() {
        var found = typeof doc.querySelectorAll === 'function' ? doc.querySelectorAll('[aria-modal="true"]') : [];
        for (var i = 0; i < found.length; i++) {
            if (found[i] === dlg || (found[i].closest && found[i].closest('[hidden]'))) continue;
            return found[i];
        }
        return null;
    }

    function onKey(e) {
        if (!box || openModal()) return;
        if (e.key === 'Escape') {
            close(true);
        } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            if (results.length) selected = (selected + (e.key === 'ArrowDown' ? 1 : results.length - 1)) % results.length;
            mark();
        } else if (e.key === 'Enter') {
            if (!e.repeat && results[selected]) go(results[selected].href);
        } else if (e.key === 'Tab') {
            input.focus();
        } else {
            e.stopPropagation();          // a letter typed here is not a shortcut of the page under it
            return;
        }
        e.preventDefault();
        e.stopPropagation();
    }

    function open() {
        if (box) {
            input.focus();
            return;
        }
        if (root.Laqta && typeof root.Laqta.closeAccount === 'function') root.Laqta.closeAccount();
        entries = readEntries();
        opener = doc.activeElement;
        selected = 0;
        box = el('div', 'lq-goto-backdrop');
        dlg = el('div', 'lq-goto');
        dlg.setAttribute('role', 'dialog');
        dlg.setAttribute('aria-modal', 'true');
        dlg.setAttribute('aria-label', 'روح لـ…');
        var field = el('label', 'lq-goto__field');
        input = el('input', 'lq-goto__input');
        input.type = 'search';
        input.id = 'lqGotoInput';
        input.setAttribute('placeholder', 'صفحة، كرت، إعداد، رقم صف، أو اسم منتج');
        input.setAttribute('autocomplete', 'off');
        input.setAttribute('spellcheck', 'false');
        input.setAttribute('role', 'combobox');
        input.setAttribute('aria-expanded', 'true');
        input.setAttribute('aria-controls', 'lqGotoList');
        input.setAttribute('aria-autocomplete', 'list');
        var label = el('span', 'lq-sr-only', 'روح لـ');
        field.appendChild(label);
        field.appendChild(input);
        list = el('ul', 'lq-goto__list');
        list.id = 'lqGotoList';
        list.setAttribute('role', 'listbox');
        list.setAttribute('aria-label', 'النتائج');
        empty = el('p', 'lq-goto__empty', 'ما في شي بهالاسم.');
        empty.hidden = true;
        status = el('div', 'lq-sr-only');
        status.setAttribute('role', 'status');
        var hint = el('div', 'lq-goto__hint');
        [['↑ ↓', 'تنقّل'], ['Enter', 'روح'], ['Esc', 'سكّر']].forEach(function (h) {
            var s = el('span');
            s.appendChild(el('kbd', null, h[0]));
            s.appendChild(doc.createTextNode(' ' + h[1]));
            hint.appendChild(s);
        });
        dlg.appendChild(field);
        dlg.appendChild(list);
        dlg.appendChild(empty);
        dlg.appendChild(status);
        dlg.appendChild(hint);
        box.appendChild(dlg);
        box.addEventListener('mousedown', function (e) { if (e.target === box) close(true); });
        input.addEventListener('input', function () {
            selected = 0;
            render();
        });
        doc.addEventListener('keydown', onKey, true);
        doc.body.appendChild(box);
        render();
        input.focus();
    }

    function close(refocus) {
        if (!box) return;
        doc.removeEventListener('keydown', onKey, true);
        if (box.parentNode) box.parentNode.removeChild(box);
        box = null;
        dlg = null;
        // back where it was, unless that is gone or hidden meanwhile (the «حسابي» sheet hands its focus to its toggle)
        if (refocus && opener && typeof opener.focus === 'function' && doc.contains(opener)
            && !(opener.closest && opener.closest('[hidden]'))) opener.focus();
    }

    function typing(target) {
        var tag = target && target.tagName ? String(target.tagName).toLowerCase() : '';
        return tag === 'input' || tag === 'textarea' || tag === 'select' || !!(target && target.isContentEditable);
    }

    root.LaqtaGoto = { open: open, close: close, search: search };

    if (!doc || typeof doc.addEventListener !== 'function') return;
    doc.addEventListener('keydown', function (e) {
        // a question or the zoom is open: its own keys (Enter = «أكيد») stay its own
        if (box || e.defaultPrevented || e.repeat || openModal()) return;
        var k = e.code === 'KeyK' || String(e.key).toLowerCase() === 'k';
        if (k && (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey) {
            e.preventDefault();
            e.stopPropagation();
            open();
            return;
        }
        var slash = e.code === 'Slash' || e.key === '/';
        // «/»: نفس الشي، إلا والمستخدم عم يكتب
        if (slash && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey && !typing(e.target)) {
            e.preventDefault();
            e.stopPropagation();
            open();
        }
    }, true);
    doc.addEventListener('click', function (e) {
        var btn = e.target && e.target.closest ? e.target.closest('[data-lq-goto-open]') : null;
        if (!btn) return;
        e.preventDefault();
        open();
    });
})(typeof window !== 'undefined' ? window : globalThis);
