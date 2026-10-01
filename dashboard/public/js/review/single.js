/*
 * لقطة · المراجعة — single-product mode: the workspace (product, the selected image, final preview, the sheet
 * against Gemini's reading, other images, "ما لقيت الصورة الصحيحة؟") and its actions.
 *
 * Safety rules (tested in tests/test_catalog_review_race.py):
 * - every search carries a token and an AbortController; an answer for a product that is no longer open, or for an
 *   older search, is dropped, and results stay bound to the identity (row, sku_key, name) they were searched for;
 * - approve / reject / upload send that bound identity with what the reviewer saw (reviewedCandidateView);
 * - Enter approves only a visible, selected image; 1–9 only select; an approval is queued (one request at a time)
 *   and the same product can never be queued twice; after approving, the next product opens.
 */
(function (root) {
    'use strict';

    const R = root.LaqtaReview = root.LaqtaReview || {};
    const { el, icon, bdi, clear } = R;

    function st() {
        return R.S;
    }

    function sessionOf(key) {
        const S = st();
        if (!S.session.has(key)) {
            S.session.set(key, { search: null, prev: null, searchError: null, pick: null, extras: [], rejected: new Set(),
                                 nfOpen: false, customQuery: null, urlDraft: '', rejecting: false, note: '' });
        }
        return S.session.get(key);
    }

    function currentItem() {
        const S = st();
        return S.openKey ? S.byKey.get(S.openKey) || null : null;
    }

    function isOpen(target) {
        const S = st();
        return !!(target && S.open) && R.sameProduct(target, S.open);
    }

    // نتائج بحث هذه الجلسة، إذا كانت لنفس هوية المنتج المفتوح
    function liveSearch(item) {
        const sess = sessionOf(item.key);
        const s = sess.search;
        if (!s || s.status !== 'done') return null;
        const identity = item.key === st().openKey && st().open ? st().open : R.productIdentity(item.product);
        return R.sameProduct(s.target, identity) || (!identity.sku_key && s.target.row_number === identity.row_number
            && s.target.product_name === identity.product_name) ? s : null;
    }

    // الصور بالترتيب الذي تعرضه الشاشة (ومفاتيح 1–9): صور المراجع، ثم اختيار النظام، ثم الباقي، والمستبعدة آخراً
    function currentCandidates(item) {
        const sess = sessionOf(item.key);
        const search = liveSearch(item);
        const sysUrl = systemPickUrl(item);
        const rank = c => (c.url === sysUrl ? 0 : (c.status === 'rejected' || c.status === 'excluded') ? 2 : 1);
        const base = (search ? search.candidates : R.storedCandidates(item.product))
            .filter(c => !sess.rejected.has(c.url))
            .map((c, i) => [c, i])
            .sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1])
            .map(x => x[0]);
        return sess.extras.concat(base);
    }

    function systemPickUrl(item) {
        const sess = sessionOf(item.key);
        const search = liveSearch(item);
        const url = search ? search.selectedUrl : ((R.storedSelected(item.product) || {}).url || '');
        return url && !sess.rejected.has(url) ? url : '';
    }

    function currentPick(item) {
        if (!item) return null;
        const sess = sessionOf(item.key);
        const cands = currentCandidates(item);
        const url = sess.pick || systemPickUrl(item);
        return url ? cands.find(c => c.url === url) || null : null;
    }

    // نتيجة بحث طلبه المراجع بعد اعتماد المنتج (لاستبدال الصورة المعتمدة)
    function approvedResearch(item) {
        const search = liveSearch(item);
        return !!(search && search.afterApproval);
    }

    function isExtra(c) {
        return !!c && (c.source === 'manual' || c.source === 'upload');
    }

    // هوية المنتج المربوطة بالنتائج المعروضة (مع قرار البحث الذي عرضها)، وليست ما يكتبه المراجع في خانة البحث
    function boundContext(item) {
        const search = liveSearch(item);
        if (search) return Object.assign({}, search.target);
        const S = st();
        const ctx = Object.assign({}, item.key === S.openKey && S.open ? S.open : R.productIdentity(item.product));
        const stored = R.storedCandidates(item.product);
        const sel = R.storedSelected(item.product);
        ctx.search_decision = stored.length
            ? ((item.product.preselected || (sel && sel.status === 'preselected')) ? 'REVIEW_PRESELECTED' : 'REVIEW_UNSELECTED') : '';
        return ctx;
    }

    function neighbour(key, delta, skipDone) {
        const list = R.visibleItems();
        const idx = list.findIndex(it => it.key === key);
        for (let i = idx + delta; i >= 0 && i < list.length; i += delta) {
            const it = list[i];
            if (it.key === key) continue;
            if (!skipDone || !['approving', 'approved'].includes(it.bucket)) return it;
        }
        return null;
    }

    // -------------------------------------------------------------------------------------------------
    // Opening a product and searching
    // -------------------------------------------------------------------------------------------------

    // بحث جارٍ يُلغى: استجابته، إن وصلت، تُهمل
    function cancelPendingSearch() {
        const S = st();
        S.searchSeq++;
        clearTimeout(S.autoTimer);
        S.autoTimer = null;
        if (S.searchController) {
            try {
                S.searchController.abort();
            } catch (e) {
                // already settled
            }
            S.searchController = null;
        }
        S.session.forEach(sess => {
            if (sess.search && sess.search.status === 'searching') {
                sess.search = sess.prev;
                sess.prev = null;
            }
        });
    }

    function openItem(key, opts) {
        const S = st();
        opts = opts || {};
        const item = S.byKey.get(key);
        if (!item) return;
        if (S.openKey !== key || !S.open) {
            // المنتج السابق: يُلغى بحثه الجاري قبل عرض المنتج الجديد
            cancelPendingSearch();
            S.openKey = key;
            S.open = R.productIdentity(item.product);
        }
        closeReasons(true);
        if (R.ensureListed(key)) R.renderList();
        renderWorkspace();
        R.markActive();
        if (!opts.noUrl) R.updateUrl(false);
        maybeAutoSearch(item);
    }

    // منتج لم يُبحث له أبداً (لا مرشحات، لا صورة نهائية، لا عطل): بحث تلقائي بعد مهلة قصيرة، والتنقل السريع لا يطلقه.
    // المنتج الذي له صورة نهائية لا يُبحث له تلقائياً أبداً (كل بحث مدفوع).
    function maybeAutoSearch(item) {
        const S = st();
        const sess = sessionOf(item.key);
        if (item.bucket !== 'idle' || item.orphan || sess.search || S.local.get(item.key) || S.mode !== 'single') return;
        if (R.storedCandidates(item.product).length || R.hasFinalImage(item.product)) return;
        const go = () => {
            S.autoTimer = null;
            if (S.openKey === item.key && !sessionOf(item.key).search && S.mode === 'single') {
                startSearch(item, { customQuery: item.product.search_query || '', auto: true });
            }
        };
        clearTimeout(S.autoTimer);
        if (!S.cfg.autoSearchDelayMs) go();
        else {
            S.autoTimer = setTimeout(go, S.cfg.autoSearchDelayMs);
            renderWorkspace();
        }
    }

    function startSearch(item, opts) {
        const S = st();
        opts = opts || {};
        if (!item || item.orphan || S.local.get(item.key) === 'approving') return;
        // بحث جديد يلغي السابق. الاستجابة تُقبل فقط إن كان رمزها الأحدث وما زال منتجها (الصف و sku_key) مفتوحاً
        cancelPendingSearch();
        const token = S.searchSeq;
        const target = Object.assign({}, item.key === S.openKey && S.open ? S.open : R.productIdentity(item.product));
        const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
        S.searchController = controller;
        const sess = sessionOf(item.key);
        sess.prev = sess.search && sess.search.status === 'done' ? sess.search : null;
        // بحث بطلب صريح بعد اعتماد المنتج في هذه الجلسة: نتيجته قابلة للاعتماد (استبدال الصورة المعتمدة)
        sess.search = { status: 'searching', target: target, token: token, auto: !!opts.auto,
                        afterApproval: S.local.get(item.key) === 'approved' };
        sess.searchError = null;
        renderWorkspace();
        R.requestJson(S.urls.search, { method: 'POST', body: R.searchBody(target, opts.customQuery, opts.skipCache),
                                        signal: controller ? controller.signal : undefined })
            .then(res => {
                if (token !== S.searchSeq || !isOpen(target)) return;    // فُتح منتج آخر أو بدأ بحث أحدث: لا تلمس النتائج
                S.searchController = null;
                applySearchResponse(item.key, res, target, '', true);
            })
            .catch(() => {
                // AbortError: بحث أُلغي، ونتيجته لا تُعرض
            });
    }

    function applySearchResponse(key, res, target, note, render) {
        const S = st();
        const data = (res && res.data) || {};
        const item = S.byKey.get(key);
        if (render && data.sku_key && S.open && !S.open.sku_key && isOpen(target)) {
            // المنتج بلا sku_key في القائمة: يؤخذ من استجابة البحث له
            S.open.sku_key = String(data.sku_key);
            target.sku_key = S.open.sku_key;
            if (item && !item.product.sku_key) item.product.sku_key = S.open.sku_key;
        }
        target.search_decision = String(data.decision || '');
        const sess = sessionOf(key);
        const pending = sess.search && sess.search.status === 'searching' ? sess.search : null;
        const result = res && res.network ? 'network' : String(data.status || 'error');
        const info = {
            result: result,
            decision: String(data.decision || ''),
            failureCode: String(data.failure_code || ''),
            error: String(data.error || data.message || (res && !res.ok && !data.status ? `HTTP ${res.status}` : ''))
        };
        if (['network', 'error', 'failed', 'provider_down'].includes(result) || info.decision === 'PROVIDER_DOWN') {
            // البحث فشل (أو المصادر غير متاحة): يُقال ذلك بوضوح، وتبقى الصور السابقة ظاهرة كما كانت
            sess.searchError = info;
            sess.search = sess.prev;
            sess.prev = null;
            if (note) sess.note = note;
            if (render && S.openKey === key) renderWorkspace();
            return;
        }
        const candidates = R.collectCandidates(data);
        const raw = data.selected_image && (data.selected_image.url || data.selected_image.image_url) ? R.normalizeCandidate(data.selected_image) : null;
        if (raw && !candidates.some(c => c.url === raw.url)) candidates.unshift(raw);
        sess.search = Object.assign(info, {
            status: 'done',
            candidates: candidates,
            selectedUrl: raw ? raw.url : '',
            target: target,
            note: note || '',
            afterApproval: !!(pending && pending.afterApproval)
        });
        sess.searchError = null;
        sess.prev = null;
        sess.pick = null;
        sess.rejected = new Set();
        sess.note = '';
        if (render && S.openKey === key) renderWorkspace();
    }

    // -------------------------------------------------------------------------------------------------
    // Selecting, approving, rejecting
    // -------------------------------------------------------------------------------------------------

    function canApprove() {
        const S = st();
        const item = currentItem();
        if (!item || S.mode !== 'single' || S.ws.state !== 'results' || S.ws.key !== item.key) return false;
        if (S.jobs && S.jobs.has(item.key)) return false;
        const flag = S.local.get(item.key);
        if (flag === 'approving' || flag === 'rejecting' || sessionOf(item.key).rejecting) return false;
        if (flag === 'approved' && !approvedResearch(item)) return false;
        const pick = currentPick(item);
        return !!(pick && pick.url);
    }

    function canReject() {
        const item = currentItem();
        return canApprove() && !isExtra(currentPick(item));
    }

    // مفاتيح الأرقام تحدد صورة فقط ولا تعتمدها
    function selectByNumber(n) {
        const S = st();
        const item = currentItem();
        if (!item || S.ws.state !== 'results' || S.ws.key !== item.key) return false;
        const c = currentCandidates(item)[n - 1];
        if (!c) return false;
        sessionOf(item.key).pick = c.url;
        renderWorkspace();
        return true;
    }

    function buildJob(item, ctx, candidate) {
        const label = item.product.product_name || item.product.product_name_ar || `صف ${item.product.row_number}`;
        const base = { key: item.key, label: label, row: ctx.row_number, ctx: ctx, candidate: candidate };
        if (candidate.source === 'upload') return Object.assign(base, { type: 'upload' });
        return Object.assign(base, { type: 'approve', body: R.selectBody(ctx, candidate) });
    }
    R.buildApproveJob = buildJob;

    // اعتماد الصورة الظاهرة المختارة: يدخل طابور الخلفية (طلب واحد في كل مرة) وننتقل للمنتج التالي
    function approveCurrent() {
        const S = st();
        if (!canApprove()) return false;
        const item = currentItem();
        const candidate = currentPick(item);
        if ((candidate.status === 'rejected' || candidate.status === 'excluded')
            && !root.confirm(`${R.candidateNote(candidate, false).text}. متأكد إنك بدك تعتمد هالصورة وتنشرها؟`)) {
            return false;
        }
        const ctx = boundContext(item);
        const next = neighbour(item.key, 1, true);
        cancelPendingSearch();
        if (!S.jobs.enqueue(buildJob(item, ctx, candidate))) return false;
        S.local.set(item.key, 'approving');
        R.rebuild();
        R.renderList();
        if (next) openItem(next.key, { from: 'approve' });
        else {
            renderWorkspace();
            R.markActive();
        }
        return true;
    }

    function sendJob(job) {
        const S = st();
        if (job.type === 'upload') {
            const form = new FormData();
            const file = job.candidate.file;
            form.append('file', file, (file && file.name) || 'manual_upload.png');
            R.uploadFields(job.ctx).forEach(([name, value]) => form.append(name, value === undefined || value === null ? '' : String(value)));
            return R.requestJson(S.urls.upload, { method: 'POST', body: form });
        }
        if (job.type === 'reject') return R.requestJson(S.urls.reject, { method: 'POST', body: job.body });
        return R.requestJson(S.urls.select, { method: 'POST', body: job.body });
    }

    function settleJob(job) {
        const S = st();
        const data = (job.result && job.result.data) || {};
        if (job.state === 'done') {
            if (job.type === 'reject') {
                const kept = !!data.approval_kept;
                if (S.local.get(job.key) === 'rejecting') S.local.delete(job.key);
                if (!kept && S.local.get(job.key) !== 'approved') S.local.set(job.key, 'rejected');
            } else {
                S.local.set(job.key, 'approved');
                const rawLink = String(data.image_link || '');
                const warning = data.warning || (rawLink.startsWith('needs_review:') ? 'background_not_removed' : '');
                S.approved.set(job.key, { link: rawLink.replace(/^needs_review:/, ''), warning: warning, url: job.candidate.url });
                if (warning) {
                    R.toast(`انعتمدت صورة «${job.label}»، بس الخلفية ما انعزلت: انكتب الرابط بالشيت بعلامة «بحاجة مراجعة».`, 'warning', 9000);
                }
            }
        } else {
            if (['approving', 'rejecting'].includes(S.local.get(job.key))) S.local.delete(job.key);
            if (String(job.detail || '').indexOf(R.PRODUCT_CHANGED) >= 0) {
                R.toast(`${R.PRODUCT_CHANGED}: «${job.label}»`, 'danger', 9000);
            }
        }
        R.rebuild();
        R.renderList();
        if (S.mode === 'single') {
            if (S.openKey === job.key) renderWorkspace();
            else updateBar();
            R.markActive();
        } else {
            R.bulk.render();
        }
    }

    function openReasons() {
        const S = st();
        if (!canReject()) return;
        S.reasonsOpen = true;
        renderReasons();
        updateBar();
    }

    function closeReasons(silent) {
        const S = st();
        if (!S.reasonsOpen) return;
        S.reasonsOpen = false;
        if (!silent) {
            renderReasons();
            updateBar();
        }
    }

    // حفظ مرشحي إعادة البحث بعد الرفض (مع page_url والطبقة) في curation_candidates، لمنتجهم فقط
    async function persistResearchCandidates(ctx, data, rejectedUrl) {
        const fresh = R.collectCandidates(data).filter(c => c.url !== rejectedUrl).map(c => {
            const row = Object.assign({}, c);
            if (row.is_selected === null) delete row.is_selected;
            delete row.file;
            return row;
        });
        if (!fresh.length) return;
        try {
            await R.requestJson(st().urls.saveCandidates || '/api/v1/curation/save-candidates', {
                method: 'POST',
                body: {
                    row_number: parseInt(ctx.row_number, 10),
                    product_name: ctx.product_name,
                    brand: ctx.brand,
                    sku_key: data.sku_key || ctx.sku_key,
                    candidates: fresh
                }
            });
        } catch (err) {
            // النتيجة تُعرض حتى لو فشل الحفظ
        }
    }

    // رفض الصورة الظاهرة بسبب محدد. research: إعادة البحث فوراً مع استبعادها (نتيجته مربوطة بالمنتج نفسه)
    async function rejectCurrent(reasonCode) {
        const S = st();
        if (!canReject()) {
            closeReasons();
            return;
        }
        const item = currentItem();
        const candidate = currentPick(item);
        const sess = sessionOf(item.key);
        const research = !!(S.dom.researchBox && S.dom.researchBox.checked);
        const ctx = boundContext(item);
        closeReasons(true);
        // الرفض مع إعادة البحث بحث جديد: يلغي البحث الجاري ويحمل رمزاً
        if (research) cancelPendingSearch();
        const token = S.searchSeq;
        const query = (sess.customQuery !== null ? sess.customQuery : item.product.search_query) || '';
        sess.rejecting = { research: research };
        renderWorkspace();
        const res = await R.requestJson(S.urls.reject, { method: 'POST', body: R.rejectBody(ctx, candidate, reasonCode, research, query) });
        sess.rejecting = false;
        const data = res.data || {};
        const recorded = !!data.rejection || (!!data.status && !['error', 'failed'].includes(String(data.status)));
        if (!recorded) {
            const msg = res.network ? 'ما قدرنا نوصل للخادم.' : R.plainError(data.error || data.message, 'ما انسجل الرفض.');
            R.toast(`ما انرفضت الصورة: ${msg}`, 'danger', 9000);
            if (String(data.error || '').indexOf(R.PRODUCT_CHANGED) >= 0) R.loadData({ quiet: true });
            if (S.openKey === item.key) renderWorkspace();
            return;
        }
        sess.rejected.add(candidate.url);
        if (sess.pick === candidate.url) sess.pick = null;
        const flag = S.local.get(item.key);
        const settled = flag === 'approving' || flag === 'approved' || (S.jobs && S.jobs.has(item.key));
        const kept = !!(data.rejection ? data.rejection.approval_kept : data.approval_kept);
        if (!kept && !settled) S.local.set(item.key, 'rejected');
        if (research) {
            if (!settled) {
                // الرفض حذف مرشحات المنتج المحفوظة؛ نحفظ المرشحين الجدد كي يبقى المنتج قابلاً للمراجعة
                await persistResearchCandidates(ctx, data, candidate.url);
                const note = `رفضتها («${R.reasonLabel(reasonCode)}») وسجّلنا السبب، وهاي نتيجة بحث جديد بدونها.`;
                // فُتح منتج آخر أو بدأ بحث أحدث: النتيجة تُحفظ لمنتجها ولا يتغير ما يعرضه المراجع
                const current = token === S.searchSeq && isOpen(ctx);
                applySearchResponse(item.key, res, Object.assign({}, ctx), note, current);
            }
        } else {
            sess.note = `رفضتها («${R.reasonLabel(reasonCode)}») وسجّلنا السبب. المنتج رجع للطابور ورح ينبحث عنه من جديد بالتشغيل الجاي؛ وإذا في صورة صحيحة تحت، اختارها واعتمدها هلق.`;
        }
        R.toast(kept ? 'انرفضت الصورة. الصورة المعتمدة قبل بتضل زي ما هي.' : 'انرفضت الصورة وسجّلنا السبب. المنتج رجع للطابور.', 'success');
        R.rebuild();
        R.renderList();
        if (S.openKey === item.key) renderWorkspace();
        R.markActive();
    }

    function skip() {
        const item = currentItem();
        const next = item ? neighbour(item.key, 1, true) : (R.visibleItems()[0] || null);
        if (next) openItem(next.key, { from: 'skip' });
    }

    function move(delta) {
        const S = st();
        const list = R.visibleItems();
        if (!list.length) return;
        let idx = list.findIndex(it => it.key === S.openKey);
        if (idx < 0) idx = delta > 0 ? -1 : list.length;
        const next = list[idx + delta];
        if (next) openItem(next.key, { from: 'keys' });
    }

    function toggleNotFound() {
        const item = currentItem();
        if (!item) return;
        const sess = sessionOf(item.key);
        sess.nfOpen = !sess.nfOpen;
        renderWorkspace();
        if (sess.nfOpen) {
            const panel = document.getElementById('rvNotFound');
            if (panel && typeof panel.scrollIntoView === 'function') panel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
    }

    // رابط صورة يلصقه المراجع: يصير الصورة المختارة، ويُعتمد مثل أي صورة
    function previewUrl(item, value) {
        const url = R.safeHttpUrl(String(value || '').trim());
        if (!url || !/^https?:\/\//i.test(String(value || '').trim())) {
            R.toast('حط رابط صورة بيبلش بـ http أو https.', 'warning');
            return false;
        }
        const sess = sessionOf(item.key);
        sess.extras = sess.extras.filter(c => c.source !== 'manual');
        sess.extras.unshift(R.normalizeCandidate({ url: url, source: 'manual', status: 'pending', title: '' }));
        sess.pick = url;
        sess.urlDraft = '';
        renderWorkspace();
        return true;
    }

    function chooseFile(item, file) {
        if (!file) return;
        if (file.type && !/^image\//.test(file.type)) {
            R.toast('اختار ملف صورة (PNG أو JPG أو WEBP).', 'warning');
            return;
        }
        const sess = sessionOf(item.key);
        sess.extras.filter(c => c.source === 'upload').forEach(c => {
            try {
                URL.revokeObjectURL(c.url);
            } catch (e) {
                // nothing to revoke
            }
        });
        let url = '';
        try {
            url = URL.createObjectURL(file);
        } catch (e) {
            url = 'blob:upload-' + Date.now();
        }
        const c = R.normalizeCandidate({ url: url, source: 'upload', status: 'pending', title: file.name || '' });
        c.file = file;
        sess.extras = sess.extras.filter(x => x.source !== 'upload');
        sess.extras.unshift(c);
        sess.pick = url;
        renderWorkspace();
    }

    async function retryFailures(items) {
        const S = st();
        items = (items || []).filter(it => it && it.product && !it.orphan);
        if (!items.length) return;
        const n = items.length;
        if (n > 1 && !root.confirm(`رح نرجّع ${R.plural(n, 'منتج واحد', 'منتجات')} للطابور ونشيلها من الأعطال. ما رح يبلش أي تشغيل من هون: بتنعالج لما تشغّل التشغيل. الصور المعتمدة ما بتنلمس.`)) {
            return;
        }
        const res = await R.requestJson(S.urls.retry, { method: 'POST', body: { barcodes: items.map(it => R.failureKey(it.product)) } });
        const data = res.data || {};
        if (res.ok && data.status === 'success') {
            items.forEach(it => S.local.set(it.key, 'requeued'));
            const done = parseInt(data.requeued, 10) || n;
            let msg = `رجعت ${R.plural(done, 'منتج واحد', 'منتجات')} للطابور. ما بتبلش معالجتها لحالها: شغّل التشغيل من صفحة «التشغيل».`;
            if (parseInt(data.not_found, 10) > 0) msg += ` ${data.not_found} ما لقيناها بالشيت فبقيت بالأعطال.`;
            R.toast(msg, 'success', 9000);
            R.rebuild();
            R.renderAll();
            R.loadData({ quiet: true });
        } else {
            const why = res.network ? 'ما قدرنا نوصل للخادم.' : R.plainError(data.error, 'صار خطأ.');
            R.toast(`ما رجعت للطابور: ${why}`, 'danger', 9000);
        }
    }

    // -------------------------------------------------------------------------------------------------
    // Rendering
    // -------------------------------------------------------------------------------------------------

    const CHECK_STATUS = {
        match: ['lq-check-row__status--match', 'check', 'مطابق'],
        unsure: ['lq-check-row__status--unsure', 'exclamation', 'تأكد بنفسك'],
        mismatch: ['lq-check-row__status--mismatch', 'x', 'غير مطابق'],
        unknown: ['lq-check-row__status--unknown', 'minus', 'لا توجد معلومة']
    };

    function alertBox(variant, title, text, extra) {
        const icons = { info: 'info', warning: 'alert', danger: 'alert', success: 'check', neutral: 'info' };
        return el('div', { className: `lq-alert lq-alert--${variant} rv-alert`, role: variant === 'danger' ? 'alert' : null }, [
            icon(icons[variant] || 'info', 18, 2, 'lq-alert__icon'),
            el('div', { className: 'lq-alert__body' }, [
                title ? el('strong', { className: 'lq-alert__title', text: title + ' ' }) : null,
                text ? el('span', { text: text }) : null
            ].concat(extra || []))
        ]);
    }

    // «N من M»: مكان المنتج المفتوح في القائمة كما تظهر الآن (الفلتر والبحث يغيّرانها دون إعادة رسم مساحة العمل)
    function updatePosition(scope, item) {
        const S = st();
        scope = scope || (S.dom.wsBody || null);
        item = item || currentItem();
        const pos = scope && typeof scope.querySelector === 'function' ? scope.querySelector('.rv-product__pos') : null;
        if (!pos) return;
        const list = R.visibleItems();
        const idx = item ? list.findIndex(it => it.key === item.key) : -1;
        pos.textContent = idx >= 0 ? `${idx + 1} من ${list.length}` : '';
        pos.hidden = idx < 0;
    }

    function productHeader(item) {
        const S = st();
        const p = item.product;
        const crumbs = el('div', { className: 'rv-product__crumbs' });
        [`صف ${p.row_number}`].concat(R.categoryPath(p)).forEach((part, i) => {
            if (i) crumbs.appendChild(el('span', { className: 'rv-sep', 'aria-hidden': 'true', text: '/' }));
            crumbs.appendChild(i ? bdi(part, null, 'auto') : el('span', { text: part }));
        });
        crumbs.appendChild(el('span', { className: 'rv-product__pos' }));
        const facts = el('div', { className: 'rv-facts' }, R.factsFor(p).map(f => el('span', {
            className: 'rv-fact' + (f.tone ? ` rv-fact--${f.tone}` : '')
        }, [icon(f.icon, 15), el('span', { className: 'rv-fact__k', text: f.label }),
            f.ltr ? bdi(f.value, 'rv-fact__v') : el('span', { className: 'rv-fact__v', text: f.value })])));

        updatePosition(crumbs, item);
        const approved = S.approved.get(item.key);
        const sheetLink = (approved && approved.link) || (R.hasFinalImage(p) ? String(p.existing_image_link || '').trim() : '');
        const box = sheetLink
            ? el('div', { className: 'rv-sheetimg__box' }, [R.img(sheetLink, 'الصورة الحالية بالشيت', S.urls.imageProxy)])
            : el('div', { className: 'rv-sheetimg__box is-empty' }, [icon('image', 20), el('span', { text: 'لا توجد صورة بالشيت' })]);
        return el('header', { className: 'rv-panel rv-product' }, [
            el('div', { className: 'rv-product__main' }, [
                crumbs,
                el('h2', { className: 'rv-product__name' }, [bdi(p.product_name || p.product_name_ar || 'بلا اسم', null, p.product_name ? 'ltr' : 'auto')]),
                facts
            ]),
            el('div', { className: 'rv-sheetimg' }, [box, el('span', { className: 'rv-sheetimg__cap', text: 'الصورة الحالية' })])
        ]);
    }

    function pickCard(item, pick, sysUrl, count, overlay) {
        const S = st();
        if (!pick) {
            return el('section', { className: 'rv-panel rv-pick rv-pick--empty', 'aria-label': 'الصورة المختارة' }, [
                el('div', { className: 'rv-pick__stage' }, [el('div', { className: 'rv-pick__none' }, [
                    icon('image', 28, 1.6),
                    el('strong', { text: 'ما في صورة مختارة لهالمنتج' }),
                    el('span', { text: count ? `اختار وحدة من الصور تحت (${count > 1 ? `1–${Math.min(count, 9)}` : '1'})، أو دوّر من جديد.`
                                              : 'دوّر بكلمات ثانية، أو حط رابط صورة، أو ارفع صورة من جهازك.' })
                ])])
            ]);
        }
        const isSystem = !!sysUrl && pick.url === sysUrl && !isExtra(pick);
        const where = R.storeOf(pick);
        const badgeText = isSystem ? 'مقترحة من النظام' : pick.source === 'manual' ? 'رابط من عندك'
            : pick.source === 'upload' ? 'صورة من جهازك' : 'اختيارك';
        const source = R.safeHttpUrl(pick.page_url);
        const res = pick.width && pick.height ? `${pick.width} × ${pick.height}` : '';
        return el('section', { className: 'rv-panel rv-pick', 'aria-label': 'الصورة المختارة', dataset: { url: pick.url } }, [
            el('div', { className: 'rv-pick__head' }, [
                el('div', { className: 'rv-pick__who' }, [
                    el('span', { className: 'rv-badge' + (isSystem ? '' : ' rv-badge--own') }, [icon('check', 14, 2.2), el('span', { text: badgeText })]),
                    isExtra(pick) ? null : el('span', { className: 'rv-pick__store', title: where.host || null,
                                                        text: [where.store, where.market].filter(Boolean).join(' · ') })
                ]),
                source ? el('a', { className: 'rv-pick__source', href: source, target: '_blank', rel: 'noopener noreferrer' },
                            [el('span', { text: 'صفحة المصدر' }), icon('external', 14)]) : null
            ]),
            el('div', { className: 'rv-pick__stage' }, [
                R.img(pick.url, pick.title || 'الصورة المختارة', S.urls.imageProxy, 'rv-pick__img'),
                res ? el('span', { className: 'rv-pick__res', dir: 'ltr', text: res }) : null,
                overlay || null
            ]),
            pick.title ? bdi(pick.title, 'rv-pick__title') : null
        ]);
    }

    function previewCard(pick) {
        const S = st();
        return el('section', { className: 'rv-panel rv-preview', 'aria-label': 'المعاينة النهائية' }, [
            el('div', { className: 'rv-preview__canvas' }, [pick ? R.img(pick.url, 'معاينة على خلفية بيضاء', S.urls.imageProxy, 'rv-preview__img')
                                                                : icon('image', 22, 1.6)]),
            el('div', { className: 'rv-preview__text' }, [
                el('strong', { className: 'rv-h3', text: 'المعاينة النهائية' }),
                el('span', { text: `${S.cfg.canvas}×${S.cfg.canvas} على خلفية بيضاء، بعد عزل الخلفية. هيك رح تنرفع على Cloudinary.` })
            ])
        ]);
    }

    function checkRow(r) {
        const s = CHECK_STATUS[r.status] || CHECK_STATUS.unknown;
        return el('div', { className: 'lq-check-row rv-check', dataset: { check: r.key, status: r.status } }, [
            el('span', { className: 'lq-check-row__label', text: r.label }),
            el('span', { className: 'lq-check-row__sheet' }, [el('span', { className: 'lq-sr-only', text: 'في الشيت: ' }), bdi(r.sheet, null, 'auto')]),
            el('span', { className: 'lq-check-row__image' }, [el('span', { className: 'lq-sr-only', text: 'في الصورة: ' }), bdi(r.image, null, 'auto')]),
            el('span', { className: `lq-check-row__status ${s[0]}`, role: 'img', 'aria-label': s[2], title: s[2] }, [icon(s[1], 14, 2.2)])
        ]);
    }

    function checksCard(item, pick) {
        const res = R.checksFor(item.product, pick);
        const cautions = R.cautionsFor(pick);
        return el('section', { className: 'rv-panel rv-checks', 'aria-label': 'الشيت مقابل الصورة' }, [
            el('div', { className: 'rv-checks__head' }, [
                el('h3', { className: 'rv-h3', text: 'الشيت مقابل الصورة' }),
                el('span', { className: 'rv-checks__src' }, [icon('sparkle', 14), el('span', { text: res.read ? 'قراءة Gemini' : 'Gemini ما قرأ هالصورة' })])
            ]),
            el('div', { className: 'rv-checks__cols', 'aria-hidden': 'true' }, [el('span'), el('span', { text: 'بالشيت' }), el('span', { text: 'بالصورة' }), el('span')]),
            el('div', { className: 'lq-checks' }, res.rows.map(checkRow)),
            cautions.length ? el('div', { className: 'rv-caution', role: 'note' }, [
                icon('alert', 18, 1.8, 'rv-caution__icon'),
                el('div', {}, [el('strong', { text: 'تأكد قبل الاعتماد: ' })].concat(cautions.map((w, i) => el('span', { className: 'rv-caution__item', text: (i ? '، ' : '') + w })))
                    .concat([el('span', { text: '.' })]))
            ]) : null
        ]);
    }

    function altButton(item, c, i, pick, sysUrl) {
        const S = st();
        const on = !!pick && c.url === pick.url;
        const note = R.candidateNote(c, !!sysUrl && c.url === sysUrl);
        const where = R.storeOf(c);
        const dim = (c.status === 'rejected' || c.status === 'excluded') && !on;
        return el('button', {
            type: 'button',
            className: 'rv-alt' + (on ? ' is-selected' : '') + (dim ? ' is-dim' : ''),
            dataset: { url: c.url, index: String(i) },
            'aria-pressed': on ? 'true' : 'false',
            'aria-keyshortcuts': i < 9 ? String(i + 1) : null,
            title: note.detail || null,
            onclick: () => {
                sessionOf(item.key).pick = c.url;
                renderWorkspace();
            }
        }, [
            el('span', { className: 'rv-alt__thumb' }, [R.img(c.url, '', S.urls.imageProxy), i < 9 ? el('span', { className: 'rv-alt__num', text: String(i + 1) }) : null]),
            el('span', { className: 'rv-alt__store', text: isExtra(c) ? note.text : [where.store, where.market].filter(Boolean).join(' · ') }),
            isExtra(c) ? null : el('span', { className: `rv-alt__note rv-tone--${note.tone}`, text: note.text })
        ]);
    }

    function altsCard(item, cands, pick, sysUrl) {
        const n = Math.min(cands.length, 9);
        return el('section', { className: 'rv-panel rv-alts', 'aria-label': 'صور أخرى وجدها البحث' }, [
            el('div', { className: 'rv-alts__head' }, [
                el('h3', { className: 'rv-h3', text: 'صور أخرى وجدها البحث' }),
                el('span', { className: 'rv-alts__hint', text: n > 1 ? `اضغط 1–${n} لعرض صورة، و Enter لاعتمادها` : 'اضغط 1 لعرضها، و Enter لاعتمادها' })
            ]),
            el('div', { className: 'rv-alts__grid' }, cands.map((c, i) => altButton(item, c, i, pick, sysUrl)))
        ]);
    }

    function notFoundPanel(item) {
        const S = st();
        const p = item.product;
        const sess = sessionOf(item.key);
        const q = el('input', { type: 'text', className: 'lq-input rv-nf__input', dir: 'auto', 'aria-label': 'كلمات البحث',
                                value: sess.customQuery !== null ? sess.customQuery : (p.search_query || [p.product_name, p.brand].filter(Boolean).join(' ')) });
        q.addEventListener('input', () => { sess.customQuery = q.value; });
        const searchForm = el('form', { className: 'rv-nf__field', onsubmit: e => {
            e.preventDefault();
            const v = q.value.trim();
            if (!v) return;
            sess.customQuery = v;
            startSearch(item, { customQuery: v, skipCache: true });
        } }, [el('span', { className: 'rv-nf__label', text: 'بحث بكلمات أخرى' }),
              el('div', { className: 'rv-nf__row' }, [q, el('button', { type: 'submit', className: 'lq-btn lq-btn--secondary lq-btn--sm', text: 'دوّر' })])]);

        const u = el('input', { type: 'url', className: 'lq-input rv-nf__input', dir: 'ltr', placeholder: 'https://', 'aria-label': 'رابط صورة من موقع',
                                value: sess.urlDraft || '' });
        u.addEventListener('input', () => { sess.urlDraft = u.value; });
        const urlForm = el('form', { className: 'rv-nf__field', onsubmit: e => {
            e.preventDefault();
            previewUrl(item, u.value);
        } }, [el('span', { className: 'rv-nf__label', text: 'رابط صورة من موقع' }),
              el('div', { className: 'rv-nf__row' }, [u, el('button', { type: 'submit', className: 'lq-btn lq-btn--secondary lq-btn--sm', text: 'اعرضها' })])]);

        const file = el('input', { type: 'file', accept: 'image/*', className: 'lq-sr-only', tabindex: '-1', 'aria-hidden': 'true' });
        file.addEventListener('change', () => chooseFile(item, file.files && file.files[0]));
        const drop = el('button', { type: 'button', className: 'rv-drop', onclick: () => file.click() },
                        [icon('upload', 16), el('span', { text: 'اختر ملف أو اسحبه هون' })]);
        drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('is-over'); });
        drop.addEventListener('dragleave', () => drop.classList.remove('is-over'));
        drop.addEventListener('drop', e => {
            e.preventDefault();
            drop.classList.remove('is-over');
            const f = e.dataTransfer && e.dataTransfer.files ? e.dataTransfer.files[0] : null;
            chooseFile(item, f);
        });
        const uploadField = el('div', { className: 'rv-nf__field' }, [el('span', { className: 'rv-nf__label', text: 'رفع صورة من جهازك' }), drop, file]);

        return el('section', { className: 'rv-panel rv-nf', id: 'rvNotFound', 'aria-label': 'ما لقيت الصورة الصحيحة؟' }, [
            el('h3', { className: 'rv-h3 rv-nf__title', text: 'ما لقيت الصورة الصحيحة؟' }),
            el('div', { className: 'rv-nf__grid' }, [searchForm, urlForm, uploadField]),
            el('p', { className: 'rv-nf__hint', text: 'الصورة من الرابط أو من جهازك بتصير هي المختارة، وبتنعتمد بـ «اعتماد ونشر» متل أي صورة.' })
        ]);
    }

    function searchButton(item, label, custom) {
        return el('button', { type: 'button', className: 'lq-btn lq-btn--secondary lq-btn--sm rv-research',
                              onclick: () => startSearch(item, { customQuery: custom || item.product.search_query || '', skipCache: !!custom }) },
                  [icon('search', 16, 2), el('span', { text: label })]);
    }

    function stateBanner(item, search, hasCands) {
        const S = st();
        const sess = sessionOf(item.key);
        const out = [];
        if (item.orphan) {
            out.push(alertBox('info', 'مش موجود بالشيت الحالي:', `هالمنتج جاهز بطابور التشغيل (صف ${item.product.row_number})، بس ما لقيناه بالشيت: يمكن انمسح أو تغيّر مكانه. شغّل تشغيل جديد ليتحدث الطابور.`));
            return out;
        }
        if (sess.note) out.push(alertBox('neutral', '', sess.note));
        const failed = sess.searchError;
        if (failed) {
            const retry = searchButton(item, 'دوّر مرة ثانية', sess.customQuery || '');
            const box = failed.result === 'provider_down' || failed.decision === 'PROVIDER_DOWN'
                ? alertBox('danger', 'مصادر البحث مش متاحة هلق:', 'رصيد أو مفتاح أو حظر. هاي مش «ما انلقت»؛ جرّب بعد شوي.', [retry])
                : alertBox('danger', 'فشل البحث:', failed.result === 'network' ? 'ما قدرنا نوصل للخادم.' : R.plainError(failed.error, 'صار خطأ أثناء البحث.'), [retry]);
            const code = [failed.decision, failed.failureCode].filter(Boolean).join(' · ');
            if (code) box.setAttribute('title', code);
            out.push(box);
        }
        if (search) {
            const code = [search.decision, search.failureCode].filter(Boolean).join(' · ');
            if (search.note) out.push(alertBox('neutral', '', search.note));
            if (!hasCands) {
                out.push(alertBox('info', 'ما انلقت:', 'ما لقينا صور مطابقة لهالمنتج. جرّب كلمات ثانية، أو حط رابط، أو ارفع صورة.'));
            } else if (search.decision === 'VERIFIER_DOWN') {
                out.push(alertBox('warning', 'Gemini مش متاح هلق:', 'ما في فحص بصري لهالنتائج، راجع الصور بنفسك قبل الاعتماد.'));
            } else if (search.decision === 'AUTO_PUBLISH') {
                out.push(alertBox('success', 'مطابقة مؤكدة بالكامل.', 'راجعها واعتمدها.'));
            } else if (!search.selectedUrl) {
                out.push(alertBox('info', 'ما في صورة مؤكدة:', 'اختار وحدة من الصور تحت، أو دوّر بكلمات ثانية.'));
            }
            if (code && out.length) out[out.length - 1].setAttribute('title', code);
            return out;
        }
        const p = item.product;
        const bucket = item.bucket;
        if (bucket === 'not_found' || bucket === 'failed') {
            const info = R.failureInfo(p.error_message, item.queue && item.queue.failure_code);
            const box = alertBox(bucket === 'failed' ? 'danger' : 'info', bucket === 'failed' ? 'عطل:' : 'ما انلقت:', info.text, [
                el('span', { className: 'rv-alert__more', text: 'إعادة المحاولة بترجّعه للطابور، وبينبحث عنه بالتشغيل الجاي.' }),
                p.has_error ? el('button', { type: 'button', className: 'lq-btn lq-btn--secondary lq-btn--sm rv-retry', text: 'إعادة المحاولة',
                                             onclick: () => retryFailures([item]) }) : null,
                info.detail ? el('details', { className: 'rv-details' }, [el('summary', { text: 'التفاصيل التقنية' }), el('code', { dir: 'ltr', text: info.detail })]) : null
            ]);
            out.push(box);
        } else if (bucket === 'rejected' || bucket === 'requeued') {
            if (!sess.note) out.push(alertBox('neutral', 'رجعت للطابور:', 'رح ينبحث عنها من جديد بالتشغيل الجاي. إذا بدك هلق: دوّر بكلمات ثانية، أو حط رابط، أو ارفع صورة.'));
        } else if (bucket === 'queued') {
            out.push(alertBox('neutral', 'بالطابور:', 'رح ينبحث عنها بالتشغيل الجاي.'));
        } else if (bucket === 'searching') {
            out.push(alertBox('neutral', 'قيد البحث:', 'التشغيل عم يدوّر على صور لهالمنتج هلق.'));
        } else if (bucket === 'stale' && hasCands) {
            out.push(alertBox('neutral', 'نتائج بحث سابق:', 'هالمنتج مش بانتظار المراجعة بالطابور، بس فيك تعتمد صورة منها.'));
        } else if (bucket === 'none' && hasCands && !systemPickUrl(item)) {
            out.push(alertBox('info', 'بلا اقتراح:', 'النظام ما اختار صورة؛ اختار وحدة من الصور تحت، أو دوّر بكلمات ثانية.'));
        } else if (item.product.has_error && !R.WAITING.includes(bucket)) {
            const info = R.failureInfo(p.error_message, null);
            out.push(alertBox('warning', 'آخر محاولة:', info.text));
        }
        return out;
    }

    function waitingView(title, text, extra) {
        return el('section', { className: 'rv-panel rv-wait', 'aria-busy': 'true' }, [
            el('span', { className: 'lq-spinner rv-wait__spin', 'aria-hidden': 'true' }),
            el('div', { className: 'rv-wait__text' }, [el('strong', { text: title }), text ? el('span', { text: text }) : null].concat(extra || []))
        ]);
    }

    function renderWorkspace() {
        const S = st();
        const d = S.dom;
        if (!d.wsBody) return;
        clear(d.wsBody);
        const item = currentItem();
        S.ws = { key: item ? item.key : null, state: 'none' };
        const body = d.wsBody;

        if (!item) {
            if (S.load.state === 'loading' || (S.load.state === 'reloading' && !S.products.length)) {
                S.ws.state = 'loading';
                body.appendChild(el('div', { className: 'rv-skeleton', 'aria-hidden': 'true' }, [
                    el('div', { className: 'rv-panel rv-skeleton__head' }, [el('span', { className: 'lq-skeleton lq-skeleton--title' }),
                                                                             el('span', { className: 'lq-skeleton lq-skeleton--short' })]),
                    el('div', { className: 'rv-compare' }, [el('div', { className: 'lq-skeleton rv-skeleton__pick' }),
                                                             el('div', { className: 'lq-skeleton rv-skeleton__side' })])
                ]));
                body.appendChild(el('p', { className: 'lq-sr-only', role: 'status', text: 'جاري تحميل قائمة المراجعة…' }));
            } else if (S.load.state === 'error' && !S.products.length) {
                body.appendChild(el('div', { className: 'lq-empty rv-empty' }, [
                    el('span', { className: 'lq-empty__icon rv-empty__icon--danger' }, [icon('alert', 24)]),
                    el('h2', { className: 'lq-empty__title', text: 'ما قدرنا نفتح قائمة المراجعة' }),
                    el('p', { className: 'lq-empty__text', text: S.load.error }),
                    el('div', { className: 'lq-empty__actions' }, [el('button', { type: 'button', className: 'lq-btn lq-btn--secondary', text: 'جرّب مرة ثانية',
                                                                                  onclick: () => R.loadData({}) })])
                ]));
            } else if (S.openKey) {
                body.appendChild(el('div', { className: 'lq-empty rv-empty' }, [
                    el('span', { className: 'lq-empty__icon' }, [icon('info', 24)]),
                    el('h2', { className: 'lq-empty__title', text: 'هالمنتج ما عاد موجود بالقائمة' }),
                    el('p', { className: 'lq-empty__text', text: 'يمكن تغيّر الشيت. اختار منتج من القائمة.' })
                ]));
            } else {
                const nothing = S.counts.waiting === 0 && S.load.state === 'ready';
                body.appendChild(el('div', { className: 'lq-empty rv-empty' }, [
                    el('span', { className: 'lq-empty__icon' }, [icon('review', 24)]),
                    el('h2', { className: 'lq-empty__title', text: nothing ? 'ما في شي بانتظار مراجعتك' : 'اختار منتج من القائمة' }),
                    el('p', { className: 'lq-empty__text', text: nothing ? 'كل الصور الجاهزة انراجعت. شغّل تشغيل جديد ليجيب نتائج جديدة.'
                                                                         : 'القائمة على اليسار؛ ↑ ↓ للتنقل.' }),
                    nothing ? el('div', { className: 'lq-empty__actions' }, [el('a', { className: 'lq-btn lq-btn--secondary', href: S.urls.run, text: 'صفحة التشغيل' })]) : null
                ]));
            }
            updateBar();
            return;
        }

        const sess = sessionOf(item.key);
        const flag = S.local.get(item.key);
        body.appendChild(productHeader(item));

        if (flag === 'approving') {
            S.ws.state = 'approving';
            const job = S.jobs ? S.jobs.activeFor(item.key) : null;
            const cand = job ? job.candidate : currentPick(item);
            body.appendChild(alertBox('info', 'عم تنعتمد بالخلفية:', 'بتقدر تكمل شغلك؛ الصورة بتنرفع وبينكتب رابطها بالشيت.'));
            body.appendChild(el('div', { className: 'rv-compare' }, [
                pickCard(item, cand, '', 0, el('span', { className: 'rv-pick__overlay' }, [el('span', { className: 'lq-spinner', 'aria-hidden': 'true' }),
                                                                                         el('span', { text: 'جاري الاعتماد…' })])),
                el('div', { className: 'rv-side' }, [previewCard(cand)])
            ]));
            updateBar();
            return;
        }
        if (flag === 'approved' && S.approved.get(item.key) && !approvedResearch(item)) {
            S.ws.state = 'approved';
            const done = S.approved.get(item.key);
            body.appendChild(alertBox('success', 'تم الاعتماد.', 'انرفعت الصورة وانكتب رابطها بالشيت.'));
            if (done.warning) body.appendChild(alertBox('warning', 'الخلفية ما انعزلت:', 'انكتب الرابط بالشيت بعلامة «بحاجة مراجعة».'));
            body.appendChild(el('section', { className: 'rv-panel rv-final', dataset: { url: done.link } }, [
                el('div', { className: 'rv-final__stage' }, [R.img(done.link || done.url, 'الصورة المعتمدة', S.urls.imageProxy)]),
                el('div', { className: 'rv-final__text' }, [
                    el('strong', { className: 'rv-h3', text: 'الصورة المنشورة' }),
                    el('span', { text: 'بدك صورة غيرها؟ دوّر من جديد (كل بحث بيكلف من رصيد البحث).' }),
                    searchButton(item, 'دوّر على صورة بديلة')
                ])
            ]));
            updateBar();
            return;
        }
        if (sess.rejecting) {
            S.ws.state = 'rejecting';
            body.appendChild(waitingView(sess.rejecting.research ? 'جاري تسجيل الرفض والبحث عن بدائل…' : 'جاري تسجيل الرفض…',
                                         sess.rejecting.research ? 'البحث بياخد عادة بين 15 و60 ثانية. بتقدر تنتقل لمنتج ثاني وترجع.' : ''));
            updateBar();
            return;
        }
        if (sess.search && sess.search.status === 'searching') {
            S.ws.state = 'searching';
            body.appendChild(waitingView('جاري البحث عن صور لهالمنتج…', 'بياخد عادة بين 15 و60 ثانية. بتقدر تنتقل لمنتج ثاني وترجع.', [
                el('button', { type: 'button', className: 'lq-btn lq-btn--ghost lq-btn--sm', text: 'وقّف البحث', onclick: () => {
                    cancelPendingSearch();
                    renderWorkspace();
                } })
            ]));
            updateBar();
            return;
        }
        if (S.autoTimer && item.bucket === 'idle' && !sess.search) {
            S.ws.state = 'searching';
            body.appendChild(waitingView('رح ندوّر على صور لهالمنتج…', 'ما انبحث عنه لسا.', [searchButton(item, 'دوّر هلق')]));
            updateBar();
            return;
        }

        const search = liveSearch(item);
        const cands = currentCandidates(item);
        const hasFinal = R.hasFinalImage(item.product);
        stateBanner(item, search, cands.length).forEach(b => body.appendChild(b));

        if (!search && !cands.length && hasFinal && !item.orphan) {
            // صورة نهائية في الشيت: تُعرض بلا بحث تلقائي، والبحث بطلب صريح فقط
            S.ws.state = 'final';
            const link = String(item.product.existing_image_link || item.product.cached_image || '');
            body.appendChild(el('section', { className: 'rv-panel rv-final', id: 'rvCurrentSheetImage', dataset: { url: link } }, [
                el('div', { className: 'rv-final__stage' }, [R.img(link, 'الصورة الحالية بالشيت', S.urls.imageProxy)]),
                el('div', { className: 'rv-final__text' }, [
                    el('strong', { className: 'rv-h3', text: 'الصورة الحالية بالشيت' }),
                    el('span', { text: 'لهالمنتج صورة نهائية بالشيت، فما دوّرنا تلقائياً (كل بحث بيكلف من رصيد البحث).' }),
                    searchButton(item, 'دوّر على صورة بديلة')
                ])
            ]));
        } else if (cands.length) {
            S.ws.state = 'results';
            const pick = currentPick(item);
            const sysUrl = systemPickUrl(item);
            body.appendChild(el('div', { className: 'rv-compare' }, [
                pickCard(item, pick, sysUrl, cands.length, null),
                el('div', { className: 'rv-side' }, [previewCard(pick), pick ? checksCard(item, pick) : null])
            ]));
            body.appendChild(altsCard(item, cands, pick, sysUrl));
        } else {
            S.ws.state = 'empty';
            if (!item.orphan && !sess.searchError) {
                body.appendChild(el('div', { className: 'rv-empty-actions' }, [searchButton(item, 'دوّر على صور هلق')]));
            }
        }
        if (!item.orphan && (sess.nfOpen || S.ws.state === 'empty')) body.appendChild(notFoundPanel(item));
        updateBar();
    }

    function renderReasons() {
        const S = st();
        const box = S.dom.reasons;
        if (!box) return;
        clear(box);
        box.hidden = !S.reasonsOpen;
        if (!S.reasonsOpen) return;
        if (!S.dom.researchBox) {
            S.dom.researchBox = el('input', { type: 'checkbox', checked: true, id: 'rvRejectResearch' });
        }
        box.appendChild(el('span', { className: 'rv-reasons__q', text: 'ليش ترفضها؟' }));
        R.REJECT_REASONS.forEach((r, i) => box.appendChild(el('button', {
            type: 'button', className: 'rv-reason', dataset: { reason: r.code }, 'aria-keyshortcuts': String(i + 1),
            onclick: () => rejectCurrent(r.code)
        }, [el('span', { className: 'rv-reason__n', text: String(i + 1) }), el('span', { text: r.label })])));
        box.appendChild(el('label', { className: 'lq-check rv-reasons__research' }, [S.dom.researchBox, el('span', { text: 'دوّر على بدائل بعد الرفض' })]));
        box.appendChild(el('button', { type: 'button', className: 'lq-btn lq-btn--ghost lq-btn--sm', onclick: () => closeReasons() },
                           [el('span', { text: 'إلغاء' }), R.kbd('Esc')]));
        // ما يفعله الرفض وما يبقيه (cli_bridge.action_reject_image): السبب يُسجل والصورة لا تُقترح لهالمنتج مرة ثانية،
        // والمنتج يرجع للطابور إلا إذا عنده صورة معتمدة غيرها، فهي تبقى كما هي
        box.appendChild(el('span', { className: 'rv-reasons__note',
                                     text: 'بنسجّل السبب وما بنرجع نقترح هالصورة لهالمنتج. المنتج بيرجع للطابور؛ والصورة المعتمدة قبل (إذا في) ما بتنلمس.' }));
    }

    function updateBar() {
        const S = st();
        const d = S.dom;
        if (!d.approveBtn) return;
        const item = currentItem();
        const approvable = canApprove();
        d.approveBtn.disabled = !approvable;
        d.approveBtn.title = approvable ? '' : (item && ['approving', 'approved'].includes(S.local.get(item.key))
            ? 'هالمنتج انعتمد أو عم ينعتمد' : 'ما في صورة ظاهرة مختارة للاعتماد');
        d.rejectBtn.disabled = !canReject();
        d.rejectBtn.setAttribute('aria-expanded', S.reasonsOpen ? 'true' : 'false');
        d.rejectBtn.classList.toggle('is-open', S.reasonsOpen);
        const next = item ? neighbour(item.key, 1, true) : null;
        d.skipBtn.disabled = !next;
        const nfOpen = !!(item && sessionOf(item.key).nfOpen);
        d.nfBtn.disabled = !item || item.orphan || ['loading', 'approving', 'approved', 'rejecting', 'searching', 'empty'].includes(S.ws.state);
        d.nfBtn.setAttribute('aria-expanded', nfOpen ? 'true' : 'false');
        clear(d.nextName);
        if (next) {
            d.nextName.appendChild(document.createTextNode('التالي: '));
            d.nextName.appendChild(bdi(next.product.product_name || next.product.product_name_ar || `صف ${next.product.row_number}`, null, 'auto'));
        } else if (item) {
            d.nextName.appendChild(document.createTextNode('هاد آخر منتج بهالقائمة'));
        }
        if (!S.reasonsOpen && d.reasons && !d.reasons.hidden) renderReasons();
        updateJobsOffset();
    }

    // لوحة الاعتمادات بالخلفية تطلع فوق شريط الأزرار في وضع المنتج الواحد
    function updateJobsOffset() {
        const S = st();
        const d = S.dom;
        if (!d.root || !d.bar || !d.root.style || typeof d.root.style.setProperty !== 'function') return;
        const h = S.mode === 'single' && d.bar.offsetHeight ? d.bar.offsetHeight + 12 : 24;
        d.root.style.setProperty('--rv-jobs-bottom', `${h}px`);
    }

    R.single = {
        sessionOf, currentItem, currentCandidates, currentPick, systemPickUrl, boundContext, openItem, startSearch,
        cancelPendingSearch, applySearchResponse, canApprove, canReject, selectByNumber, approveCurrent, sendJob,
        settleJob, openReasons, closeReasons, rejectCurrent, persistResearchCandidates, skip, move, toggleNotFound,
        previewUrl, chooseFile, retryFailures, renderWorkspace, updateBar, updateJobsOffset, isOpen, updatePosition
    };
})(typeof window !== 'undefined' ? window : globalThis);
