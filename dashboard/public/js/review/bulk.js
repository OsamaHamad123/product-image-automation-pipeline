/*
 * لقطة · المراجعة — bulk mode (?mode=bulk): the products waiting for review as cards, filtered by brand and status.
 *
 * "اعتماد N صور بلا تحذير" only ever takes images the system pre-selected WITHOUT a warning; a card with a warning
 * shows it and is approved on its own (after the warning is confirmed) or opened in single mode. Approvals and
 * rejections go through the same background queue as single mode (approvals held a few seconds for «تراجع»).
 *
 * The status chips are the one filter set of the review screen (R.FILTERS, S.filter): switching mode keeps them. The
 * chips of products that need work one by one (ما انلقت، أعطال، الخلفية) open single mode.
 *
 * The cards come in order of confidence (pre-selected without a warning, the reviewer's earlier pick, with a warning,
 * nothing proposed), each brand's cards together. Keys: arrows move between cards, Space ticks the focused card,
 * Shift+click ticks a range, A approves the focused card (after its warnings, like its button), Shift+A is the bulk
 * button (the ticked ones without warning), R / X rejects the focused card (then 1–7 for the reason), Z zooms.
 *
 * The reviewer never approves a picture they did not see: a card counts as seen once its picture has loaded while the
 * card was in the viewport (IntersectionObserver). Only seen cards are pre-ticked, approved by A or included in
 * Shift+A (the rest are counted and said); a picture that failed to render is never approvable. A tick belongs to the
 * picture it was given for (product key + pick URL): a reload that changes the pick drops it. A click anywhere in a
 * card moves the keyboard focus to that card; after A advances, a second A within approveSettleMs is ignored.
 */
