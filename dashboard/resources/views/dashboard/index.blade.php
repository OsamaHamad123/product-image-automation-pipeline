@extends('layouts.layout')

@section('title', '📊 لوحة التحكم والإحصائيات')
@section('nav_home', 'active')

@section('styles')
<style>
    .stats-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
        gap: 1.25rem;
        margin-bottom: 2rem;
    }

    .stat-card {
        background: var(--panel-bg);
        border: 1px solid var(--panel-border);
        border-radius: 8px;
        padding: 1.25rem 1.5rem;
        display: flex;
        align-items: center;
        gap: 1rem;
        transition: border-color 0.2s;
    }

    .stat-card:hover {
        border-color: var(--accent-purple);
    }

    .stat-icon {
        width: 48px;
        height: 48px;
        border-radius: 8px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1.25rem;
        position: relative;
        z-index: 2;
    }

    .stat-icon.total { background: rgba(99, 102, 241, 0.08); color: var(--accent-purple); }
    .stat-icon.success { background: var(--success-bg); color: var(--success); }
    .stat-icon.warning { background: var(--warning-bg); color: var(--warning); }
    .stat-icon.danger { background: var(--danger-bg); color: var(--danger); }
    .stat-icon.cost { background: rgba(249, 115, 22, 0.08); color: var(--accent-orange); }

    .stat-details {
        position: relative;
        z-index: 2;
    }

    .stat-details h3 {
        font-size: 0.8rem;
        color: var(--text-secondary);
        font-weight: 600;
        margin-bottom: 0.15rem;
    }

    .stat-details .value {
        font-size: 1.75rem;
        font-weight: 800;
        font-family: 'Outfit', sans-serif;
    }

    .progress-bar-container {
        background: var(--input-bg);
        border: 1px solid var(--panel-border);
        border-radius: 4px;
        height: 8px;
        width: 100%;
        overflow: hidden;
        margin-top: 0.5rem;
        position: relative;
    }

    .progress-bar-fill {
        background: var(--accent-purple);
        height: 100%;
        width: 0%;
        transition: width 0.6s cubic-bezier(0.16, 1, 0.3, 1);
        position: relative;
    }
</style>
@endsection

@section('content')
<div class="glass-panel" style="padding: 1.5rem 2rem; margin-bottom: 2rem;">
    <h1 style="font-size: 1.8rem; font-weight: 900; display: flex; align-items: center; gap: 0.75rem;">
        <i class="fas fa-chart-pie" style="color: var(--accent-purple);"></i>
        الرئيسية والإحصائيات الحالية
    </h1>
    <p style="color: var(--text-secondary); margin-top: 0.25rem; font-size: 0.9rem;">
        ملخص تشغيل البوت وأتمتة المطابقة البصرية وعزل الخلفيات لورقة Google Sheets
    </p>
</div>

@if(isset($error))
    <div class="glass-panel" style="background-color: var(--danger-bg); border-color: var(--danger); color: var(--danger); font-weight: bold; margin-bottom: 2rem;">
        <i class="fas fa-exclamation-triangle"></i> {{ $error }}
    </div>
@endif

<!-- Statistics Grid -->
<div class="stats-grid">
    <div class="stat-card">
        <div class="stat-icon total"><i class="fas fa-file-spreadsheet"></i></div>
        <div class="stat-details">
            <h3>إجمالي منتجات الشيت</h3>
            <div class="value">{{ $total }}</div>
        </div>
    </div>
    
    <div class="stat-card">
        <div class="stat-icon success"><i class="fas fa-check-circle"></i></div>
        <div class="stat-details">
            <h3>منتجات مكتملة (روابط)</h3>
            <div class="value" style="color: var(--success);">{{ $linked }}</div>
        </div>
    </div>
    
    <div class="stat-card">
        <div class="stat-icon warning"><i class="fas fa-exclamation-triangle"></i></div>
        <div class="stat-details">
            <h3>منتجات معلقة للمراجعة</h3>
            <div class="value" style="color: var(--warning);">{{ $review }}</div>
        </div>
    </div>

    <div class="stat-card">
        <div class="stat-icon danger"><i class="fas fa-times-circle"></i></div>
        <div class="stat-details">
            <h3>أخطاء تقنية بـ SQLite</h3>
            <div class="value" style="color: var(--danger);">{{ $errors }}</div>
        </div>
    </div>
</div>

