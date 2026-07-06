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
@endsection

@section('scripts')
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

    // Poll batch status on load
    window.addEventListener('load', () => {
        pollBatchStatus();
        setInterval(pollBatchStatus, 3000);
    });
</script>
@endsection
