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
        approvalJobs: '/api/approval-jobs',
        saveCandidates: '/api/v1/curation/save-candidates',
        selectCandidate: '/api/v1/curation/select-candidate',
        retry: '/api/failures/retry',
        imageProxy: '/api/image-proxy',
        export: '/rich-catalog/export',
        run: '/batch-automation',
        publishCheck: '/system-diagnostics#publish-check',
        bgMethod: '/api/settings/bg-method',
        reviewLanes: '/api/system/review-lanes'
    };

    const LIST_PAGE = 150;
    // «بالجملة» بيفتح لحاله لما يكون في هالعدد من الصور المقترحة بلا تحذير (والمراجع ما اختار وضع قبل)
    const BULK_DEFAULT_MIN = 10;
    // عرض الشاشة اللي تحته القائمة بتنزل تحت مساحة العمل (review.css @media (max-width: 980px))
    const STACK_PX = 980;
    // آخر وضع اختاره المراجع بزر «منتج واحد | بالجملة» (localStorage، لهالمتصفح بس)
    const MODE_STORE = 'laqta.review.mode';
    // طلبات الاعتماد اللي بتنبعت سوا: الخادم بيقفل كل منتج لحاله وقت الكتابة بالشيت (local_cache_db.sku_publish_lock)
    // وكل كتابة بالشيت بتتأكد من هوية صفها، فاعتمادين لمنتجين مختلفين ما بيتداخلوا
    const APPROVE_CONCURRENCY = 2;

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
        // bulk: selected / seen / loaded / failed / inView / unticked hold '<key>\n<pick url>' (a tick belongs to the picture).
        // The status filter is S.filter, shared with single mode (a mode switch keeps it)
        bulk: { brand: '', selected: new Set(), limit: 48, seeded: false, focus: null, autoTick: true,
                unticked: new Set(), seen: new Set(), loaded: new Set(), failed: new Set(), inView: new Set(), advancedAt: 0,
                everSeen: new Set(), anchor: null, startedAt: 0, reviewed: new Set() },
        // the mode came from the URL (?mode=) or the reviewer's own toggle: no automatic choice over it
        modeExplicit: false,
        modeChosen: false,
        // what the reviewer did today (this browser): the «all done» recap
        today: { approved: 0, rejected: 0 },
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

    // «منتج واحد | بالجملة»: نفس الزر برأس الوضعين؛ التبديل بيحفظ الفلتر والماركة، وبيتذكّر آخر وضع اختاره المراجع
    function modeToggle(current) {
        const item = (mode, label, iconName) => el('button', {
            type: 'button', className: 'lq-segmented__item rv-modes__item' + (mode === current ? ' is-active' : ''),
            dataset: { mode: mode }, 'aria-pressed': mode === current ? 'true' : 'false'
        }, [icon(iconName, 16), el('span', { text: label })]);
        const box = el('div', { className: 'lq-segmented lq-segmented--sm rv-modes', role: 'group', 'aria-label': 'طريقة العرض' }, [
            item('single', 'منتج واحد', 'image'), item('bulk', 'بالجملة', 'grid')
        ]);
        box.addEventListener('click', e => {
            const b = e.target && e.target.closest ? e.target.closest('[data-mode]') : null;
            if (b && b.getAttribute('data-mode') !== S.mode) chooseMode(b.getAttribute('data-mode'));
        });
        return box;
    }
    R.modeToggle = modeToggle;

    function buildShell(rootEl) {
        clear(rootEl);
        rootEl.removeAttribute('aria-busy');
        const d = S.dom;
        d.root = rootEl;

        d.wsBody = el('div', { className: 'rv-ws__body', id: 'rvWsBody' });
        d.reasons = el('div', { className: 'rv-reasons', role: 'group', 'aria-label': 'سبب الرفض', hidden: true });
        d.approveBtn = el('button', { type: 'button', className: 'lq-btn lq-btn--primary lq-btn--lg rv-approve', id: 'rvApprove',
                                      'aria-keyshortcuts': 'Enter', disabled: true },
                          [icon('check', 18, 2.2), el('span', { text: 'اعتماد' }), kbd('Enter')]);
        d.rejectBtn = el('button', { type: 'button', className: 'lq-btn lq-btn--danger lq-btn--lg rv-reject', id: 'rvReject',
                                     'aria-keyshortcuts': 'X', 'aria-expanded': 'false', 'aria-label': 'رفض', disabled: true },
                         [icon('x', 18, 2.2), el('span', { className: 'rv-reject__text', text: 'رفض' }), kbd('X')]);
        d.skipBtn = el('button', { type: 'button', className: 'lq-btn lq-btn--secondary lq-btn--lg rv-skip', id: 'rvSkip',
                                   'aria-keyshortcuts': 'S', disabled: true },
                       [el('span', { text: 'تخطي' }), kbd('S')]);
        d.nfBtn = el('button', { type: 'button', className: 'lq-btn lq-btn--ghost rv-nf-toggle', id: 'rvNotFoundToggle',
                                 'aria-expanded': 'false', disabled: true, text: 'ما لقيت الصورة الصحيحة؟' });
        // على الموبايل: الشريط سطر واحد (اعتماد + أيقونة رفض)، و«تخطي» و«ما لقيت الصورة الصحيحة؟» بقائمة «⋯»
        d.moreBtn = el('button', { type: 'button', className: 'lq-btn lq-btn--secondary lq-btn--lg rv-more', id: 'rvMore',
                                   'aria-haspopup': 'true', 'aria-expanded': 'false', 'aria-controls': 'rvMoreMenu', 'aria-label': 'المزيد',
                                   text: '⋯' });
        d.moreSkip = el('button', { type: 'button', className: 'rv-menu__item', role: 'menuitem', text: 'تخطي هالمنتج' });
        d.moreNf = el('button', { type: 'button', className: 'rv-menu__item', role: 'menuitem', text: 'ما لقيت الصورة الصحيحة؟' });
        d.moreMenu = el('div', { className: 'rv-menu', id: 'rvMoreMenu', role: 'menu', hidden: true }, [d.moreSkip, d.moreNf]);
        d.nextName = bdi('', 'rv-next__name');
        d.nextHint = el('span', { className: 'rv-next__hint rv-keyhint', text: 'بعد الاعتماد بننتقل تلقائياً للمنتج التالي' });
        d.bar = el('div', { className: 'lq-actionbar rv-actionbar', id: 'rvActionbar' }, [
            d.reasons,
            el('div', { className: 'rv-actionbar__row' }, [
                d.approveBtn, d.rejectBtn, d.skipBtn, d.nfBtn,
                el('span', { className: 'rv-more__wrap' }, [d.moreBtn, d.moreMenu]),
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
        // «اعتمادات ما زبطت» (renderFailed): فاضية ومخفية لحد ما يكون في شي
        d.failed = el('section', { className: 'rv-failed', id: 'rvFailed', role: 'region', 'aria-label': 'اعتمادات ما زبطت', hidden: true });
        d.queue = el('aside', { className: 'rv-queue', 'aria-label': 'قائمة المراجعة' }, [
            el('div', { className: 'rv-queue__head' }, [
                el('div', { className: 'rv-queue__title' }, [
                    el('h1', { className: 'rv-queue__h1', text: 'قائمة المراجعة' }), d.waiting, d.refreshBtn
                ]),
                modeToggle('single'),
                el('label', { className: 'lq-search rv-search' }, [icon('search', 18), el('span', { className: 'lq-sr-only', text: 'بحث بالقائمة' }), d.search]),
                d.filters,
                d.reasonChips,
                d.queueNote,
                // «فحص القص»: الصور المنشورة على الغامق والفاتح والمربعات (RecutController)
                S.urls.cutoutCheck ? el('a', { className: 'lq-link', href: S.urls.cutoutCheck,
                                               text: 'فحص القص: الصور المنشورة على الغامق والفاتح' }) : null
            ]),
            d.failed,
            d.list,
            el('div', { className: 'rv-queue__foot rv-keyhint' }, [el('span', { text: '↑ ↓ للتنقل بين المنتجات · Z لتكبير الصورة · ? للاختصارات' })])
        ]);
        d.single = el('div', { className: 'rv-single' }, [d.ws, d.queue]);

        d.bulk = el('section', { className: 'rv-bulk', 'aria-label': 'المراجعة بالجملة', hidden: true });
        d.jobs = el('div', { className: 'rv-jobs', id: 'rvJobs', role: 'status', 'aria-live': 'polite', hidden: true });
        // «تراجع» عن اعتماد لسا ما انبعت (jobs.js holdMs): فوق لوحة الاعتمادات بنفس المكان
        d.undo = el('div', { className: 'rv-undo', id: 'rvUndo', role: 'status', 'aria-live': 'polite', hidden: true });
        d.float = el('div', { className: 'rv-float' }, [d.undo, d.jobs]);
        d.dialog = el('div', { className: 'rv-dialog-backdrop', id: 'rvDialog', hidden: true });

        rootEl.appendChild(d.single);
        rootEl.appendChild(d.bulk);
        rootEl.appendChild(d.float);
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
        d.approveBtn.addEventListener('click', () => R.single.approveCurrent());
        d.rejectBtn.addEventListener('click', () => (S.reasonsOpen ? R.single.closeReasons() : R.single.openReasons()));
        d.skipBtn.addEventListener('click', () => R.single.skip());
        d.nfBtn.addEventListener('click', () => R.single.toggleNotFound());
        d.moreBtn.addEventListener('click', () => toggleMoreMenu());
        d.moreSkip.addEventListener('click', () => {
            toggleMoreMenu(false);
            R.single.skip();
        });
        d.moreNf.addEventListener('click', () => {
            toggleMoreMenu(false);
            R.single.toggleNotFound();
        });
        document.addEventListener('click', e => {
            if (!S.dom.moreMenu || S.dom.moreMenu.hidden) return;
            const t = e.target;
            if (t && t.closest && (t.closest('#rvMoreMenu') || t.closest('#rvMore'))) return;
            toggleMoreMenu(false);
        });
    }

    // قائمة «⋯» بشريط الموبايل: «تخطي» و«ما لقيت الصورة الصحيحة؟»
    function toggleMoreMenu(open) {
        const d = S.dom;
        if (!d.moreMenu) return false;
        const want = open === undefined ? d.moreMenu.hidden : !!open;
        if (!want && d.moreMenu.hidden) return false;
        d.moreMenu.hidden = !want;
        d.moreBtn.setAttribute('aria-expanded', want ? 'true' : 'false');
        if (want) {
            d.moreSkip.disabled = !!d.skipBtn.disabled;
            d.moreNf.disabled = !!d.nfBtn.disabled;
            d.moreNf.textContent = d.nfBtn.getAttribute('aria-expanded') === 'true' ? 'سكّر «ما لقيت الصورة الصحيحة؟»'
                : 'ما لقيت الصورة الصحيحة؟';
            const first = [d.moreSkip, d.moreNf].find(b => !b.disabled);
            if (first && typeof first.focus === 'function') first.focus();
        } else if (typeof d.moreBtn.focus === 'function' && d.moreMenu.contains(document.activeElement)) {
            d.moreBtn.focus();
        }
        return true;
    }
    R.toggleMoreMenu = toggleMoreMenu;

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
        syncServerApprovals();
    }

    // اعتمادات لسا عم تشتغل عالخادم من صفحة قبل (انسكّرت أو انتقل منها المراجع): منتجاتها «جاري الاعتماد» هون كمان،
    // فما بترجع لقائمة الانتظار ولا بتنعتمد مرتين، ولما تخلص بتنقرا القائمة من جديد بهدوء
    const SERVER_APPROVALS_POLL_MS = 3000;
    let serverPoll = null;
    async function syncServerApprovals() {
        if (!S.urls.approvalJobs || serverPoll || R.sessionExpired()) return;
        const res = await R.requestJson(`${S.urls.approvalJobs}?active=1`);
        if (!res.ok || !res.data || !Array.isArray(res.data.jobs)) return;
        S.serverOwned = S.serverOwned || new Set();
        const active = new Set(res.data.jobs.filter(j => j.status === 'queued' || j.status === 'running')
            .map(j => R.itemKey({ row_number: j.row_number, product_name: j.label || '' })));
        let changed = false;
        active.forEach(key => {
            if (!S.local.get(key) && !(S.jobs && S.jobs.has(key))) {
                S.local.set(key, 'approving');
                S.serverOwned.add(key);
                changed = true;
            }
        });
        let finished = false;
        Array.from(S.serverOwned).forEach(key => {
            if (!active.has(key)) {
                S.serverOwned.delete(key);
                if (S.local.get(key) === 'approving') S.local.delete(key);
                finished = true;
            }
        });
        if (changed) { rebuild(); renderAll(); }
        if (S.serverOwned.size) {
            serverPoll = root.setTimeout(() => { serverPoll = null; syncServerApprovals(); }, SERVER_APPROVALS_POLL_MS);
        }
        if (finished) loadData({ quiet: true });
    }

    // -------------------------------------------------------------------------------------------------
    // «اعتمادات ما زبطت»: اعتمادات فشلت عالخادم بآخر 24 ساعة وما حدا تجاهلها (GET approval-jobs?failed=1)، غالباً بعد
    // ما طلع المراجع من الصفحة. لكل وحدة: اسم المنتج والسبب بالعربي، «افتح المنتج» (بيفتحه بوضع «منتج واحد» بحالته
    // هلق، فبيعتمده من جديد) و«تجاهل». بتنقرا وقت تفتح الصفحة، ولما عدّاد الشريط الجانبي (failed_open) يتغيّر.
    // اعتمادات هالصفحة نفسها ما بتنعاد هون: لوحة الاعتمادات بتعرضها مع زر إعادتها
    // -------------------------------------------------------------------------------------------------

    let failedLoading = false;
    async function loadFailedApprovals() {
        if (!S.urls.approvalJobs || failedLoading || R.sessionExpired()) return;
        failedLoading = true;
        try {
            const res = await R.requestJson(`${S.urls.approvalJobs}?failed=1`);
            if (res.ok && res.data && Array.isArray(res.data.jobs)) {
                S.failed = res.data.jobs;
                renderFailed();
            }
        } finally {
            failedLoading = false;
        }
    }

    function pageServerJobs() {
        const ids = new Set();
        if (S.jobs) S.jobs.state().jobs.forEach(j => { if (j.serverJob) ids.add(Number(j.serverJob)); });
        return ids;
    }

    function failedItem(job) {
        const live = S.items.filter(it => !it.orphan);
        const row = String(job.row_number === null || job.row_number === undefined ? '' : job.row_number);
        return (job.sku_key && (live.find(it => it.product.sku_key === job.sku_key && String(it.product.row_number) === row)
                                || live.find(it => it.product.sku_key === job.sku_key)))
            || (row && live.find(it => String(it.product.row_number) === row
                                       && (!job.label || it.product.product_name === job.label)))
            || null;
    }

    // سبب الفشل بالعربي: نفس نصوص لوحة الاعتمادات (staleInfo / qualityInfo / plainError)
    function failedReason(job) {
        const data = { error: job.error, error_code: job.error_code, quality_flags: job.quality_flags || [] };
        const stale = R.staleInfo(data, null);
        if (stale) return stale.text;
        const quality = R.qualityInfo(data);
        if (quality) return quality.text;
        return R.plainError(job.error, R.plainError(job.error_code, 'ما انعتمدت، والخادم ما قال ليش.'));
    }

    function openFailed(job) {
        const it = failedItem(job);
        if (!it) {
            R.toast('ما لقينا هالمنتج بالقائمة هلق: يمكن انعتمد من مكان ثاني أو انشال من الشيت. حدّث القائمة وجرّب كمان شوي.', 'warning');
            return;
        }
        if (S.mode !== 'single') setMode('single', { key: it.key });
        else R.single.openItem(it.key, { from: 'list' });
    }

    async function dismissFailed(job, btn) {
        if (btn) btn.disabled = true;
        const res = await R.requestJson(`${S.urls.approvalJobs}/${encodeURIComponent(job.id)}/dismiss`, { method: 'POST', body: {} });
        if (res.ok || res.status === 404) {
            S.failed = (S.failed || []).filter(j => Number(j.id) !== Number(job.id));
            renderFailed();
            return;
        }
        if (btn) btn.disabled = false;
        if (!res.expired) R.toast(R.plainError(res.data && res.data.error, 'ما قدرنا نتجاهله هلق. جرّب كمان شوي.'), 'danger');
    }

    function renderFailed() {
        const d = S.dom;
        if (!d.failed) return;
        const own = pageServerJobs();
        const list = (S.failed || []).filter(j => !own.has(Number(j.id)));
        clear(d.failed);
        d.failed.hidden = !list.length;
        // بوضع «منتج واحد» فوق قائمة المراجعة، وبـ«بالجملة» تحت راس الصفحة فوق شريط التحديد
        const bulk = S.mode === 'bulk' && d.bulkBar && d.bulkBar.parentNode === d.bulk;
        const host = bulk ? d.bulk : d.queue;
        if (host && d.failed.parentNode !== host) host.insertBefore(d.failed, bulk ? d.bulkBar : d.list);
        if (!list.length) return;
        d.failed.appendChild(el('div', { className: 'rv-failed__head' }, [
            icon('alert', 16, 2), el('strong', { className: 'rv-failed__title', text: `اعتمادات ما زبطت (${list.length})` })
        ]));
        d.failed.appendChild(el('ul', { className: 'rv-failed__list' }, list.map(job => {
            const name = job.label || (job.row_number ? `صف ${job.row_number}` : 'منتج');
            const dismiss = el('button', { type: 'button', className: 'lq-btn lq-btn--ghost lq-btn--sm rv-failed__dismiss',
                                           dataset: { failedDismiss: job.id }, text: 'تجاهل' });
            dismiss.addEventListener('click', () => dismissFailed(job, dismiss));
            const open = el('button', { type: 'button', className: 'lq-btn lq-btn--secondary lq-btn--sm rv-failed__open',
                                        dataset: { failedOpen: job.id }, text: 'افتح المنتج' });
            open.addEventListener('click', () => openFailed(job));
            return el('li', { className: 'rv-failed__item', dataset: { failedId: job.id } }, [
                el('div', { className: 'rv-failed__text' }, [
                    bdi(name, 'rv-failed__name'),
                    el('span', { className: 'rv-failed__reason', text: failedReason(job) })
                ]),
                el('div', { className: 'rv-failed__actions' }, [open, dismiss])
            ]);
        })));
    }

    // -------------------------------------------------------------------------------------------------
    // انتهت الجلسة (ui.js requestJson، 401 / 419): لافتة وحدة فيها المنتجات اللي ما انبعتت أو فشلت بهالصفحة، وسؤال
    // الاعتمادات الشغّالة بالخلفية بيوقف (followApproval بيهدّي لحاله)
    // -------------------------------------------------------------------------------------------------

    function unsentLabels() {
        if (!S.jobs) return [];
        const st = S.jobs.state();
        const open = st.jobs.filter(j => j.state === 'failed' || j.state === 'waiting' || (j.state === 'running' && !j.accepted));
        st.held.forEach(g => g.jobs.forEach(j => open.push(j)));
        return open.map(j => j.label);
    }

    function onSessionExpired() {
        if (serverPoll) {
            root.clearTimeout(serverPoll);
            serverPoll = null;
        }
        R.showSessionBanner(unsentLabels());
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
        // الوضع: الرابط (?mode= أو ?row=) أولاً، بعده آخر وضع اختاره المراجع بنفسه، وإلا «بالجملة» لما يكون في 10 صور مقترحة
        // بلا تحذير أو أكثر (شغل الـ 100+ منتج)
        if (!S.modeExplicit && !S.modeChosen && S.load.state === 'ready') {
            const want = c.eligible >= BULK_DEFAULT_MIN ? 'bulk' : 'single';
            if (want !== S.mode) setMode(want, { auto: true });
        }
        if (!filter) {
            filter = S.mode === 'bulk' ? (c.eligible ? 'eligible' : 'all')
                : c.proposed ? 'proposed' : c.none ? 'none' : c.bg_failed ? 'bg_failed' : c.not_found ? 'not_found'
                : c.failed ? 'failed' : 'all';
        }
        // وضع الجملة بيعرض المنتظرة بس: رقاقة «ما انلقت» أو «أعطال» بتنفتح بوضع منتج واحد
        if (S.mode === 'bulk' && !filterOf(filter).waiting) filter = 'all';
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
        if (S.mode === 'bulk') updateUrl(false);
    }

    function filterOf(key) {
        return R.FILTERS.find(f => f.key === key) || R.FILTERS[0];
    }
    R.filterOf = filterOf;

    // -------------------------------------------------------------------------------------------------
    // Queue column
    // -------------------------------------------------------------------------------------------------

    function visibleItems() {
        return R.filterItems(S.items, S.filter, S.query, S.keep, S.reason);
    }
    R.visibleItems = visibleItems;

    // رقاقة من المجموعة المشتركة بين الوضعين. بوضع الجملة رقاقة مش من المنتظرة (ما انلقت، أعطال، الخلفية) بتفتح وضع
    // منتج واحد عليها، لأن كل منتج فيها بده شغل لحاله
    function setFilter(key) {
        if (!R.FILTERS.some(f => f.key === key)) return;
        if (S.filter !== key) {
            S.keep = new Set();
            S.reason = '';
        }
        S.filter = key;
        S.listLimit = LIST_PAGE;
        if (S.mode === 'bulk') {
            if (!filterOf(key).waiting) {
                chooseMode('single');
                return;
            }
            updateUrl(false);
            R.bulk.onFilter();
            return;
        }
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
        bg_failed: 'ما في صور معتمدة خلفيتها ما انعزلت.'
    };

    // القائمة، ثم «N من M» في مساحة العمل (يتغير مع الفلتر والبحث وحالة المنتجات)
    function renderList() {
        drawList();
        if (R.single && typeof R.single.updatePosition === 'function') R.single.updatePosition();
        updateTitle();
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
            if (failing.length > 1 && S.urls.retry) {
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

    // «خلصت المراجعة»: شو انعمل اليوم (review_decisions من queue-state، بتوقيت دبي) والخطوة الجاية، بدل صفحة فاضية.
    // «اليوم: اعتمدت 87، رفضت 6، 4 ما انلقت»
    function doneRecap() {
        const t = S.queue && S.queue.today && typeof S.queue.today === 'object' ? S.queue.today : null;
        const approved = t && isFinite(parseInt(t.approved, 10)) ? parseInt(t.approved, 10) : S.today.approved;
        const rejected = t && isFinite(parseInt(t.rejected, 10)) ? parseInt(t.rejected, 10) : S.today.rejected;
        const notFound = S.items.filter(it => it.bucket === 'not_found' && !it.orphan);
        const parts = [`اعتمدت ${approved}`, `رفضت ${rejected}`];
        if (notFound.length) parts.push(`${notFound.length} ما انلقت`);
        const retryable = notFound.filter(it => it.product.has_error);
        const actions = [];
        if (retryable.length && S.urls.retry) {
            actions.push(el('button', { type: 'button', className: 'lq-btn lq-btn--primary', id: 'rvRetryNotFound',
                                        text: `رجّع اللي ما انلقت للطابور (${retryable.length})`,
                                        onclick: () => R.single.retryFailures(retryable) }));
        } else if (notFound.length) {
            actions.push(el('button', { type: 'button', className: 'lq-btn lq-btn--secondary', text: `شوف اللي ما انلقت (${notFound.length})`,
                                        onclick: () => setFilter('not_found') }));
        }
        if (S.urls.run) {        // فاضي للمراجع: صفحة التشغيل للمدير بس
            actions.push(el('a', { className: 'lq-btn lq-btn--secondary', href: `${S.urls.run}#run-brands`, text: 'صلّح الماركات الناقصة' }));
            actions.push(el('a', { className: 'lq-btn lq-btn--secondary', href: S.urls.run, text: 'تشغيل جديد' }));
        }
        return el('div', { className: 'lq-empty rv-empty rv-done', id: 'rvDone' }, [
            el('span', { className: 'lq-empty__icon rv-done__icon' }, [icon('check', 24, 2.2)]),
            el('h2', { className: 'lq-empty__title', text: 'خلصت المراجعة' }),
            el('p', { className: 'lq-empty__text rv-done__today', text: `اليوم: ${parts.join('، ')}` }),
            el('p', { className: 'lq-empty__text', text: retryable.length ? 'الخطوة الجاية: رجّع اللي ما انلقت للطابور، وصلّح الماركات الناقصة قبل التشغيل الجاي.'
                : 'الخطوة الجاية: شغّل تشغيل جديد ليجيب نتائج جديدة.' }),
            el('div', { className: 'lq-empty__actions' }, actions)
        ]);
    }
    R.doneRecap = doneRecap;

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

    // القائمة تحت مساحة العمل (شاشة ضيقة): مش عمود جانبي بيتمرّر لحاله
    function listStacked() {
        try {
            return !!(root.matchMedia && root.matchMedia(`(max-width: ${STACK_PX}px)`).matches);
        } catch (e) {
            return false;
        }
    }
    R.listStacked = listStacked;

    function markActive() {
        const d = S.dom;
        if (!d.list) return;
        // القائمة مكدّسة تحت مساحة العمل: scrollIntoView كان يمرّر الصفحة كلها لتحت (الصفحة تفتح على القائمة مش المنتج)
        const scroll = !listStacked();
        d.list.querySelectorAll('.rv-item').forEach(node => {
            const on = node.getAttribute('data-key') === S.openKey;
            node.classList.toggle('is-active', on);
            if (on) {
                node.setAttribute('aria-current', 'true');
                if (scroll && typeof node.scrollIntoView === 'function') node.scrollIntoView({ block: 'nearest' });
            } else {
                node.removeAttribute('aria-current');
            }
        });
    }
    R.markActive = markActive;

    // -------------------------------------------------------------------------------------------------
    // Background approvals panel, and «تراجع» while an approval is held (jobs.js holdMs)
    // -------------------------------------------------------------------------------------------------

    let jobsHideTimer = null;
    let undoTicker = null;

    // «صورة وحدة»، «صورتين»، «5 صور»، «12 صورة» (العدد بالعربي)
    function imagesText(n) {
        if (n === 1) return 'صورة وحدة';
        if (n === 2) return 'صورتين';
        return n >= 3 && n <= 10 ? `${n} صور` : `${n} صورة`;
    }
    R.imagesText = imagesText;

    function renderUndo(st) {
        const box = S.dom.undo;
        if (!box) return;
        clear(box);
        const groups = st && st.held ? st.held.slice(-3).reverse() : [];
        box.hidden = !groups.length;
        clearInterval(undoTicker);
        undoTicker = null;
        if (!groups.length) return;
        groups.forEach(g => {
            const n = g.jobs.length;
            const left = Math.max(0, Math.ceil((g.releaseAt - Date.now()) / 1000));
            const what = n === 1 ? ['اعتمدت ', bdi(g.jobs[0].label || `صف ${g.jobs[0].row}`, 'rv-undo__name', 'auto')]
                : [`اعتمدت ${imagesText(n)}`];
            box.appendChild(el('div', { className: 'rv-undo__row', dataset: { group: String(g.group) } }, [
                el('span', { className: 'rv-undo__ok' }, [icon('check', 18, 2.2)]),
                el('span', { className: 'rv-undo__text' }, what.concat([
                    el('span', { className: 'rv-undo__left', text: ` · بتنبعت بعد ${left} ث` })
                ])),
                el('button', { type: 'button', className: 'lq-btn lq-btn--secondary lq-btn--sm rv-undo__btn', dataset: { undo: String(g.group) },
                               text: 'تراجع', onclick: () => S.jobs.cancel(g.group) })
            ]));
        });
        // العدّ التنازلي: النص بس، بلا إعادة رسم الأزرار (التركيز ما بيضيع)
        undoTicker = setInterval(() => {
            box.querySelectorAll('.rv-undo__row').forEach(row => {
                const g = groups.find(x => String(x.group) === row.getAttribute('data-group'));
                const span = row.querySelector('.rv-undo__left');
                if (g && span) span.textContent = ` · بتنبعت بعد ${Math.max(0, Math.ceil((g.releaseAt - Date.now()) / 1000))} ث`;
            });
        }, 500);
    }

    function renderJobs(st) {
        renderUndo(st);
        const box = S.dom.jobs;
        if (!box) return;
        clearTimeout(jobsHideTimer);
        clear(box);
        if (!st || !st.jobs.length) {
            box.hidden = true;
            R.single.updateJobsOffset();
            return;
        }
        box.hidden = false;
        box.classList.toggle('has-failures', st.failedAll > 0);
        const onlyApprovals = st.batchJobs.every(j => j.type !== 'reject');
        const n = st.total;
        if (st.busy) {
            const what = onlyApprovals ? `جاري اعتماد ${imagesText(n)} بالخلفية` : `جاري تنفيذ ${plural(n, 'طلب واحد', 'طلبات')} بالخلفية`;
            box.appendChild(el('div', { className: 'rv-jobs__head' }, [
                el('span', { className: 'lq-spinner', 'aria-hidden': 'true' }),
                el('span', { className: 'rv-jobs__text' }, [
                    `${what} · `, el('strong', { text: `${st.settled} من ${n}` }),
                    // كلها عالخادم: الطلوع من الصفحة ما بيوقفها (approval_jobs)
                    st.onServer && st.onServer >= n - st.settled ? ' جاهزة. عم تنعتمد عالخادم: فيك تتنقّل أو تسكّر الصفحة.'
                        : ' جاهزة. بتقدر تكمل شغلك.',
                    st.failed ? el('span', { className: 'rv-jobs__bad', text: ` · ${st.failed} فشلت` }) : null
                ])
            ]));
        } else if (!st.failedAll) {
            box.appendChild(el('div', { className: 'rv-jobs__head' }, [
                el('span', { className: 'rv-jobs__ok' }, [icon('check', 18, 2.2)]),
                el('span', { className: 'rv-jobs__text', text: 'جاهزة. بتقدر تكمل شغلك.' }),
                el('button', { type: 'button', className: 'rv-jobs__close', 'aria-label': 'إغلاق', onclick: () => S.jobs.dismiss() }, [icon('x', 16, 2)])
            ]));
            jobsHideTimer = setTimeout(() => {
                if (!S.jobs.sending() && !S.jobs.state().failedAll) S.jobs.dismiss();
            }, 6000);
        } else {
            // اعتماد أو رفع ما مشي: «فحص النشر» بصفحة الصحة بيجرّب سلسلة النشر كاملة على صورة تجريبية ويقول وين وقفت
            const publishFailed = st.jobs.some(j => j.state === 'failed' && j.type !== 'reject');
            // رصيد أو مفتاح أو حصة PhotoRoom / remove.bg فشّل العزل (R.bgSkipCode): كل اعتماد رح يفشل بنفس الشكل، فاللوحة
            // بتعرض «تجاوز عزل الخلفية» (نفس زر صفحة الصحة). عزل الخلفية متوقف: «أعد المحاولة» بيعتمدها متل ما هي
            const bgFailed = st.jobs.some(j => j.state === 'failed' && j.type !== 'reject' && R.bgSkipCode(j.detail));
            const bg = S.cfg.bg && typeof S.cfg.bg === 'object' ? S.cfg.bg : {};
            const bgOff = bgFailed && bg.method === 'none';
            // «انعتمد 3، ووحدة فشلت: أعد المحاولة»
            const failText = st.failedAll === 1 ? 'وحدة فشلت' : `${st.failedAll} فشلت`;
            const doneText = st.done > 0 ? `${onlyApprovals ? 'انعتمد' : 'خلص'} ${st.done}، و` : '';
            box.appendChild(el('div', { className: 'rv-jobs__head' }, [
                el('span', { className: 'rv-jobs__warn' }, [icon('alert', 18, 2)]),
                el('span', { className: 'rv-jobs__text', text: `${doneText}${failText}: أعد المحاولة` }),
                bgFailed && bg.method && !bgOff && S.urls.bgMethod
                    ? el('button', { type: 'button', className: 'lq-btn lq-btn--danger lq-btn--sm rv-jobs__skipbg', text: 'تجاوز عزل الخلفية…',
                                     title: 'الصور بتنعتمد متل ما هي على لوحة بيضا لحد ما ترجّع عزل الخلفية', disabled: !!S.bgSaving,
                                     onclick: () => R.single.confirmBgSkip() })
                    : null,
                bgOff ? el('span', { className: 'rv-jobs__bgoff', text: 'عزل الخلفية متوقف هلق: «أعد المحاولة» بيعتمدها متل ما هي.' }) : null,
                publishFailed
                    ? el('a', { className: 'lq-btn lq-btn--secondary lq-btn--sm rv-jobs__check', href: S.urls.publishCheck,
                                title: 'بيجرّب الاعتماد كامل على صورة تجريبية وبيقلك وين وقف وشو تعمل', text: 'افحص النشر' })
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
                // فحص القص (quality): علامات العرض تُعتمد رغمها بتأكيد صريح فقط؛ عزل فشل لا يُعتمد ولا يُعاد
                j.quality
                    ? (j.quality.allowed
                        ? el('button', { type: 'button', className: 'lq-btn lq-btn--danger lq-btn--sm rv-jobs__anyway', text: 'اعتمدها رغم هيك…',
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

    // الصفحة مخفية (تبويب ثاني، التطبيق بالخلفية على الموبايل، أو عم تتسكّر)
    function pageHidden() {
        return !!(document && (document.visibilityState === 'hidden' || document.hidden === true));
    }

    // «تراجع»: الاعتمادات اللي لسا ما انبعتت بترجع متل ما كانت (ولا طلب وصل للخادم)
    function undoApprovals(jobs) {
        jobs.forEach(job => {
            if (S.local.get(job.key) === 'approving') S.local.delete(job.key);
            if (job.tick) S.bulk.selected.add(job.tick);
            S.bulk.reviewed.delete(job.key);
        });
        rebuild();
        if (S.mode === 'single') {
            renderList();
            // وضع منتج واحد: بنرجع للمنتج اللي تراجعت عنه
            const back = jobs.length === 1 && S.byKey.get(jobs[0].key) ? jobs[0].key : null;
            if (back) R.single.openItem(back, { from: 'undo' });
            else {
                R.single.renderWorkspace();
                markActive();
            }
        } else {
            renderList();
            R.bulk.render();
        }
        R.toast(jobs.length === 1 ? 'تراجعت: ما انعتمدت.' : `تراجعت: ما انعتمد ولا وحدة من ${imagesText(jobs.length)}.`, 'info');
    }

    function setupJobs() {
        S.jobs = R.createJobQueue({
            concurrency: APPROVE_CONCURRENCY,
            // نفس المنتج (sku_key) ما بيتعتمد بطلبين سوا ولو كان بصفين بالشيت
            conflictKey: job => (job.ctx && job.ctx.sku_key) || null,
            send: (job, opts) => R.single.sendJob(job, opts),
            bodySize: job => R.single.jobBodySize(job),
            hidden: pageHidden,
            onSettle: job => R.single.settleJob(job),
            onDrain: () => loadData({ quiet: true }),
            onCancel: undoApprovals,
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
            onUpdate: st => {
                renderJobs(st);
                // الجلسة منتهية: اللافتة بتعدّ اللي ما انبعت أو فشل لحد هلق
                if (R.sessionExpired()) R.showSessionBanner(unsentLabels());
            }
        });
        // اعتماد محجوز للتراجع ما بيضيع أبداً: لما الصفحة تختفي (تبويب ثاني، قفل الموبايل، أو عم تتسكّر) كل محجوز بينبعت
        // هلق بـ fetch keepalive لنفس الـ endpoint، والمتصفح بيكمّل الطلب ولو انسكّرت الصفحة. visibilitychange بيوصل قبل
        // pagehide بكل المتصفحات (وهو الوحيد الموثوق على الموبايل)، و pagehide للاحتياط. اللي ما وسعته ميزانية keepalive
        // (64KB) بيضل بالدور، و beforeunload بيسأل قبل ما تطلع
        document.addEventListener('visibilitychange', () => {
            if (pageHidden() && S.jobs) S.jobs.flush();
        });
        root.addEventListener('pagehide', () => {
            if (S.jobs) S.jobs.flush();
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
        if (S.mode === 'bulk') {
            params.set('mode', 'bulk');
            if (S.filter && S.filter !== 'all') params.set('filter', S.filter);
            return params;
        }
        if (S.openKey && S.byKey.get(S.openKey)) params.set('row', String(S.byKey.get(S.openKey).product.row_number));
        if (S.filter) params.set('filter', S.filter);
        if (S.reason) params.set('reason', S.reason);
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

    function readStoredMode() {
        try {
            const v = root.localStorage ? root.localStorage.getItem(MODE_STORE) : null;
            return v === 'single' || v === 'bulk' ? v : null;
        } catch (e) {
            return null;      // نافذة خاصة أو تخزين ممنوع: الصفحة بتشتغل بدونه
        }
    }

    // المراجع ضغط «منتج واحد» أو «بالجملة»: الوضع بينحفظ لهالمتصفح، والفلتر والماركة بيضلّوا
    function chooseMode(mode, opts) {
        if (!['single', 'bulk'].includes(mode)) return;
        S.modeChosen = true;
        try {
            if (root.localStorage) root.localStorage.setItem(MODE_STORE, mode);
        } catch (e) {
            // تخزين ممنوع: الاختيار بيضل لهالجلسة بس
        }
        setMode(mode, opts);
    }
    R.chooseMode = chooseMode;

    function setMode(mode, opts) {
        opts = opts || {};
        if (!['single', 'bulk'].includes(mode)) return;
        S.mode = mode;
        const d = S.dom;
        d.root.setAttribute('data-mode', mode);
        d.single.hidden = mode !== 'single';
        d.bulk.hidden = mode !== 'bulk';
        d.root.querySelectorAll('.rv-modes__item').forEach(b => {
            const on = b.getAttribute('data-mode') === mode;
            b.classList.toggle('is-active', on);
            b.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
        R.single.closeReasons();
        toggleMoreMenu(false);
        // وضع الجملة بيعرض المنتظرة بس
        if (mode === 'bulk' && !filterOf(S.filter).waiting) {
            S.filter = 'all';
            S.reason = '';
        }
        if (mode === 'single') {
            const key = opts.key || S.openKey || (visibleItems()[0] || {}).key;
            if (opts.key && !visibleItems().some(it => it.key === opts.key)) S.filter = 'all';
            renderList();
            if (key && S.byKey.get(key)) R.single.openItem(key, { from: 'mode', noUrl: true });
            else R.single.renderWorkspace();
        } else {
            renderList();
            R.bulk.render();
        }
        renderFailed();
        if (!opts.fromPop) updateUrl(!opts.auto);
        R.single.updateJobsOffset();
        if (!opts.auto && typeof root.scrollTo === 'function') root.scrollTo(0, 0);
    }
    R.setMode = setMode;

    function onPopState() {
        const params = new URLSearchParams(root.location.search);
        const mode = params.get('mode') === 'bulk' ? 'bulk' : 'single';
        const row = parseInt(params.get('row') || '', 10);
        const filter = params.get('filter');
        if (filter && R.FILTERS.some(f => f.key === filter)) S.filter = filter;
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
        updateTitle();
    }
    R.renderAll = renderAll;

    // عنوان التبويب: «(12) المراجعة · لقطة» بعدد المنتظرة (نفس رقم الشارة)
    let baseTitle = null;
    function updateTitle() {
        if (!document || typeof document.title !== 'string') return;
        if (baseTitle === null) baseTitle = document.title.replace(/^\(\d+\)\s*/, '');
        const n = S.load.state === 'ready' ? S.counts.waiting : 0;
        document.title = n > 0 ? `(${n}) ${baseTitle}` : baseTitle;
    }

    // -------------------------------------------------------------------------------------------------
    // Confirmations: askDialog (with images, e.g. replace an approval) and R.ask (a plain question, every former
    // window.confirm of the review screen). Enter confirms, Esc cancels, Tab stays inside, and the focus goes back to
    // where it was. Resolves true / false
    // -------------------------------------------------------------------------------------------------

    let activeDialog = null;
    // Enter بعد فتح السؤال بهالمدة بس: ضغطة Enter ثانية سريعة (اللي فتحت السؤال) ما بتأكد شي ما قراه المراجع
    const DIALOG_ENTER_GUARD_MS = 400;

    function focusables(box) {
        if (!box || typeof box.querySelectorAll !== 'function') return [];
        return Array.from(box.querySelectorAll('button, a, input, select, textarea'))
            .filter(n => !n.disabled && !n.hidden && n.getAttribute('tabindex') !== '-1');
    }

    // Tab و Shift+Tab بيلفّوا جوّا الصندوق
    function trapTab(e, box) {
        const list = focusables(box);
        if (!list.length) return;
        const i = list.indexOf(document.activeElement);
        const next = e.shiftKey ? (i <= 0 ? list.length - 1 : i - 1) : (i < 0 || i === list.length - 1 ? 0 : i + 1);
        e.preventDefault();
        if (typeof list[next].focus === 'function') list[next].focus();
    }
    R.trapTab = trapTab;

    function restoreFocus(node) {
        if (node && node.isConnected !== false && typeof node.focus === 'function') {
            try {
                node.focus();
            } catch (e) {
                // the opener was redrawn: nothing to give the focus back to
            }
        }
    }
    R.restoreFocus = restoreFocus;

    function askDialog(opts) {
        opts = opts || {};
        if (activeDialog) activeDialog.finish(false);
        const d = S.dom;
        const opener = document.activeElement;
        return new Promise(resolve => {
            const dlg = { openedAt: Date.now(), kind: opts.kind || 'dialog' };
            dlg.finish = ok => {
                if (activeDialog !== dlg) return;
                activeDialog = null;
                clear(d.dialog);
                d.dialog.hidden = true;
                restoreFocus(opener);
                resolve(!!ok);
            };
            activeDialog = dlg;
            const yes = el('button', { type: 'button', id: 'rvAskConfirm', text: opts.confirmText || 'متأكد',
                                       className: 'lq-btn ' + (opts.danger ? 'lq-btn--danger-solid' : 'lq-btn--primary'),
                                       onclick: () => dlg.finish(true) });
            const no = opts.noCancel ? null : el('button', { type: 'button', id: 'rvAskCancel', className: 'lq-btn lq-btn--secondary',
                                                             text: opts.cancelText || 'إلغاء', onclick: () => dlg.finish(false) });
            const images = (opts.images || []).filter(im => im && im.url);
            clear(d.dialog);
            dlg.box = el('div', { className: 'rv-dialog' + (opts.wide ? ' rv-dialog--wide' : ''), role: opts.noCancel ? 'dialog' : 'alertdialog',
                                  'aria-modal': 'true', 'aria-labelledby': 'rvAskTitle', 'aria-describedby': 'rvAskText' }, [
                el('h2', { className: 'rv-dialog__title', id: 'rvAskTitle', text: opts.title || '' }),
                images.length ? el('div', { className: 'rv-dialog__images' }, images.map(im => el('figure', { className: 'rv-dialog__figure' }, [
                    el('div', { className: 'rv-dialog__img' }, [R.img(im.url, im.caption || '', S.urls.imageProxy)]),
                    el('figcaption', { className: 'rv-dialog__cap' }, [bdi(im.caption || '', null, 'auto')])
                ]))) : null,
                opts.text ? el('p', { className: 'rv-dialog__text', id: 'rvAskText', text: opts.text }) : null,
                opts.body || null,
                el('div', { className: 'rv-dialog__actions' }, [yes, no])
            ]);
            d.dialog.appendChild(dlg.box);
            d.dialog.hidden = false;
            if (typeof yes.focus === 'function') yes.focus();
        });
    }
    R.askDialog = askDialog;

    // سؤال قصير بدل window.confirm: { title, text, confirmText, cancelText, danger }، أو نص لحاله
    function ask(opts) {
        if (typeof opts === 'string') opts = { title: opts };
        return R.askDialog(Object.assign({ kind: 'confirm' }, opts || {}));
    }
    R.ask = ask;

    // مفاتيح السؤال المفتوح: Enter يأكد (مش التكرار، ومش أول لحظة بعد فتحه)، Esc يلغي، Tab يضل جوّا
    function dialogKey(e, key) {
        if (!activeDialog) return false;
        if (key === 'Escape') {
            e.preventDefault();
            activeDialog.finish(false);
            return true;
        }
        if (key === 'Tab') {
            trapTab(e, activeDialog.box);
            return true;
        }
        if (key === 'Enter') {
            e.preventDefault();
            const t = document.activeElement;
            // Enter على «إلغاء» المركّز = إلغاء (متل ما بيتوقع المتصفح)
            if (t && t.id === 'rvAskCancel') {
                activeDialog.finish(false);
                return true;
            }
            if (!e.repeat && Date.now() - activeDialog.openedAt >= DIALOG_ENTER_GUARD_MS) activeDialog.finish(true);
            return true;
        }
        return true;      // باقي المفاتيح ما بتوصل للصفحة تحت السؤال
    }

    function closeAskDialog() {
        if (!activeDialog) return false;
        activeDialog.finish(false);
        return true;
    }
    R.closeAskDialog = closeAskDialog;
    R.dialogActive = () => !!activeDialog;

    // «?»: الاختصارات حسب الوضع
    const SHORTCUTS = {
        single: [
            ['Enter', 'اعتماد الصورة المختارة'], ['X', 'رفض (وبعدها 1–9 للسبب)'], ['S', 'تخطي للمنتج التالي'],
            ['↑ ↓', 'المنتج اللي قبل / بعد'], ['1–9', 'اختيار صورة من تحت (بدون اعتماد)'], ['Z', 'تكبير الصورة ومقارنتها بالشيت'],
            ['Esc', 'سكّر أي نافذة'], ['?', 'هالقائمة']
        ],
        bulk: [
            ['← → ↑ ↓', 'التنقل بين البطاقات'], ['مسافة', 'تحديد البطاقة'], ['Shift + ضغطة', 'تحديد كل اللي بيناتهم'],
            ['A', 'اعتماد البطاقة'], ['Shift+A', 'اعتماد المحددة بلا تحذير'], ['R أو X', 'رفض البطاقة (وبعدها 1–7 للسبب)'],
            ['Z', 'تكبير الصورة ومقارنتها بالشيت'], ['Esc', 'سكّر أي نافذة'], ['?', 'هالقائمة']
        ]
    };

    function showShortcuts() {
        const rows = SHORTCUTS[S.mode] || SHORTCUTS.single;
        const body = el('dl', { className: 'rv-keys' }, rows.reduce((acc, [k, v]) => acc.concat([
            el('dt', { className: 'rv-keys__k' }, [kbd(k)]), el('dd', { className: 'rv-keys__v', text: v })
        ]), []));
        return R.askDialog({ title: 'اختصارات لوحة المفاتيح', body: body, confirmText: 'تمام', noCancel: true, kind: 'keys' });
    }
    R.showShortcuts = showShortcuts;

    // -------------------------------------------------------------------------------------------------
    // Lightbox: the pick large, beside the image in the sheet now. Wheel / pinch / double-click zoom, drag to pan,
    // ← → across the candidates (RTL: ← is the next one), Esc closes and the focus goes back to the opener
    // -------------------------------------------------------------------------------------------------

    const LB_MAX = 6;
    let lightbox = null;

    // opts: { items: [{ url, caption, note }], index, compare: { url, caption } | (index) => that, title,
    //         onChoose(index) (optional «اختارها»), chosen(index) → bool }
    function openLightbox(opts) {
        opts = opts || {};
        const items = (opts.items || []).filter(it => it && it.url);
        if (!items.length) return false;
        closeLightbox(true);
        const opener = document.activeElement;
        const lb = { items: items, index: Math.min(Math.max(0, opts.index || 0), items.length - 1), opener: opener, opts: opts,
                     scale: 1, x: 0, y: 0, pointers: new Map(), pinch: null, drag: null };
        lightbox = lb;
        lb.stage = el('div', { className: 'rv-lb__stage' });
        lb.caption = el('div', { className: 'rv-lb__caption' });
        lb.count = el('span', { className: 'rv-lb__count', 'aria-live': 'polite' });
        lb.side = el('aside', { className: 'rv-lb__side', 'aria-label': 'الصورة الحالية بالشيت' });
        lb.prev = el('button', { type: 'button', className: 'rv-lb__nav rv-lb__nav--prev', 'aria-label': 'الصورة اللي قبل', title: 'اللي قبل (→)',
                                 onclick: () => stepLightbox(-1) }, [icon('arrow-left', 22, 2)]);
        lb.next = el('button', { type: 'button', className: 'rv-lb__nav rv-lb__nav--next', 'aria-label': 'الصورة اللي بعد', title: 'اللي بعد (←)',
                                 onclick: () => stepLightbox(1) }, [icon('arrow-left', 22, 2)]);
        lb.choose = opts.onChoose ? el('button', { type: 'button', className: 'lq-btn lq-btn--primary lq-btn--sm rv-lb__choose', id: 'rvLbChoose',
                                                   text: 'اختارها', onclick: () => {
                                                       const i = lb.index;
                                                       closeLightbox();
                                                       opts.onChoose(i);
                                                   } }) : null;
        lb.close = el('button', { type: 'button', className: 'rv-lb__close', id: 'rvLbClose', 'aria-label': 'سكّر (Esc)',
                                  onclick: () => closeLightbox() }, [icon('x', 22, 2)]);
        lb.box = el('div', { className: 'rv-lb', id: 'rvLightbox', role: 'dialog', 'aria-modal': 'true', 'aria-label': opts.title || 'تكبير الصورة' }, [
            el('div', { className: 'rv-lb__bar' }, [
                el('strong', { className: 'rv-lb__title' }, [bdi(opts.title || '', null, 'auto')]), lb.count,
                el('span', { className: 'rv-lb__hint rv-keyhint', text: 'دولاب الماوس أو قرصة للتكبير · ← → للتنقل' }),
                lb.choose, lb.close
            ]),
            el('div', { className: 'rv-lb__body' }, [
                el('div', { className: 'rv-lb__main' }, [lb.stage, lb.prev, lb.next, lb.caption]),
                lb.side
            ])
        ]);
        lb.box.addEventListener('click', e => {
            if (e.target === lb.box) closeLightbox();
        });
        lb.stage.addEventListener('wheel', e => {
            e.preventDefault();
            zoomLightbox(e.deltaY < 0 ? 1.2 : 1 / 1.2);
        }, { passive: false });
        lb.stage.addEventListener('dblclick', () => zoomLightbox(lb.scale > 1 ? 0 : 2.5));
        lb.stage.addEventListener('pointerdown', e => {
            lb.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
            if (typeof lb.stage.setPointerCapture === 'function') {
                try {
                    lb.stage.setPointerCapture(e.pointerId);
                } catch (err) {
                    // a synthetic pointer: no capture
                }
            }
            if (lb.pointers.size === 2) {
                const [a, b] = Array.from(lb.pointers.values());
                lb.pinch = { dist: Math.hypot(a.x - b.x, a.y - b.y) || 1, scale: lb.scale };
                lb.drag = null;
            } else if (lb.pointers.size === 1 && lb.scale > 1) {
                lb.drag = { x: e.clientX - lb.x, y: e.clientY - lb.y };
            }
        });
        lb.stage.addEventListener('pointermove', e => {
            if (!lb.pointers.has(e.pointerId)) return;
            lb.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
            if (lb.pinch && lb.pointers.size === 2) {
                const [a, b] = Array.from(lb.pointers.values());
                setZoom(lb.pinch.scale * (Math.hypot(a.x - b.x, a.y - b.y) / lb.pinch.dist));
            } else if (lb.drag) {
                lb.x = e.clientX - lb.drag.x;
                lb.y = e.clientY - lb.drag.y;
                applyZoom();
            }
        });
        const up = e => {
            lb.pointers.delete(e.pointerId);
            if (lb.pointers.size < 2) lb.pinch = null;
            if (!lb.pointers.size) lb.drag = null;
        };
        lb.stage.addEventListener('pointerup', up);
        lb.stage.addEventListener('pointercancel', up);
        S.dom.root.appendChild(lb.box);
        if (document.body && document.body.classList) document.body.classList.add('rv-lb-open');
        drawLightbox();
        if (typeof lb.close.focus === 'function') lb.close.focus();
        return true;
    }
    R.openLightbox = openLightbox;

    function drawLightbox() {
        const lb = lightbox;
        if (!lb) return;
        const it = lb.items[lb.index];
        lb.scale = 1;
        lb.x = 0;
        lb.y = 0;
        clear(lb.stage);
        lb.img = R.img(it.url, it.caption || '', S.urls.imageProxy, 'rv-lb__img');
        if (lb.img && lb.img.setAttribute) {
            lb.img.setAttribute('draggable', 'false');
            lb.img.removeAttribute('loading');
        }
        lb.stage.appendChild(lb.img);
        applyZoom();
        clear(lb.caption);
        if (it.caption) lb.caption.appendChild(bdi(it.caption, 'rv-lb__cap', 'auto'));
        if (it.note) lb.caption.appendChild(el('span', { className: 'rv-lb__note', text: it.note }));
        if (lb.opts.chosen && lb.opts.chosen(lb.index)) lb.caption.appendChild(el('span', { className: 'rv-badge', text: 'المختارة' }));
        lb.count.textContent = lb.items.length > 1 ? `${lb.index + 1} من ${lb.items.length}` : '';
        lb.prev.disabled = lb.index === 0;
        lb.next.disabled = lb.index === lb.items.length - 1;
        lb.prev.hidden = lb.next.hidden = lb.items.length < 2;
        const cmp = typeof lb.opts.compare === 'function' ? lb.opts.compare(lb.index) : lb.opts.compare;
        clear(lb.side);
        lb.side.appendChild(el('span', { className: 'rv-lb__side-k', text: (cmp && cmp.caption) || 'الصورة الحالية بالشيت' }));
        lb.side.appendChild(cmp && cmp.url
            ? el('div', { className: 'rv-lb__cmp' }, [R.img(cmp.url, 'الصورة الحالية بالشيت', S.urls.imageProxy)])
            : el('div', { className: 'rv-lb__cmp is-empty' }, [icon('image', 22), el('span', { text: 'ما في صورة بالشيت' })]));
        if (lb.choose) lb.choose.hidden = !!(lb.opts.chosen && lb.opts.chosen(lb.index));
    }

    function applyZoom() {
        const lb = lightbox;
        if (!lb || !lb.img || !lb.img.style) return;
        if (lb.scale <= 1) {
            lb.x = 0;
            lb.y = 0;
        }
        lb.img.style.transform = `translate(${lb.x}px, ${lb.y}px) scale(${lb.scale})`;
        if (lb.stage.classList) lb.stage.classList.toggle('is-zoomed', lb.scale > 1);
    }

    function setZoom(s) {
        if (!lightbox) return;
        lightbox.scale = Math.min(LB_MAX, Math.max(1, s));
        applyZoom();
    }

    // factor 0: رجوع للحجم الطبيعي
    function zoomLightbox(factor) {
        if (!lightbox) return;
        setZoom(factor === 0 ? 1 : (factor >= 2 && lightbox.scale === 1 ? factor : lightbox.scale * factor));
    }

    function stepLightbox(delta) {
        const lb = lightbox;
        if (!lb) return false;
        const next = lb.index + delta;
        if (next < 0 || next >= lb.items.length) return false;
        lb.index = next;
        drawLightbox();
        return true;
    }
    R.stepLightbox = stepLightbox;

    function closeLightbox(silent) {
        const lb = lightbox;
        if (!lb) return false;
        lightbox = null;
        if (lb.box.parentNode) lb.box.parentNode.removeChild(lb.box);
        if (document.body && document.body.classList) document.body.classList.remove('rv-lb-open');
        if (!silent) restoreFocus(lb.opener);
        return true;
    }
    R.closeLightbox = closeLightbox;
    R.lightboxOpen = () => (lightbox ? { index: lightbox.index, count: lightbox.items.length, scale: lightbox.scale,
                                        url: lightbox.items[lightbox.index].url } : null);

    // الصفحة RTL: السهم الأيسر للصورة التالية والأيمن للي قبلها (متل الشبكة بوضع الجملة)
    function lightboxKey(e, key) {
        if (!lightbox) return false;
        if (key === 'Escape' || key === 'z') {
            e.preventDefault();
            closeLightbox();
        } else if (key === 'ArrowLeft' || key === 'ArrowRight') {
            e.preventDefault();
            stepLightbox(key === 'ArrowLeft' ? 1 : -1);
        } else if (key === '+' || key === '=') {
            e.preventDefault();
            zoomLightbox(1.25);
        } else if (key === '-') {
            e.preventDefault();
            zoomLightbox(1 / 1.25);
        } else if (key === '0') {
            e.preventDefault();
            zoomLightbox(0);
        } else if (key === 'Tab') {
            trapTab(e, lightbox.box);
        } else if (key === 'Enter' && lightbox.choose && !lightbox.choose.hidden && document.activeElement === lightbox.choose) {
            return false;     // الزر نفسه
        } else if (key === 'Enter') {
            e.preventDefault();       // Enter ما بيعتمد شي من ورا التكبير
        }
        return true;
    }

    // -------------------------------------------------------------------------------------------------
    // Keyboard: ↑ ↓ move, 1–9 select (never approve), Enter approves the visible selected image, X reject, S skip,
    // Z the lightbox, ? the shortcuts. Bulk mode (bulk.js onKey): arrows move between cards, Space ticks the focused
    // card, A approves it (after its warnings), Shift+A is the «approve the pre-selected ones without a warning»
    // button, R / X rejects the focused card. Ctrl / Cmd / Alt combinations and typing in a field are left to the browser.
    // -------------------------------------------------------------------------------------------------

    function keyOf(e) {
        const code = String(e.code || '');
        if (/^Key[A-Z]$/.test(code)) return code.slice(3).toLowerCase();
        if (/^(Digit|Numpad)[0-9]$/.test(code) && !(code.startsWith('Digit') && e.shiftKey && /^[^0-9٠-٩]$/.test(String(e.key || '')))) {
            return code.slice(-1);
        }
        const k = String(e.key || '');
        if (/^[٠-٩]$/.test(k)) return String('٠١٢٣٤٥٦٧٨٩'.indexOf(k));
        if (k === '؟') return '?';
        return k.length === 1 ? k.toLowerCase() : k;
    }
    R.keyOf = keyOf;

    function onKeyDown(e) {
        if (!e || e.defaultPrevented) return;
        const key = keyOf(e);
        if (activeDialog) {
            if (!e.ctrlKey && !e.metaKey && !e.altKey) dialogKey(e, key);
            return;
        }
        if (lightbox) {
            if (!e.ctrlKey && !e.metaKey && !e.altKey) lightboxKey(e, key);
            return;
        }
        // نافذة رفض البطاقات: 1–7 للسبب
        if (R.bulk.dialogOpen()) {
            if (!e.ctrlKey && !e.metaKey && !e.altKey) R.bulk.dialogKey(e, key);
            return;
        }
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        const active = document.activeElement;
        const tag = active && active.tagName ? String(active.tagName).toLowerCase() : '';
        const inputType = tag === 'input' ? String(active.type || active.getAttribute('type') || 'text').toLowerCase() : '';
        const typing = (tag === 'input' && !['checkbox', 'radio', 'button', 'submit', 'reset', 'range', 'color', 'file'].includes(inputType))
            || tag === 'textarea' || tag === 'select' || !!(active && active.isContentEditable);
        if (key === 'Escape') {
            if (toggleMoreMenu(false)) return;
            if (R.bulk.closeDialog()) return;
            if (S.reasonsOpen) {
                R.single.closeReasons();
                return;
            }
            return;
        }
        if (typing) return;
        if (key === '?' && !e.repeat) {
            e.preventDefault();
            showShortcuts();
            return;
        }
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
            if (key === 'Enter' || key === ' ' || /^[1-9xsz]$/.test(key)) e.preventDefault();
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
        } else if (key === 'z') {
            if (R.single.openLightbox()) e.preventDefault();
        }
    }
    R.onKeyDown = onKeyDown;

    // الشارة في الشريط الجانبي تُقرأ كل 5 ثوانٍ: إذا زاد عدد الجاهز للمراجعة عن قائمتنا مرتين متتاليتين، نعرض «حدّث»
    function onRunStatus(n) {
        // عدّاد «اعتمادات فشلت» بالشريط الجانبي تغيّر: «اعتمادات ما زبطت» بتنقرا من جديد
        const failedOpen = n && n.approvals ? parseInt(n.approvals.failed_open, 10) : NaN;
        if (isFinite(failedOpen) && failedOpen !== S.failedOpenSeen) {
            const first = S.failedOpenSeen === undefined;
            S.failedOpenSeen = failedOpen;
            if (!first) loadFailedApprovals();          // أول عدّاد: الصفحة قرأتها وقت فتحت (boot)
        }
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
        // ?mode= أو ?row= بالرابط: الوضع محدد. وإلا آخر وضع اختاره المراجع (localStorage)، وإلا بيقرر chooseInitial
        S.modeExplicit = !!cfg.modeExplicit || cfg.mode === 'bulk' || !!cfg.row;
        if (!S.modeExplicit) {
            const stored = readStoredMode();
            if (stored) {
                S.mode = stored;
                S.modeChosen = true;
            }
        }
        S.cfg.canvas = parseInt(cfg.canvas, 10) || 800;
        // الاعتماد بيستنى هالمدة قبل ما ينبعت، و«تراجع» بيرجّعه (0 = بينبعت فوراً)
        S.cfg.approveUndoMs = cfg.approveUndoMs === undefined ? 8000 : Math.max(0, parseInt(cfg.approveUndoMs, 10) || 0);
        // كل قديش بتسأل الصفحة عن اعتماد عالخادم (approval_jobs)
        S.cfg.approvalPollMs = cfg.approvalPollMs === undefined ? 1500 : Math.max(10, parseInt(cfg.approvalPollMs, 10) || 1500);
        // وبعد ما تنتهي الجلسة: كل قديش بس (لحد ما ينفتح دخول من تبويب ثاني)
        S.cfg.expiredPollMs = cfg.expiredPollMs === undefined ? 30000 : Math.max(10, parseInt(cfg.expiredPollMs, 10) || 30000);
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
        // النقرة على الصورة المختارة بتفتح التكبير (بطاقات الجملة: bulk.js onGridClick)
        rootEl.addEventListener('click', e => {
            const t = e.target;
            if (S.mode !== 'single' || !t || !t.closest || t.closest('#rvLightbox')) return;
            const stage = t.closest('[data-zoom]');
            if (!stage || t.closest('button, a, input, label')) return;
            if (R.single.openLightbox()) e.preventDefault();
        });
        if (root.Laqta && typeof root.Laqta.onRunStatus === 'function') root.Laqta.onRunStatus(onRunStatus);
        R.onSessionExpired(onSessionExpired);
        loadData({});
        loadFailedApprovals();
        return true;
    }

    Object.assign(R, { boot, loadData, renderJobs, setupJobs, pageHidden, undoApprovals, updateTitle });

    if (!root.LAQTA_REVIEW_MANUAL_BOOT) {
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
        else boot();
    }
})(typeof window !== 'undefined' ? window : globalThis);
