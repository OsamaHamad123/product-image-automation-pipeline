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
        background: #05080f;
        border: 1px solid var(--panel-border);
        border-radius: 16px;
        font-family: 'Courier New', Courier, monospace;
        color: #00ffc4;
        padding: 1.5rem;
        height: 320px;
        overflow-y: auto;
        font-size: 0.85rem;
        box-shadow: inset 0 0 20px rgba(0, 0, 0, 0.8), 0 4px 20px rgba(0, 255, 196, 0.03);
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
    
    .terminal-line.error { color: #f43f5e; }
    .terminal-line.warning { color: #f59e0b; }
    .terminal-line.success { color: #10b981; }
    .terminal-line.system { color: #8b5cf6; }

    /* Curation styles */
    .candidates-scroll-gallery {
        flex: 1;
        display: flex;
        gap: 1rem;
        overflow-x: auto;
        padding: 0.5rem;
        border-right: 1px solid var(--panel-border);
        border-left: 1px solid var(--panel-border);
        margin: 0 1rem;
        scroll-snap-type: x mandatory;
        scrollbar-width: thin;
        scrollbar-color: rgba(255, 255, 255, 0.1) transparent;
    }
    .candidates-scroll-gallery::-webkit-scrollbar {
        height: 6px;
    }
    .candidates-scroll-gallery::-webkit-scrollbar-thumb {
        background: rgba(255, 255, 255, 0.15);
        border-radius: 10px;
    }
    .candidates-scroll-gallery::-webkit-scrollbar-track {
        background: transparent;
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
        scroll-snap-align: start;
    }
    .curation-thumb-card:hover {
        border-color: var(--accent-cyan) !important;
        transform: scale(1.05);
    }
    .curation-thumb-card.active-candidate {
        border-color: var(--accent-purple) !important;
        box-shadow: 0 0 15px rgba(139, 92, 246, 0.5);
        transform: scale(1.05);
        background: rgba(139, 92, 246, 0.05);
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
            <button type="button" class="btn btn-secondary" onclick="resetBatchState()" style="background: rgba(244, 63, 94, 0.1); border-color: rgba(244, 63, 94, 0.2); color: var(--danger); font-weight: 800; padding: 0.65rem 1.5rem;">
                <i class="fas fa-undo"></i> تصفير وإعادة تعيين الحالة 🔄
            </button>
            <button type="button" class="btn" id="runAllBtn" onclick="runAllAutomation()" style="background: linear-gradient(135deg, var(--accent-purple) 0%, var(--accent-cyan) 100%); font-weight: 800; padding: 0.65rem 1.5rem;">
                <i class="fas fa-play"></i> إطلاق الأتمتة الجماعية بالخلفية
            </button>
        </div>
    </div>

    <!-- Main Workspace Control Panels -->
    <div style="display: grid; grid-template-columns: 1fr 340px; gap: 2rem; align-items: start;">
        
        <!-- Right side: Console Logs & Pre-flight settings -->
        <div style="display: flex; flex-direction: column; gap: 2rem;">
            
            <!-- Config panel -->
            <div class="glass-panel" style="padding: 1.75rem;">
                <h3 style="font-size: 1.15rem; font-weight: 800; margin-bottom: 1.25rem; display: flex; align-items: center; gap: 0.5rem; color: var(--text-primary);">
                    <i class="fas fa-sliders-h" style="color: var(--accent-purple);"></i> خيارات معالجة الصور دفعة واحدة
                </h3>
                
                <div class="config-grid">
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

            <!-- Terminal logs panel -->
            <div class="glass-panel" style="padding: 1.75rem;">
                <h3 style="font-size: 1.15rem; font-weight: 800; margin-bottom: 1.25rem; display: flex; align-items: center; justify-content: space-between; color: var(--text-primary);">
                    <span style="display: flex; align-items: center; gap: 0.5rem;"><i class="fas fa-terminal" style="color: var(--accent-cyan);"></i> كونسول التشغيل والمشاهدة الحية</span>
                    <button type="button" class="btn btn-secondary btn-sm" onclick="clearConsole()" style="font-size: 0.75rem; padding: 4px 10px; color: var(--danger); background: rgba(244,63,94,0.05);">تفريغ الشاشة</button>
                </h3>
                
                <div class="terminal-container" id="terminalConsole">
                    <div class="terminal-line system">[النظام] بانتظار إطلاق العمليات...</div>
                </div>
            </div>

        </div>

        <!-- Left side: Batch Progress Panel -->
        <div class="glass-panel" style="padding: 1.75rem; display: flex; flex-direction: column; gap: 1.25rem;">
            <h3 style="font-size: 1.15rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.75rem; margin-bottom: 0.25rem; display: flex; align-items: center; gap: 0.5rem;">
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
                    <button type="button" class="btn btn-secondary btn-sm" id="pauseResumeBatchBtn" onclick="togglePauseResumeAutomation()" style="flex: 1; background: rgba(245, 158, 11, 0.1); border-color: rgba(245, 158, 11, 0.2); color: var(--warning); font-weight: bold; border-radius: 10px;">
                        <i class="fas fa-pause" id="pauseResumeIcon"></i> <span id="pauseResumeText">إيقاف مؤقت</span>
                    </button>
                    <button type="button" class="btn btn-secondary btn-sm" id="stopBatchBtn" onclick="stopBatchAutomation()" style="flex: 1; background: rgba(244, 63, 94, 0.1); border-color: rgba(244, 63, 94, 0.2); color: var(--danger); font-weight: bold; border-radius: 10px;">
                        <i class="fas fa-stop"></i> إنهاء قسري 🛑
                    </button>
                </div>
            </div>

            <div id="batchIdleState" style="text-align: center; padding: 2rem 0; color: var(--text-secondary);">
                <i class="fas fa-check-circle" style="font-size: 2.5rem; color: var(--success); margin-bottom: 0.75rem; opacity: 0.7;"></i>
                <p style="font-size: 0.85rem; font-weight: bold; margin: 0;">لا توجد عمليات جارية حالياً.</p>
                <p style="font-size: 0.75rem; margin-top: 0.25rem;">قم بتهيئة الخيارات بالأعلى واضغط إطلاق لبدء أتمتة الشيت.</p>
            </div>
        </div>

    </div>

    <!-- Bottom Section: Curation Grid (appears when results are ready) -->
    <div class="glass-panel" id="batchCurationWorkspace" style="display: none; padding: 2rem;">
        <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--panel-border); padding-bottom: 1rem; margin-bottom: 1.5rem; direction: rtl;">
            <h3 style="font-size: 1.3rem; font-weight: 800; display: flex; align-items: center; gap: 0.65rem; color: var(--accent-purple-hover); margin: 0;">
                <i class="fas fa-layer-group"></i> فرز واعتماد الدفعة الجاهزة (Batch Curation Grid)
            </h3>
            <span class="score-badge" id="curationPendingCount">0 منتجات جاهزة</span>
        </div>

        <div style="background: var(--input-bg); border: 1px solid var(--panel-border); padding: 1.25rem; border-radius: 16px; margin-bottom: 1.75rem; display: flex; flex-direction: column; gap: 1rem; direction: rtl;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 1rem;">
                <div style="display: flex; gap: 1rem; flex-wrap: wrap;">
                    <button type="button" class="btn" id="batchApproveBtn" onclick="submitBatchApproval()" style="background: linear-gradient(135deg, var(--success) 0%, #34d399 100%); color: white; font-weight: 800; padding: 0.65rem 1.5rem;">
                        <i class="fas fa-check-double"></i> اعتماد ورفع الصور المحددة للشيت سحابياً 🚀
                    </button>
                    <button type="button" class="btn btn-secondary" id="batchRejectBtn" onclick="submitBatchRejection()" style="background: rgba(244, 63, 94, 0.1); border-color: rgba(244, 63, 94, 0.2); color: var(--danger); font-weight: bold; padding: 0.65rem 1.5rem;">
                        <i class="fas fa-trash-alt"></i> استبعاد وتجاهل المحدد 🗑️
                    </button>
                </div>
                
                <div style="display: flex; gap: 0.5rem; align-items: center;">
                    <button type="button" class="btn btn-secondary btn-sm" onclick="selectAllBatch(true)" style="padding: 0.4rem 0.8rem; font-size: 0.8rem;">تحديد الكل</button>
                    <button type="button" class="btn btn-secondary btn-sm" onclick="selectAllBatch(false)" style="padding: 0.4rem 0.8rem; font-size: 0.8rem;">إلغاء التحديد</button>
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
                    btn.style.color = 'var(--success)';
                    btn.style.background = 'rgba(16, 185, 129, 0.1)';
                    btn.style.borderColor = 'rgba(16, 185, 129, 0.2)';
                } else {
                    icon.className = 'fas fa-pause';
                    txt.innerText = 'إيقاف مؤقت';
                    btn.style.color = 'var(--warning)';
                    btn.style.background = 'rgba(245, 158, 11, 0.1)';
                    btn.style.borderColor = 'rgba(245, 158, 11, 0.2)';
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

    // Poll status
    async function pollBatchStatus() {
        try {
            const res = await fetch('/api/batch-status');
            const data = await res.json();
            
            const panel = document.getElementById('batchProgressPanel');
            const idle = document.getElementById('batchIdleState');
            const runBtn = document.getElementById('runAllBtn');
            
            if (data.is_running || (data.status === 'pre_caching' && data.pause_requested === 1)) {
                panel.style.display = 'flex';
                idle.style.display = 'none';
                
                const percent = data.total > 0 ? Math.round((data.current / data.total) * 100) : 0;
                document.getElementById('batchProgressPercent').innerText = percent + '%';
                document.getElementById('batchProgressBar').style.width = percent + '%';
                
                isPaused = (data.pause_requested === 1);
                const pBtn = document.getElementById('pauseResumeBatchBtn');
                const pIcon = document.getElementById('pauseResumeIcon');
                const pTxt = document.getElementById('pauseResumeText');
                
                if (isPaused) {
                    pIcon.className = 'fas fa-play';
                    pTxt.innerText = 'استئناف الأتمتة';
                    pBtn.style.color = 'var(--success)';
                    pBtn.style.background = 'rgba(16, 185, 129, 0.1)';
                    pBtn.style.borderColor = 'rgba(16, 185, 129, 0.2)';
                    document.getElementById('batchProgressText').innerHTML = `<strong style="color: var(--warning);"><i class="fas fa-pause-circle"></i> الأتمتة موقوفة مؤقتاً</strong>`;
                } else {
                    pIcon.className = 'fas fa-pause';
                    pTxt.innerText = 'إيقاف مؤقت';
                    pBtn.style.color = 'var(--warning)';
                    pBtn.style.background = 'rgba(245, 158, 11, 0.1)';
                    pBtn.style.borderColor = 'rgba(245, 158, 11, 0.2)';
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
                runBtn.disabled = false;
                runBtn.innerHTML = '<i class="fas fa-play"></i> تشغيل أتمتة الشيت بالكامل (Batch)';
                
                // Show curation workspace
                document.getElementById('batchCurationWorkspace').style.display = 'block';
                fetchCurationProducts();
            } else {
                panel.style.display = 'none';
                idle.style.display = 'block';
                runBtn.disabled = false;
                runBtn.innerHTML = '<i class="fas fa-play"></i> تشغيل أتمتة الشيت بالكامل (Batch)';
                document.getElementById('batchCurationWorkspace').style.display = 'none';
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
                renderBatchCurationGrid();
            }
        } catch (err) {
            console.error(err);
        }
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
        const pending = currentProducts.filter(p => p.needs_review);
        
        document.getElementById('curationPendingCount').innerText = `${pending.length} منتج بانتظار المراجعة`;
        
        if (pending.length === 0) {
            grid.innerHTML = '<p style="color: var(--text-secondary); text-align: center; padding: 3rem; font-weight: bold;">🎉 لا توجد منتجات معلقة للمراجعة والتدقيق حالياً.</p>';
            return;
        }

        pending.forEach(p => {
            const card = document.createElement('div');
            card.className = 'curation-row-card';
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
                        <span class="badge-row-number" style="align-self: flex-start; background: rgba(139, 92, 246, 0.1); border-color: rgba(139, 92, 246, 0.2); color: var(--accent-purple-hover); font-weight: 800; font-size: 0.75rem; padding: 2px 8px; border-radius: 6px;">صف ${p.row_number}</span>
                        <h4 style="font-size: 0.95rem; font-weight: 800; margin: 0.25rem 0 0; color: var(--text-primary); text-overflow: ellipsis; overflow: hidden; white-space: nowrap;" title="${p.product_name}">${p.product_name}</h4>
                        <p style="font-size: 0.8rem; color: var(--text-secondary); margin: 0; font-weight: 600;">البراند: <strong style="color: var(--text-primary);">${p.brand}</strong></p>
                        
                        ${(() => {
                            const allergens = checkTitleForAllergens(p.product_name);
                            return allergens ? `<span style="background: rgba(244,63,94,0.12); border: 1px solid rgba(244,63,94,0.22); color: var(--danger); font-size: 0.7rem; font-weight: 800; padding: 2px 6px; border-radius: 4px; margin-top: 0.35rem; display: inline-block; align-self: flex-start;"><i class="fas fa-exclamation-triangle"></i> يحتوي: ${allergens}</span>` : '';
                        })()}
                        
                        ${(p.curation_candidates && p.curation_candidates.length > 0) ? `<span style="background: rgba(0, 210, 255, 0.1); border: 1px solid rgba(0, 210, 255, 0.2); color: var(--accent-cyan); font-size: 0.7rem; font-weight: 800; padding: 2px 6px; border-radius: 4px; margin-top: 0.35rem; display: inline-block; align-self: flex-start;"><i class="fas fa-bolt"></i> جاهز للمراجعة (Cached)</span>` : ''}
                        
                        <div style="display: flex; align-items: center; gap: 0.35rem; margin-top: 0.5rem; width: 100%;">
                            <input type="text" id="inline-query-${p.row_number}" value="${p.brand ? p.product_name + ' ' + p.brand : p.product_name}" style="flex: 1; font-size: 0.75rem; padding: 4px 8px; background: rgba(0,0,0,0.25); border: 1px solid var(--panel-border); color: var(--text-primary); border-radius: 6px; outline: none; width: calc(100% - 35px);">
                            <button type="button" class="btn btn-secondary btn-sm" onclick="triggerInlineSearch(${p.row_number})" style="padding: 4px; font-size: 0.7rem; display: flex; align-items: center; justify-content: center; height: 26px; width: 26px;" title="إعادة البحث بالكلمات المكتوبة">
                                <i class="fas fa-sync-alt" id="inline-spinner-${p.row_number}"></i>
                            </button>
                        </div>
                    </div>
                </div>

                <div class="candidates-scroll-gallery">
                    ${candidatesList.map((c, cIdx) => {
                        const isSelected = c.is_selected === 1 ? 'checked' : '';
                        const activeClass = c.is_selected === 1 ? 'active-candidate' : '';
                        const scorePercent = c.clip_score ? Math.round(c.clip_score * 100) : 0;
                        const scoreBadge = scorePercent > 0 ? `<span style="position: absolute; bottom: 4px; left: 4px; background: rgba(12, 18, 28, 0.75); border: 1px solid rgba(255,255,255,0.15); color: #00ffc4; font-size: 0.65rem; font-family: 'Outfit', sans-serif; font-weight: 900; padding: 1px 4px; border-radius: 4px;">${scorePercent}% Match</span>` : '';
                        const domainText = c.source_domain ? c.source_domain.replace('www.', '') : 'Unknown';
                        const hasAllergen = checkTitleForAllergens(c.title || '');
                        const allergenIcon = hasAllergen ? `<span style="position: absolute; top: 4px; left: 4px; color: #f43f5e; font-size: 0.8rem; filter: drop-shadow(0 0 4px rgba(244,63,94,0.7)); z-index: 6;" title="تحذير مسببات حساسية: ${hasAllergen}"><i class="fas fa-exclamation-triangle"></i></span>` : '';
                        
                        return `
                            <div class="curation-thumb-card ${activeClass}" onclick="selectCurationThumb(this, ${p.row_number}, '${c.image_url.replace(/'/g, "\\'")}')" style="position: relative; flex: 0 0 110px; width: 110px; height: 110px; border-radius: 14px; border: 2px solid var(--panel-border); overflow: hidden; cursor: pointer; transition: all 0.25s ease;" title="${c.title || ''} (${domainText})">
                                <img src="${getImageUrl(c.image_url)}" alt="Candidate image" style="width: 100%; height: 100%; object-fit: cover;" onerror="this.src='https://placehold.co/110x110?text=Error'">
                                <input type="radio" name="batch-candidate-radio-${p.row_number}" value="${c.image_url}" ${isSelected} style="position: absolute; top: 6px; right: 6px; width: 18px; height: 18px; accent-color: var(--accent-purple); cursor: pointer; z-index: 5;" onclick="event.stopPropagation(); selectCurationThumb(this.parentElement, ${p.row_number}, '${c.image_url.replace(/'/g, "\\'")}')">
                                ${scoreBadge}
                                ${allergenIcon}
                            </div>
                        `;
                    }).join('')}
                </div>

                <div style="flex: 0 0 160px; display: flex; flex-direction: column; gap: 0.5rem; justify-content: center; align-items: stretch;">
                    <button type="button" class="btn btn-secondary btn-sm" onclick="toggleBatchRowExclude(${p.row_number})" id="btn-exclude-${p.row_number}" style="font-size: 0.75rem; font-weight: bold; padding: 0.45rem 0.5rem; background: rgba(239, 68, 68, 0.05); color: var(--danger); border-color: rgba(239, 68, 68, 0.15); border-radius: 10px; display: flex; align-items: center; justify-content: center; gap: 0.35rem;">
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
            btn.style.background = 'rgba(239, 68, 68, 0.05)';
            btn.style.color = 'var(--danger)';
            btn.style.borderColor = 'rgba(239, 68, 68, 0.15)';
        } else {
            card.style.opacity = '0.4';
            btn.innerHTML = '<i class="fas fa-undo"></i> <span class="btn-text">إلغاء الاستبعاد</span>';
            btn.style.background = 'rgba(16, 185, 129, 0.05)';
            btn.style.color = 'var(--success)';
            btn.style.borderColor = 'rgba(16, 185, 129, 0.15)';
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
        card.querySelectorAll('.curation-thumb-card').forEach(t => t.classList.remove('active-candidate'));
        thumbEl.classList.add('active-candidate');
        
        const radio = thumbEl.querySelector('input[type="radio"]');
        if (radio) radio.checked = true;
        
        const cb = card.querySelector('.batch-select-checkbox');
        if (cb) cb.dataset.url = imageUrl;
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
                        card.style.background = 'rgba(16, 185, 129, 0.05)';
                    }
                } else {
                    failed++;
                    const card = cb.closest('.curation-row-card');
                    if (card) {
                        card.style.borderColor = 'var(--danger)';
                        card.style.background = 'rgba(239, 68, 68, 0.05)';
                    }
                }
            } catch (err) {
                console.error(err);
                failed++;
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

    // On Load
    document.addEventListener("DOMContentLoaded", () => {
        pollBatchStatus();
        setInterval(pollBatchStatus, 3000);
        
        pollLogs();
        setInterval(pollLogs, 2500);
        
        fetchCurationProducts();
    });
</script>
@endsection
