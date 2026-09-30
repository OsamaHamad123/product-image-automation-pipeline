@extends('layouts.layout')

@section('title', '🛠️ تشخيصات وسجلات النظام')
@section('nav_diagnostics', 'active')

@section('styles')
<style>
    .diagnostics-container {
        direction: rtl;
        text-align: right;
    }

    .services-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 1.25rem;
        margin-top: 1.5rem;
        margin-bottom: 2rem;
    }

    .service-card {
        background: var(--card-bg);
        border: 1px solid var(--panel-border);
        border-radius: 16px;
        padding: 1.25rem;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        min-height: 150px;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        position: relative;
        overflow: hidden;
    }

    .service-card:hover {
        transform: translateY(-4px);
        background: var(--card-bg-hover);
        border-color: var(--panel-border-hover);
        box-shadow: var(--shadow-md);
    }

    .service-header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
    }

    .service-title {
        font-weight: 800;
        font-size: 1.05rem;
        color: var(--text-primary);
        margin-bottom: 0.25rem;
    }

    .service-badge {
        font-size: 0.7rem;
        padding: 0.2rem 0.5rem;
        border-radius: 6px;
        font-weight: 800;
        border: 1px solid transparent;
    }

    .badge-critical {
        background: rgba(239, 68, 68, 0.1);
        color: #ef4444;
        border-color: rgba(239, 68, 68, 0.2);
    }

    .badge-optional {
        background: rgba(163, 163, 163, 0.1);
        color: var(--text-secondary);
        border-color: rgba(163, 163, 163, 0.2);
    }

    .service-status {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        margin-top: 1rem;
    }

    .status-indicator {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        display: inline-block;
    }

    .status-text {
        font-size: 0.85rem;
        font-weight: 700;
    }

    .status-online {
        background-color: #22c55e;
        box-shadow: 0 0 10px #22c55e;
    }

    .status-offline {
        background-color: #ef4444;
        box-shadow: 0 0 10px #ef4444;
    }

    .status-unknown {
        background-color: #a3a3a3;
        box-shadow: 0 0 10px #a3a3a3;
    }

    /* Terminal Console */
    .console-wrapper {
        margin-top: 1.5rem;
    }

    .console-tabs {
        display: flex;
        gap: 0.5rem;
        background: var(--tabs-bg);
        padding: 0.35rem;
        border-radius: 12px;
        border: 1px solid var(--panel-border);
        width: fit-content;
        margin-bottom: 0.75rem;
    }

    .console-tab {
        padding: 0.5rem 1.25rem;
        border-radius: 8px;
        border: none;
        background: transparent;
        color: var(--text-secondary);
        font-weight: 700;
        cursor: pointer;
        transition: all 0.25s;
    }

    .console-tab.active {
        background: var(--card-bg-hover);
        color: var(--text-primary);
        box-shadow: var(--shadow-sm);
        border: 1px solid var(--panel-border);
    }

    .console-controls {
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-wrap: wrap;
        gap: 1rem;
        margin-bottom: 1rem;
    }

    .console-terminal {
        background: #000000;
        border: 1px solid var(--panel-border);
        border-radius: 16px;
        padding: 1.5rem;
        height: 500px;
        overflow-y: auto;
        font-family: 'Consolas', 'Monaco', monospace;
        font-size: 0.88rem;
        line-height: 1.5;
        color: #34d399; /* Emerald Green console */
        text-align: left;
        direction: ltr;
        box-shadow: inset 0 2px 10px rgba(0,0,0,0.9), var(--shadow-md);
        position: relative;
    }

    .console-line {
        margin-bottom: 0.35rem;
        white-space: pre-wrap;
        word-break: break-all;
    }

    .console-line.error {
        color: #ef4444;
    }

    .console-line.warning {
        color: #fbbf24;
    }

    .console-line.info {
        color: #60a5fa;
    }

    .console-line.success {
        color: #34d399;
    }

    .console-search {
        background: var(--input-bg);
        border: 1px solid var(--panel-border);
        border-radius: 8px;
        padding: 0.45rem 1rem;
        color: var(--text-primary);
        font-family: inherit;
        outline: none;
        width: 250px;
        font-size: 0.85rem;
    }

    .console-search:focus {
        border-color: var(--accent-purple);
    }

    .switch-container {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        font-size: 0.85rem;
        color: var(--text-secondary);
        font-weight: 700;
    }

    /* Search health and cost panel (ops_health) */
    .health-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-wrap: wrap;
        gap: 1rem;
        margin-bottom: 1rem;
    }

    .health-alert {
        display: flex;
        gap: 0.75rem;
        align-items: flex-start;
        background: rgba(239, 68, 68, 0.12);
        border: 1px solid rgba(239, 68, 68, 0.45);
        color: #ef4444;
        border-radius: 12px;
        padding: 0.85rem 1rem;
        margin-bottom: 0.75rem;
        font-weight: 800;
    }

    .health-alert-detail {
        font-weight: 600;
        font-size: 0.8rem;
        margin-top: 0.25rem;
        color: var(--text-primary);
    }

    .health-tiles {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 1rem;
        margin-bottom: 1.25rem;
    }

    .health-tile {
        background: var(--card-bg);
        border: 1px solid var(--panel-border);
        border-radius: 14px;
        padding: 1rem;
    }

    .health-tile-label {
        font-size: 0.8rem;
        color: var(--text-secondary);
        font-weight: 700;
    }

    .health-tile-value {
        font-size: 1.5rem;
        font-weight: 900;
        color: var(--text-primary);
        margin-top: 0.25rem;
    }

    .health-tile-sub {
        font-size: 0.75rem;
        color: var(--text-secondary);
        margin-top: 0.25rem;
    }

    .health-tables {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
        gap: 1rem;
    }

    .health-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.85rem;
    }

    .health-table caption {
        text-align: right;
        font-weight: 800;
        color: var(--text-primary);
        padding-bottom: 0.5rem;
    }

    .health-table th,
    .health-table td {
        border-bottom: 1px solid var(--panel-border);
        padding: 0.4rem 0.5rem;
        text-align: right;
        color: var(--text-primary);
    }

    .health-table th {
        color: var(--text-secondary);
        font-weight: 700;
    }

    .health-table td.num {
        font-family: 'Consolas', 'Monaco', monospace;
        direction: ltr;
    }

    .health-empty {
        padding: 1.5rem;
        text-align: center;
        color: var(--text-secondary);
        font-weight: 700;
        border: 1px dashed var(--panel-border);
        border-radius: 12px;
    }

    .health-note {
        font-size: 0.75rem;
        color: var(--text-secondary);
        margin-top: 1rem;
        line-height: 1.7;
    }

    .modal-body-content {
        background: var(--console-bg);
        border: 1px solid var(--panel-border);
        color: var(--text-primary);
        font-family: monospace;
        padding: 1.5rem;
        border-radius: 12px;
        white-space: pre-wrap;
        text-align: left;
        direction: ltr;
        overflow-x: auto;
        max-height: 400px;
    }
