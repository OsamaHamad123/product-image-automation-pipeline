/*
 * لقطة · المراجعة — background queue for approvals (and bulk rejections).
 *
 * The reviewer keeps working while approvals run: each approve / upload / bulk reject becomes a job, and the queue
 * sends exactly one request at a time, in order. A product has at most one job waiting or running (a second approve
 * of the same product is refused), and a failed job keeps its reason until the reviewer retries or dismisses it.
 * An approval the server refused because the product changed since the page showed it (already_approved /
 * state_changed, contract C1) keeps what changed (job.stale); only an explicit «replace» resends it, with replace.
 * An approval refused because the cut-out failed its check (quality_flags / background_failed) keeps the flags
 * (job.quality); presentation-only flags are published only by an explicit «publish anyway» (publish_anyway).
 */
(function (root) {
    'use strict';

    const R = root.LaqtaReview = root.LaqtaReview || {};

    function createJobQueue(options) {
        options = options || {};
        const jobs = [];
        let running = null;
        let seq = 0;
        // الدفعة الحالية: «جاري اعتماد N · i من N» يعدّ طلباتها فقط، وفشل دفعة سابقة يبقى ظاهراً بزر إعادته
        let batch = 0;

        // طلب جديد والطابور فاضي يبدأ دفعة جديدة: الطلبات الناجحة من الدفعة السابقة تُزال، والفاشلة تبقى
        function startBatchIfIdle() {
            if (busy()) return;
            for (let i = jobs.length - 1; i >= 0; i--) {
                if (jobs[i].state === 'done') jobs.splice(i, 1);
            }
            batch += 1;
        }

        function notify() {
            if (typeof options.onUpdate === 'function') options.onUpdate(state());
        }

        function activeFor(key) {
            return jobs.find(j => j.key === key && (j.state === 'waiting' || j.state === 'running')) || null;
        }

        function busy() {
            return jobs.some(j => j.state === 'waiting' || j.state === 'running');
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

        // job: { key, type: 'approve' | 'upload' | 'reject', label, ... } → the job, or null if this product already has one
        function enqueue(spec) {
            if (!spec || !spec.key || activeFor(spec.key)) return null;
            startBatchIfIdle();
            const job = Object.assign({}, spec, { id: ++seq, batch: batch, state: 'waiting', error: '', detail: '', result: null });
            jobs.push(job);
            notify();
            pump();
            return job;
        }

        async function pump() {
            if (running) return;
            const next = jobs.find(j => j.state === 'waiting');
            if (!next) return;
            running = next;
            next.state = 'running';
            notify();
            let result;
            try {
                result = await options.send(next);
            } catch (err) {
                result = { ok: false, status: 0, network: true, data: { status: 'error', error: String(err && err.message || err) } };
            }
            const success = !!(result && result.ok && result.data && result.data.status === 'success');
            next.result = result;
            next.state = success ? 'done' : 'failed';
            next.stale = null;
            next.quality = null;
            if (!success) {
                const e = errorOf(result, next);
                next.error = e.text;
                next.detail = e.detail;
                next.stale = e.stale;
                next.quality = e.quality;
            }
            running = null;
            if (typeof options.onSettle === 'function') {
                try {
                    options.onSettle(next);
                } catch (err) {
                    if (root.console) root.console.error(err);
                }
            }
            notify();
            if (jobs.some(j => j.state === 'waiting')) {
                pump();
            } else if (typeof options.onDrain === 'function') {
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

        function dismiss() {
            for (let i = jobs.length - 1; i >= 0; i--) {
                if (jobs[i].state === 'done' || jobs[i].state === 'failed') jobs.splice(i, 1);
            }
            notify();
        }

        // jobs: كل ما يظهر في اللوحة (الدفعة الحالية وفشل الدفعات السابقة). total/done/failed/settled: الدفعة الحالية
        // فقط، فـ«i من N» لا يعدّ طلباً فشل قبلها. failedAll: كل الفاشلة الظاهرة بزر إعادتها
        function state() {
            const current = jobs.filter(j => j.batch === batch);
            const count = s => current.filter(j => j.state === s).length;
            return {
                jobs: jobs.slice(),
                batchJobs: current,
                total: current.length,
                done: count('done'),
                failed: count('failed'),
                waiting: count('waiting'),
                running: count('running'),
                settled: count('done') + count('failed'),
                failedAll: jobs.filter(j => j.state === 'failed').length,
                busy: busy()
            };
        }

        return { enqueue, retry, dismiss, state, busy, activeFor, has: key => !!activeFor(key) };
    }

    R.createJobQueue = createJobQueue;
})(typeof window !== 'undefined' ? window : globalThis);