<!-- API Metrics & Costs -->
<div class="stats-grid" style="grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));">
    <div class="glass-panel" style="margin-bottom: 0;">
        <h3 style="font-size: 1.1rem; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.75rem; margin-bottom: 1.25rem; display: flex; align-items: center; gap: 0.5rem;">
            <i class="fas fa-key" style="color: var(--accent-cyan);"></i> استهلاك واجهة البرمجة (API Usage)
        </h3>
        <div style="display: flex; flex-direction: column; gap: 1rem;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="color: var(--text-secondary);">استدعاءات فحص صور Gemini Vision:</span>
                <strong style="font-family: 'Outfit', sans-serif; font-size: 1.15rem; color: var(--accent-cyan);">{{ $metrics['gemini_api_calls'] ?? 0 }}</strong>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="color: var(--text-secondary);">مرات رفع الصور لـ Cloudinary:</span>
                <strong style="font-family: 'Outfit', sans-serif; font-size: 1.15rem; color: var(--success);">{{ $metrics['cloudinary_uploads'] ?? 0 }}</strong>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="color: var(--text-secondary);">عمليات بحث وفرها الكاش الدلالي:</span>
                <strong style="font-family: 'Outfit', sans-serif; font-size: 1.15rem; color: #00ffcc;"><i class="fas fa-bolt" style="margin-inline-end: 0.25rem;"></i>{{ $metrics['semantic_cache_savings'] ?? 0 }}</strong>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center; border-top: 1px solid var(--panel-border); padding-top: 1rem;">
                <span style="color: var(--text-secondary); font-weight: bold;">تكلفة الاستهلاك المقدرة:</span>
                <strong style="font-family: 'Outfit', sans-serif; font-size: 1.3rem; color: #ff9100;">${{ $estimatedCost }} USD</strong>
            </div>
        </div>
    </div>
    
    <div class="glass-panel" style="margin-bottom: 0; display: flex; flex-direction: column; justify-content: space-between;">
        <div>
            <h3 style="font-size: 1.1rem; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.75rem; margin-bottom: 1.25rem; display: flex; align-items: center; gap: 0.5rem;">
                <i class="fas fa-play" style="color: var(--accent-purple);"></i> التحكم في الأتمتة بالخلفية
            </h3>
            <p style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 1.5rem;">
                يمكنك إطلاق معالجة وبحث الأتمتة لجميع المنتجات المتبقية في جدول Google Sheets دفعة واحدة بالخلفية.
            </p>
        </div>
        
        <div>
            <!-- Batch Automation progress panel -->
            <div id="batchProgressPanel" style="display: none; flex-direction: column; gap: 0.5rem; margin-bottom: 1.5rem; background: rgba(0,0,0,0.2); padding: 1rem; border-radius: 12px; border: 1px solid var(--panel-border);">
                <div style="display: flex; justify-content: space-between; font-size: 0.8rem; font-weight: bold;">
                    <span id="batchProgressText">جاري المعالجة...</span>
                    <span id="batchProgressPercent" style="color: var(--accent-cyan);">0%</span>
                </div>
                <div class="progress-bar-container">
                    <div id="batchProgressBar" class="progress-bar-fill"></div>
                </div>
                <div id="batchProgressCounts" style="font-size: 0.75rem; color: var(--text-secondary); text-align: left; direction: ltr;">
                    0 of 0 (Success: 0 | Failed: 0)
                </div>
            </div>
            
            <button class="btn" id="runAllBtn" onclick="runAllAutomation()" style="width: 100%;">
                <i class="fas fa-play"></i> تشغيل أتمتة الشيت بالكامل (Batch)
            </button>
        </div>
    </div>
</div>

<!-- Live Telemetry Graph & Analytics -->
<div class="glass-panel" style="margin-top: 2rem;">
    <h3 style="font-size: 1.1rem; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.75rem; margin-bottom: 1.25rem; display: flex; align-items: center; gap: 0.5rem;">
        <i class="fas fa-chart-line" style="color: var(--accent-cyan);"></i> مراقبة مؤشرات الأداء الفورية (Live Telemetry & Cost Analysis)
    </h3>
    <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 1.5rem; min-height: 260px;">
        <div style="background: rgba(0,0,0,0.1); border-radius: 8px; padding: 1rem; border: 1px solid var(--panel-border); position: relative; height: 260px;">
            <canvas id="telemetryChart"></canvas>
        </div>
        <div style="background: rgba(0,0,0,0.15); border-radius: 8px; padding: 1rem; border: 1px solid var(--panel-border); max-height: 260px; overflow-y: auto; display: flex; flex-direction: column;">
            <h4 style="font-size: 0.85rem; margin-bottom: 0.5rem; color: var(--text-primary); font-weight: bold; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.25rem; display: flex; justify-content: space-between; align-items: center;">
                <span><i class="fas fa-terminal" style="color: var(--accent-purple); margin-inline-end: 0.25rem;"></i> تدفق الأحداث المباشر (SSE)</span>
                <span id="sse-connection-status" class="score-badge" style="font-size: 0.65rem; padding: 2px 6px; background: rgba(239, 68, 68, 0.1); color: var(--danger); border-color: rgba(239, 68, 68, 0.2);">مغلق</span>
            </h4>
            <div id="sse-log-container" style="font-family: 'Courier New', Courier, monospace; font-size: 0.75rem; color: var(--text-secondary); line-height: 1.4; display: flex; flex-direction: column; gap: 0.35rem; text-align: left; direction: ltr; overflow-y: auto; flex: 1;">
                <span style="color: var(--text-secondary); font-style: italic;">بانتظار تدفق الأحداث الحية...</span>
            </div>
        </div>
    </div>