(function (root) {
    'use strict';

    const R = root.LaqtaReview = root.LaqtaReview || {};
    const { el, icon, bdi, clear } = R;

    function st() {
        return R.S;
    }

    const PAGE = 48;
    // أقل عدد قرارات قبل ما نقدّر الوقت الباقي («باقي ~12 دقيقة»)
    const PACE_MIN = 3;

    function norm(v) {
        return String(v || '').toLowerCase().replace(/\s+/g, ' ').trim();
    }

    // المنتجات التي تنتظر المراجعة (ومعها ما اعتُمد أو رُفض منها في هذه الجلسة، بحالته)، بترتيب الثقة ثم الماركة
    function source() {
        const items = st().items;
        if (sourceCache.items !== items) {
            sourceCache = { items: items, list: R.sortWaiting(items.filter(it => R.WAITING.includes(it.base))) };
        }
        return sourceCache.list;
    }

    function brandOf(it) {
        return String(it.product.brand || it.product.brand_ar || '').trim();
    }

    // عناصر القائمة تُبنى من جديد مع كل rebuild، فما يُحسب لكل عنصر (الصورة المقترحة ونوع البطاقة) يُحفظ معه مرة
    // واحدة، وترتيب المصدر يُحفظ لكل قائمة (الأسهم كانت تعيد ترتيب 3000 منتج مع كل ضغطة)
    const memo = new WeakMap();

    function memoOf(it) {
        let m = memo.get(it);
        if (!m) {
            m = {};
            memo.set(it, m);
        }
        return m;
    }

    let sourceCache = { items: null, list: [] };

    function selectedOf(it) {
        const m = memoOf(it);
        if (!('sel' in m)) m.sel = R.storedSelected(it.product);
        return m.sel;
    }

    // النوع الأصلي للبطاقة (قبل ما فعله المراجع في هذه الجلسة)
    function kindOf(it) {
        const m = memoOf(it);
        if (!('kind' in m)) {
            m.kind = it.base === 'warning' ? 'warning' : it.base === 'none' ? 'none'
                : (R.bulkEligible(selectedOf(it)) ? 'eligible' : 'proposed');
        }
        return m.kind;
    }

    function busyOrDone(it) {
        return ['approving', 'approved', 'rejecting', 'rejected'].includes(it.bucket);
    }

    // التحديد يخص الصورة التي أُعطي لها: المنتج ورابط صورته المقترحة
    function tickKey(it) {
        const sel = selectedOf(it);
        return `${it.key}\n${sel ? sel.url : ''}`;
    }

    function ticked(it) {
        return st().bulk.selected.has(tickKey(it));
    }

    // صورة البطاقة ما انعرضت (خطأ تحميل): لا تُحدد ولا تُعتمد
    function imageFailed(it) {
        return st().bulk.failed.has(tickKey(it));
    }

    // ظهرت صورة البطاقة للمراجع: تحمّلت والبطاقة داخل الشاشة
    function seenNow(it) {
        return st().bulk.seen.has(tickKey(it));
    }

    function selectable(it) {
        return !busyOrDone(it) && !!selectedOf(it) && !it.orphan && !imageFailed(it) && !st().moved.has(it.key);
    }

    // يُعتمد الآن (زرها، A، أو ضمن Shift+A): قابلة للتحديد وظهرت للمراجع
    function approvable(it) {
        return selectable(it) && seenNow(it);
    }

    function eligibleNow(it) {
        return selectable(it) && kindOf(it) === 'eligible';
    }

    function settleMs() {
        const v = parseInt(st().cfg.approveSettleMs, 10);
        return isFinite(v) && v >= 0 ? v : 400;
    }

    function cardName(it) {
        const p = it.product;
        return p.product_name || p.product_name_ar || `صف ${p.row_number}`;
    }

    function byBrand(list) {
        const b = st().bulk.brand;
        return b ? list.filter(it => norm(brandOf(it)) === b) : list;
    }

    // فئة اقتراح المحرك للبطاقة (R.laneOf)، تُحفظ مع العنصر مثل نوع البطاقة: اعتماد البطاقة ما يخرجها من فلترها
    function laneOfCard(it) {
        const m = memoOf(it);
        if (!('lane' in m)) m.lane = R.laneOf(selectedOf(it));
        return m.lane;
    }

    // رقاقات المجموعة المشتركة (R.FILTERS) على حالة البطاقة الأصلية: البطاقة اللي انعتمدت هلق بتضل بمكانها بعلامتها
    function byFilter(list, key) {
        const f = R.filterOf ? R.filterOf(key) : (R.FILTERS.find(x => x.key === key) || R.FILTERS[0]);
        if (!f.waiting) return [];
        if (f.key === 'all') return list;
        if (f.key === 'eligible') return list.filter(it => kindOf(it) === 'eligible');
        if (f.key === 'strict') return list.filter(it => laneOfCard(it) === 'strict');
        if (f.key === 'proposed') return list.filter(it => kindOf(it) !== 'none');
        return list.filter(it => kindOf(it) === f.key);
    }

    function currentFilter() {
        const f = R.filterOf ? R.filterOf(st().filter) : null;
        return f && f.waiting ? f.key : 'all';
    }

    function visibleCards() {
        return byFilter(byBrand(source()), currentFilter());
    }

    // البطاقات المرسومة فعلاً (أول B.limit). التحديد والاعتماد والرفض بالجملة لا يلمسون صورة ما شافها المراجع
    function shownCards() {
        return visibleCards().slice(0, st().bulk.limit);
    }

    // «تحديد المقترحة بلا تحذير»: البطاقات المؤهلة التي ظهرت للمراجع الآن، والتي تظهر بعدها تُحدد عند ظهورها
    function seedSelection() {
        const B = st().bulk;
        B.autoTick = true;
        B.unticked = new Set();
        B.selected = new Set(shownCards().filter(it => eligibleNow(it) && seenNow(it)).map(tickKey));
    }

    // تحديد لصورة تغيّرت (قراءة جاءت بصورة مقترحة أخرى) أو لبطاقة لم تعد قابلة للتحديد يسقط
    function pruneSelection(shown) {
        const B = st().bulk;
        const keep = new Set(shown.filter(selectable).map(tickKey));
        Array.from(B.selected).forEach(k => { if (!keep.has(k)) B.selected.delete(k); });
    }

    function toggleTick(it, on) {
        const B = st().bulk;
        const k = tickKey(it);
        if (on) {
            B.selected.add(k);
            B.unticked.delete(k);
        } else {
            B.selected.delete(k);
            B.unticked.add(k);
        }
    }

    // Shift + ضغطة: كل البطاقات القابلة للتحديد بين آخر بطاقة ضغطها المراجع وهاي بتاخد نفس الحالة
    function tickRange(fromKey, toKey, on) {
        const list = shownCards();
        const a = list.findIndex(it => it.key === fromKey);
        const b = list.findIndex(it => it.key === toKey);
        if (a < 0 || b < 0) return 0;
        let n = 0;
        list.slice(Math.min(a, b), Math.max(a, b) + 1).forEach(it => {
            if (!selectable(it)) return;
            toggleTick(it, on);
            n += 1;
        });
        return n;
    }

    // -------------------------------------------------------------------------------------------------
    // Structure: the head (title, mode toggle, progress, lane meter), the tools (brand, chips, export), then the bar
    // (count + approve + reject) as a direct child of the section so it stays in view over the grid: sticky at the top
    // on a desktop, at the bottom on a phone (review.css)
    // -------------------------------------------------------------------------------------------------

    function build() {
        const S = st();
        const d = S.dom;
        const box = d.bulk;
        if (!box) return;
        clear(box);
        d.bulkCount = el('span', { className: 'rv-bulk__count', text: '' });
        d.bulkProgress = el('span', { className: 'rv-bulk__progress', id: 'rvBulkProgress', 'aria-live': 'polite', hidden: true });
        d.bulkHead = el('header', { className: 'rv-bulk__head' }, [
            el('div', { className: 'rv-bulk__titles' }, [el('h1', { className: 'rv-bulk__h1', text: 'قائمة المراجعة' }), d.bulkCount, d.bulkProgress]),
            R.modeToggle('bulk')
        ]);
        d.bulkBrand = el('select', { className: 'rv-brand__select', 'aria-label': 'الماركة' });
        d.bulkBrand.addEventListener('change', () => {
            S.bulk.brand = d.bulkBrand.value;
            S.bulk.limit = PAGE;
            seedSelection();
            render();
        });
        d.bulkFilters = el('div', { className: 'rv-bulk__filters', role: 'group', 'aria-label': 'تصفية حسب الحالة' });
        d.bulkFilters.addEventListener('click', e => {
            const b = e.target && e.target.closest ? e.target.closest('[data-bulk-filter]') : null;
            if (b) R.setFilter(b.getAttribute('data-bulk-filter'));
        });
        // الملف يحمل كل حل محفوظ مع حالته (اعتماد مراجع، اختيار آلي، أو مستبدل)، فاسمه يقول ذلك
        d.bulkExport = el('a', { className: 'lq-btn lq-btn--ghost lq-btn--sm rv-bulk__export', href: S.urls.export,
                                 title: 'ملف CSV بكل صورة انحفظت ورابطها وحالتها: معتمدة من مراجع، أو آلية، أو مستبدلة' },
                          [icon('upload', 16), el('span', { text: 'تصدير سجل الصور (CSV)' })]);
        d.bulkTools = el('div', { className: 'rv-bulk__tools' }, [
            el('label', { className: 'rv-brand' }, [el('span', { className: 'rv-brand__k', text: 'الماركة' }), d.bulkBrand]),
            d.bulkFilters,
            d.bulkExport
        ]);
        // سطر المفاتيح: بيختفي على شاشات اللمس (review.css @media (hover: none))
        d.bulkKeys = el('p', { className: 'rv-bulk__keys rv-keyhint' }, [
            R.kbd('← → ↑ ↓'), el('span', { text: ' للتنقل · ' }), R.kbd('مسافة'), el('span', { text: ' للتحديد (Shift+ضغطة لمجموعة) · ' }),
            R.kbd('A'), el('span', { text: ' اعتماد · ' }), R.kbd('R'), el('span', { text: ' رفض · ' }),
            R.kbd('Z'), el('span', { text: ' تكبير · ' }), R.kbd('?'), el('span', { text: ' كل الاختصارات' })
        ]);

        d.bulkPickEligible = el('input', { type: 'checkbox', id: 'rvBulkEligible' });
        d.bulkPickEligible.addEventListener('change', () => {
            if (d.bulkPickEligible.checked) seedSelection();
            else {
                S.bulk.selected = new Set();
                S.bulk.autoTick = false;
            }
            render();
        });
        d.bulkSelected = el('span', { className: 'rv-bulkbar__count', text: '' });
        d.bulkApprove = el('button', { type: 'button', className: 'rv-bulkbar__approve', id: 'rvBulkApprove', disabled: true, text: '',
                                       'aria-keyshortcuts': 'Shift+A' });
        d.bulkApprove.addEventListener('click', approveSelected);
        d.bulkReject = el('button', { type: 'button', className: 'rv-bulkbar__reject', id: 'rvBulkReject', disabled: true, text: 'رفض المحدد…' });
        d.bulkReject.addEventListener('click', () => openRejectDialog());
        d.bulkNote = el('span', { className: 'rv-bulkbar__note', text: '' });
        d.bulkBar = el('div', { className: 'rv-bulkbar', id: 'rvBulkBar' }, [
            el('label', { className: 'rv-bulkbar__pick' }, [d.bulkPickEligible, el('span', { text: 'تحديد المقترحة بلا تحذير بس' })]),
            d.bulkSelected,
            d.bulkNote,
            d.bulkApprove,
            d.bulkReject
        ]);
        d.bulkGrid = el('div', { className: 'rv-cards', id: 'rvBulkGrid' });
        d.bulkGrid.addEventListener('click', onGridClick);
        d.bulkGrid.addEventListener('change', onGridChange);
        d.bulkMore = el('div', { className: 'rv-bulk__more' });
        d.bulkLane = el('div', { className: 'rv-bulk__lane', role: 'status', hidden: true });
        box.appendChild(el('div', { className: 'rv-bulk__top' }, [d.bulkHead, d.bulkLane, d.bulkTools, d.bulkKeys]));
        box.appendChild(d.bulkBar);
        box.appendChild(d.bulkGrid);
        box.appendChild(d.bulkMore);
    }

    // -------------------------------------------------------------------------------------------------
    // Rendering
    // -------------------------------------------------------------------------------------------------

    // عدّاد التقدم نحو «النشر الآلي لكل الماركات المؤكدة»: أرقام فئة strict من /api/system/review-lanes (نفس نداء بطاقة
    // الصحة، مخزّن في الخادم): المعتمد من المراجع من المراجَع، وكم اعتماداً متتالياً بلا رفض بقي (more_needed، حسبه
    // بايثون بصيغة ويلسون). «38 / 41 · باقي 12 اعتماد». null بلا أرقام: العداد يختفي بدل ما يقول رقماً ما عرفناه
    function laneView(strict) {
        if (!strict || typeof strict !== 'object') return null;
        if (strict.ready === true) {
            return { ready: true, pct: 100, text: 'جاهز: شغّله من الإعدادات ← النشر الآلي',
                     title: 'الاقتراحات المؤكدة تماماً وصلت للدقة المطلوبة' };
        }
        const accepted = parseInt(strict.accepted, 10);
        const reviewed = parseInt(strict.prechecked, 10);
        if (!isFinite(accepted) || !isFinite(reviewed)) return null;
        const more = parseInt(strict.more_needed, 10);
        const known = isFinite(more);
        return {
            ready: false,
            pct: known && accepted + more > 0 ? Math.round(100 * accepted / (accepted + more)) : 0,
            text: `${accepted} / ${reviewed} · ` + (known ? `باقي ${more} اعتماد` : 'الدقة هلق أقل من المطلوب'),
            title: `اعتمدت ${accepted} من ${reviewed} اقتراح مؤكد تماماً` + (known ? `، وبعد ${more} اعتماد متتالي بلا رفض بينفتح النشر الآلي`
                : '، بس دقتها هلق أقل من الحد المطلوب')
        };
    }

    function laneLine(strict) {
        const v = laneView(strict);
        return v ? v.text : null;
    }

    const LANES_STALE_MS = 60000;
    let lanesState = { state: 'idle', strict: null, at: 0 };

    function paintLane() {
        const d = st().dom;
        if (!d.bulkLane) return;
        const v = lanesState.state === 'ready' ? laneView(lanesState.strict) : null;
        clear(d.bulkLane);
        d.bulkLane.hidden = !v;
        d.bulkLane.classList.toggle('is-ready', !!(v && v.ready));
        if (!v) return;
        d.bulkLane.setAttribute('title', v.title);
        d.bulkLane.appendChild(el('span', { className: 'rv-lane-meter__k', text: 'النشر الآلي للماركات المؤكدة' }));
        d.bulkLane.appendChild(el('span', { className: 'lq-progress lq-progress--sm rv-lane-meter__bar', role: 'progressbar',
                                            'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': String(v.pct),
                                            'aria-label': v.title }, [
            el('span', { className: `lq-progress__bar lq-tone--${v.ready ? 'success' : 'teal'}`, style: `width: ${v.pct}%` })
        ]));
        d.bulkLane.appendChild(el('span', { className: 'rv-lane-meter__v', text: v.text }));
    }

    // مرة عند فتح الوضع وبعدها كل دقيقة على الأكثر مع إعادة الرسم (الخادم يخزّن الجواب 60 ثانية: لا نداء جديد ثقيل)
    function loadLanes() {
        const S = st();
        if (S.mode !== 'bulk' || !S.urls.reviewLanes || lanesState.state === 'loading') return;
        if (lanesState.state !== 'idle' && Date.now() - lanesState.at < LANES_STALE_MS) return;
        lanesState = Object.assign({}, lanesState, { state: 'loading' });
        R.requestJson(S.urls.reviewLanes).then(res => {
            const lanes = res && res.ok && res.data && res.data.status === 'success' ? res.data.lanes : null;
            lanesState = { state: lanes && lanes.strict ? 'ready' : 'error', strict: lanes ? lanes.strict || null : null, at: Date.now() };
            paintLane();
        }, () => {
            lanesState = Object.assign({}, lanesState, { state: 'error', at: Date.now() });
            paintLane();
        });
    }

    // «37 من 120 · باقي ~12 دقيقة»: قرارات هالجلسة بوضع الجملة (اعتماد أو رفض، بيضلّوا محسوبين بعد ما القراءة الجاية
    // تشيلهم من المنتظرة) من كل اللي كان بانتظارك، والوقت الباقي من سرعتك من أول ما فتحت الوضع (بعد PACE_MIN قرارات)
    function minutesText(n) {
        if (n === 1) return 'دقيقة';
        if (n === 2) return 'دقيقتين';
        return n <= 10 ? `${n} دقايق` : `${n} دقيقة`;
    }

    function progressView() {
        const B = st().bulk;
        const done = B.reviewed.size;
        if (!done) return null;
        const left = source().filter(it => !busyOrDone(it) && !B.reviewed.has(it.key)).length;
        let eta = '';
        if (done >= PACE_MIN && left > 0 && B.startedAt) {
            const perItem = Math.max(0, Date.now() - B.startedAt) / done;
            eta = ` · باقي ~${minutesText(Math.max(1, Math.round(perItem * left / 60000)))}`;
        }
        return { done: done, total: done + left, text: `${done} من ${done + left}${eta}` };
    }

    function paintProgress() {
        const d = st().dom;
        if (!d.bulkProgress) return;
        const v = progressView();
        d.bulkProgress.hidden = !v;
        d.bulkProgress.textContent = v ? v.text : '';
    }

    function noteReviewed(keys) {
        const B = st().bulk;
        if (!B.startedAt) B.startedAt = Date.now();
        (keys || []).forEach(k => B.reviewed.add(k));
    }

    function statusChip(it) {
        if (busyOrDone(it)) return R.chipFor(it.bucket, true);
        const kind = kindOf(it);
        const map = { eligible: ['proposed', 'مقترحة'], proposed: ['proposed', 'مقترحة'], warning: ['warning', 'فيها تحذير'], none: ['none', 'بلا اقتراح'] };
        const m = map[kind];
        return el('span', { className: `lq-chip lq-chip--${m[0]} lq-chip--sm rv-chip`, text: m[1] });
    }

    // صورة البطاقة: متى تحمّلت أو فشل عرضها، لهذه الصورة بالذات (المنتج والرابط)
    function cardImage(it, shown, name) {
        const S = st();
        const B = S.bulk;
        const node = R.img(shown.url, name, S.urls.imageProxy);
        const k = `${it.key}\n${shown.url}`;
        if (!node || String(node.tagName || '').toUpperCase() !== 'IMG') {
            B.failed.add(k);
            return node;
        }
        node.addEventListener('load', () => {
            B.failed.delete(k);
            B.loaded.add(k);
            if (B.inView.has(k)) markSeen(k);
        });
        node.addEventListener('error', () => {
            B.failed.add(k);
            B.loaded.delete(k);
            B.seen.delete(k);
            B.selected.delete(k);
            scheduleRefresh();
        });
        return node;
    }

    function card(it) {
        const S = st();
        const p = it.product;
        const sel = selectedOf(it);
        const shown = sel || R.storedCandidates(p)[0] || null;
        const checked = ticked(it);
        const can = selectable(it);
        const name = cardName(it);
        const moved = S.moved.get(it.key);
        const where = shown ? R.storeOf(shown) : null;
        const meta = [`صف ${p.row_number}`, R.sizeText(p.size), where ? where.store : ''].filter(Boolean).join(' · ');
        // كل تحذيرات الصورة المقترحة، لا أولها فقط
        const warns = sel ? sel.warnings.map(w => R.warningText(w)) : [];
        // بطاقة «بلا اقتراح»: لماذا (catalog_match.explain) بجملة واحدة، والرمز في التلميح
        const why = sel ? null : R.noPickReason(it);
        const focused = S.bulk.focus === it.key;
        const state = it.bucket;
        let overlay = null;
        if (state === 'approving' || state === 'rejecting') {
            const job = S.jobs ? S.jobs.activeFor(it.key) : null;
            overlay = job && job.state === 'held'
                ? el('span', { className: 'rv-card__overlay is-held' }, [icon('check', 18, 2.2), el('span', { text: 'رح تنعتمد…' })])
                : el('span', { className: 'rv-card__overlay' }, [el('span', { className: 'lq-spinner', 'aria-hidden': 'true' }),
                                                                 el('span', { text: state === 'approving' ? 'جاري الاعتماد…' : 'جاري الرفض…' })]);
        } else if (state === 'approved') {
            overlay = el('span', { className: 'rv-card__overlay is-done' }, [icon('check', 18, 2.2), el('span', { text: 'انعتمدت' })]);
        } else if (state === 'rejected') {
            overlay = el('span', { className: 'rv-card__overlay is-muted' }, [el('span', { text: 'رجعت للطابور' })]);
        }
        return el('article', {
            className: 'rv-card' + (checked ? ' is-selected' : '') + (busyOrDone(it) ? ' is-done' : '') + (focused ? ' is-focused' : ''),
            dataset: { key: it.key, kind: kindOf(it) },
            tabindex: '-1',
            'aria-current': focused ? 'true' : null
        }, [
            el('div', { className: 'rv-card__img' + (sel ? '' : ' is-unproposed'),
                        dataset: Object.assign({ tick: shown ? `${it.key}\n${shown.url}` : '' }, shown ? { zoom: it.key } : {}),
                        title: sel ? 'كبّر الصورة (Z)' : 'ما في صورة مقترحة: هاي أول صورة لقاها البحث' }, [
                shown ? cardImage(it, shown, name) : el('span', { className: 'rv-card__none' }, [icon('image', 26, 1.6), el('span', { text: 'بلا اقتراح' })]),
                el('label', { className: 'rv-card__check', title: can ? 'تحديد (Shift لمجموعة)' : (sel ? 'ما بينحدد هلق' : 'بلا اقتراح: افتحه لتختار صورة') }, [
                    el('input', { type: 'checkbox', dataset: { select: it.key }, checked: checked, disabled: !can, 'aria-label': `تحديد ${name}` })
                ]),
                el('span', { className: 'rv-card__chip' }, [statusChip(it)]),
                overlay
            ]),
            el('div', { className: 'rv-card__body' }, [
                bdi(name, 'rv-card__name', p.product_name ? 'ltr' : 'auto'),
                el('span', { className: 'rv-card__meta', text: meta }),
                sel ? R.laneBadge(sel) : null,
                !sel && !busyOrDone(it) ? el('p', { className: 'rv-card__why', title: why ? why.key : null,
                                                    dataset: { nopick: why ? why.key : '' }, text: why ? why.text : R.NO_PICK_FALLBACK }) : null,
                it.orphan ? el('span', { className: 'rv-card__warn' }, [icon('info', 14, 2), el('span', { text: 'مش موجود بالشيت الحالي' })]) : null,
                moved ? el('div', { className: 'rv-card__warn rv-card__moved' }, [
                    icon('alert', 14, 2), el('span', { text: 'تغيّر هالمنتج بعد ما ظهر لك: ما بينعتمد قبل ما تعرضه من جديد. ' }),
                    el('button', { type: 'button', className: 'rv-linkbtn rv-card__reopen', dataset: { reopen: it.key }, text: 'اعرضه من جديد' })
                ]) : null,
                imageFailed(it) && sel ? el('span', { className: 'rv-card__warn' }, [icon('alert', 14, 2),
                    el('span', { text: 'ما قدرنا نعرض الصورة، فما بتنعتمد من هون: افتحه لتشوفه' })]) : null,
                warns.length ? el('ul', { className: 'rv-card__warns' },
                                  warns.map((w, i) => el('li', { className: 'rv-card__warn', title: sel.warnings[i] || null },
                                                         [icon('alert', 14, 2), el('span', { text: w })]))) : null,
                sel && R.explainList ? R.explainList(sel, true) : null,
                el('div', { className: 'rv-card__actions' }, [
                    el('button', { type: 'button', className: 'lq-btn lq-btn--soft lq-btn--sm rv-card__approve', dataset: { approve: it.key },
                                   disabled: !approvable(it), title: can && !seenNow(it) ? 'الصورة لسا ما ظهرت لك' : null,
                                   text: 'اعتماد' }),
                    el('a', { className: 'lq-btn lq-btn--secondary lq-btn--sm rv-card__open', dataset: { open: it.key },
                              href: `?row=${encodeURIComponent(p.row_number)}`, text: 'افتح' })
                ])
            ])
        ]);
    }

    // الرقاقات: نفس مجموعة وضع منتج واحد (R.FILTERS). اللي مش من المنتظرة بتفتح وضع منتج واحد عليها
    function drawChips(inBrand, loading, unread) {
        const S = st();
        const d = S.dom;
        const current = currentFilter();
        clear(d.bulkFilters);
        R.FILTERS.forEach(f => {
            const waiting = !!f.waiting;
            const n = waiting ? byFilter(inBrand, f.key).length : (S.counts[f.key] || 0);
            if (!waiting && !n) return;          // ما في منتجات بهالرقاقة: ما في داعي تبيّن هون
            d.bulkFilters.appendChild(el('button', {
                type: 'button', className: 'lq-filter rv-filter--box' + (waiting ? '' : ' rv-filter--elsewhere'),
                dataset: { bulkFilter: f.key }, 'aria-pressed': waiting && current === f.key ? 'true' : 'false',
                title: waiting ? null : 'بتنفتح بوضع منتج واحد'
            }, [el('span', { text: f.label }),
                el('span', { className: 'lq-filter__count', text: loading ? '…' : unread ? '—' : String(n) })]));
        });
    }

    function render() {
        const S = st();
        const d = S.dom;
        if (!d.bulkGrid) return;
        const B = S.bulk;
        const loading = S.load.state === 'loading';
        // المنتجات ما انقرأت: «—» بدل أي صفر؛ وحالة الطابور المجهولة تُقال «تقدير» مثل وضع المنتج الواحد
        const unread = S.load.state === 'error' && !S.products.length;
        const queueKnown = S.queue && S.queue.status !== 'unavailable';
        const all = source();

        loadLanes();
        paintLane();
        d.bulkCount.textContent = loading ? 'لحظة…' : unread ? '—'
            : `${S.counts.waiting} بانتظار المراجعة${queueKnown ? '' : ' (تقدير)'}`;

        // الماركات من المنتجات المنتظرة، الأكثر أولاً
        const brands = new Map();
        all.forEach(it => {
            const label = brandOf(it) || 'بلا ماركة';
            const k = norm(brandOf(it));
            const cur = brands.get(k) || { label: label, n: 0 };
            cur.n += 1;
            brands.set(k, cur);
        });
        clear(d.bulkBrand);
        d.bulkBrand.appendChild(el('option', { value: '', text: loading || unread ? 'كل الماركات' : `كل الماركات (${all.length})` }));
        Array.from(brands.entries()).sort((a, b) => b[1].n - a[1].n || a[1].label.localeCompare(b[1].label))
            .forEach(([k, v]) => d.bulkBrand.appendChild(el('option', { value: k, text: `${v.label} (${v.n})` })));
        if (B.brand && !brands.has(B.brand)) B.brand = '';
        d.bulkBrand.value = B.brand;

        const inBrand = byBrand(all);
        drawChips(inBrand, loading, unread);
        paintProgress();

        const visible = byFilter(inBrand, currentFilter());
        const shown = visible.slice(0, B.limit);
        if (!B.seeded && S.load.state === 'ready') {
            B.seeded = true;
            seedSelection();
        }
        if (!B.startedAt && S.load.state === 'ready') B.startedAt = Date.now();
        pruneSelection(shown);
        renderBar(visible, shown);

        clear(d.bulkGrid);
        clear(d.bulkMore);
        d.bulkGrid.setAttribute('aria-busy', loading ? 'true' : 'false');
        d.bulkBar.hidden = !loading && !unread && !all.length;
        if (loading) {
            for (let i = 0; i < 8; i++) {
                d.bulkGrid.appendChild(el('div', { className: 'rv-card rv-card--skeleton', 'aria-hidden': 'true' }, [
                    el('div', { className: 'lq-skeleton rv-card__skel' }),
                    el('div', { className: 'rv-card__body' }, [el('span', { className: 'lq-skeleton lq-skeleton--text' }),
                                                                el('span', { className: 'lq-skeleton lq-skeleton--short' })])
                ]));
            }
            return;
        }
        if (S.load.state === 'error' && !S.products.length) {
            d.bulkGrid.appendChild(el('div', { className: 'lq-alert lq-alert--danger rv-bulk__state', role: 'alert' }, [
                icon('alert', 18, 2, 'lq-alert__icon'),
                el('div', { className: 'lq-alert__body' }, [el('strong', { className: 'lq-alert__title', text: 'ما انفتحت القائمة: ' }),
                                                             el('span', { text: S.load.error })]),
                el('button', { type: 'button', className: 'lq-alert__action rv-linkbtn', text: 'جرّب مرة ثانية', onclick: () => R.loadData({}) })
            ]));
            return;
        }
        if (!visible.length) {
            if (!all.length) {
                // خلصت المراجعة: شو انعمل اليوم والخطوة الجاية
                d.bulkGrid.appendChild(el('div', { className: 'rv-bulk__state' }, [R.doneRecap()]));
                return;
            }
            d.bulkGrid.appendChild(el('div', { className: 'lq-empty rv-bulk__state' }, [
                el('span', { className: 'lq-empty__icon' }, [icon('review', 24)]),
                el('h2', { className: 'lq-empty__title', text: 'ما في منتجات بهالفلتر' }),
                el('p', { className: 'lq-empty__text', text: 'جرّب ماركة ثانية أو فلتر «الكل».' })
            ]));
            return;
        }
        if (B.focus && !shown.some(it => it.key === B.focus)) B.focus = null;
        // ما يراه المراجع في كل بطاقة (C1): لقطة عند أول رسم، ولا تتحدث بقراءة هادئة
        shown.forEach(it => {
            if (!S.seen.has(it.key)) R.snapshot(it);
        });
        shown.forEach(it => d.bulkGrid.appendChild(card(it)));
        observeCards();
        if (B.focus && B.focusDom) {
            // التنقل بالأسهم ينقل تركيز المتصفح إلى البطاقة (تظهر في الشاشة ويقرؤها قارئ الشاشة)
            const node = Array.from(d.bulkGrid.querySelectorAll('.rv-card')).find(n => n.getAttribute('data-key') === B.focus);
            if (node && typeof node.focus === 'function') node.focus();
            if (node && typeof node.scrollIntoView === 'function') node.scrollIntoView({ block: 'nearest' });
        }
        B.focusDom = false;
        if (visible.length > B.limit) {
            d.bulkMore.appendChild(el('button', { type: 'button', className: 'lq-btn lq-btn--secondary lq-btn--sm',
                                                  text: `اعرض ${Math.min(PAGE, visible.length - B.limit)} كمان (من ${visible.length})`,
                                                  onclick: () => { B.limit += PAGE; render(); } }));
        }
    }

    // رقاقة تغيّرت (R.setFilter): الصفحة من أولها والتحديد من جديد
    function onFilter() {
        const S = st();
        S.bulk.limit = PAGE;
        seedSelection();
        render();
    }

    function renderBar(visible, shown) {
        const S = st();
        const d = S.dom;
        const B = S.bulk;
        const selected = shown.filter(ticked);
        const eligible = selected.filter(eligibleNow);
        const others = selected.length - eligible.length;
        const ready = eligible.filter(seenNow);
        const eligibleVisible = shown.filter(eligibleNow);
        // مقترحة بلا تحذير ومرسومة، لكن صورتها لم تظهر للمراجع بعد (خارج الشاشة أو لم تتحمّل): لا تُحدد ولا تُعتمد
        const unseen = eligibleVisible.filter(it => !seenNow(it)).length;
        const known = S.load.state !== 'loading' && !(S.load.state === 'error' && !S.products.length);
        d.bulkSelected.textContent = known ? `${selected.length} محددة من ${visible.length}` : '';
        d.bulkPickEligible.checked = !!B.autoTick && eligibleVisible.length > 0
            && eligibleVisible.filter(seenNow).every(ticked);
        d.bulkPickEligible.disabled = !eligibleVisible.length;
        const k = ready.length;
        d.bulkApprove.textContent = k === 0 ? 'اعتماد المقترحة بلا تحذير' : `اعتماد ${R.imagesText(k)} بلا تحذير`;
        d.bulkApprove.disabled = k === 0;
        d.bulkReject.disabled = selected.length === 0;
        const notes = [];
        if (others > 0) notes.push(`${others} من المحددة ما بتنعتمد من هون (فيها تحذير أو مش من اقتراح النظام)`);
        if (unseen > 0) notes.push(`${unseen === 1 ? 'وحدة مقترحة' : `${unseen} مقترحة`} بلا تحذير ما ظهرت صورتها لك بعد: ما رح تنعتمد قبل ما تشوفها`);
        d.bulkNote.textContent = notes.join(' · ');
        d.bulkNote.hidden = notes.length === 0;
    }

    // -------------------------------------------------------------------------------------------------
    // Which cards the reviewer has seen: the picture loaded while the card was in the viewport
    // -------------------------------------------------------------------------------------------------

    let observer = null;
    let refreshTimer = null;

    function markSeen(k) {
        const S = st();
        const B = S.bulk;
        if (B.seen.has(k)) return;
        B.seen.add(k);
        const productKey = String(k).split('\n')[0];
        const it = S.byKey.get(productKey);
        // «تحديد المقترحة بلا تحذير»: البطاقة المؤهلة تُحدد عندما تظهر أول مرة، إلا إذا ألغى المراجع تحديدها. صورة
        // مقترحة جديدة لبطاقة ظهرت قبل (قراءة غيّرت الاختيار) لا تُحدد وحدها: يحددها المراجع بعد أن يراها
        const first = !B.everSeen.has(productKey);
        B.everSeen.add(productKey);
        if (it && first && B.autoTick && eligibleNow(it) && tickKey(it) === k && !B.unticked.has(k)) B.selected.add(k);
        scheduleRefresh();
    }

    // تحديث خفيف بعد ظهور بطاقات (أو فشل صورها): الشريط ومربعات التحديد وأزرار الاعتماد، بلا إعادة رسم الشبكة
    function scheduleRefresh() {
        if (refreshTimer) return;
        refreshTimer = true;
        Promise.resolve().then(() => {
            refreshTimer = null;
            refreshCards();
        });
    }

    function refreshCards() {
        const S = st();
        const d = S.dom;
        if (!d.bulkGrid || S.mode !== 'bulk') return;
        const visible = visibleCards();
        const shown = visible.slice(0, S.bulk.limit);
        pruneSelection(shown);
        d.bulkGrid.querySelectorAll('.rv-card').forEach(node => {
            const it = S.byKey.get(node.getAttribute('data-key'));
            if (!it) return;
            const on = ticked(it);
            node.classList.toggle('is-selected', on);
            const box = node.querySelector('input[type="checkbox"]');
            if (box) {
                box.checked = on;
                box.disabled = !selectable(it);
            }
            const btn = node.querySelector('[data-approve]');
            if (btn) {
                btn.disabled = !approvable(it);
                if (approvable(it)) btn.removeAttribute('title');
            }
        });
        renderBar(visible, shown);
    }

    // كل صورة بطاقة تُراقب: داخل الشاشة (نصفها على الأقل) وتحمّلت = ظهرت. بلا IntersectionObserver (متصفح قديم جداً)
    // تُعد البطاقة المرسومة داخل الشاشة
    function observeCards() {
        const S = st();
        const B = S.bulk;
        const d = S.dom;
        if (observer) observer.disconnect();
        observer = null;
        const nodes = d.bulkGrid.querySelectorAll('.rv-card__img');
        const IO = root.IntersectionObserver;
        if (typeof IO !== 'function') {
            nodes.forEach(n => {
                const k = n.getAttribute('data-tick');
                if (!k) return;
                B.inView.add(k);
                if (B.loaded.has(k)) markSeen(k);
            });
            return;
        }
        observer = new IO(entries => {
            entries.forEach(entry => {
                const k = entry.target.getAttribute('data-tick');
                if (!k) return;
                if (entry.isIntersecting) {
                    B.inView.add(k);
                    if (B.loaded.has(k)) markSeen(k);
                } else {
                    B.inView.delete(k);
                }
            });
        }, { threshold: 0.5 });
        nodes.forEach(n => observer.observe(n));
    }

    // -------------------------------------------------------------------------------------------------
    // Actions
    // -------------------------------------------------------------------------------------------------

    function contextFor(it) {
        const ctx = R.productIdentity(it.product);
        const sel = selectedOf(it);
        ctx.search_decision = sel && sel.status === 'preselected' ? 'REVIEW_PRESELECTED' : 'REVIEW_UNSELECTED';
        return ctx;
    }

    // اعتماد مجموعة بطاقات بطلب لكل وحدة: محجوزة سوا approveUndoMs («تراجع» بيرجّعها كلها)، بعدها بالدور
    function enqueueApprovals(list) {
        const S = st();
        const ready = list.filter(it => selectedOf(it) && approvable(it));
        if (!ready.length) return 0;
        const specs = ready.map(it => Object.assign(R.buildApproveJob(it, contextFor(it), selectedOf(it)),
                                                    { tick: ticked(it) ? tickKey(it) : null }));
        const jobs = S.jobs.enqueueMany(specs, { holdMs: S.cfg.approveUndoMs });
        jobs.forEach(job => {
            S.keep.delete(job.key);
            S.local.set(job.key, 'approving');
            const it = S.byKey.get(job.key);
            if (it) S.bulk.selected.delete(tickKey(it));
        });
        noteReviewed(jobs.map(j => j.key));
        return jobs.length;
    }

    // اعتماد المحدد: فقط المقترحة من النظام وبلا تحذير، التي ظهرت صورتها للمراجع، بالخلفية
    function approveSelected() {
        const S = st();
        const shown = shownCards();
        const list = shown.filter(ticked).filter(eligibleNow).filter(seenNow);
        if (!list.length) return false;
        const n = list.length;
        // المقترحة بلا تحذير التي لم تظهر للمراجع (محددة أو لا) تُترك، ويُقال عددها
        const skipped = shown.filter(it => eligibleNow(it) && !seenNow(it)).length;
        const skip = skipped > 0 ? ` ${skipped === 1 ? 'وحدة مقترحة' : `${skipped} مقترحة`} ما ظهرت صورتها لك بعد، فما رح تنعتمد هلق.` : '';
        return R.ask({ title: `اعتماد ${R.imagesText(n)}؟`,
                       text: 'بتنحط بالشيت وبتقدر تكمل شغلك. اللي فيها تحذير أو بلا اقتراح ما رح تنلمس.' + skip,
                       confirmText: 'اعتمدها' }).then(ok => {
            if (!ok) return false;
            // انحسبت من جديد بعد السؤال: بطاقة صارت مش قابلة للاعتماد وقت السؤال بتنترك
            const done = enqueueApprovals(list.filter(it => S.byKey.get(it.key) && ticked(it) && eligibleNow(it) && seenNow(it)));
            R.rebuild();
            R.renderList();
            render();
            return done > 0;
        });
    }

    // بطاقة وحدة (زرها أو A): true / false فوراً، أو Promise لما في تحذير بده تأكيد
    function approveOne(key) {
        const S = st();
        const it = S.byKey.get(key);
        if (!it || !approvable(it)) return false;
        const sel = selectedOf(it);
        const cautions = R.cautionsFor(sel);
        const go = () => {
            const now = S.byKey.get(key) || it;
            if (!approvable(now) || S.jobs.has(key)) return false;
            const n = enqueueApprovals([now]);
            R.rebuild();
            R.renderList();
            render();
            return n > 0;
        };
        if (!cautions.length) return go();
        return R.ask({ title: `«${cardName(it)}»: تأكد قبل الاعتماد`, text: `${cautions.join('، ')}. بدك تعتمدها؟`,
                       confirmText: 'اعتمدها', cancelText: 'لا، رجوع' }).then(ok => ok && go());
    }

    // -------------------------------------------------------------------------------------------------
    // Keyboard (app.js onKeyDown sends the keys here in bulk mode)
    // -------------------------------------------------------------------------------------------------

    // أعمدة الشبكة كما تظهر (بطاقات الصف الأول لها نفس الارتفاع عن أعلى الشبكة)؛ 1 بلا تخطيط
    function columns(nodes) {
        if (!nodes.length || typeof nodes[0].offsetTop !== 'number') return 1;
        const top = nodes[0].offsetTop;
        let n = 0;
        while (n < nodes.length && nodes[n].offsetTop === top) n += 1;
        return Math.max(1, n);
    }

    function focusCard(key) {
        const B = st().bulk;
        B.focus = key;
        B.focusDom = true;
        render();
    }

    // البطاقة التالية بعد المركّزة التي لم تُعتمد أو تُرفض بعد (مثل «بعد الاعتماد ننتقل للمنتج التالي»)
    function nextOpen(list, from) {
        for (let i = from + 1; i < list.length; i++) if (!busyOrDone(list[i])) return list[i].key;
        for (let i = from - 1; i >= 0; i--) if (!busyOrDone(list[i])) return list[i].key;
        return null;
    }

    function onKey(key, e) {
        const S = st();
        const B = S.bulk;
        const list = shownCards();
        if (!list.length) return false;
        const idx = list.findIndex(it => it.key === B.focus);
        if (/^Arrow(Up|Down|Left|Right)$/.test(key)) {
            const cols = S.dom.bulkGrid ? columns(S.dom.bulkGrid.querySelectorAll('.rv-card')) : 1;
            // الشبكة من اليمين لليسار: السهم الأيسر للبطاقة التالية
            const step = { ArrowLeft: 1, ArrowRight: -1, ArrowDown: cols, ArrowUp: -cols }[key];
            const target = idx < 0 ? 0 : Math.min(list.length - 1, Math.max(0, idx + step));
            focusCard(list[target].key);
            return true;
        }
        if (key === 'a' && e && e.shiftKey) {
            if (S.dom.bulkApprove && !S.dom.bulkApprove.disabled) approveSelected();
            return true;
        }
        if (idx < 0) return false;
        const it = list[idx];
        if (key === ' ') {
            if (selectable(it)) {
                if (e && e.shiftKey && B.anchor) tickRange(B.anchor, it.key, !ticked(it));
                else toggleTick(it, !ticked(it));
                B.anchor = it.key;
                B.focusDom = true;
                render();
            }
            return true;
        }
        if (key === 'a') {
            // بعد اعتماد بـ A والانتقال للبطاقة التالية: ضغطة ثانية سريعة لا تعتمدها قبل أن يراها المراجع
            if (Date.now() - (B.advancedAt || 0) < settleMs()) return true;
            const advance = () => {
                B.advancedAt = Date.now();
                focusCard(nextOpen(shownCards(), idx) || it.key);
            };
            const res = approveOne(it.key);
            if (res === true) advance();
            else if (res && typeof res.then === 'function') res.then(ok => { if (ok) advance(); });
            return true;
        }
        if (key === 'r' || key === 'x') {
            if (selectable(it)) openRejectDialog([it], { quick: true });
            return true;
        }
        if (key === 'z') {
            openLightbox(it.key);
            return true;
        }
        return false;
    }

    // أي نقرة داخل بطاقة تنقل تركيز لوحة المفاتيح إليها: A بعدها يعتمد البطاقة التي لمسها المراجع، لا غيرها
    function focusFromEvent(t) {
        const B = st().bulk;
        const node = t && t.closest ? t.closest('.rv-card') : null;
        const key = node ? node.getAttribute('data-key') : null;
        if (!key) return false;
        const changed = B.focus !== key;
        B.focus = key;
        B.focusDom = true;
        return changed;
    }

    // Shift على آخر نقرة على مربع تحديد (حدث change ما بيحمل shiftKey)
    let shiftClick = false;

    function onGridClick(e) {
        const t = e.target;
        if (!t || !t.closest) return;
        if (t.closest('[data-select]')) shiftClick = !!e.shiftKey;
        const zoom = t.closest('[data-zoom]');
        if (zoom && !t.closest('label, button, a, input')) {
            focusFromEvent(t);
            e.preventDefault();
            openLightbox(zoom.getAttribute('data-zoom'));
            return;
        }
        const moved = focusFromEvent(t);
        const reopen = t.closest('[data-reopen]');
        if (reopen) {
            const it = st().byKey.get(reopen.getAttribute('data-reopen'));
            if (it) R.snapshot(it);
            render();
            return;
        }
        const approve = t.closest('[data-approve]');
        if (approve && !approve.disabled) {
            const res = approveOne(approve.getAttribute('data-approve'));
            if (!(res && typeof res.then === 'function')) render();
            return;
        }
        const open = t.closest('[data-open]');
        if (open) {
            e.preventDefault();
            R.setMode('single', { key: open.getAttribute('data-open') });
            return;
        }
        // مربع التحديد يرسم الشبكة في change؛ نقرة على البطاقة نفسها تُظهر تركيزها
        if (moved && !t.closest('[data-select]') && !t.closest('label')) render();
    }

    function onGridChange(e) {
        const t = e.target;
        if (!t || !t.getAttribute || !t.getAttribute('data-select')) return;
        const S = st();
        const B = S.bulk;
        const key = t.getAttribute('data-select');
        focusFromEvent(t);
        const it = S.byKey.get(key);
        if (!it || !selectable(it)) {
            t.checked = false;
            render();
            return;
        }
        if (shiftClick && B.anchor && B.anchor !== key) tickRange(B.anchor, key, !!t.checked);
        else toggleTick(it, !!t.checked);
        shiftClick = false;
        B.anchor = key;
        render();
    }

    // التكبير على البطاقات: صور البطاقات المرسومة بالترتيب، ← → بينها، وجنبها صورة الشيت الحالية لكل بطاقة
    function openLightbox(key) {
        const S = st();
        const cards = shownCards().map(it => ({ it: it, pick: selectedOf(it) || R.storedCandidates(it.product)[0] || null }))
            .filter(c => c.pick && c.pick.url);
        const index = cards.findIndex(c => c.it.key === key);
        if (index < 0) return false;
        return R.openLightbox({
            items: cards.map(c => {
                const where = R.storeOf(c.pick);
                return { url: c.pick.url, caption: cardName(c.it), note: [`صف ${c.it.product.row_number}`, where.store].filter(Boolean).join(' · ') };
            }),
            index: index,
            title: 'تكبير الصورة',
            compare: i => ({ url: R.shownApprovedUrl(cards[i].it.product) || '', caption: 'الصورة الحالية بالشيت' })
        });
    }

    // -------------------------------------------------------------------------------------------------
    // Reject dialog: asks for the reason (1–7 on the keyboard) and says what happens. opts.quick (R / X on a card):
    // a number key rejects at once with that reason
    // -------------------------------------------------------------------------------------------------

    let dialog = null;

    function openRejectDialog(cards, opts) {
        const S = st();
        opts = opts || {};
        const list = (cards || shownCards().filter(it => ticked(it))).filter(selectable);
        if (!list.length) return;
        const d = S.dom;
        const n = list.length;
        const opener = document.activeElement;
        dialog = { list: list, reason: '', quick: !!opts.quick, opener: opener };
        const confirmBtn = el('button', { type: 'button', className: 'lq-btn lq-btn--danger-solid', id: 'rvRejectConfirm', disabled: true,
                                          text: n === 1 ? 'رفض الصورة' : `رفض ${R.imagesText(n)}` });
        const reasons = el('div', { className: 'rv-dialog__reasons', role: 'radiogroup', 'aria-labelledby': 'rvRejectTitle' },
            R.REJECT_REASONS.map((r, i) => {
                const input = el('input', { type: 'radio', name: 'rv_bulk_reason', value: r.code });
                input.addEventListener('change', () => {
                    if (!dialog) return;
                    dialog.reason = r.code;
                    confirmBtn.disabled = false;
                });
                return el('label', { className: 'rv-dialog__reason' }, [input, el('span', { className: 'rv-reason__n', text: String(i + 1) }), el('span', { text: r.label })]);
            }));
        confirmBtn.addEventListener('click', () => {
            if (!dialog || !dialog.reason) return;
            const code = dialog.reason;
            const chosen = dialog.list;
            closeDialog();
            rejectList(chosen, code);
        });
        const what = n === 1 ? `«${cardName(list[0])}»` : `${n} منتجات`;
        clear(d.dialog);
        dialog.box = el('div', { className: 'rv-dialog', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'rvRejectTitle' }, [
            el('h2', { className: 'rv-dialog__title', id: 'rvRejectTitle', text: 'ليش ترفضها؟' }),
            el('p', { className: 'rv-dialog__text', text: `رح نرفض الصورة المقترحة لـ ${what}${opts.quick ? ' (اضغط رقم السبب 1–7)' : ''}. `
                + 'بنسجّل السبب. المنتج اللي إله صور ثانية بيضل بانتظار مراجعتك فيها، واللي ما ضل إله صور بيرجع للطابور ليندوّر عليه من جديد بالتشغيل الجاي. '
                + 'الصور المعتمدة قبل ما بتنلمس.' }),
            reasons,
            el('div', { className: 'rv-dialog__actions' }, [
                confirmBtn,
                el('button', { type: 'button', className: 'lq-btn lq-btn--secondary', text: 'إلغاء', onclick: () => closeDialog() })
            ])
        ]);
        d.dialog.appendChild(dialog.box);
        d.dialog.hidden = false;
        const first = d.dialog.querySelector('input');
        if (first && typeof first.focus === 'function') first.focus();
    }

    // مفاتيح نافذة الرفض: 1–7 السبب (quick: ويرفض فوراً)، Enter يأكد سبباً مختاراً، Esc يسكّر، Tab يضل جوّا
    function dialogKey(e, key) {
        if (!dialog) return false;
        if (key === 'Escape') {
            e.preventDefault();
            closeDialog();
            return true;
        }
        if (key === 'Tab') {
            R.trapTab(e, dialog.box);
            return true;
        }
        if (/^[1-9]$/.test(key) && !e.repeat) {
            const reason = R.REJECT_REASONS[parseInt(key, 10) - 1];
            if (!reason) return true;
            e.preventDefault();
            const radio = Array.from(dialog.box.querySelectorAll('input[type="radio"]')).find(r => r.value === reason.code);
            if (radio && !radio.checked) radio.click();
            if (dialog.quick) {
                const chosen = dialog.list;
                closeDialog();
                rejectList(chosen, reason.code);
            }
            return true;
        }
        if (key === 'Enter') {
            const t = document.activeElement;
            if (t && t.tagName && /^(BUTTON|A)$/i.test(String(t.tagName))) return false;       // الزر نفسه
            e.preventDefault();
            if (dialog.reason && !e.repeat) {
                const chosen = dialog.list;
                const code = dialog.reason;
                closeDialog();
                rejectList(chosen, code);
            }
            return true;
        }
        return false;
    }

    function rejectList(list, code) {
        const S = st();
        const keys = [];
        list.forEach(it => {
            const sel = selectedOf(it);
            if (!sel || !selectable(it)) return;
            const ctx = contextFor(it);
            const job = S.jobs.enqueue({ key: it.key, type: 'reject', label: it.product.product_name || it.product.product_name_ar,
                                         row: ctx.row_number, ctx: ctx, candidate: sel,
                                         body: R.rejectBody(ctx, sel, code, false, '') });
            if (job) {
                keys.push(it.key);
                S.local.set(it.key, 'rejecting');
                S.bulk.selected.delete(tickKey(it));
            }
        });
        noteReviewed(keys);
        R.rebuild();
        R.renderList();
        render();
    }

    function closeDialog() {
        const d = st().dom;
        if (!dialog) return false;
        const opener = dialog.opener;
        dialog = null;
        clear(d.dialog);
        d.dialog.hidden = true;
        if (opener && opener.isConnected !== false && typeof opener.focus === 'function') opener.focus();
        else if (d.bulkReject && typeof d.bulkReject.focus === 'function') d.bulkReject.focus();
        return true;
    }

    R.bulk = {
        build, render, onFilter, approveSelected, approveOne, openRejectDialog, closeDialog, dialogOpen: () => !!dialog, dialogKey,
        visibleCards, shownCards, kindOf, laneOfCard, laneLine, laneView, progressView, seedSelection, rejectList, onKey,
        refreshCards, openLightbox, tickRange,
        // مفاتيح المنتجات المحددة الآن (لصورها الحالية)
        tickedKeys: () => shownCards().filter(ticked).map(it => it.key)
    };
})(typeof window !== 'undefined' ? window : globalThis);
