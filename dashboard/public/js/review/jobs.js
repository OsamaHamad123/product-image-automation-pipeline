/*
 * لقطة · المراجعة — background queue for approvals and rejections (bulk ones, and single-mode ones without a new search).
 *
 * The reviewer keeps working while approvals run: each approve / upload / bulk reject becomes a job, and the queue
 * sends at most `concurrency` requests at a time (2 for approvals: the server serialises each product under its own
 * publish lock, local_cache_db.sku_publish_lock, and every sheet write carries the row's identity), in order. A
 * product has at most one job held, waiting or running (a second approve of the same product is refused), and a
 * failed job keeps its reason until the reviewer retries or dismisses it.
 *
 * Undo: an approval (or a bulk reject) can be held for a few seconds (enqueue(spec, { holdMs })) before it is sent, so
 * «تراجع» takes it back (cancel(group)); enqueueMany(specs, { holdMs, onCancel }) gives the group its own undo handler
 * (bulk rejects: the card goes back as it was), otherwise options.onCancel. watch(fn) is told after every onUpdate. A held approval is never lost silently: when the page is hidden or closed (flush(), wired to
 * visibilitychange / pagehide by app.js) every held or waiting job is sent at once with fetch keepalive, which the
 * browser completes even after the page is gone, as far as the keepalive budget allows; the rest stay queued and the
 * page's beforeunload prompt covers them.
 *
 * An approval the server refused because the product changed since the page showed it (already_approved /
 * state_changed, contract C1) keeps what changed (job.stale); only an explicit «replace» resends it, with replace.
 * An approval refused because the cut-out failed its check (quality_flags / background_failed) keeps the flags
 * (job.quality); presentation-only flags are published only by an explicit «publish anyway» (publish_anyway).
 */
