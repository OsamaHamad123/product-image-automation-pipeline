/*
 * لقطة · الصحة والتكلفة (resources/views/dashboard/diagnostics.blade.php).
 *
 * - Service cards start from the saved result of the last connection check (#lq-health-initial). Opening the page
 *   never runs the check: only the «فحص الاتصالات الآن» button POSTs /api/system/run-diagnostics.
 * - «عمليات البحث» comes from GET /api/system/ops-health (ops_health.summarize over automation_queue.trace_json),
 *   split in plain Arabic; decision and failure codes appear only in tooltips.
 * - «السجل» shows the tails of GET /api/view-pipeline-log, /api/view-laravel-log and /api/view-nightly-log (the
 *   newest temp/nightly/nightly_YYYY-MM-DD.log); a missing file is the normal «لسا ما في سجل» state.
 * - «فحص النشر» starts from the saved result of the last publish rehearsal (#publish-check-initial); only its button
 *   POSTs /api/system/publish-check (publish_check.py through the bridge: it may cost one background-removal call).
 *   Step codes appear only in tooltips. When its processing step failed on PhotoRoom / remove.bg credit, key or quota,
 *   «تجاوز عزل الخلفية» (after a confirm) POSTs /api/settings/bg-method {method: 'none'}; while background removal is
 *   off, «رجّع عزل الخلفية (…)» restores the previous method. Neither runs the check again.
 * - «فهرس المتاجر المحلي»: the rows are rendered by the server (LocalIndexController); «حدّث الفهرس هلق» POSTs
 *   /api/system/local-index/refresh (a background job started through the bridge, it answers at once) and, while it
 *   runs, GET /api/system/local-index feeds the status line until the page reloads with the new numbers.
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
    var PUBLISH_CHECK_URL = '/api/system/publish-check';
    var LANES_URL = '/api/system/review-lanes';
    /* «دقة الاقتراحات الحقيقية»: the lanes of catalog_match.decide.pick_lane (HealthController::lanesPayload). */
    var LANES = [['strict', 'عدّى كل قواعد النشر الآلي'], ['unsure', 'الملصق مش واضح بس الاسم مطابق'],
        ['other', 'باقي الاقتراحات']];

    /* «فحص النشر»: the four steps of publish_check.py and the words of each status (HealthController::PUBLISH_STEP_*). */
    var PUBLISH_STEPS = [['download', 'تنزيل الصورة'], ['process', 'عزل الخلفية والمعالجة'],
        ['upload', 'الرفع على Cloudinary'], ['sheet', 'الكتابة بالشيت']];
    var PUBLISH_STATUS = {
        ok: ['نجحت', 'success', 'check'],
        warn: ['فيها ملاحظة', 'warning', 'exclamation'],
        fail: ['ما زبطت', 'danger', 'x'],
        skipped: ['ما انفحصت', 'muted', 'minus'],
        idle: ['لسا ما انفحصت', 'muted', 'minus']
    };
    var PUBLISH_OVERALL = { ok: 'success', warn: 'warning', fail: 'danger' };
    var PUBLISH_DONE = {
        ok: ['خلص فحص النشر: كل الخطوات نجحت.', 'success'],
        warn: ['خلص فحص النشر: النشر لازم يمشي، بس في ملاحظات.', 'warning'],
        fail: ['خلص فحص النشر: في خطوة ما زبطت، شوف شو لازم تعمل.', 'danger']
    };

    /* «تجاوز عزل الخلفية»: PhotoRoom / remove.bg credit, key or quota codes (publish_check.BG_SKIP_CODE_RE,
       HealthController::BG_SKIP_PATTERN) and the method names (SettingsController::BG_METHOD_LABELS). */
    var BG_METHOD_URL = '/api/settings/bg-method';
    var BG_SKIP_RE = /^(photoroom|removebg)_(no_key|401|402|403|429)$/;
    var BG_PROVIDERS = { photoroom: 'PhotoRoom', removebg: 'remove.bg' };
    var BG_METHOD_LABELS = { photoroom: 'PhotoRoom', remove_bg_api: 'remove.bg', grabcut: 'GrabCut', rembg: 'rembg',
        none: 'بدون عزل الخلفية' };

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
        SOCIAL_ONLY: ['المنتج ظاهر فقط بمنشورات تواصل اجتماعي', 'صورها ما بتنزل؛ ارفع صورة يدوياً أو خذها من المنشور'],
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

    // The owner's time zone (layouts/laqta.blade.php body[data-lq-tz], config app.display_timezone, Asia/Dubai): every
    // time on the pages is said in it, whatever the viewer's computer is set to. Without it (node tests), local time.
    var zoneFormats = {};
    function displayZone() {
        var doc = window.document;
        var body = doc && doc.body;
        var tz = body && typeof body.getAttribute === 'function' ? body.getAttribute('data-lq-tz') : '';
        return tz || '';
    }

    // { y, mo (0-11), d, h, mi, wd (0 = Sunday) } of an instant (ms) in the display zone
    function zoneParts(ms) {
        var tz = displayZone();
        if (tz && window.Intl && typeof window.Intl.DateTimeFormat === 'function') {
            try {
                var f = zoneFormats[tz] || (zoneFormats[tz] = new window.Intl.DateTimeFormat('en-US', {
                    timeZone: tz, year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric',
                    weekday: 'short', hourCycle: 'h23' }));
                var p = {};
                f.formatToParts(new Date(ms)).forEach(function (x) { p[x.type] = x.value; });
                var wd = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(p.weekday);
                return { y: +p.year, mo: +p.month - 1, d: +p.day, h: (+p.hour) % 24, mi: +p.minute, wd: wd };
            } catch (e) {
                // an unknown zone: local time
            }
        }
        var dt = new Date(ms);
        return { y: dt.getFullYear(), mo: dt.getMonth(), d: dt.getDate(), h: dt.getHours(), mi: dt.getMinutes(), wd: dt.getDay() };
    }

    /* "اليوم 09:12" / "مبارح 18:40" / "28 أيلول 09:12" in the owner's time zone (displayZone). */
    function whenText(ms, nowMs) {
        var d = zoneParts(ms);
        var now = zoneParts(nowMs);
        var yesterday = zoneParts(nowMs - (now.h * 60 + now.mi) * 60000 - 12 * 3600000);
        var same = function (a, b) { return a.y === b.y && a.mo === b.mo && a.d === b.d; };
        var clock = pad(d.h) + ':' + pad(d.mi);
        if (same(d, now)) return 'اليوم ' + clock;
        if (same(d, yesterday)) return 'مبارح ' + clock;
        var text = d.d + ' ' + MONTHS[d.mo];
        if (d.y !== now.y) text += ' ' + d.y;
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
                    + 'حسب الاستهلاك بأسعار تبويب «نماذج التحقق».');
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

    /* «1.2 ث» from milliseconds (whole tenths, as HealthController::stepSeconds). */
    function seconds(ms) {
        var tenths = Math.round(Math.max(0, num(ms) || 0) / 100);
        return Math.floor(tenths / 10) + '.' + (tenths % 10) + ' ث';
    }

    /* The «فحص النشر» card for a saved or fresh result (HealthController::publishCheckView says the same). */
    function publishView(result, nowMs) {
        var byKey = {};
        var valid = isObject(result) && Array.isArray(result.steps);
        if (valid) {
            result.steps.forEach(function (s) {
                if (isObject(s) && s.key) byKey[s.key] = s;
            });
        }
        var steps = PUBLISH_STEPS.map(function (pair) {
            var step = byKey[pair[0]] || null;
            var status = step && PUBLISH_STATUS[step.status] ? step.status : 'idle';
            var meta = PUBLISH_STATUS[status];
            var ran = status === 'ok' || status === 'warn' || status === 'fail';
            return {
                key: pair[0], status: status, label: meta[0], tone: meta[1], icon: meta[2],
                title: step && step.title_ar ? String(step.title_ar) : pair[1],
                time: ran ? seconds(step.ms) : '',
                detail: step ? String(step.detail_ar || '') : '',
                action: ran && status !== 'ok' ? String(step.action_ar || '') : '',
                code: step ? String(step.code || '') : ''
            };
        });
        if (!valid) {
            return { state: 'never', tone: 'muted', summary: 'لسا ما انعمل فحص للنشر.', sample: '', notes: [],
                steps: steps, when: '' };
        }
        var overall = PUBLISH_OVERALL[result.overall] ? result.overall : 'fail';
        var sample = isObject(result.sample) ? result.sample : {};
        var name = String(sample.product_name || '').trim();
        var row = sample.row_number;
        var finished = typeof result.finished_at === 'string' ? Date.parse(result.finished_at) : NaN;
        return {
            state: overall,
            tone: PUBLISH_OVERALL[overall],
            summary: String(result.summary_ar || ''),
            sample: sample.kind === 'review' && name
                ? 'الصورة: ' + name + (typeof row === 'number' && isFinite(row) ? ' (صف ' + row + ')' : '')
                : 'الصورة: الصورة التجريبية',
            notes: (Array.isArray(result.notes) ? result.notes : []).map(String),
            steps: steps,
            when: isFinite(finished) ? whenText(finished, nowMs) : ''
        };
    }

    /* «رصيد PhotoRoom خلص أو الاشتراك موقوف» for a code the skip helps with, else '' (HealthController::bgProblem). */
    function bgProblem(code) {
        var m = BG_SKIP_RE.exec(String(code || ''));
        if (!m) return '';
        var name = BG_PROVIDERS[m[1]];
        if (m[2] === '402') return 'رصيد ' + name + ' خلص أو الاشتراك موقوف';
        if (m[2] === '429') return name + ' رافض طلبات كتير هلق';
        if (m[2] === 'no_key') return 'مفتاح ' + name + ' مش محفوظ';
        return name + ' رفض المفتاح';
    }

    /* The «تجاوز عزل الخلفية» box of the card (HealthController::bgSkipView says the same): offer after a processing
       step that failed on credit / key / quota, off while the method is «بدون عزل الخلفية», hidden otherwise or when
       the method is unknown (bg null: the database did not answer, so nothing could be saved). */
    function bgView(result, bg) {
        var hidden = { state: 'hidden', text: '', restore: '', restore_label: '' };
        if (!isObject(bg) || typeof bg.method !== 'string' || !bg.method) return hidden;
        if (bg.method === 'none') {
            var previous = BG_METHOD_LABELS[bg.previous] && bg.previous !== 'none' ? bg.previous : 'photoroom';
            return { state: 'off',
                text: 'عزل الخلفية متوقف: الصور اللي بتعتمدها بتنتشر متل ما هي على لوحة بيضا، بدون أي طلب عزل مدفوع.',
                restore: previous, restore_label: 'رجّع عزل الخلفية (' + BG_METHOD_LABELS[previous] + ')' };
        }
        var steps = isObject(result) && Array.isArray(result.steps) ? result.steps : [];
        for (var i = 0; i < steps.length; i++) {
            var s = steps[i];
            if (isObject(s) && s.key === 'process' && s.status === 'fail') {
                var problem = bgProblem(s.code);
                if (problem) {
                    return { state: 'offer', text: problem + '، فكل اعتماد رح يفشل بنفس الشكل لحد ما ينحل. فيك تتجاوز '
                        + 'عزل الخلفية هلق: الصور بتنتشر متل ما هي على لوحة بيضا.', restore: '', restore_label: '' };
                }
            }
        }
        return hidden;
    }

    /* While the rehearsal runs: every step waits (the server answers once, at the end). */
    function publishRunningView() {
        return {
            state: 'running', tone: 'muted', sample: '', notes: [], when: '',
            summary: 'جاري الفحص… الخطوات بتمشي ورا بعض، وعادةً بيخلص بأقل من دقيقة.',
            steps: PUBLISH_STEPS.map(function (pair) {
                return { key: pair[0], status: 'running', label: 'جاري الفحص…', tone: 'muted', icon: 'spinner',
                    title: pair[1], time: '', detail: '', action: '', code: '' };
            })
        };
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
                    deps.renderOps({ kind: 'error', text: requestError(res, 'ما قدرنا نوصل لبيانات النظام. جرّب بعد شوي، وإذا ضل بلّغ المطوّر.') });
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

    /* «فحص النشر»: only run() (the button) POSTs the check; start() shows the saved result. Deps: fetchJson,
       renderPublish, setPublishBusy, toast, now; for «تجاوز عزل الخلفية»: bg ({method, previous} or null), renderBg,
       setBgBusy, confirm and confirmText. skipBg() asks first, then POSTs {method: 'none'}; restoreBg() POSTs the
       previous method. Neither re-runs the check: the toast says to run it again. */
    function createPublishCheck(deps) {
        var state = { last: deps.initial || null, running: false, bg: isObject(deps.bg) ? deps.bg : null, saving: false };

        function now() {
            return deps.now ? deps.now() : Date.now();
        }

        function show() {
            deps.renderPublish(publishView(state.last, now()));
            if (deps.renderBg) deps.renderBg(bgView(state.last, state.bg));
        }

        function saveBg(method) {
            if (state.saving || !state.bg) return Promise.resolve(false);
            state.saving = true;
            if (deps.setBgBusy) deps.setBgBusy(true);
            return deps.fetchJson(BG_METHOD_URL, { method: 'POST', body: { method: method } }).then(function (res) {
                var data = res && res.ok && isObject(res.data) && res.data.status === 'success' ? res.data : null;
                if (data) {
                    state.bg = { method: String(data.method || method), previous: String(data.previous || '') };
                    show();
                    deps.toast(String(data.message || 'انحفظ.'), 'success');
                    return true;
                }
                deps.toast('ما انحفظ: ' + requestError(res, 'الخادم ما ردّ.'), 'danger');
                return false;
            }, function () {
                deps.toast('ما قدرنا نوصل للخادم لنحفظ.', 'danger');
                return false;
            }).then(function (done) {
                state.saving = false;
                if (deps.setBgBusy) deps.setBgBusy(false);
                return done;
            });
        }

        function skipBg() {
            if (bgView(state.last, state.bg).state !== 'offer') return Promise.resolve(false);
            // deps.confirm: true / false، أو Promise (سؤال الصفحة Laqta.ask)
            return Promise.resolve(deps.confirm ? deps.confirm(deps.confirmText || 'تجاوز عزل الخلفية؟') : true).then(function (ok) {
                return ok ? saveBg('none') : false;
            });
        }

        function restoreBg() {
            var view = bgView(state.last, state.bg);
            return view.state === 'off' ? saveBg(view.restore) : Promise.resolve(false);
        }

        function run() {
            if (state.running) return Promise.resolve(false);
            state.running = true;
            deps.setPublishBusy(true);
            deps.renderPublish(publishRunningView());
            return deps.fetchJson(PUBLISH_CHECK_URL, { method: 'POST', body: {} }).then(function (res) {
                var result = res && res.ok && isObject(res.data) && isObject(res.data.result) ? res.data.result : null;
                if (result && Array.isArray(result.steps)) {
                    state.last = result;
                    show();
                    var done = PUBLISH_DONE[result.overall] || PUBLISH_DONE.fail;
                    deps.toast(done[0], done[1]);
                    return true;
                }
                show();
                deps.toast('ما خلص فحص النشر: ' + requestError(res, 'الخادم ما رجّع نتيجة.'), 'danger');
                return false;
            }, function () {
                show();
                deps.toast('ما قدرنا نوصل للخادم لنفحص النشر.', 'danger');
                return false;
            }).then(function (done) {
                state.running = false;
                deps.setPublishBusy(false);
                return done;
            });
        }

        return { start: show, run: run, skipBg: skipBg, restoreBg: restoreBg, state: state };
    }

    /* One row per lane: «اعتمدت X من Y» and the guaranteed lower bound. null payload: the bridge did not answer. */
    function lanesView(payload) {
        if (!isObject(payload) || payload.status !== 'success' || !isObject(payload.lanes)) {
            return { kind: 'error', text: 'ما قدرنا نحسب دقة الاقتراحات هلق.', rows: [] };
        }
        var rows = LANES.map(function (pair) {
            var lane = isObject(payload.lanes[pair[0]]) ? payload.lanes[pair[0]] : {};
            var n = count(lane.prechecked);
            var bound = num(lane.lower_bound);
            return {
                key: pair[0],
                label: pair[1],
                text: n ? 'اعتمدت ' + count(lane.accepted) + ' من ' + n : 'لسا ما في مراجعات',
                bound: n && bound !== null ? 'أقل دقة متوقعة ' + (100 * bound).toFixed(1) + '%' : '',
                ready: lane.ready === true
            };
        });
        return { kind: 'ok', text: '', rows: rows };
    }

    // ------------------------------------------------------------------
    // «فهرس المتاجر المحلي» (LocalIndexController): the refresh button and its progress line. Its own URLs, view
    // function and controller; the rows are rendered by the server and read again on reload.
    // ------------------------------------------------------------------

    var LOCAL_INDEX_URL = '/api/system/local-index';
    var LOCAL_INDEX_REFRESH_URL = '/api/system/local-index/refresh';
    var LOCAL_INDEX_POLL_MS = 4000;

    /* The button and the status line from LocalIndexController::card (GET /api/system/local-index -> card). */
    function localIndexView(card) {
        var refresh = isObject(card) && isObject(card.refresh) ? card.refresh : {};
        var running = refresh.running === true;
        return { running: running, text: String(refresh.text || ''), disabled: running,
            label: running ? 'عم يحدّث…' : 'حدّث الفهرس هلق' };
    }

    /* run() (the button) POSTs the refresh job and returns at once; the page then asks GET /api/system/local-index every
       few seconds while it runs, and reloads once it is over so the rows show the new numbers. Deps: fetchJson, render,
       toast, schedule, reload; running: the page was opened while a refresh was running. */
    function createLocalIndex(deps) {
        var state = { running: deps.running === true, busy: false, timer: null };

        function show(view) {
            state.running = view.running;
            deps.render(view);
        }

        function watch() {
            if (state.timer === null && state.running) state.timer = deps.schedule(poll, LOCAL_INDEX_POLL_MS);
        }

        function poll() {
            state.timer = null;
            return deps.fetchJson(LOCAL_INDEX_URL, { method: 'GET' }).then(function (res) {
                var card = res && res.ok && isObject(res.data) && isObject(res.data.card) ? res.data.card : null;
                if (card) {
                    var was = state.running;
                    var view = localIndexView(card);
                    show(view);
                    if (was && !view.running) {
                        deps.reload();
                        return;
                    }
                }
                watch();
            }, function () {
                watch();
            });
        }

        function run() {
            if (state.busy || state.running) return Promise.resolve(false);
            state.busy = true;
            show({ running: true, text: 'عم يبلّش تحديث الفهرس…', label: 'عم يحدّث…', disabled: true });
            return deps.fetchJson(LOCAL_INDEX_REFRESH_URL, { method: 'POST', body: {} }).then(function (res) {
                var data = res && res.ok && isObject(res.data) && res.data.status === 'success' ? res.data : null;
                if (data && data.running === true) {
                    deps.toast(String(data.message || 'بلّش تحديث الفهرس بالخلفية.'), 'success');
                    watch();
                    return true;
                }
                show(localIndexView(null));
                if (data) deps.toast(String(data.message || 'ما بلّش التحديث.'), 'warning');
                else deps.toast('ما بلّش التحديث: ' + requestError(res, 'الخادم ما ردّ.'), 'danger');
                return false;
            }, function () {
                show(localIndexView(null));
                deps.toast('ما قدرنا نوصل للخادم لنبدأ التحديث.', 'danger');
                return false;
            }).then(function (started) {
                state.busy = false;
                return started;
            });
        }

        return { run: run, poll: poll, start: watch, state: state };
    }

    // ------------------------------------------------------------------
    // متقدم: «صدّر مجموعة اختبار» (HealthController::exportEvalSet): one POST, then where the file is and its link
    // ------------------------------------------------------------------

    var EVAL_EXPORT_URL = '/api/system/eval-export';
    var EVAL_EXPORT_LABEL = 'صدّر مجموعة اختبار';

    /* The status line, the button and the download link from the endpoint's answer (null: no answer yet). */
    function evalExportView(res) {
        var data = res && isObject(res.data) ? res.data : null;
        if (data && res.ok && data.status === 'success') {
            var link = data.products > 0 && typeof data.download === 'string' ? data.download : '';
            return { text: String(data.message || ''), link: link, label: EVAL_EXPORT_LABEL, disabled: false,
                tone: link ? 'success' : 'warning' };
        }
        var why = data && typeof data.error === 'string' && data.error ? data.error
            : 'ما قدرنا نجهّز مجموعة الاختبار: ' + requestError(res, 'الخادم ما ردّ.');
        return { text: why, link: '', label: EVAL_EXPORT_LABEL, disabled: false, tone: 'danger' };
    }

    /* run() (the button) POSTs once (a second click while it works does nothing) and renders the answer. Deps:
       fetchJson, render, toast. */
    function createEvalExport(deps) {
        var state = { busy: false };

        function run() {
            if (state.busy) return Promise.resolve(null);
            state.busy = true;
            deps.render({ text: 'عم نجهّز الملف… ممكن ياخد دقيقة.', link: '', label: 'عم يجهّز…', disabled: true,
                tone: 'muted' });
            return deps.fetchJson(EVAL_EXPORT_URL, { method: 'POST', body: {} }).then(function (res) {
                return evalExportView(res);
            }, function () {
                return evalExportView(null);
            }).then(function (view) {
                state.busy = false;
                deps.render(view);
                deps.toast(view.tone === 'success' ? 'مجموعة الاختبار جاهزة.' : view.text,
                    view.tone === 'success' ? 'success' : (view.tone === 'warning' ? 'warning' : 'danger'));
                return view;
            });
        }

        return { run: run, state: state };
    }

    // ------------------------------------------------------------------
    // متقدم: «صور قديمة بخلفية بيضا» (RecutController): «احسب» POSTs the dry run (it changes nothing), «ابدأ» POSTs a
    // capped batch that runs in the background, and GET /api/system/reprocess follows it every few seconds while it runs
    // ------------------------------------------------------------------

    var REPROCESS_PLAN_URL = '/api/system/reprocess/plan';
    var REPROCESS_START_URL = '/api/system/reprocess/start';
    var REPROCESS_STATUS_URL = '/api/system/reprocess';
    var REPROCESS_POLL_MS = 5000;
    var REPROCESS_DEFAULT_MAX = 20;
    var REPROCESS_ENDED = {
        done: 'آخر دفعة خلصت',
        max: 'آخر دفعة وقفت عند عدد الصور اللي حددته',
        budget: 'آخر دفعة وقفت عند أقصى تكلفة حددتها',
        stopped: 'آخر دفعة وقفت بخطأ',
        stale: 'آخر دفعة انقطعت بالنص'
    };
    var REPROCESS_ERRORS = [
        [/^photoroom_402$/, 'رصيد PhotoRoom خلص أو الاشتراك موقوف'],
        [/^photoroom_(401|403|no_key)$/, 'مفتاح PhotoRoom مش شغّال'],
        [/^photoroom_429$/, 'PhotoRoom رافض طلبات كتير هلق'],
        [/timeout|connection_error|_5\d\d$/, 'الخدمة ما ردّت (انقطاع أو بطء)'],
        [/^upload_/, 'الرفع على Cloudinary ما زبط'],
        [/^(download_|source_|candidate_)/, 'ما قدرنا ننزّل الصورة المنشورة'],
        [/^outbox_unreadable$/, 'ما قدرنا نقرا طابور الكتابة بالشيت']
    ];

    function pictures(n) {
        return plural(count(n), 'صورة', 'صورتين', 'صور', 'صورة وحدة');
    }

    function reprocessError(code) {
        code = String(code || '');
        for (var i = 0; i < REPROCESS_ERRORS.length; i++) {
            if (REPROCESS_ERRORS[i][0].test(code)) return REPROCESS_ERRORS[i][1];
        }
        return 'صار خطأ (التفاصيل بسجل الأتمتة)';
    }

    /* The status line of the last batch (RecutController::batchState), '' when there was none. */
    function reprocessBatchText(batch) {
        if (!isObject(batch) || !batch.state || batch.state === 'none') return '';
        var done = count(batch.done);
        var money = usd(batch.spent_usd);
        if (batch.running && batch.state === 'starting') return 'عم تبلّش الدفعة…';
        if (batch.running) {
            return 'عم نعيد القص: خلص ' + done + ' من ' + count(batch.planned > 0 ? Math.min(batch.planned, batch.max || batch.planned) : batch.max)
                + '، والتكلفة لهلق ' + money + '.' + (batch.current ? ' هلق: «' + batch.current + '».' : '');
        }
        var text = (REPROCESS_ENDED[batch.state] || 'آخر دفعة خلصت') + ': انعادت ' + pictures(done) + ' شفافة، والتكلفة ' + money + '.';
        if (count(batch.needs_look) > 0) {
            text += ' ' + pictures(batch.needs_look) + ' طلع قصها بملاحظات وبدها عينك بـ«فحص القص».';
        }
        if (batch.state === 'stopped') {
            text += ' وقفت لأنو ' + reprocessError(batch.last_error) + (batch.last_item ? ' (عند «' + batch.last_item + '»)' : '')
                + '. اضغط «ابدأ» مرة تانية بعد ما ينحل لتكمّل من وين وقفت.';
        } else if (batch.state === 'stale') {
            text += ' اضغط «ابدأ» لتكمّل من وين وقفت.';
        }
        return text;
    }

    /* «احسب»'s answer: {text, tone, form (show «ابدأ»), max (the suggested batch size)}. */
    function reprocessPlanView(res) {
        var data = res && isObject(res.data) ? res.data : null;
        if (!data || !res.ok || data.status !== 'success' || !isObject(data.plan)) {
            return { text: requestError(res, 'ما قدرنا نعدّ الصور: الخادم ما ردّ.'), tone: 'danger', form: false, max: 0 };
        }
        var plan = data.plan;
        if (data.bg_off) {
            return { text: 'عزل الخلفية متوقف بالإعدادات، فما منقدر نعيد قص الصور هلق. رجّعه أول من تبويب «معالجة الصور».',
                tone: 'warning', form: false, max: 0 };
        }
        if (data.white_output) {
            return { text: 'الإعدادات بتنشر الصور على خلفية بيضا، فالصور البيضا مش غلط وما في شي نعيده.',
                tone: 'muted', form: false, max: 0 };
        }
        var todo = count(plan.todo);
        var parts = [];
        if (todo === 0) {
            parts.push('ما في صور بيضا لازم تنعاد: كل الصور اللي بالشيت شفافة.');
        } else {
            parts.push('في ' + pictures(todo) + ' بخلفية بيضا لازم تنعاد.');
            if (num(plan.price) > 0) {
                parts.push('التكلفة التقريبية ' + usd(plan.usd) + ' (' + plan.calls + ' طلب عزل × ' + usdPrecise(plan.price)
                    + ')، ولو بعض الصور احتاجت إعادة ممكن توصل لـ ' + usd(plan.worst_usd) + '.');
            } else {
                parts.push('ما في تكلفة: القص بطريقة محلية مجانية.');
            }
        }
        if (count(plan.transparent) > 0) parts.push(pictures(plan.transparent) + ' شفافة أصلاً.');
        if (count(plan.not_in_sheet) > 0) parts.push(pictures(plan.not_in_sheet) + ' ما عادت بالشيت أو غيّرتها بإيدك، فما رح نلمسها.');
        if (count(plan.skipped_before) > 0) parts.push(pictures(plan.skipped_before) + ' وقفت عندها دفعة قبل وبدها عينك بـ«فحص القص».');
        if (count(plan.probe_failed) > 0) parts.push('ما قدرنا نفحص ' + pictures(plan.probe_failed) + ' هلق.');
        return { text: parts.join(' '), tone: todo > 0 ? 'warning' : 'success', form: todo > 0,
            max: Math.max(1, Math.min(todo, REPROCESS_DEFAULT_MAX, count(data.batch_max) || REPROCESS_DEFAULT_MAX)) };
    }

    /* plan() and start(max, usd) POST once each (a second click while one works does nothing); while a batch runs the
       status line follows GET /api/system/reprocess. Deps: fetchJson, render({text, busy, form, max}), toast, confirm,
       schedule. */
    function createReprocess(deps) {
        var state = { busy: false, running: false, timer: null, planText: '' };

        function show(view) {
            deps.render(view);
        }

        function watch() {
            if (state.timer === null && state.running) state.timer = deps.schedule(poll, REPROCESS_POLL_MS);
        }

        function follow(batch) {
            state.running = isObject(batch) && batch.running === true;
            var line = reprocessBatchText(batch);
            show({ text: [state.planText, line].filter(Boolean).join(' '), busy: state.running, form: !state.running && state.form });
            watch();
        }

        function poll() {
            state.timer = null;
            return deps.fetchJson(REPROCESS_STATUS_URL, { method: 'GET' }).then(function (res) {
                if (res && res.ok && isObject(res.data)) follow(res.data.batch);
                else watch();
            }, function () {
                watch();
            });
        }

        function plan() {
            if (state.busy || state.running) return Promise.resolve(null);
            state.busy = true;
            show({ text: 'عم نعدّ الصور… ممكن ياخد دقيقة.', busy: true, form: false });
            return deps.fetchJson(REPROCESS_PLAN_URL, { method: 'POST', body: {} }).then(function (res) {
                return [reprocessPlanView(res), res && res.data];
            }, function () {
                return [reprocessPlanView(null), null];
            }).then(function (pair) {
                var view = pair[0];
                state.busy = false;
                state.form = view.form;
                state.planText = view.text;
                show({ text: view.text, busy: false, form: view.form, max: view.max, tone: view.tone });
                if (isObject(pair[1]) && isObject(pair[1].batch) && pair[1].batch.running) follow(pair[1].batch);
                return view;
            });
        }

        function start(max, money) {
            if (state.busy || state.running) return Promise.resolve(false);
            var n = count(max);
            var cap = num(money);
            if (n < 1 || cap === null || cap <= 0) {
                deps.toast('اكتب كم صورة وأقصى تكلفة أكبر من صفر.', 'warning');
                return Promise.resolve(false);
            }
            return Promise.resolve(deps.confirm('رح نعيد قص لحد ' + pictures(n) + ' شفافة، وما منتعدّى ' + usd(cap)
                + '. الصورة الجديدة بتنكتب بالشيت بس إذا لسا الرابط القديم بخليتها. متأكد؟')).then(function (ok) {
                if (!ok) return false;
                state.busy = true;
                show({ text: 'عم تبلّش الدفعة…', busy: true, form: false });
                return deps.fetchJson(REPROCESS_START_URL, { method: 'POST', body: { max: n, max_usd: cap } }).then(function (res) {
                    var data = res && res.ok && isObject(res.data) && res.data.status === 'success' ? res.data : null;
                    state.busy = false;
                    if (data && data.started) {
                        deps.toast(String(data.message || 'بلّشت الدفعة بالخلفية.'), 'success');
                        follow(isObject(data.batch) ? data.batch : { state: 'starting', running: true });
                        return true;
                    }
                    var text = data ? String(data.message || 'ما بلّشت الدفعة.') : requestError(res, 'ما بلّشت الدفعة: الخادم ما ردّ.');
                    show({ text: text, busy: false, form: state.form });
                    deps.toast(text, data ? 'warning' : 'danger');
                    return false;
                }, function () {
                    state.busy = false;
                    show({ text: 'ما قدرنا نوصل للخادم لنبدأ الدفعة.', busy: false, form: state.form });
                    return false;
                });
            });
        }

        return { plan: plan, start: start, poll: poll, state: state };
    }

    var api = {
        lanesView: lanesView, LANES: LANES,
        publishView: publishView, publishRunningView: publishRunningView, createPublishCheck: createPublishCheck,
        bgView: bgView, bgProblem: bgProblem, BG_SKIP_RE: BG_SKIP_RE, BG_METHOD_LABELS: BG_METHOD_LABELS,
        seconds: seconds, PUBLISH_STEPS: PUBLISH_STEPS, PUBLISH_STATUS: PUBLISH_STATUS,
        serviceView: serviceView, servicesView: servicesView, checkingView: checkingView, optionalView: optionalView,
        checkedView: checkedView, opsView: opsView, logView: logView, whenText: whenText, ageText: ageText,
        usd: usd, usdPrecise: usdPrecise, requestError: requestError, createController: createController,
        SERVICE_KEYS: SERVICE_KEYS, RESULT_BUCKETS: RESULT_BUCKETS, REASONS: REASONS
    };
    if (typeof window !== 'undefined') window.LaqtaHealth = api;
    api.localIndexView = localIndexView;
    api.createLocalIndex = createLocalIndex;
    api.evalExportView = evalExportView;
    api.createEvalExport = createEvalExport;
    api.reprocessPlanView = reprocessPlanView;
    api.reprocessBatchText = reprocessBatchText;
    api.createReprocess = createReprocess;

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
            $('ops-error-text').textContent = view.text || 'ما قدرنا نوصل لبيانات النظام. جرّب بعد شوي، وإذا ضل بلّغ المطوّر.';
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

    // ------------------------------------------------------------------
    // «فحص النشر»
    // ------------------------------------------------------------------

    var publishCard = $('publish');
    var publishButton = $('publish-run');
    var publishLabel = $('publish-run-label');
    /* The same paths as resources/views/components/lq/icon.blade.php */
    var ICON_PATHS = { check: 'M5 12.5 10 17.5 19 7', x: 'M6 6l12 12M18 6 6 18', exclamation: 'M12 7v6M12 16.5v.5',
        minus: 'M6 12h12' };

    function svgIcon(name) {
        var ns = 'http://www.w3.org/2000/svg';
        var svg = document.createElementNS(ns, 'svg');
        var attrs = { 'class': 'lq-icon', width: '14', height: '14', viewBox: '0 0 24 24', fill: 'none',
            stroke: 'currentColor', 'stroke-width': '2.4', 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
            focusable: 'false', 'aria-hidden': 'true' };
        Object.keys(attrs).forEach(function (k) { svg.setAttribute(k, attrs[k]); });
        var path = document.createElementNS(ns, 'path');
        path.setAttribute('d', ICON_PATHS[name] || ICON_PATHS.minus);
        svg.appendChild(path);
        return svg;
    }

    function readPublishInitial() {
        var holder = document.getElementById('publish-check-initial');
        if (!holder) return null;
        try {
            var data = JSON.parse(holder.textContent || 'null');
            return isObject(data) && Array.isArray(data.steps) ? data : null;
        } catch (e) {
            return null;
        }
    }

    function renderPublish(view) {
        if (!publishCard) return;
        publishCard.setAttribute('data-tone', view.tone);
        var dot = $('publish-dot');
        if (dot) dot.className = 'lq-dot lq-dot--lg ' + (DOTS[view.tone] || DOTS.muted);
        $('publish-summary').textContent = view.summary;
        var meta = $('publish-meta');
        clear(meta);
        if (view.when) {
            meta.appendChild(document.createTextNode('آخر فحص للنشر: '));
            meta.appendChild(make('time', '', view.when));
            if (view.sample) meta.appendChild(document.createTextNode(' · ' + view.sample));
        }
        $('publish-notes').textContent = view.notes.join(' ');
        view.steps.forEach(function (step) {
            var li = publishCard.querySelector('[data-step="' + step.key + '"]');
            if (!li) return;
            li.setAttribute('data-status', step.status);
            if (step.code) li.title = step.code;
            else li.removeAttribute('title');
            var icon = li.querySelector('[data-step-icon]');
            if (icon) {
                icon.className = 'lq-health-step__icon lq-health-step__icon--' + step.tone;
                icon.setAttribute('aria-label', step.label);
                clear(icon);
                if (step.icon === 'spinner') {
                    var spin = make('span', 'lq-spinner');
                    spin.setAttribute('aria-hidden', 'true');
                    icon.appendChild(spin);
                } else {
                    icon.appendChild(svgIcon(step.icon));
                }
            }
            li.querySelector('[data-step-title]').textContent = step.title;
            li.querySelector('[data-step-state]').textContent = step.label;
            li.querySelector('[data-step-time]').textContent = step.time;
            var detail = li.querySelector('[data-step-detail]');
            detail.textContent = step.detail;
            setHidden(detail, !step.detail);
            var action = li.querySelector('[data-step-action]');
            action.textContent = step.action;
            setHidden(action, !step.action);
        });
    }

    function setPublishBusy(busy) {
        if (!publishButton) return;
        publishButton.disabled = busy;
        if (busy) publishButton.setAttribute('aria-busy', 'true');
        else publishButton.removeAttribute('aria-busy');
        if (publishLabel) publishLabel.textContent = busy ? 'جاري الفحص…' : 'افحص النشر';
    }

    // «تجاوز عزل الخلفية» / «رجّع عزل الخلفية (…)»: the method as the page was rendered (data-method, '' without the
    // database), then what the endpoint answered
    var bgBox = $('bg-box');
    var bgSkip = $('bg-skip');
    var bgRestore = $('bg-restore');

    function readBg() {
        var method = bgBox ? String(bgBox.getAttribute('data-method') || '') : '';
        return method ? { method: method, previous: String(bgBox.getAttribute('data-previous') || '') } : null;
    }

    function renderBg(view) {
        if (!bgBox) return;
        bgBox.setAttribute('data-state', view.state);
        setHidden(bgBox, view.state === 'hidden');
        $('bg-text').textContent = view.text;
        setHidden(bgSkip, view.state !== 'offer');
        setHidden(bgRestore, view.state !== 'off');
        if (bgRestore) bgRestore.setAttribute('data-method', view.restore);
        $('bg-restore-label').textContent = view.restore_label;
    }

    function setBgBusy(busy) {
        [bgSkip, bgRestore].forEach(function (button) {
            if (!button) return;
            button.disabled = busy;
            if (busy) button.setAttribute('aria-busy', 'true');
            else button.removeAttribute('aria-busy');
        });
    }

    // «دقة الاقتراحات الحقيقية»: one read when the page opens (cached on the server like ops-health)
    var lanesBox = $('lanes');

    function renderLanes(view) {
        if (!lanesBox) return;
        clear(lanesBox);
        lanesBox.removeAttribute('aria-busy');
        if (view.kind !== 'ok') {
            lanesBox.appendChild(make('p', 'lq-health__footnote', view.text));
            return;
        }
        view.rows.forEach(function (row) {
            var line = make('div', 'lq-health-lane');
            line.setAttribute('data-lane', row.key);
            line.appendChild(make('span', 'lq-health-lane__label', row.label));
            line.appendChild(make('strong', '', row.text));
            if (row.bound) line.appendChild(make('span', 'lq-health-lane__bound', row.bound));
            lanesBox.appendChild(line);
        });
    }

    if (lanesBox) {
        fetchJson(LANES_URL, { method: 'GET' }).then(function (res) {
            renderLanes(lanesView(res && res.ok ? res.data : null));
        }, function () {
            renderLanes(lanesView(null));
        });
    }

    if (publishCard) {
        var publish = createPublishCheck({
            initial: readPublishInitial(),
            bg: readBg(),
            fetchJson: fetchJson,
            renderPublish: renderPublish,
            renderBg: renderBg,
            setPublishBusy: setPublishBusy,
            setBgBusy: setBgBusy,
            confirm: function (text) {
                return window.Laqta && window.Laqta.ask ? window.Laqta.ask({ title: 'تجاوز عزل الخلفية؟', text: text, confirmText: 'تجاوز', danger: true })
                    : window.confirm(text);
            },
            confirmText: bgSkip ? bgSkip.getAttribute('data-confirm') : '',
            toast: function (text, variant) {
                if (window.Laqta && window.Laqta.toast) window.Laqta.toast(text, { variant: variant });
            },
            now: function () { return Date.now(); }
        });
        publish.start();
        if (bgSkip) bgSkip.addEventListener('click', function () { publish.skipBg(); });
        if (bgRestore) bgRestore.addEventListener('click', function () { publish.restoreBg(); });
        if (publishButton) {
            publishButton.addEventListener('click', function () { publish.run(); });
            // from the review screen's failed approvals (/system-diagnostics#publish-check): the button is ready,
            // never pressed for the owner (the check may cost a background-removal call)
            if (window.location && window.location.hash === '#publish-check') publishButton.focus();
        }
    }

    // «فهرس المتاجر المحلي»: «حدّث الفهرس هلق» starts the background refresh; while one runs the status line follows it
    var indexButton = $('index-refresh');
    var indexStatus = $('index-status');
    var indexLabel = $('index-refresh-label');

    function renderLocalIndex(view) {
        if (indexStatus) indexStatus.textContent = view.text;
        if (indexLabel) indexLabel.textContent = view.label;
        if (!indexButton) return;
        indexButton.disabled = view.disabled;
        if (view.disabled) indexButton.setAttribute('aria-busy', 'true');
        else indexButton.removeAttribute('aria-busy');
    }

    if (indexButton) {
        var localIndex = createLocalIndex({
            running: indexButton.disabled === true,
            fetchJson: fetchJson,
            render: renderLocalIndex,
            toast: function (text, variant) {
                if (window.Laqta && window.Laqta.toast) window.Laqta.toast(text, { variant: variant });
            },
            schedule: function (fn, ms) { return window.setTimeout(fn, ms); },
            reload: function () { window.location.reload(); }
        });
        indexButton.addEventListener('click', function () { localIndex.run(); });
        localIndex.start();
    }

    // متقدم: «صدّر مجموعة اختبار» writes the set and says where the file is, with its download link
    var evalButton = $('eval-export');
    var evalStatus = $('eval-export-status');
    var evalLabel = $('eval-export-label');
    var evalLink = $('eval-export-link');

    function renderEvalExport(view) {
        if (evalStatus) evalStatus.textContent = view.text;
        if (evalLabel) evalLabel.textContent = view.label;
        if (evalLink) {
            if (view.link) evalLink.setAttribute('href', view.link);
            setHidden(evalLink, !view.link);
        }
        if (!evalButton) return;
        evalButton.disabled = view.disabled;
        if (view.disabled) evalButton.setAttribute('aria-busy', 'true');
        else evalButton.removeAttribute('aria-busy');
    }

    if (evalButton) {
        var evalExport = createEvalExport({
            fetchJson: fetchJson,
            render: renderEvalExport,
            toast: function (text, variant) {
                if (window.Laqta && window.Laqta.toast) window.Laqta.toast(text, { variant: variant });
            }
        });
        evalButton.addEventListener('click', function () { evalExport.run(); });
    }

    // متقدم: «صور قديمة بخلفية بيضا»: «احسب» counts and prices, «ابدأ» starts a capped batch, the line follows it
    var reprocessButton = $('reprocess-plan');
    var reprocessStatus = $('reprocess-status');
    var reprocessLabel = $('reprocess-plan-label');
    var reprocessForm = $('reprocess-form');
    var reprocessMax = $('reprocess-max');
    var reprocessUsd = $('reprocess-usd');
    var reprocessStart = $('reprocess-start');

    function renderReprocess(view) {
        if (reprocessStatus) reprocessStatus.textContent = view.text || '';
        if (reprocessLabel) reprocessLabel.textContent = view.busy ? 'عم يشتغل…' : 'احسب';
        if (reprocessButton) {
            reprocessButton.disabled = !!view.busy;
            if (view.busy) reprocessButton.setAttribute('aria-busy', 'true');
            else reprocessButton.removeAttribute('aria-busy');
        }
        if (reprocessStart) reprocessStart.disabled = !!view.busy;
        setHidden(reprocessForm, !view.form);
        if (reprocessMax && view.max) reprocessMax.value = String(view.max);
    }

    if (reprocessButton) {
        var reprocess = createReprocess({
            fetchJson: fetchJson,
            render: renderReprocess,
            toast: function (text, variant) {
                if (window.Laqta && window.Laqta.toast) window.Laqta.toast(text, { variant: variant });
            },
            confirm: function (text) {
                return window.Laqta && window.Laqta.ask ? window.Laqta.ask({ title: 'نبلّش الدفعة؟', text: text, confirmText: 'ابدأ' })
                    : window.confirm(text);
            },
            schedule: function (fn, ms) { return window.setTimeout(fn, ms); }
        });
        reprocessButton.addEventListener('click', function () { reprocess.plan(); });
        if (reprocessForm) {
            reprocessForm.addEventListener('submit', function (event) {
                event.preventDefault();
                reprocess.start(reprocessMax ? reprocessMax.value : 0, reprocessUsd ? reprocessUsd.value : 0);
            });
        }
        reprocess.poll();
    }
})();
