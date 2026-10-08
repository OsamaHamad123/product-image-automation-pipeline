/*
 * لقطة · المراجعة — single-product mode: the workspace (product, the selected image, the source before background
 * removal, the sheet against Gemini's reading, other images, "ما لقيت الصورة الصحيحة؟") and its actions.
 *
 * Safety rules (tested in tests/test_catalog_review_race.py):
 * - every search carries a token and an AbortController; an answer for a product that is no longer open, or for an
 *   older search, is dropped, and results stay bound to the identity (row, sku_key, name) they were searched for;
 * - approve / reject / upload send that bound identity with what the reviewer saw (reviewedCandidateView);
 * - Enter approves only a visible, selected image whose picture has loaded on screen (a picture that failed to render
 *   is never approvable) and has been shown for approveSettleMs (a fast second Enter after approving does not
 *   approve the next product unseen); a pick with warnings is confirmed by name first; 1–9 only select; an approval
 *   is queued (one request at a time) and the same product can never be queued twice; after approving, the next
 *   product opens;
 * - an approval carries what the page showed when the product was opened (expected_state, contract C1, a snapshot
 *   app.js takes on open and never refreshes from a quiet reload); a reload that changes the open product shows a
 *   banner and blocks approval until the product is shown again (one click); when the product changed meanwhile the
 *   server refuses it and only an explicit «replace», confirmed with the approved image shown, resends it;
 * - a reject with research never saves candidates from the page: the server saves them and puts the product back
 *   to review (contract C2), and the product stays listed in its chip.
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
            S.session.set(key, { search: null, prev: null, searchError: null, pick: null, extras: [], rejected: new Set(), rejectedWhy: new Map(), undoing: null,
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
    // «غامق / فاتح / مربعات» خلف الصورة المنشورة (theme_preview.js): بيغيّر خلفية المعاينة بس، بلا أي طلب
    function themeToggle(stage) {
        return R.themePreview ? R.themePreview.control(stage) : null;
    }

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
        ctx.search_decision = stored.length && item.base !== 'bg_failed'
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
        if (!S.byKey.get(key)) return;
        // فتح المنتج (أو فتحه من جديد) = ما يراه المراجع الآن: لقطة expected_state جديدة (C1). تغيّر بعد فتحه: يُعرض
        // كما هو الآن (resetMoved يعيد بناء القائمة، فيُقرأ العنصر بعده)
        if (S.moved.has(key)) resetMoved(S.byKey.get(key));
        const item = S.byKey.get(key);
        if (S.openKey !== key || !S.open) {
            // المنتج السابق: يُلغى بحثه الجاري قبل عرض المنتج الجديد
            cancelPendingSearch();
            S.openKey = key;
            S.open = R.productIdentity(item.product);
        }
        R.snapshot(item);
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
        // فئة اختيار المحرك (strict | unsure | other): تُسجّل مع قرار المراجع دليلاً لدقة كل فئة
        target.search_lane = String(data.lane || '');
        const sess = sessionOf(key);
        const pending = sess.search && sess.search.status === 'searching' ? sess.search : null;
        const result = res && res.network ? 'network' : String(data.status || 'error');
        const info = {
            result: result,
            decision: String(data.decision || ''),
            failureCode: String(data.failure_code || ''),
            // لماذا لا اقتراح (catalog_match.explain): null عندما اختار البحث صورة
            explain: data.explain && typeof data.explain === 'object' ? data.explain : null,
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
        return approveBlock() === '';
    }

    // لماذا لا يُعتمد الآن (نص للزر)، أو '' إذا كان الاعتماد ممكناً
    function approveBlock() {
        const S = st();
        const item = currentItem();
        if (!item || S.mode !== 'single' || S.ws.state !== 'results' || S.ws.key !== item.key) return 'ما في صورة ظاهرة مختارة للاعتماد';
        if (S.jobs && S.jobs.has(item.key)) return 'هالمنتج انعتمد أو عم ينعتمد';
        const flag = S.local.get(item.key);
        if (flag === 'approving' || flag === 'rejecting' || sessionOf(item.key).rejecting) return 'هالمنتج انعتمد أو عم ينعتمد';
        if (flag === 'approved' && !approvedResearch(item)) return 'هالمنتج انعتمد أو عم ينعتمد';
        if (S.moved.has(item.key)) return 'تغيّر هالمنتج بعد ما فتحته: اعرضه من جديد قبل الاعتماد';
        const pick = currentPick(item);
        if (!pick || !pick.url) return 'ما في صورة ظاهرة مختارة للاعتماد';
        // الصورة نفسها ظهرت على الشاشة (حدث load)، ومرّ عليها approveSettleMs: لا اعتماد لصورة لم يرها المراجع
        const shown = S.shown;
        if (!shown || shown.key !== item.key || shown.url !== pick.url || shown.failed) {
            return shown && shown.failed && shown.key === item.key && shown.url === pick.url
                ? 'ما قدرنا نعرض هالصورة، فما بتنعتمد: اختار صورة ثانية' : 'الصورة لسا عم تتحمّل';
        }
        if (!shown.loadedAt) return 'الصورة لسا عم تتحمّل';
        if (Date.now() < shown.loadedAt + settleMs()) return 'لحظة: الصورة لسا ظهرت هلق';
        return '';
    }

    function settleMs() {
        const v = parseInt(st().cfg.approveSettleMs, 10);
        return isFinite(v) && v >= 0 ? v : 400;
    }

    // الصورة المختارة كما تظهر في مساحة العمل: متى ظهرت (load) أو فشل عرضها. مفتاحها المنتج والرابط، فإعادة الرسم
    // لا تعيد العدّ، وصورة منتج جديد (بعد الاعتماد والانتقال) تبدأ من جديد
    function trackPick(item, pick, node) {
        const S = st();
        if (!S.shown || S.shown.key !== item.key || S.shown.url !== pick.url) {
            S.shown = { key: item.key, url: pick.url, loadedAt: 0, failed: false };
        }
        const mark = loaded => {
            const sh = S.shown;
            if (!sh || sh.key !== item.key || sh.url !== pick.url) return;
            if (loaded) {
                if (!sh.loadedAt) sh.loadedAt = Date.now();
                sh.failed = false;
                clearTimeout(S.settleTimer);
                S.settleTimer = setTimeout(updateBar, settleMs() + 20);
            } else {
                sh.failed = true;
            }
            updateBar();
        };
        if (!node || String(node.tagName || '').toUpperCase() !== 'IMG') {
            S.shown.failed = true;           // R.img ما عرض شيئاً (رابط غير صالح)
            return;
        }
        node.addEventListener('load', () => mark(true));
        node.addEventListener('error', () => mark(false));
    }

    function productLabel(item) {
        const p = (item && item.product) || {};
        return p.product_name || p.product_name_ar || `صف ${p.row_number}`;
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
        const label = productLabel(item);
        // ما رآه المراجع عن المنتج (C1): لقطة فتحه، أو ما قاله الخادم بعد آخر إجراء له على المنتج في هذه الجلسة. لا
        // تتغير بقراءة هادئة. replace يُرسل فقط بعد تأكيد صريح من المراجع
        const expected = R.seenExpected(item);
        const base = { key: item.key, label: label, row: ctx.row_number, ctx: ctx, candidate: candidate,
                       expected: expected, replace: false, publishAnyway: false };
        if (candidate.source === 'upload') return Object.assign(base, { type: 'upload' });
        return Object.assign(base, { type: 'approve', body: R.selectBody(ctx, candidate) });
    }
    R.buildApproveJob = buildJob;

    // اعتماد الصورة الظاهرة المختارة: يدخل طابور الخلفية (محجوز approveUndoMs لـ«تراجع»، بعدها طلبين بالأكثر سوا)
    // وننتقل للمنتج التالي. false لما ما بينعتمد هلق؛ Promise لما في سؤال قبل (صورة عليها تحذير)
    function approveCurrent() {
        if (!canApprove()) return false;
        const item = currentItem();
        const candidate = currentPick(item);
        // مثل وضع الجملة: صورة عليها تحذير (أو استبعدها النظام / رُفضت قبل) تُعتمد بعد تأكيد يسمّي المنتج
        const cautions = R.cautionsFor(candidate);
        if (!cautions.length) return enqueueCurrent(item, candidate);
        return R.ask({ title: `«${productLabel(item)}»: تأكد قبل الاعتماد`, text: `${cautions.join('، ')}. بدك تعتمدها؟`,
                       confirmText: 'اعتمدها', cancelText: 'لا، رجوع' })
            .then(ok => ok && enqueueCurrent(item, candidate));
    }

    function enqueueCurrent(item, candidate) {
        const S = st();
        // تغيّر شيء أثناء التأكيد (منتج ثاني انفتح، أو الصورة تغيّرت): ما في اعتماد
        if (!canApprove() || currentItem() !== item || (currentPick(item) || {}).url !== candidate.url) return false;
        const ctx = boundContext(item);
        const next = neighbour(item.key, 1, true);
        cancelPendingSearch();
        if (!S.jobs.enqueue(buildJob(item, ctx, candidate), { holdMs: S.cfg.approveUndoMs })) return false;
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

    // جسم طلب الاعتماد أو الرفض كما ينبعت (JSON)؛ الرفع ملف (FormData) ما إله جسم هون
    function jobPayload(job) {
        if (job.type === 'upload') return null;
        if (job.type === 'reject') return job.body;
        const guard = { expected_state: job.expected || null };
        if (job.replace) guard.replace = true;
        if (job.publishAnyway) guard.publish_anyway = true;
        return Object.assign({}, job.body, guard);
    }

    // حجم الجسم بالبايت (لميزانية keepalive لما الصفحة تختفي)، أو null للرفع (ملف ما بينبعت keepalive)
    function jobBodySize(job) {
        const body = jobPayload(job);
        if (!body) return null;
        const text = JSON.stringify(body);
        if (typeof TextEncoder === 'function') return new TextEncoder().encode(text).length;
        return text.length * 3;
    }

    // اعتماد عالخادم (approval_jobs): الصفحة بتسأل عن نتيجته كل approvalPollMs لحد ما يخلص، والنتيجة نفسها اللي كان
    // select_image بيردها ({ok, status, data}) فبيتسوّى متل قبل. انقطاع الشبكة بالنص ما بيوقفه: بيضل يسأل، والاعتماد ماشي
    async function followApproval(id) {
        const S = st();
        for (;;) {
            await new Promise(resolve => root.setTimeout(resolve, (S.cfg && S.cfg.approvalPollMs) || 1500));
            const res = await R.requestJson(`${S.urls.approvalJobs}?ids=${encodeURIComponent(id)}`);
            const jobs = res.ok && res.data && Array.isArray(res.data.jobs) ? res.data.jobs : [];
            const found = jobs.find(j => Number(j.id) === Number(id));
            if (found && (found.status === 'done' || found.status === 'failed')) {
                const status = Number(found.http_status) || 500;
                return { ok: status >= 200 && status < 300, status: status, data: found.result || {} };
            }
        }
    }

    // opts.keepalive: الصفحة عم تختفي، والمتصفح بيكمّل الطلب ولو انسكّرت (jobs.js flush)
    async function sendJob(job, opts) {
        const S = st();
        const keepalive = !!(opts && opts.keepalive);
        if (job.type === 'upload') {
            const form = new FormData();
            const file = job.candidate.file;
            form.append('file', file, (file && file.name) || 'manual_upload.png');
            R.uploadFields(job.ctx).forEach(([name, value]) => form.append(name, value === undefined || value === null ? '' : String(value)));
            // C1: حقول النموذج نصوص، فما رأته الصفحة يُرسل JSON (ApiController يفكه)
            form.append('expected_state', JSON.stringify(job.expected || null));
            if (job.replace) form.append('replace', '1');
            if (job.publishAnyway) form.append('publish_anyway', '1');
            return R.requestJson(S.urls.upload, { method: 'POST', body: form });
        }
        if (job.type === 'reject') {
            return R.requestJson(S.urls.reject, { method: 'POST', body: jobPayload(job), keepalive: keepalive });
        }
        // الاعتماد بيتسجّل عالخادم (202 + job_id) وبيشتغل هناك: من هون الصفحة بتنسكّر أو بتتنقّل وما بيضيع شي.
        // خادم أقدم بيرد النتيجة مباشرة (200): بتتسوّى متل قبل
        const res = await R.requestJson(S.urls.select, { method: 'POST', body: Object.assign(jobPayload(job), { async: 1 }),
                                                         keepalive: keepalive });
        if (keepalive || res.status !== 202 || !res.data || !res.data.job_id) return res;
        job.serverJob = res.data.job_id;
        if (S.jobs && typeof S.jobs.accept === 'function') S.jobs.accept(job);
        return followApproval(res.data.job_id);
    }

    // استبدال صورة معتمدة تغيّرت بعد فتح الصفحة (C1): تأكيد صريح يعرض الصورة المعتمدة الآن (ولمن اعتُمدت) بجانب صورة
    // المراجع ويقول ما تغيّر، ثم الطلب نفسه مع replace وما عرضه التأكيد (expected = current). الخادم يقارنه: إن تغيّر
    // شيء مرة أخرى بعد التأكيد يُرفض ويُعرض من جديد
    async function confirmReplace(job) {
        const S = st();
        if (!job || !job.stale || job.stale.replaceable === false) return false;
        const cur = job.stale.current || {};
        const prev = job.expected || {};
        const pick = key => (cur[key] === undefined ? (prev[key] === undefined ? null : prev[key]) : (cur[key] || null));
        const expected = { queue_status: pick('queue_status'), queue_updated_at: pick('queue_updated_at'),
                           approved_url: pick('approved_url'), queue_row: pick('queue_row') };
        const approvedNow = String(expected.approved_url || '').replace(/^needs_review:/, '');
        const who = String(cur.approved_for || '').trim() || job.label;
        const by = cur.approved_by === 'auto' || cur.approval_status === 'auto_verified' ? 'اعتمدها النظام تلقائياً'
            : cur.approved_by ? 'اعتمدها مراجع' : '';
        const images = [];
        if (approvedNow) images.push({ url: approvedNow, caption: `المعتمدة الآن لـ «${who}»${by ? ` (${by})` : ''}` });
        if (job.candidate && job.candidate.url) images.push({ url: job.candidate.url, caption: 'الصورة التي اخترتها' });
        const ok = await R.askDialog({
            title: `استبدال صورة «${job.label}»؟`,
            text: `${job.stale.text || ''} ${approvedNow ? 'إذا استبدلتها، بتنلغى الصورة المعتمدة هلق وبتنعتمد صورتك مكانها.'
                : 'ما في صورة معتمدة إله هلق؛ إذا كمّلت بتنعتمد صورتك.'}`.trim(),
            images: images,
            confirmText: 'استبدلها بصورتي',
            cancelText: 'إلغاء',
            danger: true
        });
        if (!ok) return false;
        return S.jobs.retry(job.id, { replace: true, expected: expected, stale: null });
    }

    // اعتماد صورة فيها علامات عرض من فحص القص (publish_anyway): تأكيد صريح يقول ما تعنيه كل علامة في الصورة المعتمدة،
    // ثم الطلب نفسه. لا يقول إن الخلفية معزولة: العلامات وحدها تقول ما في الصورة (opaque_fill لا تُعرض هنا أبداً)
    async function confirmPublishAnyway(job) {
        const S = st();
        if (!job || !job.quality || !job.quality.allowed) return false;
        const what = (job.quality.anywayTexts || []).length ? job.quality.anywayTexts.join('، ')
            : (job.quality.texts.length ? job.quality.texts.join('، ') : 'ملاحظات من فحص القص');
        const ok = await R.ask({ title: `فحص القص لقى بصورة «${job.label}»: ${what}.`,
                                 text: 'إذا اعتمدتها بتنحط بالشيت متل ما هي، وبتضل عليها ملاحظة بصفحة المنتج. بدك تعتمدها هيك؟',
                                 confirmText: 'اعتمدها هيك', danger: true });
        if (!ok) return false;
        return S.jobs.retry(job.id, { publishAnyway: true, quality: null });
    }

    // «تجاوز عزل الخلفية» من لوحة الاعتمادات اللي ما مشيت لأن رصيد أو مفتاح أو حصة PhotoRoom / remove.bg فشّل العزل
    // (R.bgSkipCode): تأكيد صريح بنفس نص صفحة الصحة (cfg.bg.confirm، SettingsController::BG_SKIP_CONFIRM)، ثم
    // POST /api/settings/bg-method {method: 'none'}. ما بيعيد أي اعتماد لحاله: «أعد المحاولة» على كل صورة بينشرها متل ما هي
    async function confirmBgSkip() {
        const S = st();
        const bg = S.cfg.bg && typeof S.cfg.bg === 'object' ? S.cfg.bg : {};
        if (!bg.method || bg.method === 'none' || S.bgSaving || !S.urls.bgMethod) return false;
        if (!(await R.ask({ title: 'تجاوز عزل الخلفية؟', text: bg.confirm || '', confirmText: 'تجاوز', danger: true }))) return false;
        S.bgSaving = true;
        R.renderJobs(S.jobs.state());
        const res = await R.requestJson(S.urls.bgMethod, { method: 'POST', body: { method: 'none' } });
        S.bgSaving = false;
        const data = (res && res.data) || {};
        const done = !!(res && res.ok && data.status === 'success');
        if (done) {
            S.cfg.bg = Object.assign({}, bg, { method: String(data.method || 'none'), previous: String(data.previous || '') });
            R.toast('عزل الخلفية متوقف: اضغط «أعد المحاولة» على الصور اللي فشلت لتنعتمد متل ما هي على لوحة بيضا.',
                    'success', 9000);
        } else {
            R.toast(res && res.network ? 'ما قدرنا نوصل للخادم.' : `ما انحفظ: ${R.plainError(data.error, 'جرّب مرة ثانية.')}`,
                    'danger', 9000);
        }
        R.renderJobs(S.jobs.state());
        return done;
    }

    function settleJob(job) {
        const S = st();
        const data = (job.result && job.result.data) || {};
        if (job.state === 'done') {
            if (S.today) S.today[job.type === 'reject' ? 'rejected' : 'approved'] += 1;
            // ما قاله الخادم عن المنتج بعد هذا الإجراء يصير ما «رآه» المراجع (القراءة التالية لا تُعد تغييراً)
            R.settleSeen(job.key, data.current || (data.rejection && data.rejection.current) || null);
            if (job.type === 'reject') {
                // C2: رفض الصورة المقترحة وغيرها باقٍ يُبقي المنتج بانتظار المراجعة بصوره الباقية
                const outcome = R.rejectionOutcome(data, false);
                if (S.local.get(job.key) === 'rejecting') S.local.delete(job.key);
                if (S.local.get(job.key) !== 'approved' && !outcome.kept) {
                    if (outcome.requeued) S.local.set(job.key, 'rejected');
                    else dropCandidate(job.key, job.candidate.url);
                }
            } else {
                S.local.set(job.key, 'approved');
                const rawLink = String(data.image_link || '');
                const notes = R.approvalNotes(data);
                // C3: ما حدث في الشيت كما قاله الخادم؛ «كُتب في الشيت» فقط عند written
                const sheet = R.sheetNote(data.sheet);
                S.approved.set(job.key, { link: rawLink.replace(/^needs_review:/, ''), warning: notes.bgFailed ? 'background_not_removed' : '',
                                          url: job.candidate.url, sheet: sheet.state, notes: notes,
                                          pageGtin: String(data.page_gtin || ''),
                                          current: data.current && typeof data.current === 'object' ? data.current : null });
                const flagsPart = notes.flagTexts.length ? ` فحص القص: ${notes.flagTexts.join('، ')}.` : '';
                if (notes.publishedAnyway) {
                    R.toast(`انعتمدت صورة «${job.label}» رغم ملاحظات فحص القص: ${notes.flagTexts.join('، ') || 'ملاحظات العرض'}.`
                            + (sheet.state && sheet.state !== 'written' ? ` ${sheet.text}` : ''), 'warning', 9000);
                } else if (notes.bgFailed) {
                    const sheetPart = sheet.state === 'written' ? 'وكُتب رابطها في الشيت بعلامة «بحاجة مراجعة».' : sheet.text;
                    R.toast(`اعتُمدت صورة «${job.label}» ولم تُعزل خلفيتها${sheetPart ? '، ' + sheetPart : '.'}${flagsPart} تجدها في رقاقة «الخلفية لم تُعزل».`,
                            'warning', 9000);
                } else if (sheet.state && sheet.state !== 'written') {
                    R.toast(`اعتُمدت صورة «${job.label}» ${sheet.text}`, sheet.tone, 12000);
                }
                if (notes.bgSkipped && !S.bgSkippedSaid) {
                    // مرة بالجلسة: كل اعتماد بعده بينتشر متل ما هو، واللوحة النهائية بتقولها لكل منتج
                    S.bgSkippedSaid = true;
                    R.toast(`انعتمدت صورة «${job.label}» بدون عزل الخلفية (عزل الخلفية متوقف بالإعدادات).`, 'info', 9000);
                }
                if (notes.bgFallback && !S.bgFallbackSaid) {
                    // مرة بالجلسة: كل اعتماد بعده بيعزل محلياً لحد ما ينشحن الرصيد، واللوحة النهائية بتقولها لكل منتج
                    S.bgFallbackSaid = true;
                    R.toast(`${notes.bgFallback.text} (صورة «${job.label}»). اشحن الرصيد لجودة أحسن.`, 'info', 9000);
                }
                if (notes.duplicate) {
                    const who = notes.duplicateOf.length ? `: ${notes.duplicateOf.map(n => `«${n}»`).join('، ')}` : '';
                    R.toast(`صورة «${job.label}» نفسها منشورة لمنتج آخر${who}. تأكد أنها ليست صورة منتج مختلف.`, 'warning', 12000);
                }
            }
        } else {
            if (['approving', 'rejecting'].includes(S.local.get(job.key))) S.local.delete(job.key);
            if (job.quality) {
                R.toast(`ما انعتمدت صورة «${job.label}»: ${job.quality.text}`
                        + (job.quality.allowed ? ' بتقدر تعتمدها رغم هيك من لوحة الاعتمادات بعد ما تراجعها.' : ''), 'danger', 12000);
            } else if (job.stale && job.stale.replaceable === false) {
                R.toast(`لم تُعتمد صورة «${job.label}»: ${job.stale.text}`, 'danger', 12000);
            } else if (job.stale) {
                R.toast(`لم تُعتمد صورة «${job.label}»: ${job.stale.text} تستطيع استبدالها من لوحة الاعتمادات بعد التأكد.`, 'danger', 12000);
            } else if (String(job.detail || '').indexOf(R.PRODUCT_CHANGED) >= 0) {
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

    // صورة رُفضت والمنتج باقٍ بانتظار المراجعة (C2): تُزال من صوره المحفوظة هنا، ويبقى في رقاقته
    function dropCandidate(key, url) {
        const S = st();
        const item = S.byKey.get(key);
        if (!item) return;
        item.product.curation_candidates = (item.product.curation_candidates || [])
            .filter(c => String(c.url || c.image_url || '') !== url);
        S.keep.add(key);
    }

    // أسباب الرفض المعروضة للمنتج المفتوح (التجميلية أولاً لاعتماد لم تُعزل خلفيته)
    function currentReasons() {
        const item = currentItem();
        return R.rejectReasonsFor(item ? item.base : '');
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

    // عدد المرشحين الذين حفظهم الخادم بعد الرفض مع إعادة البحث (C2: candidates_saved)، أو 0
    function savedCount(data) {
        const v = data.candidates_saved !== undefined ? data.candidates_saved : (data.rejection || {}).candidates_saved;
        if (v === true) return 1;
        const n = parseInt(v, 10);
        return isFinite(n) && n > 0 ? n : 0;
    }

    // C2: الخادم حفظ مرشحي البحث الجديد وأعاد المنتج «بانتظار المراجعة». الصفحة تعرض ذلك فوراً (المرشحون نفسهم
    // في بيانات المنتج وصف الطابور) دون أي حفظ من عندها، والمنتج يبقى في رقاقته؛ القراءة التالية تأتي بما حفظه الخادم
    function requeueForReview(item, data, rejectedUrl) {
        const S = st();
        item.product.curation_candidates = R.collectCandidates(data).filter(c => c.url !== rejectedUrl).map(c => ({
            image_url: c.url, title: c.title, page_url: c.page_url, domain: c.domain, status: c.status,
            reasons: c.reasons.concat(c.warnings.map(w => 'warn:' + w).filter(w => !c.reasons.includes(w))),
            evidence: c.evidence, vlm: c.vlm, width: c.width, height: c.height, identity_tier: c.identity_tier,
            content_sha256: c.content_sha256, is_selected: c.status === 'preselected' ? 1 : 0
        }));
        item.product.needs_review = true;
        const queueKnown = S.queue && (S.queue.status === 'success' || S.queue.status === 'no_queue');
        if (item.queue) {
            item.queue.status = 'ready_for_review';
            item.queue.failure_code = null;
        } else if (queueKnown && Array.isArray(S.queue.rows)) {
            S.queue.rows.push({ row_number: parseInt(item.product.row_number, 10) || 0, sku_key: item.product.sku_key || null,
                                status: 'ready_for_review', failure_code: null, product_name: item.product.product_name || '',
                                brand: item.product.brand || '', updated_at: null });
        }
        if (S.local.get(item.key) === 'rejected') S.local.delete(item.key);
        S.keep.add(item.key);
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
        // صورة غير اختيار النظام: رفضها لا يمس باقي الصور ولا يعيد المنتج للطابور (C2)
        const sysUrl = systemPickUrl(item);
        const alternative = !!sysUrl && candidate.url !== sysUrl;
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
        if (S.today) S.today.rejected += 1;
        sess.rejected.add(candidate.url);
        sess.rejectedWhy.set(candidate.url, reasonCode);
        if (sess.pick === candidate.url) sess.pick = null;
        R.settleSeen(item.key, data.current || null);
        const flag = S.local.get(item.key);
        const settled = flag === 'approving' || flag === 'approved' || (S.jobs && S.jobs.has(item.key));
        // C2: ما قاله الخادم عن الطابور (rejection.queue_status) والصور الباقية (candidates_left)
        const outcome = R.rejectionOutcome(data, alternative);
        const kept = outcome.kept;
        const requeued = outcome.requeued;
        const left = outcome.left ? ` (${outcome.left})` : '';
        const saved = savedCount(data);
        let message = kept ? 'انرفضت الصورة. الصورة المعتمدة قبل بتضل زي ما هي.' : 'انرفضت الصورة وسجّلنا السبب. المنتج رجع للطابور.';
        if (research) {
            if (!settled) {
                const note = `رفضتها («${R.reasonLabel(reasonCode)}») وسجّلنا السبب، وهاي نتيجة بحث جديد بدونها.`;
                // فُتح منتج آخر أو بدأ بحث أحدث: النتيجة تُحفظ لمنتجها ولا يتغير ما يعرضه المراجع
                const current = token === S.searchSeq && isOpen(ctx);
                applySearchResponse(item.key, res, Object.assign({}, ctx), note, current);
                if (kept) {
                    // الاعتماد السابق باقٍ (approval_kept) والطابور لم يتغيّر: لا «بانتظار مراجعتك»
                    message = 'انرفضت الصورة وسجّلنا السبب. الصورة المعتمدة قبل بتضل زي ما هي.';
                } else if (saved && outcome.queueStatus === 'ready_for_review') {
                    // الخادم حفظ المرشحين الجدد والمنتج بانتظار المراجعة: لا حفظ من الصفحة، ولا «رجع للطابور»
                    requeueForReview(item, data, candidate.url);
                    message = 'انرفضت الصورة وسجّلنا السبب. نتيجة البحث الجديد محفوظة والمنتج بانتظار مراجعتك.';
                } else if (requeued) {
                    S.local.set(item.key, 'rejected');
                } else {
                    dropCandidate(item.key, candidate.url);
                    message = `انرفضت الصورة وسجّلنا السبب. باقي الصور ما زالت للمراجعة${left}.`;
                }
            }
        } else if (!requeued && !kept) {
            // صورة بديلة: تُستبعد وحدها، والصورة المقترحة وباقي الصور تبقى للمراجعة
            if (!settled) dropCandidate(item.key, candidate.url);
            sess.note = `رفضتها («${R.reasonLabel(reasonCode)}») وسجّلنا السبب، ولن نقترحها لهذا المنتج مرة أخرى. باقي الصور ما زالت للمراجعة${left}.`;
            message = `انرفضت الصورة وسجّلنا السبب. باقي الصور ما زالت للمراجعة${left}.`;
        } else {
            if (!kept && !settled) S.local.set(item.key, 'rejected');
            sess.note = kept
                ? `رفضتها («${R.reasonLabel(reasonCode)}») وسجّلنا السبب. الصورة المعتمدة قبل بتضل زي ما هي.`
                : `رفضتها («${R.reasonLabel(reasonCode)}») وسجّلنا السبب. المنتج رجع للطابور ورح ينبحث عنه من جديد بالتشغيل الجاي؛ وإذا في صورة صحيحة تحت، اختارها واعتمدها هلق.`;
        }
        R.toast(message, 'success');
        R.rebuild();
        R.renderList();
        if (S.openKey === item.key) renderWorkspace();
        R.markActive();
        // الخادم هو المرجع: قراءة هادئة تأتي بما حفظه فعلاً (المرشحون وحالة الطابور)
        if (!settled) R.loadData({ quiet: true });
    }

    // «تراجع عن الرفض»: بعد تأكيد، الخادم يشيل رفض الصورة (cli_bridge.undo_reject) فترجع للاقتراحات وما تنحسب
    // بالإحصائيات، و«دوّر مرة ثانية» بيقدر يلاقيها. الشيت والاعتماد ما بيتغيروا
    async function undoReject(item, url) {
        const S = st();
        const sess = sessionOf(item.key);
        if (!item || !url || sess.undoing || !S.urls.undoReject) return false;
        if (!(await R.ask({ title: R.UNDO_REJECT_CONFIRM, confirmText: 'رجّعها' }))) return false;
        sess.undoing = url;
        if (S.openKey === item.key) renderWorkspace();
        const res = await R.requestJson(S.urls.undoReject, { method: 'POST', body: R.undoRejectBody(boundContext(item), url) });
        sess.undoing = null;
        const data = (res && res.data) || {};
        const done = !!(res && res.ok && data.status === 'success');
        if (done || data.status === 'not_found') {
            sess.rejected.delete(url);
            sess.rejectedWhy.delete(url);
            R.forgetRejection(item.product, url);
            R.toast(done ? (data.still_rejected ? 'انشال رفض واحد، بس الصورة مرفوضة مرة ثانية لهالمنتج.'
                                                : 'رجعت الصورة للاقتراحات. «دوّر مرة ثانية» بيقدر يلاقيها.')
                         : 'ما في رفض مسجل لهالصورة.', done ? 'success' : 'info');
            R.loadData({ quiet: true });
        } else {
            R.toast(res && res.network ? 'ما قدرنا نوصل للخادم.' : `ما رجعت الصورة: ${R.plainError(data.error, 'جرّب مرة ثانية.')}`,
                    'danger', 9000);
        }
        if (S.openKey === item.key) renderWorkspace();
        return done;
    }

    // الصور اللي رفضها مراجع لهالمنتج، كل وحدة بزر «تراجع عن الرفض»
    function rejectedPanel(item) {
        const S = st();
        const sess = sessionOf(item.key);
        const list = R.rejectedImages(item.product, sess.rejected, sess.rejectedWhy);
        if (!list.length || !S.urls.undoReject || item.orphan) return null;
        return el('section', { className: 'rv-panel rv-rejected', id: 'rvRejected', 'aria-label': 'صور رفضتها لهالمنتج' }, [
            el('div', { className: 'rv-alts__head' }, [
                el('h3', { className: 'rv-h3', text: 'صور رفضتها لهالمنتج' }),
                el('span', { className: 'rv-alts__hint', text: 'رفضت وحدة بالغلط أو للتجربة؟ رجّعها للاقتراحات.' })
            ]),
            el('div', { className: 'rv-rejected__grid' }, list.map(r => el('div', { className: 'rv-rejected__item', dataset: { url: r.url } }, [
                el('span', { className: 'rv-rejected__thumb' }, [R.img(r.url, '', S.urls.imageProxy)]),
                el('span', { className: 'rv-alt__note rv-tone--danger', text: r.reason_code ? `مرفوضة: ${R.reasonLabel(r.reason_code)}` : 'مرفوضة' }),
                el('button', { type: 'button', className: 'lq-btn lq-btn--secondary lq-btn--sm', dataset: { undoReject: r.url },
                               disabled: !!sess.undoing, text: sess.undoing === r.url ? 'عم نرجّعها…' : R.UNDO_REJECT_LABEL,
                               onclick: () => undoReject(item, r.url) })
            ])))
        ]);
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
        if (n > 1 && !(await R.ask({ title: `رح نرجّع ${R.plural(n, 'منتج واحد', 'منتجات')} للطابور؟`,
                                     text: 'بنشيلها من الأعطال. ما رح يبلش أي تشغيل من هون: بتنعالج لما تشغّل التشغيل. الصور المعتمدة ما بتنلمس.',
                                     confirmText: 'رجّعها للطابور' }))) {
            return;
        }
        const res = await R.requestJson(S.urls.retry, { method: 'POST', body: { barcodes: items.map(it => R.failureKey(it.product)) } });
        const data = res.data || {};
        if (res.ok && data.status === 'success') {
            items.forEach(it => {
                S.local.set(it.key, 'requeued');
                R.settleSeen(it.key, null);      // إجراء المراجع نفسه: القراءة التالية لا تُعد «تغيّر بعد فتحه»
            });
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
        unknown: ['lq-check-row__status--unknown', 'minus', 'ما في معلومة']
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
        const sheetLink = (approved && approved.link) || R.shownApprovedUrl(p);
        const box = sheetLink
            ? el('div', { className: 'rv-sheetimg__box' }, [R.img(sheetLink, 'الصورة الحالية بالشيت', S.urls.imageProxy)])
            : el('div', { className: 'rv-sheetimg__box is-empty' }, [icon('image', 20), el('span', { text: 'ما في صورة بالشيت' })]);
        return el('header', { className: 'rv-panel rv-product' }, [
            el('div', { className: 'rv-product__main' }, [
                crumbs,
                el('h2', { className: 'rv-product__name' }, [bdi(p.product_name || p.product_name_ar || 'بلا اسم', null, p.product_name ? 'ltr' : 'auto')]),
                facts
            ]),
            el('div', { className: 'rv-sheetimg' }, [box, el('span', { className: 'rv-sheetimg__cap', text: 'الصورة الحالية' })])
        ]);
    }

    // «لماذا هذه الصورة؟» رقائق صغيرة من الأدلة (R.explainPick)؛ لا شيء إذا لم يوجد دليل
    function explainList(c, compact) {
        const lines = R.explainPick(c);
        if (!lines.length) return null;
        return el('ul', { className: 'rv-explain' + (compact ? ' rv-explain--compact' : ''), 'aria-label': 'لماذا هذه الصورة' },
                  lines.map(x => el('li', { className: 'rv-explain__chip', dataset: { explain: x.key } },
                                    [icon('check', 12, 2.4), el('span', { text: x.text })])));
    }
    R.explainList = explainList;

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
        const bgFailed = item.base === 'bg_failed' && pick.url === R.bgFailedLink(item.product);
        const badgeText = bgFailed ? 'معتمدة · الخلفية لم تُعزل' : isSystem ? 'مقترحة من النظام' : pick.source === 'manual' ? 'رابط من عندك'
            : pick.source === 'upload' ? 'صورة من جهازك' : 'اختيارك';
        const source = R.safeHttpUrl(pick.page_url);
        const res = pick.width && pick.height ? `${pick.width} × ${pick.height}` : '';
        return el('section', { className: 'rv-panel rv-pick', 'aria-label': 'الصورة المختارة', dataset: { url: pick.url } }, [
            el('div', { className: 'rv-pick__head' }, [
                el('div', { className: 'rv-pick__who' }, [
                    el('span', { className: 'rv-badge' + (isSystem ? '' : ' rv-badge--own') }, [icon('check', 14, 2.2), el('span', { text: badgeText })]),
                    isSystem ? R.laneBadge(pick) : null,
                    isExtra(pick) ? null : el('span', { className: 'rv-pick__store', title: where.host || null,
                                                        text: [where.store, where.market].filter(Boolean).join(' · ') }),
                    R.galleryNote(pick) ? el('span', { className: 'rv-gallery', text: R.galleryNote(pick) }) : null
                ]),
                source ? el('a', { className: 'rv-pick__source', href: source, target: '_blank', rel: 'noopener noreferrer' },
                            [el('span', { text: 'صفحة المصدر' }), icon('external', 14)]) : null
            ]),
            // ضغطة على الصورة (أو Z) بتكبّرها جنب صورة الشيت
            el('div', { className: 'rv-pick__stage', dataset: { zoom: item.key }, title: 'كبّر الصورة (Z)' }, [
                pickImage(item, pick),
                res ? el('span', { className: 'rv-pick__res', dir: 'ltr', text: res }) : null,
                overlay || null
            ]),
            pick.title ? bdi(pick.title, 'rv-pick__title') : null,
            pageGtinLine(R.pageGtinOf(item.product, pick)),
            explainList(pick, false)
        ]);
    }

    // «الباركود من صفحة المتجر: …» مع زر نسخ، ليلصقه المالك بالشيت بإيده (ما منكتب بالشيت تلقائياً)
    function pageGtinLine(gtin) {
        const code = String(gtin || '').trim();
        if (!code) return null;
        return el('div', { className: 'rv-gtin', dataset: { pageGtin: code } }, [
            el('span', { className: 'rv-gtin__label', text: R.PAGE_GTIN_LABEL }),
            el('bdi', { className: 'rv-gtin__code', dir: 'ltr', text: code }),
            el('button', { type: 'button', className: 'lq-btn lq-btn--ghost lq-btn--sm', dataset: { copyGtin: code },
                           'aria-label': `انسخ الباركود ${code}`, text: 'انسخ', onclick: () => copyText(code) })
        ]);
    }
    R.pageGtinLine = pageGtinLine;

    function copyText(text) {
        const nav = root.navigator;
        if (nav && nav.clipboard && typeof nav.clipboard.writeText === 'function') {
            nav.clipboard.writeText(text).then(() => R.toast('نسخنا الباركود.', 'success'),
                                               () => R.toast(`انسخه بإيدك: ${text}`, 'info', 9000));
        } else {
            R.toast(`انسخه بإيدك: ${text}`, 'info', 9000);
        }
    }

    function pickImage(item, pick) {
        const node = R.img(pick.url, pick.title || 'الصورة المختارة', st().urls.imageProxy, 'rv-pick__img');
        trackPick(item, pick, node);
        return node;
    }

    // الصورة هنا هي المصدر نفسه مصغّراً على خلفية بيضاء، وليست نتيجة عزل الخلفية (لا معاينة حقيقية للقص قبل
    // الاعتماد)، فالعنوان يقول ذلك ولا يدّعي أنها ما سيُرفع
    function previewCard(pick) {
        const S = st();
        return el('section', { className: 'rv-panel rv-preview', 'aria-label': 'المصدر قبل عزل الخلفية' }, [
            el('div', { className: 'rv-preview__canvas' }, [pick ? R.img(pick.url, 'الصورة المصدر قبل عزل الخلفية', S.urls.imageProxy, 'rv-preview__img')
                                                                : icon('image', 22, 1.6)]),
            el('div', { className: 'rv-preview__text' }, [
                el('strong', { className: 'rv-h3', text: 'المصدر قبل عزل الخلفية' }),
                el('span', { text: `هذه الصورة كما هي في مصدرها، وليست النتيجة النهائية. عند الاعتماد تُعزل خلفيتها وتوضع على لوحة بيضاء ${S.cfg.canvas}×${S.cfg.canvas} ثم تُرفع إلى Cloudinary.` })
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
                el('span', { className: 'rv-checks__src' }, [icon('sparkle', 14), el('span', { text: res.read ? 'قراءة الملصق' : 'نموذج القراءة ما قرأ هالصورة' })])
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

    function altButton(item, c, i, pick, sysUrl, noPick) {
        const S = st();
        const on = !!pick && c.url === pick.url;
        const note = R.candidateNote(c, !!sysUrl && c.url === sysUrl);
        const where = R.storeOf(c);
        const dim = (c.status === 'rejected' || c.status === 'excluded') && !on;
        // منتج بلا اقتراح: تحت كل صورة «لماذا لم تُختر» من أدلتها مكان الحالة العامة («مطابقة محتملة»)، مع تحذيراتها كما
        // لأي صورة؛ الصورة التي استبعدها النظام يقول سطرها لماذا
        const why = noPick && !isExtra(c) && !['rejected', 'excluded'].includes(c.status) ? R.whyNotPicked(c, item.product) : null;
        const showNote = !isExtra(c) && (!why || c.warnings.length > 0);
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
            R.galleryNote(c) ? el('span', { className: 'rv-gallery', text: R.galleryNote(c) }) : null,
            showNote ? el('span', { className: `rv-alt__note rv-tone--${note.tone}`, text: note.text }) : null,
            why ? el('span', { className: `rv-alt__why rv-tone--${why.tone}`, title: why.code }, [
                el('span', { className: 'rv-alt__why-k', text: 'ليش ما انختارت: ' }), el('span', { text: why.text })]) : null,
            // كل تحذيرات الصورة البديلة (الأول في السطر السابق)، وأدلتها
            !isExtra(c) && c.warnings.length > 1 && !['rejected', 'excluded'].includes(c.status)
                ? el('span', { className: 'rv-alt__warns' }, c.warnings.slice(1).map(w => el('span', { className: 'rv-alt__warn', text: R.warningText(w) })))
                : null,
            isExtra(c) || ['rejected', 'excluded'].includes(c.status) ? null : explainList(c, true)
        ]);
    }

    // opts.grid: منتج بلا اقتراح ولم يختر المراجع صورة بعد: الشبكة أول ما في مساحة العمل (الأفضل ترتيباً أولاً)، ولا
    // شيء مختار؛ الاعتماد بعد اختيار صريح فقط (1–9 أو ضغطة). opts.why: «لماذا لم تُختر» تحت كل صورة
    function altsCard(item, cands, pick, sysUrl, opts) {
        opts = opts || {};
        const noPick = !!opts.grid;
        const n = Math.min(cands.length, 9);
        const keys = n > 1 ? `1–${n}` : '1';
        return el('section', { className: 'rv-panel rv-alts' + (noPick ? ' rv-alts--nopick' : ''), id: noPick ? 'rvNoPickGrid' : null,
                               'aria-label': noPick ? 'الصور اللي لقاها البحث' : 'صور أخرى وجدها البحث' }, [
            el('div', { className: 'rv-alts__head' }, [
                el('h3', { className: 'rv-h3', text: noPick ? 'الصور اللي لقاها البحث' : 'صور أخرى وجدها البحث' }),
                el('span', { className: 'rv-alts__hint', text: noPick ? `ما في صورة مختارة: اختار وحدة (${keys} أو اضغط عليها)، وبعدين Enter لاعتمادها`
                    : n > 1 ? `اضغط 1–${n} لعرض صورة، و Enter لاعتمادها` : 'اضغط 1 لعرضها، و Enter لاعتمادها' })
            ]),
            el('div', { className: 'rv-alts__grid' }, cands.map((c, i) => altButton(item, c, i, pick, sysUrl, noPick || !!opts.why)))
        ]);
    }

    // «ليش ما في اقتراح؟»: جملة واحدة فيها الحقيقة وما العمل (catalog_match.explain)، وملاحظات الشيت الباقية تحتها.
    // الرمز في التلميح فقط
    function noPickBanner(explain, title) {
        const notes = R.sheetNotes(explain);
        const box = alertBox('info', title || 'ليش ما في اقتراح:', explain ? explain.text : R.NO_PICK_FALLBACK, [
            notes.length ? el('span', { className: 'rv-alert__more rv-nopick__sheet', text: `وكمان بالشيت: ${notes.join('، ')}.` }) : null
        ]);
        box.classList.add('rv-nopick');
        if (explain) {
            box.setAttribute('title', [explain.key, explain.engine].filter((v, i, a) => v && a.indexOf(v) === i).join(' · '));
            box.setAttribute('data-nopick', explain.key);
        }
        return box;
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
            el('p', { className: 'rv-nf__hint', text: 'الصورة من الرابط أو من جهازك بتصير هي المختارة، وبتنعتمد بـ «اعتماد» متل أي صورة.' })
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
            const why = R.noPickReason(item, search);
            if (!hasCands) {
                out.push(why ? noPickBanner(why, 'ما انلقت:')
                    : alertBox('info', 'ما انلقت:', 'ما لقينا صور مطابقة لهالمنتج. جرّب كلمات ثانية، أو حط رابط، أو ارفع صورة.'));
            } else if (!search.selectedUrl && why) {
                out.push(noPickBanner(why));
            } else if (search.decision === 'VERIFIER_DOWN') {
                out.push(alertBox('warning', 'نموذج القراءة مش متاح هلق:', 'ما في فحص بصري لهالنتائج، راجع الصور بنفسك قبل الاعتماد.'));
            } else if (search.decision === 'AUTO_PUBLISH') {
                out.push(alertBox('success', 'مطابقة مؤكدة بالكامل.', 'راجعها واعتمدها.'));
            } else if (!search.selectedUrl) {
                out.push(alertBox('info', 'ما في صورة مؤكدة:', 'اختار وحدة من الصور تحت، أو دوّر بكلمات ثانية.'));
            }
            if (code && out.length && !out[out.length - 1].classList.contains('rv-nopick')) out[out.length - 1].setAttribute('title', code);
            return out;
        }
        const p = item.product;
        const bucket = item.bucket;
        if (bucket === 'not_found' || bucket === 'failed') {
            const info = R.failureInfo(p.error_message, item.queue && item.queue.failure_code);
            // ما انلقت: الجملة التي تقول لماذا (catalog_match.explain) بدل نص الرمز العام، إن حُفظت
            const why = bucket === 'not_found' ? R.noPickReason(item) : null;
            const box = alertBox(bucket === 'failed' ? 'danger' : 'info', bucket === 'failed' ? 'عطل:' : 'ما انلقت:', why ? why.text : info.text, [
                el('span', { className: 'rv-alert__more', text: 'إعادة المحاولة بترجّعه للطابور، وبينبحث عنه بالتشغيل الجاي.' }),
                p.has_error ? el('button', { type: 'button', className: 'lq-btn lq-btn--secondary lq-btn--sm rv-retry', text: 'إعادة المحاولة',
                                             onclick: () => retryFailures([item]) }) : null,
                info.detail ? el('details', { className: 'rv-details' }, [el('summary', { text: 'التفاصيل التقنية' }), el('code', { dir: 'ltr', text: info.detail })]) : null
            ]);
            if (why) {
                box.classList.add('rv-nopick');
                box.setAttribute('data-nopick', why.key);
                box.setAttribute('title', why.key);
            }
            out.push(box);
        } else if (bucket === 'rejected' || bucket === 'requeued') {
            if (!sess.note) out.push(alertBox('neutral', 'رجعت للطابور:', 'رح ينبحث عنها من جديد بالتشغيل الجاي. إذا بدك هلق: دوّر بكلمات ثانية، أو حط رابط، أو ارفع صورة.'));
        } else if (bucket === 'queued') {
            out.push(alertBox('neutral', 'بالطابور:', 'رح ينبحث عنها بالتشغيل الجاي.'));
        } else if (bucket === 'searching') {
            out.push(alertBox('neutral', 'قيد البحث:', 'التشغيل عم يدوّر على صور لهالمنتج هلق.'));
        } else if (bucket === 'bg_failed') {
            out.push(alertBox('warning', 'الخلفية لم تُعزل:', 'اعتُمدت هذه الصورة ورُفعت، لكن عزل خلفيتها فشل، فرابطها في الشيت معلَّم «بحاجة مراجعة». '
                + 'إن كانت الصورة سليمة فاعتمدها مرة أخرى لتُعاد محاولة عزل الخلفية؛ وإن كان فيها هالة أو بقايا خلفية أو قص فارفضها بالسبب المناسب.'));
        } else if (bucket === 'stale' && hasCands) {
            out.push(alertBox('neutral', 'نتائج بحث سابق:', 'هالمنتج مش بانتظار المراجعة بالطابور، بس فيك تعتمد صورة منها.'));
        } else if (bucket === 'none' && hasCands && !systemPickUrl(item)) {
            out.push(noPickBanner(R.noPickReason(item)));
        } else if (item.product.has_error && !R.WAITING.includes(bucket)) {
            const info = R.failureInfo(p.error_message, null);
            out.push(alertBox('warning', 'آخر محاولة:', info.text));
        }
        return out;
    }

    // ما تغيّر في المنتج المفتوح بعد فتحه (قراءة هادئة، app.js detectMoved)، بالعربي
    function movedText(moved) {
        const a = moved.before || {};
        const b = moved.after || {};
        const parts = [];
        const was = String(a.approved_url || '').trim();
        const now = String(b.approved_url || '').trim();
        if (was !== now) parts.push(now ? (was ? 'الصورة المعتمدة له تغيّرت' : 'صار له صورة معتمدة') : 'الصورة المعتمدة له أُلغيت');
        if ((a.queue_status || null) !== (b.queue_status || null)) {
            parts.push(`حالته كانت «${R.queueText(a.queue_status)}» وصارت «${R.queueText(b.queue_status)}»`);
        } else if ((a.queue_updated_at || null) !== (b.queue_updated_at || null) || (a.queue_row || null) !== (b.queue_row || null)) {
            parts.push('تحدّث صفه في طابور التشغيل');
        }
        return parts.length ? parts.join('، ') : 'تغيّرت بياناته';
    }

    function movedBanner(item) {
        const S = st();
        const moved = S.moved.get(item.key);
        if (!moved) return null;
        const again = el('button', { type: 'button', className: 'lq-btn lq-btn--secondary lq-btn--sm rv-reopen', id: 'rvReopen',
                                     text: 'اعرضه من جديد', onclick: () => reopen(item.key) });
        return alertBox('warning', 'تغيّر هالمنتج بعد ما فتحته:',
                        `${movedText(moved)}. ما بتقدر تعتمد صورة له قبل ما تعرضه من جديد وتشوف وضعه هلق.`, [again]);
    }

    // «اعرضه من جديد»: نفس فتح المنتج من القائمة (لقطة جديدة لما يراه المراجع الآن)
    function reopen(key) {
        const S = st();
        if (!S.byKey.get(key)) return;
        resetMoved(S.byKey.get(key));
        R.snapshot(S.byKey.get(key));
        renderWorkspace();
        R.markActive();
    }

    // المنتج تغيّر بعد فتحه: إن تغيّرت صورته المعتمدة (اعتمد مراجع آخر، أو أُلغي اعتماد) فما بقي من هذه الجلسة عنه
    // (نتيجة بحث، اختيار، «تم الاعتماد») لا يُعرض فوق وضعه الجديد: يظهر كما هو الآن
    function resetMoved(item) {
        const S = st();
        const moved = S.moved.get(item.key);
        if (!moved) return;
        const was = String((moved.before || {}).approved_url || '');
        const now = String((moved.after || {}).approved_url || '');
        if (was !== now) {
            const sess = sessionOf(item.key);
            if (sess.search && sess.search.status === 'done') sess.search = null;
            sess.prev = null;
            sess.pick = null;
            sess.note = '';
            if (S.local.get(item.key) === 'approved') S.local.delete(item.key);
            S.approved.delete(item.key);
            R.rebuild();
            R.renderList();
        }
        S.moved.delete(item.key);
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
            } else if (S.counts.waiting === 0 && S.load.state === 'ready') {
                // خلصت المراجعة: شو انعمل اليوم وشو الخطوة الجاية، مش صفحة فاضية
                body.appendChild(R.doneRecap());
            } else {
                // القائمة عمود على اليسار (RTL)، أو تحت مساحة العمل بالشاشة الضيقة
                body.appendChild(el('div', { className: 'lq-empty rv-empty' }, [
                    el('span', { className: 'lq-empty__icon' }, [icon('review', 24)]),
                    el('h2', { className: 'lq-empty__title', text: 'اختار منتج من القائمة' }),
                    el('p', { className: 'lq-empty__text', text: R.listStacked() ? 'القائمة تحت؛ اختار منتج منها.' : 'القائمة على اليسار؛ ↑ ↓ للتنقل.' })
                ]));
            }
            updateBar();
            return;
        }

        const sess = sessionOf(item.key);
        const flag = S.local.get(item.key);
        body.appendChild(productHeader(item));
        const moved = movedBanner(item);
        if (moved) body.appendChild(moved);

        if (flag === 'approving') {
            S.ws.state = 'approving';
            const job = S.jobs ? S.jobs.activeFor(item.key) : null;
            const cand = job ? job.candidate : currentPick(item);
            if (job && job.state === 'held') {
                // لسا ما انبعت: «تراجع» بيرجّعه متل ما كان
                body.appendChild(alertBox('info', 'رح تنعتمد بعد لحظات:', 'بتقدر تتراجع قبل ما تنبعت.', [
                    el('button', { type: 'button', className: 'lq-btn lq-btn--secondary lq-btn--sm rv-undo__btn', text: 'تراجع',
                                   onclick: () => S.jobs.cancel(job.group) })
                ]));
            } else {
                body.appendChild(alertBox('info', 'عم تنعتمد بالخلفية:', 'بتقدر تكمل شغلك؛ الصورة بتنرفع وبينكتب رابطها بالشيت.'));
            }
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
            // C3: ما حدث في الشيت كما قاله الخادم، بلا ادعاء
            const sheet = R.sheetNote(done.sheet);
            body.appendChild(alertBox(sheet.tone, 'تم الاعتماد.', sheet.text ? `رُفعت الصورة ${sheet.text}` : 'رُفعت الصورة.'));
            const notes = done.notes || { flagTexts: [], noteTexts: [], duplicate: false, duplicateOf: [], flags: [] };
            if (done.warning) {
                const why = notes.flagTexts.length ? ` فحص القص: ${notes.flagTexts.join('، ')}.` : '';
                const box = alertBox('warning', 'الخلفية لم تُعزل:', (sheet.state === 'written'
                    ? 'كُتب الرابط في الشيت بعلامة «بحاجة مراجعة».' : 'الصورة بحاجة مراجعة: تجدها في رقاقة «الخلفية لم تُعزل».') + why);
                if (notes.flags.length) box.setAttribute('title', notes.flags.join(' · '));
                body.appendChild(box);
            }
            if (notes.publishedAnyway) {
                // نُشرت رغم ملاحظات فحص القص بعد تأكيد المراجع: الملاحظة تبقى ظاهرة على الصورة المعتمدة
                const box = alertBox('warning', 'انعتمدت رغم ملاحظات فحص القص:',
                                     `${notes.flagTexts.join('، ') || 'ملاحظات على شكل الصورة'}. راجع الصورة المعتمدة.`);
                if (notes.flags.length) box.setAttribute('title', notes.flags.join(' · '));
                body.appendChild(box);
            }
            if (notes.bgSkipped) {
                body.appendChild(alertBox('info', 'انعتمدت بدون عزل الخلفية:',
                                          'عزل الخلفية متوقف بالإعدادات، فالصورة انعتمدت متل ما هي على لوحة بيضا.'));
            }
            if (notes.bgFallback) {
                body.appendChild(alertBox('info', 'عزل محلي:', `${notes.bgFallback.text}، فانعزلت بـ ${notes.bgFallback.local}. `
                                          + 'اشحن الرصيد لجودة أحسن.'));
            }
            if (notes.noteTexts && notes.noteTexts.length) {
                body.appendChild(alertBox('info', 'ملاحظة من فحص القص:', `${notes.noteTexts.join('، ')}.`));
            }
            if (notes.duplicate) {
                const who = notes.duplicateOf.length ? `: ${notes.duplicateOf.map(n => `«${n}»`).join('، ')}` : '';
                body.appendChild(alertBox('warning', 'الصورة نفسها لمنتج آخر:', `هذه الصورة منشورة أيضاً لمنتج آخر${who}. تأكد أنها ليست صورة منتج مختلف.`));
            }
            const doneStage = el('div', { className: 'rv-final__stage' }, [R.img(done.link || done.url, 'الصورة المعتمدة', S.urls.imageProxy)]);
            body.appendChild(el('section', { className: 'rv-panel rv-final', dataset: { url: done.link } }, [
                doneStage,
                el('div', { className: 'rv-final__text' }, [
                    el('strong', { className: 'rv-h3', text: 'الصورة المعتمدة' }),
                    themeToggle(doneStage),
                    pageGtinLine(done.pageGtin),
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
            const sheetStage = el('div', { className: 'rv-final__stage' }, [R.img(link, 'الصورة الحالية بالشيت', S.urls.imageProxy)]);
            body.appendChild(el('section', { className: 'rv-panel rv-final', id: 'rvCurrentSheetImage', dataset: { url: link } }, [
                sheetStage,
                el('div', { className: 'rv-final__text' }, [
                    el('strong', { className: 'rv-h3', text: 'الصورة الحالية بالشيت' }),
                    themeToggle(sheetStage),
                    pageGtinLine(item.product.page_gtin),
                    el('span', { text: 'لهالمنتج صورة نهائية بالشيت، فما دوّرنا تلقائياً (كل بحث بيكلف من رصيد البحث).' }),
                    searchButton(item, 'دوّر على صورة بديلة')
                ])
            ]));
        } else if (cands.length) {
            S.ws.state = 'results';
            const pick = currentPick(item);
            const sysUrl = systemPickUrl(item);
            if (!pick && !sysUrl) {
                // بلا اقتراح ولا اختيار بعد: لا لوحة فارغة؛ الصور أول ما في مساحة العمل، كل وحدة مع «لماذا لم تُختر»
                body.appendChild(altsCard(item, cands, null, '', { grid: true }));
            } else {
                body.appendChild(el('div', { className: 'rv-compare' }, [
                    pickCard(item, pick, sysUrl, cands.length, null),
                    el('div', { className: 'rv-side' }, [previewCard(pick), pick ? checksCard(item, pick) : null])
                ]));
                // بلا اقتراح والمراجع اختار صورة: الصور الباقية تبقى مع «لماذا لم تُختر»
                body.appendChild(altsCard(item, cands, pick, sysUrl, { why: !sysUrl }));
            }
        } else {
            S.ws.state = 'empty';
            if (!item.orphan && !sess.searchError) {
                body.appendChild(el('div', { className: 'rv-empty-actions' }, [searchButton(item, 'دوّر على صور هلق')]));
            }
        }
        const rejected = rejectedPanel(item);
        if (rejected) body.appendChild(rejected);
        if (!item.orphan && (sess.nfOpen || S.ws.state === 'empty')) body.appendChild(notFoundPanel(item));
        updateBar();
        preloadAround(item);
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
        currentReasons().forEach((r, i) => box.appendChild(el('button', {
            type: 'button', className: 'rv-reason', dataset: { reason: r.code }, 'aria-keyshortcuts': i < 9 ? String(i + 1) : null,
            onclick: () => rejectCurrent(r.code)
        }, [i < 9 ? el('span', { className: 'rv-reason__n', text: String(i + 1) }) : null, el('span', { text: r.label })])));
        box.appendChild(el('label', { className: 'lq-check rv-reasons__research' }, [S.dom.researchBox, el('span', { text: 'دوّر على بدائل بعد الرفض' })]));
        box.appendChild(el('button', { type: 'button', className: 'lq-btn lq-btn--ghost lq-btn--sm', onclick: () => closeReasons() },
                           [el('span', { text: 'إلغاء' }), R.kbd('Esc')]));
        // ما يفعله الرفض وما يبقيه (cli_bridge.action_reject_image، عقد C2): السبب يُسجل والصورة لا تُقترح لهالمنتج مرة
        // ثانية؛ إذا بقي له صور ثانية يبقى بانتظار المراجعة فيها، وإلا يرجع للطابور؛ والصورة المعتمدة غيرها تبقى كما هي
        box.appendChild(el('span', { className: 'rv-reasons__note',
                                     text: 'بنسجّل السبب وما بنرجع نقترح هالصورة لهالمنتج. إذا ضل له صور ثانية بيضل بانتظار مراجعتك فيها، وإلا بيرجع للطابور؛ والصورة المعتمدة قبل (إذا في) ما بتنلمس.' }));
        const item = currentItem();
        if (item && item.base === 'bg_failed') {
            // الأسباب التجميلية لا تستبعد المصدر من البحث (local_cache_db.get_rejections)
            box.appendChild(el('span', { className: 'rv-reasons__note',
                                         text: 'هالة وبقايا الخلفية والقص أسباب تخص المعالجة: تُلغي هذا الاعتماد ويرجع المنتج للطابور، وقد يُقترح المصدر نفسه مرة أخرى.' }));
        }
    }

    // التكبير (ضغطة على الصورة أو Z): صور المنتج المفتوح بالترتيب اللي بتعرضه الشاشة، و← → بينهم، وجنبها صورة الشيت
    // الحالية. «اختارها» بيختار الصورة الظاهرة بس (متل 1–9)، والاعتماد بعدها من الشريط
    function openLightbox() {
        const S = st();
        const item = currentItem();
        if (!item || S.mode !== 'single') return false;
        const p = item.product;
        const approved = S.approved.get(item.key);
        const sheetUrl = (approved && approved.link) || R.shownApprovedUrl(p) || '';
        const compare = { url: sheetUrl, caption: 'الصورة الحالية بالشيت' };
        const describe = c => {
            const where = isExtra(c) ? null : R.storeOf(c);
            return { url: c.url, caption: c.title || '', note: where ? [where.store, where.market].filter(Boolean).join(' · ') : '' };
        };
        if (S.ws.state === 'results') {
            const cands = currentCandidates(item);
            const pick = currentPick(item);
            if (!cands.length) return false;
            const index = pick ? Math.max(0, cands.findIndex(c => c.url === pick.url)) : 0;
            return R.openLightbox({
                items: cands.map(describe), index: index, title: productLabel(item), compare: compare,
                chosen: i => !!pick && cands[i].url === pick.url,
                onChoose: i => {
                    if (st().openKey !== item.key || !cands[i]) return;
                    sessionOf(item.key).pick = cands[i].url;
                    renderWorkspace();
                }
            });
        }
        const job = S.jobs ? S.jobs.activeFor(item.key) : null;
        const url = S.ws.state === 'approving' ? ((job && job.candidate.url) || '')
            : S.ws.state === 'approved' && approved ? (approved.link || approved.url)
            : S.ws.state === 'final' ? String(p.existing_image_link || p.cached_image || '') : '';
        if (!url) return false;
        return R.openLightbox({ items: [{ url: url, caption: productLabel(item) }], index: 0, title: productLabel(item),
                                compare: S.ws.state === 'final' ? null : compare });
    }

    // السرعة: صور المنتجين الجايين و أول 4 صور بديلة للمنتج المفتوح بتتحمّل بالخلفية (new Image)، فلما تنفتح بتطلع فوراً
    // من كاش المتصفح (/api/image-proxy: Cache-Control private, max-age=3600)
    const preloaded = new Set();

    function preload(url) {
        const S = st();
        if (!url || preloaded.has(url) || typeof root.Image !== 'function') return;
        const src = R.imageUrl(url, S.urls.imageProxy);
        if (!src || src.startsWith('blob:')) return;
        if (preloaded.size > 500) preloaded.clear();
        preloaded.add(url);
        const im = new root.Image();
        im.decoding = 'async';
        im.referrerPolicy = 'no-referrer';
        im.src = src;
    }
    R.preloadImage = preload;

    function preloadAround(item) {
        if (!item) return;
        const sysUrl = systemPickUrl(item);
        const pick = currentPick(item);
        currentCandidates(item).filter(c => !isExtra(c) && c.url !== (pick && pick.url) && c.url !== sysUrl)
            .slice(0, 4).forEach(c => preload(c.url));
        let key = item.key;
        for (let i = 0; i < 2; i++) {
            const next = neighbour(key, 1, true);
            if (!next) break;
            const sel = R.storedSelected(next.product) || R.storedCandidates(next.product)[0];
            if (sel) preload(sel.url);
            key = next.key;
        }
    }

    function updateBar() {
        const S = st();
        const d = S.dom;
        if (!d.approveBtn) return;
        const item = currentItem();
        const block = approveBlock();
        d.approveBtn.disabled = block !== '';
        d.approveBtn.title = block;
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
        sessionOf, currentItem, currentCandidates, currentPick, systemPickUrl, boundContext, openItem, startSearch, reopen,
        resetMoved, approveBlock,
        cancelPendingSearch, applySearchResponse, canApprove, canReject, selectByNumber, approveCurrent, sendJob,
        settleJob, confirmReplace, confirmPublishAnyway, confirmBgSkip, undoReject, rejectedPanel, openReasons, closeReasons, currentReasons, rejectCurrent, skip, move, toggleNotFound,
        previewUrl, chooseFile, retryFailures, renderWorkspace, updateBar, updateJobsOffset, isOpen, updatePosition,
        openLightbox, jobBodySize, preloadAround
    };
})(typeof window !== 'undefined' ? window : globalThis);