(function (root) {
    'use strict';

    const R = root.LaqtaReview = root.LaqtaReview || {};

    // keepalive requests of one page may carry 64 KB of body in flight together: a margin under it
    const KEEPALIVE_BUDGET = 60000;

    function createJobQueue(options) {
        options = options || {};
        const jobs = [];
        const running = new Set();
        let seq = 0;
        let groupSeq = 0;
        // الدفعة الحالية: «جاري اعتماد N · i من N» يعدّ طلباتها فقط، وفشل دفعة سابقة يبقى ظاهراً بزر إعادته
        let batch = 0;
        let keepaliveBytes = 0;
        const timers = new Map();      // group -> release timer
        const cancelers = new Map();   // group -> its own «تراجع» handler (enqueueMany opts.onCancel)
        const watchers = [];

        function concurrency() {
            const raw = typeof options.concurrency === 'function' ? options.concurrency() : options.concurrency;
            const n = parseInt(raw, 10);
            return isFinite(n) && n > 0 ? Math.min(n, 4) : 1;
        }

        function sentOrQueued(j) {
            return j.state === 'waiting' || j.state === 'running';
        }

        // طلب جديد والطابور فاضي يبدأ دفعة جديدة: الطلبات الناجحة من الدفعة السابقة تُزال، والفاشلة تبقى
        function startBatchIfIdle() {
            if (jobs.some(sentOrQueued)) return;
            for (let i = jobs.length - 1; i >= 0; i--) {
                if (jobs[i].state === 'done') jobs.splice(i, 1);
            }
            batch += 1;
        }

        function notify() {
            const s = state();
            if (typeof options.onUpdate === 'function') options.onUpdate(s);
            // بعد ما ترسم الصفحة (renderJobs): مثلاً وضع الجملة بيكتب «رفضت» بصندوق «تراجع» بدل «اعتمدت»
            watchers.slice().forEach(fn => {
                try {
                    fn(s);
                } catch (err) {
                    if (root.console) root.console.error(err);
                }
            });
        }

        function watch(fn) {
            if (typeof fn === 'function' && !watchers.includes(fn)) watchers.push(fn);
        }

        function heldGroupDone(group) {
            clearTimeout(timers.get(group));
            timers.delete(group);
            cancelers.delete(group);
        }

        function activeFor(key) {
            return jobs.find(j => j.key === key && (j.state === 'held' || j.state === 'waiting' || j.state === 'running')) || null;
        }

        // شي لسا ما خلص: محجوز للتراجع، أو بالدور، أو عم ينبعت
        // اعتماد قبله الخادم (approval_jobs) بيكمّل ولو انسكّرت الصفحة: ما بيمنع الطلوع منها
        function busy() {
            return jobs.some(j => j.state === 'held' || (sentOrQueued(j) && !j.accepted));
        }

        // الخادم سجّل الاعتماد (single.sendJob): بيضل «جاري» بالصفحة لحد ما توصل نتيجته، بس صار آمن تطلع
        // وبيفضّي مكانه بالدور: الاعتماد الجاي بينبعت فوراً (الخادم بيرتّبهم وبيحمي كل منتج بقفل نشره)
        function accept(job) {
            if (!job || job.accepted) return;
            job.accepted = true;
            running.delete(job);
            notify();
            pump();
        }

        function errorOf(result, job) {
            if (!result || result.network) return { text: 'ما قدرنا نوصل للخادم.', detail: 'network', stale: null, quality: null };
            const data = result.data || {};
            const raw = String(data.error || data.message || data.error_code || (result.status ? `HTTP ${result.status}` : ''));
            const stale = R.staleInfo ? R.staleInfo(data, job && job.expected) : null;
            const quality = R.qualityInfo ? R.qualityInfo(data) : null;
            const text = stale ? stale.text : (quality ? quality.text : R.plainError(raw, 'ما انعتمدت.'));
            return { text: text, detail: raw, stale: stale, quality: quality };
        }

        // specs: [{ key, type: 'approve' | 'upload' | 'reject', label, ... }] → the jobs queued (a product that already has
        // one is skipped). opts.holdMs > 0: the jobs wait that long (one group, cancel(group) takes them back) before
        // they are sent; uploads are never held (a file cannot go out with keepalive when the page closes).
        // opts.onCancel: this group's own «تراجع» handler (instead of options.onCancel)
        function enqueueMany(specs, opts) {
            opts = opts || {};
            const holdMs = Math.max(0, parseInt(opts.holdMs, 10) || 0);
            const out = [];
            let group = null;
            (specs || []).forEach(spec => {
                if (!spec || !spec.key || activeFor(spec.key)) return;
                const hold = holdMs > 0 && (spec.type === 'approve' || spec.type === 'reject');
                if (hold && group === null) group = ++groupSeq;
                if (!hold) startBatchIfIdle();
                const job = Object.assign({}, spec, { id: ++seq, batch: hold ? 0 : batch, state: hold ? 'held' : 'waiting',
                                                      group: hold ? group : null, releaseAt: hold ? Date.now() + holdMs : 0,
                                                      error: '', detail: '', result: null });
                jobs.push(job);
                out.push(job);
            });
            if (group !== null) {
                timers.set(group, setTimeout(() => release(group), holdMs));
                if (typeof opts.onCancel === 'function') cancelers.set(group, opts.onCancel);
            }
            if (out.length) {
                notify();
                pump();
            }
            return out;
        }

        function enqueue(spec, opts) {
            return enqueueMany([spec], opts)[0] || null;
        }

        // انتهت مهلة التراجع: تدخل الدور بترتيبها
        function release(group) {
            heldGroupDone(group);
            const held = jobs.filter(j => j.state === 'held' && j.group === group);
            if (!held.length) return;
            startBatchIfIdle();
            held.forEach(j => {
                j.state = 'waiting';
                j.batch = batch;
            });
            notify();
            pump();
        }

        // «تراجع»: طلبات المجموعة اللي لسا محجوزة تنشال (ما انبعت منها شي) → الطلبات اللي انشالت
        function cancel(group) {
            const handler = cancelers.get(group) || options.onCancel;
            heldGroupDone(group);
            const taken = [];
            for (let i = jobs.length - 1; i >= 0; i--) {
                if (jobs[i].state === 'held' && jobs[i].group === group) taken.unshift(jobs.splice(i, 1)[0]);
            }
            if (taken.length) {
                if (typeof handler === 'function') {
                    try {
                        handler(taken);
                    } catch (err) {
                        if (root.console) root.console.error(err);
                    }
                }
                notify();
            }
            return taken;
        }

        function bodySize(job) {
            if (typeof options.bodySize !== 'function') return null;
            const n = options.bodySize(job);
            return typeof n === 'number' && isFinite(n) && n >= 0 ? n : null;
        }

        // الصفحة عم تختفي (تبويب ثاني، أو عم تتسكّر): كل طلب محجوز أو بالدور بينبعت هلق مع keepalive (المتصفح بيكمّله ولو
        // انسكّرت الصفحة)، قد ما بتسمح ميزانية keepalive؛ والباقي بيضل بالدور
        function flush() {
            const held = jobs.filter(j => j.state === 'held');
            if (held.length) {
                startBatchIfIdle();
                held.forEach(j => {
                    heldGroupDone(j.group);
                    j.state = 'waiting';
                    j.batch = batch;
                });
            }
            jobs.filter(j => j.state === 'waiting').forEach(j => {
                if (j.state !== 'waiting' || conflicts(j)) return;
                const size = bodySize(j);
                if (size === null || keepaliveBytes + size > KEEPALIVE_BUDGET) return;
                start(j, { keepalive: true, size: size });
            });
            notify();
            pump();
            return held.length;
        }

        // طلب بالدور لمنتج (conflictKey، sku_key) عم ينبعت له طلب ثاني: بيستنى دوره، والطلب اللي بعده بيمشي
        function conflicts(job) {
            if (typeof options.conflictKey !== 'function') return false;
            const k = options.conflictKey(job);
            return !!k && Array.from(running).some(r => options.conflictKey(r) === k);
        }

        function pump() {
            while (running.size < concurrency()) {
                const next = jobs.find(j => j.state === 'waiting' && !conflicts(j));
                if (!next) return;
                // صفحة مخفية: الطلب بيروح مع keepalive إذا بتسمح الميزانية، فما بيضيع إذا انسكّرت
                const hidden = typeof options.hidden === 'function' && options.hidden();
                const size = hidden ? bodySize(next) : null;
                const keep = size !== null && keepaliveBytes + size <= KEEPALIVE_BUDGET;
                start(next, keep ? { keepalive: true, size: size } : {});
            }
        }

        async function start(job, sendOpts) {
            sendOpts = sendOpts || {};
            running.add(job);
            job.state = 'running';
            job.keepalive = !!sendOpts.keepalive;
            if (sendOpts.keepalive) keepaliveBytes += sendOpts.size || 0;
            notify();
            let result;
            try {
                result = await options.send(job, { keepalive: !!sendOpts.keepalive });
            } catch (err) {
                result = { ok: false, status: 0, network: true, data: { status: 'error', error: String(err && err.message || err) } };
            }
            if (sendOpts.keepalive) keepaliveBytes = Math.max(0, keepaliveBytes - (sendOpts.size || 0));
            running.delete(job);
            const success = !!(result && result.ok && result.data && result.data.status === 'success');
            job.result = result;
            job.state = success ? 'done' : 'failed';
            job.stale = null;
            job.quality = null;
            if (!success) {
                const e = errorOf(result, job);
                job.error = e.text;
                job.detail = e.detail;
                job.stale = e.stale;
                job.quality = e.quality;
            }
            if (typeof options.onSettle === 'function') {
                try {
                    options.onSettle(job);
                } catch (err) {
                    if (root.console) root.console.error(err);
                }
            }
            notify();
            if (jobs.some(j => j.state === 'waiting')) {
                pump();
            } else if (!running.size && typeof options.onDrain === 'function') {
                options.onDrain(state());
            }
        }

        // إعادة طلب فشل: نفس الجسم، بشرط ألا يكون للمنتج طلب آخر جارٍ. patch: ما يتغيّر في الطلب المعاد فقط
        // (الاستبدال بعد تأكيد صريح: { replace: true, expected: ما يعرفه الخادم الآن })
        function retry(id, patch) {
            const job = jobs.find(j => j.id === id && j.state === 'failed');
            if (!job || activeFor(job.key)) return false;
            if (typeof options.canRetry === 'function' && !options.canRetry(job)) return false;
            if (patch && typeof patch === 'object') Object.assign(job, patch);
            startBatchIfIdle();
            job.batch = batch;
            job.state = 'waiting';
            job.error = '';
            job.detail = '';
            // الصفحة تعرض المنتج «جاري الاعتماد» (أو الرفض) من جديد، مثل أول مرة
            if (typeof options.onRetry === 'function') {
                try {
                    options.onRetry(job);
                } catch (err) {
                    if (root.console) root.console.error(err);
                }
            }
            notify();
            pump();
            return true;
        }

        // طلب خلص أو فشل بينشال من اللوحة لحاله (رفض انضغط «تراجع» عليه وفشل: ما في شي يتراجع عنه)
        function drop(id) {
            const i = jobs.findIndex(j => j.id === id && (j.state === 'done' || j.state === 'failed'));
            if (i < 0) return false;
            jobs.splice(i, 1);
            notify();
            return true;
        }

        function dismiss() {
            for (let i = jobs.length - 1; i >= 0; i--) {
                if (jobs[i].state === 'done' || jobs[i].state === 'failed') jobs.splice(i, 1);
            }
            notify();
        }

        // jobs: كل ما يظهر في اللوحة (الدفعة الحالية وفشل الدفعات السابقة؛ المحجوز للتراجع مش منها). total/done/failed/
        // settled: الدفعة الحالية فقط، فـ«i من N» لا يعدّ طلباً فشل قبلها. failedAll: كل الفاشلة الظاهرة بزر إعادتها.
        // held: مجموعات «تراجع» اللي لسا محجوزة، الأحدث آخراً { group, jobs, releaseAt }
        function state() {
            const shown = jobs.filter(j => j.state !== 'held');
            const current = shown.filter(j => j.batch === batch);
            const count = s => current.filter(j => j.state === s).length;
            const groups = new Map();
            jobs.filter(j => j.state === 'held').forEach(j => {
                if (!groups.has(j.group)) groups.set(j.group, { group: j.group, jobs: [], releaseAt: j.releaseAt });
                groups.get(j.group).jobs.push(j);
            });
            return {
                jobs: shown,
                batchJobs: current,
                total: current.length,
                done: count('done'),
                failed: count('failed'),
                waiting: count('waiting'),
                running: count('running'),
                settled: count('done') + count('failed'),
                failedAll: shown.filter(j => j.state === 'failed').length,
                busy: shown.some(sentOrQueued),
                // جاري عالخادم: الصفحة بتستنى نتيجته بس صار آمن تسكّرها
                onServer: shown.filter(j => j.state === 'running' && j.accepted).length,
                held: Array.from(groups.values()).sort((a, b) => a.group - b.group),
                heldCount: jobs.filter(j => j.state === 'held').length
            };
        }

        return { enqueue, enqueueMany, release, cancel, flush, retry, drop, dismiss, state, busy, accept, activeFor, watch,
                 has: key => !!activeFor(key), sending: () => jobs.some(sentOrQueued) };
    }

    R.createJobQueue = createJobQueue;
    R.KEEPALIVE_BUDGET = KEEPALIVE_BUDGET;
})(typeof window !== 'undefined' ? window : globalThis);