</style>
@endsection

@section('content')
<div class="diagnostics-container">
    
    <!-- Header -->
    <div class="glass-panel" style="padding: 1.5rem 2rem; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 1rem;">
        <div>
            <h2 style="font-size: 1.4rem; font-weight: 900; margin: 0; color: var(--text-primary); display: flex; align-items: center; gap: 0.65rem;">
                <i class="fas fa-terminal" style="color: var(--accent-purple);"></i> تشخيصات واشتراكات وسجلات النظام الحية
            </h2>
            <p style="font-size: 0.85rem; color: var(--text-secondary); margin-top: 0.25rem;">
                راقب حالة اتصال الخوادم وتفاصيل استهلاك الـ APIs والسجلات البرمجية الحية دون الحاجة لـ RDP.
            </p>
        </div>
        <div style="display: flex; flex-direction: column; align-items: flex-end; gap: 0.35rem;">
            <button type="button" class="btn" id="runDiagnosticBtn" onclick="runDiagnostics()" style="background: var(--accent-gradient); color: var(--btn-text); font-weight: 800;">
                <i class="fas fa-sync-alt" id="syncIcon"></i> فحص الاتصالات الآن
            </button>
            <span id="diagCheckNote" style="font-size: 0.75rem; color: var(--text-secondary); max-width: 360px;">
                لا يعمل الفحص تلقائياً عند فتح الصفحة. كل فحص يرسل استعلام صور واحداً إلى Serper (يُخصم من رصيده) واستدعاءً واحداً إلى PhotoRoom، ولا يتجاوز 45 ثانية.
            </span>
            <span id="lastCheckInfo" style="font-size: 0.75rem; color: var(--text-secondary); font-weight: bold;"></span>
        </div>
    </div>
    <div id="lastDiagnosticsData" hidden data-result="{{ json_encode($lastDiagnostics ?? null, JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE) }}"></div>

    <!-- Services Cards Grid -->
    <div class="services-grid" id="servicesGrid">
        <!-- Card 1: Google Sheets -->
        <div class="service-card" id="card-google_sheets">
            <div class="service-header">
                <div>
                    <h4 class="service-title">Google Sheets API</h4>
                    <span class="service-badge badge-critical">حرج (Critical)</span>
                </div>
                <i class="fas fa-file-excel" style="font-size: 1.5rem; color: #22c55e;"></i>
            </div>
            <div class="service-status">
                <span class="status-indicator status-unknown" id="ind-google_sheets"></span>
                <span class="status-text text-secondary" id="text-google_sheets">بانتظار الفحص</span>
            </div>
        </div>

        <!-- Card 2: Cloudinary -->
        <div class="service-card" id="card-cloudinary">
            <div class="service-header">
                <div>
                    <h4 class="service-title">Cloudinary CDN</h4>
                    <span class="service-badge badge-critical">حرج (Critical)</span>
                </div>
                <i class="fas fa-cloud-upload-alt" style="font-size: 1.5rem; color: #3b82f6;"></i>
            </div>
            <div class="service-status">
                <span class="status-indicator status-unknown" id="ind-cloudinary"></span>
                <span class="status-text text-secondary" id="text-cloudinary">بانتظار الفحص</span>
            </div>
        </div>

        <!-- Card 3: PhotoRoom -->
        <div class="service-card" id="card-photoroom">
            <div class="service-header">
                <div>
                    <h4 class="service-title">PhotoRoom Cloud API</h4>
                    <span class="service-badge badge-critical">حرج (Critical)</span>
                </div>
                <i class="fas fa-magic" style="font-size: 1.5rem; color: #ec4899;"></i>
            </div>
            <div class="service-status">
                <span class="status-indicator status-unknown" id="ind-photoroom"></span>
                <span class="status-text text-secondary" id="text-photoroom">بانتظار الفحص</span>
            </div>
        </div>

        <!-- Card 4: Gemini -->
        <div class="service-card" id="card-gemini">
            <div class="service-header">
                <div>
                    <h4 class="service-title">Google Gemini API</h4>
                    <span class="service-badge badge-critical">حرج (Critical)</span>
                </div>
                <i class="fas fa-brain" style="font-size: 1.5rem; color: #8b5cf6;"></i>
            </div>
            <div class="service-status">
                <span class="status-indicator status-unknown" id="ind-gemini"></span>
                <span class="status-text text-secondary" id="text-gemini">بانتظار الفحص</span>
            </div>
        </div>


        <!-- Card 5: Serper (Google Images) -->
        <div class="service-card" id="card-serper">
            <div class="service-header">
                <div>
                    <h4 class="service-title">Serper (Google Images)</h4>
                    <span class="service-badge badge-critical">حرج: مصدر الصور الأساسي</span>
                </div>
                <i class="fas fa-search" style="font-size: 1.5rem; color: #22c55e;"></i>
            </div>
            <div class="service-status">
                <span class="status-indicator status-unknown" id="ind-serper"></span>
                <span class="status-text text-secondary" id="text-serper">بانتظار الفحص</span>
            </div>
        </div>

        <!-- Card 6: Proxy Server -->
        <div class="service-card" id="card-proxy">
            <div class="service-header">
                <div>
                    <h4 class="service-title">البروكسي السكني (Proxy)</h4>
                    <span class="service-badge badge-optional">اختياري (Optional)</span>
                </div>
                <i class="fas fa-network-wired" style="font-size: 1.5rem; color: #a855f7;"></i>
            </div>
            <div class="service-status">
                <span class="status-indicator status-unknown" id="ind-proxy"></span>
                <span class="status-text text-secondary" id="text-proxy">بانتظار الفحص</span>
            </div>
        </div>
    </div>

    <!-- Search health and cost (ops_health: automation_queue.trace_json, read-only) -->
    <div class="glass-panel" id="opsHealthPanel" style="margin-bottom: 2rem;">
        <div class="health-header">
            <div>
                <h3 style="font-size: 1.15rem; font-weight: 800; color: var(--text-primary); margin: 0; display: flex; align-items: center; gap: 0.5rem;">
                    <i class="fas fa-heartbeat"></i> صحة البحث وتكلفته
                </h3>
                <p style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 0.25rem;">
                    من سجل عمليات البحث المحفوظ في طابور الأتمتة: القرارات، ردود المزودين، استدعاءات Gemini والتكلفة التقديرية.
                </p>
            </div>
            <div style="display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap;">
                <div class="console-tabs" style="margin-bottom: 0;">
                    <button type="button" class="console-tab active" id="health-tab-24h" onclick="switchHealthWindow('24h')">آخر 24 ساعة</button>
                    <button type="button" class="console-tab" id="health-tab-7d" onclick="switchHealthWindow('7d')">آخر 7 أيام</button>
                </div>
                <button type="button" class="btn btn-secondary btn-sm" id="opsHealthRefreshBtn" onclick="loadOpsHealth(true)">
                    <i class="fas fa-sync-alt"></i> تحديث
                </button>
            </div>
        </div>
        <div id="opsHealthAlerts"></div>
        <div id="opsHealthBody"><div class="health-empty">جاري تحميل ملخص الصحة والتكلفة...</div></div>
        <div class="health-note" id="opsHealthNote"></div>
    </div>

    <!-- Live Logs Console Panel -->
    <div class="glass-panel console-wrapper">
        <h3 style="font-size: 1.15rem; font-weight: 800; color: var(--text-primary); margin-bottom: 1rem; display: flex; align-items: center; gap: 0.5rem;">
            <i class="fas fa-terminal"></i> سجلات خوادم الخلفية والمزامنة الحية (Live Log Viewer)
        </h3>

        <!-- Control Area -->
        <div class="console-controls">
            <div class="console-tabs">
                <button type="button" class="console-tab active" id="tab-pipeline" onclick="switchLogTab('pipeline')">
                    <i class="fas fa-robot"></i> سجل الأتمتة (Python Pipeline)
                </button>
                <button type="button" class="console-tab" id="tab-laravel" onclick="switchLogTab('laravel')">
                    <i class="fas fa-bug"></i> سجل النظام (Laravel Errors)
                </button>
            </div>

            <div style="display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;">
                <!-- Filter Search -->
                <input type="text" id="logSearchInput" class="console-search" oninput="applyLogFilter()" placeholder="🔍 فلترة اللوغز (بحث)...">
                
                <!-- Auto-Scroll Checkbox -->
                <label class="switch-container">
                    <input type="checkbox" id="autoScrollCheck" checked style="width: 16px; height: 16px; cursor: pointer;">
                    <span>تمرير تلقائي (Auto Scroll)</span>
                </label>

                <!-- Auto-Update Checkbox -->
                <label class="switch-container">
                    <input type="checkbox" id="autoUpdateCheck" checked style="width: 16px; height: 16px; cursor: pointer;">
                    <span>تحديث حي (Auto Update)</span>
                </label>

                <!-- Clear Screen button -->
                <button type="button" class="btn btn-secondary btn-sm" onclick="clearConsoleScreen()" style="padding: 0.45rem 1rem;">
                    <i class="fas fa-trash-alt"></i> مسح الشاشة
                </button>
            </div>
        </div>

        <!-- The Terminal Window -->
        <div class="console-terminal" id="consoleTerminal">
            <div class="console-line info">[System Notice] جاري التوصيل بسجل الخادم الحية...</div>
        </div>
        
        <!-- Details Log Modal Trigger -->
        <div style="display: flex; justify-content: space-between; margin-top: 1rem; font-size: 0.8rem; color: var(--text-secondary); font-weight: bold;">
            <span id="logUpdateStatus">آخر تحديث: جاري التحميل...</span>
            <button type="button" class="btn btn-secondary btn-sm" id="viewDetailedRawLogBtn" onclick="showRawLogModal()" style="font-size: 0.75rem;">
                <i class="fas fa-expand"></i> عرض السجل التفصيلي الخام
            </button>
        </div>
    </div>