</div>

<!-- System Services Monitor & Auto-Start Controller -->
<div class="glass-panel" style="margin-top: 2rem;">
    <h3 style="font-size: 1.1rem; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.75rem; margin-bottom: 1.25rem; display: flex; align-items: center; gap: 0.5rem;">
        <i class="fas fa-server" style="color: var(--accent-purple);"></i> لوحة التحكم التلقائي وإدارة الخدمات (Auto-Start System Services)
    </h3>
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 1.5rem;">
        <div>
            <h4 style="font-size: 0.95rem; margin-bottom: 0.75rem; color: var(--text-primary); font-weight: bold;">حالة خوادم النظام الحية</h4>
            <div style="display: flex; flex-direction: column; gap: 0.8rem;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="color: var(--text-secondary);">خادم لوحة التحكم (Laravel):</span>
                    <span id="status-laravel" class="score-badge" style="background: rgba(16, 185, 129, 0.1); color: var(--success); border-color: rgba(16, 185, 129, 0.2);">جاري التشغيل (Port 8000)</span>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="color: var(--text-secondary);">خادم الأتمتة الميكروي (Flask):</span>
                    <span id="status-flask" class="score-badge" style="background: rgba(239, 68, 68, 0.1); color: var(--danger); border-color: rgba(239, 68, 68, 0.2); font-weight: bold;">جاري الفحص...</span>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="color: var(--text-secondary);">قاعدة البيانات المحلية (SQLite Cache):</span>
                    <span id="status-db" class="score-badge" style="background: var(--input-bg); color: var(--text-secondary); font-weight: bold;">جاري الفحص...</span>
                </div>
            </div>
        </div>
        
        <div style="display: flex; flex-direction: column; justify-content: center; gap: 0.75rem;">
            <h4 style="font-size: 0.95rem; margin-bottom: 0.25rem; color: var(--text-primary); font-weight: bold;">إجراءات التحكم السريعة للتشغيل التلقائي</h4>
            <div style="display: flex; gap: 1rem; width: 100%;">
                <button class="btn" id="startFlaskBtn" onclick="controlSystem('start-flask')" style="flex: 1; background: var(--success); border-color: var(--success);">
                    <i class="fas fa-play"></i> تشغيل خادم بايثون 🚀
                </button>
                <button class="btn btn-secondary" id="stopFlaskBtn" onclick="controlSystem('stop-flask')" style="flex: 1;">
                    <i class="fas fa-stop"></i> إيقاف خادم بايثون 🛑
                </button>
            </div>
            <p style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 0.25rem;">
                * يتيح لك هذا القسم تشغيل وإعادة تهيئة خادم الخلفية (Flask Microservice) برمجياً عند توقفه دون الحاجة لفتح سطر الأوامر يدوياً.
            </p>
        </div>
    </div>
</div>
@endsection

