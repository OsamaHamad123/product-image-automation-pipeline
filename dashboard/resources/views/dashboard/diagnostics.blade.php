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
        <button type="button" class="btn" id="runDiagnosticBtn" onclick="runDiagnostics()" style="background: var(--accent-gradient); color: var(--btn-text); font-weight: 800;">
            <i class="fas fa-sync-alt" id="syncIcon"></i> فحص حالة الاتصالات والاشتراكات 🔄
        </button>
    </div>

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

        <!-- Card 5: Google Custom Search -->
        <div class="service-card" id="card-google_search">
            <div class="service-header">
                <div>
                    <h4 class="service-title">Google Custom Search</h4>
                    <span class="service-badge badge-optional">اختياري (Optional)</span>
                </div>
                <i class="fas fa-search" style="font-size: 1.5rem; color: #f59e0b;"></i>
            </div>
            <div class="service-status">
                <span class="status-indicator status-unknown" id="ind-google_search"></span>
                <span class="status-text text-secondary" id="text-google_search">بانتظار الفحص</span>
            </div>
        </div>
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

    // Run Diagnostics check synchronously
    async function runDiagnostics() {
        const btn = document.getElementById('runDiagnosticBtn');
        const icon = document.getElementById('syncIcon');
        btn.disabled = true;
        icon.className = 'fas fa-spinner fa-spin';
        
        // Reset states
        const services = ['google_sheets', 'cloudinary', 'photoroom', 'gemini', 'google_search'];
        services.forEach(s => {
            const ind = document.getElementById(`ind-${s}`);
            const txt = document.getElementById(`text-${s}`);
            ind.className = 'status-indicator status-unknown';
            txt.innerText = 'جاري الفحص...';
            txt.className = 'status-text text-secondary';
        });

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
                updateDiagnosticsUI(data.services, data.raw_logs);
            } else {
                alert('❌ فشل تشغيل فحص التشخيصات: ' + (data.error || 'خطأ غير معروف'));
            }
        } catch (e) {
            btn.disabled = false;
            icon.className = 'fas fa-sync-alt';
            alert('❌ خطأ في الاتصال بالخادم أثناء إجراء الفحص.');
        }
    }

    function updateDiagnosticsUI(services, rawLogs) {
        Object.keys(services).forEach(key => {
            const service = services[key];
            const ind = document.getElementById(`ind-${key}`);
            const txt = document.getElementById(`text-${key}`);
            const card = document.getElementById(`card-${key}`);
            
            // Remove previous error buttons if any
            const prevBtn = card.querySelector('.diag-err-btn');
            if (prevBtn) prevBtn.remove();

            if (service.status === 'online') {
                ind.className = 'status-indicator status-online';
                txt.innerText = 'يعمل بنجاح';
                txt.className = 'status-text text-success';
            } else {
                ind.className = 'status-indicator status-offline';
                txt.innerText = 'فشل الاتصال / متوقف';
                txt.className = 'status-text text-danger';

                // Add "Show Error details" button inside card
                const errBtn = document.createElement('button');
                errBtn.type = 'button';
                errBtn.className = 'btn btn-secondary btn-sm diag-err-btn';
                errBtn.style.marginTop = '0.75rem';
                errBtn.style.fontSize = '0.7rem';
                errBtn.style.padding = '0.2rem 0.5rem';
                errBtn.innerHTML = '<i class="fas fa-info-circle"></i> تفاصيل الخطأ';
                errBtn.onclick = (e) => {
                    e.stopPropagation();
                    showDiagErrorModal(service.name, rawLogs);
                };
                card.appendChild(errBtn);
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
        
        clearConsoleScreen();
        fetchLogs();
    }

    function clearConsoleScreen() {
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
                displayLogsInConsole(logs);
                
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

    // Initialize poller on load
    window.addEventListener('load', () => {
        // Run initial diagnostics checking on load to feed stats
        runDiagnostics();
        
        // Load initial logs
        fetchLogs();
        
        // Start live update loop
        updateInterval = setInterval(fetchLogs, 3000);
    });
</script>
@endsection
