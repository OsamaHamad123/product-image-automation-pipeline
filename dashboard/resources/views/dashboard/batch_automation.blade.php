@extends('layouts.layout')

@section('title', '⚙️ مركز التحكم والأتمتة الجماعية')

@section('nav_batch', 'active')

@section('styles')

<style>

    .batch-container {

        direction: rtl;

        text-align: right;

        display: flex;

        flex-direction: column;

        gap: 2rem;

    }

    

    /* Config Panel */

    .config-grid {

        display: grid;

        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));

        gap: 1.25rem;

    }

    

    .config-card {

        background: rgba(255, 255, 255, 0.02);

        border: 1px solid var(--panel-border);

        border-radius: 16px;

        padding: 1.25rem;

        display: flex;

        flex-direction: column;

        gap: 0.75rem;

        transition: all 0.3s;

    }

    

    .config-card:hover {

        border-color: var(--accent-purple);

        background: rgba(255, 255, 255, 0.04);

        box-shadow: 0 4px 15px rgba(139, 92, 246, 0.05);

    }

    

    .config-card label {

        font-weight: 800;

        font-size: 0.9rem;

        color: var(--text-primary);

    }

    .config-card input[type="text"], 

    .config-card select, 

    .config-card input[type="number"] {

        width: 100%;

        padding: 0.6rem 0.85rem;

        background: var(--input-bg);

        border: 1px solid var(--panel-border);

        border-radius: 8px;

        color: var(--text-primary);

        font-family: inherit;

        font-size: 0.85rem;

        outline: none;

    }

    .config-card input:focus, .config-card select:focus {

        border-color: var(--accent-purple);

    }

    

    /* Terminal Console */

    .terminal-container {

        background: #000000;

        border: 1px solid var(--panel-border);

        border-radius: 16px;

        font-family: 'Courier New', Courier, monospace;

        color: #ffffff;

        padding: 1.5rem;

        height: 320px;

        overflow-y: auto;

        font-size: 0.85rem;

        box-shadow: inset 0 0 20px rgba(0, 0, 0, 0.8), 0 4px 20px var(--btn-shadow);

        display: flex;

        flex-direction: column;

        gap: 0.35rem;

        text-align: left;

        direction: ltr;

    }

    

    .terminal-line {

        line-height: 1.4;

        white-space: pre-wrap;

        word-break: break-all;

    }

    

    .terminal-line.error { color: var(--danger); }

    .terminal-line.warning { color: var(--warning); }

    .terminal-line.success { color: var(--success); }

    .terminal-line.system { color: var(--text-secondary); }

    .candidates-grid-gallery {

        flex: 1;

        display: flex;

        flex-wrap: wrap;

        gap: 0.85rem;

        padding: 0.5rem;

        border-right: 1px solid var(--panel-border);

        border-left: 1px solid var(--panel-border);

        margin: 0 1rem;

    }

    

    .curation-thumb-card {

        position: relative;

        flex: 0 0 110px;

        width: 110px;

        height: 110px;

        border-radius: 14px;

        border: 2px solid var(--panel-border);

        overflow: hidden;

        cursor: pointer;

        transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);

    }

    .curation-thumb-card:hover {

        border-color: var(--accent-cyan) !important;

        transform: scale(1.05);

    }

    .curation-thumb-card.active-candidate {

        border-color: var(--accent-purple) !important;

        box-shadow: 0 0 15px var(--btn-shadow);

        transform: scale(1.05);

        background: var(--active-menu-bg);

    }

    

    .curation-row-card {

        background: var(--card-bg);

        border: 1px solid var(--panel-border);

        border-radius: 16px;

        padding: 1.5rem;

        margin-bottom: 1.5rem;

        display: flex;

        align-items: center;

        gap: 1.5rem;

        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);

        direction: rtl;

        position: relative;

        overflow: hidden;

        backdrop-filter: blur(15px);

        -webkit-backdrop-filter: blur(15px);

    }

    .curation-row-card:hover {

        border-color: var(--accent-cyan) !important;

        box-shadow: 0 8px 30px rgba(0, 245, 255, 0.06);

        transform: translateY(-2px);

    }

    .curation-row-card.focused-card {

        border-color: var(--accent-purple) !important;

        box-shadow: 0 8px 30px rgba(139, 92, 246, 0.15) !important;

        transform: translateY(-2px);

        background: rgba(255, 255, 255, 0.03) !important;

    }

</style>

@endsection

@section('content')

