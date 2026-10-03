/*
 * لقطة · الصحة والتكلفة (resources/views/dashboard/diagnostics.blade.php).
 *
 * - Service cards start from the saved result of the last connection check (#lq-health-initial). Opening the page
 *   never runs the check: only the «فحص الاتصالات الآن» button POSTs /api/system/run-diagnostics.
 * - «عمليات البحث» comes from GET /api/system/ops-health (ops_health.summarize over automation_queue.trace_json),
 *   split in plain Arabic; decision and failure codes appear only in tooltips.
 * - «السجل» shows the tails of GET /api/view-pipeline-log, /api/view-laravel-log and /api/view-nightly-log (the
 *   newest temp/nightly/nightly_YYYY-MM-DD.log); a missing file is the normal «لسا ما في سجل» state.
 * The view functions are pure (node tests call them through window.LaqtaHealth); the DOM code below only sets
 * textContent and attributes, never HTML.
 */
(function () {
    'use strict';

    var RUN_URL = '/api/system/run-diagnostics';
    var OPS_URL = '/api/system/ops-health';
    var LOG_URLS = { pipeline: '/api/view-pipeline-log', laravel: '/api/view-laravel-log',
        nightly: '/api/view-nightly-log' };
    var LOG_POLL_MS = 5000;

    var SERVICE_KEYS = ['google_sheets', 'serper', 'gemini', 'photoroom', 'cloudinary'];
    var OPTIONAL_SERVICES = { proxy: 'البروكسي', google_search: 'Google Custom Search (قديم)' };

    /* Decision codes of ops_health windows -> the result words of the review and run screens. */
    var RESULT_BUCKETS = [
        { key: 'proposed', label: 'مقترحة', tone: 'success', codes: ['REVIEW_PRESELECTED', 'AUTO_PUBLISH'] },
        { key: 'none', label: 'بلا اقتراح', tone: 'muted', codes: ['REVIEW_UNSELECTED', 'VERIFIER_DOWN'] },
        { key: 'not_found', label: 'ما انلقت', tone: 'info', codes: ['NOT_FOUND'] },
        { key: 'error', label: 'أعطال مؤقتة', tone: 'danger', codes: ['PROVIDER_DOWN'] }
    ];

    var PROVIDER_NAMES = {
        serper: 'Serper (Google)',
        serper_web: 'Serper · صفحات المتاجر',
        serper_shopping: 'Serper · Google Shopping',
        lens_serper: 'Serper · بحث بالصورة',
        lens_serpapi: 'SerpApi · Google Lens',
        bing_html: 'Bing (احتياطي)',
        off: 'Open Food Facts',
        local_index: 'الفهرس المحلي (مجاني)',
        cse_legacy: 'Google Custom Search (قديم)'
    };

    /* Failure codes -> [what happened, what to try]. The code itself stays in the tooltip. */
    var REASONS = {
        ALL_CONFLICTED: ['كل النتائج لمنتج أو ماركة تانية', 'جرّب بحث بكلمات أخرى أو اسم المتجر'],
        NO_RESULTS: ['ما في نتائج أبداً', 'غالباً اسم غير مألوف أو فيه اختصار'],
        NO_MATCH: ['ما في صورة مطابقة للمنتج', 'جرّب بحث بكلمات أخرى أو اسم المتجر'],
        DOWNLOAD_FAILED: ['ما قدرنا ننزّل الصور', 'صفحات تواصل اجتماعي أو مواقع بطيئة'],
        VERIFIER_DOWN: ['نموذج قراءة الملصق ما ردّ', 'الصور راحت لمراجعتك بدون قراءة الملصق'],
        PROVIDER_DOWN: ['مصادر البحث ما ردّت', 'المنتجات رجعت للطابور وبتنعاد بالتشغيل الجاي'],
        SEARCH_ERROR: ['صار خطأ أثناء البحث', 'التفاصيل بسجل الأتمتة تحت'],
        CANDIDATE_SAVE_FAILED: ['ما قدرنا نحفظ الاقتراحات', 'تأكد إن قاعدة البيانات شغّالة'],
        WORKER_ERROR: ['التشغيل وقف فجأة', 'التفاصيل بسجل الأتمتة تحت'],
        REJECTED: ['رفضت كل الاقتراحات', 'بتقدر تدوّر من جديد من شاشة المراجعة']
    };
    var OTHER_REASON = ['سبب تاني', 'التفاصيل بسجل الأتمتة تحت'];

    var ALERT_TEXT = {
        SERPER_CREDIT: function (n) {
            return 'آخر ' + n + ' عمليات بحث رفض فيها Serper كل الاستعلامات. اشحن الرصيد أو غيّر المفتاح من الإعدادات.';
        },
        GEMINI_DOWN: function (n) {
            return 'آخر ' + n + ' عمليات بحث احتاجت نموذج قراءة الملصق وما ردّ، فالنتائج بتستنى مراجعتك. تأكد من مفتاحه (Gemini أو Anthropic) من الإعدادات.';
        },
        VERIFIER_BUDGET: function () {
            return 'المنتجات المش مؤكدة بتستنى مراجعتك بدون نظرة تانية. ارفع الميزانية من «نماذج التحقق» أو استنى الشهر الجاي.';
        },
        VERIFIER_KEY: function (n) {
            return 'آخر ' + n + ' عمليات بحث ما قدر فيها النموذج القوي يقرأ لأن مفتاحه مرفوض أو مش محفوظ. ضيفه من «المفاتيح» أو وقّفه من «نماذج التحقق».';
        }
    };

    /* Label readers (catalog_match/verifiers/registry.SUPPORTED_MODELS); another model shows its own id. */
    var MODEL_NAMES = {
        'gemini-3.1-flash-lite': 'Gemini 3.1 Flash-Lite',
        'gemini-3.5-flash': 'Gemini 3.5 Flash',
        'claude-haiku-4-5': 'Claude Haiku 4.5',
        'claude-sonnet-5-5': 'Claude Sonnet 5.5',
        'claude-opus-5-5': 'Claude Opus 5.5'
    };

    var WINDOW_LABELS = { '24h': 'آخر 24 ساعة', '7d': 'آخر 7 أيام' };
    var MONTHS = ['كانون الثاني', 'شباط', 'آذار', 'نيسان', 'أيار', 'حزيران', 'تموز', 'آب', 'أيلول', 'تشرين الأول',
        'تشرين الثاني', 'كانون الأول'];
    /* Log lines shown in red: English and Arabic failure words, and the cross mark (U+274C) the pipeline prints. */
    var ERROR_LINE = /(error|fail|exception|traceback|timeout|timed out|critical|\u274C|خطأ|فشل|تعذر)/i;

    // ------------------------------------------------------------------
    // Small helpers
    // ------------------------------------------------------------------

    function isObject(v) {
        return v !== null && typeof v === 'object' && !Array.isArray(v);
    }

    function num(v) {
        var n = typeof v === 'string' && v.trim() !== '' ? Number(v) : v;
        return typeof n === 'number' && isFinite(n) ? n : null;
    }

    function count(v) {
        var n = num(v);
        return n === null || n < 0 ? 0 : Math.floor(n);
    }

    function pad(n) {
        return (n < 10 ? '0' : '') + n;
    }

    /* "منتج واحد", "منتجين", "5 منتجات", "40 منتج" (western digits); `single` overrides the one-item form. */
    function plural(n, one, two, few, single) {
        if (n === 1) return single !== undefined ? single : one + ' واحد';
        if (n === 2) return two;
        return n + ' ' + (n >= 3 && n <= 10 ? few : one);
    }

    /* «صف واحد», «صفين», «5 صفوف», «100 صف» (HealthController::rows says the same). */
    function rows(n) {
        return plural(count(n), 'صف', 'صفين', 'صفوف');
    }

    /* Money in an LTR isolate so «$0.01» keeps its sign on the left inside Arabic text. */
    function ltr(text) {
        return '\u2066' + text + '\u2069';
    }

    function usd(v) {
        v = num(v);
        if (v === null) return '—';
        if (v > 0 && v < 0.005) return 'أقل من ' + ltr('$0.01');
        return ltr('$' + v.toFixed(2));
    }

    function usdPrecise(v) {
        v = num(v);
        if (v === null) return '—';
        if (v > 0 && v < 0.0005) return 'أقل من ' + ltr('$0.001');
        return ltr('$' + v.toFixed(3));
    }

    function price(v) {
        v = num(v);
        if (v === null) return '—';
        return ltr('$' + String(Number(v.toFixed(6))));
    }

    function ageText(seconds) {
        seconds = count(seconds);
        if (seconds < 60) return 'أقل من دقيقة';
        var minutes = Math.round(seconds / 60);
        if (minutes < 60) return plural(minutes, 'دقيقة', 'دقيقتين', 'دقائق', 'دقيقة');
        var hours = Math.round(seconds / 3600);
        if (hours < 24) return plural(hours, 'ساعة', 'ساعتين', 'ساعات', 'ساعة');
        return plural(Math.round(seconds / 86400), 'يوم', 'يومين', 'أيام', 'يوم');
    }

    /* "اليوم 09:12" / "مبارح 18:40" / "28 أيلول 09:12" in the viewer's local time. */
    function whenText(ms, nowMs) {
        var d = new Date(ms);
        var now = new Date(nowMs);
        var clock = pad(d.getHours()) + ':' + pad(d.getMinutes());
        var startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
        if (ms >= startOfToday && ms < startOfToday + 86400000) return 'اليوم ' + clock;
        if (ms >= startOfToday - 86400000 && ms < startOfToday) return 'مبارح ' + clock;
        var text = d.getDate() + ' ' + MONTHS[d.getMonth()];
        if (d.getFullYear() !== now.getFullYear()) text += ' ' + d.getFullYear();
        return text + ' ' + clock;
    }

    // ------------------------------------------------------------------
    // Views (pure)
    // ------------------------------------------------------------------

    /* One service card from the last check (same words as HealthController::serviceCards). */
    function serviceView(key, result) {
        if (!isObject(result) || !isObject(result.services)) {
            return { state: 'لسا ما انفحص', tone: 'muted', details: '' };
        }
        var service = result.services[key];
        var status = isObject(service) ? String(service.status || '') : '';
        if (!status) return { state: 'ما انفحص', tone: 'muted', details: '' };
        if (status === 'online') return { state: key === 'google_sheets' ? 'متصل' : 'يعمل', tone: 'success', details: '' };
        if (status === 'disabled') return { state: 'مش مفعّل', tone: 'muted', details: '' };
        return { state: 'ما بيرد', tone: 'danger', details: String(service.details || '').trim().slice(0, 2000) };
    }

    function servicesView(result) {
        var out = {};
        SERVICE_KEYS.forEach(function (key) { out[key] = serviceView(key, result); });
        return out;
    }

    function checkingView() {
        var out = {};
        SERVICE_KEYS.forEach(function (key) { out[key] = { state: 'عم نفحص…', tone: 'muted', details: '' }; });
        return out;
    }

    function optionalView(result) {
        var items = [];
        if (!isObject(result) || !isObject(result.services)) return items;
        Object.keys(OPTIONAL_SERVICES).forEach(function (key) {
            var service = result.services[key];
            var status = isObject(service) ? String(service.status || '') : '';
            if (!status || status === 'disabled') return;
            items.push({ name: OPTIONAL_SERVICES[key], state: status === 'online' ? 'يعمل' : 'ما بيرد',
                tone: status === 'online' ? 'success' : 'warning' });
        });
        return items;
    }

    function checkedView(result, nowMs) {
        var at = isObject(result) && typeof result.checked_at === 'string' ? Date.parse(result.checked_at) : NaN;
        if (!isFinite(at)) return { text: 'لسا ما انعمل فحص للاتصالات.', warn: false };
        return { text: 'آخر فحص للاتصالات: ' + whenText(at, nowMs) + '.', warn: result.all_ok === false };
    }

    function decisionsView(w) {
        var decisions = isObject(w.decisions) ? w.decisions : {};
        var total = count(w.searches);
        var used = {};
        var rows = RESULT_BUCKETS.map(function (b) {
            var value = 0;
            var parts = [];
            b.codes.forEach(function (code) {
                var n = count(decisions[code]);
                used[code] = true;
                if (n > 0) {
                    value += n;
                    parts.push(code + ' ' + n);
                }
            });
            return { key: b.key, label: b.label, tone: b.tone, value: value, title: parts.join(' · ') };
        });
        var other = 0;
        var otherParts = [];
        Object.keys(decisions).forEach(function (code) {
            if (used[code]) return;
            var n = count(decisions[code]);
            if (n > 0) {
                other += n;
                otherParts.push(code + ' ' + n);
            }
        });
        if (other > 0) rows.push({ key: 'other', label: 'غير ذلك', tone: 'muted', value: other, title: otherParts.join(' · ') });
        rows.forEach(function (r) {
            r.pct = total > 0 ? Math.min(100, Math.round(r.value / total * 1000) / 10) : 0;
        });
        return rows;
    }

    function providersView(w) {
        var providers = isObject(w.providers) ? w.providers : {};
        var names = Object.keys(providers).sort(function (a, b) {
            if (a === 'serper') return -1;
            if (b === 'serper') return 1;
            return a < b ? -1 : (a > b ? 1 : 0);
        });
        return names.map(function (name) {
            var c = isObject(providers[name]) ? providers[name] : {};
            var problems = [];
            var tone = 'muted';
            if (count(c.error) > 0) { problems.push('أخطاء ' + count(c.error)); tone = 'danger'; }
            if (count(c.quota) > 0) { problems.push('رفض الرصيد ' + count(c.quota)); tone = 'danger'; }
            if (count(c.blocked) > 0) { problems.push('محجوب ' + count(c.blocked)); tone = tone === 'danger' ? tone : 'warning'; }
            if (count(c.other) > 0) { problems.push('غير ذلك ' + count(c.other)); tone = tone === 'muted' ? 'warning' : tone; }
            return {
                name: PROVIDER_NAMES[name] || name,
                ok: 'نجحت ' + count(c.ok),
                empty: 'فاضية ' + count(c.empty),
                problems: problems.length ? problems.join(' · ') : 'ما في أخطاء',
                tone: tone,
                title: name
            };
        });
    }

    /* One cost line per label reader and role (ops_health window.verifier_models), e.g. «Claude Sonnet 5.5 · نظرة تانية · فحصين». */
    function modelLines(models) {
        return models.filter(isObject).map(function (m) {
            var model = String(m.model || '');
            var name = MODEL_NAMES[model] || model || 'نموذج';
            var role = m.role === 'strong' ? ' · نظرة تانية' : '';
            return { label: name + role + ' · ' + plural(count(m.calls), 'فحص', 'فحصين', 'فحوصات'), value: usd(m.usd),
                title: String(m.provider || '') + ':' + model };
        });
    }

    function costView(w, prices, month, sourcePrices, runs) {
        var cost = isObject(w.cost_usd) ? w.cost_usd : {};
        var sources = isObject(w.sources) ? w.sources : {};
        var lens = isObject(sources.lens_serpapi) ? sources.lens_serpapi : null;
        var verifier = isObject(w.verifier) ? w.verifier : {};
        var models = Array.isArray(w.verifier_models) ? w.verifier_models.filter(isObject) : [];
        var searches = count(w.searches);
        var total = num(cost.total);
        var note = [];
        if (searches > 0 && total !== null) note.push('التكلفة لكل منتج تقريباً ' + usdPrecise(total / searches) + '.');
        var lines = [
            { label: 'Serper · ' + plural(count(w.serper_queries), 'استعلام', 'استعلامين', 'استعلامات'), value: usd(cost.serper) }
        ];
        /* SerpApi Google Lens (the expansion round, catalog_match/expand.py): its own line, at its own price */
        if (lens || num(cost.serpapi) !== null) {
            lines.push({ label: 'SerpApi Lens · ' + plural(count(lens ? lens.calls : 0), 'بحث', 'بحثين', 'عمليات بحث'),
                value: usd(cost.serpapi), title: 'lens_serpapi' });
        }
        if (!models.length) {
            lines.push({ label: 'Gemini · ' + plural(count(verifier.calls), 'فحص', 'فحصين', 'فحوصات'), value: usd(cost.gemini) });
            if (isObject(prices)) {
                note.push('الأسعار تقديرية: Serper ' + price(prices.serper_per_query) + ' لكل استعلام بيرد عليه، وGemini '
                    + price(prices.gemini_per_call) + ' لكل فحص.');
            }
        } else {
            lines = lines.concat(modelLines(models));
            var geminiUsage = 0;
            models.forEach(function (m) {
                if (m.provider !== 'claude') geminiUsage += num(m.usd) || 0;
            });
            var older = (num(cost.gemini) || 0) - geminiUsage;
            if (older > 0.0005) {
                lines.push({ label: 'Gemini · قراءات أقدم بلا تفاصيل', value: usd(older) });
            }
            if (isObject(prices)) {
                note.push('الأسعار تقديرية: Serper ' + price(prices.serper_per_query) + ' لكل استعلام بيرد عليه، ونماذج القراءة '
                    + 'حسب الـ tokens بأسعار تبويب «نماذج التحقق».');
            }
        }
        if (lens && isObject(sourcePrices) && num(sourcePrices.lens_serpapi) !== null) {
            note.push('بحث SerpApi ' + price(sourcePrices.lens_serpapi) + ' للبحث الواحد (من تبويب «متقدم»).');
        }
        if (isObject(month) && num(month.budget_usd) !== null) {
            note.push('النموذج القوي هالشهر: ' + usd(month.strong_usd) + ' من ' + usd(month.budget_usd) + '.');
        }
        /* run_history (ops_health.runs_cost): each run's cost saved when it ended, kept after a later search
           overwrites the rows; the figure above sees only each row's last search. */
        if (isObject(runs) && count(runs.priced_runs) > 0) {
            note.push('حسب سجل التشغيلات: ' + usd(runs.usd) + ' في ' + plural(count(runs.priced_runs), 'تشغيل', 'تشغيلين', 'تشغيلات')
                + ' (يشمل عمليات بحث أعيدت لاحقاً).');
        }
        return { total: usd(total), lines: lines, note: note.join(' ') };
    }

    function reasonsView(w) {
        var codes = Array.isArray(w.failure_codes) ? w.failure_codes : [];
        return codes.filter(function (c) { return isObject(c) && count(c.count) > 0; }).map(function (c) {
            var code = String(c.code || '');
            var text = REASONS[code.toUpperCase()] || OTHER_REASON;
            return { label: text[0], hint: text[1], value: count(c.count), title: code };
        });
    }

    function alertsView(report) {
        var alerts = Array.isArray(report.alerts) ? report.alerts : [];
        return alerts.filter(function (a) { return isObject(a) && String(a.message || '').trim() !== ''; }).map(function (a) {
            var code = String(a.code || '');
            var n = count(a.searches);
            return {
                title: String(a.message).trim(),
                text: ALERT_TEXT[code] ? ALERT_TEXT[code](n) : '',
                detail: String(a.detail || '')
            };
        });
    }

    function noteView(report, w) {
        var parts = [];
        if (num(report.latest_age_s) !== null) parts.push('آخر بحث قبل ' + ageText(report.latest_age_s) + '.');
        parts.push('الأرقام من آخر بحث لكل منتج بالطابور (' + rows(report.scanned)
            + ')؛ إعادة البحث من شاشة المراجعة ما بتنحسب.');
        if (report.truncated) parts.push('وصلنا للحد (' + rows(report.limit) + ')، فالأرقام الأقدم ناقصة.');
        if (count(report.timed_by_row_update) > 0) {
            parts.push('وقت بعض عمليات البحث مأخوذ من آخر تحديث للصف، فممكن تدخل عمليات أقدم بالفترة.');
        }
        if (count(w.unreadable) > 0) parts.push('في ' + rows(w.unreadable) + ' ما قدرنا نقرأ نتيجة بحثه.');
        return parts.join(' ');
    }

    /* The whole «عمليات البحث» section for one window: kind ok | empty | window-empty. */
    function opsView(report, windowName) {
        if (!isObject(report)) return { kind: 'error', text: '' };
        var label = WINDOW_LABELS[windowName] || WINDOW_LABELS['24h'];
        var alerts = alertsView(report);
        if (count(report.scanned) === 0) {
            return { kind: 'empty', alerts: alerts, title: 'ما في عمليات بحث بآخر 7 أيام',
                text: 'أول ما تشغّل بحث من صفحة التشغيل، بتبيّن هون النتائج وردود المصادر والتكلفة.', action: true, note: '' };
        }
        var w = isObject(report.windows) && isObject(report.windows[windowName]) ? report.windows[windowName] : {};
        var codes = Array.isArray(w.failure_codes) ? w.failure_codes : [];
        if (count(w.searches) === 0 && count(w.unreadable) === 0 && codes.length === 0) {
            return { kind: 'window-empty', alerts: alerts, title: 'ما في عمليات بحث ب' + label,
                text: windowName === '24h' ? 'جرّب «آخر 7 أيام» لتشوف عمليات البحث الأقدم.' : '', action: false,
                note: noteView(report, w) };
        }
        return {
            kind: 'ok',
            alerts: alerts,
            total: plural(count(w.searches), 'منتج', 'منتجين', 'منتجات'),
            decisions: decisionsView(w),
            providers: providersView(w),
            cost: costView(w, report.prices, report.verifier_month, report.source_prices,
                isObject(report.runs_cost) ? report.runs_cost[windowName] : null),
            reasons: reasonsView(w),
            note: noteView(report, w)
        };
    }

    function logView(payload, kind, nowMs) {
        if (!isObject(payload) || payload.status !== 'success') {
            return { kind: 'error', text: 'ما قدرنا نقرأ السجل. جرّب كمان شوي.', lines: [], meta: '' };
        }
        if (!payload.exists) {
            var why = kind === 'laravel' ? ' لوحة التحكم ما سجّلت ولا شي لهلق.'
                : kind === 'nightly' ? ' التشغيل الليلي ما اشتغل لهلق (سجّله بـ scripts\\schedule_nightly.ps1).'
                    : ' بيبلّش أول ما تشغّل بحث من صفحة التشغيل.';
            return { kind: 'missing', text: 'لسا ما في سجل.' + why, lines: [], meta: '' };
        }
        var lines = (Array.isArray(payload.lines) ? payload.lines : []).map(function (line) {
            line = String(line);
            return { text: line, error: ERROR_LINE.test(line) };
        });
        if (!lines.length) return { kind: 'empty', text: 'السجل فاضي.', lines: [], meta: '' };
        var meta = lines.length === 1 ? 'آخر سطر' : 'آخر ' + plural(lines.length, 'سطر', 'سطرين', 'أسطر');
        var at = num(payload.updated_at);
        if (kind === 'nightly' && typeof payload.date === 'string' && payload.date) meta = 'ليلة ' + payload.date + ' · ' + meta;
        if (at !== null) meta += ' · آخر تعديل ' + whenText(at * 1000, nowMs);
        return { kind: 'ok', text: '', lines: lines, meta: meta };
    }

    function requestError(res, fallback) {
        if (res && res.status === 419) return 'انتهت صلاحية الصفحة. حدّثها وجرّب مرة تانية.';
        var data = res && isObject(res.data) ? res.data : null;
        var text = data && typeof data.error === 'string' ? data.error.trim() : '';
        return text && /[؀-ۿ]/.test(text) ? text : fallback;
    }

    // ------------------------------------------------------------------
    // Controller (deps injected: fetchJson, render*, toast, now, schedule, isHidden)
    // ------------------------------------------------------------------

    function createController(deps) {
        var state = { last: deps.initial || null, report: null, windowName: '24h', logKind: 'pipeline',
            checking: false, opsSeq: 0, logSeq: 0, timer: null };

        function now() {
            return deps.now ? deps.now() : Date.now();
        }

        function showServices() {
            deps.renderServices(servicesView(state.last));
            deps.renderChecked(checkedView(state.last, now()));
            deps.renderOptional(optionalView(state.last));
        }

        /* The connection check: only from the button. Every card says «عم نفحص…» until the answer. */
        function runCheck() {
            if (state.checking) return Promise.resolve(false);
            state.checking = true;
            deps.setChecking(true);
            deps.renderServices(checkingView());
            return deps.fetchJson(RUN_URL, { method: 'POST', body: {} }).then(function (res) {
                if (res && res.ok && isObject(res.data) && isObject(res.data.services)) {
                    state.last = res.data;
                    showServices();
                    deps.toast(res.data.all_ok === false ? 'خلص الفحص: في خدمات أساسية ما بتردّ.'
                        : 'خلص الفحص: كل الخدمات الأساسية بتردّ.', res.data.all_ok === false ? 'warning' : 'success');
                    return true;
                }
                showServices();
                deps.toast('ما خلص الفحص: ' + requestError(res, 'الخادم ما رجّع نتيجة.'), 'danger');
                return false;
            }, function () {
                showServices();
                deps.toast('ما قدرنا نوصل للخادم لنفحص الاتصالات.', 'danger');
                return false;
            }).then(function (done) {
                state.checking = false;
                deps.setChecking(false);
                return done;
            });
        }

        function loadOps(refresh) {
            var seq = ++state.opsSeq;
            deps.renderOps({ kind: 'loading' });
            return deps.fetchJson(OPS_URL + (refresh ? '?refresh=1' : ''), { method: 'GET' }).then(function (res) {
                if (seq !== state.opsSeq) return;
                if (res && res.ok && isObject(res.data) && res.data.status === 'success') {
                    state.report = res.data;
                    deps.renderOps(opsView(state.report, state.windowName));
                } else {
                    state.report = null;
                    deps.renderOps({ kind: 'error', text: requestError(res, 'جسر بايثون أو قاعدة البيانات ما ردّ.') });
                }
            }, function () {
                if (seq !== state.opsSeq) return;
                state.report = null;
                deps.renderOps({ kind: 'error', text: 'ما قدرنا نوصل للخادم.' });
            });
        }

        function setWindow(name) {
            if (!WINDOW_LABELS[name]) return;
            state.windowName = name;
            if (state.report) deps.renderOps(opsView(state.report, name));
        }

        function loadLog() {
            var seq = ++state.logSeq;
            var kind = state.logKind;
            return deps.fetchJson(LOG_URLS[kind], { method: 'GET' }).then(function (res) {
                if (seq !== state.logSeq) return;
                deps.renderLog(logView(res && res.data, kind, now()), kind);
            }, function () {
                if (seq !== state.logSeq) return;
                deps.renderLog(logView(null, kind, now()), kind);
            });
        }

        function setLogKind(kind) {
            if (!LOG_URLS[kind] || kind === state.logKind) return Promise.resolve();
            state.logKind = kind;
            deps.renderLog({ kind: 'loading', text: 'لحظة، عم نقرأ السجل…', lines: [], meta: '' }, kind);
            return loadLog();
        }

        function poll() {
            if (!(deps.isHidden && deps.isHidden())) loadLog();
            state.timer = deps.schedule(poll, LOG_POLL_MS);
        }

        function start() {
            showServices();
            loadOps(false);
            loadLog();
            state.timer = deps.schedule(poll, LOG_POLL_MS);
        }

        return { start: start, runCheck: runCheck, loadOps: loadOps, setWindow: setWindow, loadLog: loadLog,
            setLogKind: setLogKind, state: state };
    }

    var api = {
        serviceView: serviceView, servicesView: servicesView, checkingView: checkingView, optionalView: optionalView,
        checkedView: checkedView, opsView: opsView, logView: logView, whenText: whenText, ageText: ageText,
        usd: usd, usdPrecise: usdPrecise, requestError: requestError, createController: createController,
        SERVICE_KEYS: SERVICE_KEYS, RESULT_BUCKETS: RESULT_BUCKETS, REASONS: REASONS
    };
    if (typeof window !== 'undefined') window.LaqtaHealth = api;

    // ------------------------------------------------------------------
    // DOM
    // ------------------------------------------------------------------

    if (typeof document === 'undefined' || !document.querySelector || !document.querySelector('[data-health-page]')) {
        return;
    }

    var page = document.querySelector('[data-health-page]');
    var DOTS = { success: 'lq-dot--success', danger: 'lq-dot--danger', warning: 'lq-dot--warning', info: 'lq-dot--info',
        muted: 'lq-dot--muted' };

    function $(hook) {
        return page.querySelector('[data-health="' + hook + '"]');
    }

    function setHidden(el, hidden) {
        if (!el) return;
        if (hidden) el.setAttribute('hidden', '');
        else el.removeAttribute('hidden');
    }

    function clear(el) {
        while (el && el.firstChild) el.removeChild(el.firstChild);
    }

    function make(tag, className, text) {
        var el = document.createElement(tag);
        if (className) el.className = className;
        if (text !== undefined && text !== null) el.textContent = String(text);
        return el;
    }

    function csrf() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    function fetchJson(url, opts) {
        opts = opts || {};
        var init = { method: opts.method || 'GET', credentials: 'same-origin', cache: 'no-store',
            headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' } };
        if (init.method !== 'GET') {
            init.headers['Content-Type'] = 'application/json';
            init.headers['X-CSRF-TOKEN'] = csrf();
            init.body = JSON.stringify(opts.body || {});
        }
        return fetch(url, init).then(function (res) {
            return res.text().then(function (text) {
                var data = null;
                try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
                return { ok: res.ok, status: res.status, data: data };
            });
        });
    }

    function readInitial() {
        var holder = document.getElementById('lq-health-initial');
        if (!holder) return null;
        try {
            var data = JSON.parse(holder.textContent || 'null');
            return isObject(data) && isObject(data.services) ? data : null;
        } catch (e) {
            return null;
        }
    }

    function renderServices(views) {
        Object.keys(views).forEach(function (key) {
            var card = page.querySelector('[data-service="' + key + '"]');
            if (!card) return;
            var view = views[key];
            card.setAttribute('data-tone', view.tone);
            var state = card.querySelector('[data-service-state]');
            if (state) state.textContent = view.state;
            var dot = card.querySelector('[data-service-dot]');
            if (dot) dot.className = 'lq-dot lq-dot--lg ' + (DOTS[view.tone] || DOTS.muted);
            var details = card.querySelector('[data-service-details]');
            var detailsText = card.querySelector('[data-service-details-text]');
            if (detailsText) detailsText.textContent = view.details || '';
            setHidden(details, !view.details);
        });
    }

    function renderChecked(view) {
        var text = $('checked-text');
        if (text) text.textContent = view.text;
        setHidden($('checked-warn'), !view.warn);
    }

    function renderOptional(items) {
        var holder = $('optional');
        if (!holder) return;
        clear(holder);
        holder.appendChild(document.createTextNode('خدمات اختيارية: '));
        items.forEach(function (item) {
            var span = make('span', 'lq-health__optional-item');
            var dot = make('span', 'lq-dot ' + (DOTS[item.tone] || DOTS.muted));
            dot.setAttribute('aria-hidden', 'true');
            span.appendChild(dot);
            span.appendChild(document.createTextNode(item.name + ' ' + item.state));
            holder.appendChild(span);
        });
        setHidden(holder, items.length === 0);
    }

    var runButton = $('run-check');
    var runLabel = runButton ? runButton.querySelector('[data-health="run-check-label"]') : null;

    function setChecking(busy) {
        if (!runButton) return;
        runButton.disabled = busy;
        if (busy) runButton.setAttribute('aria-busy', 'true');
        else runButton.removeAttribute('aria-busy');
        if (runLabel) runLabel.textContent = busy ? 'عم نفحص…' : 'فحص الاتصالات الآن';
    }

    function skeletonRows(holder, n, extra) {
        clear(holder);
        for (var i = 0; i < n; i++) {
            var s = make('span', 'lq-skeleton ' + (extra || 'lq-skeleton--text'));
            s.setAttribute('aria-hidden', 'true');
            holder.appendChild(s);
        }
    }

    function renderAlerts(alerts) {
        var holder = $('alerts');
        clear(holder);
        (alerts || []).forEach(function (a) {
            var box = make('div', 'lq-alert lq-alert--danger lq-alert--banner');
            box.setAttribute('role', 'alert');
            if (a.detail) box.title = a.detail;
            var body = make('div', 'lq-alert__body');
            body.appendChild(make('strong', 'lq-alert__title', a.title + (a.text ? ':' : '')));
            if (a.text) body.appendChild(document.createTextNode(' ' + a.text));
            box.appendChild(body);
            var link = make('a', 'lq-alert__action', 'الإعدادات');
            link.href = (page.getAttribute('data-settings-url') || '/settings') + '?tab=keys';
            box.appendChild(link);
            holder.appendChild(box);
        });
        setHidden(holder, !alerts || alerts.length === 0);
    }

    function renderDecisions(rows) {
        var holder = $('decisions');
        clear(holder);
        rows.forEach(function (r) {
            var row = make('div', 'lq-health-result');
            if (r.title) row.title = r.title;
            row.appendChild(make('span', 'lq-health-result__label', r.label));
            var track = make('span', 'lq-health-result__track');
            track.setAttribute('aria-hidden', 'true');
            var fill = make('span', 'lq-health-result__fill lq-tone--' + r.tone);
            fill.style.width = r.pct + '%';
            track.appendChild(fill);
            row.appendChild(track);
            row.appendChild(make('span', 'lq-health-result__value', r.value));
            holder.appendChild(row);
        });
    }

    function renderProviders(rows) {
        var holder = $('providers');
        clear(holder);
        if (!rows.length) {
            holder.appendChild(make('p', 'lq-health__muted', 'ما في ردود مصادر بهالفترة.'));
            return;
        }
        rows.forEach(function (p) {
            var row = make('div', 'lq-health-provider');
            row.title = p.title;
            row.appendChild(make('span', 'lq-health-provider__name', p.name));
            row.appendChild(make('span', 'lq-health-provider__ok', p.ok));
            row.appendChild(make('span', 'lq-health-provider__empty', p.empty));
            row.appendChild(make('span', 'lq-health-provider__problems lq-health-provider__problems--' + p.tone, p.problems));
            holder.appendChild(row);
        });
    }

    function renderCost(cost) {
        $('cost-total').textContent = cost.total;
        var lines = $('cost-lines');
        clear(lines);
        cost.lines.forEach(function (l) {
            var row = make('div', 'lq-health-cost__line');
            if (l.title) row.title = l.title;
            row.appendChild(make('span', '', l.label));
            row.appendChild(make('span', 'lq-health-cost__value', l.value));
            lines.appendChild(row);
        });
        $('cost-note').textContent = cost.note;
    }

    function renderReasons(rows) {
        var holder = $('reasons');
        clear(holder);
        if (!rows.length) {
            holder.appendChild(make('p', 'lq-health__muted', 'ما في ولا سبب بهالفترة: كل منتج لقينا إله اقتراح أو بلا اقتراح.'));
            return;
        }
        rows.forEach(function (r) {
            var row = make('div', 'lq-health-reason');
            row.title = r.title;
            var text = make('span', 'lq-health-reason__text');
            text.appendChild(make('span', 'lq-health-reason__label', r.label));
            text.appendChild(make('span', 'lq-health-reason__hint', r.hint));
            row.appendChild(text);
            row.appendChild(make('span', 'lq-health-reason__value', r.value));
            holder.appendChild(row);
        });
    }

    function renderOps(view) {
        var grid = $('ops');
        var empty = $('ops-empty');
        var error = $('ops-error');
        var note = $('ops-note');
        if (view.kind === 'loading') {
            grid.setAttribute('aria-busy', 'true');
            setHidden(grid, false);
            setHidden(empty, true);
            setHidden(error, true);
            $('results-total').textContent = '';
            skeletonRows($('decisions'), 4);
            skeletonRows($('providers'), 1, 'lq-skeleton--short');
            skeletonRows($('reasons'), 2);
            skeletonRows($('cost-total'), 1, 'lq-skeleton--title');
            clear($('cost-lines'));
            $('cost-note').textContent = '';
            return;
        }
        grid.removeAttribute('aria-busy');
        renderAlerts(view.alerts);
        note.textContent = view.note || '';
        if (view.kind === 'error') {
            renderAlerts([]);
            setHidden(grid, true);
            setHidden(empty, true);
            setHidden(error, false);
            $('ops-error-text').textContent = view.text || 'جسر بايثون أو قاعدة البيانات ما ردّ.';
            return;
        }
        setHidden(error, true);
        if (view.kind === 'empty' || view.kind === 'window-empty') {
            setHidden(grid, true);
            setHidden(empty, false);
            var title = empty.querySelector('.lq-empty__title');
            var text = empty.querySelector('.lq-empty__text');
            var actions = empty.querySelector('.lq-empty__actions');
            if (title) title.textContent = view.title;
            if (text) {
                text.textContent = view.text;
                setHidden(text, !view.text);
            }
            setHidden(actions, !view.action);
            return;
        }
        setHidden(empty, true);
        setHidden(grid, false);
        $('results-total').textContent = view.total;
        renderDecisions(view.decisions);
        renderProviders(view.providers);
        renderCost(view.cost);
        renderReasons(view.reasons);
    }

    var logBody = $('log-body');
    var logPre = $('log');
    var logEmpty = $('log-empty');
    var logMeta = $('log-meta');

    function renderLog(view) {
        if (view.kind !== 'ok') {
            setHidden(logPre, true);
            clear(logPre);
            logEmpty.textContent = view.text;
            setHidden(logEmpty, false);
            logMeta.textContent = view.meta || '';
            return;
        }
        var nearBottom = logBody.scrollHeight - logBody.scrollTop - logBody.clientHeight < 40;
        var wasHidden = logPre.hasAttribute('hidden');
        clear(logPre);
        view.lines.forEach(function (line, i) {
            var span = make('span', line.error ? 'lq-log__error' : '', line.text + (i < view.lines.length - 1 ? '\n' : ''));
            logPre.appendChild(span);
        });
        setHidden(logEmpty, true);
        setHidden(logPre, false);
        logMeta.textContent = view.meta;
        if (nearBottom || wasHidden) logBody.scrollTop = logBody.scrollHeight;
    }

    var controller = createController({
        initial: readInitial(),
        fetchJson: fetchJson,
        renderServices: renderServices,
        renderChecked: renderChecked,
        renderOptional: renderOptional,
        setChecking: setChecking,
        renderOps: renderOps,
        renderLog: renderLog,
        toast: function (text, variant) {
            if (window.Laqta && window.Laqta.toast) window.Laqta.toast(text, { variant: variant });
        },
        now: function () { return Date.now(); },
        schedule: function (fn, ms) { return setTimeout(fn, ms); },
        isHidden: function () { return document.hidden; }
    });

    if (runButton) {
        runButton.addEventListener('click', function () { controller.runCheck(); });
    }
    var windowGroup = $('window');
    if (windowGroup) {
        windowGroup.addEventListener('lq:change', function (e) {
            controller.setWindow(e.detail && e.detail.value);
        });
    }
    var retry = $('ops-retry');
    if (retry) retry.addEventListener('click', function () { controller.loadOps(true); });

    var tabs = page.querySelectorAll('[data-log-tab]');
    function selectTab(tab) {
        for (var i = 0; i < tabs.length; i++) {
            var on = tabs[i] === tab;
            tabs[i].setAttribute('aria-selected', on ? 'true' : 'false');
            tabs[i].tabIndex = on ? 0 : -1;
        }
        logBody.setAttribute('aria-labelledby', tab.id);
        controller.setLogKind(tab.getAttribute('data-log-tab'));
    }
    for (var t = 0; t < tabs.length; t++) {
        tabs[t].addEventListener('click', function (e) { selectTab(e.currentTarget); });
        tabs[t].addEventListener('keydown', function (e) {
            if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
            var list = Array.prototype.slice.call(tabs);
            var next = list[(list.indexOf(e.currentTarget) + 1) % list.length];
            next.focus();
            selectTab(next);
            e.preventDefault();
        });
    }
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) controller.loadLog();
    });

    controller.start();
})();
