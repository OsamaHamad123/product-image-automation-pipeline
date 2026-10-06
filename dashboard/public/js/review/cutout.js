/*
 * لقطة · «فحص القص» (resources/views/dashboard/cutout_check.blade.php, RecutController).
 *
 * The published pictures, newest first, each on the app's dark and light themes and on a checkerboard side by side
 * (theme_preview.js apply), with the cut-check flags; a filter per flag. Per picture «أعد القص بـPhotoRoom» (one paid
 * call) or «جرّب القص المحلي» (rembg / BiRefNet, free, only when installed) makes a new cut that WAITS: the card shows
 * before / after on the three backgrounds, then «اعتمد الجديد» publishes it as a new version (the outbox, a versioned
 * link, the cells that still hold the old link) or «خلّي القديم» throws it away. Nothing is ever replaced without the
 * owner's click; «آخر التبديلات» lists every replacement with «رجّع القديم».
 *
 * Data: GET urls.gallery (?flag, ?page, ?measure: the server checks up to 24 pictures it never looked at per call),
 * POST urls.try {id, method: photoroom | local}, urls.apply {token}, urls.discard {token}, urls.undo {log_id}.
 * Web data only ever goes through textContent and attributes (ui.js el / img), never HTML. The pure helpers are
 * exported (R.cutout) for the node tests.
 */