@section('scripts')
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
    // تحديث حالة الأتمتة بالخلفية
    async function pollBatchStatus() {
        try {
            const res = await fetch('/api/batch-status');
            const data = await res.json();
            
            const panel = document.getElementById('batchProgressPanel');
            const runBtn = document.getElementById('runAllBtn');
            
            if (data.is_running) {
                panel.style.display = 'flex';
                const percent = data.total > 0 ? Math.round((data.current / data.total) * 100) : 0;
                document.getElementById('batchProgressPercent').innerText = percent + '%';
                document.getElementById('batchProgressBar').style.width = percent + '%';
                document.getElementById('batchProgressText').innerHTML = `جاري معالجة: <strong style="color: var(--accent-cyan);">${data.current_product || 'جاري البحث...'}</strong>`;
                document.getElementById('batchProgressCounts').innerText = `${data.current} from ${data.total} (Success: ${data.success} | Failed: ${data.failed})`;
                
                runBtn.disabled = true;
                runBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> جاري الأتمتة بالخلفية...';
            } else {
                panel.style.display = 'none';
                if (runBtn.disabled) {
                    runBtn.disabled = false;
                    runBtn.innerHTML = '<i class="fas fa-play"></i> تشغيل أتمتة الشيت بالكامل (Batch)';
                    location.reload(); // Refresh to update statistics
                }
            }
        } catch (err) {
            console.error("Error polling batch status:", err);
        }
    }

    // إطلاق الأتمتة بالخلفية
    async function runAllAutomation() {
        if (!confirm("هل أنت متأكد من رغبتك في تشغيل الأتمتة الكاملة لكافة منتجات الشيت بالخلفية؟")) {
            return;
        }
        
        const btn = document.getElementById('runAllBtn');
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> جاري التشغيل...';
        
        try {
            const res = await fetch('/api/run-all', { 
                method: 'POST',
                headers: { 'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content }
            });
            const data = await res.json();
            if (data.status === 'success') {
                alert("🎉 تم إطلاق الأتمتة بالخلفية بنجاح! يمكنك متابعة التقدم هنا بالصفحة.");
                setInterval(pollBatchStatus, 2000);
            } else {
                alert("❌ فشل تشغيل الأتمتة: " + data.error);
                btn.disabled = false;
                btn.innerHTML = '<i class="fas fa-play"></i> تشغيل أتمتة الشيت بالكامل (Batch)';
            }
        } catch (err) {
            console.error(err);
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-play"></i> تشغيل أتمتة الشيت بالكامل (Batch)';
        }
    }

    // جلب حالة الخدمات والخوادم
    async function pollSystemStatus() {
        try {
            const res = await fetch('/api/system/status');
            const data = await res.json();
            
            const flaskEl = document.getElementById('status-flask');
            const dbEl = document.getElementById('status-db');
            const startBtn = document.getElementById('startFlaskBtn');
            const stopBtn = document.getElementById('stopFlaskBtn');
            
            if (data.flask_server === 'online') {
                flaskEl.innerText = 'متصل فعال (Port 5000) 🟢';
                flaskEl.style.background = 'rgba(16, 185, 129, 0.1)';
                flaskEl.style.color = 'var(--success)';
                flaskEl.style.borderColor = 'rgba(16, 185, 129, 0.2)';
                
                startBtn.disabled = true;
                startBtn.style.opacity = '0.5';
                startBtn.style.cursor = 'not-allowed';
                stopBtn.disabled = false;
                stopBtn.style.opacity = '1';
                stopBtn.style.cursor = 'pointer';
            } else {
                flaskEl.innerText = 'متوقف مغلق (Port 5000) 🔴';
                flaskEl.style.background = 'rgba(239, 68, 68, 0.1)';
                flaskEl.style.color = 'var(--danger)';
                flaskEl.style.borderColor = 'rgba(239, 68, 68, 0.2)';
                
                startBtn.disabled = false;
                startBtn.style.opacity = '1';
                startBtn.style.cursor = 'pointer';
                stopBtn.disabled = true;
                stopBtn.style.opacity = '0.5';
                stopBtn.style.cursor = 'not-allowed';
            }
            
            if (data.local_cache_db === 'active') {
                dbEl.innerText = 'نشطة ومحملة 💾';
                dbEl.style.color = 'var(--accent-cyan)';
            } else {
                dbEl.innerText = 'فارغة / ممسوحة 🧹';
                dbEl.style.color = 'var(--text-secondary)';
            }
        } catch (err) {
            console.error("Error polling system status:", err);
        }
    }

    // إطلاق إجراءات تشغيل/إيقاف الخدمات
    async function controlSystem(action) {
        const startBtn = document.getElementById('startFlaskBtn');
        const stopBtn = document.getElementById('stopFlaskBtn');
        
        startBtn.disabled = true;
        stopBtn.disabled = true;
        
        try {
            const res = await fetch(`/api/system/${action}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content
                }
            });
            const data = await res.json();
            if (data.status === 'success') {
                alert(`✅ الإجراء تم بنجاح: ${data.message}`);
            } else {
                alert(`❌ فشل تنفيذ الإجراء: ${data.error}`);
            }
            setTimeout(pollSystemStatus, 2000);
        } catch (err) {
            console.error(err);
            alert(`❌ فشل الاتصال بالخادم.`);
        } finally {
            startBtn.disabled = false;
            stopBtn.disabled = false;
        }
    }

    // تهيئة رسم التليمتري البياني لسرعة الطوابير والتوكنز
    let queueTelemetryChart = null;

    function initTelemetryChart() {
        const ctx = document.getElementById('telemetryChart').getContext('2d');
        queueTelemetryChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: [],
                datasets: [{
                    label: 'تأخير طوابير المعالجة (ثواني)',
                    data: [],
                    borderColor: 'rgb(255, 99, 132)',
                    backgroundColor: 'rgba(255, 99, 132, 0.05)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.3
                }, {
                    label: 'استهلاك الرموز (Gemini Tokens)',
                    data: [],
                    borderColor: 'rgb(54, 162, 235)',
                    backgroundColor: 'rgba(54, 162, 235, 0.05)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.3
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: {
                        grid: { color: 'rgba(255,255,255,0.05)' },
                        ticks: { color: '#8e9bb0', font: { size: 9 } }
                    },
                    y: {
                        grid: { color: 'rgba(255,255,255,0.05)' },
                        ticks: { color: '#8e9bb0', font: { size: 9 } }
                    }
                },
                plugins: {
                    legend: {
                        labels: { color: '#f3f4f6', font: { size: 10 } }
                    }
                }
            }
        });
    }

    // فتح اتصال البث الحي SSE مع خادم بايثون
    function startSSEConnection() {
        const statusEl = document.getElementById('sse-connection-status');
        const logContainer = document.getElementById('sse-log-container');
        
        try {
            const eventSource = new EventSource('http://127.0.0.1:5000/api/v1/telemetry/stream/enterprise_tenant_102');
            
            eventSource.onopen = () => {
                statusEl.innerText = 'متصل فعال 🟢';
                statusEl.style.background = 'rgba(16, 185, 129, 0.1)';
                statusEl.style.color = 'var(--success)';
                statusEl.style.borderColor = 'rgba(16, 185, 129, 0.2)';
            };
            
            eventSource.onmessage = (event) => {
                const payload = JSON.parse(event.data);
                
                // إضافة السجل في شاشة الكونسول الجانبية
                if (payload.log) {
                    if (logContainer.innerHTML.includes('بانتظار تدفق')) {
                        logContainer.innerHTML = '';
                    }
                    const newLog = document.createElement('span');
                    newLog.style.display = 'block';
                    newLog.style.borderBottom = '1px solid rgba(255,255,255,0.02)';
                    newLog.style.paddingBottom = '2px';
                    newLog.innerText = payload.log;
                    logContainer.appendChild(newLog);
                    logContainer.scrollTop = logContainer.scrollHeight;
                }
                
                // تحديث شريط التقدم التلقائي
                if (payload.pipeline_metrics && payload.pipeline_metrics.progress_percentage !== undefined) {
                    const percent = payload.pipeline_metrics.progress_percentage;
                    const fillBar = document.getElementById('batchProgressBar');
                    const percentText = document.getElementById('batchProgressPercent');
                    
                    if (fillBar) fillBar.style.width = percent + '%';
                    if (percentText) percentText.innerText = percent + '%';
                }
                
                // تحديث الرسم البياني بالقيم الجديدة
                if (queueTelemetryChart && payload.telemetry) {
                    const timeLabel = new Date(payload.timestamp * 1000).toLocaleTimeString();
                    
                    queueTelemetryChart.data.labels.push(timeLabel);
                    queueTelemetryChart.data.datasets[0].data.push(payload.telemetry.queue_delay_seconds);
                    queueTelemetryChart.data.datasets[1].data.push(payload.telemetry.gemini_api_tokens);
                    
                    // إبقاء آخر 12 عينة فقط لمنع تكدس الذاكرة للمتصفح
                    if (queueTelemetryChart.data.labels.length > 12) {
                        queueTelemetryChart.data.labels.shift();
                        queueTelemetryChart.data.datasets[0].data.shift();
                        queueTelemetryChart.data.datasets[1].data.shift();
                    }
                    
                    queueTelemetryChart.update();
                }
            };
            
            eventSource.onerror = () => {
                statusEl.innerText = 'غير متصل 🔴';
                statusEl.style.background = 'rgba(239, 68, 68, 0.1)';
                statusEl.style.color = 'var(--danger)';
                statusEl.style.borderColor = 'rgba(239, 68, 68, 0.2)';
            };
        } catch (err) {
            console.error("SSE Connection failed:", err);
        }
    }

    // Poll batch status and system status on load
    window.addEventListener('load', () => {
        pollBatchStatus();
        setInterval(pollBatchStatus, 3000);
        
        pollSystemStatus();
        setInterval(pollSystemStatus, 5000);

        // تهيئة التليمتري والبث المباشر
        initTelemetryChart();
        startSSEConnection();
    });
</script>
@endsection
