/*
 * لقطة · المراجعة — page controller: state, data, the review queue column, the background-approval panel,
 * the two modes (?mode=bulk), deep links (?row=N, ?filter=failed) and the keyboard.
 * The single-product workspace lives in single.js, the bulk grid in bulk.js.
 */
(function (root) {
    'use strict';

    const R = root.LaqtaReview = root.LaqtaReview || {};
    const { el, icon, bdi, clear } = R;

    const DEFAULT_URLS = {
        page: '/catalog',
        products: '/api/products-json',
        queueState: '/api/review/queue-state',
        explainBackfill: '/api/review/explain-backfill',
        undoReject: '/api/review/undo-reject',
        clearCache: '/api/clear-products-cache',
        search: '/api/search',
        select: '/api/select_image',
        reject: '/api/reject_image',
        upload: '/api/upload_manual_image',
        saveCandidates: '/api/v1/curation/save-candidates',
        selectCandidate: '/api/v1/curation/select-candidate',
        retry: '/api/failures/retry',
        imageProxy: '/api/image-proxy',
        export: '/rich-catalog/export',
        run: '/batch-automation',
        publishCheck: '/system-diagnostics#publish-check',
        bgMethod: '/api/settings/bg-method'
    };

    const LIST_PAGE = 150;

    const S = R.S = {
        cfg: {},
        urls: Object.assign({}, DEFAULT_URLS),
        mode: 'single',
        filter: 'all',
        reason: '',                // رقاقة «السبب» (catalog_match.explain): '' = كل الأسباب
        explainBackfill: false,    // طُلب حساب أسباب «بلا اقتراح» للصفوف المحفوظة قبلها (مرة في الجلسة)
        query: '',
        products: [],
        queue: null,
        items: [],
        byKey: new Map(),
        counts: R.countBuckets([]),
        load: { state: 'loading', error: '', detail: '', queueError: '' },
        local: new Map(),          // key -> approving | approved | rejected | requeued (this page session)
        approved: new Map(),       // key -> { link, warning, url }
        session: new Map(),        // key -> search results, the reviewer's pick, pasted / uploaded images
        keep: new Set(),           // keys requeued for review in this session (reject + research): stay in the chip
        // C1: key -> { expected, view, rebase }. expected: what an approval carries (the product as the reviewer was
        // shown it when it was opened / its card was drawn, or what the server answered after the reviewer's own action);
        // view: the page's reading at that moment. A quiet reload never moves either: it is compared with view instead
        seen: new Map(),
        moved: new Map(),          // key -> { before, after }: a reload changed the product after it was shown
        shown: null,               // single mode: the pick on screen { key, url, loadedAt, failed }
        settleTimer: null,
        openKey: null,
        open: null,                // identity of the open product (as in the sheet when it was opened)
        searchSeq: 0,
        searchController: null,
        autoTimer: null,
        ws: { key: null, state: 'none' },
        reasonsOpen: false,
        listLimit: LIST_PAGE,
        // bulk: selected / seen / loaded / failed / inView / unticked hold '<key>\n<pick url>' (a tick belongs to the picture)
        bulk: { brand: '', filter: 'all', selected: new Set(), limit: 48, seeded: false, focus: null, autoTick: true,
                unticked: new Set(), seen: new Set(), loaded: new Set(), failed: new Set(), inView: new Set(), advancedAt: 0,
                everSeen: new Set() },
        runDiff: 0,
        dom: {},
        jobs: null
    };

    function plural(n, one, many) {
        return n === 1 ? one : `${n} ${many}`;
    }
    R.plural = plural;

    // -------------------------------------------------------------------------------------------------
    // Shell
    // -------------------------------------------------------------------------------------------------

    function kbd(text) {
        return el('kbd', { className: 'lq-kbd rv-kbd', 'aria-hidden': 'true', text: text });
    }
    R.kbd = kbd;

    function buildShell(rootEl) {
        clear(rootEl);
        rootEl.removeAttribute('aria-busy');
        const d = S.dom;
        d.root = rootEl;

        d.wsBody = el('div', { className: 'rv-ws__body', id: 'rvWsBody' });
        d.reasons = el('div', { className: 'rv-reasons', role: 'group', 'aria-label': 'سبب الرفض', hidden: true });
        d.approveBtn = el('button', { type: 'button', className: 'lq-btn lq-btn--primary lq-btn--lg rv-approve', id: 'rvApprove',
                                      'aria-keyshortcuts': 'Enter', disabled: true },
                          [icon('check', 18, 2.2), el('span', { text: 'اعتماد ونشر' }), kbd('Enter')]);
        d.rejectBtn = el('button', { type: 'button', className: 'lq-btn lq-btn--danger lq-btn--lg rv-reject', id: 'rvReject',
                                     'aria-keyshortcuts': 'X', 'aria-expanded': 'false', disabled: true },
                         [icon('x', 18, 2.2), el('span', { text: 'رفض' }), kbd('X')]);
        d.skipBtn = el('button', { type: 'button', className: 'lq-btn lq-btn--secondary lq-btn--lg rv-skip', id: 'rvSkip',
                                   'aria-keyshortcuts': 'S', disabled: true },
                       [el('span', { text: 'تخطي' }), kbd('S')]);
        d.nfBtn = el('button', { type: 'button', className: 'lq-btn lq-btn--ghost rv-nf-toggle', id: 'rvNotFoundToggle',
                                 'aria-expanded': 'false', disabled: true, text: 'ما لقيت الصورة الصحيحة؟' });
        d.nextName = bdi('', 'rv-next__name');
        d.nextHint = el('span', { className: 'rv-next__hint', text: 'بعد الاعتماد بننتقل تلقائياً للمنتج التالي' });
        d.bar = el('div', { className: 'lq-actionbar rv-actionbar', id: 'rvActionbar' }, [
            d.reasons,
            el('div', { className: 'rv-actionbar__row' }, [
                d.approveBtn, d.rejectBtn, d.skipBtn, d.nfBtn,
                el('span', { className: 'lq-actionbar__aside rv-next' }, [d.nextHint, d.nextName])
            ])
        ]);
        d.ws = el('section', { className: 'rv-ws', 'aria-label': 'مساحة المراجعة' }, [d.wsBody, d.bar]);

        d.waiting = el('span', { className: 'rv-queue__count', id: 'rvWaiting', text: 'لحظة…' });
        d.refreshBtn = el('button', { type: 'button', className: 'lq-btn lq-btn--ghost lq-btn--sm lq-btn--icon rv-refresh',
                                      'aria-label': 'اقرأ الشيت من جديد', title: 'اقرأ الشيت من جديد' }, [icon('refresh', 16, 2)]);
        d.search = el('input', { type: 'search', className: 'lq-search__input', id: 'rvSearch',
                                 placeholder: 'اسم، باركود، أو رقم صف', autocomplete: 'off', spellcheck: 'false' });
        d.filters = el('div', { className: 'rv-filters', role: 'group', 'aria-label': 'تصفية القائمة' });
        d.reasonChips = el('div', { className: 'rv-reasons-filter', role: 'group', 'aria-label': 'تصفية حسب سبب «بلا اقتراح»', hidden: true });
        d.queueNote = el('div', { className: 'rv-queue__note' });
        d.list = el('ul', { className: 'rv-list', id: 'rvList', 'aria-label': 'المنتجات' });
        d.toBulk = el('a', { className: 'rv-queue__bulk', href: '?mode=bulk' }, [icon('grid', 16), el('span', { text: 'وضع الجملة' })]);
        d.queue = el('aside', { className: 'rv-queue', 'aria-label': 'قائمة المراجعة' }, [
            el('div', { className: 'rv-queue__head' }, [
                el('div', { className: 'rv-queue__title' }, [
                    el('h1', { className: 'rv-queue__h1', text: 'قائمة المراجعة' }), d.waiting, d.refreshBtn
                ]),
                el('label', { className: 'lq-search rv-search' }, [icon('search', 18), el('span', { className: 'lq-sr-only', text: 'بحث بالقائمة' }), d.search]),
                d.filters,
                d.reasonChips,
                d.queueNote
            ]),
            d.list,
            el('div', { className: 'rv-queue__foot' }, [el('span', { text: '↑ ↓ للتنقل بين المنتجات' }), d.toBulk])
        ]);
        d.single = el('div', { className: 'rv-single' }, [d.ws, d.queue]);

        d.bulk = el('section', { className: 'rv-bulk', 'aria-label': 'المراجعة بالجملة', hidden: true });
        d.jobs = el('div', { className: 'rv-jobs', id: 'rvJobs', role: 'status', 'aria-live': 'polite', hidden: true });
        d.dialog = el('div', { className: 'rv-dialog-backdrop', id: 'rvDialog', hidden: true });

        rootEl.appendChild(d.single);
        rootEl.appendChild(d.bulk);
        rootEl.appendChild(d.jobs);
        rootEl.appendChild(d.dialog);

        d.list.addEventListener('click', e => {
            const btn = e.target && e.target.closest ? e.target.closest('[data-key]') : null;
            if (btn && !btn.disabled) R.single.openItem(btn.getAttribute('data-key'), { from: 'list' });
            const more = e.target && e.target.closest ? e.target.closest('[data-more]') : null;
            if (more) {
                S.listLimit += LIST_PAGE;
                renderList();
            }
        });
        d.filters.addEventListener('click', e => {
            const btn = e.target && e.target.closest ? e.target.closest('[data-filter]') : null;
            if (btn) setFilter(btn.getAttribute('data-filter'));
        });
        d.reasonChips.addEventListener('click', e => {
            const btn = e.target && e.target.closest ? e.target.closest('[data-nopick-reason]') : null;
            if (btn) setReason(btn.getAttribute('data-nopick-reason'));
        });
        let searchTimer = null;
        d.search.addEventListener('input', () => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => {
                S.query = d.search.value;
                S.listLimit = LIST_PAGE;
                renderList();
            }, 120);
        });
        d.refreshBtn.addEventListener('click', () => loadData({ refresh: true }));
        d.toBulk.addEventListener('click', e => {
            e.preventDefault();
            setMode('bulk');
        });
        d.approveBtn.addEventListener('click', () => R.single.approveCurrent());
        d.rejectBtn.addEventListener('click', () => (S.reasonsOpen ? R.single.closeReasons() : R.single.openReasons()));
        d.skipBtn.addEventListener('click', () => R.single.skip());
        d.nfBtn.addEventListener('click', () => R.single.toggleNotFound());
    }

    // -------------------------------------------------------------------------------------------------
    // Data
    // -------------------------------------------------------------------------------------------------

    function productsError(res) {
        const raw = String((res && res.data && (res.data.error || res.data.message)) || (res && res.status ? `HTTP ${res.status}` : ''));
        if (res && res.network) return { error: 'ما قدرنا نوصل للخادم.', detail: raw };
        if (S.cfg.db === 'offline' || /SQLSTATE|Connection refused|database/i.test(raw)) {
            return { error: 'قاعدة البيانات مش متاحة، فما قدرنا نجيب المنتجات وصورها المقترحة.', detail: raw };
        }
        if (/sheet|google/i.test(raw)) {
            return { error: 'ما قدرنا نقرأ Google Sheet. تأكد من رابط الشيت ومشاركته مع حساب الخدمة، وجرّب مرة ثانية.', detail: raw };
        }
        return { error: 'ما قدرنا نجيب قائمة المنتجات.', detail: raw };
    }

    let loadSeq = 0;

    async function loadData(opts) {
        opts = opts || {};
        const seq = ++loadSeq;
        if (!opts.quiet) {
            S.load = Object.assign({}, S.load, { state: S.products.length ? 'reloading' : 'loading', error: '', detail: '' });
            renderList();
            if (!S.products.length) R.single.renderWorkspace();
        }
        if (opts.refresh) {
            await R.requestJson(S.urls.clearCache, { method: 'POST', body: {} });
        }
        const productsUrl = S.urls.products + (opts.refresh ? '?refresh=true' : '');
        const [prodRes, queueRes] = await Promise.all([R.requestJson(productsUrl), R.requestJson(S.urls.queueState)]);
        if (seq !== loadSeq) return;

        const productsOk = prodRes.ok && prodRes.data.status === 'success' && Array.isArray(prodRes.data.products);
        if (productsOk) {
            S.products = prodRes.data.products;
            S.load = { state: 'ready', error: '', detail: '', queueError: '' };
        } else if (S.products.length && opts.quiet) {
            S.load.queueError = '';
        } else {
            const e = productsError(prodRes);
            S.load = { state: 'error', error: e.error, detail: e.detail, queueError: '' };
        }
        const q = queueRes.data || {};
        if (queueRes.ok && (q.status === 'success' || q.status === 'no_queue')) {
            S.queue = q;
        } else {
            S.queue = { status: 'unavailable', rows: [], ready_for_review: null };
            S.load.queueError = R.plainError(q.error, 'ما قدرنا نقرأ حالة طابور التشغيل.');
        }
        if (productsOk) settleLocalFlags();
        maybeBackfillExplain(q);
        rebuild();
        if (productsOk) refreshOpenIdentity();
        detectMoved();
        if (!S.seeded) {
            S.seeded = true;
            chooseInitial();
        }
        renderAll();
    }

    // صفوف حُفظت قبل أن يحسب العامل سبب «بلا اقتراح» (queue-state: explain_missing): يُطلب حسابه مرة في الجلسة مما
    // حُفظ (بلا بحث ولا تكلفة)، وتُقرأ القائمة بهدوء بعده. فشله لا يوقف شيئاً: المنتج يعرض جملة عامة صادقة
    function maybeBackfillExplain(q) {
        if (S.explainBackfill || !q || !(parseInt(q.explain_missing, 10) > 0) || !S.urls.explainBackfill) return;
        S.explainBackfill = true;
        R.requestJson(S.urls.explainBackfill, { method: 'POST', body: {} }).then(res => {
            if (res && res.ok && res.data && parseInt(res.data.filled, 10) > 0) loadData({ quiet: true });
        }).catch(() => {});
    }

    // حالات هذه الجلسة التي لحقتها قاعدة البيانات تُزال (القراءة الجديدة تقول الشيء نفسه)
    function settleLocalFlags() {
        const probe = R.buildItems(S.products, S.queue, null);
        const byKey = new Map(probe.map(it => [it.key, it]));
        S.local.forEach((flag, key) => {
            const it = byKey.get(key);
            if (!it) return;
            if (flag === 'approved' && it.base === 'approved') S.local.delete(key);
            if ((flag === 'rejected' || flag === 'requeued') && ['requeued', 'queued', 'searching'].includes(it.base)) S.local.delete(key);
        });
    }

    // المنتج المفتوح تغيّر في الشيت (هوية مختلفة بنفس الصف والاسم): نتائجه القديمة لا تُعرض ولا تُعتمد له
    function refreshOpenIdentity() {
        if (!S.openKey || !S.open) return;
        const it = S.byKey.get(S.openKey);
        if (!it) return;
        const fresh = R.productIdentity(it.product);
        if (!fresh.sku_key && S.open.sku_key) fresh.sku_key = S.open.sku_key;      // المفتاح الذي عُرف من استجابة البحث
        if (R.sameProduct(fresh, S.open) && fresh.barcode === S.open.barcode && fresh.size === S.open.size) return;
        R.single.cancelPendingSearch();
        S.open = fresh;
        S.session.delete(S.openKey);
        R.toast('بيانات هالمنتج تغيّرت بالشيت، فعرضناه من جديد.', 'info');
    }

    // -------------------------------------------------------------------------------------------------
    // C1: what the reviewer was shown (expected_state) is a snapshot, never refreshed by a quiet reload
    // -------------------------------------------------------------------------------------------------

    function snapshot(item) {
        if (!item) return;
        const prev = S.seen.get(item.key);
        // إجراء المراجع نفسه أُجيب ولم تأتِ القراءة التالية بعد: ما قاله الخادم (current) أدق مما في الصفحة
        if (prev && prev.rebase) return;
        // expected: بعد اعتماد في هذه الجلسة ما قاله الخادم عنه (current؛ رابطه قد لا يكون في الشيت بعد)، وإلا ما تعرضه
        // الصفحة. view: ما في بيانات الصفحة وحدها، تُقارن به القراءات التالية
        const approved = S.approved.get(item.key);
        const expected = approved && approved.current ? R.expectedFromCurrent(approved.current)
            : R.expectedState(item, approved && approved.link);
        S.seen.set(item.key, { expected: expected, view: R.expectedState(item), rebase: false, known: queueKnown() });
        S.moved.delete(item.key);
    }
    R.snapshot = snapshot;

    function seenExpected(item) {
        if (!S.seen.has(item.key)) snapshot(item);
        return Object.assign({}, S.seen.get(item.key).expected);
    }
    R.seenExpected = seenExpected;

    // بعد إجراء المراجع نفسه على المنتج (اعتماد، رفع، رفض، إعادة للطابور): ما قاله الخادم (current) هو ما يُرسل مع
    // الاعتماد التالي، والقراءة التالية تُؤخذ كما هي (rebase) ولا تُعد «تغيّر بعد فتحه»
    function settleSeen(key, current) {
        const s = S.seen.get(key);
        const expected = current && typeof current === 'object' ? R.expectedFromCurrent(current) : (s ? s.expected : null);
        S.seen.set(key, { expected: expected || {}, view: s ? s.view : null, rebase: true, known: false });
        S.moved.delete(key);
    }
    R.settleSeen = settleSeen;

    function ownActionInFlight(key) {
        const flag = S.local.get(key);
        const sess = S.session.get(key);
        return !!((S.jobs && S.jobs.has(key)) || flag === 'approving' || flag === 'rejecting' || (sess && sess.rejecting));
    }

    // بعد كل قراءة: منتج عُرض للمراجع وتغيّر صف طابوره أو صورته المعتمدة منذ ذلك (مراجع آخر، أو العامل) يُعلَّم، فلا
    // يُعتمد قبل أن يُعرض من جديد
    function queueKnown() {
        return !!(S.queue && (S.queue.status === 'success' || S.queue.status === 'no_queue'));
    }

    function detectMoved() {
        // طابور التشغيل لم يُقرأ هذه المرة (عطل مؤقت): لا يُحكم على أي منتج بأنه تغيّر
        if (!queueKnown()) return;
        S.seen.forEach((s, key) => {
            const it = S.byKey.get(key);
            if (!it || ownActionInFlight(key)) return;
            const view = R.expectedState(it);
            if (s.rebase || !s.view || !s.known) {
                s.view = view;
                s.rebase = false;
                s.known = true;
                return;
            }
            if (!R.sameExpected(s.view, view)) S.moved.set(key, { before: s.view, after: view });
            else S.moved.delete(key);
        });
    }
    R.detectMoved = detectMoved;

    function rebuild() {
        // الشيت ما انقرأ: صفوف الطابور وحدها لا تُعرض كأنها «مش موجودة بالشيت»
        const sheetUnread = S.load.state === 'error' && !S.products.length;
        S.items = sheetUnread ? [] : R.buildItems(S.products, S.queue, S.local);
        S.byKey = new Map(S.items.map(it => [it.key, it]));
        S.counts = R.countBuckets(S.items);
    }
    R.rebuild = rebuild;

    function chooseInitial() {
        const c = S.counts;
        let filter = S.cfg.filter && R.FILTERS.some(f => f.key === S.cfg.filter) ? S.cfg.filter : null;
        // ?reason=no_size: منتجات هذا السبب في «الكل» (بلا اقتراح وما انلقت معاً) إلا إذا حُددت رقاقة
        if (S.cfg.reason && /^[a-z_]{1,40}$/.test(String(S.cfg.reason))) {
            S.reason = String(S.cfg.reason);
            if (!filter) filter = 'all';
        }
        if (!filter) {
            filter = c.proposed ? 'proposed' : c.none ? 'none' : c.bg_failed ? 'bg_failed' : c.not_found ? 'not_found'
                : c.failed ? 'failed' : 'all';
        }
        S.filter = filter;
        let key = null;
        if (S.cfg.row) {
            const it = S.items.find(x => parseInt(x.product.row_number, 10) === S.cfg.row && !x.orphan)
                || S.items.find(x => parseInt(x.product.row_number, 10) === S.cfg.row);
            if (it) {
                key = it.key;
                if (!R.filterItems([it], S.filter, '', null, S.reason).length) {
                    S.filter = 'all';
                    S.reason = '';
                }
            } else if (S.load.state === 'ready') {
                R.toast(`ما لقينا الصف ${S.cfg.row} بالشيت.`, 'warning');
            }
        }
        if (!key) {
            const first = visibleItems()[0];
            key = first ? first.key : null;
        }
        if (S.mode === 'single' && key) R.single.openItem(key, { from: 'initial', replaceUrl: !!S.cfg.row });
        else if (key) S.openKey = key;
    }

    // -------------------------------------------------------------------------------------------------
    // Queue column
    // -------------------------------------------------------------------------------------------------

    function visibleItems() {
        return R.filterItems(S.items, S.filter, S.query, S.keep, S.reason);
    }
    R.visibleItems = visibleItems;

    function setFilter(key) {
        if (!R.FILTERS.some(f => f.key === key)) return;
        if (S.filter !== key) {
            S.keep = new Set();
            S.reason = '';
        }
        S.filter = key;
        S.listLimit = LIST_PAGE;
        updateUrl(false);
        renderList();
        const list = visibleItems();
        if (S.mode === 'single' && list.length && !list.some(it => it.key === S.openKey)) {
            R.single.openItem(list[0].key, { from: 'filter' });
        } else {
            R.single.updateBar();
        }
    }
    R.setFilter = setFilter;

    // رقاقة «السبب»: الضغط عليها يعرض منتجاتها فقط، والضغط مرة ثانية (أو «كل الأسباب») يلغيها
    function setReason(key) {
        key = String(key || '');
        if (key && !/^[a-z_]{1,40}$/.test(key)) return;
        S.reason = key && key !== S.reason ? key : '';
        S.listLimit = LIST_PAGE;
        updateUrl(false);
        renderList();
        const list = visibleItems();
        if (S.mode === 'single' && list.length && !list.some(it => it.key === S.openKey)) {
            R.single.openItem(list[0].key, { from: 'filter' });
        } else {
            R.single.updateBar();
        }
    }
    R.setReason = setReason;

    function thumbFor(item) {
        const p = item.product;
        const approved = S.approved.get(item.key);
        const sel = R.storedSelected(p);
        const first = R.storedCandidates(p)[0];
        const url = (approved && approved.link) || (sel && sel.url) || (first && first.url)
            || (R.hasFinalImage(p) ? (p.existing_image_link || p.cached_image) : '');
        return url ? R.img(url, '', S.urls.imageProxy) : icon('image', 22, 1.6, 'rv-thumb__icon');
    }

    function chipFor(bucket, small) {
        const b = R.BUCKET_LABELS[bucket] || R.BUCKET_LABELS.idle;
        return el('span', { className: `lq-chip lq-chip--${b.chip}${small ? ' lq-chip--sm' : ''} rv-chip`, text: b.text });
    }
    R.chipFor = chipFor;

    function listItem(item) {
        const p = item.product;
        const size = R.sizeText(p.size);
        // سبب «بلا اقتراح» بكلمتين تحت الاسم (الجملة كاملة في مساحة العمل)
        const why = R.NO_PICK_BUCKETS.includes(item.bucket) ? R.noPickReason(item) : null;
        const active = item.key === S.openKey;
        const btn = el('button', { type: 'button', className: 'rv-item' + (active ? ' is-active' : ''), dataset: { key: item.key },
                                   'aria-current': active ? 'true' : null }, [
            el('span', { className: 'rv-thumb' }, [thumbFor(item)]),
            el('span', { className: 'rv-item__text' }, [
                bdi(p.product_name || p.product_name_ar || 'بلا اسم', 'rv-item__name'),
                el('span', { className: 'rv-item__meta' }, [
                    el('span', { text: `صف ${p.row_number}` }),
                    size ? el('span', { className: 'rv-item__dot', 'aria-hidden': 'true', text: '•' }) : null,
                    size ? el('span', { text: size }) : null
                ]),
                why ? el('span', { className: 'rv-item__why', title: why.key, text: why.label }) : null
            ]),
            chipFor(item.bucket, true)
        ]);
        return el('li', {}, [btn]);
    }

    // رقاقات «السبب» تحت رقاقات القائمة: أسباب «بلا اقتراح» (وما ينقص الشيت) لمنتجات الرقاقة الحالية بعددها، حتى
    // يصلح المالك مجموعة كاملة مرة واحدة (مثلاً كل «حجم ناقص بالشيت»)
    function drawReasonChips(hide) {
        const d = S.dom;
        if (!d.reasonChips) return;
        clear(d.reasonChips);
        const counts = hide ? [] : R.reasonCounts(R.filterItems(S.items, S.filter, '', S.keep));
        if (S.reason && !counts.some(c => c.key === S.reason)) counts.push({ key: S.reason, count: 0, label: R.noPickLabel(S.reason) });
        d.reasonChips.hidden = !counts.length;
        if (!counts.length) return;
        d.reasonChips.appendChild(el('span', { className: 'rv-reasons-filter__label', text: 'السبب:' }));
        d.reasonChips.appendChild(el('button', { type: 'button', className: 'lq-filter rv-filter rv-reason-chip', dataset: { nopickReason: '' },
                                                  'aria-pressed': S.reason ? 'false' : 'true' }, [el('span', { text: 'كل الأسباب' })]));
        counts.forEach(c => d.reasonChips.appendChild(el('button', {
            type: 'button', className: 'lq-filter rv-filter rv-reason-chip', dataset: { nopickReason: c.key }, title: c.key,
            'aria-pressed': S.reason === c.key ? 'true' : 'false'
        }, [el('span', { text: c.label }), el('span', { className: 'lq-filter__count', text: String(c.count) })])));
    }

    const EMPTY_FILTER_TEXT = {
        all: 'ما في منتجات بالشيت.',
        proposed: 'ما في صور مقترحة بانتظارك.',
        warning: 'ما في صور مقترحة فيها تحذير.',
        none: 'ما في منتجات بلا اقتراح.',
        not_found: 'ما في منتجات ما انلقت صورتها.',
        failed: 'ما في أعطال مسجلة.',
        bg_failed: 'لا توجد صور معتمدة لم تُعزل خلفيتها.'
    };

    // القائمة، ثم «N من M» في مساحة العمل (يتغير مع الفلتر والبحث وحالة المنتجات)
    function renderList() {
        drawList();
        if (R.single && typeof R.single.updatePosition === 'function') R.single.updatePosition();
    }

    function drawList() {
        const d = S.dom;
        if (!d.list) return;
        const c = S.counts;
        const loading = S.load.state === 'loading';
        // المنتجات ما انقرأت: لا رقم يُعرض (لا صفر ولا تقدير)، بل «—»
        const unread = S.load.state === 'error' && !S.products.length;
        const queueKnown = S.queue && S.queue.status !== 'unavailable';
        d.waiting.textContent = loading ? 'لحظة…' : (unread ? '—'
            : `${c.waiting} بانتظار المراجعة${queueKnown ? '' : ' (تقدير)'}`);
        d.refreshBtn.disabled = loading || S.load.state === 'reloading';

        clear(d.filters);
        R.FILTERS.forEach(f => {
            // الرقاقة الحالية تعدّ ما تعرضه القائمة: ومعه منتجات بقيت فيها بعد الرفض (S.keep)
            const count = f.key === S.filter && S.keep.size ? R.filterItems(S.items, f.key, '', S.keep).length
                : (f.key === 'all' ? c.all : c[f.key]);
            d.filters.appendChild(el('button', { type: 'button', className: 'lq-filter rv-filter', dataset: { filter: f.key },
                                                 'aria-pressed': S.filter === f.key ? 'true' : 'false' }, [
                el('span', { text: f.label }),
                el('span', { className: 'lq-filter__count', text: loading ? '…' : unread ? '—' : String(count || 0) })
            ]));
        });

        drawReasonChips(loading || unread);

        clear(d.queueNote);
        if (S.load.queueError) {
            d.queueNote.appendChild(el('div', { className: 'lq-alert lq-alert--warning rv-note' }, [
                icon('alert', 16, 2, 'lq-alert__icon'),
                el('div', { className: 'lq-alert__body', text: unread ? S.load.queueError
                    : `${S.load.queueError} الأرقام محسوبة من الصور المحفوظة، وممكن تختلف عن الشارة.` })
            ]));
        }
        if (S.runDiffShown) {
            d.queueNote.appendChild(el('div', { className: 'lq-alert lq-alert--info rv-note' }, [
                icon('info', 16, 2, 'lq-alert__icon'),
                el('div', { className: 'lq-alert__body', text: 'وصلت منتجات جديدة للمراجعة من التشغيل.' }),
                el('button', { type: 'button', className: 'lq-alert__action rv-linkbtn', text: 'حدّث القائمة', onclick: () => {
                    S.runDiffShown = false;
                    S.runDiff = 0;
                    loadData({});
                } })
            ]));
        }
        if (['failed', 'not_found'].includes(S.filter) && S.load.state === 'ready') {
            const failing = visibleItems().filter(it => it.product.has_error && !it.orphan && !S.local.get(it.key));
            if (failing.length > 1) {
                d.queueNote.appendChild(el('div', { className: 'rv-retry-all' }, [
                    el('span', { text: 'إعادة المحاولة بترجّعها للطابور، وما بتبلش معالجتها لحالها.' }),
                    el('button', { type: 'button', className: 'lq-btn lq-btn--secondary lq-btn--sm',
                                   text: `رجّع ${plural(failing.length, 'منتج واحد', 'منتجات')} للطابور`,
                                   onclick: () => R.single.retryFailures(failing) })
                ]));
            }
        }

        clear(d.list);
        d.list.setAttribute('aria-busy', loading ? 'true' : 'false');
        if (loading) {
            for (let i = 0; i < 6; i++) {
                d.list.appendChild(el('li', { className: 'rv-item rv-item--skeleton', 'aria-hidden': 'true' }, [
                    el('span', { className: 'lq-skeleton lq-skeleton--thumb' }),
                    el('span', { className: 'rv-item__text' }, [el('span', { className: 'lq-skeleton lq-skeleton--text' }),
                                                                 el('span', { className: 'lq-skeleton lq-skeleton--short' })])
                ]));
            }
            return;
        }
        if (S.load.state === 'error' && !S.products.length) {
            d.list.appendChild(el('li', { className: 'rv-list__state' }, [
                el('div', { className: 'lq-alert lq-alert--danger', role: 'alert' }, [
                    icon('alert', 18, 2, 'lq-alert__icon'),
                    el('div', { className: 'lq-alert__body' }, [
                        el('strong', { className: 'lq-alert__title', text: 'ما انفتحت القائمة: ' }),
                        el('span', { text: S.load.error }),
                        S.load.detail ? el('details', { className: 'rv-details' }, [el('summary', { text: 'التفاصيل التقنية' }),
                                                                                   el('code', { dir: 'ltr', text: S.load.detail })]) : null
                    ])
                ]),
                el('button', { type: 'button', className: 'lq-btn lq-btn--secondary lq-btn--sm', text: 'جرّب مرة ثانية',
                               onclick: () => loadData({}) })
            ]));
            return;
        }
        const list = visibleItems();
        if (!list.length) {
            const text = S.query ? `ما في نتائج لـ «${S.query.trim()}» بهالفلتر.`
                : S.reason ? `ما في منتجات سببها «${R.noPickLabel(S.reason)}» بهالرقاقة.` : EMPTY_FILTER_TEXT[S.filter];
            d.list.appendChild(el('li', { className: 'rv-list__state' }, [
                el('p', { className: 'rv-list__empty', text: text }),
                S.filter !== 'all' && S.query ? el('button', { type: 'button', className: 'lq-btn lq-btn--ghost lq-btn--sm',
                                                               text: 'ابحث بالكل', onclick: () => setFilter('all') }) : null
            ]));
            return;
        }
        list.slice(0, S.listLimit).forEach(it => d.list.appendChild(listItem(it)));
        if (list.length > S.listLimit) {
            d.list.appendChild(el('li', { className: 'rv-list__more' }, [
                el('button', { type: 'button', className: 'lq-btn lq-btn--ghost lq-btn--sm', dataset: { more: '1' },
                               text: `اعرض ${Math.min(LIST_PAGE, list.length - S.listLimit)} كمان (من ${list.length})` })
            ]));
        }
    }
    R.renderList = renderList;

    // يضمن أن عنصر المنتج المفتوح مرسوم في القائمة (التنقل بالأسهم بعد أول 150)
    function ensureListed(key) {
        const list = visibleItems();
        const idx = list.findIndex(it => it.key === key);
        if (idx >= S.listLimit) {
            S.listLimit = Math.ceil((idx + 1) / LIST_PAGE) * LIST_PAGE;
            return true;
        }
        return false;
    }
    R.ensureListed = ensureListed;

    function markActive() {
        const d = S.dom;
        if (!d.list) return;
        d.list.querySelectorAll('.rv-item').forEach(node => {
            const on = node.getAttribute('data-key') === S.openKey;
            node.classList.toggle('is-active', on);
            if (on) {
                node.setAttribute('aria-current', 'true');
                if (typeof node.scrollIntoView === 'function') node.scrollIntoView({ block: 'nearest' });
            } else {
                node.removeAttribute('aria-current');
            }
        });
    }
    R.markActive = markActive;

    // -------------------------------------------------------------------------------------------------
    // Background approvals panel
    // -------------------------------------------------------------------------------------------------

    let jobsHideTimer = null;

    function renderJobs(st) {
        const box = S.dom.jobs;
        if (!box) return;
        clearTimeout(jobsHideTimer);
        clear(box);
        if (!st || !st.jobs.length) {
            box.hidden = true;
            return;
        }
        box.hidden = false;
        box.classList.toggle('has-failures', st.failedAll > 0);
        const onlyApprovals = st.batchJobs.every(j => j.type !== 'reject');
        const n = st.total;
        if (st.busy) {
            const what = onlyApprovals ? `جاري اعتماد ${plural(n, 'صورة وحدة', 'صور')} بالخلفية` : `جاري تنفيذ ${plural(n, 'طلب واحد', 'طلبات')} بالخلفية`;
            box.appendChild(el('div', { className: 'rv-jobs__head' }, [
                el('span', { className: 'lq-spinner', 'aria-hidden': 'true' }),
                el('span', { className: 'rv-jobs__text' }, [
                    `${what} · `, el('strong', { text: `${st.settled} من ${n}` }), ' جاهزة. بتقدر تكمل شغلك.',
                    st.failed ? el('span', { className: 'rv-jobs__bad', text: ` · ${st.failed} ما مشيت` }) : null
                ])
            ]));
        } else if (!st.failedAll) {
            box.appendChild(el('div', { className: 'rv-jobs__head' }, [
                el('span', { className: 'rv-jobs__ok' }, [icon('check', 18, 2.2)]),
                el('span', { className: 'rv-jobs__text', text: 'جاهزة. بتقدر تكمل شغلك.' }),
                el('button', { type: 'button', className: 'rv-jobs__close', 'aria-label': 'إغلاق', onclick: () => S.jobs.dismiss() }, [icon('x', 16, 2)])
            ]));
            jobsHideTimer = setTimeout(() => {
                if (!S.jobs.busy() && !S.jobs.state().failedAll) S.jobs.dismiss();
            }, 6000);
        } else {
            // اعتماد أو رفع ما مشي: «فحص النشر» بصفحة الصحة بيجرّب سلسلة النشر كاملة على صورة تجريبية ويقول وين وقفت
            const publishFailed = st.jobs.some(j => j.state === 'failed' && j.type !== 'reject');
            // رصيد أو مفتاح أو حصة PhotoRoom / remove.bg فشّل العزل (R.bgSkipCode): كل اعتماد رح يفشل بنفس الشكل، فاللوحة
            // بتعرض «تجاوز عزل الخلفية» (نفس زر صفحة الصحة). عزل الخلفية متوقف: «أعد المحاولة» بينشرها متل ما هي
            const bgFailed = st.jobs.some(j => j.state === 'failed' && j.type !== 'reject' && R.bgSkipCode(j.detail));
            const bg = S.cfg.bg && typeof S.cfg.bg === 'object' ? S.cfg.bg : {};
            const bgOff = bgFailed && bg.method === 'none';
            box.appendChild(el('div', { className: 'rv-jobs__head' }, [
                el('span', { className: 'rv-jobs__warn' }, [icon('alert', 18, 2)]),
                el('span', { className: 'rv-jobs__text', text: `خلصت: ${st.done} مشيت، و${plural(st.failedAll, 'وحدة ما مشيت', 'ما مشيت')}:` }),
                bgFailed && bg.method && !bgOff && S.urls.bgMethod
                    ? el('button', { type: 'button', className: 'lq-btn lq-btn--danger lq-btn--sm rv-jobs__skipbg', text: 'تجاوز عزل الخلفية…',
                                     title: 'الصور بتنتشر متل ما هي على لوحة بيضا لحد ما ترجّع عزل الخلفية', disabled: !!S.bgSaving,
                                     onclick: () => R.single.confirmBgSkip() })
                    : null,
                bgOff ? el('span', { className: 'rv-jobs__bgoff', text: 'عزل الخلفية متوقف هلق: «أعد المحاولة» بينشرها متل ما هي.' }) : null,
                publishFailed
                    ? el('a', { className: 'lq-btn lq-btn--secondary lq-btn--sm rv-jobs__check', href: S.urls.publishCheck,
                                title: 'بيجرّب النشر كامل على صورة تجريبية وبيقلك وين وقف وشو تعمل', text: 'افحص النشر' })
                    : null,
                el('button', { type: 'button', className: 'rv-jobs__close', 'aria-label': 'إغلاق', onclick: () => S.jobs.dismiss() }, [icon('x', 16, 2)])
            ]));
        }
        const failed = st.jobs.filter(j => j.state === 'failed');
        if (failed.length) {
            box.appendChild(el('ul', { className: 'rv-jobs__list' }, failed.map(j => el('li', { className: 'rv-jobs__item' }, [
                el('span', { className: 'rv-jobs__what' }, [
                    el('span', { text: j.type === 'reject' ? 'ما انرفضت: ' : 'ما انعتمدت: ' }),
                    bdi(j.label || `صف ${j.row}`, 'rv-jobs__name'),
                    el('span', { className: 'rv-jobs__why', title: j.detail || null, text: ` — ${j.error}` })
                ]),
                // تغيّر المنتج بعد فتح الصفحة (C1): الإعادة كما هي تُرفض مرة أخرى؛ الاستبدال بتأكيد صريح فقط. صورة رفضها
                // مراجع آخر لا تُستبدل ولا تُعاد (الخادم يرفضها دائماً): لا زر
                // فحص القص (quality): علامات العرض تُنشر رغمها بتأكيد صريح فقط؛ عزل فشل لا يُنشر ولا يُعاد
                j.quality
                    ? (j.quality.allowed
                        ? el('button', { type: 'button', className: 'lq-btn lq-btn--danger lq-btn--sm rv-jobs__anyway', text: 'انشرها رغم ذلك…',
                                         disabled: S.jobs.has(j.key), onclick: () => R.single.confirmPublishAnyway(j) })
                        : null)
                    : j.stale
                    ? (j.stale.replaceable === false ? null
                        : el('button', { type: 'button', className: 'lq-btn lq-btn--danger lq-btn--sm rv-jobs__replace', text: 'استبدال المعتمدة…',
                                         disabled: S.jobs.has(j.key), onclick: () => R.single.confirmReplace(j) }))
                    : el('button', { type: 'button', className: 'lq-btn lq-btn--secondary lq-btn--sm', text: 'أعد المحاولة',
                                     disabled: S.jobs.has(j.key), onclick: () => S.jobs.retry(j.id) })
            ]))));
        }
        R.single.updateJobsOffset();
    }

    function setupJobs() {
        S.jobs = R.createJobQueue({
            send: job => R.single.sendJob(job),
            onSettle: job => R.single.settleJob(job),
            onDrain: () => loadData({ quiet: true }),
            canRetry: job => S.local.get(job.key) !== 'approved',
            // طلب يُعاد: منتجه «جاري الاعتماد» (أو «جاري الرفض») من جديد، فلا يظهر بانتظار المراجعة وهو عم ينعتمد
            onRetry: job => {
                S.local.set(job.key, job.type === 'reject' ? 'rejecting' : 'approving');
                rebuild();
                if (S.mode === 'single') {
                    renderList();
                    if (S.openKey === job.key) R.single.renderWorkspace();
                    else R.single.updateBar();
                    markActive();
                } else {
                    renderList();
                    R.bulk.render();
                }
            },
            onUpdate: renderJobs
        });
        root.addEventListener('beforeunload', e => {
            if (S.jobs && S.jobs.busy()) {
                e.preventDefault();
                e.returnValue = 'في صور عم تنعتمد بالخلفية. إذا طلعت هلق بتوقف.';
                return e.returnValue;
            }
            return undefined;
        });
    }

    // -------------------------------------------------------------------------------------------------
    // Modes and URL
    // -------------------------------------------------------------------------------------------------

    function currentParams() {
        const params = new URLSearchParams();
        if (S.mode === 'bulk') params.set('mode', 'bulk');
        else if (S.openKey && S.byKey.get(S.openKey)) params.set('row', String(S.byKey.get(S.openKey).product.row_number));
        if (S.mode === 'single' && S.filter) params.set('filter', S.filter);
        if (S.mode === 'single' && S.reason) params.set('reason', S.reason);
        return params;
    }

    function updateUrl(push) {
        if (!root.history || typeof root.history.replaceState !== 'function') return;
        const qs = currentParams().toString();
        const url = (root.location && root.location.pathname ? root.location.pathname : S.urls.page) + (qs ? '?' + qs : '');
        try {
            if (push) root.history.pushState({ rv: 1 }, '', url);
            else root.history.replaceState({ rv: 1 }, '', url);
        } catch (e) {
            // file:// or a sandbox without history: the page still works
        }
    }
    R.updateUrl = updateUrl;

    function setMode(mode, opts) {
        opts = opts || {};
        if (!['single', 'bulk'].includes(mode)) return;
        S.mode = mode;
        const d = S.dom;
        d.root.setAttribute('data-mode', mode);
        d.single.hidden = mode !== 'single';
        d.bulk.hidden = mode !== 'bulk';
        R.single.closeReasons();
        if (mode === 'single') {
            const key = opts.key || S.openKey || (visibleItems()[0] || {}).key;
            if (opts.key && !visibleItems().some(it => it.key === opts.key)) S.filter = 'all';
            renderList();
            if (key) R.single.openItem(key, { from: 'mode', noUrl: true });
            else R.single.renderWorkspace();
        } else {
            R.bulk.render();
        }
        if (!opts.fromPop) updateUrl(true);
        R.single.updateJobsOffset();
        if (typeof root.scrollTo === 'function') root.scrollTo(0, 0);
    }
    R.setMode = setMode;

    function onPopState() {
        const params = new URLSearchParams(root.location.search);
        const mode = params.get('mode') === 'bulk' ? 'bulk' : 'single';
        const row = parseInt(params.get('row') || '', 10);
        const it = row ? S.items.find(x => parseInt(x.product.row_number, 10) === row) : null;
        setMode(mode, { fromPop: true, key: it ? it.key : null });
    }

    function renderAll() {
        renderList();
        if (S.mode === 'single') {
            R.single.renderWorkspace();
            markActive();
        } else {
            R.bulk.render();
        }
    }
    R.renderAll = renderAll;

    // -------------------------------------------------------------------------------------------------
    // A confirmation that shows images (replace an approval): resolves true / false
    // -------------------------------------------------------------------------------------------------

    let activeDialog = null;

    function askDialog(opts) {
        opts = opts || {};
        if (activeDialog) activeDialog.finish(false);
        const d = S.dom;
        return new Promise(resolve => {
            const dlg = {};
            dlg.finish = ok => {
                if (activeDialog !== dlg) return;
                activeDialog = null;
                clear(d.dialog);
                d.dialog.hidden = true;
                resolve(!!ok);
            };
            activeDialog = dlg;
            const yes = el('button', { type: 'button', id: 'rvAskConfirm', text: opts.confirmText || 'متأكد',
                                       className: 'lq-btn ' + (opts.danger ? 'lq-btn--danger-solid' : 'lq-btn--primary'),
                                       onclick: () => dlg.finish(true) });
            const no = el('button', { type: 'button', id: 'rvAskCancel', className: 'lq-btn lq-btn--secondary',
                                      text: opts.cancelText || 'إلغاء', onclick: () => dlg.finish(false) });
            const images = (opts.images || []).filter(im => im && im.url);
            clear(d.dialog);
            d.dialog.appendChild(el('div', { className: 'rv-dialog', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'rvAskTitle' }, [
                el('h2', { className: 'rv-dialog__title', id: 'rvAskTitle', text: opts.title || '' }),
                images.length ? el('div', { className: 'rv-dialog__images' }, images.map(im => el('figure', { className: 'rv-dialog__figure' }, [
                    el('div', { className: 'rv-dialog__img' }, [R.img(im.url, im.caption || '', S.urls.imageProxy)]),
                    el('figcaption', { className: 'rv-dialog__cap' }, [bdi(im.caption || '', null, 'auto')])
                ]))) : null,
                el('p', { className: 'rv-dialog__text', text: opts.text || '' }),
                el('div', { className: 'rv-dialog__actions' }, [yes, no])
            ]));
            d.dialog.hidden = false;
            if (typeof no.focus === 'function') no.focus();
        });
    }
    R.askDialog = askDialog;

    function closeAskDialog() {
        if (!activeDialog) return false;
        activeDialog.finish(false);
        return true;
    }
    R.closeAskDialog = closeAskDialog;
    R.dialogActive = () => !!activeDialog;

    // -------------------------------------------------------------------------------------------------
    // Keyboard: ↑ ↓ move, 1–9 select (never publish), Enter approves the visible selected image, X reject, S skip.
    // Bulk mode (bulk.js onKey): arrows move between cards, Space ticks the focused card, A approves it (after its
    // warnings), Shift+A is the «approve the pre-selected ones without a warning» button.
    // Ctrl / Cmd / Alt combinations and typing in a field are left to the browser.
    // -------------------------------------------------------------------------------------------------

    function keyOf(e) {
        const code = String(e.code || '');
        if (/^Key[A-Z]$/.test(code)) return code.slice(3).toLowerCase();
        if (/^(Digit|Numpad)[0-9]$/.test(code)) return code.slice(-1);
        const k = String(e.key || '');
        if (/^[٠-٩]$/.test(k)) return String('٠١٢٣٤٥٦٧٨٩'.indexOf(k));
        return k.length === 1 ? k.toLowerCase() : k;
    }

    function onKeyDown(e) {
        if (!e || e.defaultPrevented) return;
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        const active = document.activeElement;
        const tag = active && active.tagName ? String(active.tagName).toLowerCase() : '';
        const inputType = tag === 'input' ? String(active.type || active.getAttribute('type') || 'text').toLowerCase() : '';
        const typing = (tag === 'input' && !['checkbox', 'radio', 'button', 'submit', 'reset', 'range', 'color', 'file'].includes(inputType))
            || tag === 'textarea' || tag === 'select' || !!(active && active.isContentEditable);
        const key = keyOf(e);
        if (key === 'Escape') {
            if (closeAskDialog()) return;
            if (R.bulk.closeDialog()) return;
            if (S.reasonsOpen) {
                R.single.closeReasons();
                return;
            }
            return;
        }
        if (typing || R.bulk.dialogOpen() || activeDialog) return;
        if (S.mode === 'bulk') {
            // مسافة أو Enter على زر أو رابط أو مربع تحديد يفعّله المتصفح نفسه
            if ((key === ' ' || key === 'Enter') && ['button', 'a', 'summary', 'label', 'input'].includes(tag)) return;
            // التكرار يُقبل للأسهم فقط: A المضغوط باستمرار لا يعتمد البطاقة التالية قبل أن يراها المراجع
            if (e.repeat && !/^Arrow/.test(key)) {
                if (key === ' ' || key === 'a') e.preventDefault();
                return;
            }
            if (R.bulk.onKey(key, e)) e.preventDefault();
            return;
        }
        if (S.mode !== 'single') return;
        // مفتاح مضغوط باستمرار يكرر نفسه: بعد الاعتماد يفتح المنتج التالي، فالتكرار كان يعتمده قبل ما يشوفه المراجع.
        // التكرار يُقبل للأسهم فقط (التنقل بالقائمة)، ولا يضغط زراً مركّزاً مرة ثانية
        if (e.repeat && key !== 'ArrowDown' && key !== 'ArrowUp') {
            if (key === 'Enter' || key === ' ' || /^[1-9xs]$/.test(key)) e.preventDefault();
            return;
        }
        if (S.reasonsOpen) {
            if (/^[1-9]$/.test(key)) {
                const reason = R.single.currentReasons()[parseInt(key, 10) - 1];
                if (reason) {
                    e.preventDefault();
                    R.single.rejectCurrent(reason.code);
                }
            }
            return;
        }
        if (key === 'ArrowDown' || key === 'ArrowUp') {
            e.preventDefault();
            R.single.move(key === 'ArrowDown' ? 1 : -1);
        } else if (key === 'Enter') {
            // Enter على زر أو رابط يفعّله المتصفح نفسه؛ لا نضيف فوقه اعتماداً
            if (['button', 'a', 'summary', 'label', 'input'].includes(tag)) return;
            if (R.single.canApprove()) {
                e.preventDefault();
                R.single.approveCurrent();
            }
        } else if (/^[1-9]$/.test(key)) {
            if (R.single.selectByNumber(parseInt(key, 10))) e.preventDefault();
        } else if (key === 'x') {
            if (R.single.canReject()) {
                e.preventDefault();
                R.single.openReasons();
            }
        } else if (key === 's') {
            e.preventDefault();
            R.single.skip();
        }
    }
    R.onKeyDown = onKeyDown;

    // الشارة في الشريط الجانبي تُقرأ كل 5 ثوانٍ: إذا زاد عدد الجاهز للمراجعة عن قائمتنا مرتين متتاليتين، نعرض «حدّث»
    function onRunStatus(n) {
        if (!n || n.reviewCount === null || n.reviewCount === undefined || S.load.state !== 'ready') return;
        if (S.jobs && S.jobs.busy()) {
            S.runDiff = 0;
            return;
        }
        const pendingLocal = Array.from(S.local.values()).filter(v => v === 'approving' || v === 'rejecting').length;
        if (n.reviewCount > S.counts.waiting + pendingLocal) {
            S.runDiff += 1;
            if (S.runDiff >= 2 && !S.runDiffShown) {
                S.runDiffShown = true;
                renderList();
            }
        } else {
            S.runDiff = 0;
            if (S.runDiffShown) {
                S.runDiffShown = false;
                renderList();
            }
        }
    }

    // -------------------------------------------------------------------------------------------------
    // Boot
    // -------------------------------------------------------------------------------------------------

    function readConfig(rootEl) {
        let cfg = {};
        try {
            cfg = JSON.parse(rootEl.getAttribute('data-config') || '{}') || {};
        } catch (e) {
            cfg = {};
        }
        S.cfg = cfg;
        S.urls = Object.assign({}, DEFAULT_URLS, cfg.urls || {});
        S.mode = cfg.mode === 'bulk' ? 'bulk' : 'single';
        S.cfg.canvas = parseInt(cfg.canvas, 10) || 800;
        S.cfg.autoSearchDelayMs = cfg.autoSearchDelayMs === undefined ? 700 : Math.max(0, parseInt(cfg.autoSearchDelayMs, 10) || 0);
        // الاعتماد بعد ظهور الصورة بهذه المدة على الأقل (ضغطة ثانية سريعة بعد الاعتماد لا تعتمد المنتج التالي قبل رؤيته)
        S.cfg.approveSettleMs = cfg.approveSettleMs === undefined ? 400 : Math.max(0, parseInt(cfg.approveSettleMs, 10) || 0);
    }

    function boot() {
        const rootEl = document.getElementById('rvApp');
        if (!rootEl || rootEl.getAttribute('data-booted') === '1') return false;
        rootEl.setAttribute('data-booted', '1');
        readConfig(rootEl);
        buildShell(rootEl);
        R.bulk.build();
        setupJobs();
        setMode(S.mode, { fromPop: true });
        document.addEventListener('keydown', onKeyDown);
        root.addEventListener('popstate', onPopState);
        root.addEventListener('resize', () => R.single.updateJobsOffset());
        if (root.Laqta && typeof root.Laqta.onRunStatus === 'function') root.Laqta.onRunStatus(onRunStatus);
        loadData({});
        return true;
    }

    Object.assign(R, { boot, loadData, renderJobs, setupJobs });

    if (!root.LAQTA_REVIEW_MANUAL_BOOT) {
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
        else boot();
    }
})(typeof window !== 'undefined' ? window : globalThis);
