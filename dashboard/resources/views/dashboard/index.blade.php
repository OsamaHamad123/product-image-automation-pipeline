@extends('layouts.layout')

@section('title', '📊 لوحة التحكم والإحصائيات')
@section('nav_home', 'active')

@section('styles')
<style>
    .stats-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
        gap: 1.5rem;
        margin-bottom: 2.5rem;
    }

    .stat-card {
        background: var(--panel-bg);
        border: 1px solid var(--panel-border);
        border-radius: var(--border-radius-md);
        padding: 1.75rem;
        display: flex;
        align-items: center;
        gap: 1.5rem;
        box-shadow: var(--shadow-sm);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        position: relative;
        overflow: hidden;
    }

    .stat-card::before {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: var(--accent-gradient);
        opacity: 0;
        transition: opacity 0.4s ease;
        z-index: 1;
    }

    .stat-card:hover {
        transform: translateY(-5px);
    }

    .stat-card:hover::before {
        opacity: 0.02;
    }

    .stat-icon {
        width: 60px;
        height: 60px;
        border-radius: var(--border-radius-sm);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1.6rem;
        transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
        box-shadow: var(--shadow-sm);
        z-index: 2;
        border: 1px solid rgba(255, 255, 255, 0.03);
    }

    .stat-card:hover .stat-icon {
        transform: scale(1.1) rotate(5deg);
        filter: brightness(1.2);
    }

    .stat-icon.total { background: rgba(139, 92, 246, 0.15); color: var(--accent-purple); border-color: rgba(139, 92, 246, 0.25); }
    .stat-icon.success { background: var(--success-bg); color: var(--success); border-color: rgba(16, 185, 129, 0.25); }
    .stat-icon.warning { background: var(--warning-bg); color: var(--warning); border-color: rgba(245, 158, 11, 0.25); }
    .stat-icon.danger { background: var(--danger-bg); color: var(--danger); border-color: rgba(244, 63, 94, 0.25); }

    .stat-details {
        position: relative;
        z-index: 2;
    }

    .stat-details h3 {
        font-size: 0.85rem;
        color: var(--text-secondary);
        font-weight: 700;
        margin-bottom: 0.35rem;
        letter-spacing: 0.3px;
    }

    .stat-details .value {
        font-size: 2.3rem;
        font-weight: 800;
        font-family: 'Outfit', sans-serif;
        line-height: 1;
        display: inline-block;
    }

    .stat-card.total .value { background: linear-gradient(135deg, #a78bfa, #00d2ff); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .stat-card.success .value { background: linear-gradient(135deg, #34d399, #059669); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .stat-card.warning .value { background: linear-gradient(135deg, #fbbf24, #d97706); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .stat-card.danger .value { background: linear-gradient(135deg, #f87171, #dc2626); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }

    .stat-card.total:hover { border-color: var(--accent-purple); box-shadow: 0 15px 45px rgba(139, 92, 246, 0.2); }
    .stat-card.success:hover { border-color: var(--success); box-shadow: 0 15px 45px rgba(16, 185, 129, 0.2); }
    .stat-card.warning:hover { border-color: var(--warning); box-shadow: 0 15px 45px rgba(245, 158, 11, 0.2); }
    .stat-card.danger:hover { border-color: var(--danger); box-shadow: 0 15px 45px rgba(244, 63, 94, 0.2); }

    .progress-bar-container {
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid var(--panel-border);
        border-radius: 20px;
        height: 8px;
        width: 100%;
        overflow: hidden;
        margin-top: 0.75rem;
        position: relative;
    }

    .progress-bar-fill {
        background: var(--accent-gradient);
        height: 100%;
        width: 0%;
        border-radius: 20px;
        transition: width 0.8s cubic-bezier(0.16, 1, 0.3, 1);
        box-shadow: 0 0 10px rgba(139, 92, 246, 0.4);
    }

    /* Breathing glowing status dot */
    .status-dot {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        display: inline-block;
        margin-inline-end: 0.6rem;
        background-color: var(--success);
        box-shadow: 0 0 10px var(--success);
        animation: pulse-glow-green 2s infinite;
    }
    
    .status-dot.danger {
        background-color: var(--danger);
        box-shadow: 0 0 10px var(--danger);
        animation: pulse-glow-red 2s infinite;
    }

    @keyframes pulse-glow-green {
        0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.6); }
        70% { box-shadow: 0 0 0 8px rgba(16, 185, 129, 0); }
        100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
    }

    @keyframes pulse-glow-red {
        0% { box-shadow: 0 0 0 0 rgba(244, 63, 94, 0.6); }
        70% { box-shadow: 0 0 0 8px rgba(244, 63, 94, 0); }
        100% { box-shadow: 0 0 0 0 rgba(244, 63, 94, 0); }
    }

    .score-badge {
        background: var(--active-menu-bg);
        border: 1px solid var(--panel-border);
        color: var(--accent-purple);
        padding: 0.25rem 0.75rem;
        font-family: 'Outfit', sans-serif;
        border-radius: var(--border-radius-sm);
        font-size: 0.85rem;
        font-weight: 800;
        display: inline-flex;
        align-items: center;
        gap: 0.4rem;
        transition: all 0.3s;
    }

    /* Terminal Console Window styling */
    .terminal-console {
        background: #04060f !important;
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: var(--border-radius-md);
        display: flex;
        flex-direction: column;
        overflow: hidden;
        box-shadow: var(--shadow-lg);
    }

    .terminal-header {
        background: #090c1a;
        padding: 0.75rem 1.25rem;
        border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        display: flex;
        align-items: center;
        justify-content: space-between;
    }

    .terminal-dots {
        display: flex;
        gap: 6px;
    }

    .terminal-dot {
        width: 10px;
        height: 10px;
        border-radius: 50%;
    }
    .terminal-dot.red { background: #ff5f56; }
    .terminal-dot.yellow { background: #ffbd2e; }
    .terminal-dot.green { background: #27c93f; }

    .terminal-body {
        padding: 1.25rem;
        font-family: 'Courier New', Courier, monospace;
        font-size: 0.8rem;
        color: #38edf6;
        line-height: 1.6;
        overflow-y: auto;
        flex-grow: 1;
        text-align: left;
        direction: ltr;
        background: linear-gradient(180deg, #04060f 0%, #080c1e 100%);
    }

    .terminal-body span {
        animation: type-in-log 0.2s ease-out;
    }

    @keyframes type-in-log {
        from { opacity: 0; transform: translateY(4px); }
        to { opacity: 1; transform: translateY(0); }
    }
</style>
@endsection

@section('content')
<div class="glass-panel" style="padding: 1.75rem 2.25rem; margin-bottom: 1.5rem; background: var(--active-menu-bg); border-color: var(--panel-border-hover);">
    <h1 style="font-size: 1.85rem; font-weight: 900; display: flex; align-items: center; gap: 0.85rem; letter-spacing: 0.5px;">
        <i class="fas fa-chart-pie" style="background: var(--accent-gradient); -webkit-background-clip: text; -webkit-text-fill-color: transparent; filter: drop-shadow(0 0 6px var(--accent-purple));"></i>
        لوحة التحكم والإحصائيات
    </h1>
    <p style="color: var(--text-secondary); margin-top: 0.35rem; font-size: 0.95rem; font-weight: bold;">
        مركز التحكم والمراقبة لأتمتة البحث الذكي، عزل الخلفيات، وتنسيق البيانات مع Google Sheets
    </p>
</div>

@if(isset($error))
    <div class="glass-panel" style="background-color: var(--danger-bg); border-color: var(--danger); color: var(--danger); font-weight: bold; margin-bottom: 2rem;">
        <i class="fas fa-exclamation-triangle" style="margin-inline-end: 0.5rem;"></i> {{ $error }}
    </div>
@endif

<!-- Statistics Grid -->
<div class="stats-grid">
    <div class="stat-card total">
        <div class="stat-icon total"><i class="fas fa-file-invoice"></i></div>
        <div class="stat-details">
            <h3>إجمالي منتجات الشيت</h3>
            <div class="value">{{ $total }}</div>
        </div>
    </div>
    
    <div class="stat-card success">
        <div class="stat-icon success"><i class="fas fa-check-circle"></i></div>
        <div class="stat-details">
            <h3>منتجات مكتملة (روابط)</h3>
            <div class="value">{{ $linked }}</div>
        </div>
    </div>
    
    <div class="stat-card warning">
        <div class="stat-icon warning"><i class="fas fa-exclamation-triangle"></i></div>
        <div class="stat-details">
            <h3>منتجات معلقة للمراجعة</h3>
            <div class="value">{{ $review }}</div>
        </div>
    </div>

    <div class="stat-card danger">
        <div class="stat-icon danger"><i class="fas fa-times-circle"></i></div>
        <div class="stat-details">
            <h3>أخطاء تقنية بـ SQLite</h3>
            <div class="value">{{ $errors }}</div>
        </div>
    </div>
</div>

<!-- API Metrics & Costs -->
<div class="stats-grid" style="grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1.5rem;">
    <div class="glass-panel" style="margin-bottom: 0; padding: 2rem;">
        <h3 style="font-size: 1.15rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 1rem; margin-bottom: 1.5rem; display: flex; align-items: center; gap: 0.75rem; color: var(--text-primary);">
            <i class="fas fa-key" style="color: #06b6d4;"></i> استهلاك واجهة البرمجة (API Usage)
        </h3>
        <div style="display: flex; flex-direction: column; gap: 1.25rem;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="color: var(--text-secondary); font-size: 0.9rem; font-weight: 600;">فحص صور Gemini Vision:</span>
                <strong style="font-family: 'Outfit', sans-serif; font-size: 1.25rem; color: #06b6d4;">{{ $metrics['gemini_api_calls'] ?? 0 }}</strong>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="color: var(--text-secondary); font-size: 0.9rem; font-weight: 600;">رفع الصور لـ Cloudinary:</span>
                <strong style="font-family: 'Outfit', sans-serif; font-size: 1.25rem; color: var(--success);">{{ $metrics['cloudinary_uploads'] ?? 0 }}</strong>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="color: var(--text-secondary); font-size: 0.9rem; font-weight: 600;">عمليات وفرها الكاش الدلالي:</span>
                <strong style="font-family: 'Outfit', sans-serif; font-size: 1.25rem; color: var(--accent-purple);"><i class="fas fa-bolt" style="margin-inline-end: 0.35rem; color: #f59e0b;"></i>{{ $metrics['semantic_cache_savings'] ?? 0 }}</strong>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center; border-top: 1px solid var(--panel-border); padding-top: 1rem; margin-top: 0.5rem;">
                <span style="color: var(--text-secondary); font-weight: bold; font-size: 0.95rem;">تكلفة الاستهلاك المقدرة:</span>
                <strong style="font-family: 'Outfit', sans-serif; font-size: 1.5rem; color: #f97316; filter: drop-shadow(0 0 8px rgba(249, 115, 22, 0.25));">${{ $estimatedCost }} USD</strong>
            </div>
            <!-- تفاصيل التكلفة المقدرة -->
            <div style="display: flex; flex-direction: column; gap: 0.45rem; background: rgba(0,0,0,0.15); padding: 0.75rem 1rem; border-radius: 10px; font-size: 0.8rem; margin-top: 0.25rem;">
                <div style="display: flex; justify-content: space-between;">
                    <span style="color: var(--text-secondary);">استعلامات فحص Gemini:</span>
                    <span style="color: #06b6d4; font-family: 'Outfit', sans-serif; font-weight: 700;">${{ number_format($metrics['gemini_cost'] ?? 0, 3) }}</span>
                </div>
                <div style="display: flex; justify-content: space-between;">
                    <span style="color: var(--text-secondary);">عزل خلفية PhotoRoom:</span>
                    <span style="color: var(--danger); font-family: 'Outfit', sans-serif; font-weight: 700;">${{ number_format($metrics['photoroom_cost'] ?? 0, 3) }}</span>
                </div>
                <div style="display: flex; justify-content: space-between;">
                    <span style="color: var(--text-secondary);">رفع وسائط Cloudinary:</span>
                    <span style="color: var(--success); font-family: 'Outfit', sans-serif; font-weight: 700;">${{ number_format($metrics['cloudinary_cost'] ?? 0, 3) }}</span>
                </div>
            </div>
        </div>
    </div>
    
    <div class="glass-panel" style="margin-bottom: 0; padding: 2rem; display: flex; flex-direction: column; justify-content: space-between;">
        <div>
            <h3 style="font-size: 1.15rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 1rem; margin-bottom: 1.5rem; display: flex; align-items: center; gap: 0.75rem; color: var(--text-primary);">
                <i class="fas fa-play-circle" style="color: var(--accent-purple);"></i> التحكم في الأتمتة بالخلفية
            </h3>
            <p style="font-size: 0.9rem; color: var(--text-secondary); margin-bottom: 1.5rem; line-height: 1.6;">
                يمكنك إطلاق معالجة وبحث الأتمتة لجميع المنتجات المتبقية في جدول Google Sheets دفعة واحدة بالخلفية. سيقوم النظام بعزل الخلفيات والتدقيق التلقائي لكل المنتجات.
            </p>
        </div>
        
        <div>
            <!-- Batch Automation progress panel -->
            <div id="batchProgressPanel" style="display: none; flex-direction: column; gap: 0.8rem; margin-bottom: 1.5rem; background: var(--input-bg); padding: 1.25rem; border-radius: var(--border-radius-md); border: 1px solid var(--panel-border);">
                <div style="display: flex; justify-content: space-between; font-size: 0.85rem; font-weight: bold; align-items: center;">
                    <span id="batchProgressText">جاري المعالجة...</span>
                    <span id="batchProgressPercent" style="color: var(--accent-purple); font-family: 'Outfit', sans-serif; font-size: 1.05rem;">0%</span>
                </div>
                <div class="progress-bar-container">
                    <div id="batchProgressBar" class="progress-bar-fill"></div>
                </div>
                <div id="batchProgressCounts" style="font-size: 0.8rem; color: var(--text-secondary); text-align: left; direction: ltr; font-family: 'Outfit', sans-serif; margin-top: 0.3rem;">
                    0 of 0 (Success: 0 | Failed: 0)
                </div>
                <button class="btn btn-secondary btn-sm" id="stopBatchBtn" onclick="stopBatchAutomation()" style="margin-top: 0.5rem; background: rgba(244, 63, 94, 0.1); border-color: rgba(244, 63, 94, 0.2); color: var(--danger); width: 100%;">
                    <i class="fas fa-stop"></i> إيقاف الأتمتة فوراً 🛑
                </button>
            </div>
            
            <!-- Curation Mode Switch -->
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.25rem; background: var(--card-bg); padding: 0.75rem 1.1rem; border-radius: 8px; border: 1px solid var(--panel-border);">
                <span style="font-size: 0.85rem; font-weight: 700; color: var(--text-secondary);"><i class="fas fa-eye" style="color: var(--accent-cyan); margin-inline-end: 0.35rem;"></i> أتمتة الفرز والمراجعة (Curation Mode)</span>
                <label class="switch" style="margin: 0;">
                    <input type="checkbox" id="curationMode" checked>
                    <span class="slider"></span>
                </label>
            </div>
            
            <button class="btn" id="runAllBtn" onclick="runAllAutomation()" style="width: 100%;">
                <i class="fas fa-play"></i> تشغيل أتمتة الشيت بالكامل (Batch)
            </button>
        </div>
    </div>
</div>

<!-- Live Telemetry Graph & Analytics -->
<div class="glass-panel" style="margin-top: 2rem; padding: 2rem;">
    <h3 style="font-size: 1.15rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 1rem; margin-bottom: 1.5rem; display: flex; align-items: center; gap: 0.75rem; color: var(--text-primary);">
        <i class="fas fa-chart-line" style="color: #3b82f6;"></i> مراقبة مؤشرات الأداء الفورية (Live Telemetry)
    </h3>
    <div style="background: var(--card-bg); border-radius: var(--border-radius-md); padding: 1.5rem; border: 1px solid var(--panel-border); position: relative; height: 320px; width: 100%;">
        <canvas id="telemetryChart"></canvas>
    </div>
</div>

<!-- Floating Log Button -->
<button type="button" class="btn" onclick="toggleLogDrawer()" style="position: fixed; bottom: 2rem; left: 2rem; z-index: 9999; border-radius: 50px; width: 60px; height: 60px; display: flex; align-items: center; justify-content: center; box-shadow: 0 8px 32px rgba(0, 210, 255, 0.35); font-size: 1.4rem; padding: 0; background: linear-gradient(135deg, var(--accent-purple) 0%, var(--accent-cyan) 100%); border: 1px solid rgba(255,255,255,0.15); cursor: pointer;" title="فتح سجل التشغيل المباشر">
    <i class="fas fa-terminal"></i>
</button>

<!-- Slide-out Log Drawer -->
<div id="logDrawer" style="position: fixed; top: 0; left: -430px; width: 420px; height: 100vh; background: rgba(5, 7, 18, 0.96); border-right: 1px solid var(--panel-border); box-shadow: var(--shadow-lg); backdrop-filter: blur(25px); -webkit-backdrop-filter: blur(25px); z-index: 10000; transition: left 0.4s cubic-bezier(0.4, 0, 0.2, 1); display: flex; flex-direction: column; direction: rtl; text-align: right;">
    <!-- Drawer Header -->
    <div style="padding: 1.5rem; border-bottom: 1px solid var(--panel-border); display: flex; justify-content: space-between; align-items: center;">
        <span style="font-weight: 800; font-size: 1.15rem; color: var(--text-primary); display: flex; align-items: center; gap: 0.5rem;">
            <i class="fas fa-terminal" style="color: var(--accent-cyan);"></i> سجلات التشغيل الحية (SSE Logs)
        </span>
        <button type="button" class="btn btn-secondary btn-sm" onclick="toggleLogDrawer()" style="width: 32px; height: 32px; border-radius: 50%; padding: 0; display: flex; align-items: center; justify-content: center;">
            <i class="fas fa-times"></i>
        </button>
    </div>

    <!-- Log Filters & Actions -->
    <div style="padding: 1rem; border-bottom: 1px solid var(--panel-border); display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap;">
        <select id="logTypeFilter" onchange="filterDrawerLogs()" style="flex: 1; padding: 6px 12px; font-size: 0.8rem; background: rgba(0,0,0,0.3); border: 1px solid var(--panel-border); color: var(--text-primary); border-radius: 8px; outline: none; font-family: inherit;">
            <option value="all">جميع السجلات</option>
            <option value="info">معلومات (Info)</option>
            <option value="warning">تنبيهات (Warning)</option>
            <option value="error">أخطاء (Error)</option>
        </select>
        <button type="button" class="btn btn-secondary btn-sm" onclick="downloadDrawerLogs()" style="font-size: 0.75rem; padding: 6px 12px;" title="تنزيل ملف السجل">
            <i class="fas fa-download"></i> تنزيل
        </button>
        <button type="button" class="btn btn-secondary btn-sm" onclick="clearDrawerLogs()" style="font-size: 0.75rem; padding: 6px 12px; color: var(--danger); background: rgba(244,63,94,0.05);" title="تفريغ الشاشة">
            <i class="fas fa-trash-alt"></i> مسح
        </button>
    </div>

    <!-- SSE Status Badge -->
    <div style="padding: 0.65rem 1.5rem; border-bottom: 1px solid rgba(255,255,255,0.03); background: rgba(0,0,0,0.1); font-size: 0.8rem; display: flex; justify-content: space-between; align-items: center;">
        <span style="color: var(--text-secondary); font-weight: 700;">حالة الاتصال المباشر:</span>
        <span id="sse-connection-status" style="display: inline-flex; align-items: center; font-weight: bold; font-family: 'Tajawal';">
            <span class="status-dot danger" id="sse-status-dot" style="margin-inline-end: 0.35rem;"></span>
            <span id="sse-status-text" style="color: var(--text-secondary);">مغلق</span>
        </span>
    </div>

    <!-- Logs Container -->
    <div id="sse-log-container" style="flex: 1; overflow-y: auto; padding: 1.5rem; font-family: monospace; font-size: 0.8rem; line-height: 1.6; color: #a7f3d0; background: #030409; direction: ltr; text-align: left; scroll-behavior: smooth;">
        <span style="color: var(--text-secondary); font-style: italic;">بانتظار تدفق الأحداث الحية من الخادم...</span>
    </div>
</div>

<!-- System Services Monitor & Auto-Start Controller -->
<div class="glass-panel" style="margin-top: 2rem; padding: 2rem;">
    <h3 style="font-size: 1.15rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 1rem; margin-bottom: 1.5rem; display: flex; align-items: center; gap: 0.75rem; color: var(--text-primary);">
        <i class="fas fa-server" style="color: #8b5cf6;"></i> إدارة الخدمات التشغيلية (Auto-Start Services)
    </h3>
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 2.5rem;">
        <div>
            <h4 style="font-size: 0.95rem; margin-bottom: 1rem; color: var(--text-primary); font-weight: 800; display: flex; align-items: center; gap: 0.5rem;">
                <i class="fas fa-signal" style="color: var(--text-secondary); font-size: 0.85rem;"></i> حالة خوادم النظام الحية
            </h4>
            <div style="display: flex; flex-direction: column; gap: 1rem;">
                <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255,255,255,0.03); padding-bottom: 0.65rem;">
                    <span style="color: var(--text-secondary); font-size: 0.9rem; font-weight: 600;">خادم لوحة التحكم (Laravel):</span>
                    <span id="status-laravel" class="score-badge" style="background: rgba(16, 185, 129, 0.08); color: var(--success); border-color: rgba(16, 185, 129, 0.15);">
                        <span class="status-dot" style="margin: 0;"></span>Port 8000
                    </span>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255,255,255,0.03); padding-bottom: 0.65rem;">
                    <span style="color: var(--text-secondary); font-size: 0.9rem; font-weight: 600;">خادم النماذج المساعد (FastAPI):</span>
                    <span id="status-fastapi" class="score-badge" style="background: rgba(239, 68, 68, 0.08); color: var(--danger); border-color: rgba(239, 68, 68, 0.15); font-weight: bold;">
                        <span class="status-dot danger" style="margin: 0;"></span>جاري الفحص...
                    </span>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center; padding-bottom: 0.25rem;">
                    <span style="color: var(--text-secondary); font-size: 0.9rem; font-weight: 600;">قاعدة البيانات المحلية (SQLite Cache):</span>
                    <span id="status-db" class="score-badge" style="background: var(--card-bg); color: var(--text-secondary); font-weight: bold;">جاري الفحص...</span>
                </div>
            </div>
        </div>
        
        <div style="display: flex; flex-direction: column; justify-content: center; gap: 0.85rem;">
            <h4 style="font-size: 0.95rem; margin-bottom: 0.35rem; color: var(--text-primary); font-weight: 800;">إجراءات التحكم السريعة للتشغيل التلقائي</h4>
            <div style="display: flex; gap: 1rem; width: 100%;">
                <button class="btn" id="startFlaskBtn" onclick="controlSystem('start-flask')" style="flex: 1; background: var(--success); border-color: var(--success); box-shadow: 0 4px 14px 0 rgba(16, 185, 129, 0.25);">
                    <i class="fas fa-play"></i> خادم بايثون المساعد 🚀
                </button>
                <button class="btn btn-secondary" id="stopFlaskBtn" onclick="controlSystem('stop-flask')" style="flex: 1;">
                    <i class="fas fa-info-circle"></i> تعليمات خادم النماذج
                </button>
            </div>
            <p style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 0.5rem; line-height: 1.6;">
                * يعمل خادم FastAPI بالخلفية لتقديم تحسينات الصور. تتم إدارته بشكل مباشر وتلقائي بالكامل عند بدء تشغيل لوحة التحكم عبر سكربت `setup_and_launch.bat`.
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
        
        // جلب التفضيلات المخزنة محلياً لتخصيص سلوك تشغيل الكل
        const settings = {
            ignoreUnitClash: localStorage.getItem('ignoreUnitClash') === 'true',
            strictBrandMatch: localStorage.getItem('strictBrandMatch') !== 'false',
            aiUpscale: localStorage.getItem('aiUpscale') !== 'false',
            aiEnhance: localStorage.getItem('aiEnhance') === 'true',
            skipCache: localStorage.getItem('skipCache') === 'true',
            target_width: parseInt(localStorage.getItem('target_width')) || 0,
            target_height: parseInt(localStorage.getItem('target_height')) || 0,
            padding_ratio: parseFloat(localStorage.getItem('padding_ratio')) || 0.85,
            bg_color: localStorage.getItem('bg_color') || 'ffffff',
            curation_mode: document.getElementById('curationMode') ? document.getElementById('curationMode').checked : true
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

    // إيقاف عملية الأتمتة فورياً
    async function stopBatchAutomation() {
        if (!confirm("⚠️ هل أنت متأكد من رغبتك في إيقاف عملية الأتمتة الكلية بالخلفية فورياً؟")) {
            return;
        }
        const btn = document.getElementById('stopBatchBtn');
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> جاري الإيقاف...';
        }
        try {
            const res = await fetch('/api/stop-batch', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content
                }
            });
            const data = await res.json();
            if (data.status === 'success') {
                alert("🛑 تم إيقاف عملية الأتمتة الكلية بنجاح!");
                location.reload();
            } else {
                alert("❌ فشل إيقاف الأتمتة: " + data.error);
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fas fa-stop"></i> إيقاف الأتمتة فوراً 🛑';
                }
            }
        } catch (err) {
            console.error("Error stopping batch:", err);
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = '<i class="fas fa-stop"></i> إيقاف الأتمتة فوراً 🛑';
            }
        }
    }

    // جلب حالة الخدمات والخوادم
    async function pollSystemStatus() {
        try {
            const res = await fetch('/api/system/status');
            const data = await res.json();
            
            const flaskEl = document.getElementById('status-fastapi');
            const dbEl = document.getElementById('status-db');
            const startBtn = document.getElementById('startFlaskBtn');
            const stopBtn = document.getElementById('stopFlaskBtn');
            
            if (data.fastapi_server === 'online') {
                flaskEl.innerHTML = '<span class="status-dot"></span>Port 8001 (فعال)';
                flaskEl.style.background = 'rgba(16, 185, 129, 0.08)';
                flaskEl.style.color = 'var(--success)';
                flaskEl.style.borderColor = 'rgba(16, 185, 129, 0.15)';
                
                startBtn.disabled = true;
                startBtn.style.opacity = '0.5';
                startBtn.style.cursor = 'not-allowed';
                stopBtn.disabled = false;
                stopBtn.style.opacity = '1';
                stopBtn.style.cursor = 'pointer';
            } else {
                flaskEl.innerHTML = '<span class="status-dot danger"></span>Port 8001 (مغلق)';
                flaskEl.style.background = 'rgba(239, 68, 68, 0.08)';
                flaskEl.style.color = 'var(--danger)';
                flaskEl.style.borderColor = 'rgba(239, 68, 68, 0.15)';
                
                startBtn.disabled = false;
                startBtn.style.opacity = '1';
                startBtn.style.cursor = 'pointer';
                stopBtn.disabled = true;
                stopBtn.style.opacity = '0.5';
                stopBtn.style.cursor = 'not-allowed';
            }
            
            if (data.local_cache_db === 'active') {
                dbEl.innerText = 'نشطة ومحملة 💾';
                dbEl.style.color = 'var(--success)';
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
        const isLight = document.body.classList.contains('light-theme');
        const gridColor = isLight ? 'rgba(0, 0, 0, 0.08)' : 'rgba(255, 255, 255, 0.05)';
        const textColor = isLight ? '#475569' : '#8e9bb0';
        const legendColor = isLight ? '#0f172a' : '#f3f4f6';

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
                        grid: { color: gridColor },
                        ticks: { color: textColor, font: { size: 9 } }
                    },
                    y: {
                        grid: { color: gridColor },
                        ticks: { color: textColor, font: { size: 9 } }
                    }
                },
                plugins: {
                    legend: {
                        labels: { color: legendColor, font: { size: 10 } }
                    }
                }
            }
        });

        // استماع لزر تغيير المظهر لتحديث ألوان الرسم البياني
        document.querySelector('.theme-toggle-btn')?.addEventListener('click', () => {
            setTimeout(() => {
                if (queueTelemetryChart) {
                    const activeLight = document.body.classList.contains('light-theme');
                    const updatedGrid = activeLight ? 'rgba(0, 0, 0, 0.08)' : 'rgba(255, 255, 255, 0.05)';
                    const updatedText = activeLight ? '#475569' : '#8e9bb0';
                    const updatedLegend = activeLight ? '#0f172a' : '#f3f4f6';

                    queueTelemetryChart.options.scales.x.grid.color = updatedGrid;
                    queueTelemetryChart.options.scales.x.ticks.color = updatedText;
                    queueTelemetryChart.options.scales.y.grid.color = updatedGrid;
                    queueTelemetryChart.options.scales.y.ticks.color = updatedText;
                    queueTelemetryChart.options.plugins.legend.labels.color = updatedLegend;
                    queueTelemetryChart.update();
                }
            }, 100);
        });
    }

    let rawLogText = "";

    function toggleLogDrawer() {
        const drawer = document.getElementById('logDrawer');
        if (drawer.style.left === '0px') {
            drawer.style.left = '-430px';
        } else {
            drawer.style.left = '0px';
        }
    }

    function filterDrawerLogs() {
        const filterType = document.getElementById('logTypeFilter').value;
        const logs = document.querySelectorAll('#sse-log-container span');
        logs.forEach(log => {
            if (log.dataset.severity) {
                if (filterType === 'all' || log.dataset.severity === filterType) {
                    log.style.display = 'block';
                } else {
                    log.style.display = 'none';
                }
            }
        });
    }

    function downloadDrawerLogs() {
        const blob = new Blob([rawLogText], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `pipeline_log_${new Date().toISOString().slice(0,10)}.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    function clearDrawerLogs() {
        document.getElementById('sse-log-container').innerHTML = '<span style="color: var(--text-secondary); font-style: italic;">تم تفريغ السجل. بانتظار أحداث جديدة...</span>';
        rawLogText = "";
    }

    // فتح اتصال البث الحي SSE مع خادم بايثون
    function startSSEConnection() {
        const statusEl = document.getElementById('sse-connection-status');
        const logContainer = document.getElementById('sse-log-container');
        
        try {
            const eventSource = new EventSource('http://127.0.0.1:8001/api/v1/telemetry/stream/enterprise_tenant_102');
            
            eventSource.onopen = () => {
                const sseDot = document.getElementById('sse-status-dot');
                const sseText = document.getElementById('sse-status-text');
                if (sseDot) {
                    sseDot.className = 'status-dot';
                }
                if (sseText) {
                    sseText.innerText = 'متصل';
                    sseText.style.color = 'var(--success)';
                }
            };
            
            eventSource.onmessage = (event) => {
                const payload = JSON.parse(event.data);
                
                // إضافة السجل في شاشة الكونسول الجانبية
                if (payload.log) {
                    if (logContainer.innerHTML.includes('بانتظار تدفق')) {
                        logContainer.innerHTML = '';
                    }
                    rawLogText += payload.log + "\n";
                    const newLog = document.createElement('span');
                    newLog.style.display = 'block';
                    newLog.style.borderBottom = '1px solid rgba(255,255,255,0.02)';
                    newLog.style.paddingBottom = '4px';
                    
                    let severity = 'info';
                    let logTextLower = payload.log.toLowerCase();
                    if (logTextLower.includes('error') || logTextLower.includes('fail')) {
                        severity = 'error';
                        newLog.style.color = '#f87171'; // red
                    } else if (logTextLower.includes('warning') || logTextLower.includes('warn')) {
                        severity = 'warning';
                        newLog.style.color = '#fbbf24'; // orange
                    } else {
                        newLog.style.color = '#a7f3d0'; // green
                    }
                    
                    newLog.dataset.severity = severity;
                    newLog.innerText = payload.log;
                    logContainer.appendChild(newLog);
                    
                    filterDrawerLogs();
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
                const sseDot = document.getElementById('sse-status-dot');
                const sseText = document.getElementById('sse-status-text');
                if (sseDot) {
                    sseDot.className = 'status-dot danger';
                }
                if (sseText) {
                    sseText.innerText = 'مغلق';
                    sseText.style.color = 'var(--danger)';
                }
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