<div class="batch-container">

    

    <!-- Header -->

    <div class="glass-panel" style="padding: 1.5rem 2rem; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 1rem;">

        <div>

            <h2 style="font-size: 1.4rem; font-weight: 900; margin: 0; color: var(--text-primary); display: flex; align-items: center; gap: 0.65rem;">

                <i class="fas fa-magic" style="color: var(--accent-purple-hover);"></i> مركز الأتمتة والتحكم الجماعي الشامل

            </h2>

            <p style="font-size: 0.85rem; color: var(--text-secondary); margin-top: 0.25rem;">

                أطلق عمليات التحضير المسبق لصور المنتجات في الشيت، تابع كونسول التشغيل والتقدم بشكل تفاعلي، واعتمد كلياً.

            </p>

        </div>

        

        <div style="display: flex; gap: 0.75rem;">

            <button type="button" class="btn btn-secondary" onclick="resetBatchState()" style="background: var(--danger-bg); border-color: var(--panel-border); color: var(--danger); font-weight: 800; padding: 0.65rem 1.5rem;">

                <i class="fas fa-undo"></i> تصفير وإعادة تعيين الحالة 🔄

            </button>

            <button type="button" class="btn" id="runAllBtn" onclick="runAllAutomation()" style="background: var(--accent-gradient); color: var(--btn-text); font-weight: 800; padding: 0.65rem 1.5rem;">

                <i class="fas fa-play"></i> إطلاق الأتمتة الجماعية بالخلفية

            </button>

        </div>

    </div>

    <!-- Stats Cards Grid -->

    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1.5rem; margin-bottom: 0.5rem;">

        <!-- Card 1: Queue -->

        <div class="glass-panel" style="padding: 1.25rem; display: flex; align-items: center; gap: 1.25rem; border-color: var(--panel-border);">

            <div style="background: var(--active-menu-bg); width: 50px; height: 50px; border-radius: 12px; display: flex; align-items: center; justify-content: center; font-size: 1.5rem; color: var(--text-primary);">

                <i class="fas fa-layer-group"></i>

            </div>

            <div>

                <span style="font-size: 0.8rem; color: var(--text-secondary); font-weight: bold; display: block;">المنتجات المعلقة للمراجعة</span>

                <strong style="font-size: 1.6rem; font-weight: 900; color: var(--text-primary); font-family: 'Outfit', sans-serif;" id="statQueueCount">0</strong>

            </div>

        </div>



        <!-- Card 2: Progress -->

        <div class="glass-panel" style="padding: 1.25rem; display: flex; align-items: center; gap: 1.25rem; border-color: var(--panel-border);">

            <div style="background: var(--active-menu-bg); width: 50px; height: 50px; border-radius: 12px; display: flex; align-items: center; justify-content: center; font-size: 1.5rem; color: var(--text-primary);">

                <i class="fas fa-chart-line"></i>

            </div>

            <div style="flex: 1;">

                <span style="font-size: 0.8rem; color: var(--text-secondary); font-weight: bold; display: block;">الحالة والتقدم العام</span>

                <span style="font-size: 1.1rem; font-weight: 800; color: var(--text-primary);" id="statProgressLabel">خامل (Idle)</span>

            </div>

        </div>



        <!-- Card 3: Failures -->

        <div class="glass-panel" style="padding: 1.25rem; display: flex; align-items: center; gap: 1.25rem; border-color: var(--panel-border);">

            <div style="background: var(--danger-bg); width: 50px; height: 50px; border-radius: 12px; display: flex; align-items: center; justify-content: center; font-size: 1.5rem; color: var(--danger);">

                <i class="fas fa-exclamation-triangle"></i>

            </div>

            <div>

                <span style="font-size: 0.8rem; color: var(--text-secondary); font-weight: bold; display: block;">الأخطاء والفشل</span>

                <div style="display: flex; align-items: center; gap: 0.75rem;">

                    <strong style="font-size: 1.6rem; font-weight: 900; color: var(--danger); font-family: 'Outfit', sans-serif;" id="statFailedCount">0</strong>

                    <button type="button" class="btn btn-secondary btn-sm" id="statRetryBtn" onclick="retryFailedTasks()" style="display: none; padding: 2px 8px; font-size: 0.75rem; color: var(--warning); background: var(--warning-bg); border-color: var(--panel-border);">إعادة المحاولة</button>

                </div>

            </div>

        </div>



        <!-- Card 4: Quality Breakdown -->

        <div class="glass-panel" style="padding: 1.25rem; display: flex; align-items: center; gap: 1.25rem; border-color: var(--panel-border);">

            <div style="background: var(--active-menu-bg); width: 50px; height: 50px; border-radius: 12px; display: flex; align-items: center; justify-content: center; font-size: 1.5rem; color: var(--accent-cyan);">

                <i class="fas fa-percentage"></i>

            </div>

            <div style="flex: 1;">

                <span style="font-size: 0.8rem; color: var(--text-secondary); font-weight: bold; display: block;">توزيع جودة المرشحات</span>

                <span id="statQualityText" style="font-size: 0.8rem; font-weight: 800; color: var(--text-primary); display: block; margin-top: 0.15rem; direction: ltr; text-align: right;">Excellent: 0 | Med: 0 | Weak: 0</span>

            </div>

        </div>

    </div>

    

    <!-- Tabs Navigation -->
    <div class="glass-panel" style="padding: 0.5rem; display: flex; gap: 0.5rem; margin-bottom: 2rem; border-radius: 14px; direction: rtl; border-color: var(--panel-border);">
        <button type="button" class="btn" id="tabBtnAutomation" onclick="switchTab('automation')" style="flex: 1; font-weight: 800; font-size: 0.95rem; border-radius: 10px; padding: 0.75rem; transition: all 0.3s; background: var(--accent-gradient); color: var(--btn-text);">
            <i class="fas fa-magic"></i> لوحة التحكم والأتمتة الحية
        </button>
        <button type="button" class="btn btn-secondary" id="tabBtnCuration" onclick="switchTab('curation')" style="flex: 1; font-weight: 800; font-size: 0.95rem; border-radius: 10px; padding: 0.75rem; transition: all 0.3s; background: transparent; border-color: transparent; color: var(--text-secondary);">
            <i class="fas fa-layer-group"></i> فرز واعتماد الصور الجاهزة
            <span id="tabCurationBadge" style="display: none; background: var(--danger); color: white; border-radius: 20px; padding: 2px 8px; font-size: 0.75rem; margin-right: 0.5rem; font-family: 'Outfit', sans-serif;">0</span>
        </button>
    </div>

    <!-- Tab 1: Automation Dashboard & Live Logs -->
    <div id="tabContentAutomation" style="display: block;">
        <!-- Split-Screen Layout (RTL: right side is larger, left side is logs) -->
        <div style="display: grid; grid-template-columns: 1.3fr 1fr; gap: 2rem; align-items: start; direction: rtl;">
            
            <!-- Right side: Grouped Configuration Cards -->
            <div style="display: flex; flex-direction: column; gap: 1.5rem;">
                
                <!-- Group 0: Google Sheets Fetcher -->
                <div class="glass-panel" style="padding: 1.5rem;">
                    <h3 style="font-size: 1.05rem; font-weight: 800; margin-bottom: 1rem; display: flex; align-items: center; justify-content: space-between; color: var(--accent-cyan);">
                        <span style="display: flex; align-items: center; gap: 0.5rem;"><i class="fas fa-file-excel"></i> تهيئة ومزامنة مصدر البيانات (Google Sheets)</span>
                        <span class="score-badge" style="background: rgba(6, 182, 212, 0.1); border-color: var(--accent-cyan); color: var(--accent-cyan); font-size: 0.75rem;">نشط ⚡</span>
                    </h3>
                    
                    <div style="background: rgba(0,0,0,0.15); border: 1px solid var(--panel-border); border-radius: 12px; padding: 1rem; margin-bottom: 1.25rem; font-size: 0.8rem; line-height: 1.6; color: var(--text-secondary);">
                        <i class="fas fa-info-circle" style="color: var(--accent-cyan); margin-inline-end: 0.35rem;"></i>
                        الرجاء مشاركة ملف الـ Google Sheet الخاص بك مع حساب الخدمة التالي كـ <strong>Editor</strong> لتمكينه من القراءة وتحديث الروابط تلقائياً:
                        <code style="display: block; margin-top: 0.5rem; background: rgba(0,0,0,0.3); padding: 6px 12px; border-radius: 6px; font-family: monospace; color: var(--text-primary); text-align: left; direction: ltr; font-weight: bold; border: 1px solid var(--panel-border);">outomation-agent@boulevard-a50a0.iam.gserviceaccount.com</code>
                    </div>
                    
                    <div style="display: flex; flex-direction: column; gap: 1rem;">
                        <div style="display: flex; gap: 1rem; flex-wrap: wrap;">
                            <div style="flex: 2; min-width: 280px; display: flex; flex-direction: column; gap: 0.45rem;">
                                <label style="font-size: 0.85rem; color: var(--text-secondary); font-weight: bold;">رابط أو اسم ملف Google Sheet</label>
                                <input type="text" id="spreadsheetUrlOrName" placeholder="أدخل رابط جدول البيانات بالكامل..." value="{{ config('sheets.spreadsheet_name_or_url') ?? env('SPREADSHEET_NAME_OR_URL') }}" style="width: 100%; padding: 0.65rem 1rem; background: var(--input-bg); border: 1px solid var(--panel-border); border-radius: 10px; color: var(--text-primary); font-family: inherit; font-size: 0.85rem; outline: none;">
                            </div>
                            <div style="flex: 1; min-width: 150px; display: flex; flex-direction: column; gap: 0.45rem;">
                                <label style="font-size: 0.85rem; color: var(--text-secondary); font-weight: bold;">اسم ورقة العمل (Tab)</label>
                                <input type="text" id="spreadsheetTab" placeholder="مثال: Sheet1 أو المنتجات" value="{{ env('SPREADSHEET_TAB_NAME') ?: 'المنتجات' }}" style="width: 100%; padding: 0.65rem 1rem; background: var(--input-bg); border: 1px solid var(--panel-border); border-radius: 10px; color: var(--text-primary); font-family: inherit; font-size: 0.85rem; outline: none;">
                            </div>
                        </div>
                        
                        <div style="display: flex; gap: 0.75rem; justify-content: flex-end;">
                            <button type="button" class="btn btn-secondary" id="previewSheetBtn" onclick="previewGoogleSheet()" style="font-weight: bold; padding: 0.65rem 1.25rem;">
                                <i class="fas fa-eye"></i> معاينة وجلب البيانات 🔍
                            </button>
                            <button type="button" class="btn" id="saveSheetBtn" onclick="saveGoogleSheetConfig()" style="background: var(--accent-gradient); color: var(--btn-text); font-weight: bold; padding: 0.65rem 1.5rem;">
                                <i class="fas fa-save"></i> حفظ المزامنة سحابياً 💾
                            </button>
                        </div>
                        
                        <!-- Sheet Preview Table -->
                        <div id="sheetPreviewContainer" style="display: none; border-top: 1px solid var(--panel-border); padding-top: 1.25rem; margin-top: 0.5rem;">
                            <h4 style="font-size: 0.85rem; color: var(--text-primary); font-weight: bold; margin: 0 0 0.75rem 0;"><i class="fas fa-table"></i> معاينة البيانات المستوردة (أول 5 صفوف):</h4>
                            <div style="overflow-x: auto; border: 1px solid var(--panel-border); border-radius: 10px; background: rgba(0,0,0,0.2);">
                                <table id="sheetPreviewTable" style="width: 100%; border-collapse: collapse; font-size: 0.75rem; text-align: right; direction: rtl;">
                                    <thead>
                                        <tr style="background: rgba(255,255,255,0.03); border-bottom: 1px solid var(--panel-border);">
                                            <!-- dynamic headers -->
                                        </tr>
                                    </thead>
                                    <tbody>
                                        <!-- dynamic rows -->
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- Group A: Image Processing -->
                <div class="glass-panel" style="padding: 1.5rem;">
                    <h3 style="font-size: 1.05rem; font-weight: 800; margin-bottom: 1.25rem; display: flex; align-items: center; gap: 0.5rem; color: var(--accent-cyan);">
                        <i class="fas fa-image"></i> خيارات معالجة الصور
                    </h3>
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem;">
                        <div class="config-card">
                            <label for="bgColor">لون الخلفية المستهدف</label>
                            <input type="text" id="bgColor" value="ffffff" placeholder="مثال: ffffff">
                        </div>
                        <div class="config-card">
                            <label for="paddingRatio">نسبة الهامش (Padding)</label>
                            <input type="number" id="paddingRatio" value="0.85" step="0.05" min="0.5" max="0.95">
                        </div>
                        <div class="config-card">
                            <label for="bgRemovalMethod">مزيل الخلفية</label>
                            <select id="bgRemovalMethod">
                                <option value="photoroom">PhotoRoom AI</option>
                                <option value="none">بدون عزل (تعديل الأبعاد فقط)</option>
                            </select>
                        </div>
                    </div>
                </div>

                <!-- Group B: AI Improvements -->
                <div class="glass-panel" style="padding: 1.5rem;">
                    <h3 style="font-size: 1.05rem; font-weight: 800; margin-bottom: 1.25rem; display: flex; align-items: center; gap: 0.5rem; color: var(--accent-purple-hover);">
                        <i class="fas fa-brain"></i> تحسينات الذكاء الاصطناعي (AI)
                    </h3>
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem;">
                        <div class="config-card">
                            <label for="aiUpscale">ترقية الدقة (AI Upscale)</label>
                            <select id="aiUpscale">
                                <option value="true">تفعيل (AI Upscale)</option>
                                <option value="false">تعطيل</option>
                            </select>
                        </div>
                        <div class="config-card">
                            <label for="aiEnhance">تحسين الألوان (AI Enhance)</label>
                            <select id="aiEnhance">
                                <option value="false">تعطيل</option>
                                <option value="true">تفعيل (AI Enhance)</option>
                            </select>
                        </div>
                    </div>
                </div>

                <!-- Group C: Ranges and Auto Approve -->
                <div class="glass-panel" style="padding: 1.5rem;">
                    <h3 style="font-size: 1.05rem; font-weight: 800; margin-bottom: 1.25rem; display: flex; align-items: center; gap: 0.5rem; color: var(--warning);">
                        <i class="fas fa-filter"></i> نطاق العمل وقوانين الأتمتة
                    </h3>
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem;">
                        <div class="config-card">
                            <label for="autoApproveThreshold">حد الاعتماد التلقائي (Auto Ingest)</label>
                            <select id="autoApproveThreshold" onchange="updatePreflightEstimates()">
                                <option value="0.0">تعطيل الاعتماد التلقائي</option>
                                <option value="0.98">طابق دقيق (98% فما فوق)</option>
                                <option value="0.95">طابق مرتفع (95% فما فوق)</option>
                                <option value="0.90">طابق مقبول (90% فما فوق)</option>
                            </select>
                        </div>
                        <div class="config-card">
                            <label for="brandFilter">فلترة حسب الماركة (Brand)</label>
                            <input type="text" id="brandFilter" placeholder="مثال: Lipton" oninput="updatePreflightEstimates()">
                        </div>
                        <div class="config-card">
                            <label for="rowFilter">فلترة حسب الصفوف (Rows)</label>
                            <input type="text" id="rowFilter" placeholder="مثال: 5-20 أو 5,8,12" oninput="updatePreflightEstimates()">
                        </div>
                        <div class="config-card">
                            <label for="forceOverwrite">معالجة الصور الموجودة</label>
                            <select id="forceOverwrite" onchange="updatePreflightEstimates()">
                                <option value="true" selected>فرض إعادة المعالجة والكتابة فوق الصور (افتراضي)</option>
                                <option value="false">تخطي الصفوف التي تمتلك صوراً مسبقاً</option>
                            </select>
                        </div>
                    </div>
                </div>

                <!-- Pre-flight Estimates Panel -->
                <div class="glass-panel" style="padding: 1.5rem;">
                    <h4 style="font-size: 0.95rem; font-weight: 800; color: var(--accent-cyan); margin: 0 0 1rem 0; display: flex; align-items: center; gap: 0.45rem;">
                        <i class="fas fa-calculator"></i> التقديرات والتوقعات الذكية (Pre-flight Estimates)
                    </h4>
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 1rem;">
                        <div style="background: rgba(0,0,0,0.2); padding: 0.75rem; border-radius: 10px; border: 1px solid var(--panel-border);">
                            <span style="font-size: 0.7rem; color: var(--text-secondary); display: block; margin-bottom: 0.25rem;">الصفوف المستهدفة</span>
                            <strong id="estimateRows" style="font-size: 1.05rem; color: var(--text-primary);">0 صف</strong>
                        </div>
                        <div style="background: rgba(0,0,0,0.2); padding: 0.75rem; border-radius: 10px; border: 1px solid var(--panel-border);">
                            <span style="font-size: 0.7rem; color: var(--text-secondary); display: block; margin-bottom: 0.25rem;">الوقت المتوقع</span>
                            <strong id="estimateTime" style="font-size: 1.05rem; color: var(--text-primary);">0 ثانية</strong>
                        </div>
                        <div style="background: rgba(0,0,0,0.2); padding: 0.75rem; border-radius: 10px; border: 1px solid var(--panel-border);">
                            <span style="font-size: 0.7rem; color: var(--text-secondary); display: block; margin-bottom: 0.25rem;">مكالمات Gemini المتوقعة</span>
                            <strong id="estimateGemini" style="font-size: 1.05rem; color: var(--text-primary);">0 طلب</strong>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Left side: Live Console & Progress Tracker stacked -->
            <div style="display: flex; flex-direction: column; gap: 1.5rem;">
                
                <!-- Background Process Tracker Card -->
                <div class="glass-panel" style="padding: 1.5rem; display: flex; flex-direction: column; gap: 1.25rem;">
                    <h3 style="font-size: 1.05rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.75rem; margin-bottom: 0.25rem; display: flex; align-items: center; gap: 0.5rem; color: var(--text-primary);">
                        <i class="fas fa-tasks" style="color: var(--accent-cyan);"></i> تتبع الأتمتة بالخلفية
                    </h3>
                    
                    <div id="batchProgressPanel" style="display: none; flex-direction: column; gap: 0.85rem;">
                        <div style="display: flex; justify-content: space-between; font-size: 0.85rem; font-weight: bold; align-items: center;">
                            <span id="batchProgressText">جاري المعالجة...</span>
                            <span id="batchProgressPercent" style="color: var(--accent-purple); font-family: 'Outfit', sans-serif; font-size: 1.05rem;">0%</span>
                        </div>
                        <div class="progress-bar-container" style="height: 8px;">
                            <div id="batchProgressBar" class="progress-bar-fill"></div>
                        </div>
                        <div id="batchProgressCounts" style="font-size: 0.8rem; color: var(--text-secondary); text-align: left; direction: ltr; font-family: 'Outfit', sans-serif;">
                            0 of 0 (Success: 0 | Failed: 0)
                        </div>
                        
                        <div style="display: flex; gap: 0.5rem; margin-top: 0.5rem; width: 100%;">
                            <button type="button" class="btn btn-secondary btn-sm" id="pauseResumeBatchBtn" onclick="togglePauseResumeAutomation()" style="flex: 1; background: var(--warning-bg); border-color: var(--panel-border); color: var(--warning); font-weight: bold; border-radius: 10px;">
                                <i class="fas fa-pause" id="pauseResumeIcon"></i> <span id="pauseResumeText">إيقاف مؤقت</span>
                            </button>
                            <button type="button" class="btn btn-secondary btn-sm" id="stopBatchBtn" onclick="stopBatchAutomation()" style="flex: 1; background: var(--danger-bg); border-color: var(--panel-border); color: var(--danger); font-weight: bold; border-radius: 10px;">
                                <i class="fas fa-stop"></i> إنهاء قسري 🛑
                            </button>
                        </div>
                    </div>
                    <div id="batchIdleState" style="text-align: center; padding: 2rem 0; color: var(--text-secondary);">
                        <i class="fas fa-check-circle" style="font-size: 2.5rem; color: var(--success); margin-bottom: 0.75rem; opacity: 0.7;"></i>
                        <p style="font-size: 0.85rem; font-weight: bold; margin: 0; color: var(--success);">لا توجد عمليات جارية حالياً.</p>
                        <p style="font-size: 0.75rem; margin-top: 0.25rem;">قم بتهيئة الخيارات بالأعلى واضغط إطلاق لبدء أتمتة الشيت.</p>
                    </div>
                </div>

                <!-- Live Terminal console panel -->
                <div class="glass-panel" style="padding: 1.5rem; display: flex; flex-direction: column; height: 350px;">
                    <h3 style="font-size: 1.05rem; font-weight: 800; margin-bottom: 1rem; display: flex; align-items: center; justify-content: space-between; color: var(--text-primary); margin-top: 0;">
                        <span style="display: flex; align-items: center; gap: 0.5rem;"><i class="fas fa-terminal" style="color: var(--accent-cyan);"></i> كونسول المشاهدة الحية</span>
                        <button type="button" class="btn btn-secondary btn-sm" onclick="clearConsole()" style="font-size: 0.7rem; padding: 2px 8px; color: var(--danger); background: var(--danger-bg); border-color: var(--panel-border);">تفريغ الشاشة</button>
                    </h3>
                    
                    <div class="terminal-container" id="terminalConsole" style="flex: 1; overflow-y: auto; font-family: monospace;">
                        <div class="terminal-line system">[النظام] بانتظار إطلاق العمليات...</div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Tab 2: Curation & Review Workspace -->
    <div id="tabContentCuration" style="display: none;">
    <!-- Bottom Section: Curation Grid (appears when results are ready) -->

    <div class="glass-panel" id="batchCurationWorkspace" style="display: none; padding: 2rem;">

        <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--panel-border); padding-bottom: 1rem; margin-bottom: 1.5rem; direction: rtl;">

            <h3 style="font-size: 1.3rem; font-weight: 800; display: flex; align-items: center; gap: 0.65rem; color: var(--accent-purple-hover); margin: 0;">

                <i class="fas fa-layer-group"></i> فرز واعتماد الدفعة الجاهزة (Batch Curation Grid)

            </h3>

            <span class="score-badge" id="curationPendingCount">0 منتجات جاهزة</span>

        </div>

        <!-- Advanced Filtering & Curation Tools -->

        <div style="background: var(--input-bg); border: 1px solid var(--panel-border); padding: 1.25rem; border-radius: 16px; margin-bottom: 1.75rem; display: flex; flex-direction: column; gap: 1rem; direction: rtl;">

            <!-- Row 1: Actions -->

            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 1rem;">

                <div style="display: flex; gap: 1rem; flex-wrap: wrap;">

                    <button type="button" class="btn" id="batchApproveBtn" onclick="submitBatchApproval()" style="background: var(--accent-gradient); color: var(--btn-text); font-weight: 800; padding: 0.65rem 1.5rem;">

                        <i class="fas fa-check-double"></i> اعتماد ورفع الصور المحددة للشيت سحابياً 🚀

                    </button>

                    <button type="button" class="btn btn-secondary" id="batchRejectBtn" onclick="submitBatchRejection()" style="background: var(--danger-bg); border-color: var(--panel-border); color: var(--danger); font-weight: bold; padding: 0.65rem 1.5rem;">

                        <i class="fas fa-trash-alt"></i> استبعاد وتجاهل المحدد 🗑️

                    </button>

                    <button type="button" class="btn btn-secondary" onclick="exportCurationToCSV()" style="background: rgba(0,245,255,0.08); border-color: var(--accent-cyan); color: var(--accent-cyan); font-weight: bold; padding: 0.65rem 1.25rem;">

                        <i class="fas fa-file-csv"></i> تصدير التقرير (CSV) 📊

                    </button>

                </div>

                

                <div style="display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap;">

                    <button type="button" class="btn btn-secondary btn-sm" onclick="selectAllBatch(true)" style="padding: 0.4rem 0.8rem; font-size: 0.8rem;">تحديد الكل</button>

                    <button type="button" class="btn btn-secondary btn-sm" onclick="selectAllBatch(false)" style="padding: 0.4rem 0.8rem; font-size: 0.8rem;">إلغاء التحديد</button>

                    <button type="button" class="btn btn-secondary btn-sm" onclick="selectHighMatchBatch()" style="padding: 0.4rem 0.8rem; font-size: 0.8rem; background: rgba(46,204,113,0.15); color: #2ecc71; border-color: var(--panel-border);">تحديد مطابقة >= 95%</button>

                </div>

            </div>



            <!-- Row 2: Search & Advanced Filters -->

            <div style="display: grid; grid-template-columns: 2fr 1fr 1.2fr 1fr; gap: 1rem; border-top: 1px solid var(--panel-border); padding-top: 1rem; align-items: center;">

                <div>

                    <label style="font-size: 0.75rem; color: var(--text-secondary); display: block; margin-bottom: 0.25rem; font-weight: 800;">البحث بالنص (اسم أو ماركة)</label>

                    <input type="text" id="curationSearch" placeholder="ابحث عن منتج..." oninput="onCurationFilterChange()" style="width: 100%; font-size: 0.8rem; padding: 6px 12px; background: rgba(0,0,0,0.25); border: 1px solid var(--panel-border); color: var(--text-primary); border-radius: 8px; outline: none;">

                </div>

                <div>

                    <label style="font-size: 0.75rem; color: var(--text-secondary); display: block; margin-bottom: 0.25rem; font-weight: 800;">تصفية جودة الصورة</label>

                    <select id="curationQualityFilter" onchange="onCurationFilterChange()" style="width: 100%; font-size: 0.8rem; padding: 6px; background: rgba(0,0,0,0.25); border: 1px solid var(--panel-border); color: var(--text-primary); border-radius: 8px; outline: none; height: 32px;">

                        <option value="all">كل الدرجات</option>

                        <option value="excellent">مطابقة ممتازة (>= 95%)</option>

                        <option value="medium">مطابقة متوسطة (90-95%)</option>

                        <option value="weak">مطابقة ضعيفة (< 90%)</option>

                    </select>

                </div>

                <div style="display: flex; gap: 1rem; align-items: center; height: 100%; padding-top: 1rem;">

                    <label style="font-size: 0.8rem; color: var(--text-primary); display: flex; align-items: center; gap: 0.35rem; cursor: pointer; user-select: none;">

                        <input type="checkbox" id="curationAllergenFilter" onchange="onCurationFilterChange()" style="width: 18px; height: 18px; cursor: pointer; accent-color: var(--danger);"> مسببات الحساسية ⚠️

                    </label>

                    <label style="font-size: 0.8rem; color: var(--text-primary); display: flex; align-items: center; gap: 0.35rem; cursor: pointer; user-select: none;">

                        <input type="checkbox" id="curationExcludedFilter" onchange="onCurationFilterChange()" style="width: 18px; height: 18px; cursor: pointer; accent-color: var(--accent-purple);"> المستبعدة فقط

                    </label>

                </div>

                <div style="display: flex; flex-direction: column; align-items: flex-end; justify-content: center;">

                    <span style="font-size: 0.75rem; color: var(--text-secondary); font-weight: 800;" id="curationFilteredCount">يتم عرض: 0 من 0 منتج</span>

                </div>

            </div>



            <!-- Row 3: Pagination Controls -->

            <div style="display: flex; justify-content: space-between; align-items: center; border-top: 1px solid var(--panel-border); padding-top: 0.75rem; margin-top: 0.25rem;">

                <div style="display: flex; gap: 0.5rem; align-items: center;">

                    <button type="button" class="btn btn-secondary btn-sm" id="btnPrevPage" onclick="changeCurationPage(-1)" style="padding: 4px 12px; font-size: 0.75rem; font-weight: bold; border-radius: 8px;">السابق</button>

                    <span style="font-size: 0.8rem; color: var(--text-primary); font-weight: bold;" id="curationPageInfo">صفحة 1 من 1</span>

                    <button type="button" class="btn btn-secondary btn-sm" id="btnNextPage" onclick="changeCurationPage(1)" style="padding: 4px 12px; font-size: 0.75rem; font-weight: bold; border-radius: 8px;">التالي</button>

                </div>

                <div style="display: flex; align-items: center; gap: 0.5rem;">

                    <span style="font-size: 0.75rem; color: var(--text-secondary);">عرض السجلات لكل صفحة:</span>

                    <select id="curationPageSize" onchange="onPageSizeChange()" style="font-size: 0.75rem; padding: 4px; background: rgba(0,0,0,0.25); border: 1px solid var(--panel-border); color: var(--text-primary); border-radius: 6px;">

                        <option value="10">10 سجلات</option>

                        <option value="25" selected>25 سجل</option>

                        <option value="50">50 سجل</option>

                        <option value="100">100 سجل</option>

                    </select>

                </div>

            </div>



            <!-- Approval Progress bar -->

            <div id="batchCurationProgress" style="display: none; flex-direction: column; gap: 0.5rem; border-top: 1px solid var(--panel-border); padding-top: 1rem; margin-top: 0.5rem;">

                <div style="display: flex; justify-content: space-between; font-size: 0.85rem; font-weight: bold;">

                    <span id="batchCurationProgressText">جاري الرفع سحابياً...</span>

                    <span id="batchCurationProgressPercent" style="color: var(--accent-cyan);">0%</span>

                </div>

                <div class="progress-bar-container" style="height: 8px; background: rgba(255,255,255,0.03); border-radius: 20px; border: 1px solid var(--panel-border); overflow: hidden;">

                    <div id="batchCurationProgressBar" class="progress-bar-fill" style="width: 0%;"></div>

                </div>

            </div>

        </div>

        <div id="batchCurationGrid" style="display: flex; flex-direction: column; gap: 1.5rem; width: 100%;">

            <!-- Card templates rendered dynamically -->

        </div>

    </div>

