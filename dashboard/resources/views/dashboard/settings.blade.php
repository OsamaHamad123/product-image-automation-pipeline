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

        @php
            // المفاتيح السرية لا تُطبع أبداً: الحقل فارغ ويظهر آخر 4 أحرف فقط، والحقل الفارغ يُبقي القيمة المحفوظة
            $secretField = function (string $key) use ($masked) {
                $mask = $masked[$key] ?? '';
                return $mask !== '' ? 'محفوظ: ' . $mask . ' — اتركه فارغاً للإبقاء عليه' : 'غير مضبوط';
            };
        @endphp
        <form action="{{ route('dashboard.save_settings') }}" method="POST" autocomplete="off">
            @csrf

            <div class="settings-grid">

                <!-- Section 1: PhotoRoom & Gemini -->
                <div>
                    <div class="glass-panel" style="padding: 1.5rem; margin-bottom: 0;">
                        <h3 class="settings-section-title">
                            <i class="fas fa-eye" style="color: var(--text-secondary);"></i>
                            <span>إزالة الخلفية والتحقق البصري</span>
                        </h3>

                        <!-- PhotoRoom API Key -->
                        <div class="form-group">
                            <label class="form-label" for="photoroom_api_key">مفتاح API الخاص بـ PhotoRoom</label>
                            <input type="password" id="photoroom_api_key" name="photoroom_api_key" class="form-control" value="" autocomplete="new-password" placeholder="{{ $secretField('photoroom_api_key') }}">
                            <label class="form-help"><input type="checkbox" name="clear_photoroom_api_key" value="1"> مسح المفتاح المحفوظ</label>
                            <span class="form-help">يستخدم لعزل خلفية المنتج قبل وضعه على اللوحة البيضاء. عند فشل العزل لا تُنشر الصورة الخام بصمت بل تُعلَّم للمراجعة.</span>
                        </div>

                        <!-- Gemini API Key -->
                        <div class="form-group">
                            <label class="form-label" for="gemini_api_key">مفتاح API الخاص بـ Google Gemini</label>
                            <input type="password" id="gemini_api_key" name="gemini_api_key" class="form-control" value="" autocomplete="new-password" placeholder="{{ $secretField('gemini_api_key') }}">
                            <label class="form-help"><input type="checkbox" name="clear_gemini_api_key" value="1"> مسح المفتاح المحفوظ</label>
                            <span class="form-help">يستخدم للتحقق البصري المقارن (البراند، النوع، الحجم). عند تعذر الاتصال لا يُعتمد أي شيء تلقائياً.</span>
                        </div>

                        <!-- Gemini Model -->
                        <div class="form-group">
                            <label class="form-label" for="gemini_model">نموذج Gemini للتحقق البصري</label>
                            <select id="gemini_model" name="gemini_model" class="form-control">
                                @foreach($geminiModels as $modelId => $modelLabel)
                                    <option value="{{ $modelId }}" {{ $settings['gemini_model'] === $modelId ? 'selected' : '' }}>{{ $modelLabel }}</option>
                                @endforeach
                            </select>
                            @if(!array_key_exists($settings['gemini_model'], $geminiModels))
                                <span class="form-help" style="color: var(--danger); font-weight: bold;">النموذج المحفوظ حالياً ({{ $settings['gemini_model'] }}) متقاعد أو غير مدعوم؛ اختر نموذجاً من القائمة واحفظ.</span>
                            @endif
                            <span class="form-help">النماذج المتقاعدة أزيلت من القائمة.</span>
                        </div>
                    </div>
                </div>

                <!-- Section 2: Search engines & Cloudinary -->
                <div>
                    <div class="glass-panel" style="padding: 1.5rem; margin-bottom: 0;">
                        <h3 class="settings-section-title">
                            <i class="fas fa-search" style="color: var(--text-secondary);"></i>
                            <span>محركات البحث</span>
                        </h3>

                        <!-- Search engine selector -->
                        <div class="form-group">
                            <label class="form-label" for="search_engine">محرك اختيار الصور (SEARCH_ENGINE)</label>
                            <select id="search_engine" name="search_engine" class="form-control">
                                <option value="v2" {{ $settings['search_engine'] === 'v2' ? 'selected' : '' }}>v2 — مطابقة الهوية والتحقق (افتراضي)</option>
                                <option value="v1" {{ $settings['search_engine'] === 'v1' ? 'selected' : '' }}>v1 — المحرك القديم (للتراجع المؤقت فقط)</option>
                            </select>
                        </div>

                        <!-- Serper API Key -->
                        <div class="form-group">
                            <label class="form-label" for="serper_api_key">مفتاح Serper.dev (SERPER_API_KEY)</label>
                            <input type="password" id="serper_api_key" name="serper_api_key" class="form-control" value="" autocomplete="new-password" placeholder="{{ $secretField('serper_api_key') }}">
                            <label class="form-help"><input type="checkbox" name="clear_serper_api_key" value="1"> مسح المفتاح المحفوظ</label>
                            <span class="form-help">المصدر الأساسي لنتائج صور Google (الإمارات، عربي وإنجليزي) مع عنوان ورابط صفحة المنتج. بدونه يُستخدم بديل Bing محدود ولا يُسمح بالنشر التلقائي.</span>
                        </div>

                        <!-- Google Search API Keys (legacy) -->
                        <div class="form-group">
                            <label class="form-label" for="google_search_api_key">مفاتيح Google Custom Search (قديم، مفصولة بفاصلة)</label>
                            <input type="password" id="google_search_api_key" name="google_search_api_key" class="form-control" value="" autocomplete="new-password" placeholder="{{ $secretField('google_search_api_key') }}">
                            <label class="form-help"><input type="checkbox" name="clear_google_search_api_key" value="1"> مسح المفاتيح المحفوظة</label>
                            <span class="form-help">يعمل فقط إذا كان مضبوطاً مسبقاً، ويتوقف بعد 2026-12-31.</span>
                        </div>

                        <!-- Google Search CX -->
                        <div class="form-group">
                            <label class="form-label" for="google_search_cx">معرف محرك البحث المخصص (CX)</label>
                            <input type="text" id="google_search_cx" name="google_search_cx" class="form-control" value="{{ $settings['google_search_cx'] }}">
                        </div>

                        <!-- Proxy URL -->
                        <div class="form-group">
                            <label class="form-label" for="proxy_url">عنوان خادم البروكسي (PROXY_URL)</label>
                            <input type="password" id="proxy_url" name="proxy_url" class="form-control" value="" autocomplete="new-password" placeholder="{{ $masked['proxy_url'] !== '' ? $secretField('proxy_url') : 'http://username:password@ip:port' }}">
                            <label class="form-help"><input type="checkbox" name="clear_proxy_url" value="1"> مسح العنوان المحفوظ</label>
                            <span class="form-help">اختياري: لتمرير طلبات بديل Bing عند الحظر.</span>
                        </div>
                    </div>
                </div>

            </div>

            <div class="settings-grid" style="margin-top: 2rem;">
                <!-- Section 3: Cloudinary -->
                <div>
                    <div class="glass-panel" style="padding: 1.5rem; margin-bottom: 0;">
                        <h3 class="settings-section-title">
                            <i class="fas fa-cloud-upload-alt" style="color: var(--text-secondary);"></i>
                            <span>التخزين السحابي (Cloudinary)</span>
                        </h3>

                        <!-- Cloudinary Name -->
                        <div class="form-group">
                            <label class="form-label" for="cloudinary_cloud_name">اسم الحساب السحابي لـ Cloudinary</label>
                            <input type="text" id="cloudinary_cloud_name" name="cloudinary_cloud_name" class="form-control" value="{{ $settings['cloudinary_cloud_name'] }}">
                        </div>

                        <!-- Cloudinary API Key -->
                        <div class="form-group">
                            <label class="form-label" for="cloudinary_api_key">مفتاح API الخاص بـ Cloudinary</label>
                            <input type="password" id="cloudinary_api_key" name="cloudinary_api_key" class="form-control" value="" autocomplete="new-password" placeholder="{{ $secretField('cloudinary_api_key') }}">
                            <label class="form-help"><input type="checkbox" name="clear_cloudinary_api_key" value="1"> مسح المفتاح المحفوظ</label>
                        </div>

                        <!-- Cloudinary API Secret -->
                        <div class="form-group">
                            <label class="form-label" for="cloudinary_api_secret">الرمز السري لـ Cloudinary (API Secret)</label>
                            <input type="password" id="cloudinary_api_secret" name="cloudinary_api_secret" class="form-control" value="" autocomplete="new-password" placeholder="{{ $secretField('cloudinary_api_secret') }}">
                            <label class="form-help"><input type="checkbox" name="clear_cloudinary_api_secret" value="1"> مسح الرمز المحفوظ</label>
                        </div>
                    </div>
                </div>

                <!-- Section 4: Auto-publish policy -->
                <div>
                    <div class="glass-panel" style="padding: 1.5rem; margin-bottom: 0;">
                        <h3 class="settings-section-title">
                            <i class="fas fa-user-check" style="color: var(--text-secondary);"></i>
                            <span>سياسة النشر والمطابقة</span>
                        </h3>

                        <!-- Auto-publish toggle -->
                        <div style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1.25rem;">
                            <input type="checkbox" id="auto_publish_enabled" name="auto_publish_enabled" value="true" style="width: 20px; height: 20px; cursor: pointer;" {{ $settings['auto_publish_enabled'] === 'true' ? 'checked' : '' }}>
                            <div>
                                <label class="form-label" for="auto_publish_enabled" style="cursor: pointer; margin: 0;">النشر التلقائي للموثق فقط (AUTO_PUBLISH_ENABLED — معطل افتراضياً)</label>
                                <span class="form-help" style="display: block; margin-top: 0.2rem;">يُنشر تلقائياً فقط ما تحقق بالكامل (GTIN أو براند+حجم+نوع، تحقق بصري MATCH، مصدر معتمد، براند معرّف في جدول البراندات) للبراندات المسموح بها أدناه. كل ما عدا ذلك يذهب للمراجعة البشرية.</span>
                            </div>
                        </div>

                        <!-- Auto-publish brand allow-list -->
                        <div class="form-group">
                            <label class="form-label" for="auto_publish_brands">البراندات المسموح نشرها تلقائياً (AUTO_PUBLISH_BRANDS)</label>
                            <input type="text" id="auto_publish_brands" name="auto_publish_brands" class="form-control" value="{{ $settings['auto_publish_brands'] }}" placeholder="مثال: Almarai, Al Rawabi">
                        </div>

                        <!-- Strict Brand Match Toggle -->
                        <div style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1.25rem;">
                            <input type="checkbox" id="strict_brand_match" name="strict_brand_match" value="true" style="width: 20px; height: 20px; cursor: pointer;" {{ $settings['strict_brand_match'] === 'true' ? 'checked' : '' }}>
                            <div>
                                <label class="form-label" for="strict_brand_match" style="cursor: pointer; margin: 0;">مطابقة البراند الصارمة (Strict Brand Match)</label>
                                <span class="form-help" style="display: block; margin-top: 0.2rem;">يرفض الصور التي تظهر فيها علامة تجارية منافسة دون البراند المطلوب.</span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Section 5: Legacy v1 engine toggles -->
            <div class="glass-panel" style="padding: 1.5rem; margin-top: 2rem; margin-bottom: 0;">
                <h3 class="settings-section-title">
                    <i class="fas fa-history" style="color: var(--text-secondary);"></i>
                    <span>خيارات المحرك القديم v1 (تعمل فقط عند اختيار v1)</span>
                </h3>

                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem 2rem;">
                    <!-- Gemini Pre-Validation Toggle -->
                    <div style="display: flex; align-items: center; gap: 0.75rem;">
                        <input type="checkbox" id="enable_gemini_pre_validation" name="enable_gemini_pre_validation" value="true" style="width: 20px; height: 20px; cursor: pointer;" {{ $settings['enable_gemini_pre_validation'] === 'true' ? 'checked' : '' }}>
                        <label class="form-label" for="enable_gemini_pre_validation" style="cursor: pointer; margin: 0;">تحقق Gemini المسبق (v1)</label>
                    </div>

                    <!-- Filter Competitors Toggle -->
                    <div style="display: flex; align-items: center; gap: 0.75rem;">
                        <input type="checkbox" id="filter_competitors" name="filter_competitors" value="true" style="width: 20px; height: 20px; cursor: pointer;" {{ $settings['filter_competitors'] === 'true' ? 'checked' : '' }}>
                        <label class="form-label" for="filter_competitors" style="cursor: pointer; margin: 0;">فلترة البراندات المنافسة (v1)</label>
                    </div>

                    <!-- Bypass White Background Check Toggle -->
                    <div style="display: flex; align-items: center; gap: 0.75rem;">
                        <input type="checkbox" id="bypass_white_background_check" name="bypass_white_background_check" value="true" style="width: 20px; height: 20px; cursor: pointer;" {{ $settings['bypass_white_background_check'] === 'true' ? 'checked' : '' }}>
                        <label class="form-label" for="bypass_white_background_check" style="cursor: pointer; margin: 0;">تخطي فحص الخلفية البيضاء (v1)</label>
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
