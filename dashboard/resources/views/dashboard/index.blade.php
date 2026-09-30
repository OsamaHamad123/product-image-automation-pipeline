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

    .stat-icon.total { background: rgba(255, 255, 255, 0.08); color: var(--text-primary); border-color: var(--panel-border); }
    .stat-icon.success { background: var(--success-bg); color: var(--success); border-color: var(--panel-border); }
    .stat-icon.warning { background: var(--warning-bg); color: var(--warning); border-color: var(--panel-border); }
    .stat-icon.danger { background: var(--danger-bg); color: var(--danger); border-color: var(--panel-border); }
    .stat-icon.info { background: var(--info-bg); color: var(--info); border-color: var(--panel-border); }

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

    .stat-card.total .value { color: var(--text-primary); }
    .stat-card.success .value { color: var(--success); }
    .stat-card.warning .value { color: var(--warning); }
    .stat-card.danger .value { color: var(--danger); }
    .stat-card.info .value { color: var(--info); }

    .stat-card.total:hover { border-color: var(--accent-purple); box-shadow: 0 10px 30px rgba(255, 255, 255, 0.03); }
    .stat-card.success:hover { border-color: var(--panel-border-hover); box-shadow: 0 10px 30px rgba(255, 255, 255, 0.03); }
    .stat-card.warning:hover { border-color: var(--panel-border-hover); box-shadow: 0 10px 30px rgba(255, 255, 255, 0.03); }
    .stat-card.danger:hover { border-color: var(--danger); box-shadow: 0 10px 30px rgba(239, 68, 68, 0.03); }
    .stat-card.info:hover { border-color: var(--info); box-shadow: 0 10px 30px rgba(6, 182, 212, 0.03); }

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
        box-shadow: 0 0 10px var(--btn-shadow);
    }

    /* Breathing glowing status dot */
    .status-dot {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        display: inline-block;
        margin-inline-end: 0.6rem;
        background-color: var(--success);
        box-shadow: 0 0 10px var(--btn-shadow);
        animation: pulse-glow-green 2s infinite;
    }
    
    .status-dot.danger {
        background-color: var(--danger);
        box-shadow: 0 0 10px var(--btn-shadow);
        animation: pulse-glow-red 2s infinite;
    }

    @keyframes pulse-glow-green {
        0% { box-shadow: 0 0 0 0 rgba(255, 255, 255, 0.5); }
        70% { box-shadow: 0 0 0 8px rgba(255, 255, 255, 0); }
        100% { box-shadow: 0 0 0 0 rgba(255, 255, 255, 0); }
    }

    @keyframes pulse-glow-red {
        0% { box-shadow: 0 0 0 0 rgba(115, 115, 115, 0.5); }
        70% { box-shadow: 0 0 0 8px rgba(115, 115, 115, 0); }
        100% { box-shadow: 0 0 0 0 rgba(115, 115, 115, 0); }
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
    .terminal-dot.red { background: #555555; }
    .terminal-dot.yellow { background: #888888; }
    .terminal-dot.green { background: #bbbbbb; }

    .terminal-body {
        padding: 1.25rem;
        font-family: 'Courier New', Courier, monospace;
        font-size: 0.8rem;
        color: #ffffff;
        line-height: 1.6;
        overflow-y: auto;
        flex-grow: 1;
        text-align: left;
        direction: ltr;
        background: #000000;
    }

    .terminal-body span {
        animation: type-in-log 0.2s ease-out;
    }

    @keyframes type-in-log {
        from { opacity: 0; transform: translateY(4px); }
        to { opacity: 1; transform: translateY(0); }
    }
    
    @media (max-width: 992px) {
        .telemetry-grid {
            grid-template-columns: 1fr !important;
        }
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

    <div class="stat-card info">
        <div class="stat-icon info"><i class="fas fa-hourglass-start"></i></div>
        <div class="stat-details">
            <h3>منتجات لم تبدأ بعد</h3>
            <div class="value">{{ $missing }}</div>
        </div>
    </div>

    <div class="stat-card danger">
        <div class="stat-icon danger"><i class="fas fa-times-circle"></i></div>
        <div class="stat-details">
            <h3>أخطاء تقنية بـ MariaDB</h3>
            <div class="value">{{ $errors }}</div>
        </div>
    </div>
</div>

<!-- عدادات الأتمتة الحقيقية من جدول automation_queue -->
@php
    $qs = $queueCounters['by_status'] ?? [];
    $qCodes = $queueCounters['by_failure_code'] ?? [];
@endphp
<div class="glass-panel" style="margin-bottom: 2.5rem; padding: 2rem; border-color: var(--panel-border-hover);">
    <h2 style="font-size: 1.35rem; font-weight: 900; border-bottom: 1px solid var(--panel-border); padding-bottom: 1rem; margin-bottom: 1.75rem; display: flex; align-items: center; justify-content: space-between; color: var(--text-primary);">
        <span><i class="fas fa-list-ol" style="color: var(--accent-purple); margin-inline-end: 0.5rem;"></i> عدادات الأتمتة والمراجعة (من قاعدة البيانات)</span>
        <span class="score-badge" style="font-size: 0.75rem;">تحديث تلقائي كل 3 ثوانٍ</span>
    </h2>

    <div class="stats-grid" style="grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1.5rem; margin-bottom: 1.5rem;">
        <div class="stat-card warning">
            <div class="stat-icon warning"><i class="fas fa-eye"></i></div>
            <div class="stat-details">
                <h3>جاهزة للمراجعة</h3>
                <div class="value" id="valReadyForReview">{{ $qs['ready_for_review'] ?? 0 }}</div>
            </div>
        </div>
        <div class="stat-card success">
            <div class="stat-icon success"><i class="fas fa-check-double"></i></div>
            <div class="stat-details">
                <h3>معتمدة ومنشورة</h3>
                <div class="value" id="valApproved">{{ $qs['completed'] ?? 0 }}</div>
            </div>
        </div>
        <div class="stat-card info">
            <div class="stat-icon info"><i class="fas fa-hourglass-half"></i></div>
            <div class="stat-details">
                <h3>بانتظار المعالجة</h3>
                <div class="value" id="valPendingQueue">{{ ($qs['pending'] ?? 0) + ($qs['processing'] ?? 0) }}</div>
            </div>
        </div>
        <div class="stat-card danger">
            <div class="stat-icon danger"><i class="fas fa-times-circle"></i></div>
            <div class="stat-details">
                <h3>فشلت (حسب الطابور)</h3>
                <div class="value" id="valFailedQueue">{{ $qs['failed'] ?? 0 }}</div>
            </div>
        </div>
    </div>

    <div style="background: rgba(0, 0, 0, 0.2); padding: 1rem 1.25rem; border-radius: var(--border-radius-sm); border: 1px solid var(--panel-border);">
        <span style="font-weight: 700; font-size: 0.85rem; color: var(--text-secondary); display: block; margin-bottom: 0.6rem;">أسباب الفشل وإعادة المحاولة حسب الرمز (failure_code):</span>
        <div id="failureCodesList" style="display: flex; flex-wrap: wrap; gap: 0.6rem;">
            @forelse($qCodes as $code => $count)
                <span class="score-badge">{{ $code }}: {{ $count }}</span>
            @empty
                <span style="font-size: 0.8rem; color: var(--text-secondary);">لا توجد رموز فشل.</span>
            @endforelse
        </div>
        <span style="font-size: 0.75rem; color: var(--text-secondary); display: block; margin-top: 0.6rem;">PROVIDER_DOWN و VERIFIER_DOWN أعطال مؤقتة في المحركات وليست منتجات غير موجودة.</span>
    </div>
</div>

<!-- Verified catalog counts & batch control -->
<div class="stats-grid" style="grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1.5rem;">

    <div class="glass-panel" style="margin-bottom: 0; padding: 2rem;">
        <h3 style="font-size: 1.15rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 1rem; margin-bottom: 1.5rem; display: flex; align-items: center; gap: 0.75rem; color: var(--text-primary);">
            <i class="fas fa-shield-alt" style="color: #06b6d4;"></i> الصور المعتمدة حسب حالة التحقق
        </h3>
        <div style="display: flex; flex-direction: column; gap: 1.25rem;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="color: var(--text-secondary); font-size: 0.9rem; font-weight: 600;">معتمدة بواسطة مراجع بشري:</span>
                <strong style="font-family: 'Outfit', sans-serif; font-size: 1.25rem; color: var(--success);">{{ $resolvedByStatus['human_approved'] ?? 0 }}</strong>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="color: var(--text-secondary); font-size: 0.9rem; font-weight: 600;">منشورة تلقائياً بعد تحقق كامل:</span>
                <strong style="font-family: 'Outfit', sans-serif; font-size: 1.25rem; color: #06b6d4;">{{ $resolvedByStatus['auto_verified'] ?? 0 }}</strong>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="color: var(--text-secondary); font-size: 0.9rem; font-weight: 600;">قديمة (قبل التحقق، تحتاج مراجعة):</span>
                <strong style="font-family: 'Outfit', sans-serif; font-size: 1.25rem; color: var(--warning);">{{ $resolvedByStatus['legacy'] ?? 0 }}</strong>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="color: var(--text-secondary); font-size: 0.9rem; font-weight: 600;">ملغاة بعد رفض بشري:</span>
                <strong style="font-family: 'Outfit', sans-serif; font-size: 1.25rem; color: var(--danger);">{{ $resolvedByStatus['superseded'] ?? 0 }}</strong>
            </div>
            <p style="font-size: 0.78rem; color: var(--text-secondary); margin: 0; line-height: 1.6; border-top: 1px solid var(--panel-border); padding-top: 0.75rem;">
                أعداد فعلية من جدول resolved_products. لا تُعرض نسب مطابقة أو تكاليف تقديرية.
            </p>
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
            <!-- خطأ التشغيل أو تنبيه العامل بالعربية (data.alert من /api/batch-status) -->
            <div id="batchStateAlert" role="alert" style="display: none; margin-bottom: 1rem; padding: 0.85rem 1rem; border-radius: var(--border-radius-sm); background: var(--danger-bg); border: 1px solid var(--danger); color: var(--danger); font-size: 0.85rem; font-weight: 800; line-height: 1.6;"></div>
            <p id="batchPhaseNote" style="font-size: 0.85rem; color: var(--text-secondary); margin: 0 0 1rem 0; font-weight: 700;"></p>
            <!-- Batch Automation progress panel -->
            <div id="batchProgressPanel" style="display: none; flex-direction: column; gap: 0.8rem; margin-bottom: 1.5rem; background: var(--input-bg); padding: 1.25rem; border-radius: var(--border-radius-md); border: 1px solid var(--panel-border);">
                <div style="display: flex; justify-content: space-between; font-size: 0.85rem; font-weight: bold; align-items: center;">
                    <span id="batchProgressText">جاري المعالجة...</span>
                    <span id="batchProgressPercent" style="color: var(--accent-purple); font-family: 'Outfit', sans-serif; font-size: 1.05rem;">0%</span>
                </div>
                <div class="progress-bar-container">
                    <div id="batchProgressBar" class="progress-bar-fill"></div>
                </div>
                <div id="batchProgressCounts" style="font-size: 0.8rem; color: var(--text-secondary);"></div>
                <div style="display: flex; gap: 0.5rem; margin-top: 0.5rem; width: 100%;">
                    <button type="button" class="btn btn-secondary btn-sm" id="pauseResumeBatchBtn" onclick="togglePauseResumeAutomation()" style="flex: 1; background: var(--warning-bg); border-color: var(--panel-border); color: var(--warning); font-weight: bold; border-radius: 10px;">
                        <i class="fas fa-pause" id="pauseResumeIcon"></i> <span id="pauseResumeText">إيقاف مؤقت</span>
                    </button>
                    <button type="button" class="btn btn-secondary btn-sm" id="stopBatchBtn" onclick="stopBatchAutomation()" title="يوقف العامل ويعيد الصفوف قيد المعالجة إلى الانتظار؛ لا يحذف أي صف" style="flex: 1; background: var(--danger-bg); border-color: var(--panel-border); color: var(--danger); font-weight: bold; border-radius: 10px;">
                        <i class="fas fa-stop"></i> إيقاف التشغيل 🛑
                    </button>
                </div>
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

<!-- Live Telemetry Graph & Analytics Grid -->
<div class="telemetry-grid" style="display: grid; grid-template-columns: 2fr 1fr; gap: 1.5rem; margin-top: 2rem;">
    <!-- Live Telemetry Chart -->
    <div class="glass-panel" style="margin-bottom: 0; padding: 2rem;">
        <h3 style="font-size: 1.15rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 1rem; margin-bottom: 1.5rem; display: flex; align-items: center; gap: 0.75rem; color: var(--text-primary);">
            <i class="fas fa-chart-line" style="color: var(--text-primary);"></i> تقدم الأتمتة الحالي (من حالة الطابور)
        </h3>
        <div style="background: var(--card-bg); border-radius: var(--border-radius-md); padding: 1.5rem; border: 1px solid var(--panel-border); position: relative; height: 320px; width: 100%;">
            <canvas id="telemetryChart"></canvas>
        </div>
    </div>
    
    <!-- Real-time Cost & Speed Curation Metrics -->
    <div class="glass-panel" style="margin-bottom: 0; padding: 2rem; display: flex; flex-direction: column; justify-content: space-between;">
        <div>
            <h3 style="font-size: 1.15rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 1rem; margin-bottom: 1.5rem; display: flex; align-items: center; gap: 0.75rem; color: var(--text-primary);">
                <i class="fas fa-tachometer-alt" style="color: var(--accent-purple);"></i> إحصائيات المعالجة الفورية
            </h3>
            
            <div style="display: flex; flex-direction: column; gap: 1.15rem; margin-top: 1rem;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="color: var(--text-secondary); font-size: 0.85rem; font-weight: 600;">سرعة المعالجة الحالية:</span>
                    <strong id="liveThroughput" style="font-family: 'Outfit', sans-serif; font-size: 1.1rem; color: var(--success);">0.0 منتج/دقيقة</strong>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="color: var(--text-secondary); font-size: 0.85rem; font-weight: 600;">الوقت المتبقي للاكتمال (ETA):</span>
                    <strong id="liveETA" style="font-family: 'Outfit', sans-serif; font-size: 1.1rem; color: var(--info);">00:00:00</strong>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="color: var(--text-secondary); font-size: 0.85rem; font-weight: 600;">معدل النجاح الإجمالي:</span>
                    <strong style="font-family: 'Outfit', sans-serif; font-size: 1.1rem; color: var(--success);">{{ $total > 0 ? round(($linked / $total) * 100, 1) : 0 }}%</strong>
                </div>
            </div>
        </div>
        
    </div>
</div>

<!-- Floating Log Button -->
<button type="button" class="btn" onclick="toggleLogDrawer()" style="position: fixed; bottom: 2rem; left: 2rem; z-index: 9999; border-radius: 50px; width: 60px; height: 60px; display: flex; align-items: center; justify-content: center; box-shadow: 0 8px 32px var(--btn-hover-shadow); font-size: 1.4rem; padding: 0; background: var(--accent-gradient); border: 1px solid rgba(255,255,255,0.15); cursor: pointer;" title="فتح سجل التشغيل المباشر">
    <i class="fas fa-terminal"></i>
</button>

<!-- Slide-out Log Drawer -->
<div id="logDrawer" style="position: fixed; top: 0; left: -430px; width: 420px; height: 100vh; background: rgba(5, 7, 18, 0.96); border-right: 1px solid var(--panel-border); box-shadow: var(--shadow-lg); backdrop-filter: blur(25px); -webkit-backdrop-filter: blur(25px); z-index: 10000; transition: left 0.4s cubic-bezier(0.4, 0, 0.2, 1); display: flex; flex-direction: column; direction: rtl; text-align: right;">
    <!-- Drawer Header -->
    <div style="padding: 1.5rem; border-bottom: 1px solid var(--panel-border); display: flex; justify-content: space-between; align-items: center;">
        <span style="font-weight: 800; font-size: 1.15rem; color: var(--text-primary); display: flex; align-items: center; gap: 0.5rem;">
            <i class="fas fa-terminal" style="color: var(--accent-cyan);"></i> سجل التشغيل (pipeline.log)
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
        <button type="button" class="btn btn-secondary btn-sm" onclick="clearDrawerLogs()" style="font-size: 0.75rem; padding: 6px 12px; color: var(--danger); background: var(--danger-bg);" title="تفريغ الشاشة">
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
    <div id="sse-log-container" style="flex: 1; overflow-y: auto; padding: 1.5rem; font-family: monospace; font-size: 0.8rem; line-height: 1.6; color: var(--text-primary); background: #000000; direction: ltr; text-align: left; scroll-behavior: smooth;">
        <span style="color: var(--text-secondary); font-style: italic;">بانتظار تدفق الأحداث الحية من الخادم...</span>
    </div>
</div>

<!-- System Services Monitor & Auto-Start Controller -->
<div class="glass-panel" style="margin-top: 2rem; padding: 2rem;">
    <h3 style="font-size: 1.15rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 1rem; margin-bottom: 1.5rem; display: flex; align-items: center; gap: 0.75rem; color: var(--text-primary);">
        <i class="fas fa-server" style="color: var(--text-primary);"></i> إدارة الخدمات التشغيلية (Auto-Start Services)
    </h3>
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 2.5rem;">
        <div>
            <h4 style="font-size: 0.95rem; margin-bottom: 1rem; color: var(--text-primary); font-weight: 800; display: flex; align-items: center; gap: 0.5rem;">
                <i class="fas fa-signal" style="color: var(--text-secondary); font-size: 0.85rem;"></i> حالة خوادم النظام الحية
            </h4>
            <div style="display: flex; flex-direction: column; gap: 1rem;">
                <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255,255,255,0.03); padding-bottom: 0.65rem;">
                    <span style="color: var(--text-secondary); font-size: 0.9rem; font-weight: 600;">خادم لوحة التحكم (Laravel):</span>
                    <span id="status-laravel" class="score-badge" style="background: var(--success-bg); color: var(--success); border-color: var(--panel-border);">
                        <span class="status-dot" style="margin: 0;"></span>Port 8000
                    </span>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255,255,255,0.03); padding-bottom: 0.65rem;">
                    <span style="color: var(--text-secondary); font-size: 0.9rem; font-weight: 600;">جسر بايثون (cli_bridge.py):</span>
                    <span id="status-bridge" class="score-badge" style="background: var(--card-bg); color: var(--text-secondary); border-color: var(--panel-border); font-weight: bold;">جاري الفحص...</span>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center; padding-bottom: 0.25rem;">
                    <span style="color: var(--text-secondary); font-size: 0.9rem; font-weight: 600;">قاعدة البيانات المحلية (MariaDB Cache):</span>
                    <span id="status-db" class="score-badge" style="background: var(--card-bg); color: var(--text-secondary); font-weight: bold;">جاري الفحص...</span>
                </div>
            </div>
        </div>
        
        <div style="display: flex; flex-direction: column; justify-content: center; gap: 0.85rem;">
            <h4 style="font-size: 0.95rem; margin-bottom: 0.35rem; color: var(--text-primary); font-weight: 800;">طريقة التشغيل</h4>
            <p style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 0.5rem; line-height: 1.6;">
                كل إجراءات لوحة التحكم (البحث، الاعتماد، الرفض) تُنفذ مباشرة عبر <code>cli_bridge.py</code> بترميز UTF-8. لا يوجد خادم FastAPI منفصل يحتاج للتشغيل أو الإيقاف.
            </p>
        </div>
    </div>
</div>
@endsection

@section('scripts')
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
    // تحديث حالة الأتمتة بالخلفية
    let isPaused = false;

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

    // عدادات الطابور الحقيقية من /api/batch-status
    function renderQueueCounters(data) {
        const q = data.queue || {};
        const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
        setText('valReadyForReview', data.ready_for_review || 0);
        setText('valApproved', data.approved || 0);
        setText('valPendingQueue', (q.pending || 0) + (q.processing || 0));
        setText('valFailedQueue', q.failed || 0);
        const list = document.getElementById('failureCodesList');
        if (list) {
            list.textContent = '';
            const codes = Object.entries(data.failed_by_code || {});
            if (!codes.length) {
                const empty = document.createElement('span');
                empty.style.fontSize = '0.8rem';
                empty.style.color = 'var(--text-secondary)';
                empty.textContent = 'لا توجد رموز فشل.';
                list.appendChild(empty);
            }
            codes.forEach(([code, count]) => {
                const badge = document.createElement('span');
                badge.className = 'score-badge';
                badge.textContent = `${code}: ${count}`;
                list.appendChild(badge);
            });
        }
        if (queueTelemetryChart) {
            queueTelemetryChart.data.labels.push(new Date().toLocaleTimeString());
            queueTelemetryChart.data.datasets[0].data.push(data.current || 0);
            queueTelemetryChart.data.datasets[1].data.push(data.ready_for_review || 0);
            if (queueTelemetryChart.data.labels.length > 20) {
                queueTelemetryChart.data.labels.shift();
                queueTelemetryChart.data.datasets[0].data.shift();
                queueTelemetryChart.data.datasets[1].data.shift();
            }
            queueTelemetryChart.update();
        }
    }

    // الشريط الأحمر: خطأ التشغيل أو تنبيه العامل كما يصوغه الخادم بالعربية (data.alert)
    function renderStateAlert(data) {
        const box = document.getElementById('batchStateAlert');
        if (!box) return;
        box.textContent = data.alert ? '⚠️ ' + data.alert : '';
        box.style.display = data.alert ? 'block' : 'none';
    }

    // زر التشغيل: run (تشغيل جديد) | busy (تشغيل جارٍ) | review (فتح تبويب المراجعة في صفحة الأتمتة مباشرة)
    function setRunButton(mode) {
        const runBtn = document.getElementById('runAllBtn');
        runBtn.disabled = mode === 'busy';
        runBtn.className = mode === 'review' ? 'btn btn-secondary' : 'btn';
        runBtn.style.background = mode === 'review' ? 'var(--active-menu-bg)' : '';
        runBtn.style.borderColor = mode === 'review' ? 'var(--panel-border)' : '';
        runBtn.style.color = mode === 'review' ? 'var(--text-primary)' : '';
        if (mode === 'busy') {
            runBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> جاري الأتمتة بالخلفية...';
        } else if (mode === 'review') {
            runBtn.innerHTML = '<i class="fas fa-images"></i> فتح تبويب فرز واعتماد الصور الآن';
            runBtn.onclick = () => window.location.href = "/batch-automation?tab=review";
        } else {
            runBtn.innerHTML = '<i class="fas fa-play"></i> تشغيل أتمتة الشيت بالكامل (Batch)';
            runBtn.onclick = runAllAutomation;
        }
    }

    // سرعة المعالجة والوقت المتبقي من تقدم التشغيل الحالي فقط (run)؛ القياس يبدأ من جديد لكل تشغيل
    function updateRunEta(run) {
        const key = String(run.run_id || '');
        if (localStorage.getItem('batch_start_run') !== key || !localStorage.getItem('batch_start_time')) {
            localStorage.setItem('batch_start_run', key);
            localStorage.setItem('batch_start_time', Date.now());
            localStorage.setItem('batch_start_current', run.processed || 0);
        }
        const startTime = parseInt(localStorage.getItem('batch_start_time'));
        const startCurrent = parseInt(localStorage.getItem('batch_start_current'));
        const current = run.processed || 0;
        if (!startTime || isNaN(startTime) || current < startCurrent) return;
        const elapsedSeconds = (Date.now() - startTime) / 1000;
        const processedCount = current - startCurrent;
        if (elapsedSeconds <= 1 || processedCount <= 0) return;
        const itemsPerSecond = processedCount / elapsedSeconds;
        const liveThroughputEl = document.getElementById('liveThroughput');
        if (liveThroughputEl) liveThroughputEl.innerText = `${(itemsPerSecond * 60).toFixed(1)} منتج/دقيقة`;
        const etaSeconds = Math.max(0, (run.total || 0) - current) / itemsPerSecond;
        const pad = (num) => String(num).padStart(2, '0');
        const liveETAEl = document.getElementById('liveETA');
        if (liveETAEl) liveETAEl.innerText = `${pad(Math.floor(etaSeconds / 3600))}:${pad(Math.floor((etaSeconds % 3600) / 60))}:${pad(Math.floor(etaSeconds % 60))}`;
    }

    function resetRunEta() {
        localStorage.removeItem('batch_start_run');
        localStorage.removeItem('batch_start_time');
        localStorage.removeItem('batch_start_current');
        const liveThroughputEl = document.getElementById('liveThroughput');
        if (liveThroughputEl) liveThroughputEl.innerText = '0.0 منتج/دقيقة';
        const liveETAEl = document.getElementById('liveETA');
        if (liveETAEl) liveETAEl.innerText = '00:00:00';
    }

    // مراحل التشغيل كما يحسبها الخادم (data.phase)
    const ACTIVE_PHASES = ['starting', 'running', 'paused', 'stopping'];
    let lastRunPhase = null;

    async function pollBatchStatus() {
        try {
            const res = await fetch('/api/batch-status');
            const data = await res.json();
            renderQueueCounters(data);
            renderStateAlert(data);
            
            const panel = document.getElementById('batchProgressPanel');
            const phase = data.phase || 'idle';
            const run = data.run || {};
            const note = document.getElementById('batchPhaseNote');
            if (note) note.textContent = ACTIVE_PHASES.includes(phase) ? '' : (data.phase_text || '');
            
            if (ACTIVE_PHASES.includes(phase)) {
                panel.style.display = 'flex';
                // التقدم من صفوف هذا التشغيل فقط؛ أثناء قراءة الشيت لا تُعرض أرقام التشغيل السابق
                const percent = run.total > 0 ? Math.round((run.processed / run.total) * 100) : 0;
                document.getElementById('batchProgressPercent').innerText = phase === 'starting' ? '—' : percent + '%';
                document.getElementById('batchProgressBar').style.width = (phase === 'starting' ? 0 : percent) + '%';
                if (phase === 'running') {
                    updateRunEta(run);
                } else {
                    resetRunEta();
                }
                
                isPaused = (phase === 'paused');
                const pBtn = document.getElementById('pauseResumeBatchBtn');
                const pIcon = document.getElementById('pauseResumeIcon');
                const pTxt = document.getElementById('pauseResumeText');
                // الإيقاف المؤقت يخص العامل: لا معنى له أثناء قراءة الشيت أو الإيقاف
                pBtn.disabled = phase === 'starting' || phase === 'stopping';
                const progressText = document.getElementById('batchProgressText');
                
                if (isPaused) {
                    pIcon.className = 'fas fa-play';
                    pTxt.innerText = 'استئناف الأتمتة';
                    pBtn.style.color = 'var(--text-primary)';
                    pBtn.style.background = 'var(--success-bg)';
                    pBtn.style.borderColor = 'var(--panel-border)';
                    progressText.innerHTML = `<strong style="color: var(--warning);"><i class="fas fa-pause-circle"></i> الأتمتة موقوفة مؤقتاً</strong>`;
                } else {
                    pIcon.className = 'fas fa-pause';
                    pTxt.innerText = 'إيقاف مؤقت';
                    pBtn.style.color = 'var(--text-secondary)';
                    pBtn.style.background = 'var(--warning-bg)';
                    pBtn.style.borderColor = 'var(--panel-border)';
                    if (phase === 'running') {
                        progressText.textContent = 'جاري تحضير مرشحات: ';
                        const strong = document.createElement('strong');
                        strong.style.color = 'var(--accent-cyan)';
                        strong.textContent = data.current_product || 'جاري البحث...';
                        progressText.appendChild(strong);
                    } else {
                        progressText.textContent = data.phase_text || '';
                    }
                }
                
                document.getElementById('batchProgressCounts').innerText = phase === 'starting' ? '' :
                    `${run.processed || 0} من ${run.total || 0} في هذا التشغيل — للمراجعة: ${run.ready_for_review || 0} | معتمدة: ${run.completed || 0} | فشل: ${run.failed || 0} | بانتظار: ${(run.pending || 0) + (run.processing || 0)}`;
                
                const stopBtn = document.getElementById('stopBatchBtn');
                const stopping = phase === 'stopping' || data.stop_requested === 1;
                stopBtn.disabled = stopping;
                stopBtn.innerHTML = stopping ? '<i class="fas fa-spinner fa-spin"></i> طلب الإيقاف مسجل' : '<i class="fas fa-stop"></i> إيقاف التشغيل 🛑';
                setRunButton('busy');
            } else if (phase === 'review') {
                resetRunEta();
                panel.style.display = 'flex';
                document.getElementById('batchProgressPercent').innerText = '100%';
                document.getElementById('batchProgressBar').style.width = '100%';
                document.getElementById('batchProgressText').innerHTML = `<strong style="color: var(--success);"><i class="fas fa-check-circle"></i> انتهى التحضير: منتجات جاهزة للمراجعة.</strong>`;
                document.getElementById('batchProgressCounts').innerText = `${data.ready_for_review || 0} منتج بانتظار المراجعة في تبويب الفرز والاعتماد.`;
                setRunButton('review');
            } else {
                resetRunEta();
                panel.style.display = 'none';
                setRunButton('run');
                if (ACTIVE_PHASES.includes(lastRunPhase)) {
                    location.reload(); // Refresh to update statistics
                }
            }
            lastRunPhase = phase;
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
            strictBrandMatch: localStorage.getItem('strictBrandMatch') !== 'false',
            aiEnhance: localStorage.getItem('aiEnhance') === 'true',
            skipCache: localStorage.getItem('skipCache') === 'true',
            target_width: parseInt(localStorage.getItem('target_width')) || 0,
            target_height: parseInt(localStorage.getItem('target_height')) || 0,
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

    // إيقاف التشغيل بأمان: لا يُحذف أي صف (ApiController::stopBatch ثم run_control stop)
    const STOP_CONFIRM_TEXT = "إيقاف التشغيل:\n" +
        "• يتوقف العامل الآن، وإن كان التشغيل ما زال يقرأ الشيت فيتوقف قبل معالجة أي منتج.\n" +
        "• تعود الصفوف التي كانت قيد المعالجة إلى الانتظار لتُعالج في التشغيل القادم.\n" +
        "• لا يُحذف أي صف: المنتجات الجاهزة للمراجعة والمعتمدة والفاشلة تبقى كما هي.\n\n" +
        "هل تريد الإيقاف؟";

    async function stopBatchAutomation() {
        if (!confirm(STOP_CONFIRM_TEXT)) {
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
                alert("🛑 " + (data.message || "تم إيقاف التشغيل، ولم يُحذف أي صف."));
                pollBatchStatus();
            } else {
                alert("❌ فشل إيقاف التشغيل: " + (data.error || 'خطأ غير معروف'));
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fas fa-stop"></i> إيقاف التشغيل 🛑';
                }
            }
        } catch (err) {
            console.error("Error stopping batch:", err);
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = '<i class="fas fa-stop"></i> إيقاف التشغيل 🛑';
            }
        }
    }

    // جلب حالة جسر بايثون وقاعدة البيانات
    async function pollSystemStatus() {
        try {
            const res = await fetch('/api/system/status');
            const data = await res.json();
            const bridgeEl = document.getElementById('status-bridge');
            const dbEl = document.getElementById('status-db');
            if (bridgeEl) {
                const ok = data.python_bridge === 'present' && data.python_path !== 'missing';
                bridgeEl.textContent = ok ? 'جاهز (CLI)' : 'غير موجود';
                bridgeEl.style.background = ok ? 'var(--success-bg)' : 'var(--danger-bg)';
                bridgeEl.style.color = ok ? 'var(--success)' : 'var(--danger)';
            }
            if (dbEl) {
                const online = data.database === 'online';
                dbEl.textContent = online ? 'متصلة' : 'غير متصلة';
                dbEl.style.color = online ? 'var(--success)' : 'var(--danger)';
            }
        } catch (err) {
            console.error("Error polling system status:", err);
        }
    }

    // تهيئة رسم التليمتري البياني لسرعة الطوابير والتوكنز
    let queueTelemetryChart = null;

    function initTelemetryChart() {
        // Chart.js يأتي من CDN: بدون إنترنت لا يجب أن يوقف باقي تهيئة الصفحة (مثل تحديث السجلات)
        if (typeof Chart === 'undefined') return;
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
                    label: 'منتجات تمت معالجتها في الجلسة',
                    data: [],
                    borderColor: 'rgb(255, 99, 132)',
                    backgroundColor: 'rgba(255, 99, 132, 0.05)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.3
                }, {
                    label: 'جاهزة للمراجعة',
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
            lastDrawerLogCount = -1;
            pollDrawerLogs();
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

    // سجل التشغيل الحقيقي (temp/pipeline.log) عبر /api/logs
    let lastDrawerLogCount = -1;
    async function pollDrawerLogs() {
        const drawer = document.getElementById('logDrawer');
        if (!drawer || drawer.style.left !== '0px') return;
        const sseDot = document.getElementById('sse-status-dot');
        const sseText = document.getElementById('sse-status-text');
        try {
            const res = await fetch('/api/logs');
            const data = await res.json();
            const logs = Array.isArray(data.logs) ? data.logs : [];
            if (sseDot) sseDot.className = 'status-dot';
            if (sseText) {
                sseText.innerText = 'متصل (تحديث كل 3 ث)';
                sseText.style.color = 'var(--success)';
            }
            if (logs.length === lastDrawerLogCount) return;
            lastDrawerLogCount = logs.length;
            const logContainer = document.getElementById('sse-log-container');
            logContainer.textContent = '';
            rawLogText = logs.join("\n");
            logs.forEach(line => {
                const row = document.createElement('span');
                row.style.display = 'block';
                row.style.borderBottom = '1px solid rgba(255,255,255,0.02)';
                row.style.paddingBottom = '4px';
                const lower = String(line).toLowerCase();
                let severity = 'info';
                if (lower.includes('error') || lower.includes('fail') || line.includes('❌')) {
                    severity = 'error';
                    row.style.color = 'var(--danger)';
                } else if (lower.includes('warn') || line.includes('⚠')) {
                    severity = 'warning';
                    row.style.color = 'var(--warning)';
                } else {
                    row.style.color = 'var(--text-primary)';
                }
                row.dataset.severity = severity;
                row.textContent = line;
                logContainer.appendChild(row);
            });
            filterDrawerLogs();
            logContainer.scrollTop = logContainer.scrollHeight;
        } catch (err) {
            if (sseDot) sseDot.className = 'status-dot danger';
            if (sseText) {
                sseText.innerText = 'تعذر القراءة';
                sseText.style.color = 'var(--danger)';
            }
        }
    }

    // Poll batch status and system status on load
    window.addEventListener('load', () => {
        pollBatchStatus();
        setInterval(pollBatchStatus, 3000);
        
        pollSystemStatus();
        setInterval(pollSystemStatus, 5000);

        // تهيئة رسم التقدم وسجل التشغيل
        initTelemetryChart();
        setInterval(pollDrawerLogs, 3000);
    });
</script>
@endsection

