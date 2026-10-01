/*
 * لقطة · المراجعة — pure helpers shared by both review modes (no DOM, no network).
 *
 * Plain-Arabic labels for every code the pipeline emits (warnings, failure codes, reject reasons, Gemini's reading),
 * candidate normalisation, the product identity sent with approve / reject / upload, the review bucket of each
 * product and the counts behind the filter chips, and the request bodies.
 *
 * Buckets and counts: a product "waits for review" only when its automation_queue row is ready_for_review
 * (GET /api/review/queue-state). The sidebar badge (GET /api/batch-status) counts the same rows with the same
 * query (QueueStats::counters), so "بانتظار المراجعة" here and the badge are one number from one source.
 *
 * Loaded first by catalog.blade.php; the node tests load it on its own (module.exports).
 */
(function (root) {
    'use strict';

    const R = root.LaqtaReview = root.LaqtaReview || {};

    // -------------------------------------------------------------------------------------------------
    // Labels (plain Arabic; codes only ever appear in a secondary tooltip)
    // -------------------------------------------------------------------------------------------------

    // تحذيرات المراجعة (warnings في استجابة البحث، أو warn:<code> في أسباب المرشح المحفوظ): جملة عربية لكل رمز
    const REVIEW_WARNING_LABELS = {
        sheet_silent: 'الشيت ما حدد النوع',
        vlm_unsure: 'Gemini غير متأكد من المطابقة',
        low_resolution: 'صورة منخفضة الدقة (أقل من 500 بكسل)',
        chat_or_screenshot: 'صورة من واتساب أو لقطة شاشة',
        social_media: 'الصورة من مواقع التواصل الاجتماعي',
        foreign_store: 'الصورة من متجر خارج الإمارات (قد تختلف العبوة)',
        barcode_conflict: 'الباركود بالشيت مختلف عن باركود صفحة المتجر: تأكد من المنتج'
    };

    const VARIANT_AXIS_LABELS = {
        fries_cut: 'طريقة التقطيع',
        cheese_form: 'شكل الجبن',
        fat: 'نسبة الدسم',
        sugar: 'السكر',
        caffeine: 'الكافيين',
        form: 'الشكل',
        medium: 'الزيت أو الماء',
        flavour: 'النكهة',
        tuna_meat: 'نوع لحم التونة',
        tuna_cut: 'تقطيع التونة'
    };

    function warningText(code) {
        code = String(code || '');
        const sep = code.indexOf(':');
        const name = sep >= 0 ? code.slice(0, sep) : code;
        const detail = sep >= 0 ? code.slice(sep + 1) : '';
        if (name === 'sheet_silent' && detail) {
            // sheet_silent:<axis>=<value>
            const eq = detail.indexOf('=');
            const axis = eq >= 0 ? detail.slice(0, eq) : '';
            const value = (eq >= 0 ? detail.slice(eq + 1) : detail).split('+').join(' / ');
            const label = VARIANT_AXIS_LABELS[axis] ? `الشيت ما حدد ${VARIANT_AXIS_LABELS[axis]}` : REVIEW_WARNING_LABELS.sheet_silent;
            return `${label}: ${value}`;
        }
        return REVIEW_WARNING_LABELS[name] || code;
    }

    // أسباب الرفض المرقّمة («ليش ترفضها؟»): رموز الهوية نفسها في cli_bridge و CurationController
    const REJECT_REASONS = [
        { code: 'WRONG_PRODUCT', label: 'منتج مختلف' },
        { code: 'WRONG_BRAND', label: 'ماركة أخرى' },
        { code: 'WRONG_VARIANT', label: 'نوع أو نكهة مختلفة' },
        { code: 'WRONG_SIZE', label: 'حجم مختلف' },
        { code: 'WRONG_PACK', label: 'عدد عبوات مختلف' },
        { code: 'NOT_PACKSHOT', label: 'مش صورة منتج' },
        { code: 'LOW_QUALITY', label: 'جودة ضعيفة' }
    ];

    function reasonLabel(code) {
        const r = REJECT_REASONS.find(x => x.code === code);
        return r ? r.label : 'سبب آخر';
    }

    // سبب الفشل بالعربي: رمز الطابور (failure_code) أو بادئة رسالة product_failures ("NO_RESULTS: ...")
    const NOT_FOUND_CODES = ['NO_RESULTS', 'NO_MATCH', 'ALL_CONFLICTED', 'NOT_FOUND'];
    const FAILURE_TEXT = {
        NO_RESULTS: 'البحث ما رجّع أي صورة لهالمنتج.',
        NO_MATCH: 'لقينا صور، بس ولا وحدة طابقت المنتج.',
        ALL_CONFLICTED: 'كل الصور اللي لقيناها لمنتج مختلف (ماركة أو حجم أو نوع).',
        NOT_FOUND: 'ما لقينا صورة مطابقة.',
        SEARCH_ERROR: 'صار خطأ أثناء البحث.',
        CANDIDATE_SAVE_FAILED: 'لقينا صور، بس ما قدرنا نحفظها بقاعدة البيانات.',
        WORKER_ERROR: 'صار خطأ غير متوقع أثناء التشغيل.',
        DOWNLOAD_FAILED: 'ما قدرنا نحمّل الصور من مواقعها.',
        VERIFIER_DOWN: 'Gemini ما كان متاح وقت الفحص.',
        PROVIDER_DOWN: 'مصادر البحث ما كانت متاحة.',
        REJECTED: 'رفضت الصورة المقترحة، فرجع المنتج للطابور.'
    };

    function failureInfo(message, queueCode) {
        const text = String(message || '').trim();
        const m = /^([A-Z][A-Z0-9_]{2,})\s*:\s*([\s\S]*)$/.exec(text);
        let code = queueCode ? String(queueCode) : (m ? m[1] : '');
        if (!code && m) code = m[1];
        if (!code && /^فشل النشر/.test(text)) {
            return { kind: 'failed', code: 'PUBLISH_FAILED', text: 'فشل النشر (عزل الخلفية أو الرفع أو الكتابة بالشيت).', detail: text };
        }
        if (!code && /no acceptable image|not found/i.test(text)) code = 'NO_RESULTS';
        const kind = NOT_FOUND_CODES.includes(code) ? 'not_found' : 'failed';
        const plain = FAILURE_TEXT[code] || (/[؀-ۿ]/.test(m ? m[2] : text) ? (m ? m[2] : text) : 'عطل غير معروف.');
        return { kind: kind, code: code, text: plain, detail: text };
    }

    // رسائل الخادم الإنجليزية بالعربي؛ الرسالة العربية (مثل «المنتج تغيّر أثناء المراجعة؛ افتحه من جديد») كما هي
    const PRODUCT_CHANGED = 'المنتج تغيّر أثناء المراجعة؛ افتحه من جديد';
    const SERVER_ERROR_TEXT = [
        [/Publishing the image failed/i, 'فشل النشر (عزل الخلفية أو الرفع أو الكتابة بالشيت).'],
        [/Uploading the image failed/i, 'فشل رفع الصورة من جهازك.'],
        [/^upload_failed$/i, 'فشل رفع الصورة على Cloudinary.'],
        [/^sheet_write_failed$/i, 'انرفعت الصورة، بس ما انكتب رابطها بالشيت.'],
        [/^processing_failed$/i, 'فشلت معالجة الصورة (عزل الخلفية).'],
        [/barcode is required/i, 'الباركود ناقص بالطلب: حدّث الصفحة وجرّب مرة ثانية.'],
        [/sku_key (or product_name )?is required/i, 'هوية المنتج ناقصة: حدّث الصفحة وجرّب مرة ثانية.'],
        [/are required|Missing parameters/i, 'بيانات المنتج ناقصة بالطلب.'],
        [/could not record the rejection/i, 'ما قدرنا نسجّل الرفض بقاعدة البيانات.'],
        [/invalid reason_code/i, 'سبب الرفض مش معروف.'],
        [/No file uploaded/i, 'ما وصل أي ملف.'],
        [/Search failed/i, 'فشل البحث.'],
        [/CSRF token mismatch|Page Expired|HTTP 419/i, 'انتهت صلاحية الصفحة: حدّثها وجرّب مرة ثانية.'],
        [/HTTP 5\d\d|Server Error/i, 'الخادم ما رد صح. جرّب مرة ثانية.']
    ];

    function plainError(text, fallback) {
        text = String(text === undefined || text === null ? '' : text).trim();
        if (!text) return fallback || 'صار خطأ غير معروف.';
        for (const [re, ar] of SERVER_ERROR_TEXT) {
            if (re.test(text)) return ar;
        }
        if (/[؀-ۿ]/.test(text)) return text;
        return fallback || 'صار خطأ غير معروف.';
    }

    // ما قرأه Gemini عن زاوية الصورة (verdict.view)
    const VIEW_LABELS = {
        front_packshot: 'واجهة المنتج',
        other_side: 'جهة ثانية من العبوة',
        lifestyle: 'صورة استخدام',
        multi_product: 'أكثر من منتج',
        banner: 'بانر إعلاني',
        not_product: 'مش صورة منتج'
    };

    // مصدر الصورة بالعربي (المتجر والسوق)؛ النطاق المجهول يظهر كما هو
    const STORES = [
        [/(^|\.)luluhypermarket\.|(^|\.)lulu/i, 'لولو'],
        [/carrefour|mafrservices|mafretail/i, 'كارفور'],
        [/(^|\.)amazon\.|media-amazon|ssl-images-amazon/i, 'أمازون'],
        [/(^|\.)noon\.com|nooncdn/i, 'نون'],
        [/talabat/i, 'طلبات'],
        [/instagram|cdninstagram/i, 'انستغرام'],
        [/facebook|fbcdn/i, 'فيسبوك'],
        [/spinneys/i, 'سبينيس'],
        [/unioncoop/i, 'تعاونية الاتحاد'],
        [/choithrams/i, 'شويترامز'],
        [/kibsons/i, 'كبسونز'],
        [/openfoodfacts/i, 'Open Food Facts'],
        [/(^|\.)almarai\./i, 'موقع المراعي']
    ];

    function hostOf(url) {
        const m = /^[a-z][a-z0-9+.-]*:\/\/([^/?#:]+)/i.exec(String(url || ''));
        return m ? m[1].toLowerCase().replace(/^www\./, '') : '';
    }

    function marketOf(url) {
        const u = String(url || '').toLowerCase();
        if (!u) return '';
        if (/saudi|\/ksa[/-]|\.sa(\/|$)|-sa\/|\/sa-|\/en-sa|\/ar-sa/.test(u)) return 'السعودية';
        if (/\/uae[/-]|uae\.|\.ae(\/|$)|-ae\/|\/ae-|\/en-ae|\/ar-ae|carrefouruae|luluhypermarket/.test(u)) return 'الإمارات';
        return '';
    }

    function storeOf(c) {
        c = c || {};
        const source = String(c.source || '');
        if (source === 'manual') return { store: 'رابط من عندك', market: '' };
        if (source === 'upload') return { store: 'صورة من جهازك', market: '' };
        const host = String(c.domain || '').replace(/^www\./, '') || hostOf(c.page_url) || hostOf(c.url);
        let store = '';
        for (const [re, name] of STORES) {
            if (re.test(host) || re.test(hostOf(c.page_url))) {
                store = name;
                break;
            }
        }
        return { store: store || host || 'مصدر غير معروف', market: marketOf(c.page_url) || marketOf(c.url), host: host };
    }

    // -------------------------------------------------------------------------------------------------
    // Candidates
    // -------------------------------------------------------------------------------------------------

    function toInt(v) {
        const n = parseInt(v, 10);
        return isFinite(n) && n > 0 ? n : null;
    }

    // مرشح موحد من أي مصدر: استجابة البحث (candidates / selected_image) أو صف curation_candidates المحفوظ
    function normalizeCandidate(c) {
        c = c || {};
        const ev = (c.evidence && typeof c.evidence === 'object' && !Array.isArray(c.evidence)) ? c.evidence : {};
        let reasons = c.reasons;
        if (!Array.isArray(reasons)) reasons = reasons ? [String(reasons)] : [];
        reasons = reasons.map(r => String(r));
        // استجابة البحث ترسل warnings جاهزة؛ صفوف curation_candidates المحفوظة تحمل الأسباب فقط (warn:<code>)
        const warnings = Array.isArray(c.warnings) ? c.warnings.map(w => String(w))
            : reasons.filter(r => r.startsWith('warn:')).map(r => r.slice(5));
        const selected = (c.is_selected === undefined || c.is_selected === null) ? null
            : (parseInt(c.is_selected, 10) === 1 ? 1 : 0);
        return {
            url: String(c.url || c.image_url || ''),
            title: String(c.title || c.page_title || ev.page_title || ev.title || ''),
            page_url: String(c.page_url || ev.page_url || ''),
            domain: String(c.domain || c.source_domain || ev.page_domain || ev.domain || ''),
            status: String(c.status || 'eligible'),
            reasons: reasons,
            warnings: warnings,
            evidence: ev,
            vlm: (c.vlm && typeof c.vlm === 'object' && !Array.isArray(c.vlm)) ? c.vlm : null,
            width: toInt(c.width),
            height: toInt(c.height),
            // الجسر يرسل الطبقة داخل evidence.tier؛ الجدول يحفظها في identity_tier
            identity_tier: c.identity_tier || ev.tier || (c.scores && c.scores.tier) || null,
            content_sha256: c.content_sha256 || ev.content_sha256 || null,
            source: String(c.source || (ev.source === 'cache' ? 'cache' : '')),
            is_selected: selected,
            file: c.file || null
        };
    }

    // مرشحو استجابة البحث: candidates حسب عقد cli_bridge، أو خطوات التتبع للمحرك القديم
    function collectCandidates(data) {
        let list = [];
        if (data && Array.isArray(data.candidates) && data.candidates.length) {
            list = data.candidates;
        } else if (data && data.trace && Array.isArray(data.trace.steps)) {
            data.trace.steps.forEach(step => (step.candidates || []).forEach(c => list.push(c)));
        }
        const seen = new Set();
        return list.map(normalizeCandidate).filter(c => {
            if (!c.url || seen.has(c.url)) return false;
            seen.add(c.url);
            return true;
        });
    }

    // المرشحون المحفوظون لمنتج (curation_candidates)، أو رابط needs_review القديم في الشيت
    function storedCandidates(prod) {
        const list = (prod && Array.isArray(prod.curation_candidates) ? prod.curation_candidates : [])
            .map(normalizeCandidate).filter(c => c.url);
        if (!list.length && prod && prod.needs_review_url) {
            list.push(normalizeCandidate({ url: prod.needs_review_url, status: prod.preselected ? 'preselected' : 'pending',
                                           is_selected: 1, title: '' }));
        }
        return list;
    }

    // الصورة التي اختارها النظام (أو اختيار سابق للمراجع) من المرشحين المحفوظين، أو null
    function storedSelected(prod) {
        return storedCandidates(prod).find(c => c.is_selected === 1) || null;
    }

    // مؤهلة للاعتماد بالجملة: مقترحة من النظام وبلا أي تحذير
    function bulkEligible(c) {
        return !!c && c.status === 'preselected' && c.is_selected !== 0 && !(c.warnings && c.warnings.length);
    }

    // ما يُقال تحت كل صورة بديلة: حالة بالعربي ولونها، والرموز الخام في التفاصيل فقط
    function candidateNote(c, isSystemPick) {
        const detail = c.reasons.join(' · ');
        if (c.status === 'excluded') return { text: 'رفضتها قبل هيك', tone: 'danger', detail: detail };
        if (c.status === 'rejected') {
            const r = c.reasons;
            let text = 'استبعدها النظام';
            if (r.some(x => /^vlm:MISMATCH/.test(x))) text = 'Gemini شاف منتج مختلف';
            else if (r.some(x => /^download:/.test(x))) text = 'ما قدرنا نحمّلها';
            else if (r.some(x => /^quality:/.test(x))) text = 'جودة ضعيفة';
            else if (r.some(x => /size/.test(x))) text = 'حجم مختلف';
            else if (r.some(x => /brand|competitor/.test(x))) text = 'ماركة مختلفة';
            else if (r.some(x => /variant/.test(x))) text = 'نوع مختلف';
            return { text: text, tone: 'danger', detail: detail };
        }
        if (c.warnings.length) return { text: warningText(c.warnings[0]), tone: 'warning', detail: detail };
        if (isSystemPick) {
            const ev = c.evidence || {};
            const brandOk = ev.brand !== undefined && ev.brand !== null && ev.brand !== '' && ev.brand !== false;
            const sizeOk = ev.size === 'match' || ev.size === true;
            return { text: brandOk && sizeOk ? 'مقترحة · ماركة وحجم مطابقين' : 'مقترحة', tone: 'success', detail: detail };
        }
        if (c.source === 'manual') return { text: 'رابط من عندك', tone: 'info', detail: detail };
        if (c.source === 'upload') return { text: 'صورة من جهازك', tone: 'info', detail: detail };
        if (c.vlm && c.vlm.decision === 'UNSURE') return { text: 'Gemini مش متأكد', tone: 'muted', detail: detail };
        if (c.vlm && c.vlm.decision === 'MATCH') return { text: 'Gemini شافها مطابقة', tone: 'success', detail: detail };
        return { text: 'مطابقة محتملة', tone: 'muted', detail: detail };
    }

    // -------------------------------------------------------------------------------------------------
    // Product identity and request bodies
    // -------------------------------------------------------------------------------------------------

    function productIdentity(prod) {
        prod = prod || {};
        return {
            row_number: String(prod.row_number === undefined || prod.row_number === null ? '' : prod.row_number),
            product_name: prod.product_name || '',
            brand: prod.brand || '',
            product_name_ar: prod.product_name_ar || '',
            brand_ar: prod.brand_ar || '',
            barcode: prod.barcode || '',
            sku_key: prod.sku_key || '',
            category: prod.category || '',
            size: prod.size || '',
            sub_category: prod.sub_category || '',
            origin: prod.origin || ''
        };
    }

    // نفس المنتج؟ (الصف و sku_key والاسم كما في الشيت)
    function sameProduct(a, b) {
        return !!(a && b) && String(a.row_number) === String(b.row_number)
            && String(a.sku_key || '') === String(b.sku_key || '') && String(a.product_name || '') === String(b.product_name || '');
    }

    function norm(value) {
        return String(value || '').toLowerCase().replace(/\s+/g, ' ').trim();
    }

    // مفتاح المنتج داخل الصفحة: الصف واسم المنتج (sku_key قد يُعرف لاحقاً من استجابة البحث)
    function itemKey(prod) {
        return `${parseInt(prod.row_number, 10) || 0}|${norm(prod.product_name)}`;
    }

    // مفتاح سجل الفشل في product_failures (نفس cli_bridge.get_products و ApiController::retryFailures)
    function failureKey(prod) {
        const barcode = String(prod.barcode || '').trim();
        if (barcode) return barcode;
        return 'ERR_' + `${prod.product_name || ''}_${prod.brand || ''}`.split(' ').join('_');
    }

    // ما رآه المراجع عن الصورة التي يعتمدها أو يرفضها: يُحسب به دليل دقة الاختيار المسبق لكل براند
    function reviewedCandidateView(c, ctx) {
        const cacheHit = c.reasons.includes('cache_hit') || c.source === 'cache' || (c.evidence && c.evidence.source === 'cache');
        return {
            search_decision: ctx.search_decision || '',
            candidate_status: c.status,
            candidate_cache_hit: cacheHit,
            identity_tier: c.identity_tier === null || c.identity_tier === undefined ? '' : String(c.identity_tier),
            vlm_decision: (c.vlm && c.vlm.decision) ? String(c.vlm.decision) : ''
        };
    }

    // بحث لمنتج واحد: هوية المنتج كما في الشيت (مع الحجم والفئة الفرعية والمنشأ) واستعلامه
    function searchBody(ctx, customQuery, skipCache) {
        return {
            product_name: ctx.product_name,
            brand: ctx.brand,
            product_name_ar: ctx.product_name_ar,
            brand_ar: ctx.brand_ar,
            category: ctx.category,
            custom_query: customQuery || '',
            skip_cache: !!skipCache,
            barcode: ctx.barcode,
            sku_key: ctx.sku_key,
            size: ctx.size,
            sub_category: ctx.sub_category,
            origin: ctx.origin,
            row_number: ctx.row_number
        };
    }

    // اعتماد صورة: هوية المنتج الذي وُجدت له الصورة (ctx)، وليس ما يعرضه النموذج الآن. 0×0 = اللوحة المضبوطة
    function selectBody(ctx, candidate) {
        return {
            image_url: candidate.url,
            page_url: candidate.page_url,
            candidate_sha256: candidate.content_sha256,
            product_name: ctx.product_name,
            brand: ctx.brand,
            row_number: ctx.row_number,
            barcode: ctx.barcode,
            sku_key: ctx.sku_key,
            size: ctx.size,
            product_name_ar: ctx.product_name_ar,
            brand_ar: ctx.brand_ar,
            category: ctx.category,
            ...reviewedCandidateView(candidate, ctx),
            // no enhance / bg_removal_method: the bridge applies the saved image-processing settings
            target_width: 0,
            target_height: 0
        };
    }

    // رفض صورة بسبب محدد (research: إعادة البحث فوراً مع استبعادها)
    function rejectBody(ctx, candidate, reasonCode, research, customQuery) {
        return {
            row_number: ctx.row_number,
            image_url: candidate.url,
            page_url: candidate.page_url,
            candidate_sha256: candidate.content_sha256 || null,
            product_name: ctx.product_name,
            brand: ctx.brand,
            barcode: ctx.barcode,
            sku_key: ctx.sku_key,
            reason_code: reasonCode,
            rejection_reasons: [reasonCode],
            ...reviewedCandidateView(candidate, ctx),
            research: !!research,
            product_name_ar: ctx.product_name_ar,
            brand_ar: ctx.brand_ar,
            category: ctx.category,
            size: ctx.size,
            sub_category: ctx.sub_category,
            origin: ctx.origin,
            custom_query: customQuery || ''
        };
    }

    // رفع صورة من الجهاز: حقول النموذج (الملف يُضاف عند الإرسال)
    function uploadFields(ctx) {
        return [
            ['row_number', ctx.row_number],
            ['product_name', ctx.product_name],
            ['brand', ctx.brand],
            ['barcode', ctx.barcode],
            ['sku_key', ctx.sku_key],
            ['search_decision', ctx.search_decision || ''],
            // الحجم والاسم والبراند بالعربية يدخلون في sku_key الذي يتحقق منه الجسر
            ['size', ctx.size],
            ['product_name_ar', ctx.product_name_ar],
            ['brand_ar', ctx.brand_ar],
            ['category', ctx.category],
            ['target_width', '0'],
            ['target_height', '0']
        ];
    }

    // -------------------------------------------------------------------------------------------------
    // Queue state, buckets and counts
    // -------------------------------------------------------------------------------------------------

    function sameQueueProduct(q, prod) {
        if (q.sku_key && prod.sku_key) return q.sku_key === prod.sku_key;
        return norm(q.product_name) === norm(prod.product_name);
    }

    // يربط كل منتج بصف طابوره (رقم الصف فريد في automation_queue؛ الصف المزاح يُعرف بـ sku_key).
    // orphans: صفوف جاهزة للمراجعة لا يقابلها منتج في الشيت الحالي (تُعرض ولا تسقط من العدد)
    function matchQueue(products, rows) {
        rows = Array.isArray(rows) ? rows : [];
        const byRow = new Map();
        const bySku = new Map();
        rows.forEach(r => {
            byRow.set(parseInt(r.row_number, 10), r);
            if (r.sku_key) {
                if (!bySku.has(r.sku_key)) bySku.set(r.sku_key, []);
                bySku.get(r.sku_key).push(r);
            }
        });
        const used = new Set();
        const match = new Map();
        products.forEach(p => {
            let q = byRow.get(parseInt(p.row_number, 10)) || null;
            if (q && (used.has(q) || !sameQueueProduct(q, p))) q = null;
            if (!q && p.sku_key && bySku.has(p.sku_key)) q = bySku.get(p.sku_key).find(r => !used.has(r)) || null;
            if (q) {
                used.add(q);
                match.set(p, q);
            }
        });
        const orphans = rows.filter(r => !used.has(r) && r.status === 'ready_for_review');
        return { match: match, orphans: orphans };
    }

    function hasFinalImage(prod) {
        return !prod.needs_review && (String(prod.existing_image_link || '').trim() !== '' || !!prod.cached_image);
    }

    // مجموعة المنتج في القائمة. queueKnown=false: حالة الطابور غير معروفة (تُقدّر من المرشحين المحفوظين)
    function classify(prod, q, queueKnown) {
        const selected = storedSelected(prod);
        const waitingBucket = selected ? (selected.warnings.length ? 'warning' : 'proposed') : 'none';
        if (q && q.status === 'ready_for_review') return waitingBucket;
        if (prod.has_error) return failureInfo(prod.error_message, q && q.status === 'failed' ? q.failure_code : null).kind;
        if (q && q.status === 'failed') return failureInfo('', q.failure_code).kind;
        if (hasFinalImage(prod)) return 'approved';
        if (q && q.status === 'processing') return 'searching';
        if (q && q.status === 'pending') return q.failure_code ? 'requeued' : 'queued';
        if (!queueKnown && (prod.needs_review || storedCandidates(prod).length)) return waitingBucket;
        if (storedCandidates(prod).length) return 'stale';
        return 'idle';
    }

    const WAITING = ['proposed', 'warning', 'none'];

    const BUCKET_LABELS = {
        proposed: { text: 'مقترحة', chip: 'proposed' },
        warning: { text: 'مقترحة · تحذير', chip: 'warning' },
        none: { text: 'بلا اقتراح', chip: 'none' },
        not_found: { text: 'ما انلقت', chip: 'not-found' },
        failed: { text: 'عطل', chip: 'error' },
        approved: { text: 'معتمدة', chip: 'approved' },
        approving: { text: 'جاري الاعتماد', chip: 'approved' },
        rejecting: { text: 'جاري الرفض', chip: 'none' },
        rejected: { text: 'رجعت للطابور', chip: 'none' },
        requeued: { text: 'رجعت للطابور', chip: 'none' },
        queued: { text: 'بالطابور', chip: 'none' },
        searching: { text: 'قيد البحث', chip: 'none' },
        stale: { text: 'نتائج سابقة', chip: 'none' },
        idle: { text: 'ما انبحث', chip: 'none' }
    };

    const BUCKET_RANK = {
        proposed: 0, warning: 0, none: 1, not_found: 2, failed: 3, rejected: 4, requeued: 4, searching: 5,
        queued: 6, stale: 6, idle: 7, rejecting: 8, approving: 8, approved: 9
    };

    // رقاقات قائمة المراجعة (?filter=)
    const FILTERS = [
        { key: 'all', label: 'الكل', buckets: null },
        { key: 'proposed', label: 'مقترحة', buckets: ['proposed', 'warning'] },
        { key: 'warning', label: 'فيها تحذير', buckets: ['warning'] },
        { key: 'none', label: 'بلا اقتراح', buckets: ['none'] },
        { key: 'not_found', label: 'ما انلقت', buckets: ['not_found'] },
        { key: 'failed', label: 'أعطال', buckets: ['failed'] }
    ];

    // عناصر القائمة من منتجات الشيت وحالة الطابور. local: حالة هذه الجلسة لكل مفتاح (approving / approved / rejected)
    function buildItems(products, queue, local) {
        products = Array.isArray(products) ? products : [];
        const queueKnown = !!(queue && (queue.status === 'success' || queue.status === 'no_queue'));
        const rows = queueKnown && Array.isArray(queue.rows) ? queue.rows : [];
        const { match, orphans } = matchQueue(products, rows);
        const items = products.map(p => {
            const q = match.get(p) || null;
            return { key: itemKey(p), product: p, queue: q, base: classify(p, q, queueKnown), orphan: false };
        });
        orphans.forEach(q => {
            const p = { row_number: q.row_number, product_name: q.product_name, brand: q.brand, sku_key: q.sku_key || '',
                        curation_candidates: [] };
            items.push({ key: itemKey(p) + '|orphan', product: p, queue: q, base: 'none', orphan: true });
        });
        items.forEach(it => {
            const flag = local && typeof local.get === 'function' ? local.get(it.key) : null;
            it.bucket = flag && BUCKET_LABELS[flag] ? flag : it.base;
        });
        items.sort((a, b) => (BUCKET_RANK[a.bucket] - BUCKET_RANK[b.bucket])
            || ((parseInt(a.product.row_number, 10) || 0) - (parseInt(b.product.row_number, 10) || 0)));
        return items;
    }

    function countBuckets(items) {
        const c = { all: items.length, proposed: 0, warning: 0, none: 0, not_found: 0, failed: 0, waiting: 0, approved: 0 };
        items.forEach(it => {
            if (it.bucket === 'proposed' || it.bucket === 'warning') c.proposed++;
            if (it.bucket === 'warning') c.warning++;
            if (it.bucket === 'none') c.none++;
            if (it.bucket === 'not_found') c.not_found++;
            if (it.bucket === 'failed') c.failed++;
            if (it.bucket === 'approved') c.approved++;
            if (WAITING.includes(it.bucket)) c.waiting++;
        });
        return c;
    }

    function digits(s) {
        return String(s || '').replace(/[٠-٩]/g, d => String('٠١٢٣٤٥٦٧٨٩'.indexOf(d)))
            .replace(/[۰-۹]/g, d => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(d)));
    }

    // بحث القائمة: الاسم (إنجليزي أو عربي) أو البراند أو الباركود أو رقم الصف
    function matchesQuery(item, query) {
        const q = norm(digits(query));
        if (!q) return true;
        const p = item.product;
        if (/^\d+$/.test(q)) {
            if (String(parseInt(p.row_number, 10)) === q) return true;
            if (String(p.barcode || '').replace(/\D/g, '').includes(q)) return true;
        }
        return [p.product_name, p.product_name_ar, p.brand, p.brand_ar, p.barcode].some(v => norm(v).includes(q));
    }

    function filterItems(items, filterKey, query) {
        const f = FILTERS.find(x => x.key === filterKey) || FILTERS[0];
        return items.filter(it => (!f.buckets || f.buckets.includes(it.bucket)) && matchesQuery(it, query));
    }

    // -------------------------------------------------------------------------------------------------
    // Facts, sheet-versus-image checks
    // -------------------------------------------------------------------------------------------------

    const UNITS = [
        [/^(kg|kgs|kilo|kilogram)$/i, 'كغ'], [/^(g|gm|gms|gr|gram|grams)$/i, 'غ'], [/^(l|lt|ltr|litre|liter)$/i, 'لتر'],
        [/^(ml|mls)$/i, 'مل'], [/^(pcs|pc|pieces|s|eggs)$/i, 'حبة']
    ];

    // «1KG» → «1 كغ»؛ ما لا يُفهم يبقى كما في الشيت
    function sizeText(size) {
        const raw = String(size || '').trim();
        const m = /^(\d+(?:[.,]\d+)?)\s*([a-z]+)\.?$/i.exec(raw);
        if (!m) return raw;
        const unit = UNITS.find(([re]) => re.test(m[2]));
        return unit ? `${m[1].replace(',', '.')} ${unit[1]}` : raw;
    }

    function categoryPath(prod) {
        const parts = [];
        String(prod.category || '').split(/\s*[>›/]\s*/).forEach(x => { if (x.trim()) parts.push(x.trim()); });
        [prod.sub_category, prod.sub_sub_category_ar || prod.sub_sub_category].forEach(x => {
            const v = String(x || '').trim();
            if (v && !parts.includes(v)) parts.push(v);
        });
        return parts;
    }

    // رقائق حقائق المنتج؛ الناقص منها يُعلَّم «غير موجود»
    function factsFor(prod) {
        const brand = prod.brand || prod.brand_ar || '';
        const size = sizeText(prod.size);
        return [
            { key: 'brand', label: 'الماركة', icon: 'store', value: brand || 'غير موجود', missing: !brand, tone: brand ? '' : 'missing', ltr: !!prod.brand },
            { key: 'size', label: 'الحجم', icon: 'weight', value: size || 'غير موجود', missing: !size, tone: size ? '' : 'missing', ltr: false },
            { key: 'barcode', label: 'الباركود', icon: 'barcode', value: prod.barcode || 'غير موجود بالشيت', missing: !prod.barcode,
              tone: prod.barcode ? '' : 'missing', ltr: !!prod.barcode },
            { key: 'name_ar', label: 'الاسم العربي', icon: 'text', value: prod.product_name_ar || 'غير موجود',
              missing: !prod.product_name_ar, tone: prod.product_name_ar ? '' : 'muted', ltr: false }
        ];
    }

    function matchStatus(value) {
        const v = String(value === undefined || value === null ? '' : value).toLowerCase();
        if (v === 'yes' || v === 'match' || v === 'true') return 'match';
        if (v === 'no' || v === 'conflict' || v === 'mismatch' || v === 'false') return 'mismatch';
        if (v === 'unsure' || v === 'ambiguous') return 'unsure';
        return 'unknown';
    }

    // نوع المنتج كما حدده الشيت: evidence.variants تحمل رموز المحاور المتطابقة (fat, form ...) وقيمها في
    // evidence.variants_found؛ تُعرض بالعربي («نسبة الدسم: full») ولا يظهر رمز المحور نفسه
    function variantsText(ev) {
        const axes = Array.isArray(ev.variants) ? ev.variants.map(a => String(a)) : [];
        const found = ev.variants_found && typeof ev.variants_found === 'object' ? ev.variants_found : {};
        return axes.map(axis => {
            let value = '';
            Object.keys(found).some(field => {
                const v = found[field] && typeof found[field] === 'object' ? found[field][axis] : null;
                if (v === undefined || v === null || v === '') return false;
                value = (Array.isArray(v) ? v.join('+') : String(v)).split('+').join(' / ');
                return true;
            });
            const label = VARIANT_AXIS_LABELS[axis] || 'النوع';
            return value ? `${label}: ${value}` : label;
        }).join('، ');
    }

    // «الشيت مقابل الصورة»: ما في الشيت مقابل ما قرأه Gemini على الصورة، وعلامة لكل سطر
    function checksFor(prod, c) {
        const vlm = c && c.vlm ? c.vlm : null;
        const ev = (c && c.evidence) || {};
        const warnings = (c && c.warnings) || [];
        const silent = warnings.find(w => String(w).startsWith('sheet_silent'));
        const variants = Array.isArray(ev.variants) ? ev.variants : [];
        const rows = [];
        const brandSheet = prod.brand || prod.brand_ar || 'غير موجود';
        let brand = vlm ? matchStatus(vlm.brand_match) : 'unknown';
        if (brand === 'unknown' && ev.brand) brand = 'match';
        rows.push({ key: 'brand', label: 'الماركة', sheet: brandSheet, image: vlm && vlm.brand_text ? String(vlm.brand_text) : '—', status: brand });
        let size = vlm ? matchStatus(vlm.size_match) : 'unknown';
        if (size === 'unknown') size = matchStatus(ev.size);
        rows.push({ key: 'size', label: 'الحجم', sheet: sizeText(prod.size) || 'غير موجود', image: vlm && vlm.size_text ? String(vlm.size_text) : '—', status: size });
        let variant = vlm ? matchStatus(vlm.variant_match) : 'unknown';
        if (ev.variant_status === 'conflict') variant = 'mismatch';
        if (silent && variant !== 'mismatch') variant = 'unsure';
        const variantSheet = variants.length ? variantsText(ev) : 'غير محدد';
        rows.push({ key: 'variant', label: 'النوع', sheet: variantSheet, image: vlm && vlm.variant_text ? String(vlm.variant_text) : '—', status: variant });
        const view = vlm ? String(vlm.view || '') : '';
        let angle = 'unknown';
        if (view === 'front_packshot') angle = 'match';
        else if (view === 'other_side') angle = 'unsure';
        else if (view) angle = 'mismatch';
        rows.push({ key: 'angle', label: 'الزاوية', sheet: 'واجهة المنتج', image: view ? (VIEW_LABELS[view] || view) : '—', status: angle });
        return { read: !!vlm, rows: rows };
    }

    // «تأكد قبل الاعتماد»: كل تحذير بجملة، وما يخالف فيه Gemini الشيت. الصورة التي يختارها المراجع بنفسه لا تحمل
    // تحذيرات الاختيار المسبق (warn:*)، فشك Gemini فيها وزاويتها يُقالان هنا أيضاً
    function cautionsFor(c) {
        if (!c) return [];
        const out = c.warnings.map(w => warningText(w));
        if (c.status === 'rejected' || c.status === 'excluded') out.unshift(candidateNote(c, false).text);
        const vlm = c.vlm || {};
        if (vlm.decision === 'MISMATCH' && !out.includes('Gemini شاف منتج مختلف')) out.unshift('Gemini شاف منتج مختلف');
        if (vlm.decision === 'UNSURE' && !c.warnings.some(w => String(w).split(':')[0] === 'vlm_unsure')) {
            out.push(REVIEW_WARNING_LABELS.vlm_unsure);
        }
        const view = String(vlm.view || '');
        if (view && view !== 'front_packshot') out.push(`الصورة مش لواجهة المنتج (${VIEW_LABELS[view] || 'زاوية ثانية'})`);
        return out;
    }

    Object.assign(R, {
        REVIEW_WARNING_LABELS, VARIANT_AXIS_LABELS, REJECT_REASONS, FAILURE_TEXT, NOT_FOUND_CODES, VIEW_LABELS,
        BUCKET_LABELS, FILTERS, WAITING, PRODUCT_CHANGED,
        warningText, reasonLabel, failureInfo, plainError, hostOf, marketOf, storeOf,
        normalizeCandidate, collectCandidates, storedCandidates, storedSelected, bulkEligible, candidateNote,
        productIdentity, sameProduct, itemKey, failureKey, reviewedCandidateView,
        searchBody, selectBody, rejectBody, uploadFields,
        matchQueue, classify, hasFinalImage, buildItems, countBuckets, matchesQuery, filterItems,
        sizeText, categoryPath, factsFor, checksFor, cautionsFor
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = R;
    }
})(typeof window !== 'undefined' ? window : globalThis);
