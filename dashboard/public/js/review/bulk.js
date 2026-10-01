/*
 * لقطة · المراجعة — bulk mode (?mode=bulk): the products waiting for review as cards, filtered by brand and status.
 *
 * "اعتماد N صور مقترحة بلا تحذير" only ever takes images the system pre-selected WITHOUT a warning; a card with a
 * warning shows it and is approved on its own (after the warning is confirmed) or opened in single mode. Approvals
 * and rejections go through the same background queue as single mode: one request at a time.
 */
(function (root) {
    'use strict';

    const R = root.LaqtaReview = root.LaqtaReview || {};
    const { el, icon, bdi, clear } = R;

    function st() {
        return R.S;
    }

    const BULK_FILTERS = [
        { key: 'all', label: 'الكل' },
        { key: 'eligible', label: 'مقترحة بلا تحذير' },
        { key: 'warning', label: 'فيها تحذير' },
        { key: 'none', label: 'بلا اقتراح' }
    ];

    const PAGE = 48;

    function norm(v) {
        return String(v || '').toLowerCase().replace(/\s+/g, ' ').trim();
    }

    // المنتجات التي تنتظر المراجعة (ومعها ما اعتُمد أو رُفض منها في هذه الجلسة، بحالته)
    function source() {
        return st().items.filter(it => R.WAITING.includes(it.base))
            .sort((a, b) => (parseInt(a.product.row_number, 10) || 0) - (parseInt(b.product.row_number, 10) || 0));
    }

    function brandOf(it) {
        return String(it.product.brand || it.product.brand_ar || '').trim();
    }

    function selectedOf(it) {
        return R.storedSelected(it.product);
    }

    // النوع الأصلي للبطاقة (قبل ما فعله المراجع في هذه الجلسة)
    function kindOf(it) {
        if (it.base === 'warning') return 'warning';
        if (it.base === 'none') return 'none';
        return R.bulkEligible(selectedOf(it)) ? 'eligible' : 'proposed';
    }

    function busyOrDone(it) {
        return ['approving', 'approved', 'rejecting', 'rejected'].includes(it.bucket);
    }

    function selectable(it) {
        return !busyOrDone(it) && !!selectedOf(it) && !it.orphan;
    }

    function eligibleNow(it) {
        return selectable(it) && kindOf(it) === 'eligible';
    }

    function byBrand(list) {
        const b = st().bulk.brand;
        return b ? list.filter(it => norm(brandOf(it)) === b) : list;
    }

    function byFilter(list, f) {
        if (f === 'all') return list;
        return list.filter(it => kindOf(it) === f);
    }

    function visibleCards() {
        return byFilter(byBrand(source()), st().bulk.filter);
    }

    // البطاقات المرسومة فعلاً (أول B.limit). التحديد والاعتماد والرفض بالجملة لا يلمسون صورة ما شافها المراجع
    function shownCards() {
        return visibleCards().slice(0, st().bulk.limit);
    }

    function seedSelection() {
        const B = st().bulk;
        B.selected = new Set(shownCards().filter(eligibleNow).map(it => it.key));
    }

    function pruneSelection(shown) {
        const B = st().bulk;
        const keep = new Set(shown.filter(selectable).map(it => it.key));
        Array.from(B.selected).forEach(k => { if (!keep.has(k)) B.selected.delete(k); });
    }

    // -------------------------------------------------------------------------------------------------
    // Structure
    // -------------------------------------------------------------------------------------------------

    function build() {
        const S = st();
        const d = S.dom;
        const box = d.bulk;
        if (!box) return;
        clear(box);
        d.bulkCount = el('span', { className: 'rv-bulk__count', text: '' });
        const single = el('a', { className: 'lq-segmented__item', href: '?', dataset: { mode: 'single' }, text: 'منتج واحد' });
        single.addEventListener('click', e => {
            e.preventDefault();
            R.setMode('single');
        });
        d.bulkHead = el('header', { className: 'rv-bulk__head' }, [
            el('div', { className: 'rv-bulk__titles' }, [el('h1', { className: 'rv-bulk__h1', text: 'قائمة المراجعة' }), d.bulkCount]),
            el('div', { className: 'lq-segmented rv-bulk__modes', role: 'group', 'aria-label': 'طريقة العرض' }, [
                single, el('span', { className: 'lq-segmented__item is-active', 'aria-current': 'page', text: 'بالجملة' })
            ])
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
            if (!b) return;
            S.bulk.filter = b.getAttribute('data-bulk-filter');
            S.bulk.limit = PAGE;
            seedSelection();
            render();
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

        d.bulkPickEligible = el('input', { type: 'checkbox', id: 'rvBulkEligible' });
        d.bulkPickEligible.addEventListener('change', () => {
            if (d.bulkPickEligible.checked) seedSelection();
            else S.bulk.selected = new Set();
            render();
        });
        d.bulkSelected = el('span', { className: 'rv-bulkbar__count', text: '' });
        d.bulkApprove = el('button', { type: 'button', className: 'rv-bulkbar__approve', id: 'rvBulkApprove', disabled: true, text: '' });
        d.bulkApprove.addEventListener('click', approveSelected);
        d.bulkReject = el('button', { type: 'button', className: 'rv-bulkbar__reject', id: 'rvBulkReject', disabled: true, text: 'رفض المحدد…' });
        d.bulkReject.addEventListener('click', openRejectDialog);
        d.bulkNote = el('span', { className: 'rv-bulkbar__note', text: '' });
        d.bulkBar = el('div', { className: 'rv-bulkbar' }, [
            el('label', { className: 'rv-bulkbar__pick' }, [d.bulkPickEligible, el('span', { text: 'تحديد المقترحة بلا تحذير فقط' })]),
            d.bulkSelected,
            d.bulkNote,
            d.bulkApprove,
            d.bulkReject
        ]);
        d.bulkGrid = el('div', { className: 'rv-cards', id: 'rvBulkGrid' });
        d.bulkGrid.addEventListener('click', onGridClick);
        d.bulkGrid.addEventListener('change', onGridChange);
        d.bulkMore = el('div', { className: 'rv-bulk__more' });
        box.appendChild(el('div', { className: 'rv-bulk__top' }, [d.bulkHead, d.bulkTools, d.bulkBar]));
        box.appendChild(d.bulkGrid);
        box.appendChild(d.bulkMore);
    }

    // -------------------------------------------------------------------------------------------------
    // Rendering
    // -------------------------------------------------------------------------------------------------

    function statusChip(it) {
        if (busyOrDone(it)) return R.chipFor(it.bucket, true);
        const kind = kindOf(it);
        const map = { eligible: ['proposed', 'مقترحة'], proposed: ['proposed', 'مقترحة'], warning: ['warning', 'فيها تحذير'], none: ['none', 'بلا اقتراح'] };
        const m = map[kind];
        return el('span', { className: `lq-chip lq-chip--${m[0]} lq-chip--sm rv-chip`, text: m[1] });
    }

    function card(it) {
        const S = st();
        const p = it.product;
        const sel = selectedOf(it);
        const shown = sel || R.storedCandidates(p)[0] || null;
        const checked = S.bulk.selected.has(it.key);
        const can = selectable(it);
        const name = p.product_name || p.product_name_ar || `صف ${p.row_number}`;
        const where = shown ? R.storeOf(shown) : null;
        const meta = [`صف ${p.row_number}`, R.sizeText(p.size), where ? where.store : ''].filter(Boolean).join(' · ');
        const warn = sel && sel.warnings.length ? R.warningText(sel.warnings[0]) : '';
        const state = it.bucket;
        let overlay = null;
        if (state === 'approving' || state === 'rejecting') {
            overlay = el('span', { className: 'rv-card__overlay' }, [el('span', { className: 'lq-spinner', 'aria-hidden': 'true' }),
                                                                   el('span', { text: state === 'approving' ? 'جاري الاعتماد…' : 'جاري الرفض…' })]);
        } else if (state === 'approved') {
            overlay = el('span', { className: 'rv-card__overlay is-done' }, [icon('check', 18, 2.2), el('span', { text: 'تم الاعتماد' })]);
        } else if (state === 'rejected') {
            overlay = el('span', { className: 'rv-card__overlay is-muted' }, [el('span', { text: 'رجعت للطابور' })]);
        }
        return el('article', {
            className: 'rv-card' + (checked ? ' is-selected' : '') + (busyOrDone(it) ? ' is-done' : ''),
            dataset: { key: it.key, kind: kindOf(it) }
        }, [
            el('div', { className: 'rv-card__img' + (sel ? '' : ' is-unproposed'), title: sel ? null : 'ما في صورة مقترحة: هاي أول صورة لقاها البحث' }, [
                shown ? R.img(shown.url, name, S.urls.imageProxy) : el('span', { className: 'rv-card__none' }, [icon('image', 26, 1.6), el('span', { text: 'بلا اقتراح' })]),
                el('label', { className: 'rv-card__check', title: can ? 'تحديد' : (sel ? 'ما بينحدد هلق' : 'بلا اقتراح: افتحه لتختار صورة') }, [
                    el('input', { type: 'checkbox', dataset: { select: it.key }, checked: checked, disabled: !can, 'aria-label': `تحديد ${name}` })
                ]),
                el('span', { className: 'rv-card__chip' }, [statusChip(it)]),
                overlay
            ]),
            el('div', { className: 'rv-card__body' }, [
                bdi(name, 'rv-card__name', p.product_name ? 'ltr' : 'auto'),
                el('span', { className: 'rv-card__meta', text: meta }),
                it.orphan ? el('span', { className: 'rv-card__warn' }, [icon('info', 14, 2), el('span', { text: 'مش موجود بالشيت الحالي' })]) : null,
                warn ? el('span', { className: 'rv-card__warn' }, [icon('alert', 14, 2), el('span', { text: warn })]) : null,
                el('div', { className: 'rv-card__actions' }, [
                    el('button', { type: 'button', className: 'lq-btn lq-btn--soft lq-btn--sm rv-card__approve', dataset: { approve: it.key },
                                   disabled: !can, text: 'اعتماد' }),
                    el('a', { className: 'lq-btn lq-btn--secondary lq-btn--sm rv-card__open', dataset: { open: it.key },
                              href: `?row=${encodeURIComponent(p.row_number)}`, text: 'افتح' })
                ])
            ])
        ]);
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
        clear(d.bulkFilters);
        BULK_FILTERS.forEach(f => {
            const n = byFilter(inBrand, f.key).length;
            d.bulkFilters.appendChild(el('button', { type: 'button', className: 'lq-filter rv-filter--box', dataset: { bulkFilter: f.key },
                                                     'aria-pressed': B.filter === f.key ? 'true' : 'false' },
                                         [el('span', { text: f.label }),
                                          el('span', { className: 'lq-filter__count', text: loading ? '…' : unread ? '—' : String(n) })]));
        });

        const visible = byFilter(inBrand, B.filter);
        const shown = visible.slice(0, B.limit);
        if (!B.seeded && S.load.state === 'ready') {
            B.seeded = true;
            seedSelection();
        }
        pruneSelection(shown);
        renderBar(visible, shown);

        clear(d.bulkGrid);
        clear(d.bulkMore);
        d.bulkGrid.setAttribute('aria-busy', loading ? 'true' : 'false');
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
            const nothing = !all.length;
            d.bulkGrid.appendChild(el('div', { className: 'lq-empty rv-bulk__state' }, [
                el('span', { className: 'lq-empty__icon' }, [icon('review', 24)]),
                el('h2', { className: 'lq-empty__title', text: nothing ? 'ما في شي بانتظار مراجعتك' : 'ما في منتجات بهالفلتر' }),
                el('p', { className: 'lq-empty__text', text: nothing ? 'كل الصور الجاهزة انراجعت. شغّل تشغيل جديد ليجيب نتائج جديدة.'
                                                                     : 'جرّب ماركة ثانية أو فلتر «الكل».' })
            ]));
            return;
        }
        shown.forEach(it => d.bulkGrid.appendChild(card(it)));
        if (visible.length > B.limit) {
            d.bulkMore.appendChild(el('button', { type: 'button', className: 'lq-btn lq-btn--secondary lq-btn--sm',
                                                  text: `اعرض ${Math.min(PAGE, visible.length - B.limit)} كمان (من ${visible.length})`,
                                                  onclick: () => { B.limit += PAGE; render(); } }));
        }
    }

    function renderBar(visible, shown) {
        const S = st();
        const d = S.dom;
        const B = S.bulk;
        const selected = shown.filter(it => B.selected.has(it.key));
        const eligible = selected.filter(eligibleNow);
        const others = selected.length - eligible.length;
        const eligibleVisible = shown.filter(eligibleNow);
        const known = S.load.state !== 'loading' && !(S.load.state === 'error' && !S.products.length);
        d.bulkSelected.textContent = known ? `${selected.length} محددة من ${visible.length}` : '';
        d.bulkPickEligible.checked = eligibleVisible.length > 0 && selected.length === eligibleVisible.length
            && eligibleVisible.every(it => B.selected.has(it.key));
        d.bulkPickEligible.disabled = !eligibleVisible.length;
        const k = eligible.length;
        d.bulkApprove.textContent = k === 0 ? 'اعتماد المقترحة بلا تحذير'
            : k === 1 ? 'اعتماد صورة وحدة مقترحة بلا تحذير' : `اعتماد ${k} صور مقترحة بلا تحذير`;
        d.bulkApprove.disabled = k === 0;
        d.bulkReject.disabled = selected.length === 0;
        d.bulkNote.textContent = others > 0 ? `${others} من المحددة ما بتنعتمد من هون (فيها تحذير أو مش من اقتراح النظام)` : '';
        d.bulkNote.hidden = others === 0;
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

    function enqueueApprove(it) {
        const S = st();
        const sel = selectedOf(it);
        if (!sel || !selectable(it)) return false;
        const job = S.jobs.enqueue(R.buildApproveJob(it, contextFor(it), sel));
        if (!job) return false;
        S.local.set(it.key, 'approving');
        S.bulk.selected.delete(it.key);
        return true;
    }

    // اعتماد المحدد: فقط المقترحة من النظام وبلا تحذير، بطلب واحد في كل مرة بالخلفية
    function approveSelected() {
        const S = st();
        const list = shownCards().filter(it => S.bulk.selected.has(it.key)).filter(eligibleNow);
        if (!list.length) return;
        const n = list.length;
        const what = n === 1 ? 'صورة وحدة مقترحة' : `${n} صور مقترحة`;
        if (!root.confirm(`رح ننشر ${what} بلا تحذير: بتنعزل خلفيتها وبتنرفع على Cloudinary وبينكتب رابطها بالشيت. `
            + 'الصور اللي فيها تحذير أو بلا اقتراح ما رح تنلمس. بتقدر تكمل شغلك وهي عم تنعتمد بالخلفية.')) {
            return;
        }
        list.forEach(enqueueApprove);
        R.rebuild();
        R.renderList();
        render();
    }

    function approveOne(key) {
        const S = st();
        const it = S.byKey.get(key);
        if (!it || !selectable(it)) return;
        const sel = selectedOf(it);
        const cautions = R.cautionsFor(sel);
        if (cautions.length && !root.confirm(`تأكد قبل الاعتماد: ${cautions.join('، ')}. بدك تعتمدها وتنشرها؟`)) return;
        enqueueApprove(it);
        R.rebuild();
        R.renderList();
        render();
    }

    function onGridClick(e) {
        const t = e.target;
        if (!t || !t.closest) return;
        const approve = t.closest('[data-approve]');
        if (approve && !approve.disabled) {
            approveOne(approve.getAttribute('data-approve'));
            return;
        }
        const open = t.closest('[data-open]');
        if (open) {
            e.preventDefault();
            R.setMode('single', { key: open.getAttribute('data-open') });
        }
    }

    function onGridChange(e) {
        const t = e.target;
        if (!t || !t.getAttribute || !t.getAttribute('data-select')) return;
        const S = st();
        const key = t.getAttribute('data-select');
        const it = S.byKey.get(key);
        if (!it || !selectable(it)) {
            t.checked = false;
            return;
        }
        if (t.checked) S.bulk.selected.add(key);
        else S.bulk.selected.delete(key);
        render();
    }

    // -------------------------------------------------------------------------------------------------
    // Reject dialog: asks for the reason and says what happens
    // -------------------------------------------------------------------------------------------------

    let dialogOpen = false;
    let dialogReason = '';

    function openRejectDialog() {
        const S = st();
        const list = shownCards().filter(it => S.bulk.selected.has(it.key) && selectable(it));
        if (!list.length) return;
        dialogOpen = true;
        dialogReason = '';
        const d = S.dom;
        const n = list.length;
        const confirmBtn = el('button', { type: 'button', className: 'lq-btn lq-btn--danger-solid', id: 'rvRejectConfirm', disabled: true,
                                          text: n === 1 ? 'رفض صورة وحدة' : `رفض ${n} صور` });
        const reasons = el('div', { className: 'rv-dialog__reasons', role: 'radiogroup', 'aria-labelledby': 'rvRejectTitle' },
            R.REJECT_REASONS.map((r, i) => {
                const input = el('input', { type: 'radio', name: 'rv_bulk_reason', value: r.code });
                input.addEventListener('change', () => {
                    dialogReason = r.code;
                    confirmBtn.disabled = false;
                });
                return el('label', { className: 'rv-dialog__reason' }, [input, el('span', { className: 'rv-reason__n', text: String(i + 1) }), el('span', { text: r.label })]);
            }));
        confirmBtn.addEventListener('click', () => {
            if (!dialogReason) return;
            const code = dialogReason;
            closeDialog();
            rejectList(list, code);
        });
        clear(d.dialog);
        d.dialog.appendChild(el('div', { className: 'rv-dialog', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'rvRejectTitle' }, [
            el('h2', { className: 'rv-dialog__title', id: 'rvRejectTitle', text: 'ليش ترفضها؟' }),
            el('p', { className: 'rv-dialog__text', text: `رح نرفض الصورة المقترحة لـ ${n === 1 ? 'منتج واحد' : `${n} منتجات`} ونسجّل السبب. `
                + 'المنتجات بترجع للطابور ليندوّر عليها من جديد بالتشغيل الجاي. الصور المعتمدة قبل ما بتنلمس.' }),
            reasons,
            el('div', { className: 'rv-dialog__actions' }, [
                confirmBtn,
                el('button', { type: 'button', className: 'lq-btn lq-btn--secondary', text: 'إلغاء', onclick: () => closeDialog() })
            ])
        ]));
        d.dialog.hidden = false;
        const first = d.dialog.querySelector('input');
        if (first && typeof first.focus === 'function') first.focus();
    }

    function rejectList(list, code) {
        const S = st();
        list.forEach(it => {
            const sel = selectedOf(it);
            if (!sel || !selectable(it)) return;
            const ctx = contextFor(it);
            const job = S.jobs.enqueue({ key: it.key, type: 'reject', label: it.product.product_name || it.product.product_name_ar,
                                         row: ctx.row_number, ctx: ctx, candidate: sel,
                                         body: R.rejectBody(ctx, sel, code, false, '') });
            if (job) {
                S.local.set(it.key, 'rejecting');
                S.bulk.selected.delete(it.key);
            }
        });
        R.rebuild();
        R.renderList();
        render();
    }

    function closeDialog() {
        const d = st().dom;
        if (!dialogOpen) return false;
        dialogOpen = false;
        clear(d.dialog);
        d.dialog.hidden = true;
        if (d.bulkReject && typeof d.bulkReject.focus === 'function') d.bulkReject.focus();
        return true;
    }

    R.bulk = {
        build, render, approveSelected, approveOne, openRejectDialog, closeDialog, dialogOpen: () => dialogOpen,
        visibleCards, shownCards, kindOf, seedSelection, rejectList
    };
})(typeof window !== 'undefined' ? window : globalThis);
