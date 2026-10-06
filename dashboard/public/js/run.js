/*
 * لقطة · التشغيل (resources/views/dashboard/batch_automation.blade.php).
 *
 * Reads GET /api/run/live (the /api/batch-status payload the sidebar uses, plus the run's summary and latest
 * rows) every few seconds and GET /api/run/plan for «قبل ما تبدأ». Writes only through the Phase 1 run
 * controls: POST /api/run-all, /api/batch/pause, /api/batch/resume, /api/stop-batch, /api/batch/reset.
 *
 * describeLive / describePlan / runBody are pure (tests/test_laqta_run.py runs them under node);
 * createController holds the page logic with injected fetch / render / confirm so it is testable too.
 */
(function (root) {
    'use strict';

    var C = root.LaqtaRunCommon;
    var POLL_ACTIVE_MS = 4000;
    var POLL_IDLE_MS = 8000;

    // No silent destructive button: each confirmation says what happens and what is kept.
    var STOP_CONFIRM_TEXT = 'إيقاف التشغيل:\n' +
        '• يكمّل العامل المنتجات الجارية ثم يتوقف ويكتب تقرير التشغيل (إذا ما توقف خلال دقيقة ونص بيُنهى)، وإن كان التشغيل ما زال يقرأ الشيت فيتوقف قبل البحث عن أي منتج.\n' +
        '• تعود الصفوف التي كانت قيد المعالجة إلى الانتظار لتُعالج في التشغيل القادم.\n' +
        '• لا يُحذف أي صف: المنتجات الجاهزة للمراجعة والمعتمدة والفاشلة تبقى كما هي.\n\n' +
        'بدك توقف التشغيل؟';

    var RESET_CONFIRM_TEXT = 'إصلاح تشغيل عالق (استعمله بس إذا التشغيل عالق أو ضلّ عطل قديم ظاهر):\n' +
        '• يُوقف أي عامل ما زال يعمل في الخلفية بعد المنتجات الجارية (إذا ما توقف خلال دقيقة ونص بيُنهى).\n' +
        '• يحذف ملف القفل وعدادات التقدم والتنبيه، ويلغي الإيقاف المؤقت.\n' +
        '• تعود الصفوف العالقة في «قيد المعالجة» إلى الانتظار.\n' +
        '• يُعاد تحميل قائمة المنتجات من الشيت.\n' +
        '• لا يُحذف أي منتج جاهز للمراجعة أو معتمد أو فاشل، ولا أي مرشح أو قرار مراجعة.\n\n' +
        'بدك تكمّل؟';

    var FORCE_CONFIRM_TEXT = 'إعادة البحث حتى للمنتجات اللي إلها صورة:\n' +
        '• منبحث من جديد عن كل منتجات النطاق، حتى اللي إلها صورة نهائية أو بانتظار مراجعتك.\n' +
        '• اقتراحات المنتجات اللي بانتظار المراجعة بتتبدّل بنتائج البحث الجديد.\n' +
        '• الصور المنشورة بالشيت بتضل مكانها لحد ما تعتمد غيرها (أو ينشر النشر الآلي صورة مؤكدة لماركتها)، ' +
        'وما بينكتب أبداً فوق صورة اعتمدها مراجع.\n\n' +
        'بدك تبدأ؟';

    var CHIPS = {
        loading: { label: 'لحظة…', status: 'none' },
        unavailable: { label: 'مش معروف', status: 'error' },
        starting: { label: 'عم يجهّز', status: 'none' },
        running: { label: 'يعمل', status: 'proposed' },
        paused: { label: 'متوقف مؤقتاً', status: 'warning' },
        stopping: { label: 'عم يوقف', status: 'warning' },
        error: { label: 'توقف بعطل', status: 'error' },
        finished: { label: 'خلص', status: 'approved' },
        stopped: { label: 'وقف قبل ما يخلص', status: 'warning' },
        idle: { label: 'ما في تشغيل', status: 'none' }
    };

    // A server text the page may show as is: plain Arabic, never an English message or an exception.
    function arabic(text) {
        return typeof text === 'string' && /[؀-ۿ]/.test(text);
    }

    function batchOf(snapshot) {
        return snapshot && snapshot.status === 'success' && snapshot.batch && typeof snapshot.batch === 'object'
            ? snapshot.batch : null;
    }

    /*
     * The «التشغيل الحالي» card and the red banner from one /api/run/live snapshot.
     * Progress is the current run only (batch.run, rows of its run_id), exactly what the sidebar shows.
     */
    function describeLive(snapshot, nowSec, fallbackRate) {
        var b = batchOf(snapshot);
        if (!b) {
            return {
                state: 'unavailable', chip: CHIPS.unavailable, meta: '',
                message: (snapshot && snapshot.message) || 'تعذّر قراءة حالة التشغيل هلق. منعيد المحاولة بعد شوي.',
                banner: { visible: false, text: '', title: '', variant: 'danger' },
                progress: null, finished: null, stuck: { visible: false, reason: '' }, recent: [],
                startBlocked: false, phase: null
            };
        }
        var phase = String(b.phase || 'idle');
        var active = C.isActive(phase);
        var run = snapshot.run && typeof snapshot.run === 'object' ? snapshot.run : null;
        var runB = b.run && typeof b.run === 'object' ? b.run : {};
        // a run that stopped with rows still waiting did not finish: say so instead of «خلص»
        var unfinished = C.runUnfinished(run);
        // the summary is of the run before a newer one that has no rows yet (starting, or failed before its search).
        // Without a newer run (after «إصلاح تشغيل عالق», which clears the state's run id) it is simply the last run.
        var previous = !!run && run.is_state_run === false && (active || phase === 'error');
        var view = {
            phase: phase,
            state: active ? phase : (phase === 'error' ? 'error'
                : (run && run.total > 0 ? (unfinished ? 'stopped' : 'finished') : 'idle')),
            meta: '',
            message: '',
            banner: {
                visible: !!b.alert,
                text: C.alertText(b.alert),
                title: phase === 'error' ? 'وقف التشغيل:' : 'انتبه:',
                variant: phase === 'error' ? 'danger' : 'warning'
            },
            progress: null,
            finished: null,
            stuck: { visible: !!b.stuck, reason: String(b.stuck || '') },
            recent: [],
            startBlocked: active,
            // a new run stopped before it searched anything: say so, and show the run before it separately
            startFailed: phase === 'error' && previous
        };
        view.chip = CHIPS[view.state] || CHIPS.idle;

        if (active) {
            var starting = phase === 'starting';
            var total = C.num(runB.total) || 0;
            var done = Math.min(C.num(runB.processed) || 0, total);
            var percent = total > 0 ? Math.round(done / total * 100) : 0;
            var current = run && run.current ? run : null;
            var rate = current && C.num(current.per_product_s) ? current.per_product_s : C.num(fallbackRate);
            var remaining = '';
            if (phase === 'running' && total > done && rate) {   // no estimate while paused, starting or stopping
                var secs = (total - done) * rate;
                remaining = secs < 60 ? 'متبقي أقل من دقيقة' : 'متبقي تقريباً ' + C.durationText(secs);
            }
            var counts = current ? current.counts : {
                proposed: 0, none: C.num(runB.ready_for_review) || 0, not_found: 0, error: C.num(runB.failed) || 0,
                requeued: 0, approved: C.num(runB.completed) || 0
            };
            // The bar and its legend are the processed rows only, so the fill is exactly «N من M» (the sidebar and
            // the Home card show the same). A row back in the queue after an outage waits again: it is not filled,
            // and «آخر النتائج» shows it as «رجعت للطابور».
            var processedCounts = {};
            for (var key in counts) {
                if (Object.prototype.hasOwnProperty.call(counts, key)) processedCounts[key] = counts[key];
            }
            processedCounts.requeued = 0;
            var tiles = C.resultTiles(processedCounts);
            view.progress = {
                starting: starting,
                done: starting ? null : done,
                total: starting ? null : total,
                percent: starting ? null : percent,
                percentText: starting ? '—' : percent + '%',
                countsText: starting ? '' : done + ' من ' + total + ' في هذا التشغيل',
                remaining: remaining,
                segments: starting || total <= 0 ? [] : tiles.filter(function (t) { return t.value > 0; })
                    .map(function (t) { return { tone: t.tone, pct: Math.round(t.value / total * 1000) / 10 }; }),
                legend: starting ? [] : tiles,
                phaseText: phase === 'running' && b.current_product
                    ? 'عم ندوّر هلق على: ' + b.current_product : String(b.phase_text || ''),
                pause: {
                    action: phase === 'paused' ? 'resume' : 'pause',
                    label: phase === 'paused' ? 'استئناف' : 'إيقاف مؤقت',
                    icon: phase === 'paused' ? 'play' : 'pause',
                    // pausing belongs to the worker: meaningless while the sheet is read or a stop is pending
                    disabled: starting || phase === 'stopping'
                },
                stop: {
                    disabled: phase === 'stopping' || C.num(b.stop_requested) === 1,
                    label: phase === 'stopping' || C.num(b.stop_requested) === 1 ? 'طلب الإيقاف مسجّل' : 'إيقاف'
                }
            };
            var metaParts = [];
            if (starting) {
                metaParts.push('عم نقرأ الشيت');
            } else {
                if (current && C.num(current.started_at) !== null) metaParts.push('بدأ ' + C.clockText(current.started_at));
                if (current && current.rows_label) metaParts.push('الصفوف ' + current.rows_label);
            }
            view.meta = metaParts.join(' · ');
        } else if (run && run.total > 0) {
            var ready = C.num(b.ready_for_review) || 0;
            view.finished = {
                title: previous ? 'التشغيل اللي قبله' + (unfinished ? ' (وقف قبل ما يخلص: ' + (C.num(run.processed) || 0) +
                        ' من ' + run.total + ')' : '')
                    : (phase === 'error' ? 'آخر تشغيل وقف بعطل'
                        : (unfinished ? 'آخر تشغيل وقف قبل ما يخلص: ' + (C.num(run.processed) || 0) + ' من ' + run.total
                            : 'خلص آخر تشغيل')),
                tiles: C.resultTiles(run.counts),
                explain: String(run.explain || ''),
                reviewCount: ready,
                reviewVisible: ready > 0,
                reviewLabel: ready > 0 ? 'راجع النتائج (' + ready + ')' : ''
            };
            view.meta = previous ? '' : C.runMeta(run, (nowSec || Date.now() / 1000) * 1000);
            if (view.startFailed) view.message = 'التشغيل الجديد وقف قبل ما يبحث عن ولا منتج. السبب بالشريط فوق.';
        } else if (phase === 'error') {
            view.message = String(b.phase_text || '');
        }

        // while a new run starts, the rows of the previous run are not «آخر النتائج» of this one
        var recent = active && !(run && run.current) ? [] : (Array.isArray(snapshot.recent) ? snapshot.recent : []);
        view.recent = recent.map(function (r) {
            // «عم ندوّر · هلق» only while a worker runs; without one (an inactive phase) that row is stuck
            var stuckRow = r.kind === 'searching' && !active;
            return {
                row: r.row, name: String(r.name || ''),
                label: stuckRow ? 'عالق' : String(r.label || ''),
                chip: stuckRow ? 'warning' : String(r.chip || 'none'),
                why: stuckRow ? 'ما في عامل شغّال يكمّل البحث عنه' : String(r.why || ''), href: String(r.href || '/catalog'),
                ago: r.kind === 'searching' ? (stuckRow ? '' : 'هلق') : C.agoText(r.at, nowSec)
            };
        });
        return view;
    }

    function planQuery(form, fresh) {
        var parts = ['scope=' + encodeURIComponent(form.scope)];
        if (form.scope === 'brand') parts.push('brand=' + encodeURIComponent(form.brand || ''));
        if (form.scope === 'rows') parts.push('rows=' + encodeURIComponent(C.normalizeRows(form.rows)));
        if (form.force) parts.push('force=1');
        if (fresh) parts.push('refresh=1');
        return parts.join('&');
    }

    function has(n) {
        return (C.num(n) || 0) > 0;
    }

    /* «قبل ما تبدأ» from a /api/run/plan response ({ok, status, data}). */
    function describePlan(res) {
        if (!res) return { state: 'loading' };
        var plan = res.data || {};
        if (!res.ok || plan.status !== 'success') {
            return { state: 'error', headline: 'ما في تقدير هلق',
                     detail: plan.message || 'تعذّر حساب التقدير. فيك تبدأ التشغيل عادي.' };
        }
        if (plan.error) {
            return { state: 'invalid', headline: 'النطاق مش واضح', detail: plan.error, invalid: plan.error,
                     brands: Array.isArray(plan.brands) ? plan.brands : null };
        }
        var total = C.num(plan.total) || 0;
        var final = C.num(plan.skipped_final) || 0;
        var parts = [];
        if (!plan.force) {
            parts.push(final === 1 ? 'منتج واحد عنده صورة نهائية وبنتخطاه'
                : (final === 0 ? 'ولا منتج عنده صورة نهائية' : final + ' عندها صورة نهائية وبنتخطاها'));
        }
        if (has(plan.kept_waiting)) {
            parts.push(C.countText(plan.kept_waiting) + ' بانتظار مراجعتك أصلاً وما منعيد البحث عنها');
        }
        if (has(plan.skipped_review_link)) {
            parts.push(C.countText(plan.skipped_review_link) + ' عليها صورة قديمة بالشيت بانتظار مراجعة');
        }
        if (has(plan.leftovers)) {
            parts.push('ومعهم ' + C.countText(plan.leftovers) + ' باقية بالطابور من تشغيل سابق');
        }
        var sentence = parts.length ? parts.join('، ') + '.' : '';
        var est = plan.estimate || {};
        if (total > 0) {
            sentence += (sentence ? ' ' : '') + 'التكلفة التقريبية ' + C.usdText(est.cost_usd) +
                '، والوقت تقريباً ' + C.durationText(est.seconds) + '.';
            if (est.time_basis === 'default' || est.cost_basis === 'default') {
                sentence += ' (تقدير أولي: ما في تشغيلات سابقة كفاية نحسب منها.)';
            }
        } else if (!plan.force && (final > 0 || has(plan.kept_waiting))) {
            sentence += ' إذا بدك تعيد البحث عنها، فعّل «إعادة البحث» من الخيارات المتقدمة.';
        }
        return {
            state: 'ready',
            headline: total > 0 ? 'رح نبحث عن ' + C.countText(total) : 'ما في منتجات جديدة ندوّر عليها بهالنطاق',
            detail: sentence,
            total: total,
            brands: Array.isArray(plan.brands) ? plan.brands : [],
            secondsPerProduct: C.num(est.seconds_per_product)
        };
    }

    /* The POST /api/run-all body (keys main.load_run_config reads). */
    function runBody(form) {
        return {
            row_filter: form.scope === 'rows' ? C.normalizeRows(form.rows) : '',
            brand_filter: form.scope === 'brand' ? String(form.brand || '').trim() : '',
            forceOverwrite: !!form.force,
            skipCache: !!form.skipCache
        };
    }

    // «جودة بيانات الشيت» (GET /api/run/sheet-quality): view {state, lead, groups: [{key, label, count, rows, brands}]}.
    // state: loading | ready | clean | not_loaded | stale | error. Only groups with rows are listed.
    function describeQuality(res) {
        var data = res && res.data;
        if (!res || !data) return { state: 'error', lead: 'ما قدرنا نقرأ جودة بيانات الشيت هلق.', groups: [] };
        if (data.status === 'not_loaded') {
            return { state: 'not_loaded', lead: 'ما قرينا صفوف الشيت لسا بهالجلسة. اضغط «اقرأ الشيت من جديد».', groups: [] };
        }
        if (data.status === 'stale') {
            return { state: 'stale', lead: 'صفوف الشيت المحفوظة انقرت قبل فحص الجودة. اضغط «اقرأ الشيت من جديد» ليظهر الفحص.', groups: [] };
        }
        if (!res.ok || data.status !== 'success' || !Array.isArray(data.groups)) {
            return { state: 'error', lead: arabic(data.message) ? data.message : 'ما قدرنا نقرأ جودة بيانات الشيت هلق.', groups: [] };
        }
        var groups = data.groups.filter(function (g) { return g && C.num(g.count) > 0; }).map(function (g) {
            return {
                key: String(g.key || ''), label: String(g.label || ''), count: C.num(g.count),
                rows: (Array.isArray(g.rows) ? g.rows : []).map(function (r) {
                    var row = C.num(r.row) || 0;
                    return { row: row, name: String(r.name || ''), text: String(r.text || ''), href: '/catalog?row=' + row };
                }),
                brands: (Array.isArray(g.brands) ? g.brands : []).map(function (b) {
                    return { brand: String(b.brand || ''), count: C.num(b.count) || 0 };
                })
            };
        });
        var total = C.num(data.total) || 0;
        if (!groups.length) {
            return { state: 'clean', groups: [],
                     lead: 'كل صفوف الشيت (' + C.countText(total, 'منتج', 'منتجين', 'منتجات') + ') فيها حجم وباركود وماركة معروفة، وما لقينا غلطة إملائية.' };
        }
        return { state: 'ready', groups: groups,
                 lead: 'هالصفوف بتمنع اختيار واثق أو بتخلي البحث يغلط. صلّحها بالشيت (أو بـ Brands Mapping) وأعد البحث عنها.' };
    }

    // «ماركات ناقصة من Brands Mapping» (GET /api/run/brand-suggestions): the brands the queue names that the sheet has no
    // row for. Writes happen only through the two buttons of each brand («أضف») and «أضف الكل بدون مواقع».
    var BRAND_SYNONYM_MAX = 10;
    var BRANDS_LEAD = 'هالماركات بالطابور وما إلها صف بـ Brands Mapping، فالبحث بيعتبرها «ماركة غير معروفة» وما بينشر صورها لحاله. ' +
        'راجع المرادفات (الاسم العربي وكتابات المتاجر اللي لقاها البحث) وأضفها: التشغيل الجاي بيعرفها.';
    var BRANDS_CLEAN = 'كل ماركات الطابور موجودة بـ Brands Mapping.';
    var BRANDS_ERROR = 'ما قدرنا نقرأ الماركات الناقصة هلق.';
    var BRAND_ADDED_TEXT = 'انضافت الماركة. التشغيل الجاي بيعرفها.';
    var SITE_COST_NOTE = 'بتكلّف بحث واحد';
    var BRAND_REQUEST_ERROR = 'ما قدرنا نوصل للخادم. ما انكتب شي.';

    function brandsTitle(count) {
        return 'ماركات ناقصة من Brands Mapping (' + count + ')';
    }

    function brandRowsText(n) {
        return n === 1 ? 'صف واحد' : (n === 2 ? 'صفين' : n + (n >= 3 && n <= 10 ? ' صفوف' : ' صف'));
    }

    function addedText(n) {
        if (n <= 1) return BRAND_ADDED_TEXT;
        if (n === 2) return 'انضافت ماركتين. التشغيل الجاي بيعرفهم.';
        return 'انضافت ' + n + (n <= 10 ? ' ماركات' : ' ماركة') + '. التشغيل الجاي بيعرفها.';
    }

    function addAllConfirmText(count) {
        return 'أضف الكل بدون مواقع:\n' +
            '• بينكتب صف لكل ماركة (' + count + ') بورقة Brands Mapping بالشيت: الاسم ومرادفاته الظاهرة بالحقول، بدون موقع رسمي.\n' +
            '• اللي صارت موجودة أصلاً بتنتخطى، وما بيتغير أي صف تاني بالشيت.\n\n' +
            'بدك تضيفها؟';
    }

    // "a, b، c\n d" -> ['a', 'b', 'c', 'd'] without empty or repeated items (case-insensitive)
    function splitList(text) {
        var seen = {};
        return String(text || '').split(/[,،;\n\r]+/).map(function (x) { return x.replace(/\s+/g, ' ').trim(); })
            .filter(function (x) {
                var key = x.toLowerCase();
                if (!x || seen[key]) return false;
                seen[key] = true;
                return true;
            });
    }

    // What the owner pasted into the site field as a bare host: «https://www.almarai.com/en?x=1» -> «www.almarai.com».
    function bareHost(text) {
        var t = String(text || '').trim().toLowerCase().replace(/^[a-z][a-z0-9+.-]*:\/\//, '');
        return t.split(/[\/?#]/)[0].trim();
    }

    // The synonyms field of a brand: its Arabic name first, then the store spellings, without repeats or the brand itself.
    function brandSynonymsText(b) {
        var own = String(b.brand || '').toLowerCase();
        return splitList([b.brand_ar].concat(Array.isArray(b.synonyms) ? b.synonyms : []).filter(Boolean).join(', '))
            .filter(function (x) { return x.toLowerCase() !== own; }).slice(0, BRAND_SYNONYM_MAX).join('، ');
    }

    // POST /api/run/brand-add body from what the fields hold now; the server checks all of it again.
    function brandBody(brand, synonymsText, siteText) {
        var host = bareHost(siteText);
        return { brand: String(brand || ''), synonyms: splitList(synonymsText), official_domains: host ? [host] : [] };
    }

    // POST /api/run/brand-add-all body: [{brand, synonymsText}] -> every brand with its synonyms only, never a site.
    function addAllBody(rows) {
        return { items: rows.map(function (r) { return { brand: String(r.brand || ''), synonyms: splitList(r.synonymsText) }; }) };
    }

    // view {state: ready | clean | error, count, title, lead, brands: [{brand, rows, rowsText, synonymsText}]}
    function describeBrands(res) {
        var data = res && res.data;
        if (!res || !data || !res.ok || data.status !== 'success' || !Array.isArray(data.brands)) {
            return { state: 'error', count: 0, title: brandsTitle(0), brands: [],
                     lead: data && arabic(data.message) ? data.message : BRANDS_ERROR };
        }
        var brands = data.brands.filter(function (b) { return b && typeof b.brand === 'string' && b.brand.trim(); }).map(function (b) {
            var rows = C.num(b.rows) || 0;
            return { brand: b.brand.trim(), rows: rows, rowsText: brandRowsText(rows), synonymsText: brandSynonymsText(b) };
        });
        return { state: brands.length ? 'ready' : 'clean', count: brands.length, title: brandsTitle(brands.length),
                 lead: brands.length ? BRANDS_LEAD : BRANDS_CLEAN, brands: brands };
    }

    // The message of a refused or failed brand request: the server's Arabic text, else the generic one.
    function brandFailure(res) {
        var data = res && res.data;
        return data && arabic(data.message) ? data.message : (res && res.status ? 'ما قدرنا نكمّل هلق. ما انكتب شي. جرّب بعد شوي.' : BRAND_REQUEST_ERROR);
    }

    // «باركودات لقيناها من صفحات المتاجر» (GET /api/run/barcode-suggestions): approved rows whose store page stated a
    // barcode while the sheet's barcode cell is empty. Writes happen only through «اكتب الباركودات المختارة بالشيت».
    var BARCODES_LEAD = 'صفحة المتجر للصورة المعتمدة ذكرت باركود صالح، وخلية الباركود بالشيت فاضية. علّم الصفوف اللي بدك ياها ' +
        'ومنكتب الباركود بعمود الباركود بس.';
    var BARCODES_CLEAN = 'ما في باركودات جديدة من صفحات المتاجر لصفوف بلا باركود.';
    var BARCODES_ERROR = 'ما قدرنا نقرأ الباركودات هلق.';
    var BARCODE_CONFIRM_TEXT = 'رح ننكتب الباركود بعمود الباركود للصفوف المختارة بس، وما منغيّر أي خلية فيها باركود';
    var BARCODE_DUPLICATE_NOTE = 'نفس الباركود لأكتر من صف: ما منكتبه. راجع الصفحتين.';
    var BARCODE_WRITE_ERROR = 'ما قدرنا نوصل للخادم. ما انكتب شي.';

    function barcodesTitle(count) {
        return 'باركودات لقيناها من صفحات المتاجر (' + count + ')';
    }

    function filledNote(value) {
        return 'خلية الباركود فيها «' + value + '»: ما منكتب فوقها.';
    }

    // view {state: ready | clean | error, count, title, lead, rows: [...], apart: [...]}; a row {row, sku_key, name, brand,
    // gtin, domain, checked, note}. rows can be written (all ticked); apart are duplicates or a filled cell (never sent).
    function describeBarcodes(res) {
        var data = res && res.data;
        if (!res || !data || !res.ok || data.status !== 'success' || !Array.isArray(data.rows)) {
            return { state: 'error', count: 0, title: barcodesTitle(0), rows: [], apart: [],
                     lead: data && arabic(data.message) ? data.message : BARCODES_ERROR };
        }
        var pick = function (list, checked, note) {
            return (Array.isArray(list) ? list : []).filter(function (r) {
                return r && C.num(r.row) && typeof r.sku_key === 'string' && r.sku_key && /^\d{8,14}$/.test(String(r.gtin || ''));
            }).map(function (r) {
                return { row: C.num(r.row), sku_key: r.sku_key, name: String(r.name || ''), brand: String(r.brand || ''),
                         gtin: String(r.gtin), domain: String(r.domain || ''), checked: checked,
                         note: typeof note === 'function' ? note(r) : note };
            });
        };
        var rows = pick(data.rows, true, '');
        var apart = pick(data.duplicates, false, BARCODE_DUPLICATE_NOTE)
            .concat(pick(data.filled, false, function (r) { return filledNote(String(r.sheet_barcode || '')); }));
        var count = rows.length + apart.length;
        return { state: count ? 'ready' : 'clean', count: count, title: barcodesTitle(count),
                 lead: count ? BARCODES_LEAD : BARCODES_CLEAN, rows: rows, apart: apart };
    }

    // POST /api/run/barcode-write body: the ticked rows that can be written; never a row listed apart.
    function barcodeWriteBody(rows) {
        return { items: rows.filter(function (r) { return r.checked && !r.note; }).map(function (r) {
            return { row: r.row, sku_key: r.sku_key, gtin: r.gtin };
        }) };
    }

    // The result of a write: the server's Levantine text, else the counts.
    function barcodeResult(res) {
        var data = res && res.data;
        if (res && res.ok && data && data.status === 'success') {
            var done = (C.num(data.written) || 0) + (C.num(data.queued) || 0);
            return { ok: done > 0, text: arabic(data.message) ? data.message : (done ? 'انكتبت الباركودات.' : 'ما انكتب شي.') };
        }
        return { ok: false, text: data && arabic(data.message) ? data.message
            : (res && res.status ? 'ما قدرنا نكتب بالشيت هلق. ما انكتب شي. جرّب بعد شوي.' : BARCODE_WRITE_ERROR) };
    }

    // «تصدير تقرير للتحليل»: الرابط حسب النطاق، واسم الملف من رد الخادم (Content-Disposition)
    function exportQuery(scope) {
        return '/api/run/export?scope=' + (scope === 'review' ? 'review' : 'latest');
    }

    function exportFileName(disposition) {
        var m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(String(disposition || ''));
        var name = m ? m[1] : '';
        return /^laqta_run_[0-9_-]+\.json$/.test(name) ? name : 'laqta_run.json';
    }

    /*
     * deps: fetchJson(url, opts) -> Promise<{ok,status,data}>, renderLive(view), renderPlan(view), renderStart(view),
     * confirm(text) -> bool, toast(text, variant), now() -> epoch seconds, schedule(fn, ms) -> handle.
     */
    function createController(deps) {
        var state = {
            form: { scope: 'all', brand: '', rows: '', force: false, skipCache: false },
            lastPhase: null,
            snapshot: null,
            plan: null,
            planSeq: 0,
            busy: false,
            timer: null
        };

        function now() {
            return deps.now ? deps.now() : Date.now() / 1000;
        }

        function handleLive(snapshot) {
            state.snapshot = snapshot;
            var view = describeLive(snapshot, now(), state.plan && state.plan.secondsPerProduct);
            deps.renderLive(view);
            deps.renderStart({ busy: state.busy, blocked: view.startBlocked });
            if (view.phase !== null && view.phase !== undefined) {
                if (C.runJustFinished(state.lastPhase, view.phase)) onRunFinished(snapshot, view);
                state.lastPhase = view.phase;
            }
            return view;
        }

        // End of a run seen on this page: say so and read the sheet again past every cache for the next plan.
        function onRunFinished(snapshot, view) {
            refreshPlan(true);
            var b = snapshot.batch || {};
            var ready = C.num(b.ready_for_review) || 0;
            var waiting = ready > 0 ? C.countText(ready) + ' بانتظار مراجعتك.' : '';
            if (view.phase === 'error') {
                deps.toast('وقف التشغيل بعطل. السبب بالشريط فوق.', 'danger');
            } else if (view.state === 'stopped') {
                // a stop (or a crash) is not «خلص»: the card says «وقف قبل ما يخلص» and so does the toast
                deps.toast('وقف التشغيل قبل ما يخلص' + (waiting ? ': ' + waiting : '.'), 'warning');
            } else {
                deps.toast(waiting ? 'خلص التشغيل: ' + waiting : 'خلص التشغيل.', 'success');
            }
        }

        function poll() {
            return deps.fetchJson('/api/run/live').then(function (res) {
                var snap = res.data && typeof res.data === 'object' ? res.data : null;
                if (!res.ok && !(snap && snap.status)) snap = { status: 'error' };
                return handleLive(snap);
            });
        }

        function loop() {
            poll().then(function (view) {
                var ms = view && C.isActive(view.phase) ? POLL_ACTIVE_MS : POLL_IDLE_MS;
                state.timer = deps.schedule(loop, ms);
            });
        }

        function refreshPlan(fresh) {
            var seq = ++state.planSeq;
            deps.renderPlan({ state: 'loading' });
            return deps.fetchJson('/api/run/plan?' + planQuery(state.form, fresh)).then(function (res) {
                if (seq !== state.planSeq) return null;       // an answer for an older form
                var view = describePlan(res);
                state.plan = view;
                deps.renderPlan(view);
                return view;
            });
        }

        function setForm(patch) {
            for (var k in patch) {
                if (Object.prototype.hasOwnProperty.call(patch, k)) state.form[k] = patch[k];
            }
            return refreshPlan(false);
        }

        /*
         * latest: the form as it is on screen when «ابدأ التشغيل» is pressed (the rows field updates the form only
         * after a short pause in typing). When it differs, the plan is read for it first and checked as usual, so
         * the run always starts on the scope the owner sees, never on the one typed half a second earlier.
         */
        function start(latest) {
            if (state.busy) return Promise.resolve(false);
            if (latest && typeof latest === 'object') {
                for (var k in latest) {
                    if (Object.prototype.hasOwnProperty.call(latest, k) && latest[k] !== state.form[k]) {
                        return setForm(latest).then(function () { return start(); });
                    }
                }
            }
            var body = runBody(state.form);
            if (state.form.scope === 'rows' && !body.row_filter) {
                deps.renderStart({ error: 'اكتب الصفوف اللي بدك تشغّلها، مثل 62-101.' });
                return Promise.resolve(false);
            }
            if (state.form.scope === 'brand' && !body.brand_filter) {
                deps.renderStart({ error: 'اختار الماركة اللي بدك تشغّلها.' });
                return Promise.resolve(false);
            }
            if (state.plan && state.plan.state === 'invalid') {
                deps.renderStart({ error: state.plan.invalid });
                return Promise.resolve(false);
            }
            if (body.forceOverwrite && !deps.confirm(FORCE_CONFIRM_TEXT)) return Promise.resolve(false);
            state.busy = true;
            deps.renderStart({ busy: true });
            return deps.fetchJson('/api/run-all', { method: 'POST', body: body }).then(function (res) {
                state.busy = false;
                var data = res.data || {};
                if (res.ok && data.status === 'success') {
                    deps.renderStart({ busy: false, blocked: true });
                    deps.toast('بلّش التشغيل. النتائج بتوصل على قائمة المراجعة وهو شغّال.', 'success');
                    state.lastPhase = 'starting';
                    poll();
                    return true;
                }
                var message = data.error || 'ما قدرنا نبدأ التشغيل. جرّب كمان مرة.';
                deps.renderStart({ busy: false, error: message });
                deps.toast(message, 'danger');
                return false;
            });
        }

        function control(url, okText, failText) {
            return deps.fetchJson(url, { method: 'POST', body: {} }).then(function (res) {
                var data = res.data || {};
                // the server's own message only when it is Arabic (run_control's stop/reset messages); pause and
                // resume answer in English ("Automation paused."), so the page says it in plain Arabic instead
                if (res.ok && data.status !== 'failed' && !data.error) {
                    deps.toast(arabic(data.message) ? data.message : okText, 'success');
                } else {
                    deps.toast(arabic(data.error) ? data.error : failText, 'danger');
                }
                return poll();
            });
        }

        function pause() {
            return control('/api/batch/pause', 'انوقف التشغيل مؤقتاً.', 'ما قدرنا نوقف التشغيل مؤقتاً.');
        }

        function resume() {
            return control('/api/batch/resume', 'رجع التشغيل يشتغل.', 'ما قدرنا نكمّل التشغيل.');
        }

        function stop() {
            if (!deps.confirm(STOP_CONFIRM_TEXT)) return Promise.resolve(false);
            deps.toast('عم نوقف التشغيل: العامل بيكمّل المنتجات الجارية (حتى دقيقة ونص).', 'info');
            return control('/api/stop-batch', 'انوقف التشغيل، وما انحذف ولا صف.', 'ما قدرنا نوقف التشغيل.');
        }

        function reset() {
            if (!deps.confirm(RESET_CONFIRM_TEXT)) return Promise.resolve(false);
            return control('/api/batch/reset', 'انصلح التشغيل العالق، وما انحذف ولا منتج.', 'ما قدرنا نصلّح التشغيل.');
        }

        return {
            state: state,
            handleLive: handleLive,
            poll: poll,
            loop: loop,
            refreshPlan: refreshPlan,
            setForm: setForm,
            start: start,
            pause: pause,
            resume: resume,
            stop: stop,
            reset: reset
        };
    }

    // ---------------------------------------------------------------------------------------------
    // DOM
    // ---------------------------------------------------------------------------------------------

    function mount(doc) {
        var page = doc.querySelector('[data-run-page]');
        if (!page) return null;
        var $ = function (name) { return page.querySelector('[data-run="' + name + '"]'); };
        var chipClasses = ['proposed', 'warning', 'none', 'not-found', 'approved', 'error'];

        function setChip(node, textNode, chip) {
            if (!node) return;
            chipClasses.forEach(function (c) { node.classList.remove('lq-chip--' + c); });
            node.classList.add('lq-chip--' + chip.status);
            C.setText(textNode, chip.label);
        }

        function chipEl(label, status) {
            var chip = C.el(doc, 'span', 'lq-chip lq-chip--sm lq-chip--' + status);
            chip.appendChild(C.el(doc, 'span', 'lq-chip__dot'));
            chip.appendChild(doc.createTextNode(label));
            return chip;
        }

        function renderTiles(container, tiles) {
            C.clear(container);
            tiles.forEach(function (t) {
                var tile = C.el(doc, 'div', 'lq-stat lq-stat--' + t.tone);
                tile.appendChild(C.el(doc, 'span', 'lq-stat__value', t.value));
                tile.appendChild(C.el(doc, 'span', 'lq-stat__label', t.label));
                container.appendChild(tile);
            });
        }

        function renderLegend(container, tiles) {
            C.clear(container);
            tiles.forEach(function (t) {
                var item = C.el(doc, 'li', 'lq-legend__item');
                item.appendChild(C.el(doc, 'span', 'lq-legend__swatch lq-tone--' + t.tone));
                item.appendChild(doc.createTextNode(t.label));
                item.appendChild(C.el(doc, 'span', 'lq-legend__value', t.value));
                container.appendChild(item);
            });
        }

        function renderLive(view) {
            var card = $('current');
            card.setAttribute('data-state', view.state);
            setChip($('chip'), $('chip-text'), view.chip);
            C.setText($('meta'), view.meta || '');

            var alert = $('alert');
            C.setHidden(alert, !view.banner.visible);
            if (view.banner.visible) {
                alert.classList.toggle('lq-alert--danger', view.banner.variant === 'danger');
                alert.classList.toggle('lq-alert--warning', view.banner.variant !== 'danger');
                C.setText($('alert-title'), view.banner.title);
                C.setText($('alert-text'), view.banner.text);
            }

            C.setHidden($('loading'), true);
            C.setHidden($('unavailable'), view.state !== 'unavailable');
            if (view.state === 'unavailable') C.setText($('unavailable-text'), view.message);
            var errorBlock = view.state === 'error' && (!view.finished || view.startFailed);
            C.setHidden($('empty'), !(view.state === 'idle' || errorBlock));
            if (errorBlock) {
                C.setText($('empty-title'), 'آخر تشغيل وقف قبل ما يبلّش');
                C.setText($('empty-text'), view.message || 'السبب بالشريط فوق. صلّحه وابدأ تشغيل جديد.');
            } else if (view.state === 'idle') {
                C.setText($('empty-title'), 'ما في تشغيل هلق');
                C.setText($('empty-text'), 'اختار النطاق من «تشغيل جديد» واضغط «ابدأ التشغيل». النتائج بتوصل على قائمة المراجعة وهو شغّال.');
            }

            var p = view.progress;
            C.setHidden($('progress'), !p);
            if (p) {
                C.setText($('done'), p.starting ? '—' : p.done);
                C.setText($('of'), p.starting ? '' : 'من ' + p.total);
                C.setText($('remaining'), p.remaining);
                C.setText($('phase-text'), p.phaseText);
                C.setHidden($('phase-text'), !p.phaseText);
                var bar = $('bar');
                bar.setAttribute('aria-valuenow', String(p.percent || 0));
                bar.setAttribute('aria-valuetext', p.starting ? 'عم نقرأ الشيت' : p.countsText);
                C.clear(bar);
                p.segments.forEach(function (s) {
                    var seg = C.el(doc, 'span', 'lq-progress__seg lq-tone--' + s.tone);
                    seg.style.width = s.pct + '%';
                    bar.appendChild(seg);
                });
                renderLegend($('legend'), p.legend);
                C.setHidden($('legend'), !p.legend.length);
                var pauseBtn = $('pause');
                pauseBtn.disabled = p.pause.disabled;
                pauseBtn.setAttribute('data-action', p.pause.action);
                C.setText($('pause-text'), p.pause.label);
                C.setHidden($('pause-icon-pause'), p.pause.icon !== 'pause');
                C.setHidden($('pause-icon-play'), p.pause.icon !== 'play');
                $('stop').disabled = p.stop.disabled;
                C.setText($('stop-text'), p.stop.label);
            }

            var f = view.finished;
            C.setHidden($('finished'), !f);
            if (f) {
                C.setText($('finished-title'), f.title);
                renderTiles($('tiles'), f.tiles);
                C.setText($('explain'), f.explain);
                C.setHidden($('explain'), !f.explain);
                C.setHidden($('review'), !f.reviewVisible);
                C.setText($('review-text'), f.reviewLabel);
            }

            C.setHidden($('stuck'), !view.stuck.visible);
            C.setText($('stuck-reason'), view.stuck.reason);

            var list = $('recent');
            C.clear(list);
            view.recent.forEach(function (r) {
                var li = C.el(doc, 'li', 'lq-run-recent__item');
                var link = C.el(doc, 'a', 'lq-run-recent__link');
                link.setAttribute('href', r.href);
                link.setAttribute('title', r.why ? r.why + ' · افتح بالمراجعة' : 'افتح بالمراجعة');
                link.appendChild(C.el(doc, 'span', 'lq-run-recent__row', 'صف ' + r.row));
                var name = C.el(doc, 'bdi', 'lq-run-recent__name', r.name);
                name.setAttribute('dir', 'auto');
                link.appendChild(name);
                link.appendChild(chipEl(r.label, r.chip));
                link.appendChild(C.el(doc, 'span', 'lq-run-recent__ago', r.ago));
                li.appendChild(link);
                list.appendChild(li);
            });
            C.setHidden($('recent-empty'), view.recent.length > 0 || view.state === 'unavailable');
        }

        function fillBrands(brands) {
            var select = $('brand');
            var current = select.value;
            C.clear(select);
            var first = C.el(doc, 'option', null, brands.length ? 'اختار ماركة…' : 'ما في ماركات بالشيت');
            first.value = '';
            select.appendChild(first);
            brands.forEach(function (b) {
                var opt = C.el(doc, 'option', null, b.label || b.brand);
                opt.value = b.brand;
                select.appendChild(opt);
            });
            select.value = current;
        }

        var brandsFilled = null;    // the brand list shown in the select ('' + labels), null before the first answer

        // The brand select follows the plan: the sheet's brands (or «ما في ماركات بالشيت»), and when the sheet could
        // not be read before any list arrived, it says so instead of «عم نقرأ الماركات…» forever.
        function renderBrands(view) {
            if ((view.state === 'ready' || view.state === 'invalid') && Array.isArray(view.brands)) {
                var brands = view.brands;
                var signature = brands.map(function (b) { return (b.label || b.brand) + '|' + b.brand; }).join('\n');
                if (signature !== brandsFilled) {
                    fillBrands(brands);
                    brandsFilled = signature;
                }
            } else if (view.state === 'error' && brandsFilled === null) {
                var select = $('brand');
                C.clear(select);
                var note = C.el(doc, 'option', null, 'ما قدرنا نقرأ الماركات من الشيت هلق');
                note.value = '';
                select.appendChild(note);
            }
        }

        function renderPlan(view) {
            var box = $('plan');
            box.setAttribute('data-state', view.state);
            box.setAttribute('aria-busy', view.state === 'loading' ? 'true' : 'false');
            C.setHidden($('plan-skeleton'), view.state !== 'loading');
            C.setHidden($('plan-headline'), view.state === 'loading');
            C.setHidden($('plan-detail'), view.state === 'loading');
            if (view.state === 'loading') return;
            C.setText($('plan-headline'), view.headline || '');
            C.setText($('plan-detail'), view.detail || '');
            var rowsError = $('rows-error');
            var invalidRows = view.state === 'invalid' && controller.state.form.scope === 'rows';
            C.setHidden(rowsError, !invalidRows);
            C.setText(rowsError, invalidRows ? view.invalid : '');
            $('rows').setAttribute('aria-invalid', invalidRows ? 'true' : 'false');
            renderBrands(view);
        }

        function renderStart(view) {
            var btn = $('start');
            var errorBox = $('start-error');
            if (view.error !== undefined) {
                C.setHidden(errorBox, !view.error);
                C.setText(errorBox, view.error || '');
            } else if (view.busy) {
                C.setHidden(errorBox, true);
            }
            if (view.busy !== undefined) {
                btn.classList.toggle('is-loading', !!view.busy);
                btn.setAttribute('aria-busy', view.busy ? 'true' : 'false');
                C.setText($('start-text'), view.busy ? 'عم نبلّش…' : 'ابدأ التشغيل');
            }
            if (view.blocked !== undefined) {
                btn.disabled = !!view.blocked || !!view.busy;
                C.setHidden($('start-note'), !view.blocked);
            }
        }

        function renderQuality(view) {
            var card = $('quality');
            card.setAttribute('data-state', view.state);
            C.setText($('quality-lead'), view.lead || '');
            var box = $('quality-groups');
            C.clear(box);
            view.groups.forEach(function (g) {
                var group = C.el(doc, 'details', 'lq-run-quality__group');
                group.setAttribute('data-issue', g.key);
                var summary = C.el(doc, 'summary', 'lq-run-quality__summary');
                summary.appendChild(C.el(doc, 'span', 'lq-run-quality__label', g.label));
                summary.appendChild(C.el(doc, 'span', 'lq-run-quality__count lq-num', String(g.count)));
                group.appendChild(summary);
                if (g.brands.length) {
                    group.appendChild(C.el(doc, 'p', 'lq-run-quality__brands', 'الماركات: ' + g.brands.map(function (b) {
                        return b.brand + ' (' + b.count + ')';
                    }).join('، ')));
                }
                var list = C.el(doc, 'ol', 'lq-run-quality__rows');
                g.rows.forEach(function (r) {
                    var li = C.el(doc, 'li', 'lq-run-quality__row');
                    var link = C.el(doc, 'a', 'lq-link', 'صف ' + r.row);
                    link.setAttribute('href', r.href);
                    li.appendChild(link);
                    var name = C.el(doc, 'bdi', 'lq-run-quality__name', r.name);
                    name.setAttribute('dir', 'auto');
                    li.appendChild(name);
                    if (r.text) li.appendChild(C.el(doc, 'span', 'lq-run-quality__text', r.text));
                    list.appendChild(li);
                });
                if (g.count > g.rows.length) {
                    list.appendChild(C.el(doc, 'li', 'lq-run-quality__more', 'و' + (g.count - g.rows.length) + ' صف غيرهم'));
                }
                group.appendChild(list);
                box.appendChild(group);
            });
        }

        function loadQuality(refresh) {
            var btn = $('quality-refresh');
            btn.disabled = true;
            if (refresh) renderQuality({ state: 'loading', lead: 'عم نقرأ صفوف الشيت…', groups: [] });
            return C.fetchJson('/api/run/sheet-quality' + (refresh ? '?refresh=1' : '')).then(function (res) {
                btn.disabled = false;
                renderQuality(describeQuality(res));
            });
        }

        // «ماركات ناقصة»: one row per brand with its own fields; the rows live in brandRows so «أضف الكل» reads what they hold.
        var brandRows = [];

        function brandNote(text, kind) {
            var done = kind === 'done';
            C.setText($('brands-error'), done ? '' : text);
            C.setHidden($('brands-error'), done || !text);
            C.setText($('brands-done'), done ? text : '');
            C.setHidden($('brands-done'), !done || !text);
        }

        function renderMissingHead(view) {
            $('brands').setAttribute('data-state', view.state);
            C.setText($('brands-title'), view.title);
            C.setText($('brands-lead'), view.lead || '');
            C.setHidden($('brands-foot'), !brandRows.length);
        }

        function refreshMissingHead() {
            var left = brandRows.length;
            renderMissingHead({ state: left ? 'ready' : 'clean', title: brandsTitle(left), lead: left ? BRANDS_LEAD : BRANDS_CLEAN });
        }

        function forgetBrandRow(row) {
            var i = brandRows.indexOf(row);
            if (i !== -1) brandRows.splice(i, 1);
            if (row.el.parentNode) row.el.parentNode.removeChild(row.el);
        }

        function setBrandBusy(row, busy) {
            row.busy = busy;
            row.add.disabled = busy;
            row.add.setAttribute('aria-busy', busy ? 'true' : 'false');
            row.suggest.disabled = busy || row.searched;
        }

        function rowError(row, text) {
            C.setText(row.error, text || '');
            C.setHidden(row.error, !text);
        }

        function showCandidates(row, candidates) {
            C.clear(row.candidates);
            candidates.forEach(function (c) {
                var chip = C.el(doc, 'button', 'lq-btn lq-btn--soft lq-btn--sm lq-run-brand__candidate');
                chip.setAttribute('type', 'button');
                chip.appendChild(C.el(doc, 'bdi', 'lq-run-brand__domain', c.domain));
                if (c.title) chip.appendChild(C.el(doc, 'span', 'lq-run-brand__title', c.title));
                chip.addEventListener('click', function () { row.site.value = c.domain; });
                row.candidates.appendChild(chip);
            });
            C.setHidden(row.candidates, !candidates.length);
        }

        function suggestSite(row) {
            if (row.searched || row.searching) return Promise.resolve();   // one search per click: never a second one by accident
            row.searching = true;
            rowError(row, '');
            row.suggest.disabled = true;
            C.setText(row.suggestText, 'عم نبحث…');
            return C.fetchJson('/api/run/brand-official-site', { method: 'POST', body: { brand: row.brand } }).then(function (res) {
                var data = res.data || {};
                row.searching = false;
                if (res.ok && data.status === 'success') {
                    var found = (Array.isArray(data.candidates) ? data.candidates : []).filter(function (c) {
                        return c && typeof c.domain === 'string' && c.domain;
                    }).map(function (c) { return { domain: c.domain, title: String(c.title || '') }; });
                    row.searched = true;
                    C.setText(row.suggestText, 'تم البحث (بحث واحد)');
                    showCandidates(row, found);
                    if (found.length && !String(row.site.value || '').trim()) row.site.value = found[0].domain;
                    rowError(row, found.length ? '' : (arabic(data.message) ? data.message : 'ما لقينا موقع رسمي واضح. اكتبه بإيدك إذا بتعرفه.'));
                    return;
                }
                C.setText(row.suggestText, 'اقترح الموقع الرسمي');
                row.suggest.disabled = row.busy;
                rowError(row, brandFailure(res));
            });
        }

        function addBrand(row) {
            if (row.busy) return Promise.resolve();
            rowError(row, '');
            brandNote('');
            setBrandBusy(row, true);
            return C.fetchJson('/api/run/brand-add', { method: 'POST', body: brandBody(row.brand, row.syn.value, row.site.value) })
                .then(function (res) {
                    var data = res.data || {};
                    if (res.ok && data.status === 'success') {
                        forgetBrandRow(row);
                        refreshMissingHead();
                        brandNote(arabic(data.message) ? data.message : BRAND_ADDED_TEXT, 'done');
                        C.toast(BRAND_ADDED_TEXT, 'success');
                        return;
                    }
                    setBrandBusy(row, false);
                    rowError(row, brandFailure(res));
                });
        }

        function addAllBrands() {
            if (!brandRows.length) return Promise.resolve();
            if (!root.confirm(addAllConfirmText(brandRows.length))) return Promise.resolve();
            brandNote('');
            var btn = $('brands-add-all');
            var rows = brandRows.slice();
            btn.disabled = true;
            btn.setAttribute('aria-busy', 'true');
            rows.forEach(function (r) { setBrandBusy(r, true); });
            var body = addAllBody(rows.map(function (r) { return { brand: r.brand, synonymsText: r.syn.value }; }));
            return C.fetchJson('/api/run/brand-add-all', { method: 'POST', body: body }).then(function (res) {
                var data = res.data || {};
                btn.disabled = false;
                btn.setAttribute('aria-busy', 'false');
                if (res.ok && data.status === 'success') {
                    rows.forEach(forgetBrandRow);          // the ones skipped are mapped now too
                    refreshMissingHead();
                    var n = Array.isArray(data.added) ? data.added.length : rows.length;
                    brandNote(addedText(n), 'done');
                    C.toast(addedText(n), 'success');
                    return;
                }
                rows.forEach(function (r) { setBrandBusy(r, false); });
                brandNote(brandFailure(res));
            });
        }

        function brandRow(b) {
            var el = C.el(doc, 'article', 'lq-run-brand');
            el.setAttribute('data-brand', b.brand);
            var head = C.el(doc, 'div', 'lq-run-brand__head');
            var name = C.el(doc, 'bdi', 'lq-run-brand__name', b.brand);
            name.setAttribute('dir', 'auto');
            head.appendChild(name);
            head.appendChild(C.el(doc, 'span', 'lq-run-brand__rows lq-num', b.rowsText));
            el.appendChild(head);

            var synField = C.el(doc, 'label', 'lq-field');
            synField.appendChild(C.el(doc, 'span', 'lq-field__label', 'المرادفات (فاصلة بين كل واحد وواحد)'));
            var syn = C.el(doc, 'input', 'lq-input lq-run-brand__syn');
            syn.setAttribute('type', 'text');
            syn.setAttribute('dir', 'auto');
            syn.setAttribute('maxlength', '800');
            syn.value = b.synonymsText;
            synField.appendChild(syn);
            el.appendChild(synField);

            var siteField = C.el(doc, 'label', 'lq-field');
            siteField.appendChild(C.el(doc, 'span', 'lq-field__label', 'الموقع الرسمي (اختياري)'));
            var site = C.el(doc, 'input', 'lq-input lq-run-brand__site');
            site.setAttribute('type', 'text');
            site.setAttribute('inputmode', 'url');
            site.setAttribute('dir', 'ltr');
            site.setAttribute('placeholder', 'almarai.com');
            site.setAttribute('maxlength', '253');
            siteField.appendChild(site);
            el.appendChild(siteField);

            var candidates = C.el(doc, 'div', 'lq-run-brand__candidates');
            candidates.setAttribute('hidden', '');
            el.appendChild(candidates);

            var actions = C.el(doc, 'div', 'lq-run-brand__actions');
            var suggest = C.el(doc, 'button', 'lq-btn lq-btn--ghost lq-btn--sm');
            suggest.setAttribute('type', 'button');
            var suggestText = C.el(doc, 'span', '', 'اقترح الموقع الرسمي');
            suggest.appendChild(suggestText);
            actions.appendChild(suggest);
            actions.appendChild(C.el(doc, 'span', 'lq-field__hint lq-run-brand__cost', SITE_COST_NOTE));
            var add = C.el(doc, 'button', 'lq-btn lq-btn--primary lq-btn--sm lq-run-brand__add');
            add.setAttribute('type', 'button');
            add.appendChild(C.el(doc, 'span', '', 'أضف'));
            actions.appendChild(add);
            el.appendChild(actions);

            var error = C.el(doc, 'p', 'lq-field__error');
            error.setAttribute('role', 'alert');
            error.setAttribute('hidden', '');
            el.appendChild(error);

            var row = { brand: b.brand, el: el, syn: syn, site: site, candidates: candidates, suggest: suggest,
                        suggestText: suggestText, add: add, error: error, busy: false, searched: false, searching: false };
            suggest.addEventListener('click', function () { suggestSite(row); });
            add.addEventListener('click', function () { addBrand(row); });
            return row;
        }

        function renderMissing(view) {
            var box = $('brands-list');
            C.clear(box);
            brandRows = [];
            view.brands.forEach(function (b) {
                var row = brandRow(b);
                brandRows.push(row);
                box.appendChild(row.el);
            });
            renderMissingHead(view);
        }

        function loadMissing() {
            var btn = $('brands-refresh');
            btn.disabled = true;
            brandNote('');
            return C.fetchJson('/api/run/brand-suggestions').then(function (res) {
                btn.disabled = false;
                renderMissing(describeBrands(res));
            });
        }

        // «باركودات من صفحات المتاجر»: one row per sheet row with its checkbox; the rows live in barcodeRows so the write
        // button reads what is ticked now. Rows listed apart (a shared barcode, a filled cell) have no live checkbox.
        var barcodeRows = [];

        function barcodeNote(text, kind) {
            var done = kind === 'done';
            C.setText($('barcodes-error'), done ? '' : text);
            C.setHidden($('barcodes-error'), done || !text);
            C.setText($('barcodes-done'), done ? text : '');
            C.setHidden($('barcodes-done'), !done || !text);
        }

        function refreshBarcodeButton() {
            var ticked = barcodeWriteBody(barcodeRows).items.length;
            $('barcodes-write').disabled = !ticked;
            C.setText($('barcodes-count'), ticked ? 'اخترت ' + C.countText(ticked, 'صف', 'صفين', 'صفوف') : 'ما اخترت ولا صف');
        }

        function barcodeRow(r) {
            var el = C.el(doc, 'label', 'lq-run-barcode' + (r.note ? ' lq-run-barcode--apart' : ''));
            el.setAttribute('data-row', String(r.row));
            var box = C.el(doc, 'input', 'lq-run-barcode__check');
            box.setAttribute('type', 'checkbox');
            box.checked = r.checked;
            box.disabled = !!r.note;
            el.appendChild(box);
            var body = C.el(doc, 'span', 'lq-run-barcode__body');
            var name = C.el(doc, 'bdi', 'lq-run-barcode__name', r.name);
            name.setAttribute('dir', 'auto');
            body.appendChild(name);
            var meta = C.el(doc, 'span', 'lq-run-barcode__meta');
            var gtin = C.el(doc, 'bdi', 'lq-run-barcode__gtin lq-num', r.gtin);
            gtin.setAttribute('dir', 'ltr');
            meta.appendChild(gtin);
            meta.appendChild(C.el(doc, 'span', 'lq-run-barcode__store', r.domain ? 'من ' + r.domain : 'من صفحة المتجر'));
            meta.appendChild(C.el(doc, 'span', 'lq-run-barcode__row lq-num', 'صف ' + r.row + (r.brand ? ' · ' + r.brand : '')));
            body.appendChild(meta);
            if (r.note) body.appendChild(C.el(doc, 'span', 'lq-run-barcode__note', r.note));
            el.appendChild(body);
            var row = { row: r.row, sku_key: r.sku_key, gtin: r.gtin, note: r.note, checked: r.checked, el: el, box: box };
            box.addEventListener('change', function () {
                row.checked = !!box.checked;
                refreshBarcodeButton();
            });
            return row;
        }

        function renderBarcodes(view) {
            var box = $('barcodes-list');
            var apart = $('barcodes-apart');
            C.clear(box);
            C.clear(apart);
            barcodeRows = [];
            view.rows.forEach(function (r) {
                var row = barcodeRow(r);
                barcodeRows.push(row);
                box.appendChild(row.el);
            });
            if (view.apart.length) {
                apart.appendChild(C.el(doc, 'p', 'lq-run-barcodes__apart-title', 'ما منكتبها (' + view.apart.length + ')'));
                view.apart.forEach(function (r) { apart.appendChild(barcodeRow(r).el); });
            }
            C.setHidden(apart, !view.apart.length);
            $('barcodes').setAttribute('data-state', view.state);
            C.setText($('barcodes-title'), view.title);
            C.setText($('barcodes-lead'), view.lead || '');
            C.setHidden($('barcodes-foot'), !barcodeRows.length);
            refreshBarcodeButton();
        }

        function loadBarcodes() {
            var btn = $('barcodes-refresh');
            btn.disabled = true;
            return C.fetchJson('/api/run/barcode-suggestions').then(function (res) {
                btn.disabled = false;
                renderBarcodes(describeBarcodes(res));
            });
        }

        function writeBarcodes() {
            var body = barcodeWriteBody(barcodeRows);
            if (!body.items.length) return Promise.resolve();
            if (!root.confirm(BARCODE_CONFIRM_TEXT)) return Promise.resolve();
            barcodeNote('');
            var btn = $('barcodes-write');
            btn.disabled = true;
            btn.setAttribute('aria-busy', 'true');
            return C.fetchJson('/api/run/barcode-write', { method: 'POST', body: body }).then(function (res) {
                btn.setAttribute('aria-busy', 'false');
                var result = barcodeResult(res);
                if (!res.ok) {
                    refreshBarcodeButton();
                    barcodeNote(result.text);
                    return;
                }
                if (result.ok) C.toast(result.text, 'success');
                return loadBarcodes().then(function () { barcodeNote(result.text, result.ok ? 'done' : ''); });
            });
        }

        function exportRun() {
            var btn = $('export');
            var scope = $('export-scope').value;
            C.setHidden($('export-error'), true);
            C.setHidden($('export-done'), true);
            btn.disabled = true;
            btn.setAttribute('aria-busy', 'true');
            C.setText($('export-text'), 'عم نجهّز التقرير…');
            var finish = function (error, done) {
                btn.disabled = false;
                btn.setAttribute('aria-busy', 'false');
                C.setText($('export-text'), 'تصدير تقرير للتحليل');
                C.setText($('export-error'), error || '');
                C.setHidden($('export-error'), !error);
                C.setText($('export-done'), done || '');
                C.setHidden($('export-done'), !done);
            };
            var generic = 'ما قدرنا نجهّز التقرير هلق. جرّب مرة ثانية.';
            if (typeof root.fetch !== 'function') return Promise.resolve(finish(generic));
            return root.fetch(exportQuery(scope), { credentials: 'same-origin', cache: 'no-store' }).then(function (res) {
                var disposition = res.headers && res.headers.get ? (res.headers.get('Content-Disposition') || '') : '';
                if (res.ok && /attachment/i.test(disposition)) {
                    var name = exportFileName(disposition);
                    var rows = res.headers.get('X-Laqta-Rows');
                    return res.blob().then(function (blob) {
                        var url = root.URL.createObjectURL(blob);
                        var a = doc.createElement('a');
                        a.setAttribute('href', url);
                        a.setAttribute('download', name);
                        doc.body.appendChild(a);
                        a.click();
                        doc.body.removeChild(a);
                        root.setTimeout(function () { root.URL.revokeObjectURL(url); }, 4000);
                        var count = rows !== null && rows !== undefined ? ' (' + C.countText(C.num(rows) || 0, 'صف', 'صفين', 'صفوف') + ')' : '';
                        finish('', 'نزل الملف ' + name + count + '. ابعته للمطوّر.');
                    });
                }
                return res.json().then(function (data) {
                    finish(data && arabic(data.message) ? data.message : generic);
                }, function () { finish(generic); });
            }, function () { finish('ما قدرنا نوصل للخادم.'); });
        }

        var controller = createController({
            fetchJson: C.fetchJson,
            renderLive: renderLive,
            renderPlan: renderPlan,
            renderStart: renderStart,
            confirm: function (text) { return root.confirm(text); },
            toast: C.toast,
            now: function () { return Date.now() / 1000; },
            schedule: function (fn, ms) { return root.setTimeout(fn, ms); }
        });

        // Scope, brand, rows and the two options feed the plan.
        page.addEventListener('lq:change', function (e) {
            var scope = e.detail && e.detail.value;
            if (!scope) return;
            C.setHidden($('brand-field'), scope !== 'brand');
            C.setHidden($('rows-field'), scope !== 'rows');
            renderStart({ error: '' });
            controller.setForm({ scope: scope });
        });
        $('brand').addEventListener('change', function () {
            renderStart({ error: '' });
            controller.setForm({ brand: $('brand').value });
        });
        var rowsTimer = null;
        $('rows').addEventListener('input', function () {
            root.clearTimeout(rowsTimer);
            rowsTimer = root.setTimeout(function () {
                renderStart({ error: '' });
                controller.setForm({ rows: $('rows').value });
            }, 400);
        });
        $('force').addEventListener('change', function () { controller.setForm({ force: $('force').checked }); });
        $('skip-cache').addEventListener('change', function () { controller.state.form.skipCache = $('skip-cache').checked; });
        $('start').addEventListener('click', function () {
            // the rows typed in the last 400 ms have not reached the form yet: start on what the field shows
            root.clearTimeout(rowsTimer);
            controller.start({ rows: $('rows').value });
        });
        $('pause').addEventListener('click', function () {
            if ($('pause').getAttribute('data-action') === 'resume') controller.resume();
            else controller.pause();
        });
        $('stop').addEventListener('click', function () { controller.stop(); });
        $('reset').addEventListener('click', function () { controller.reset(); });
        $('quality-refresh').addEventListener('click', function () { loadQuality(true); });
        $('brands-refresh').addEventListener('click', function () { loadMissing(); });
        $('brands-add-all').addEventListener('click', function () { addAllBrands(); });
        $('barcodes-refresh').addEventListener('click', function () { barcodeNote(''); loadBarcodes(); });
        $('barcodes-write').addEventListener('click', function () { writeBarcodes(); });
        $('export').addEventListener('click', function () { exportRun(); });

        var initial = null;
        var island = doc.getElementById('lq-run-initial');
        try {
            initial = island ? JSON.parse(island.textContent || 'null') : null;
        } catch (e) {
            initial = null;
        }
        // «جودة بيانات الشيت» بعد «قبل ما تبدأ»: الخطة تقرأ صفوف الشيت (من كاشها)، والجودة تقرأ الكاش نفسه فقط
        controller.refreshPlan(false).then(function () { return loadQuality(false); }, function () { return loadQuality(false); })
            .then(function () { return loadMissing(); }).then(function () { return loadBarcodes(); });
        if (initial) controller.handleLive(initial);
        controller.state.timer = root.setTimeout(controller.loop, initial ? POLL_ACTIVE_MS : 0);
        doc.addEventListener('visibilitychange', function () {
            if (!doc.hidden) controller.poll();
        });
        return controller;
    }

    root.LaqtaRunPage = {
        STOP_CONFIRM_TEXT: STOP_CONFIRM_TEXT,
        RESET_CONFIRM_TEXT: RESET_CONFIRM_TEXT,
        FORCE_CONFIRM_TEXT: FORCE_CONFIRM_TEXT,
        describeLive: describeLive,
        describePlan: describePlan,
        describeQuality: describeQuality,
        describeBrands: describeBrands,
        brandBody: brandBody,
        addAllBody: addAllBody,
        bareHost: bareHost,
        splitList: splitList,
        brandsTitle: brandsTitle,
        addedText: addedText,
        addAllConfirmText: addAllConfirmText,
        BRAND_ADDED_TEXT: BRAND_ADDED_TEXT,
        describeBarcodes: describeBarcodes,
        barcodeWriteBody: barcodeWriteBody,
        barcodeResult: barcodeResult,
        barcodesTitle: barcodesTitle,
        BARCODE_CONFIRM_TEXT: BARCODE_CONFIRM_TEXT,
        SITE_COST_NOTE: SITE_COST_NOTE,
        exportQuery: exportQuery,
        exportFileName: exportFileName,
        planQuery: planQuery,
        runBody: runBody,
        createController: createController,
        mount: mount
    };

    var doc = root.document;
    if (doc && typeof doc.querySelector === 'function') {
        if (doc.readyState === 'loading') {
            doc.addEventListener('DOMContentLoaded', function () { mount(doc); });
        } else {
            mount(doc);
        }
    }
})(typeof window !== 'undefined' ? window : globalThis);
