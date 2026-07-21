@extends('layouts.layout')

@section('title', '⚙️ إعدادات ومفاتيح الـ API')
@section('nav_settings', 'active')

@section('styles')
<style>
    .settings-container {
        direction: rtl;
        text-align: right;
    }

    .settings-section-title {
        font-family: 'Tajawal', sans-serif;
        font-weight: 900;
        color: var(--text-primary);
        font-size: 1.25rem;
        margin-bottom: 1.5rem;
        display: flex;
        align-items: center;
        gap: 0.75rem;
        border-bottom: 1px solid var(--panel-border);
        padding-bottom: 0.75rem;
    }

    .form-group {
        margin-bottom: 1.5rem;
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
    }

    .form-label {
        font-family: 'Tajawal', sans-serif;
        font-weight: 700;
        font-size: 0.95rem;
        color: var(--text-primary);
    }

    .form-control {
        background: var(--input-bg);
        border: 1px solid var(--panel-border);
        border-radius: var(--border-radius-sm);
        padding: 0.85rem 1rem;
        color: var(--text-primary);
        font-family: inherit;
        font-size: 0.95rem;
        transition: all 0.3s ease;
        width: 100%;
        box-shadow: var(--shadow-sm);
    }

    .form-control:focus {
        border-color: var(--accent-purple);
        outline: none;
        box-shadow: 0 0 10px rgba(255, 255, 255, 0.05);
    }

    .form-help {
        font-size: 0.8rem;
        color: var(--text-secondary);
        line-height: 1.4;
    }

    .alert {
        padding: 1rem 1.5rem;
        border-radius: var(--border-radius-sm);
        margin-bottom: 1.5rem;
        font-family: 'Tajawal', sans-serif;
        font-weight: 700;
        display: flex;
        align-items: center;
        gap: 0.75rem;
    }

    .alert-success {
        background: rgba(34, 197, 94, 0.1);
        color: #22c55e;
        border: 1px solid rgba(34, 197, 94, 0.2);
    }

    .alert-danger {
        background: rgba(239, 68, 68, 0.1);
        color: #ef4444;
        border: 1px solid rgba(239, 68, 68, 0.2);
    }

    .settings-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 2rem;
    }

    @media (max-width: 992px) {
        .settings-grid {
            grid-template-columns: 1fr;
        }
    }

    .action-bar {
        display: flex;
        justify-content: flex-start;
        gap: 1rem;
        margin-top: 2rem;
        padding-top: 1.5rem;
        border-top: 1px solid var(--panel-border);
    }
</style>
@endsection

