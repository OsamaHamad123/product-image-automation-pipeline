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
        // listing_silent:<axis>=<value>: الشيت يذكر نوعاً لا تُظهره صفحة المتجر ولا قراءة الملصق
        listing_silent: 'الشيت بيذكر نوع ما بيبيّنه المتجر ولا الملصق: تأكد إنها نفس النوع',
        vlm_unsure: 'نموذج القراءة غير متأكد من المطابقة',
        multipack_unit_image: 'الصورة لعبوة وحدة، والمنتج باكيت من أكثر من حبة: تأكد إنها مناسبة',
        // size_close:<الحجم على العلبة>/<حجم الشيت>: الحجمان متقاربان (ضمن السماحية) بس مش نفس الرقم
        size_close: 'الحجم على العلبة قريب من الشيت بس مش نفسه: تأكد وصحّح الشيت',
        low_resolution: 'صورة منخفضة الدقة (أقل من 500 بكسل)',
        chat_or_screenshot: 'صورة من واتساب أو لقطة شاشة',
        social_media: 'الصورة من مواقع التواصل الاجتماعي',
        foreign_store: 'الصورة من متجر خارج الإمارات (قد تختلف العبوة)',
        barcode_conflict: 'الباركود بالشيت مختلف عن باركود صفحة المتجر: تأكد من المنتج',
        brand_spelling: 'المتاجر بتكتب اسم الماركة غير الشيت (غلطة إملائية أو اختصار): تأكد إنها نفس الماركة',
        // catalog_match.normalizer: الشيت ما فيه ماركة أو ما لقيناها، فجربنا بحث باسم الماركة اللي خمّنه نموذج القراءة
        brand_from_normaliser: 'جربنا البحث بماركة خمّنها النظام من اسم الشيت المختصر: تأكد إنها نفس الماركة',
        // catalog_match.embeddings: الصورة بعيدة عن كل الصور المعتمدة للماركة وقريبة من صورة معتمدة لماركة ثانية
        brand_look_mismatch: 'شكل العبوة أقرب لماركة تانية: تأكد إنها نفس الماركة قبل الاعتماد',
        // للعرض فقط (decide.candidate_warnings): الشيت يذكر الحجم أو النوع ولم يؤكده دليل
        size_unverified: 'الحجم غير مؤكد: لم تؤكده صفحة المتجر ولا قراءة الملصق',
        variant_unverified: 'النوع غير مؤكد: لم تؤكده صفحة المتجر ولا قراءة الملصق',
        // main.DUPLICATE_WARNING: الصورة نفسها منشورة لمنتج آخر
        duplicate_image: 'الصورة نفسها منشورة لمنتج آخر: تأكد إنها مش صورة منتج مختلف'
    };
    // رمز تحذير لا تعرفه الصفحة بعد (حزمة أحدث في الخادم): جملة عامة، والرمز في التلميح فقط
    const UNKNOWN_WARNING = 'تحذير آخر على هالصورة: راجعها بعناية قبل الاعتماد';

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
        tuna_cut: 'تقطيع التونة',
        protein: 'نوع اللحم'
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
        if (name === 'listing_silent' && detail) {
            // listing_silent:<axis>=<value> ('flavour=chili' لـ 'H/S'): الشيت يذكره، والصفحة والملصق ساكتان
            const eq = detail.indexOf('=');
            const axis = eq >= 0 ? detail.slice(0, eq) : '';
            const value = (eq >= 0 ? detail.slice(eq + 1) : detail).split('+').join(' / ');
            const what = VARIANT_AXIS_LABELS[axis] || 'النوع';
            return `الشيت بيقول ${what}: ${value}، بس صفحة المتجر والملصق ما بيبيّنوه: تأكد إنها نفس المنتج`;
        }
        if (name === 'size_close' && detail.trim()) {
            // size_close:<الحجم على العلبة>/<حجم الشيت> ('840g/850g')
            const slash = detail.indexOf('/');
            const printed = (slash >= 0 ? detail.slice(0, slash) : detail).trim();
            const sheet = slash >= 0 ? detail.slice(slash + 1).trim() : '';
            const sizes = sheet ? `${printed} مقابل ${sheet}` : printed;
            return `الحجم على العلبة قريب من الشيت بس مش نفسه (${sizes}): تأكد وصحّح الشيت`;
        }
        if (name === 'brand_spelling' && detail.trim()) {
            // brand_spelling:<الكتابة>: الكتابة التي وجدتها المتاجر تظهر للمراجع
            return `المتاجر تكتب الماركة «${detail.trim()}» بشكل مختلف عن الشيت: تأكد أنها الماركة نفسها`;
        }
        return REVIEW_WARNING_LABELS[name] || UNKNOWN_WARNING;
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

    // أسباب تجميلية تقبلها الواجهة الخلفية (local_cache_db.COSMETIC_REASON_CODES): تخص المعالجة لا هوية الصورة،
    // فتُعرض أولاً لصورة اعتُمدت ولم تُعزل خلفيتها
    const COSMETIC_REASONS = [
        { code: 'HALO_ARTIFACT', label: 'هالة حول المنتج' },
        { code: 'BACKGROUND_BLEED', label: 'بقايا من الخلفية' },
        { code: 'CROP_MARGIN_CLIPPING', label: 'المنتج مقصوص من الأطراف' }
    ];

    function rejectReasonsFor(bucket) {
        return bucket === 'bg_failed' ? COSMETIC_REASONS.concat(REJECT_REASONS) : REJECT_REASONS;
    }

    function reasonLabel(code) {
        const r = REJECT_REASONS.concat(COSMETIC_REASONS).find(x => x.code === code);
        return r ? r.label : 'سبب آخر';
    }

    // «تراجع عن الرفض»: صور هالمنتج اللي رفضها مراجع (rejected_images من /api/products-json) واللي رفضتها بهالجلسة،
    // كل رابط مرة وحدة. الزر يبعت undoRejectBody لـ POST /api/review/undo-reject (cli_bridge.undo_reject)
    const UNDO_REJECT_LABEL = 'تراجع عن الرفض';
    const UNDO_REJECT_CONFIRM = 'ترجع هالصورة للاقتراحات؟';

    function rejectedImages(product, sessionUrls, sessionReasons) {
        const out = [];
        const seen = new Set();
        const add = (url, code) => {
            const u = String(url || '').trim();
            if (!u || seen.has(u) || !/^https?:\/\//i.test(u)) return;
            seen.add(u);
            out.push({ url: u, reason_code: String(code || '') });
        };
        (Array.isArray(product && product.rejected_images) ? product.rejected_images : [])
            .forEach(r => add(r && r.url, r && r.reason_code));
        (sessionUrls ? Array.from(sessionUrls) : []).forEach(u => add(u, sessionReasons && sessionReasons.get ? sessionReasons.get(u) : ''));
        return out;
    }

    function undoRejectBody(ctx, url) {
        return { sku_key: ctx.sku_key, image_url: url, row_number: ctx.row_number };
    }

    // بعد التراجع: الصورة تطلع من قائمة المرفوضة، ومرشحها المستبعد ('excluded') يرجع للاقتراحات متل ما عمل الخادم
    function forgetRejection(product, url) {
        if (!product) return;
        if (Array.isArray(product.rejected_images)) product.rejected_images = product.rejected_images.filter(r => !r || r.url !== url);
        (Array.isArray(product.curation_candidates) ? product.curation_candidates : []).forEach(c => {
            if (c && (c.image_url === url || c.url === url) && c.status === 'excluded') c.status = 'eligible';
        });
    }

    // «الباركود من صفحة المتجر»: صف بلا باركود بالشيت، والصفحة اللي جت منها الصورة ذكرت باركود (evidence.page_gtin: GTIN
    // صالح وعالمي من catalog_match.facade). بينحفظ مع الاعتماد وبينصدّر (scripts/export_barcodes.py)؛ الشيت ما بيتغيّر
    const PAGE_GTIN_LABEL = 'الباركود من صفحة المتجر: ';

    function sheetLacksBarcode(product) {
        const p = product || {};
        if ((Array.isArray(p.sheet_issues) ? p.sheet_issues : []).some(i => i && i.key === 'no_barcode')) return true;
        return !String(p.barcode || '').trim();
    }

    function pageGtinOf(product, candidate) {
        const ev = (candidate && candidate.evidence && typeof candidate.evidence === 'object') ? candidate.evidence : {};
        const gtin = String(ev.page_gtin || '').trim();
        return /^\d{8,14}$/.test(gtin) && sheetLacksBarcode(product) ? gtin : '';
    }

    // صورة ثانية من معرض صفحة المتجر (evidence.page_gallery): البحث جابها لأن الصورة الرئيسية للصفحة غلط (عبوتين، لوغو المتجر)
    const GALLERY_NOTE = 'صورة ثانية من معرض صفحة المتجر';

    function galleryNote(c) {
        const ev = (c && c.evidence && typeof c.evidence === 'object') ? c.evidence : {};
        return ev.page_gallery === true ? GALLERY_NOTE : '';
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
        SOCIAL_ONLY: 'المنتج ظاهر فقط بمنشورات تواصل اجتماعي ما بتنزل صورها.',
        VERIFIER_DOWN: 'نموذج قراءة الملصق ما كان متاح وقت الفحص.',
        RECHECK_NOT_FOUND: 'رجعنا فحصنا بنموذج قراءة الملصق وما لقينا صورة أحسن؛ الاقتراحات القديمة بتستنى عينك.',
        SHEET_WRITE_FAILED: 'الصورة معتمدة، بس ما انكتب رابطها بالشيت؛ بينكتب بالتشغيل الجاي.',
        PROVIDER_DOWN: 'مصادر البحث ما كانت متاحة.',
        PRODUCT_TIMEOUT: 'البحث عن هالمنتج طوّل كتير، فوقّفناه. بنرجع نجرّب بعد يوم.',
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
        // مزوّد عزل الخلفية (image_processor: photoroom_* و removebg_*): ما انعزلت الخلفية فما نُشر شيء. الرصيد والمفتاح
        // والحصة (BG_SKIP_RE) بيفشّلوا كل اعتماد بنفس الشكل: اللوحة بتعرض «تجاوز عزل الخلفية»
        [/^photoroom_402$/i, 'رصيد PhotoRoom خلص أو الاشتراك موقوف، فما انعزلت الخلفية وما انعتمدت الصورة.'],
        [/^photoroom_(401|403)$/i, 'PhotoRoom رفض المفتاح، فما انعزلت الخلفية: حدّث مفتاح PhotoRoom بالإعدادات.'],
        [/^photoroom_429$/i, 'PhotoRoom رافض طلبات كتير هلق، فما انعزلت الخلفية: استنى دقيقة وأعد المحاولة.'],
        [/^photoroom_no_key$/i, 'مفتاح PhotoRoom مش محفوظ بالإعدادات، فما انعزلت الخلفية.'],
        [/^photoroom_(timeout|connection_error|5\d\d)$/i, 'PhotoRoom ما ردّ، فما انعزلت الخلفية: تأكد من الإنترنت وأعد المحاولة.'],
        [/^photoroom_/i, 'PhotoRoom رجّع خطأ، فما انعزلت الخلفية: أعد المحاولة.'],
        [/^removebg_402$/i, 'رصيد remove.bg خلص، فما انعزلت الخلفية وما انعتمدت الصورة.'],
        [/^removebg_(401|403)$/i, 'remove.bg رفض المفتاح، فما انعزلت الخلفية: حدّث مفتاح remove.bg.'],
        [/^removebg_429$/i, 'remove.bg رافض طلبات كتير هلق، فما انعزلت الخلفية: استنى دقيقة وأعد المحاولة.'],
        [/^removebg_no_key$/i, 'مفتاح remove.bg مش محفوظ، فما انعزلت الخلفية.'],
        [/^removebg_(timeout|connection_error|5\d\d)$/i, 'remove.bg ما ردّ، فما انعزلت الخلفية: تأكد من الإنترنت وأعد المحاولة.'],
        [/^removebg_/i, 'remove.bg رجّع خطأ، فما انعزلت الخلفية: أعد المحاولة.'],
        [/^rembg_not_installed$/i, 'مكتبة rembg مش منزّلة على هالجهاز: اختار طريقة عزل ثانية من الإعدادات (تبويب «معالجة الصور»).'],
        [/^(grabcut|rembg)_|_empty_cutout$/i, 'عزل الخلفية المحلي ما طلّع المنتج من الصورة: اختر صورة ثانية أو طريقة عزل ثانية.'],
        [/^(bria_rmbg_unsupported|unknown_bg_method)$/i, 'طريقة عزل الخلفية المختارة مش مدعومة: اختار PhotoRoom من الإعدادات (تبويب «معالجة الصور»).'],
        // رموز تجهيز الصورة المعتمدة (image_processor): ما نُشر شيء
        [/^source_changed$/i, 'الصورة على موقع المتجر تغيّرت من وقت ما انفحصت، فما نشرناها: أعد البحث عن المنتج.'],
        [/^download_(timeout|connection_error|http_5\d\d|failed)$/i, 'ما قدرنا ننزّل الصورة من موقع المتجر (الاتصال أو البروكسي): جرّب مرة ثانية.'],
        [/^download_http_(403|429)$/i, 'موقع المتجر رفض تنزيل الصورة: جرّب مرة ثانية، وإذا تكرر جرّب صورة من متجر ثاني.'],
        [/^download_http_(404|410)$/i, 'الصورة انشالت من موقع المتجر: أعد البحث أو اختر صورة ثانية.'],
        [/^download_host_slow$/i, 'موقع المتجر بطيء أو ما بيرد هلق وتخطّيناه مؤقتاً: جرّب بعد شوي أو اختر صورة من متجر ثاني.'],
        [/^download_blocked_url$/i, 'رابط الصورة بيودّي على عنوان داخلي أو مش آمن، فما نزّلناه: اختر صورة ثانية.'],
        [/^download_|^(not_image|image_too_large|source_too_large)$/i, 'الرابط ما عاد صورة صالحة: اختر صورة ثانية أو أعد البحث.'],
        [/^(source_missing|source_not_found|source_unreadable)$/i, 'الصورة مش موجودة على الجهاز: اختر صورة ثانية أو أعد البحث.'],
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

    // رموز فشل عزل الخلفية اللي بيحلها «تجاوز عزل الخلفية» (رصيد أو مفتاح أو حصة): نفس publish_check.BG_SKIP_CODE_RE
    // و HealthController::BG_SKIP_PATTERN و health.js
    const BG_SKIP_RE = /^(photoroom|removebg)_(no_key|401|402|403|429)$/;

    function bgSkipCode(code) {
        return BG_SKIP_RE.test(String(code || '').trim());
    }

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

    // أقسام البلدان في مواقع المتاجر ('/en-kw/' في لولو، '/saudi-en/' في نون، '/kuwait/' في طلبات): نفس قاعدة الخادم
    // (catalog_match.text_norm.store_market، تحذير foreign_store)، وتُفحص قبل اسم الموقع: لولو الكويت ليست الإمارات
    const MARKET_WORDS = {
        ae: 'الإمارات', uae: 'الإمارات',
        sa: 'السعودية', ksa: 'السعودية', saudi: 'السعودية', kw: 'الكويت', kuwait: 'الكويت', qa: 'قطر', qatar: 'قطر',
        om: 'عُمان', oman: 'عُمان', bh: 'البحرين', bahrain: 'البحرين', eg: 'مصر', egypt: 'مصر', jo: 'الأردن',
        jordan: 'الأردن', in: 'الهند', india: 'الهند', pk: 'باكستان', pakistan: 'باكستان'
    };
    const UAE_MARKET_WORDS = ['ae', 'uae'];
    const LOCALE_WORDS = ['en', 'ar'];
    // نطاقات الدول (catalog_match.decide._FOREIGN_TLDS) بأسمائها
    const MARKET_TLDS = { ae: 'الإمارات', sa: 'السعودية', kw: 'الكويت', qa: 'قطر', om: 'عُمان', bh: 'البحرين', eg: 'مصر',
                          jo: 'الأردن', in: 'الهند', pk: 'باكستان' };

    function pathOf(url) {
        const m = /^[a-z][a-z0-9+.-]*:\/\/[^/?#]*([^?#]*)/i.exec(String(url || ''));
        return m ? m[1] : '';
    }

    // 'foreign' أو 'uae' أو '' من أول قسمين في مسار الصفحة، بالضبط كما يقرؤهما الخادم (store_market)، مع اسم البلد
    function storeMarket(url) {
        const segs = pathOf(url).toLowerCase().split('/').filter(Boolean).slice(0, 2);
        for (const seg of segs) {
            const parts = seg.split(/[-_]/).filter(Boolean);
            if (!parts.length || parts.length > 2 || !parts.every(p => MARKET_WORDS[p] || LOCALE_WORDS.includes(p))) {
                return { market: '', name: '' };       // اسم منتج في الرابط، لا قسم بلد
            }
            const foreign = parts.find(p => MARKET_WORDS[p] && !UAE_MARKET_WORDS.includes(p));
            if (foreign) return { market: 'foreign', name: MARKET_WORDS[foreign] };
            if (parts.some(p => UAE_MARKET_WORDS.includes(p))) return { market: 'uae', name: 'الإمارات' };
        }
        return { market: '', name: '' };
    }

    function marketOf(url) {
        const u = String(url || '').toLowerCase();
        if (!u) return '';
        const section = storeMarket(u);
        if (section.name) return section.name;
        const host = hostOf(u);
        if (/(^|\.)uae\.|carrefouruae|luluhypermarket/.test(host)) return 'الإمارات';
        const tld = host.split('.').pop();
        return MARKET_TLDS[tld] || '';
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

    // الأرقام الهندية والفارسية ('١ لتر') أرقاماً لاتينية
    function digits(s) {
        return String(s || '').replace(/[٠-٩]/g, d => String('٠١٢٣٤٥٦٧٨٩'.indexOf(d)))
            .replace(/[۰-۹]/g, d => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(d)));
    }

    // حجم في نص الشيت (خلية الحجم أو الاسم): رقم ثم وحدة (catalog_match.sizes يقرأ الصيغ نفسها وأكثر)
    const SIZE_IN_TEXT = /(\d+(?:[.,]\d+)?)\s*(ml|mls|cl|l|lt|ltr|ltrs|litre|litres|liter|liters|g|gm|gms|gr|grm|gram|grams|kg|kgs|kilo|oz|lb|lbs|مل|ملل|مليلتر|لتر|ل|غ|غم|غرام|جم|جرام|كغ|كجم|كيلو)(?![a-z\u0600-\u06ff])/i;
    // عدد العبوات: '6x330ml'، '6 × 330 مل'، 'pack of 6'، '6 pack'
    const PACK_IN_TEXT = [/(\d+)\s*[x×*]\s*\d/i, /pack\s+of\s+(\d+)/i, /(\d+)\s*-?\s*(?:pack|pk|pcs)\b/i];

    // ما يذكره صف الشيت كما قرأه الخادم (cli_bridge.get_products: sheet_states من catalog_match.identity)، وإلا يُقرأ
    // هنا من خلية الحجم والاسم (بلا محاور النوع: معجمها في الخادم وحده)
    function sheetStates(prod) {
        prod = prod || {};
        const st = prod.sheet_states;
        if (st && typeof st === 'object' && !Array.isArray(st)) {
            return { size: !!st.size, pack: parseInt(st.pack, 10) || 1,
                     variants: Array.isArray(st.variants) ? st.variants.map(v => String(v)) : [] };
        }
        const text = digits([prod.size, prod.product_name, prod.product_name_ar].filter(Boolean).join(' '));
        let pack = 1;
        PACK_IN_TEXT.some(re => {
            const m = re.exec(text);
            if (m && parseInt(m[1], 10) > 1) pack = parseInt(m[1], 10);
            return pack > 1;
        });
        return { size: SIZE_IN_TEXT.test(text) || /\d/.test(digits(prod.size)), pack: pack, variants: [] };
    }

    // «الحجم / النوع غير مؤكد» بقاعدة الخادم (catalog_match.decide.unverified_warnings) من الأدلة المحفوظة: الشيت يذكر
    // حجماً (أو عدد عبوات) / نوعاً، ولم تؤكده صفحة المتجر ولا قراءة الملصق («نعم»)، ولا باركود الشيت على الصفحة.
    // صف بلا دليل محفوظ (evidence فارغة) غير مؤكد
    function unverifiedWarnings(c, prod) {
        const ev = c.evidence || {};
        const vlm = c.vlm || {};
        if (ev.gtin === 'match') return [];
        const st = sheetStates(prod);
        const out = [];
        const sizeOk = ev.size === 'match' || ev.size === true || vlm.size_match === 'yes';
        const packOk = ev.pack === 'match' || (vlm.pack_count !== undefined && vlm.pack_count !== null
                                               && parseInt(vlm.pack_count, 10) === st.pack);
        if ((st.size && !sizeOk) || (st.pack > 1 && !packOk)) out.push('size_unverified');
        if (st.variants.length) {
            const matched = (Array.isArray(ev.variants_matched) ? ev.variants_matched : Array.isArray(ev.variants) ? ev.variants : [])
                .map(a => String(a));
            if (!st.variants.every(a => matched.includes(a)) && vlm.variant_match !== 'yes') out.push('variant_unverified');
        }
        return out;
    }

    // صف محفوظ قبل أن يحسب الخادم هذين التحذيرين: يُشتقان هنا. تضيف تحذيراً ولا تزيل شيئاً
    function withDerivedWarnings(c, prod) {
        if (!['preselected', 'eligible'].includes(c.status)) return c;
        // صورة اعتمدها مراجع لهذا المنتج سابقاً (الكاش): هويتها مؤكدة بذلك الاعتماد
        if (c.reasons.includes('cache_hit') || (c.evidence && c.evidence.source === 'cache')) return c;
        const add = unverifiedWarnings(c, prod).filter(w => !c.warnings.includes(w));
        if (add.length) c.warnings = c.warnings.concat(add);
        return c;
    }

    // المرشحون المحفوظون لمنتج (curation_candidates)، أو رابط needs_review القديم في الشيت
    function storedCandidates(prod) {
        const list = (prod && Array.isArray(prod.curation_candidates) ? prod.curation_candidates : [])
            .map(normalizeCandidate).filter(c => c.url).map(c => withDerivedWarnings(c, prod));
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

    // مؤهلة للاعتماد بالجملة: مقترحة من النظام وبلا أي تحذير (ومنها «الحجم غير مؤكد» و«النوع غير مؤكد»)
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
            if (r.some(x => /^vlm:MISMATCH/.test(x))) text = 'نموذج القراءة شاف منتج مختلف';
            else if (r.some(x => /^download:host_slow$/.test(x))) text = 'موقع المتجر بطيء: تخطّيناها';
            else if (r.some(x => /^download:blocked_url$/.test(x))) text = 'رابطها مش آمن: ما نزّلناها';
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
        if (c.vlm && c.vlm.decision === 'UNSURE') return { text: 'نموذج القراءة مش متأكد', tone: 'muted', detail: detail };
        if (c.vlm && c.vlm.decision === 'MATCH') return { text: 'نموذج القراءة شافها مطابقة', tone: 'success', detail: detail };
        return { text: 'مطابقة محتملة', tone: 'muted', detail: detail };
    }

    const SOURCE_CLASS_TEXT = {
        official: 'موقع الماركة الرسمي',
        uae_retailer: 'متجر في الإمارات',
        reviewed_source: 'موقع اعتُمدت صوره سابقاً',
        structured: 'قاعدة بيانات منتجات'
    };

    function truthy(v) {
        return v !== undefined && v !== null && v !== '' && v !== false;
    }

    const SIZE_CORROBORATED_TEXT = 'الحجم مأكد من موقعين';

    // «لماذا هذه الصورة؟»: سطور قصيرة من الأدلة التي حسبها المحرك فعلاً (facade.evidence وقراءة الملصق)، بلا تخمين.
    // لا يُقال شيء إذا لم يوجد دليل
    function explainPick(c) {
        // صورة استبعدها النظام أو رفضها مراجع: لا «لماذا هذه الصورة»
        if (!c || c.status === 'rejected' || c.status === 'excluded') return [];
        const ev = c.evidence || {};
        const vlm = c.vlm || {};
        const out = [];
        if ((c.reasons || []).includes('cache_hit') || ev.source === 'cache') {
            out.push({ key: 'cache', text: 'اعتُمدت لهذا المنتج سابقاً' });
        }
        if (ev.gtin === 'match') out.push({ key: 'gtin', text: 'باركود الصفحة يطابق الشيت' });
        // نفس الرابط من أكثر من موقع (consensus_count)، أو نفس الصورة بروابط ثانية على مواقع مختلفة (same_picture_domains)
        const sameSites = Array.isArray(ev.same_picture_domains) ? ev.same_picture_domains.length : 0;
        const n = Math.max(parseInt(ev.consensus_count, 10) || 0, sameSites);
        if (n >= 2) out.push({ key: 'consensus', text: n === 2 ? 'الصورة نفسها في مصدرين' : `الصورة نفسها في ${n} مصادر` });
        // الملصق ما بيّن الحجم، بس موقعين موثوقين بيذكروه بعنوان نفس الصورة (للعرض فقط: ما بيغيّر الاقتراح)
        if ((c.reasons || []).includes('size_corroborated')) {
            out.push({ key: 'size_corroborated', text: SIZE_CORROBORATED_TEXT });
        }
        if (SOURCE_CLASS_TEXT[ev.source_class]) out.push({ key: 'source', text: SOURCE_CLASS_TEXT[ev.source_class] });
        const page = [];
        if (truthy(ev.brand)) page.push('الماركة');
        if (ev.size === 'match' || ev.size === true) page.push('الحجم');
        if (ev.pack === 'match') page.push('العبوات');
        if (ev.variant_status === 'match') page.push('النوع');
        if (page.length) out.push({ key: 'page', text: `الصفحة تذكر: ${page.join('، ')}` });
        const label = [];
        if (vlm.brand_match === 'yes') label.push('الماركة');
        if (vlm.size_match === 'yes') label.push('الحجم');
        if (vlm.variant_match === 'yes') label.push('النوع');
        if (label.length) out.push({ key: 'label', text: `الملصق يطابق: ${label.join('، ')}` });
        return out;
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
            search_lane: ctx.search_lane || '',
            candidate_status: c.status,
            candidate_cache_hit: cacheHit,
            identity_tier: c.identity_tier === null || c.identity_tier === undefined ? '' : String(c.identity_tier),
            vlm_decision: (c.vlm && c.vlm.decision) ? String(c.vlm.decision) : '',
            // تحذيرات الصورة كما رآها المراجع: اعتماد صورة عليها brand_spelling:<الكتابة> يعلّم البحث هذه الكتابة
            candidate_warnings: (c.warnings || []).map(w => String(w)).join('|')
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
            // the barcode the image's page stated: kept with the approval when the sheet row has none
            page_gtin: String(((candidate.evidence && typeof candidate.evidence === 'object') ? candidate.evidence : {}).page_gtin || ''),
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
            ['search_lane', ctx.search_lane || ''],
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

    // اعتماد رُفعت صورته ولم تُعزل خلفيتها: الشيت يحمل needs_review:<رابط Cloudinary> (main.publish_image)، ولا
    // مرشحات محفوظة. يعيد الرابط، أو '' لغير ذلك
    function bgFailedLink(prod) {
        const raw = String((prod && prod.existing_image_link) || '').trim();
        if (!raw.startsWith('needs_review:')) return '';
        const link = raw.slice('needs_review:'.length).trim();
        return /^https:\/\/res\.cloudinary\.com\//i.test(link) ? link : '';
    }

    // الصورة المعتمدة التي تعرضها الصفحة للمنتج (رابط الشيت، أو الاعتماد المحفوظ، أو اعتماد لم تُعزل خلفيته)
    function shownApprovedUrl(prod) {
        if (!prod) return '';
        if (hasFinalImage(prod)) return String(prod.existing_image_link || prod.cached_image || '').trim();
        return bgFailedLink(prod);
    }

    // ما تعرضه الصفحة عن المنتج (عقد C1: expected_state): حالة صف الطابور ووقت تحديثه ورقمه (queue_row: صف الطابور
    // الذي طابقته الصفحة، وقد يكون صفاً مزاحاً عُرف بـ sku_key) والصورة المعتمدة. الخادم يرفض الاعتماد إذا تغيّر شيء
    // منها (already_approved / state_changed). app.js يأخذ لقطة منها عند فتح المنتج ولا يحدّثها من القراءات الهادئة
    function expectedState(item, approvedLink) {
        const q = item && item.queue;
        const url = String(approvedLink || shownApprovedUrl(item && item.product) || '').trim();
        const row = q ? parseInt(q.row_number, 10) : NaN;
        return {
            queue_status: q && q.status ? String(q.status) : null,
            queue_updated_at: q && q.updated_at ? String(q.updated_at) : null,
            approved_url: url || null,
            queue_row: isFinite(row) && row > 0 ? row : null
        };
    }

    // هل تغيّر ما تعرضه الصفحة عن المنتج بين لقطتين (صف الطابور أو الصورة المعتمدة)؟
    function sameExpected(a, b) {
        a = a || {};
        b = b || {};
        return ['queue_status', 'queue_updated_at', 'approved_url', 'queue_row']
            .every(k => (a[k] === undefined || a[k] === null ? null : String(a[k])) === (b[k] === undefined || b[k] === null ? null : String(b[k])));
    }

    const STALE_CODES = ['already_approved', 'state_changed'];
    const QUEUE_STATUS_TEXT = {
        ready_for_review: 'بانتظار المراجعة', pending: 'في الطابور', processing: 'قيد البحث', failed: 'فيه عطل',
        completed: 'مكتمل'
    };

    function queueText(status) {
        return status ? (QUEUE_STATUS_TEXT[status] || 'حالة غير معروفة') : 'ليس في الطابور';
    }

    // ما تغيّر منذ فتح الصفحة، بالعربي، من رد الخادم (error_code و current) وما أرسلته الصفحة (expected)؛ null لغيره.
    // replaceable=false: لا يُعرض «استبدال المعتمدة»؛ الصورة نفسها رفضها مراجع آخر (reason=image_rejected) والخادم
    // يرفض اعتمادها حتى مع replace
    function staleInfo(data, expected) {
        data = data || {};
        const code = String(data.error_code || '');
        if (!STALE_CODES.includes(code)) return null;
        const cur = data.current && typeof data.current === 'object' && !Array.isArray(data.current) ? data.current : {};
        if (data.reason === 'image_rejected' || cur.rejected_image === true) {
            return { code: code, reason: 'image_rejected', current: cur, approvedUrl: String(cur.approved_url || '').trim(),
                     replaceable: false, text: 'هذه الصورة رفضها مراجع آخر لهذا المنتج بعد فتح الصفحة؛ اختر صورة أخرى.' };
        }
        const exp = expected || {};
        const who = String(cur.approved_for || '').trim();
        const parts = [code === 'already_approved' ? `اعتُمدت لهذا المنتج${who ? ` («${who}»)` : ''} صورة بعد فتح الصفحة`
            : 'تغيّرت حالة المنتج بعد فتح الصفحة'];
        const curUrl = String(cur.approved_url || '').trim();
        const expUrl = String(exp.approved_url || '').trim();
        if (curUrl && curUrl !== expUrl) parts.push(expUrl ? 'الصورة المعتمدة الآن غير التي ظهرت لك' : 'صار له صورة معتمدة');
        else if (!curUrl && expUrl && 'approved_url' in cur) parts.push('الصورة المعتمدة التي ظهرت لك أُلغيت');
        if ('queue_status' in cur && (cur.queue_status || null) !== (exp.queue_status || null)) {
            parts.push(`حالته كانت «${queueText(exp.queue_status)}» وصارت «${queueText(cur.queue_status)}»`);
        }
        return { code: code, current: cur, approvedUrl: curUrl, replaceable: true, text: parts.join('، ') + '.' };
    }

    // ما يُقال عن الشيت بعد الاعتماد (عقد C3: sheet = written | pending | conflict | unknown). لا يُقال «كُتب في الشيت»
    // إلا إذا قال الخادم written
    const SHEET_STATES = {
        written: { tone: 'success', text: 'وكُتب رابطها في الشيت.' },
        pending: { tone: 'warning', text: 'وكتابة رابطها في الشيت بالانتظار: ستُكتب عند توفر الشيت.' },
        conflict: { tone: 'danger', text: 'ولم يُكتب رابطها في الشيت: الخلية تغيّرت وفيها قيمة أخرى. راجع الشيت.' },
        unknown: { tone: 'warning', text: 'ولا نعرف إن كُتب رابطها في الشيت: تأكد من الشيت.' }
    };

    function sheetNote(sheet) {
        const s = SHEET_STATES[String(sheet || '')];
        return s ? { state: String(sheet), tone: s.tone, text: s.text } : { state: '', tone: 'success', text: '' };
    }

    // ما يعرفه الخادم بعد الاعتماد (current في كل استجابة): يصير ما «رأته الصفحة» للاعتماد التالي لنفس المنتج
    function expectedFromCurrent(cur) {
        cur = cur && typeof cur === 'object' ? cur : {};
        const row = parseInt(cur.queue_row, 10);
        return {
            queue_status: cur.queue_status ? String(cur.queue_status) : null,
            queue_updated_at: cur.queue_updated_at ? String(cur.queue_updated_at) : null,
            approved_url: cur.approved_url ? String(cur.approved_url) : null,
            queue_row: isFinite(row) && row > 0 ? row : null
        };
    }

    // علامات فحص القص (quality_flags، image_processor): لماذا لم تُعتبر الخلفية معزولة. الرمز يبقى في التلميح فقط
    const QUALITY_FLAG_LABELS = {
        opaque_fill: 'لم تُزل الخلفية (بقيت الصورة معتمة)',
        opaque_backdrop: 'بقي صندوق خلفية معتم حول المنتج',
        edge_clipped: 'المنتج مقصوص عند حافة الصورة',
        alpha_haze: 'هالة أو ضباب حول حواف المنتج',
        second_object: 'ظهر جسم آخر بجانب المنتج',
        upscaled: 'الصورة المصدر صغيرة فكُبّرت',
        too_small_on_canvas: 'المنتج صغير على اللوحة',
        kept_shadow: 'بقي ظل ظاهر مع المنتج',
        dark_halo: 'حواف فاتحة بتبين على الوضع الغامق',
        photoroom_unsure: 'PhotoRoom مش متأكد من حدود المنتج',
        dark_rim: 'حواف غامقة بتبين على الوضع الفاتح'
    };
    // علامات تخص شكل الصورة المنشورة فقط والخلفية معزولة (main.PRESENTATION_FLAGS): ما تعنيه لمن ينشرها رغمها.
    // opaque_fill و opaque_backdrop و edge_clipped ليست منها: الخلفية لم تُعزل، ولا تُنشر «رغم ذلك» أبداً
    const PRESENTATION_FLAG_TEXT = {
        upscaled: 'الصورة المصدر صغيرة فكُبّرت، وقد تظهر أقل حدة',
        too_small_on_canvas: 'المنتج سيظهر صغيراً على اللوحة البيضاء',
        second_object: 'جسم آخر بجانب المنتج سيُنشر معه',
        alpha_haze: 'هالة أو ضباب خفيف حول حواف المنتج سيظهر في الصورة',
        kept_shadow: 'ظل المنتج سيبقى ظاهراً في الصورة',
        dark_halo: 'حواف فاتحة حول المنتج رح تبين بالتطبيق على الوضع الغامق',
        photoroom_unsure: 'PhotoRoom ما كان متأكد من حدود المنتج: تأكد إنو ما في جزء ناقص أو زيادة',
        dark_rim: 'حواف غامقة حول المنتج رح تبين بالتطبيق على الوضع الفاتح'
    };
    // ملاحظات الفحص غير المانعة (quality_notes): تُعرض ملاحظةً لا تحذيراً، والصورة نُشرت نظيفة
    const QUALITY_NOTE_LABELS = {
        upscaled: 'الصورة المصدر صغيرة فكُبّرت لتملأ اللوحة',
        // انتشرت بلا عزل والخلفية الشفافة مطلوبة (cutout_finish.NOTE_NOT_CUT_OUT): بتضل معتمة بخلفيتها
        not_cut_out: 'الصورة مش مقصوصة: رح تبين بخلفيتها بالتطبيق'
    };

    function qualityFlagText(code) {
        return QUALITY_FLAG_LABELS[String(code || '')] || 'ملاحظة أخرى من فحص القص';
    }

    function qualityNoteText(code) {
        return QUALITY_NOTE_LABELS[String(code || '')] || 'ملاحظة من فحص القص';
    }

    // اعتماد / رفع لم يُنشر لأن القص لم يجتز الفحص (error_code quality_flags أو background_failed): العلامات بالعربي، و
    // allowed=true عندما تخص العرض فقط فيستطيع المراجع نشرها رغمها بعد تأكيد صريح (publish_anyway)؛ null لغيره
    function qualityInfo(data) {
        data = data || {};
        const code = String(data.error_code || '');
        if (code !== 'quality_flags' && code !== 'background_failed') return null;
        const flags = Array.isArray(data.quality_flags) ? data.quality_flags.map(f => String(f)) : [];
        const texts = Array.from(new Set(flags.map(qualityFlagText)));
        // «انشرها رغم ذلك» فقط لعلامات العرض: علامة لا تعرفها الصفحة أو تخص الخلفية لا تُعرض للنشر
        const allowed = code === 'quality_flags' && data.publish_anyway_allowed === true && flags.length > 0
            && flags.every(f => Object.prototype.hasOwnProperty.call(PRESENTATION_FLAG_TEXT, f));
        const anywayTexts = allowed ? Array.from(new Set(flags.map(f => PRESENTATION_FLAG_TEXT[f]))) : [];
        const what = texts.length ? texts.join('، ') : 'لم تُعزل الخلفية';
        const text = allowed
            ? `فحص القص لقى بالصورة: ${what}. ما انعتمدت بعد.`
            : code === 'quality_flags'
                ? `فحص القص لقى بالصورة: ${what}. ما انعتمدت؛ اختار صورة ثانية أو ارفع صورة أوضح.`
                : `ما انعزلت خلفية الصورة (${what}). ما انعتمدت؛ اختار صورة ثانية أو ارفع صورة أوضح.`;
        return { code: code, flags: flags, texts: texts, anywayTexts: anywayTexts, allowed: allowed, text: text };
    }

    // ما يُقال للمراجع بعد اعتماد ناجح: الخلفية (background_not_removed)، الصورة نفسها لمنتج آخر (duplicate_image و
    // duplicate_of)، وعلامات فحص القص
    function approvalNotes(data) {
        data = data || {};
        const list = Array.isArray(data.warnings) ? data.warnings.map(w => String(w)) : (data.warning ? [String(data.warning)] : []);
        const link = String(data.image_link || '');
        const owners = Array.isArray(data.duplicate_of) ? data.duplicate_of : (data.duplicate_of ? [data.duplicate_of] : []);
        const names = owners.map(o => (o && typeof o === 'object')
            ? String(o.product_name || o.sku_key || o.cloudinary_url || '').trim() : String(o || '').trim()).filter(Boolean);
        const flags = Array.isArray(data.quality_flags) ? data.quality_flags.map(f => String(f)) : [];
        const notes = Array.isArray(data.quality_notes) ? data.quality_notes.map(f => String(f)) : [];
        return {
            bgFailed: list.includes('background_not_removed') || link.startsWith('needs_review:'),
            // نُشرت نظيفة رغم علامات العرض بعد تأكيد المراجع (publish_anyway)
            publishedAnyway: data.published_anyway === true || list.includes('quality_flags'),
            duplicate: list.includes('duplicate_image') || owners.length > 0,
            duplicateOf: names,
            flags: flags,
            flagTexts: Array.from(new Set(flags.map(qualityFlagText))),
            noteTexts: Array.from(new Set(notes.map(qualityNoteText))),
            // انتشرت بدون عزل الخلفية لأن المالك أوقفه بالإعدادات (main.publish_image bg_skipped): ملاحظة، لا تحذير
            bgSkipped: data.bg_skipped === true,
            // خلص رصيد مزوّد العزل السحابي فعزلتها طريقة محلية مجانية (main.publish_image bg_fallback): ملاحظة، لا تحذير
            bgFallback: bgFallbackNote(data.bg_fallback)
        };
    }

    // اسم مزوّد العزل السحابي وطريقة العزل المحلية كما يقرؤهما المالك (نفس METHOD_NAMES في publish_check.py)
    const BG_FALLBACK_NAMES = { photoroom: 'PhotoRoom', remove_bg_api: 'remove.bg', removebg: 'remove.bg',
                                rembg: 'rembg', grabcut: 'GrabCut' };

    // bg_fallback من الخادم {provider, from, code} إلى {text, cloud, local}؛ شكل ثاني أو غايب = null
    function bgFallbackNote(value) {
        if (!value || typeof value !== 'object') return null;
        const from = String(value.from || '').trim();
        if (!from) return null;
        const cloud = BG_FALLBACK_NAMES[from] || from;
        const provider = String(value.provider || '').trim();
        const local = BG_FALLBACK_NAMES[provider] || provider;
        return { cloud: cloud, local: local, code: String(value.code || ''),
                 text: `انعزلت الخلفية بطريقة محلية لأن رصيد ${cloud} خلص` };
    }

    // نتيجة الرفض كما قالها الخادم (عقد C2: rejection.queue_status و candidates_left). queue_status: 'pending' =
    // رجع للطابور؛ 'ready_for_review' أو null (لم تتغير) = ما زال بانتظار المراجعة بصوره الباقية. خادم أقدم لا يرسلها:
    // رفض اختيار النظام يعيده للطابور، ورفض غيره لا
    function rejectionOutcome(data, alternative) {
        data = data || {};
        const r = data.rejection && typeof data.rejection === 'object' ? data.rejection : data;
        const kept = !!r.approval_kept;
        const n = parseInt(r.candidates_left, 10);
        let requeued;
        if (r.queue_status !== undefined) requeued = r.queue_status === 'pending';
        else if (typeof r.requeued === 'boolean') requeued = r.requeued;
        else requeued = !alternative;
        return { kept: kept, requeued: requeued, left: isFinite(n) && n >= 0 ? n : null,
                 queueStatus: r.queue_status === undefined ? undefined : (r.queue_status || null) };
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
        // اعتماد لم تُعزل خلفيته: رقاقة خاصة به بدل «نتائج سابقة» الظاهرة في «الكل» فقط
        if (bgFailedLink(prod) && !(Array.isArray(prod.curation_candidates) && prod.curation_candidates.length)) {
            return 'bg_failed';
        }
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
        bg_failed: { text: 'الخلفية لم تُعزل', chip: 'warning' },
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
        proposed: 0, warning: 0, none: 1, bg_failed: 2, not_found: 2, failed: 3, rejected: 4, requeued: 4, searching: 5,
        queued: 6, stale: 6, idle: 7, rejecting: 8, approving: 8, approved: 9
    };

    // رقاقات المراجعة (?filter=): نفس المجموعة بوضع «منتج واحد» و«بالجملة»، والاختيار بيضل لما تبدّل الوضع.
    // eligible: مقترحة من النظام بلا أي تحذير (اللي بينعتمدوا بالجملة)؛ strict: فئة «مؤكدة تماماً» (R.laneOf).
    // waiting: الرقاقة من المنتظرة؛ بوضع الجملة الرقاقات الثانية (ما انلقت، أعطال، الخلفية) بتعرض بطاقات للفتح بس
    const FILTERS = [
        { key: 'all', label: 'الكل', buckets: null, waiting: true },
        { key: 'proposed', label: 'مقترحة', buckets: ['proposed', 'warning'], waiting: true },
        { key: 'eligible', label: 'مقترحة بلا تحذير', test: it => WAITING.includes(it.bucket) && bulkEligible(storedSelected(it.product)),
          waiting: true },
        { key: 'strict', label: 'مؤكدة تماماً', test: it => WAITING.includes(it.bucket) && laneOf(storedSelected(it.product)) === 'strict',
          waiting: true },
        { key: 'warning', label: 'فيها تحذير', buckets: ['warning'], waiting: true },
        { key: 'none', label: 'بلا اقتراح', buckets: ['none'], waiting: true },
        { key: 'not_found', label: 'ما انلقت', buckets: ['not_found'] },
        { key: 'failed', label: 'أعطال', buckets: ['failed'] },
        { key: 'bg_failed', label: 'الخلفية لم تُعزل', buckets: ['bg_failed'] }
    ];

    function filterMatches(f, it) {
        if (f.buckets) return f.buckets.includes(it.bucket);
        return typeof f.test === 'function' ? !!f.test(it) : true;
    }

    // ترتيب المنتظرة حسب الثقة: مقترحة من النظام بلا تحذير، ثم اختيار سابق بلا تحذير، ثم مقترحة فيها تحذير، ثم بلا
    // اقتراح؛ وفي كل درجة منتجات الماركة الواحدة متتالية (ثم رقم الصف)
    function confidenceRank(prod) {
        const sel = storedSelected(prod);
        if (!sel) return 3;
        if (sel.warnings.length) return 2;
        return bulkEligible(sel) ? 0 : 1;
    }

    // مفتاح الترتيب يُحسب مرة لكل منتج (لا لكل مقارنة): الدرجة، ثم الماركة، ثم رقم الصف
    function waitingKey(prod) {
        return { rank: confidenceRank(prod), brand: norm(prod.brand || prod.brand_ar), row: parseInt(prod.row_number, 10) || 0 };
    }

    function compareWaitingKeys(a, b) {
        if (a.rank !== b.rank) return a.rank - b.rank;
        if (a.brand !== b.brand) {
            if (!a.brand || !b.brand) return a.brand ? -1 : 1;     // بلا ماركة آخراً
            return a.brand < b.brand ? -1 : 1;
        }
        return a.row - b.row;
    }

    function compareWaiting(a, b) {
        return compareWaitingKeys(waitingKey(a), waitingKey(b));
    }

    // منتجات بترتيب الثقة ثم الماركة (list: عناصر فيها product)
    function sortWaiting(list) {
        const keys = new Map(list.map(it => [it, waitingKey(it.product)]));
        return list.sort((a, b) => compareWaitingKeys(keys.get(a), keys.get(b)));
    }

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
        const keys = new Map(items.filter(it => WAITING.includes(it.bucket)).map(it => [it, waitingKey(it.product)]));
        items.sort((a, b) => (BUCKET_RANK[a.bucket] - BUCKET_RANK[b.bucket])
            || (keys.has(a) && keys.has(b) ? compareWaitingKeys(keys.get(a), keys.get(b)) : 0)
            || ((parseInt(a.product.row_number, 10) || 0) - (parseInt(b.product.row_number, 10) || 0)));
        return items;
    }

    function countBuckets(items) {
        const c = { all: items.length, proposed: 0, eligible: 0, strict: 0, warning: 0, none: 0, not_found: 0, failed: 0,
                    bg_failed: 0, waiting: 0, approved: 0 };
        const eligible = FILTERS.find(f => f.key === 'eligible');
        const strict = FILTERS.find(f => f.key === 'strict');
        items.forEach(it => {
            if (it.bucket === 'proposed' || it.bucket === 'warning') {
                if (filterMatches(eligible, it)) c.eligible++;
                if (filterMatches(strict, it)) c.strict++;
            }
            if (it.bucket === 'proposed' || it.bucket === 'warning') c.proposed++;
            if (it.bucket === 'warning') c.warning++;
            if (it.bucket === 'none') c.none++;
            if (it.bucket === 'not_found') c.not_found++;
            if (it.bucket === 'failed') c.failed++;
            if (it.bucket === 'bg_failed') c.bg_failed++;
            if (it.bucket === 'approved') c.approved++;
            if (WAITING.includes(it.bucket)) c.waiting++;
        });
        return c;
    }

    // بحث القائمة: الاسم (إنجليزي أو عربي) أو البراند أو الباركود أو رقم الصف أو SKU (sku_key كامل أو بدايته)
    function matchesQuery(item, query) {
        const q = norm(digits(query));
        if (!q) return true;
        const p = item.product;
        if (/^\d+$/.test(q)) {
            if (String(parseInt(p.row_number, 10)) === q) return true;
            if (String(p.barcode || '').replace(/\D/g, '').includes(q)) return true;
        }
        if (q.length >= 4 && norm(p.sku_key).startsWith(q)) return true;
        return [p.product_name, p.product_name_ar, p.brand, p.brand_ar, p.barcode].some(v => norm(v).includes(q));
    }

    // keep: مفاتيح منتجات تبقى في الرقاقة الحالية ما دامت بانتظار المراجعة، أياً كانت مجموعتها الجديدة (منتج رُفضت
    // صورته وأُعيد البحث له فوراً)
    // reason: رقاقة «السبب» (catalog_match.explain): منتجات بلا اقتراح هذا سببها أو هذا ما ينقص صفها بالشيت
    function filterItems(items, filterKey, query, keep, reason) {
        const f = FILTERS.find(x => x.key === filterKey) || FILTERS[0];
        const kept = it => !!(keep && keep.has(it.key)) && WAITING.includes(it.bucket);
        return items.filter(it => (filterMatches(f, it) || kept(it)) && matchesQuery(it, query)
            && (!reason || itemReasonKeys(it).includes(reason)));
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
        if (vlm.decision === 'MISMATCH' && !out.includes('نموذج القراءة شاف منتج مختلف')) out.unshift('نموذج القراءة شاف منتج مختلف');
        if (vlm.decision === 'UNSURE' && !c.warnings.some(w => String(w).split(':')[0] === 'vlm_unsure')) {
            out.push(REVIEW_WARNING_LABELS.vlm_unsure);
        }
        const view = String(vlm.view || '');
        if (view && view !== 'front_packshot') out.push(`الصورة مش لواجهة المنتج (${VIEW_LABELS[view] || 'زاوية ثانية'})`);
        return out;
    }

    // -------------------------------------------------------------------------------------------------
    // No pick: why the engine chose nothing (catalog_match.explain, stored as outcome.explain and served by
    // queue-state, or in a live search's answer) and, under each image, why it was not chosen
    // -------------------------------------------------------------------------------------------------

    // اسم كل سبب في رقاقات «السبب» وفي القائمة (نفس catalog_match.explain.REASON_LABELS)؛ الرمز في التلميح فقط
    const NO_PICK_LABELS = {
        typo: 'غلطة إملائية بالاسم',
        brand_unknown: 'ماركة غير معروفة',
        brand_has_product_word: 'كلمة من الاسم بعمود الماركة',
        no_brand: 'منتج بلا ماركة',
        no_size: 'حجم ناقص بالشيت',
        size_unit_typo: 'وحدة الحجم غلط',
        no_barcode: 'باركود ناقص بالشيت',
        unsure: 'قارئ الملصق غير متأكد',
        verifier_mismatch: 'قارئ الملصق شاف منتج ثاني',
        brand_not_found: 'ولا صفحة بتذكر الماركة',
        weak_only: 'صور ضعيفة بس',
        all_conflicted: 'كل الصور لمنتج ثاني',
        not_found: 'ما انلقت ولا صورة',
        only_social: 'صور تواصل اجتماعي بس',
        download_failed: 'الصور ما تحمّلت',
        verifier_down: 'قارئ الملصق ما اشتغل',
        provider_down: 'البحث ما اشتغل'
    };
    // منتج بلا اقتراح حُفظ بلا سبب (والحساب من المحفوظ لم ينجح بعد): جملة عامة صادقة
    const NO_PICK_FALLBACK = 'النظام ما اختار صورة لهالمنتج، وسببه مش محفوظ. اختر من الصور تحت.';
    // المجموعات التي يُعرض فيها السبب: بانتظار المراجعة بلا اقتراح، وما انلقت
    const NO_PICK_BUCKETS = ['none', 'not_found'];
    const CODE_RE = /^[a-z_]{1,40}$/;

    function noPickLabel(key) {
        return NO_PICK_LABELS[String(key || '')] || 'سبب آخر';
    }

    // السبب كما يصل (queue-state أو رد البحث) بعد التحقق منه، أو null
    function validExplain(raw) {
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
        const key = String(raw.key || '');
        const text = String(raw.text || '').trim();
        if (!CODE_RE.test(key) || !text) return null;
        const sheet = (Array.isArray(raw.sheet) ? raw.sheet : [])
            .filter(s => s && typeof s === 'object' && CODE_RE.test(String(s.key || '')))
            .map(s => ({ key: String(s.key), text: String(s.text || ''), word: String(s.word || ''),
                         suggest: String(s.suggest || ''), known: !!s.known }));
        return { key: key, label: NO_PICK_LABELS[key] || String(raw.label || '') || noPickLabel(key),
                 engine: CODE_RE.test(String(raw.engine || '')) ? String(raw.engine) : '', text: text, sheet: sheet };
    }

    // لماذا لا اقتراح لهذا المنتج: من بحث هذه الجلسة إن وُجد، وإلا مما حفظه العامل لصف طابوره
    function noPickReason(item, search) {
        if (search) return validExplain(search.explain);
        return validExplain(item && item.queue ? item.queue.explain : null);
    }

    // مفاتيح رقاقات «السبب» لسبب واحد: السبب نفسه، ثم ما ينقص صف الشيت (حجم، باركود، ماركة، غلطة إملائية): منتج
    // حجمه ناقص يظهر تحت «حجم ناقص بالشيت» أياً كان سببه، فيُصلح المالك المجموعة كلها مرة واحدة
    function reasonKeys(explain) {
        if (!explain) return [];
        const keys = [explain.key];
        explain.sheet.forEach(s => { if (!keys.includes(s.key)) keys.push(s.key); });
        return keys;
    }

    function itemReasonKeys(item) {
        return item && NO_PICK_BUCKETS.includes(item.bucket) ? reasonKeys(noPickReason(item)) : [];
    }

    // رقاقات «السبب» لعناصر القائمة: [{key, label, count}] الأكثر أولاً
    function reasonCounts(items) {
        const counts = new Map();
        (items || []).forEach(it => itemReasonKeys(it).forEach(k => counts.set(k, (counts.get(k) || 0) + 1)));
        return Array.from(counts, ([key, count]) => ({ key: key, count: count, label: noPickLabel(key) }))
            .sort((a, b) => b.count - a.count || (a.label < b.label ? -1 : a.label > b.label ? 1 : 0));
    }

    // ملاحظات الشيت غير السبب نفسه، بجملة قصيرة لكل واحدة
    function sheetNotes(explain) {
        if (!explain) return [];
        return explain.sheet.filter(s => s.key !== explain.key || s.known).map(s => s.text).filter(Boolean);
    }

    function tierOf(c) {
        const n = parseInt(String(c && c.identity_tier !== null && c.identity_tier !== undefined ? c.identity_tier : '').replace(/^t/i, ''), 10);
        return isFinite(n) ? n : null;
    }

    // «لماذا لم تُختر»: سطر واحد تحت كل صورة لمنتج بلا اقتراح، من أدلتها كما حسبها المحرك (قراءة الملصق، ذكر
    // الماركة، الحجم في الصفحة، المتجر). { code, text, tone }، أو null لصورة أضافها المراجع
    function whyNotPicked(c, prod) {
        if (!c || c.source === 'manual' || c.source === 'upload') return null;
        if (c.status === 'excluded' || c.status === 'rejected') {
            return { code: c.status, text: candidateNote(c, false).text, tone: 'danger' };
        }
        const ev = c.evidence || {};
        const vlm = c.vlm || {};
        const decision = String(vlm.decision || '');
        const tier = tierOf(c);
        const warnings = (c.warnings || []).map(w => String(w).split(':')[0]);
        const sizeOnPage = ev.size === 'match' || ev.size === true || ev.gtin === 'match';
        const sheetSize = sheetStates(prod).size;
        const sizeNote = !sheetSize ? 'والشيت ما فيه حجم' : (sizeOnPage ? '' : 'والحجم غير مكتوب في الصفحة');
        const withSize = text => (sizeNote ? `${text}، ${sizeNote}` : text);
        if (decision === 'MISMATCH') return { code: 'vlm_mismatch', text: 'قارئ الملصق شاف منتج ثاني', tone: 'danger' };
        if (tier === 3 || ev.brand === false) return { code: 'no_brand', text: 'الصفحة ما بتذكر الماركة', tone: 'warning' };
        if (decision === 'UNSURE') return { code: 'vlm_unsure', text: withSize('قارئ الملصق ما تأكد'), tone: 'warning' };
        if (!decision || decision === 'UNKNOWN') return { code: 'vlm_unread', text: withSize('قارئ الملصق ما قرأها'), tone: 'warning' };
        if (warnings.includes('barcode_conflict')) return { code: 'barcode_conflict', text: 'باركود الصفحة مختلف عن الشيت', tone: 'warning' };
        if (warnings.includes('foreign_store')) return { code: 'foreign_store', text: 'متجر خارج الإمارات', tone: 'warning' };
        if (warnings.includes('social_media')) return { code: 'social_media', text: 'صورة من مواقع التواصل', tone: 'warning' };
        return { code: 'not_confident', text: 'ما وصلت للثقة اللي بتخلينا نختارها لحالنا', tone: 'muted' };
    }

    Object.assign(R, {
        NO_PICK_LABELS, NO_PICK_FALLBACK, NO_PICK_BUCKETS, noPickLabel, validExplain, noPickReason, reasonKeys,
        itemReasonKeys, reasonCounts, sheetNotes, whyNotPicked
    });

    // فئة اختيار المحرك (catalog_match.decide.lane_of): strict | unsure | other، من سبب lane:<name> المحفوظ مع الاختيار،
    // ولاختيار حُفظ قبل الفئات من preselected: و auto_blocked: (نفس قاعدة بايثون). strict لا تجتمع مع تحذير مراجعة.
    // null لما ليس اختيار المحرك: مرشح عادي، أو اعتماد سابق من الكاش
    const LANE_TEXT = { strict: 'مؤكدة تماماً', unsure: 'الملصق مش واضح' };
    const LANE_TITLE = {
        strict: 'اقتراح عدّى كل قواعد النشر الآلي بلا أي تحذير',
        unsure: 'الملصق مش واضح بالصورة، بس اسم المنتج بالصفحة مطابق'
    };
    const LANE_SETTING_BLOCKERS = ['auto_publish_disabled', 'auto_publish_off_for_brand'];

    function laneOf(c) {
        if (!c || c.status !== 'preselected' || c.is_selected === 0 || c.source === 'cache') return null;
        const reasons = (c.reasons || []).map(r => String(r));
        if (reasons.includes('cache_hit')) return null;
        let lane = null;
        const named = reasons.find(r => r.startsWith('lane:') && ['strict', 'unsure', 'other'].includes(r.slice(5)));
        if (named) {
            lane = named.slice(5);
        } else {
            const why = reasons.find(r => r.startsWith('preselected:'));
            if (!why) return null;
            const blockers = reasons.filter(r => r.startsWith('auto_blocked:')).map(r => r.slice(13));
            lane = blockers.every(b => LANE_SETTING_BLOCKERS.includes(b) || b.startsWith('brand_conf_')) ? 'strict'
                : (why.slice(12) === 'tier1_unsure' ? 'unsure' : 'other');
        }
        return lane === 'strict' && c.warnings && c.warnings.length ? 'other' : lane;
    }

    Object.assign(R, { LANE_TEXT, LANE_TITLE, laneOf });

    Object.assign(R, {
        REVIEW_WARNING_LABELS, VARIANT_AXIS_LABELS, REJECT_REASONS, COSMETIC_REASONS, FAILURE_TEXT, NOT_FOUND_CODES,
        VIEW_LABELS, BUCKET_LABELS, FILTERS, WAITING, PRODUCT_CHANGED, STALE_CODES,
        warningText, reasonLabel, rejectReasonsFor, UNDO_REJECT_LABEL, UNDO_REJECT_CONFIRM, rejectedImages, undoRejectBody,
        forgetRejection, PAGE_GTIN_LABEL, sheetLacksBarcode, pageGtinOf, GALLERY_NOTE, galleryNote, failureInfo, plainError, hostOf, marketOf, storeMarket, storeOf,
        sheetStates, unverifiedWarnings, PRESENTATION_FLAG_TEXT, BG_SKIP_RE, bgSkipCode, bgFallbackNote,
        normalizeCandidate, collectCandidates, storedCandidates, storedSelected, bulkEligible, candidateNote, explainPick,
        productIdentity, sameProduct, itemKey, failureKey, reviewedCandidateView,
        searchBody, selectBody, rejectBody, uploadFields,
        matchQueue, classify, hasFinalImage, bgFailedLink, shownApprovedUrl, expectedState, sameExpected, staleInfo, queueText,
        sheetNote, expectedFromCurrent, qualityFlagText, qualityNoteText, qualityInfo, approvalNotes, rejectionOutcome,
        confidenceRank, compareWaiting, sortWaiting, buildItems, countBuckets, matchesQuery, filterItems, filterMatches,
        sizeText, categoryPath, factsFor, checksFor, cautionsFor
    });

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = R;
    }
})(typeof window !== 'undefined' ? window : globalThis);