</div>

@endsection

@section('scripts')

<script>

    let isPaused = false;

    let logOffset = 0;

    let consoleInterval = null;

    let currentProducts = [];

    

    // Pagination & Filter variables for large dataset curation

    let currentPage = 1;

    let pageSize = 25;

    let filteredProducts = [];

    let focusedCardIndex = -1; // For keyboard shortcut navigation
    
    // Switch between Automation dashboard and Curation workspace tabs
    function switchTab(tabId) {
        const btnAuto = document.getElementById('tabBtnAutomation');
        const btnCur = document.getElementById('tabBtnCuration');
        const contentAuto = document.getElementById('tabContentAutomation');
        const contentCur = document.getElementById('tabContentCuration');
        
        if (tabId === 'automation') {
            contentAuto.style.display = 'block';
            contentCur.style.display = 'none';
            btnAuto.style.background = 'var(--accent-gradient)';
            btnAuto.style.color = 'var(--btn-text)';
            btnCur.style.background = 'transparent';
            btnCur.style.borderColor = 'transparent';
            btnCur.style.color = 'var(--text-secondary)';
        } else {
            contentAuto.style.display = 'none';
            contentCur.style.display = 'block';
            btnCur.style.background = 'var(--accent-gradient)';
            btnCur.style.color = 'var(--btn-text)';
            btnAuto.style.background = 'transparent';
            btnAuto.style.borderColor = 'transparent';
            btnAuto.style.color = 'var(--text-secondary)';
        }
    }

    // Helper to get image proxy URL

    function getImageUrl(url) {

        if (!url) return '';

        if (url.startsWith('http://') || url.startsWith('https://')) {

            return `/api/image-proxy?url=${encodeURIComponent(url)}`;

        }

        return url;

    }

    // Force Reset Batch state

    async function resetBatchState() {

        if (!confirm("⚠️ هل أنت متأكد من رغبتك في تصفير وإعادة تعيين حالة الأتمتة بالكامل؟ سيتم إيقاف أي عمليات معلقة وتصفير الإحصائيات.")) {

            return;

        }

        try {

            const res = await fetch('/api/batch/reset', {

                method: 'POST',

                headers: {

                    'Content-Type': 'application/json',

                    'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content

                }

            });

            const data = await res.json();

            if (data.status === 'success') {

                alert("🔄 تم تصفير وإعادة تعيين الحالة بنجاح.");

                location.reload();

            } else {

                alert("❌ فشل تصفير الحالة: " + data.error);

            }

        } catch (err) {

            console.error(err);

        }

    }

    // Toggle Pause/Resume

    async function togglePauseResumeAutomation() {

        const btn = document.getElementById('pauseResumeBatchBtn');

        const icon = document.getElementById('pauseResumeIcon');

        const txt = document.getElementById('pauseResumeText');

        btn.disabled = true;

        try {

            const url = isPaused ? '/api/batch/resume' : '/api/batch/pause';

            const res = await fetch(url, {

                method: 'POST',

                headers: {

                    'Content-Type': 'application/json',

                    'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content

                }

            });

            const data = await res.json();

            if (data.status === 'success') {

                isPaused = !isPaused;

                if (isPaused) {

                    icon.className = 'fas fa-play';

                    txt.innerText = 'استئناف الأتمتة';

                    btn.style.color = 'var(--text-primary)';

                    btn.style.background = 'var(--success-bg)';

                    btn.style.borderColor = 'var(--panel-border)';

                } else {

                    icon.className = 'fas fa-pause';

                    txt.innerText = 'إيقاف مؤقت';

                    btn.style.color = 'var(--text-secondary)';

                    btn.style.background = 'var(--warning-bg)';

                    btn.style.borderColor = 'var(--panel-border)';

                }

            } else {

                alert('فشل تغيير حالة الأتمتة: ' + data.error);

            }

        } catch (err) {

            console.error(err);

        } finally {

            btn.disabled = false;

        }

    }

    // Stop batch

    async function stopBatchAutomation() {

        if (!confirm("⚠️ هل أنت متأكد من رغبتك في إيقاف عملية الأتمتة الكلية بالخلفية فورياً؟")) {

            return;

        }

        const btn = document.getElementById('stopBatchBtn');

        btn.disabled = true;

        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> جاري الإيقاف...';

        try {

            const res = await fetch('/api/stop-batch', {

                method: 'POST',

                headers: { 'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content }

            });

            const data = await res.json();

            if (data.status === 'success') {

                alert("🛑 تم إيقاف الأتمتة وإلغاء خيوط المعالجة بنجاح.");

                location.reload();

            } else {

                alert("❌ فشل إيقاف الأتمتة: " + data.error);

                btn.disabled = false;

                btn.innerHTML = '<i class="fas fa-stop"></i> إنهاء قسري 🛑';

            }

        } catch (err) {

            console.error(err);

            btn.disabled = false;

            btn.innerHTML = '<i class="fas fa-stop"></i> إنهاء قسري 🛑';

        }

    }

    // Launch worker

    async function runAllAutomation() {

        const btn = document.getElementById('runAllBtn');

        btn.disabled = true;

        btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> جاري إطلاق الأتمتة...';

        

        const settings = {

            bgColor: document.getElementById('bgColor').value,

            padding_ratio: parseFloat(document.getElementById('paddingRatio').value),

            bgRemovalMethod: document.getElementById('bgRemovalMethod').value,

            aiUpscale: document.getElementById('aiUpscale').value === 'true',

            aiEnhance: document.getElementById('aiEnhance').value === 'true',

            brand_filter: document.getElementById('brandFilter').value.trim(),

            row_filter: document.getElementById('rowFilter').value.trim(),

            auto_approve_threshold: parseFloat(document.getElementById('autoApproveThreshold').value),

            forceOverwrite: document.getElementById('forceOverwrite').value === 'true',

            curation_mode: true

        };

        

        try {

            const res = await fetch('/api/run-all', {

                method: 'POST',

                headers: {

                    'Content-Type': 'application/json',

                    'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content

                },

                body: JSON.stringify(settings)

            });

            const data = await res.json();

            if (data.status === 'success') {

                document.getElementById('batchIdleState').style.display = 'none';

                document.getElementById('batchProgressPanel').style.display = 'flex';

                logOffset = 0;

                appendTerminalLine('System', '⚙️ تم تشغيل ملف تهيئة المهام وإطلاق الوركر بنجاح!', 'system');

            } else {

                alert('❌ فشل تشغيل الأتمتة: ' + data.error);

                btn.disabled = false;

                btn.innerHTML = '<i class="fas fa-play"></i> إطلاق الأتمتة الجماعية بالخلفية';

            }

        } catch (err) {

            console.error(err);

            btn.disabled = false;

            btn.innerHTML = '<i class="fas fa-play"></i> إطلاق الأتمتة الجماعية بالخلفية';

        }

    }

    // Append to log terminal

    function appendTerminalLine(sender, text, type = 'default') {

        const consoleEl = document.getElementById('terminalConsole');

        const line = document.createElement('div');

        line.className = `terminal-line ${type}`;

        

        const timestamp = new Date().toLocaleTimeString();

        line.innerText = `[${timestamp}] [${sender}] ${text}`;

        consoleEl.appendChild(line);

        consoleEl.scrollTop = consoleEl.scrollHeight;

    }

    // Clear console logs

    function clearConsole() {

        document.getElementById('terminalConsole').innerHTML = '';

        appendTerminalLine('System', 'تم تفريغ لوحة التحكم الحية.', 'system');

    }

    // Poll logs API

    async function pollLogs() {

        try {

            const res = await fetch('/api/logs');

            const data = await res.json();

            if (data.logs && data.logs.length > logOffset) {

                const newLines = data.logs.slice(logOffset);

                newLines.forEach(line => {

                    let type = 'default';

                    if (line.includes('❌') || line.includes('Error') || line.includes('failed')) type = 'error';

                    else if (line.includes('⚠️') || line.includes('warning')) type = 'warning';

                    else if (line.includes('✅') || line.includes('success') || line.includes('تم بنجاح')) type = 'success';

                    else if (line.includes('⚙️') || line.includes('System')) type = 'system';

                    

                    appendTerminalLine('Python Log', line, type);

                });

                logOffset = data.logs.length;

            }

        } catch (err) {

            console.error(err);

        }

    }

    // Retry failed pipeline tasks

    async function retryFailedTasks() {

        if (!confirm("هل أنت متأكد من إعادة محاولة تشغيل المهام التي فشلت؟")) {

            return;

        }

        const btn = document.getElementById('statRetryBtn');

        btn.disabled = true;

        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> جاري البدء...';

        try {

            const res = await fetch('/api/failures/retry', {

                method: 'POST',

                headers: {

                    'Content-Type': 'application/json',

                    'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content

                }

            });

            const data = await res.json();

            if (data.status === 'success') {

                alert('🎉 تم إعادة جدولة المهام الفاشلة بنجاح!');

                location.reload();

            } else {

                alert('❌ فشل إعادة جدولة المهام: ' + data.error);

                btn.disabled = false;

                btn.innerHTML = 'إعادة المحاولة';

            }

        } catch (err) {

            console.error(err);

            btn.disabled = false;

            btn.innerHTML = 'إعادة المحاولة';

        }

    }

    // Poll status

    async function pollBatchStatus() {

        try {

            const res = await fetch('/api/batch-status');

            const data = await res.json();

            

            const panel = document.getElementById('batchProgressPanel');

            const idle = document.getElementById('batchIdleState');

            const runBtn = document.getElementById('runAllBtn');

            

            // Update Dashboard metrics

            document.getElementById('statFailedCount').innerText = data.failed || 0;

            const retryBtn = document.getElementById('statRetryBtn');

            if (data.failed > 0) {

                retryBtn.style.display = 'inline-block';

            } else {

                retryBtn.style.display = 'none';

            }

            

            if (data.is_running || (data.status === 'pre_caching' && data.pause_requested === 1)) {

                panel.style.display = 'flex';

                idle.style.display = 'none';

                

                const percent = data.total > 0 ? Math.round((data.current / data.total) * 100) : 0;

                document.getElementById('batchProgressPercent').innerText = percent + '%';

                document.getElementById('batchProgressBar').style.width = percent + '%';

                

                // Update stats progress label

                document.getElementById('statProgressLabel').innerHTML = `جاري التحضير: <strong style="color: var(--accent-cyan);">${percent}%</strong>`;

                

                isPaused = (data.pause_requested === 1);

                const pBtn = document.getElementById('pauseResumeBatchBtn');

                const pIcon = document.getElementById('pauseResumeIcon');

                const pTxt = document.getElementById('pauseResumeText');

                

                if (isPaused) {

                    pIcon.className = 'fas fa-play';

                    pTxt.innerText = 'استئناف الأتمتة';

                    pBtn.style.color = 'var(--text-primary)';

                    pBtn.style.background = 'var(--success-bg)';

                    pBtn.style.borderColor = 'var(--panel-border)';

                    document.getElementById('batchProgressText').innerHTML = `<strong style="color: var(--warning);"><i class="fas fa-pause-circle"></i> الأتمتة موقوفة مؤقتاً</strong>`;

                    document.getElementById('statProgressLabel').innerHTML = `<span style="color: var(--warning); font-weight: 800;">موقوف مؤقتاً ⏸️</span>`;

                } else {

                    pIcon.className = 'fas fa-pause';

                    pTxt.innerText = 'إيقاف مؤقت';

                    pBtn.style.color = 'var(--text-secondary)';

                    pBtn.style.background = 'var(--warning-bg)';

                    pBtn.style.borderColor = 'var(--panel-border)';

                    document.getElementById('batchProgressText').innerHTML = `جاري تحضير مرشحات: <strong style="color: var(--accent-cyan);">${data.current_product || 'جاري البحث...'}</strong>`;

                }

                

                document.getElementById('batchProgressCounts').innerText = `${data.current} من ${data.total} (مكتمل: ${data.success} | فشل: ${data.failed})`;

                

                runBtn.disabled = true;

                runBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> جاري الأتمتة بالخلفية...';

            } else if (data.status === 'curation_pending') {

                panel.style.display = 'none';

                idle.style.display = 'block';

                idle.innerHTML = `

                    <i class="fas fa-check-circle" style="font-size: 2.5rem; color: var(--success); margin-bottom: 0.75rem;"></i>

                    <p style="font-size: 0.85rem; font-weight: bold; margin: 0; color: var(--success);">تم التحضير المسبق لجميع المرشحات!</p>

                    <p style="font-size: 0.75rem; margin-top: 0.25rem;">المنتجات جاهزة للمراجعة بجدول الفرز بالأسفل.</p>

                `;

                

                document.getElementById('statProgressLabel').innerHTML = `<span style="color: var(--success); font-weight: 800;">جاهز للفرز والاعتماد 🎯</span>`;

                runBtn.disabled = false;

                runBtn.innerHTML = '<i class="fas fa-play"></i> تشغيل أتمتة الشيت بالكامل (Batch)';

                

                // Show curation workspace
                const workspace = document.getElementById('batchCurationWorkspace');
                if (workspace.style.display === 'none') {
                    workspace.style.display = 'block';
                    // Auto switch tab to curation to show the grid immediately
                    switchTab('curation');
                    fetchCurationProducts();
                } else if (!currentProducts || currentProducts.length === 0) {
                    fetchCurationProducts();
                }

            } else {

                panel.style.display = 'none';

                idle.style.display = 'block';

                runBtn.disabled = false;

                runBtn.innerHTML = '<i class="fas fa-play"></i> تشغيل أتمتة الشيت بالكامل (Batch)';

                if (currentProducts && currentProducts.length > 0) {
                    document.getElementById('batchCurationWorkspace').style.display = 'block';
                } else {
                    document.getElementById('batchCurationWorkspace').style.display = 'none';
                }

                document.getElementById('statProgressLabel').innerHTML = `<span style="color: var(--text-secondary);">خامل (Idle)</span>`;

            }

        } catch (err) {

            console.error("Error polling batch status:", err);

        }

    }

    // Fetch review candidates

    async function fetchCurationProducts() {

        try {

            const res = await fetch('/api/products-json');

            const data = await res.json();

            if (data.products) {

                currentProducts = data.products;

                

                // Calculate quality statistics breakdown

                const pending = currentProducts.filter(p => p.needs_review);

                document.getElementById('statQueueCount').innerText = pending.length;

                

                let excellentCount = 0;

                let mediumCount = 0;

                let weakCount = 0;

                

                pending.forEach(p => {

                    const score = p.clip_score || 0.0;

                    if (score >= 0.95) excellentCount++;

                    else if (score >= 0.90) mediumCount++;

                    else weakCount++;

                });

                

                document.getElementById('statQualityText').innerText = `Excellent: ${excellentCount} | Med: ${mediumCount} | Weak: ${weakCount}`;

                

                // Initialize filters

                onCurationFilterChange();

                updatePreflightEstimates();

            }

        } catch (err) {

            console.error(err);

        }

    }

    // Update preflight estimates based on loaded products & filters

    function updatePreflightEstimates() {

        if (!currentProducts || currentProducts.length === 0) return;

        

        let pending = currentProducts;

        

        // Apply brand filter

        const brandQuery = document.getElementById('brandFilter').value.trim().toLowerCase();

        if (brandQuery) {

            pending = pending.filter(p => p.brand && p.brand.toLowerCase().includes(brandQuery));

        }

        

        // Apply row filter

        const rowQuery = document.getElementById('rowFilter').value.trim();

        if (rowQuery) {

            try {

                let allowed = new Set();

                const parts = rowQuery.split(',');

                parts.forEach(part => {

                    part = part.trim();

                    if (part.includes('-')) {

                        const [start, end] = part.split('-').map(Number);

                        for (let i = start; i <= end; i++) allowed.add(i);

                    } else {

                        allowed.add(Number(part));

                    }

                });

                pending = pending.filter(p => allowed.has(p.row_number));

            } catch (e) {

                console.error("Error parsing row filter for estimates", e);

            }

        }

        

        // Estimate values

        const rowCount = pending.length;

        const autoThresh = parseFloat(document.getElementById('autoApproveThreshold').value);

        

        // Time estimate: ~6 seconds per row on average

        const estSec = rowCount * 6;

        const estMin = Math.round(estSec / 60);

        const estTimeText = estSec < 60 ? `${estSec} ثانية` : `${estMin} دقيقة (${estSec} ثانية)`;

        

        // Gemini estimate: 2 calls per row

        const geminiCalls = rowCount * 2;

        

        document.getElementById('estimateRows').innerText = `${rowCount} صف`;

        document.getElementById('estimateTime').innerText = estTimeText;

        document.getElementById('estimateGemini').innerText = `${geminiCalls} طلب`;

    }

    function checkTitleForAllergens(title) {

        if (!title) return '';

        const allergens = ['مكسرات', 'حليب', 'فول سوداني', 'قمح', 'صويا', 'سمسم', 'بيض', 'nut', 'milk', 'peanut', 'wheat', 'soy', 'sesame', 'egg'];

        const found = allergens.filter(allg => title.toLowerCase().includes(allg));

        if (found.length > 0) {

            return found.join(', ');

        }

        return '';

    }

    function renderBatchCurationGrid() {

        const grid = document.getElementById('batchCurationGrid');

        grid.innerHTML = '';

        

        const totalPending = currentProducts.filter(p => p.needs_review).length;

        document.getElementById('curationPendingCount').innerText = `${totalPending} منتج بانتظار المراجعة`;

        document.getElementById('statQueueCount').innerText = totalPending;

        

        if (filteredProducts.length === 0) {

            grid.innerHTML = '<p style="color: var(--text-secondary); text-align: center; padding: 3rem; font-weight: bold;">🎉 لا توجد نتائج مطابقة لخيارات الفلترة الحالية.</p>';

            document.getElementById('curationPageInfo').innerText = "صفحة 0 من 0";

            return;

        }



        // Paginate slice

        const maxPage = Math.ceil(filteredProducts.length / pageSize) || 1;

        if (currentPage > maxPage) currentPage = maxPage;

        

        document.getElementById('curationPageInfo').innerText = `صفحة ${currentPage} من ${maxPage}`;

        

        const start = (currentPage - 1) * pageSize;

        const end = start + pageSize;

        const pageItems = filteredProducts.slice(start, end);

        

        pageItems.forEach((p, index) => {

            const card = document.createElement('div');

            card.className = 'curation-row-card';

            if (index === focusedCardIndex) {

                card.className += ' focused-card';

            }

            card.id = `batch-card-${p.row_number}`;

            card.style.position = 'relative';



            let candidatesList = p.curation_candidates || [];

            if (candidatesList.length === 0 && p.needs_review_url) {

                candidatesList.push({

                    image_url: p.needs_review_url,

                    title: "الصورة المقترحة الافتراضية",

                    width: 800,

                    height: 800,

                    clip_score: p.clip_score || 0.0,

                    source_domain: "سحابة النظام",

                    is_selected: 1

                });

            }



            const selectedCandidate = candidatesList.find(c => c.is_selected === 1) || candidatesList[0];

            const defaultUrl = selectedCandidate ? selectedCandidate.image_url : (p.needs_review_url || '');



            card.innerHTML = `

                <div style="flex: 0 0 300px; display: flex; align-items: flex-start; gap: 0.85rem;">

                    <input type="checkbox" class="batch-select-checkbox" data-row="${p.row_number}" data-url="${defaultUrl}" data-name="${p.product_name.replace(/"/g, '&quot;')}" data-brand="${p.brand.replace(/"/g, '&quot;')}" checked style="width: 22px; height: 22px; cursor: pointer; margin-top: 0.25rem; accent-color: var(--accent-purple);" onchange="toggleBatchRowSelect(this, ${p.row_number})">

                    <div style="display: flex; flex-direction: column; gap: 0.35rem; width: calc(100% - 35px);">

                        <span class="badge-row-number" style="align-self: flex-start; background: var(--active-menu-bg); border-color: var(--panel-border); color: var(--text-primary); font-weight: 800; font-size: 0.75rem; padding: 2px 8px; border-radius: 6px;">صف ${p.row_number}</span>

                        <h4 style="font-size: 0.95rem; font-weight: 800; margin: 0.25rem 0 0; color: var(--text-primary); text-overflow: ellipsis; overflow: hidden; white-space: nowrap;" title="${p.product_name}">${p.product_name}</h4>

                        <p style="font-size: 0.8rem; color: var(--text-secondary); margin: 0; font-weight: 600;">البراند: <strong style="color: var(--text-primary);">${p.brand}</strong></p>

                        

                        ${(() => {

                            const allergens = checkTitleForAllergens(p.product_name);

                            return allergens ? `<span style="background: var(--danger-bg); border: 1px solid var(--panel-border); color: var(--danger); font-size: 0.7rem; font-weight: 800; padding: 2px 6px; border-radius: 4px; margin-top: 0.35rem; display: inline-block; align-self: flex-start;"><i class="fas fa-exclamation-triangle"></i> يحتوي: ${allergens}</span>` : '';

                        })()}

                        

                        ${(p.curation_candidates && p.curation_candidates.length > 0) ? `<span style="background: var(--active-menu-bg); border: 1px solid var(--panel-border); color: var(--text-primary); font-size: 0.7rem; font-weight: 800; padding: 2px 6px; border-radius: 4px; margin-top: 0.35rem; display: inline-block; align-self: flex-start;"><i class="fas fa-bolt"></i> جاهز للمراجعة (Cached)</span>` : ''}

                        

                        <div style="display: flex; align-items: center; gap: 0.35rem; margin-top: 0.5rem; width: 100%;">

                            <input type="text" id="inline-query-${p.row_number}" value="${p.brand ? p.product_name + ' ' + p.brand : p.product_name}" style="flex: 1; font-size: 0.75rem; padding: 4px 8px; background: rgba(0,0,0,0.25); border: 1px solid var(--panel-border); color: var(--text-primary); border-radius: 6px; outline: none; width: calc(100% - 35px);" title="كلمات البحث">

                            <button type="button" class="btn btn-secondary btn-sm" onclick="triggerInlineSearch(${p.row_number})" style="padding: 4px; font-size: 0.7rem; display: flex; align-items: center; justify-content: center; height: 26px; width: 26px;" title="إعادة البحث بالكلمات المكتوبة">

                                <i class="fas fa-sync-alt" id="inline-spinner-${p.row_number}"></i>

                            </button>

                        </div>

                        

                        <div style="display: flex; align-items: center; gap: 0.35rem; margin-top: 0.35rem; width: 100%;">

                            <input type="text" id="inline-url-${p.row_number}" placeholder="أو الصق رابط صورة مخصص هنا..." style="flex: 1; font-size: 0.75rem; padding: 4px 8px; background: rgba(0,0,0,0.25); border: 1px solid var(--panel-border); color: var(--text-primary); border-radius: 6px; outline: none; width: calc(100% - 35px);">

                            <button type="button" class="btn btn-secondary btn-sm" onclick="addCustomImageUrl(${p.row_number})" style="padding: 4px; font-size: 0.7rem; display: flex; align-items: center; justify-content: center; height: 26px; width: 26px; background: var(--active-menu-bg);" title="إضافة الرابط يدوياً">

                                <i class="fas fa-plus"></i>

                            </button>

                        </div>

                    </div>

                </div>



                <div class="candidates-grid-gallery">

                    ${candidatesList.map((c, cIdx) => {

                        const isSelected = c.is_selected === 1 ? 'checked' : '';

                        const activeClass = c.is_selected === 1 ? 'active-candidate' : '';

                        const scorePercent = c.clip_score ? Math.round(c.clip_score * 100) : 0;

                        const scoreBadge = scorePercent > 0 ? `<span style="position: absolute; bottom: 4px; left: 4px; background: rgba(0, 0, 0, 0.75); border: 1px solid var(--panel-border); color: #ffffff; font-size: 0.65rem; font-family: 'Outfit', sans-serif; font-weight: 900; padding: 1px 4px; border-radius: 4px;">${scorePercent}% Match</span>` : '';

                        const domainText = c.source_domain ? c.source_domain.replace('www.', '') : 'Unknown';

                        const hasAllergen = checkTitleForAllergens(c.title || '');

                        const allergenIcon = hasAllergen ? `<span style="position: absolute; top: 4px; left: 4px; color: var(--danger); font-size: 0.8rem; filter: drop-shadow(0 0 4px var(--btn-shadow)); z-index: 6;" title="تحذير مسببات حساسية: ${hasAllergen}"><i class="fas fa-exclamation-triangle"></i></span>` : '';

                        const checkmark = c.is_selected === 1 ? `<span class="selected-check" style="position: absolute; top: 4px; right: 4px; background: var(--accent-gradient); color: var(--btn-text); border-radius: 50%; width: 20px; height: 20px; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; z-index: 10; box-shadow: 0 0 8px var(--btn-shadow);"><i class="fas fa-check"></i></span>` : '';

                        

                        return `

                            <div class="curation-thumb-card ${activeClass}" onclick="selectCurationThumb(this, ${p.row_number}, '${c.image_url.replace(/'/g, "\\'")}')" style="position: relative; flex: 0 0 110px; width: 110px; height: 110px; border-radius: 14px; border: 2px solid var(--panel-border); overflow: hidden; cursor: pointer; transition: all 0.25s ease;" title="${c.title || ''} (${domainText})">

                                <img src="${getImageUrl(c.image_url)}" alt="Candidate image" style="width: 100%; height: 100%; object-fit: cover;" onerror="this.src='https://placehold.co/110x110?text=Error'">

                                <input type="radio" name="batch-candidate-radio-${p.row_number}" value="${c.image_url}" ${isSelected} style="display: none;">

                                ${checkmark}

                                ${scoreBadge}

                                ${allergenIcon}

                            </div>

                        `;

                    }).join('')}

                </div>



                <div style="flex: 0 0 160px; display: flex; flex-direction: column; gap: 0.5rem; justify-content: center; align-items: stretch;">

                    <button type="button" class="btn btn-secondary btn-sm" onclick="toggleBatchRowExclude(${p.row_number})" id="btn-exclude-${p.row_number}" style="font-size: 0.75rem; font-weight: bold; padding: 0.45rem 0.5rem; background: var(--danger-bg); color: var(--danger); border-color: var(--panel-border); border-radius: 10px; display: flex; align-items: center; justify-content: center; gap: 0.35rem;">

                        <i class="fas fa-times-circle"></i> <span class="btn-text">استبعاد وتخطي</span>

                    </button>

                </div>

            `;

            grid.appendChild(card);

        });

    }



    function toggleBatchRowSelect(cb, rowNum) {

        const card = document.getElementById(`batch-card-${rowNum}`);

        const btn = document.getElementById(`btn-exclude-${rowNum}`);

        if (!card || !btn) return;

        

        if (cb.checked) {

            card.style.opacity = '1.0';

            btn.innerHTML = '<i class="fas fa-times-circle"></i> <span class="btn-text">استبعاد وتخطي</span>';

            btn.style.background = 'var(--danger-bg)';

            btn.style.color = 'var(--danger)';

            btn.style.borderColor = 'var(--panel-border)';

        } else {

            card.style.opacity = '0.4';

            btn.innerHTML = '<i class="fas fa-undo"></i> <span class="btn-text">إلغاء الاستبعاد</span>';

            btn.style.background = 'var(--success-bg)';

            btn.style.color = 'var(--success)';

            btn.style.borderColor = 'var(--panel-border)';

        }

    }

    function toggleBatchRowExclude(rowNum) {

        const card = document.getElementById(`batch-card-${rowNum}`);

        if (!card) return;

        

        const cb = card.querySelector('.batch-select-checkbox');

        const btn = document.getElementById(`btn-exclude-${rowNum}`);

        if (!cb || !btn) return;

        

        const isExcluded = cb.checked;

        cb.checked = !isExcluded;

        toggleBatchRowSelect(cb, rowNum);

    }

    function selectCurationThumb(thumbEl, rowNum, imageUrl) {

        const card = document.getElementById(`batch-card-${rowNum}`);

        if (!card) return;

        

        card.querySelectorAll('.curation-thumb-card').forEach(t => {

            t.classList.remove('active-candidate');

            const check = t.querySelector('.selected-check');

            if (check) check.remove();

        });

        

        thumbEl.classList.add('active-candidate');

        

        // Add checkmark

        const check = document.createElement('span');

        check.className = 'selected-check';

        check.style = "position: absolute; top: 4px; right: 4px; background: var(--accent-gradient); color: var(--btn-text); border-radius: 50%; width: 20px; height: 20px; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; z-index: 10; box-shadow: 0 0 8px var(--btn-shadow);";

        check.innerHTML = '<i class="fas fa-check"></i>';

        thumbEl.appendChild(check);

        

        const radio = thumbEl.querySelector('input[type="radio"]');

        if (radio) radio.checked = true;

        

        const cb = card.querySelector('.batch-select-checkbox');

        if (cb) cb.dataset.url = imageUrl;

        // Update local memory state so selection persists across pagination/filtering
        if (currentProducts) {
            const product = currentProducts.find(p => p.row_number === rowNum);
            if (product && product.curation_candidates) {
                product.curation_candidates.forEach(c => {
                    if (c.image_url === imageUrl) {
                        c.is_selected = 1;
                    } else {
                        c.is_selected = 0;
                    }
                });
            }
        }

    }

    function selectAllBatch(val) {

        document.querySelectorAll('.batch-select-checkbox').forEach(cb => {

            cb.checked = val;

            const rowNum = cb.dataset.row;

            toggleBatchRowSelect(cb, rowNum);

        });

    }

    // Submit approvals to Google Sheets + Cloudinary

    async function submitBatchApproval() {

        const selectedCbs = Array.from(document.querySelectorAll('.batch-select-checkbox:checked'));

        if (selectedCbs.length === 0) {

            alert('❌ يرجى تحديد منتج واحد على الأقل للاعتماد.');

            return;

        }

        if (!confirm(`هل أنت متأكد من اعتماد ورفع الصور لـ ${selectedCbs.length} منتجات دفعة واحدة؟`)) {

            return;

        }

        const approveBtn = document.getElementById('batchApproveBtn');

        const rejectBtn = document.getElementById('batchRejectBtn');

        const progressDiv = document.getElementById('batchCurationProgress');

        const progressBar = document.getElementById('batchCurationProgressBar');

        const progressPercent = document.getElementById('batchCurationProgressPercent');

        approveBtn.disabled = true;

        rejectBtn.disabled = true;

        progressDiv.style.display = 'flex';

        const total = selectedCbs.length;

        let completed = 0;

        let success = 0;

        let failed = 0;

        const w = 800; // Target output dimensions

        const h = 800;

        const paddingRatio = parseFloat(document.getElementById('paddingRatio').value);

        const bgColor = document.getElementById('bgColor').value;

        const bgRemovalMethod = document.getElementById('bgRemovalMethod').value;

        const aiUpscale = document.getElementById('aiUpscale').value === 'true';

        const aiEnhance = document.getElementById('aiEnhance').value === 'true';

        for (const cb of selectedCbs) {

            const row = cb.dataset.row;

            const url = cb.dataset.url;

            const name = cb.dataset.name;

            const brand = cb.dataset.brand;

            completed++;

            const percent = Math.round((completed / total) * 100);

            progressPercent.innerText = percent + '%';

            progressBar.style.width = percent + '%';

            

            appendTerminalLine('Ingestion', `جاري رفع الصف ${row}: ${name}...`, 'system');

            try {

                const res = await fetch('/api/select_image', {

                    method: 'POST',

                    headers: {

                        'Content-Type': 'application/json',

                        'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content

                    },

                    body: JSON.stringify({

                        image_url: url,

                        product_name: name,

                        brand: brand,

                        row_number: row,

                        upscale: aiUpscale,

                        enhance: aiEnhance,

                        bg_removal_method: bgRemovalMethod,

                        target_width: w,

                        target_height: h,

                        padding_ratio: paddingRatio,

                        bg_color: bgColor

                    })

                });

                

                const data = await res.json();

                if (data.status === 'success') {

                    success++;

                    const card = cb.closest('.curation-row-card');

                    if (card) {

                        card.style.borderColor = 'var(--success)';

                        card.style.background = 'var(--success-bg)';

                    }

                } else {

                    failed++;

                    const card = cb.closest('.curation-row-card');

                    if (card) {

                        card.style.borderColor = 'var(--danger)';

                        card.style.background = 'var(--danger-bg)';

                    }

                    appendTerminalLine('Ingestion Failed', `فشل رفع الصف ${row}: ${data.error || 'خطأ غير معروف'}`, 'error');

                }

            } catch (err) {

                console.error(err);

                failed++;

                appendTerminalLine('Ingestion Failed', `فشل رفع الصف ${row}: ${err.message || err}`, 'error');

            }

        }

        approveBtn.disabled = false;

        rejectBtn.disabled = false;

        progressDiv.style.display = 'none';

        

        appendTerminalLine('Ingestion Finished', `اكتمل الرفع. نجاح: ${success} | فشل: ${failed}`, 'success');

        alert(`🎉 اكتملت المعالجة الجماعية. نجاح: ${success} | فشل: ${failed}`);

        fetchCurationProducts();

    }

    async function triggerInlineSearch(rowNumber) {

        const queryInput = document.getElementById(`inline-query-${rowNumber}`);

        const spinner = document.getElementById(`inline-spinner-${rowNumber}`);

        if (!queryInput) return;

        

        const queryText = queryInput.value.trim();

        if (!queryText) {

            alert('الرجاء كتابة كلمات بحث صحيحة.');

            return;

        }

        

        spinner.classList.add('fa-spin');

        const p = currentProducts.find(prod => prod.row_number === rowNumber);

        if (!p) return;

        

        try {

            const res = await fetch('/api/search', {

                method: 'POST',

                headers: {

                    'Content-Type': 'application/json',

                    'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content

                },

                body: JSON.stringify({

                    product_name: p.product_name,

                    brand: p.brand,

                    custom_query: queryText,

                    barcode: p.barcode,

                    skip_cache: true

                })

            });

            

            const data = await res.json();

            if (data.status === 'success' && data.selected_image) {

                let newCandidates = [];

                if (data.trace && data.trace.steps) {

                    const seen = new Set();

                    data.trace.steps.forEach(step => {

                        if (step.candidates) {

                            step.candidates.forEach(c => {

                                if (c.url && !seen.has(c.url)) {

                                    seen.add(c.url);

                                    newCandidates.push({

                                        image_url: c.url,

                                        title: c.title,

                                        clip_score: c.relevance_score || c.clip_score || 0.0,

                                        source_domain: c.source_domain || ''

                                    });

                                }

                            });

                        }

                    });

                }

                

                if (newCandidates.length === 0) {

                    newCandidates.push({

                        image_url: data.selected_image.url,

                        title: data.selected_image.title,

                        clip_score: data.selected_image.clip_score || 0.0

                    });

                }

                

                p.curation_candidates = newCandidates;

                renderBatchCurationGrid();

            } else {

                alert('فشل البحث: لم يتم العثور على أي نتائج.');

            }

        } catch (err) {

            console.error(err);

            alert('حدث خطأ أثناء إجراء البحث.');

        } finally {

            spinner.classList.remove('fa-spin');

        }

    }

    function addCustomImageUrl(rowNumber) {

        const urlInput = document.getElementById(`inline-url-${rowNumber}`);

        if (!urlInput) return;

        

        const urlText = urlInput.value.trim();

        if (!urlText || !urlText.startsWith('http')) {

            alert('الرجاء إدخال رابط صورة صحيح يبدأ بـ http أو https.');

            return;

        }

        

        const p = currentProducts.find(prod => prod.row_number === rowNumber);

        if (!p) return;

        

        if (!p.curation_candidates) {

            p.curation_candidates = [];

        }

        

        // Unselect existing candidates

        p.curation_candidates.forEach(c => {

            c.is_selected = 0;

        });

        

        // Add new candidate at start

        p.curation_candidates.unshift({

            image_url: urlText,

            title: "رابط مخصص يدوي",

            width: 800,

            height: 800,

            clip_score: 1.0,

            source_domain: "رابط مخصص",

            is_selected: 1

        });

        

        // Update checkbox URL datasets in UI

        const cb = document.querySelector(`.batch-select-checkbox[data-row="${rowNumber}"]`);

        if (cb) {

            cb.dataset.url = urlText;

        }

        

        renderBatchCurationGrid();

    }

    // Filter changed

    function onCurationFilterChange() {

        if (!currentProducts) return;

        

        let pending = currentProducts.filter(p => p.needs_review);

        

        // 1. Text search

        const query = document.getElementById('curationSearch').value.trim().toLowerCase();

        if (query) {

            pending = pending.filter(p => 

                (p.product_name && p.product_name.toLowerCase().includes(query)) ||

                (p.brand && p.brand.toLowerCase().includes(query))

            );

        }

        

        // 2. Quality filter

        const quality = document.getElementById('curationQualityFilter').value;

        if (quality === 'excellent') {

            pending = pending.filter(p => (p.clip_score || 0.0) >= 0.95);

        } else if (quality === 'medium') {

            pending = pending.filter(p => (p.clip_score || 0.0) >= 0.90 && (p.clip_score || 0.0) < 0.95);

        } else if (quality === 'weak') {

            pending = pending.filter(p => (p.clip_score || 0.0) < 0.90);

        }

        

        // 3. Allergen filter

        const allergenOnly = document.getElementById('curationAllergenFilter').checked;

        if (allergenOnly) {

            pending = pending.filter(p => checkTitleForAllergens(p.product_name) !== '');

        }

        

        // 4. Excluded only filter

        const excludedOnly = document.getElementById('curationExcludedFilter').checked;

        if (excludedOnly) {

            pending = pending.filter(p => {

                const card = document.getElementById(`batch-card-${p.row_number}`);

                if (card) {

                    const cb = card.querySelector('.batch-select-checkbox');

                    return cb && !cb.checked;

                }

                return false;

            });

        }

        

        filteredProducts = pending;

        document.getElementById('curationFilteredCount').innerText = `يتم عرض: ${filteredProducts.length} من ${currentProducts.filter(p => p.needs_review).length} منتج`;

        

        currentPage = 1;

        focusedCardIndex = -1;

        renderBatchCurationGrid();

    }

    

    // Page Size changed

    function onPageSizeChange() {

        pageSize = parseInt(document.getElementById('curationPageSize').value);

        currentPage = 1;

        focusedCardIndex = -1;

        renderBatchCurationGrid();

    }

    

    // Page navigation

    function changeCurationPage(dir) {

        const maxPage = Math.ceil(filteredProducts.length / pageSize) || 1;

        const next = currentPage + dir;

        if (next >= 1 && next <= maxPage) {

            currentPage = next;

            focusedCardIndex = -1;

            renderBatchCurationGrid();

            // Scroll smoothly to grid top

            document.getElementById('batchCurationGrid').scrollIntoView({ behavior: 'smooth', block: 'start' });

        }

    }

    

    // Select candidates above 95% CLIP match

    function selectHighMatchBatch() {

        filteredProducts.forEach(p => {

            const score = p.clip_score || 0.0;

            const card = document.getElementById(`batch-card-${p.row_number}`);

            if (card) {

                const cb = card.querySelector('.batch-select-checkbox');

                if (cb) {

                    cb.checked = (score >= 0.95);

                    toggleBatchRowSelect(cb, p.row_number);

                }

            }

        });

    }

    

    // Export curation list to CSV file

    function exportCurationToCSV() {

        if (!filteredProducts || filteredProducts.length === 0) {

            alert("لا توجد منتجات لتصديرها.");

            return;

        }

        

        let csvContent = "\ufeff"; // BOM for UTF-8

        csvContent += "الصف,اسم المنتج,البراند,الباركود,نسبة المطابقة (CLIP),الصورة المختارة\n";

        

        filteredProducts.forEach(p => {

            let selectedUrl = "";

            const card = document.getElementById(`batch-card-${p.row_number}`);

            if (card) {

                const active = card.querySelector('.curation-thumb-card.active-candidate input[type="radio"]');

                if (active) selectedUrl = active.value;

            }

            if (!selectedUrl) {

                const candidates = p.curation_candidates || [];

                selectedUrl = candidates.length > 0 ? candidates[0].image_url : (p.needs_review_url || "");

            }

            

            const nameClean = (p.product_name || "").replace(/"/g, '""');

            const brandClean = (p.brand || "").replace(/"/g, '""');

            const barcodeClean = (p.barcode || "").replace(/"/g, '""');

            

            csvContent += `${p.row_number},"${nameClean}","${brandClean}","${barcodeClean}",${(p.clip_score || 0.0).toFixed(4)},"${selectedUrl}"\n`;

        });

        

        const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });

        const link = document.createElement("a");

        const url = URL.createObjectURL(blob);

        link.setAttribute("href", url);

        link.setAttribute("download", `Curation_Report_${new Date().toISOString().slice(0,10)}.csv`);

        link.style.visibility = 'hidden';

        document.body.appendChild(link);

        link.click();

        document.body.removeChild(link);

    }

    

    // Keyboard shortcuts handler for fast gaming-like curation

    document.addEventListener('keydown', function(e) {

        // Only run if the curation grid has products and is visible

        const workspace = document.getElementById('batchCurationWorkspace');

        if (!workspace || workspace.style.display === 'none' || filteredProducts.length === 0) return;

        

        // Avoid conflict if the user is typing in input fields

        if (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'SELECT') {

            return;

        }

        

        const pageItems = getPageItemsSlice();

        if (pageItems.length === 0) return;

        

        if (e.key === 'ArrowDown' || e.key === 'ArrowRight') {

            e.preventDefault();

            focusedCardIndex = (focusedCardIndex + 1) % pageItems.length;

            highlightFocusedCard();

        } else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') {

            e.preventDefault();

            focusedCardIndex = (focusedCardIndex - 1 + pageItems.length) % pageItems.length;

            highlightFocusedCard();

        } else if (e.key === ' ' || e.key === 'Spacebar') {

            // Space to toggle Exclude of active card

            e.preventDefault();

            if (focusedCardIndex >= 0 && focusedCardIndex < pageItems.length) {

                const activeProduct = pageItems[focusedCardIndex];

                toggleBatchRowExclude(activeProduct.row_number);

            }

        } else if (e.key === '1' || e.key === '2' || e.key === '3') {

            // Select candidate image 1, 2 or 3

            e.preventDefault();

            if (focusedCardIndex >= 0 && focusedCardIndex < pageItems.length) {

                const activeProduct = pageItems[focusedCardIndex];

                const card = document.getElementById(`batch-card-${activeProduct.row_number}`);

                if (card) {

                    const thumbs = card.querySelectorAll('.curation-thumb-card');

                    const index = parseInt(e.key) - 1;

                    if (index >= 0 && index < thumbs.length) {

                        thumbs[index].click();

                    }

                }

            }

        } else if (e.key === 'Enter') {

            // Enter to submit approval

            e.preventDefault();

            submitBatchApproval();

        }

    });

    

    function getPageItemsSlice() {

        const start = (currentPage - 1) * pageSize;

        const end = start + pageSize;

        return filteredProducts.slice(start, end);

    }

    

    function highlightFocusedCard() {

        const pageItems = getPageItemsSlice();

        // Remove focus class from all cards

        document.querySelectorAll('.curation-row-card').forEach(card => {

            card.classList.remove('focused-card');

        });

        

        if (focusedCardIndex >= 0 && focusedCardIndex < pageItems.length) {

            const activeProduct = pageItems[focusedCardIndex];

            const activeCard = document.getElementById(`batch-card-${activeProduct.row_number}`);

            if (activeCard) {

                activeCard.classList.add('focused-card');

                // Scroll to focused card if off screen

                activeCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

            }

        }

    }



    // Google Sheets Fetcher & Preview Actions
    async function previewGoogleSheet() {
        const urlInput = document.getElementById('spreadsheetUrlOrName').value.trim();
        const tabInput = document.getElementById('spreadsheetTab').value.trim();
        if (!urlInput) {
            alert('❌ يرجى إدخال رابط أو اسم ملف Google Sheet أولاً.');
            return;
        }
        
        const btn = document.getElementById('previewSheetBtn');
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> جاري الاتصال وجلب المعاينة...';
        
        const previewContainer = document.getElementById('sheetPreviewContainer');
        previewContainer.style.display = 'none';
        
        try {
            const res = await fetch('/api/sheet/preview', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content
                },
                body: JSON.stringify({
                    spreadsheet_url: urlInput,
                    tab_name: tabInput
                })
            });
            const data = await res.json();
            btn.disabled = false;
            btn.innerHTML = originalText;
            
            if (data.status === 'success') {
                const table = document.getElementById('sheetPreviewTable');
                const thead = table.querySelector('thead tr');
                const tbody = table.querySelector('tbody');
                
                thead.innerHTML = '';
                tbody.innerHTML = '';
                
                if (data.headers.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="100%" style="text-align:center; padding: 1rem; color:var(--text-secondary);">جدول البيانات فارغ تماماً.</td></tr>';
                } else {
                    // Inject headers
                    data.headers.forEach(h => {
                        const th = document.createElement('th');
                        th.style = "padding: 0.55rem 0.85rem; border: 1px solid var(--panel-border); font-weight: bold; background: rgba(255,255,255,0.02);";
                        th.innerText = h || '-';
                        thead.appendChild(th);
                    });
                    
                    // Inject rows
                    data.rows.forEach(row => {
                        const tr = document.createElement('tr');
                        tr.style = "border-bottom: 1px solid rgba(255,255,255,0.03);";
                        row.forEach(val => {
                            const td = document.createElement('td');
                            td.style = "padding: 0.55rem 0.85rem; border: 1px solid var(--panel-border); color: var(--text-secondary);";
                            td.innerText = val || '';
                            tr.appendChild(td);
                        });
                        tbody.appendChild(tr);
                    });
                }
                previewContainer.style.display = 'block';
                alert('✅ تم جلب ومعاينة البيانات بنجاح!');
            } else {
                alert('❌ فشل جلب الشيت: ' + data.error);
            }
        } catch (err) {
            console.error(err);
            btn.disabled = false;
            btn.innerHTML = originalText;
            alert('❌ حدث خطأ أثناء الاتصال بالخادم لجلب الشيت.');
        }
    }

    async function saveGoogleSheetConfig() {
        const urlInput = document.getElementById('spreadsheetUrlOrName').value.trim();
        const tabInput = document.getElementById('spreadsheetTab').value.trim();
        if (!urlInput) {
            alert('❌ يرجى إدخال رابط أو اسم ملف Google Sheet أولاً.');
            return;
        }
        
        if (!confirm('⚠️ عند حفظ المزامنة، سيتم إعادة ضبط كاش المنتجات بالكامل ليعكس بيانات الملف الجديد. هل ترغب في الاستمرار؟')) {
            return;
        }
        
        const btn = document.getElementById('saveSheetBtn');
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> جاري الحفظ وتصفير الكاش...';
        
        try {
            const res = await fetch('/api/sheet/save', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content
                },
                body: JSON.stringify({
                    spreadsheet_url: urlInput,
                    tab_name: tabInput
                })
            });
            const data = await res.json();
            btn.disabled = false;
            btn.innerHTML = originalText;
            
            if (data.status === 'success') {
                alert('✅ تم حفظ إعدادات مزامنة الشيت الجديد وتحديث البيئة وتصفير الكاش بنجاح! سيتم تحديث الصفحة الآن.');
                location.reload();
            } else {
                alert('❌ فشل حفظ الإعدادات: ' + data.error);
            }
        } catch (err) {
            console.error(err);
            btn.disabled = false;
            btn.innerHTML = originalText;
            alert('❌ خطأ في الاتصال بالخادم لحفظ الإعدادات.');
        }
    }

    // Real-Time Curation Grid Manager via Server-Sent Events (SSE)
    class CurationGridManager {
        constructor(sessionId, sseBaseUrl, mutateBaseUrl) {
            this.sessionId = sessionId;
            this.sseBaseUrl = sseBaseUrl;
            this.mutateBaseUrl = mutateBaseUrl;
            this.state = {
                products: new Map(),
                metrics: { total: 0, completed: 0, percentage: 0 }
            };
            this.eventSource = null;
            this.initSseStream();
        }

        initSseStream() {
            if (this.eventSource) {
                this.eventSource.close();
            }
            const streamUrl = `${this.sseBaseUrl}/${this.sessionId}`;
            try {
                this.eventSource = new EventSource(streamUrl);
                this.eventSource.addEventListener('curation_pending', (event) => {
                    try {
                        const productData = JSON.parse(event.data);
                        this.ingestProduct(productData);
                    } catch (error) {
                        console.error("[SSE Stream] Event parse error:", error);
                    }
                });
                this.eventSource.onerror = (err) => {
                    console.warn("[SSE Stream] Reconnecting stream...", err);
                };
            } catch (ex) {
                console.warn("[SSE Stream] EventSource init fallback:", ex);
            }
        }

        ingestProduct(product) {
            if (!product || !product.id) return;
            if (this.state.products.has(product.id)) return;

            const normalizedProduct = {
                id: product.id,
                title: product.title || `منتج ${product.id}`,
                description: product.description || '',
                status: 'pending',
                syncStatus: 'synced',
                rollbackData: null
            };

            this.state.products.set(product.id, normalizedProduct);
            if (typeof fetchCurationProducts === 'function') {
                fetchCurationProducts();
            }
        }

        async submitCuration(productId, decision) {
            const product = this.state.products.get(productId) || { id: productId, status: 'pending', syncStatus: 'synced' };
            const snapshot = { ...product };

            product.status = decision;
            product.syncStatus = 'mutating';
            product.rollbackData = snapshot;
            this.state.products.set(productId, product);

            try {
                const response = await fetch(`${this.mutateBaseUrl}/${productId}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Accept': 'application/json',
                        'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || ''
                    },
                    body: JSON.stringify({
                        decision: decision,
                        session_id: this.sessionId
                    })
                });

                if (!response.ok) {
                    throw new Error(`Server status: ${response.status}`);
                }

                const confirmedProduct = this.state.products.get(productId);
                if (confirmedProduct) {
                    confirmedProduct.syncStatus = 'synced';
                    confirmedProduct.rollbackData = null;
                    this.state.products.set(productId, confirmedProduct);
                }
            } catch (error) {
                console.error(`[Curation Mutation Error] ${productId}:`, error);
                this.rollbackProductState(productId);
            }
        }

        rollbackProductState(productId) {
            const product = this.state.products.get(productId);
            if (product && product.rollbackData) {
                const previousState = product.rollbackData;
                previousState.syncStatus = 'error';
                this.state.products.set(productId, previousState);
                console.warn(`[Curation Rollback] Reverted state for product ${productId}`);
            }
        }
    }

    document.addEventListener("DOMContentLoaded", () => {
        pollBatchStatus();
        setInterval(pollBatchStatus, 3000);

        pollLogs();
        setInterval(pollLogs, 2500);

        fetchCurationProducts();
        window.gridManager = new CurationGridManager('batch_sess_99', '/api/v1/curation/stream', '/api/v1/curation/mutate');
    });

</script>

@endsection