(function (root) {
    'use strict';

    const R = root.LaqtaReview = root.LaqtaReview || {};

    // علامات المعرض (recut.GALLERY_FLAGS) بكلام المالك، بنفس الترتيب
    const FLAGS = [
        ['white', 'خلفية بيضا'],
        ['dark_halo', 'حواف فاتحة على الغامق'],
        ['photoroom_unsure', 'PhotoRoom مش متأكد'],
        ['glass', 'زجاج أو عبوة شفافة'],
        ['dark_rim', 'حواف غامقة على الفاتح'],
        ['low_res', 'دقة قليلة']
    ];
    const FLAG_TEXT = Object.fromEntries(FLAGS);
    // علامات القص الجديد (بوابة القص بـ image_processor و cutout_finish)
    const CUT_FLAG_TEXT = Object.assign({}, FLAG_TEXT, {
        upscaled: 'المنتج انكبّر من صورة صغيرة',
        too_small_on_canvas: 'المنتج صغير على اللوحة',
        second_object: 'في شي تاني جنب المنتج',
        alpha_haze: 'ضباب حول المنتج',
        kept_shadow: 'ضل ظل مع المنتج',
        edge_clipped: 'المنتج مقصوص من طرف',
        opaque_fill: 'الخلفية ما انشالت',
        opaque_backdrop: 'ضلت خلفية حول المنتج'
    });
    const THEMES = [['dark', 'غامق'], ['light', 'فاتح'], ['checker', 'مربعات']];
    const PROVIDERS = { photoroom: 'PhotoRoom', rembg: 'القص المحلي (BiRefNet)', remove_bg_api: 'remove.bg', grabcut: 'GrabCut' };
    const SOURCES = { candidate: 'الصورة الأصلية المحفوظة', original: 'رابط الصورة الأصلي', master: 'الصورة المنشورة نفسها' };
    const ORIGINS = { gallery: 'من «فحص القص»', reprocess: 'من «صور قديمة بخلفية بيضا»' };

    function flagText(flag) {
        return CUT_FLAG_TEXT[flag] || 'ملاحظة بالقص';
    }

    function count(v) {
        const n = Number(v);
        return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
    }

    /* «صورة وحدة»، «صورتين»، «5 صور»، «40 صورة» */
    function pictures(n) {
        n = count(n);
        if (n === 1) return 'صورة وحدة';
        if (n === 2) return 'صورتين';
        return n + ' ' + (n >= 3 && n <= 10 ? 'صور' : 'صورة');
    }

    function rowsText(rows) {
        const n = (rows || []).length;
        if (n === 1) return 'الصف ' + rows[0];
        return 'الصفوف ' + rows.join('، ');
    }

    /* The line under the filters: how many pictures, how many are still unchecked. */
    function statusText(data) {
        if (!data) return '';
        const all = count(data.counts && data.counts.all);
        if (all === 0) return 'ما في صور منشورة لسا.';
        const parts = [];
        if (data.flag) parts.push(pictures(data.total) + ' فيها «' + (FLAG_TEXT[data.flag] || data.flag) + '» من أصل ' + pictures(all) + '.');
        else parts.push(pictures(all) + ' منشورة، الأحدث أول.');
        if (count(data.unchecked) > 0) parts.push('لسا ' + pictures(data.unchecked) + ' ما انفحصت (منفحص 24 بكل مرة).');
        return parts.join(' ');
    }

    /* What happened after «اعتمد الجديد», from RecutController::apply's answer. */
    function applyText(data) {
        const sheet = data && data.sheet || {};
        const rows = (data && data.rows) || [];
        if (!rows.length) return 'انتشرت النسخة الجديدة. ما في خلية بالشيت لسا فيها الرابط القديم، فما انكتب شي بالشيت.';
        const parts = ['انتشرت النسخة الجديدة.'];
        if (count(sheet.written)) parts.push('انكتبت بـ' + rowsText(rows) + '.');
        if (count(sheet.pending)) parts.push('رح تنكتب بالشيت بعد شوي.');
        if (count(sheet.conflict)) parts.push('في صف ما انكتب لأنك غيّرت صورته بإيدك.');
        if (data.busy && data.busy.length) parts.push('صف عنده صورة أحدث بالطريق ما انلمس.');
        return parts.join(' ');
    }

    /* The note under a new cut: who made it, from which source, its flags and what it cost. */
    function tryText(res) {
        const parts = ['قص ' + (PROVIDERS[res.provider] || res.provider || 'جديد') + ' من ' + (SOURCES[res.source] || 'المصدر') + '.'];
        if (res.flags && res.flags.length) parts.push('ملاحظات: ' + res.flags.map(flagText).join('، ') + '.');
        else parts.push('بلا ملاحظات.');
        if (count(res.paid_calls)) parts.push('كلّف ' + (count(res.paid_calls) === 1 ? 'طلب عزل واحد' : count(res.paid_calls) + ' طلبات عزل') + '.');
        if (!res.can_apply) parts.push('ما منقدر ننشره: الخلفية ما انشالت منيح.');
        return parts.join(' ');
    }

    function errorText(res, fallback) {
        if (res && res.status === 419) return 'انتهت صلاحية الصفحة. حدّثها وجرّب مرة تانية.';
        const data = res && res.data;
        const text = data && typeof data.error === 'string' ? data.error.trim() : '';
        return text && /[؀-ۿ]/.test(text) ? text : fallback;
    }

    function photoroomNote(methods) {
        if (!methods || methods.photoroom) return '';
        const why = String(methods.photoroom_reason || '');
        if (why === 'no_key') return 'مفتاح PhotoRoom مش محفوظ.';
        if (why.indexOf('paused') === 0) return 'PhotoRoom موقوف شوي لأنو رفض آخر طلب (رصيد أو حصة).';
        return 'PhotoRoom مش جاهز هلق.';
    }

    // ------------------------------------------------------------------
    // DOM
    // ------------------------------------------------------------------

    function shots(url, alt) {
        const el = R.el;
        return el('div', { className: 'cq-stages' }, THEMES.map(([mode, label]) => {
            const stage = el('div', { className: 'cq-stage' }, [R.img(url, alt)]);
            if (R.themePreview) R.themePreview.apply(stage, mode);
            return el('figure', { className: 'cq-shot' }, [stage, el('figcaption', { className: 'cq-shot__label', text: label })]);
        }));
    }

    function mount(app) {
        const el = R.el;
        let config = {};
        try {
            config = JSON.parse(app.getAttribute('data-config') || '{}') || {};
        } catch (e) {
            config = {};
        }
        const urls = config.urls || {};
        const S = { flag: '', page: 1, data: null, busy: new Set(), tries: new Map(), loading: false };
        const part = name => app.querySelector('[data-cq="' + name + '"]');
        const filtersBox = part('filters');
        const statusLine = part('status');
        const grid = part('grid');
        const pager = part('pager');
        const logBox = part('log');
        const logList = part('log-list');

        function toast(text, variant) {
            if (R.toast) R.toast(text, variant);
        }

        function ask(title, text, confirmText) {
            if (root.Laqta && typeof root.Laqta.ask === 'function') return root.Laqta.ask({ title, text, confirmText });
            return Promise.resolve(root.confirm ? root.confirm(title + '\n' + text) : true);
        }

        async function load(options) {
            options = options || {};
            if (S.loading) return;
            S.loading = true;
            app.setAttribute('aria-busy', 'true');
            if (!options.quiet) statusLine.textContent = options.measure === false ? 'عم نجيب الصور…' : 'عم نجيب الصور ونفحص اللي ما انفحصت… ممكن ياخد شوي.';
            const query = new URLSearchParams({ page: String(S.page), measure: options.measure === false ? '0' : '1' });
            if (S.flag) query.set('flag', S.flag);
            const res = await R.requestJson(urls.gallery + '?' + query.toString());
            S.loading = false;
            app.setAttribute('aria-busy', 'false');
            if (!res.ok || !res.data || res.data.status !== 'success') {
                statusLine.textContent = errorText(res, 'ما قدرنا نجيب الصور المنشورة. جرّب مرة تانية.');
                R.clear(grid);
                return;
            }
            S.data = res.data;
            S.page = count(res.data.page) || 1;
            render();
        }

        function renderFilters() {
            R.clear(filtersBox);
            const counts = S.data.counts || {};
            const items = [['', 'الكل', counts.all]].concat(FLAGS.map(([key, label]) => [key, label, counts[key]]));
            items.forEach(([key, label, n]) => {
                filtersBox.appendChild(el('button', {
                    type: 'button', className: 'lq-filter', 'aria-pressed': String(S.flag === key), dataset: { flag: key || 'all' },
                    onclick: () => { if (S.flag === key) return; S.flag = key; S.page = 1; load({ measure: false }); }
                }, [label, el('span', { className: 'lq-filter__count', text: String(count(n)) })]));
            });
        }

        function renderStatus() {
            R.clear(statusLine);
            statusLine.appendChild(document.createTextNode(statusText(S.data)));
            if (count(S.data.unchecked) > 0) {
                statusLine.appendChild(el('button', { type: 'button', className: 'lq-btn lq-btn--ghost lq-btn--sm', text: 'افحص أكتر',
                    onclick: () => load({ measure: true }) }));
            }
        }

        function cardActions(item, card) {
            const methods = S.data.methods || {};
            const busy = S.busy.has(item.id);
            const prNote = photoroomNote(methods);
            const actions = el('div', { className: 'cq-card__actions' }, [
                el('button', { type: 'button', className: 'lq-btn lq-btn--secondary lq-btn--sm', disabled: busy || !methods.photoroom,
                    title: prNote || null, dataset: { action: 'photoroom' }, onclick: () => tryCut(item, 'photoroom', card) },
                [R.icon('refresh', 16, 2), 'أعد القص بـPhotoRoom']),
                el('button', { type: 'button', className: 'lq-btn lq-btn--ghost lq-btn--sm', disabled: busy || !methods.local,
                    title: methods.local ? null : 'القص المحلي مش منزّل على هالجهاز.', dataset: { action: 'local' },
                    onclick: () => tryCut(item, 'local', card) }, [R.icon('sparkle', 16, 2), 'جرّب القص المحلي'])
            ]);
            const hints = ['PhotoRoom بيكلّف طلب عزل واحد بالعادة.'];
            hints.push(methods.local ? 'القص المحلي مجاني بس أبطأ.' : 'القص المحلي مش منزّل على هالجهاز.');
            if (prNote) hints.unshift(prNote);
            return [actions, el('p', { className: 'cq-card__hint', text: hints.join(' ') })];
        }

        function comparePanel(item, attempt, card) {
            const busy = S.busy.has(item.id);
            return el('div', { className: 'cq-card__new', dataset: { token: attempt.token } }, [
                el('p', { className: 'cq-card__label', text: 'القص الجديد' }),
                shots(attempt.preview, 'القص الجديد لـ ' + (item.product_name || '')),
                el('p', { className: 'cq-card__msg', text: tryText(attempt) }),
                el('div', { className: 'cq-card__actions' }, [
                    el('button', { type: 'button', className: 'lq-btn lq-btn--primary lq-btn--sm', disabled: busy || !attempt.can_apply,
                        dataset: { action: 'apply' }, onclick: () => applyCut(item, attempt, card) }, [R.icon('check', 16, 2), 'اعتمد الجديد']),
                    el('button', { type: 'button', className: 'lq-btn lq-btn--secondary lq-btn--sm', disabled: busy,
                        dataset: { action: 'keep' }, onclick: () => keepOld(item, attempt, card) }, 'خلّي القديم')
                ])
            ]);
        }

        function buildCard(item) {
            const card = el('article', { className: 'cq-card', dataset: { id: item.id } });
            fillCard(card, item);
            return card;
        }

        function fillCard(card, item, message) {
            R.clear(card);
            const attempt = S.tries.get(item.id);
            const flags = item.flags || [];
            card.appendChild(el('header', { className: 'cq-card__head' }, [
                el('div', { className: 'cq-card__title' }, [
                    R.bdi(item.product_name || 'منتج بلا اسم', 'cq-card__name'),
                    item.brand ? el('span', { className: 'cq-card__brand', text: item.brand }) : null
                ]),
                el('ul', { className: 'cq-flags', 'aria-label': 'ملاحظات القص' }, flags.length
                    ? flags.map(f => el('li', { className: 'cq-flag cq-flag--' + f, text: FLAG_TEXT[f] || f }))
                    : [el('li', { className: 'cq-flag cq-flag--ok', text: 'بلا ملاحظات' })])
            ]));
            card.appendChild(el('p', { className: 'cq-card__label', text: attempt ? 'المنشورة هلق' : 'المنشورة' }));
            card.appendChild(shots(item.url, 'الصورة المنشورة لـ ' + (item.product_name || '')));
            if (attempt) card.appendChild(comparePanel(item, attempt, card));
            else cardActions(item, card).forEach(node => card.appendChild(node));
            const msg = el('p', { className: 'cq-card__msg' + (message && message.tone === 'danger' ? ' cq-card__msg--danger' : ''),
                role: 'status', 'aria-live': 'polite', text: message ? message.text : '' });
            card.appendChild(msg);
            return card;
        }

        function refreshCard(card, item, message) {
            fillCard(card, item, message);
        }

        async function tryCut(item, method, card) {
            if (S.busy.has(item.id)) return;
            S.busy.add(item.id);
            refreshCard(card, item, { text: method === 'photoroom' ? 'عم نقص بـPhotoRoom…' : 'عم نقص محلياً… ممكن ياخد دقيقة.' });
            const res = await R.requestJson(urls.try, { method: 'POST', body: { id: item.id, method: method } });
            S.busy.delete(item.id);
            if (res.ok && res.data && res.data.status === 'success') {
                S.tries.set(item.id, res.data);
                refreshCard(card, item);
                return;
            }
            refreshCard(card, item, { text: errorText(res, 'ما زبط القص الجديد. جرّب مرة تانية.'), tone: 'danger' });
        }

        async function applyCut(item, attempt, card) {
            if (S.busy.has(item.id)) return;
            S.busy.add(item.id);
            refreshCard(card, item, { text: 'عم ننشر النسخة الجديدة…' });
            const res = await R.requestJson(urls.apply, { method: 'POST', body: { token: attempt.token } });
            S.busy.delete(item.id);
            if (res.ok && res.data && res.data.status === 'success') {
                S.tries.delete(item.id);
                toast(applyText(res.data), 'success');
                await load({ measure: false, quiet: true });
                return;
            }
            if (res.data && res.data.error_code === 'expired') S.tries.delete(item.id);
            refreshCard(card, item, { text: errorText(res, 'ما قدرنا ننشر القص الجديد. القديم لسا متل ما هو.'), tone: 'danger' });
        }

        async function keepOld(item, attempt, card) {
            S.tries.delete(item.id);
            refreshCard(card, item, { text: 'خلّينا القديم. القص الجديد انمسح.' });
            await R.requestJson(urls.discard, { method: 'POST', body: { token: attempt.token } });
        }

        async function undo(entry, button) {
            const ok = await ask('نرجّع الصورة القديمة؟', '«' + (entry.product_name || '') + '» رح ترجع للصورة اللي كانت قبل التبديل، '
                + 'وبالشيت بس بالخلايا اللي لسا فيها الصورة الجديدة.', 'رجّع القديم');
            if (!ok) return;
            button.disabled = true;
            const res = await R.requestJson(urls.undo, { method: 'POST', body: { log_id: entry.id } });
            if (res.ok && res.data && res.data.status === 'success') {
                toast('رجعت الصورة القديمة.', 'success');
                await load({ measure: false, quiet: true });
                return;
            }
            button.disabled = false;
            toast(errorText(res, 'ما قدرنا نرجّع الصورة القديمة.'), 'danger');
        }

        function renderGrid() {
            R.clear(grid);
            const items = S.data.items || [];
            if (!items.length) {
                grid.appendChild(el('p', { className: 'cq__empty', text: S.flag ? 'ما في صور فيها هالملاحظة.' : 'ما في صور منشورة لسا.' }));
                return;
            }
            items.forEach(item => grid.appendChild(buildCard(item)));
        }

        function renderPager() {
            R.clear(pager);
            const pages = count(S.data.pages) || 1;
            pager.hidden = pages <= 1;
            if (pages <= 1) return;
            const go = page => () => { S.page = page; load({ measure: false }); };
            pager.appendChild(el('button', { type: 'button', className: 'lq-btn lq-btn--secondary lq-btn--sm', text: 'السابقة',
                disabled: S.page <= 1, onclick: go(S.page - 1) }));
            pager.appendChild(el('span', { className: 'cq__page', text: 'الصفحة ' + S.page + ' من ' + pages }));
            pager.appendChild(el('button', { type: 'button', className: 'lq-btn lq-btn--secondary lq-btn--sm', text: 'التالية',
                disabled: S.page >= pages, onclick: go(S.page + 1) }));
        }

        function renderLog() {
            R.clear(logList);
            const entries = S.data.replaced || [];
            logBox.hidden = !entries.length;
            entries.forEach(entry => {
                const thumbs = el('span', { className: 'cq-log__thumbs' }, [
                    el('span', { className: 'cq-thumb rv-theme--checker' }, [R.img(entry.old_url, 'القديمة')]),
                    R.icon('arrow-left', 14, 2),
                    el('span', { className: 'cq-thumb rv-theme--checker' }, [R.img(entry.new_url, 'الجديدة')])
                ]);
                const when = String(entry.created_at || '').slice(0, 16);
                const info = el('span', { className: 'cq-log__text' }, [
                    R.bdi(entry.product_name || 'منتج', 'cq-log__name'),
                    el('span', { className: 'cq-log__meta', text: [ORIGINS[entry.origin] || '', when,
                        entry.rows && entry.rows.length ? rowsText(entry.rows) : ''].filter(Boolean).join(' · ') })
                ]);
                const action = entry.status === 'done'
                    ? el('button', { type: 'button', className: 'lq-btn lq-btn--ghost lq-btn--sm', text: 'رجّع القديم',
                        onclick: event => undo(entry, event.currentTarget) })
                    : el('span', { className: 'cq-log__undone', text: 'انرجع' });
                logList.appendChild(el('li', { className: 'cq-log__item', dataset: { id: entry.id } }, [thumbs, info, action]));
            });
        }

        function render() {
            renderFilters();
            renderStatus();
            renderGrid();
            renderPager();
            renderLog();
        }

        load({ measure: true });
        return { load: load, state: S };
    }

    R.cutout = { FLAGS: FLAGS, flagText: flagText, pictures: pictures, statusText: statusText, applyText: applyText,
                 tryText: tryText, errorText: errorText, photoroomNote: photoroomNote, mount: mount };

    if (typeof document !== 'undefined' && document.getElementById) {
        const start = () => {
            const app = document.getElementById('cqApp');
            if (app && !app.__cq) app.__cq = mount(app);
        };
        if (document.readyState === 'loading' && document.addEventListener) document.addEventListener('DOMContentLoaded', start);
        else start();
    }
    if (typeof module !== 'undefined' && module.exports) module.exports = R.cutout;
})(typeof window !== 'undefined' ? window : globalThis);