@section('content')
<div class="settings-container">
    <div class="glass-panel">
        <h1 style="font-family: 'Tajawal', sans-serif; font-weight: 900; color: var(--text-primary); margin-bottom: 0.5rem; font-size: 2.2rem; display: flex; align-items: center; gap: 0.75rem;">
            <i class="fas fa-sliders-h" style="color: var(--accent-cyan);"></i>
            <span>إعدادات النظام البرمجية ومفاتيح الـ API</span>
        </h1>
        <p style="color: var(--text-secondary); margin-bottom: 2rem; font-size: 0.95rem;">
            قم بضبط وتحديث مفاتيح الاتصال بالخدمات السحابية. يتم حفظ البيانات بأمان في قاعدة بيانات MariaDB الخاصة بالأتمتة، ولن تحتاج لتسجيل الدخول إلى الخادم (RDP) أو تعديل ملفات البيئة يدوياً مرة أخرى.
        </p>

        @if(session('success'))
            <div class="alert alert-success">
                <i class="fas fa-check-circle"></i>
                <span>{{ session('success') }}</span>
            </div>
        @endif

        @if(session('error'))
            <div class="alert alert-danger">
                <i class="fas fa-exclamation-circle"></i>
                <span>{{ session('error') }}</span>
            </div>
        @endif

        <form action="{{ route('dashboard.save_settings') }}" method="POST">
            @csrf
            
            <div class="settings-grid">
                
                <!-- Section 1: PhotoRoom & Gemini -->
                <div>
                    <div class="glass-panel" style="padding: 1.5rem; margin-bottom: 0;">
                        <h3 class="settings-section-title">
                            <i class="fas fa-eye" style="color: var(--text-secondary);"></i>
                            <span>إزالة الخلفية والذكاء الاصطناعي البصري</span>
                        </h3>

                        <!-- PhotoRoom API Key -->
                        <div class="form-group">
                            <label class="form-label" for="photoroom_api_key">مفتاح API الخاص بـ PhotoRoom</label>
                            <input type="text" id="photoroom_api_key" name="photoroom_api_key" class="form-control" value="{{ $settings['photoroom_api_key'] }}" placeholder="sk_pr_..." required>
                            <span class="form-help">يستخدم لإزالة الخلفيات وتوحيد مقاسات الصور وتوسيطها تلقائياً. تأكد من توفر رصيد بالاشتراك الخاص بك.</span>
                        </div>

                        <!-- Gemini API Key -->
                        <div class="form-group">
                            <label class="form-label" for="gemini_api_key">مفتاح API الخاص بـ Google Gemini</label>
                            <input type="password" id="gemini_api_key" name="gemini_api_key" class="form-control" value="{{ $settings['gemini_api_key'] }}" placeholder="أدخل مفتاح Gemini..." required>
                            <span class="form-help">يستخدم للتحقق البصري الدقيق واستبعاد المنتجات الخاطئة والمنافسة ومطابقة الحجم والنكهة عبر الـ VLM.</span>
                        </div>

                        <!-- Gemini Model -->
                        <div class="form-group">
                            <label class="form-label" for="gemini_model">نموذج الذكاء الاصطناعي لـ Gemini</label>
                            <select id="gemini_model" name="gemini_model" class="form-control">
                                <option value="gemini-3.5-flash" {{ $settings['gemini_model'] === 'gemini-3.5-flash' ? 'selected' : '' }}>gemini-3.5-flash (أحدث نموذج قياسي دقيق وسريع)</option>
                                <option value="gemini-2.5-flash" {{ $settings['gemini_model'] === 'gemini-2.5-flash' ? 'selected' : '' }}>gemini-2.5-flash (نموذج مستقر)</option>
                                <option value="gemini-3.1-flash-lite" {{ $settings['gemini_model'] === 'gemini-3.1-flash-lite' ? 'selected' : '' }}>gemini-3.1-flash-lite (اقتصادي للغاية ومناسب للفرز البصري)</option>
                                <option value="gemini-2.0-flash-lite" {{ $settings['gemini_model'] === 'gemini-2.0-flash-lite' ? 'selected' : '' }}>gemini-2.0-flash-lite (سريع وموفر للميزانية)</option>
                            </select>
                            <span class="form-help">اختر النموذج الأنسب لكفاءة الأداء والميزانية المتاحة لحساب Google AI Studio الخاص بك.</span>
                        </div>
                    </div>
                </div>

                <!-- Section 2: Cloudinary & Google Search -->
                <div>
                    <div class="glass-panel" style="padding: 1.5rem; margin-bottom: 0;">
                        <h3 class="settings-section-title">
                            <i class="fas fa-cloud-upload-alt" style="color: var(--text-secondary);"></i>
                            <span>التخزين السحابي ومحركات البحث</span>
                        </h3>

                        <!-- Cloudinary Name -->
                        <div class="form-group">
                            <label class="form-label" for="cloudinary_cloud_name">اسم الحساب السحابي لـ Cloudinary</label>
                            <input type="text" id="cloudinary_cloud_name" name="cloudinary_cloud_name" class="form-control" value="{{ $settings['cloudinary_cloud_name'] }}" required>
                        </div>

                        <!-- Cloudinary API Key -->
                        <div class="form-group">
                            <label class="form-label" for="cloudinary_api_key">مفتاح API الخاص بـ Cloudinary</label>
                            <input type="text" id="cloudinary_api_key" name="cloudinary_api_key" class="form-control" value="{{ $settings['cloudinary_api_key'] }}" required>
                        </div>

                        <!-- Cloudinary API Secret -->
                        <div class="form-group">
                            <label class="form-label" for="cloudinary_api_secret">الرمز السري لـ Cloudinary (API Secret)</label>
                            <input type="password" id="cloudinary_api_secret" name="cloudinary_api_secret" class="form-control" value="{{ $settings['cloudinary_api_secret'] }}" required>
                        </div>

                        <!-- Google Search API Keys -->
                        <div class="form-group">
                            <label class="form-label" for="google_search_api_key">مفاتيح Google Custom Search (مفصولة بفاصلة)</label>
                            <input type="text" id="google_search_api_key" name="google_search_api_key" class="form-control" value="{{ $settings['google_search_api_key'] }}" placeholder="مفتاح 1, مفتاح 2...">
                            <span class="form-help">يمكنك إدخال مفتاح واحد أو عدة مفاتيح مفصولة بفاصلة للتدوير التلقائي عند نفاد الحصة اليومية.</span>
                        </div>

                        <!-- Google Search CX -->
                        <div class="form-group">
                            <label class="form-label" for="google_search_cx">معرف محرك البحث المخصص (CX)</label>
                            <input type="text" id="google_search_cx" name="google_search_cx" class="form-control" value="{{ $settings['google_search_cx'] }}">
                            <span class="form-help">معرف محرك البحث المخصص من لوحة تحكم Google Programmable Search.</span>
                        </div>

                        <!-- Proxy URL -->
                        <div class="form-group">
                            <label class="form-label" for="proxy_url">عنوان خادم البروكسي (PROXY_URL)</label>
                            <input type="text" id="proxy_url" name="proxy_url" class="form-control" value="{{ $settings['proxy_url'] }}" placeholder="http://username:password@ip:port">
                            <span class="form-help">يستخدم لتشفير وتمرير طلبات البحث المدمجة (Yandex و Bing و DuckDuckGo) لتجنب حظر الـ IP والـ CAPTCHA على السيرفر.</span>
                        </div>
                    </div>
                </div>

            </div>

            <!-- Section 3: AI Quality Gatekeeper & Rules (Full Width) -->
            <div class="glass-panel" style="padding: 1.5rem; margin-top: 2rem; margin-bottom: 0;">
                <h3 class="settings-section-title">
                    <i class="fas fa-filter" style="color: var(--text-secondary);"></i>
                    <span>معايير جودة الفرز ومطابقة المنتجات بالذكاء الاصطناعي (AI Quality Rules)</span>
                </h3>
                
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 2rem;">
                    <div>
                        <!-- CLIP Relevance Threshold -->
                        <div class="form-group">
                            <label class="form-label" for="clip_relevance_threshold">حد صلة قرابة الصورة (CLIP Relevance Threshold)</label>
                            <input type="number" step="0.01" min="0" max="1" id="clip_relevance_threshold" name="clip_relevance_threshold" class="form-control" value="{{ $settings['clip_relevance_threshold'] }}" required>
                            <span class="form-help">القيمة الافتراضية 0.22. الحد الأدنى لمستوى تشابه وتطابق الصورة دلالياً مع اسم منتجك لقبولها.</span>
                        </div>

                        <!-- CLIP Grey Zone Threshold -->
                        <div class="form-group">
                            <label class="form-label" for="clip_grey_zone_threshold">حد المنطقة الرمادية (CLIP Grey Zone Threshold)</label>
                            <input type="number" step="0.01" min="0" max="1" id="clip_grey_zone_threshold" name="clip_grey_zone_threshold" class="form-control" value="{{ $settings['clip_grey_zone_threshold'] }}" required>
                            <span class="form-help">القيمة الافتراضية 0.18. الصور الأقل من هذا الحد يتم رفضها فوراً دون إرسالها لـ Gemini لتوفير التكلفة.</span>
                        </div>
                    </div>
                    
                    <div style="display: flex; flex-direction: column; gap: 1rem; justify-content: center;">
                        <!-- Strict Brand Match Toggle -->
                        <div style="display: flex; align-items: center; gap: 0.75rem;">
                            <input type="checkbox" id="strict_brand_match" name="strict_brand_match" value="true" style="width: 20px; height: 20px; cursor: pointer;" {{ $settings['strict_brand_match'] === 'true' ? 'checked' : '' }}>
                            <div>
                                <label class="form-label" for="strict_brand_match" style="cursor: pointer; margin: 0;">تفعيل فحص مطابقة البراند الصارم (Strict Brand Match)</label>
                                <span class="form-help" style="display: block; margin-top: 0.2rem;">يرفض الصور التي لا تظهر فيها العلامة التجارية المطلوبة بشكل صريح أو تتبع لشركات منافسة.</span>
                            </div>
                        </div>

                        <!-- Gemini Pre-Validation Toggle -->
                        <div style="display: flex; align-items: center; gap: 0.75rem;">
                            <input type="checkbox" id="enable_gemini_pre_validation" name="enable_gemini_pre_validation" value="true" style="width: 20px; height: 20px; cursor: pointer;" {{ $settings['enable_gemini_pre_validation'] === 'true' ? 'checked' : '' }}>
                            <div>
                                <label class="form-label" for="enable_gemini_pre_validation" style="cursor: pointer; margin: 0;">تفعيل التحقق البصري الدقيق عبر Gemini Vision VLM</label>
                                <span class="form-help" style="display: block; margin-top: 0.2rem;">يقوم بفحص النكهة، الحجم، العبوة، وتطابق تفاصيل الغلاف مع المنتج.</span>
                            </div>
                        </div>

                        <!-- Filter Competitors Toggle -->
                        <div style="display: flex; align-items: center; gap: 0.75rem;">
                            <input type="checkbox" id="filter_competitors" name="filter_competitors" value="true" style="width: 20px; height: 20px; cursor: pointer;" {{ $settings['filter_competitors'] === 'true' ? 'checked' : '' }}>
                            <div>
                                <label class="form-label" for="filter_competitors" style="cursor: pointer; margin: 0;">فلترة المنتجات والبراندات المنافسة تلقائياً</label>
                                <span class="form-help" style="display: block; margin-top: 0.2rem;">يتعرف على الماركات المنافسة الشهيرة ويستبعدها لمنع تداخل المنتجات.</span>
                            </div>
                        </div>

                        <!-- Bypass White Background Check Toggle -->
                        <div style="display: flex; align-items: center; gap: 0.75rem;">
                            <input type="checkbox" id="bypass_white_background_check" name="bypass_white_background_check" value="true" style="width: 20px; height: 20px; cursor: pointer;" {{ $settings['bypass_white_background_check'] === 'true' ? 'checked' : '' }}>
                            <div>
                                <label class="form-label" for="bypass_white_background_check" style="cursor: pointer; margin: 0;">تخطي إزالة الخلفية إذا كانت بيضاء ونقية بالفعل</label>
                                <span class="form-help" style="display: block; margin-top: 0.2rem;">يوفر استهلاك رصيد PhotoRoom للصور الجاهزة ذات الخلفية البيضاء النقية.</span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="action-bar">
                <button type="submit" class="btn">
                    <i class="fas fa-save"></i>
                    <span>حفظ وتطبيق التغييرات</span>
                </button>
                <a href="{{ route('dashboard.index') }}" class="btn" style="background: transparent; border: 1px solid var(--panel-border); color: var(--text-secondary);">
                    <span>إلغاء</span>
                </a>
            </div>
        </form>
    </div>
</div>
@endsection