</div>

<!-- Modal Raw logs detail -->
<div id="rawLogModal" class="modal">
    <div class="glass-panel" style="max-width: 900px; width: 100%; border-radius: 20px; padding: 2rem; position: relative;">
        <h3 style="font-size: 1.25rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.75rem; margin-bottom: 1.25rem; display: flex; justify-content: space-between; align-items: center; color: var(--text-primary);">
            <span><i class="fas fa-file-alt"></i> السجل البرمجي الخام (Raw Log Details)</span>
            <button type="button" class="btn btn-secondary btn-sm" onclick="copyRawLogToClipboard()"><i class="fas fa-copy"></i> نسخ السجل</button>
        </h3>
        <div id="modalRawLogContent" class="modal-body-content">جاري جلب السجل الكامل...</div>
        <div style="display: flex; justify-content: flex-end; margin-top: 1.5rem; border-top: 1px solid var(--panel-border); padding-top: 1rem;">
            <button type="button" class="btn" onclick="closeRawLogModal()" style="padding: 0.5rem 1.5rem; font-weight: bold;">إغلاق</button>
        </div>
    </div>
</div>

<!-- Diagnostics Error Detail Modal -->
<div id="diagErrorModal" class="modal">
    <div class="glass-panel" style="max-width: 600px; width: 100%; border-radius: 20px; padding: 2rem; position: relative;">
        <h3 id="diagModalTitle" style="font-size: 1.25rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.75rem; margin-bottom: 1.25rem; display: flex; align-items: center; gap: 0.5rem; color: var(--danger);">
            <i class="fas fa-exclamation-circle"></i> تفاصيل خطأ الخدمة السحابية
        </h3>
        <div id="diagModalContent" class="modal-body-content" style="color: var(--text-primary); font-family: 'Tajawal', sans-serif;"></div>
        <div style="display: flex; justify-content: flex-end; margin-top: 1.5rem; border-top: 1px solid var(--panel-border); padding-top: 1rem;">
            <button type="button" class="btn" onclick="closeDiagErrorModal()" style="padding: 0.5rem 1.5rem; font-weight: bold;">إغلاق</button>
        </div>
    </div>
</div>
@endsection

@section('scripts')
<script>
    let activeTab = 'pipeline';
    let updateInterval = null;
    let fullRawLogs = "";
    let systemLogsData = null; // Store validation data if checked
    let clearCheckpoints = {
        pipeline: 0,
        laravel: 0
    };

    // كل بطاقات الخدمات (تُعاد كلها لحالة "جاري الفحص" عند إعادة الفحص، ومنها Serper)
    const DIAG_SERVICES = ['google_sheets', 'cloudinary', 'photoroom', 'gemini', 'serper', 'proxy'];
    // آخر نتيجة محفوظة (من الخادم عند فتح الصفحة، ثم من آخر فحص ناجح)
    let lastDiagnostics = null;

    function setServiceCardsText(text) {
        DIAG_SERVICES.forEach(s => {
            const ind = document.getElementById(`ind-${s}`);
            const txt = document.getElementById(`text-${s}`);
            ind.className = 'status-indicator status-unknown';
            txt.innerText = text;
            txt.className = 'status-text text-secondary';
            const card = document.getElementById(`card-${s}`);
            const prevBtn = card && card.querySelector ? card.querySelector('.diag-err-btn') : null;
            if (prevBtn) prevBtn.remove();
        });
    }

    function readLastDiagnostics() {
        try {
            const holder = document.getElementById('lastDiagnosticsData');
            const result = holder && holder.dataset ? JSON.parse(holder.dataset.result || 'null') : null;
            return result && result.services ? result : null;
        } catch (e) {
            return null;
        }
    }

    // وقت آخر فحص بجانب الزر: الصفحة تعرض النتيجة المحفوظة ولا تفحص تلقائياً
    function showLastCheckInfo(result) {
        const info = document.getElementById('lastCheckInfo');
        if (!result || !result.checked_at) {
            info.textContent = 'لم يُجرَ أي فحص بعد: اضغط «فحص الاتصالات الآن» لفحص الخدمات.';
            return;
        }
        const at = new Date(result.checked_at);
        const age = Math.max(0, (Date.now() - at.getTime()) / 1000);
        info.textContent = `المعروض نتيجة آخر فحص: ${at.toLocaleString('ar-AE')} (قبل ${formatAge(age)})` +
            (result.all_ok === false ? ' — توجد خدمات حرجة متوقفة' : '');
    }

    function renderLastDiagnostics() {
        if (lastDiagnostics) {
            updateDiagnosticsUI(lastDiagnostics.services, lastDiagnostics.raw_logs);
        } else {
            setServiceCardsText('لم يُفحص بعد');
        }
        showLastCheckInfo(lastDiagnostics);
    }

    // فحص الاتصالات بزر صريح فقط: يستهلك استعلام Serper واحداً واستدعاء PhotoRoom واحداً
    async function runDiagnostics() {
        const btn = document.getElementById('runDiagnosticBtn');
        const icon = document.getElementById('syncIcon');
        btn.disabled = true;
        icon.className = 'fas fa-spinner fa-spin';

        // Reset states
        setServiceCardsText('جاري الفحص...');

        try {
            const response = await fetch('/api/system/run-diagnostics', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content
                }
            });
            const data = await response.json();
            btn.disabled = false;
            icon.className = 'fas fa-sync-alt';

            if (data.status === 'success') {
                systemLogsData = data;
                lastDiagnostics = data;
                updateDiagnosticsUI(data.services, data.raw_logs);
                showLastCheckInfo(data);
            } else {
                renderLastDiagnostics();
                alert('❌ فشل تشغيل فحص التشخيصات: ' + (data.error || 'خطأ غير معروف'));
            }
        } catch (e) {
            btn.disabled = false;
            icon.className = 'fas fa-sync-alt';
            renderLastDiagnostics();
            alert('❌ خطأ في الاتصال بالخادم أثناء إجراء الفحص.');
        }
    }

    function updateDiagnosticsUI(services, rawLogs) {
        Object.keys(services).forEach(key => {
            const service = services[key];
            const ind = document.getElementById(`ind-${key}`);
            const txt = document.getElementById(`text-${key}`);
            const card = document.getElementById(`card-${key}`);
            
            // If card is not in DOM (e.g. google_search card removed), skip
            if (!card) return;
            
            // Remove previous error buttons if any
            const prevBtn = card.querySelector('.diag-err-btn');
            if (prevBtn) prevBtn.remove();

            if (service.status === 'online') {
                ind.className = 'status-indicator status-online';
                txt.innerText = 'يعمل بنجاح';
                txt.className = 'status-text text-success';
            } else if (service.status === 'disabled') {
                ind.className = 'status-indicator status-unknown';
                txt.innerText = 'غير مفعّل';
                txt.className = 'status-text text-secondary';
            } else {
                if (service.is_critical) {
                    ind.className = 'status-indicator status-offline';
                    txt.innerText = 'فشل الاتصال / متوقف';
                    txt.className = 'status-text text-danger';

                    // Add "Show Error details" button inside card for critical failures
                    const errBtn = document.createElement('button');
                    errBtn.type = 'button';
                    errBtn.className = 'btn btn-secondary btn-sm diag-err-btn';
                    errBtn.style.marginTop = '0.75rem';
                    errBtn.style.fontSize = '0.7rem';
                    errBtn.style.padding = '0.2rem 0.5rem';
                    errBtn.innerHTML = '<i class="fas fa-info-circle"></i> تفاصيل الخطأ';
                    errBtn.onclick = (e) => {
                        e.stopPropagation();
                        showDiagErrorModal(service.name, service.details || rawLogs);
                    };
                    card.appendChild(errBtn);
                } else {
                    ind.className = 'status-indicator status-unknown';
                    txt.innerText = 'غير نشط / تجاوز الاتصال (اختياري)';
                    txt.className = 'status-text text-secondary';
                }
            }
        });
    }

    function showDiagErrorModal(name, rawLogs) {
        document.getElementById('diagModalTitle').innerHTML = `<i class="fas fa-exclamation-circle"></i> تفاصيل فحص: <strong>${name}</strong>`;
        
        // Extract section from raw logs related to this service
        let extractedLogs = "لم يتم التقاط أخطاء تفصيلية.";
        if (rawLogs) {
            const lines = rawLogs.split('\n');
            let startCapture = false;
            let capturedLines = [];
            
            for (let line of lines) {
                if (line.includes(name)) {
                    startCapture = true;
                } else if (line.includes('===') && startCapture && capturedLines.length > 5) {
                    break;
                }
                if (startCapture) {
                    capturedLines.push(line);
                }
            }
            if (capturedLines.length > 0) {
                extractedLogs = capturedLines.join('\n');
            } else {
                extractedLogs = rawLogs;
            }
        }
        
        document.getElementById('diagModalContent').innerText = extractedLogs;
        document.getElementById('diagErrorModal').style.display = 'flex';
    }

    // Close diagnostics detail modal
    function closeDiagErrorModal() {
        document.getElementById('diagErrorModal').style.display = 'none';
    }

    // Switch Logs tab
    function switchLogTab(tab) {
        activeTab = tab;
        document.getElementById('tab-pipeline').className = 'console-tab' + (tab === 'pipeline' ? ' active' : '');
        document.getElementById('tab-laravel').className = 'console-tab' + (tab === 'laravel' ? ' active' : '');
        
        document.getElementById('consoleTerminal').innerHTML = '';
        fetchLogs();
    }

    function clearConsoleScreen() {
        clearCheckpoints[activeTab] = fullRawLogs ? fullRawLogs.length : 0;
        document.getElementById('consoleTerminal').innerHTML = '';
    }

    // Fetch live logs text
    async function fetchLogs() {
        if (!document.getElementById('autoUpdateCheck').checked) {
            return;
        }

        const endpoint = activeTab === 'pipeline' ? '/api/view-pipeline-log' : '/api/view-laravel-log';
        try {
            const response = await fetch(endpoint);
            if (response.status === 200) {
                const logs = await response.text();
                fullRawLogs = logs;
                
                let currentCheckpoint = clearCheckpoints[activeTab] || 0;
                if (logs.length < currentCheckpoint) {
                    clearCheckpoints[activeTab] = 0;
                    currentCheckpoint = 0;
                }
                
                let displayableLogs = logs.substring(currentCheckpoint);
                displayLogsInConsole(displayableLogs);
                
                const now = new Date();
                document.getElementById('logUpdateStatus').innerText = `آخر تحديث: ${now.toLocaleTimeString('ar-SA')}`;
            } else {
                document.getElementById('logUpdateStatus').innerText = `فشل تحديث اللوغز (كود ${response.status})`;
            }
        } catch (e) {
            document.getElementById('logUpdateStatus').innerText = 'فشل الاتصال بخادم اللوغز الحية.';
        }
    }

    // Parse and display raw text in the neon console box
    function displayLogsInConsole(text) {
        const consoleTerminal = document.getElementById('consoleTerminal');
        const filterVal = document.getElementById('logSearchInput').value.toLowerCase().trim();
        
        const lines = text.split('\n');
        
        // Limit display to last 300 lines inside console to prevent browser slow-down
        const sliceLines = lines.slice(-300);
        
        let htmlContent = '';
        sliceLines.forEach(line => {
            if (filterVal && !line.toLowerCase().includes(filterVal)) {
                return;
            }
            if (!line.trim()) return;

            let lineClass = '';
            if (line.toLowerCase().includes('error') || line.includes('❌') || line.includes('fail') || line.includes('critical')) {
                lineClass = 'error';
            } else if (line.toLowerCase().includes('warn') || line.includes('⚠️') || line.includes('skip') || line.includes('تنبيه')) {
                lineClass = 'warning';
            } else if (line.toLowerCase().includes('success') || line.includes('✅') || line.includes('نجح') || line.includes('تم تحديث')) {
                lineClass = 'success';
            } else if (line.toLowerCase().includes('info') || line.includes('🔄') || line.includes('جاري')) {
                lineClass = 'info';
            }
            
            htmlContent += `<div class="console-line ${lineClass}">${escapeHtml(line)}</div>`;
        });

        consoleTerminal.innerHTML = htmlContent;

        // Auto-Scroll logic
        if (document.getElementById('autoScrollCheck').checked) {
            consoleTerminal.scrollTop = consoleTerminal.scrollHeight;
        }
    }

    function applyLogFilter() {
        if (fullRawLogs) {
            displayLogsInConsole(fullRawLogs);
        }
    }

    // Raw modal methods
    function showRawLogModal() {
        document.getElementById('modalRawLogContent').innerText = fullRawLogs || "السجل خالي أو لم يتم تحميله بعد.";
        document.getElementById('rawLogModal').style.display = 'flex';
    }

    function closeRawLogModal() {
        document.getElementById('rawLogModal').style.display = 'none';
    }

    function copyRawLogToClipboard() {
        const content = document.getElementById('modalRawLogContent').innerText;
        navigator.clipboard.writeText(content).then(() => {
            alert('📋 تم نسخ السجل البرمجي الكامل إلى الحافظة بنجاح!');
        }).catch(err => {
            alert('❌ فشل النسخ تلقائياً.');
        });
    }

    function escapeHtml(text) {
        return text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    // صحة البحث وتكلفته: كل رقم يأتي من /api/system/ops-health (ops_health.summarize على automation_queue.trace_json)
    let opsHealthData = null;
    let opsHealthWindow = '24h';
    const HEALTH_WINDOW_LABELS = { '24h': 'آخر 24 ساعة', '7d': 'آخر 7 أيام' };
    const DECISION_LABELS = {
        AUTO_PUBLISH: 'نشر تلقائي',
        REVIEW_PRESELECTED: 'مراجعة مع اختيار مسبق',
        REVIEW_UNSELECTED: 'مراجعة بدون اختيار',
        NOT_FOUND: 'لم يُعثر على صورة',
        PROVIDER_DOWN: 'محركات البحث متوقفة',
        VERIFIER_DOWN: 'التحقق متوقف'
    };
    const PROVIDER_STATUS_COLUMNS = ['ok', 'empty', 'error', 'quota', 'blocked'];

    async function loadOpsHealth(refresh) {
        const btn = document.getElementById('opsHealthRefreshBtn');
        btn.disabled = true;
        try {
            const response = await fetch('/api/system/ops-health' + (refresh ? '?refresh=1' : ''), {
                headers: { 'Accept': 'application/json' }
            });
            const data = await response.json();
            if (data.status !== 'success') {
                throw new Error(data.error || 'خطأ غير معروف');
            }
            opsHealthData = data;
            renderOpsHealth();
        } catch (e) {
            opsHealthData = null;
            document.getElementById('opsHealthAlerts').innerHTML = '';
            document.getElementById('opsHealthNote').textContent = '';
            document.getElementById('opsHealthBody').innerHTML =
                `<div class="health-empty">تعذر تحميل ملخص الصحة والتكلفة: ${escapeHtml(String(e.message || e))}</div>`;
        } finally {
            btn.disabled = false;
        }
    }

    function switchHealthWindow(name) {
        opsHealthWindow = name;
        Object.keys(HEALTH_WINDOW_LABELS).forEach(w => {
            document.getElementById(`health-tab-${w}`).className = 'console-tab' + (w === name ? ' active' : '');
        });
        renderOpsHealth();
    }

    function formatAge(seconds) {
        if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} دقيقة`;
        if (seconds < 86400) return `${Math.round(seconds / 3600)} ساعة`;
        return `${Math.round(seconds / 86400)} يوم`;
    }

    function formatUsd(value) {
        return '$' + Number(value || 0).toFixed(3);
    }

    function healthTile(label, value, sub) {
        return `<div class="health-tile"><div class="health-tile-label">${escapeHtml(label)}</div>` +
            `<div class="health-tile-value">${escapeHtml(String(value))}</div>` +
            (sub ? `<div class="health-tile-sub">${escapeHtml(sub)}</div>` : '') + `</div>`;
    }

    function healthTable(caption, headers, rows, emptyText) {
        const head = headers.map(h => `<th>${escapeHtml(h)}</th>`).join('');
        const body = rows.length
            ? rows.map(cells => '<tr>' + cells.map((c, i) =>
                `<td class="${i > 0 ? 'num' : ''}">${escapeHtml(String(c))}</td>`).join('') + '</tr>').join('')
            : `<tr><td colspan="${headers.length}" style="color: var(--text-secondary);">${escapeHtml(emptyText)}</td></tr>`;
        return `<table class="health-table"><caption>${escapeHtml(caption)}</caption><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
    }

    function renderOpsHealth() {
        const data = opsHealthData;
        if (!data) return;

        // تنبيهات الانقطاع (رصيد Serper / Gemini) بالأحمر فوق اللوحة
        document.getElementById('opsHealthAlerts').innerHTML = (data.alerts || []).map(a =>
            `<div class="health-alert"><i class="fas fa-exclamation-triangle" style="margin-top: 0.2rem;"></i>` +
            `<div>${escapeHtml(a.message || '')}<div class="health-alert-detail">${escapeHtml(a.detail || '')}</div></div></div>`
        ).join('');

        const prices = data.prices || {};
        const notes = [
            `تم فحص ${Number(data.scanned || 0)} صف من الطابور (الحد ${Number(data.limit || 0)}).` +
                (data.truncated ? ' وصل الفحص إلى الحد، فالأرقام الأقدم ناقصة.' : ''),
            'الطابور يحفظ آخر بحث لكل صف فقط: إعادة المحاولة والبحث اليدوي من الكتالوج غير محسوبة.',
            `التكلفة تقديرية: Serper ${prices.serper_per_query}$ لكل استعلام أجاب عنه، Gemini ${prices.gemini_per_call}$ لكل استدعاء.`
        ];
        if (data.timed_by_row_update) {
            notes.push('وقت البحث مأخوذ من آخر تحديث لصف الطابور؛ إعادة إضافة الصفوف للطابور قد تُدخل عمليات بحث أقدم في النافذة.');
        }
        if (data.latest_age_s !== null && data.latest_age_s !== undefined) {
            notes.unshift(`آخر بحث قبل ${formatAge(Number(data.latest_age_s))}.`);
        }
        document.getElementById('opsHealthNote').textContent = notes.join(' ');

        const body = document.getElementById('opsHealthBody');
        if (!data.scanned) {
            body.innerHTML = '<div class="health-empty">لا توجد عمليات بحث مسجلة في الطابور خلال آخر 7 أيام. ' +
                'شغّل الأتمتة من صفحة الدفعات أو انتظر التشغيل الليلي.</div>';
            return;
        }
        const w = (data.windows || {})[opsHealthWindow] || {};
        const codes = w.failure_codes || [];
        if (!w.searches && !w.unreadable && !codes.length) {
            body.innerHTML = `<div class="health-empty">لا توجد عمليات بحث مسجلة في ${escapeHtml(HEALTH_WINDOW_LABELS[opsHealthWindow])}.</div>`;
            return;
        }

        const cost = w.cost_usd || {};
        const verifier = w.verifier || {};
        let html = '<div class="health-tiles">' +
            healthTile('عمليات البحث', Number(w.searches || 0),
                w.unreadable ? `صفوف بلا نتيجة بحث مقروءة: ${Number(w.unreadable)}` : '') +
            healthTile('التكلفة التقديرية', formatUsd(cost.total),
                `Serper ${formatUsd(cost.serper)} · Gemini ${formatUsd(cost.gemini)}`) +
            healthTile('استعلامات Serper المحتسبة', Number(w.serper_queries || 0), 'الاستعلامات التي أجاب عنها Serper') +
            healthTile('استدعاءات Gemini', Number(verifier.calls || 0),
                `تعذر التحقق في ${Number(verifier.down || 0)} عملية بحث`) +
            '</div>';

        const decisions = Object.entries(w.decisions || {}).map(([code, n]) =>
            [`${DECISION_LABELS[code] || code} (${code})`, Number(n)]);
        const providers = Object.entries(w.providers || {});
        const extra = providers.some(([, counts]) => counts.other) ? ['other'] : [];
        const statusColumns = PROVIDER_STATUS_COLUMNS.concat(extra);
        const providerRows = providers.map(([name, counts]) =>
            [name].concat(statusColumns.map(s => Number(counts[s] || 0))));
        html += '<div class="health-tables">' +
            healthTable('القرارات', ['القرار', 'العدد'], decisions, 'لا توجد قرارات') +
            healthTable('ردود المزودين', ['المزود'].concat(statusColumns), providerRows, 'لا توجد استدعاءات مزودين') +
            healthTable('أكثر رموز الفشل', ['الرمز', 'العدد'], codes.map(c => [c.code, Number(c.count)]), 'لا توجد رموز فشل') +
            '</div>';
        body.innerHTML = html;
    }

    // Initialize poller on load
    window.addEventListener('load', () => {
        // آخر نتيجة محفوظة فقط: فحص الاتصالات لا يعمل عند فتح الصفحة (له زر صريح)
        lastDiagnostics = readLastDiagnostics();
        renderLastDiagnostics();
        
        // Search health and cost panel
        loadOpsHealth(false);

        // Load initial logs
        fetchLogs();
        
        // Start live update loop
        updateInterval = setInterval(fetchLogs, 3000);
    });
</script>
@endsection
