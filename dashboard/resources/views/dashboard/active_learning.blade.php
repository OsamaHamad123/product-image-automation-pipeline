@extends('layouts.layout')

@section('title', '🧠 لوحة التحكم بالتعلم النشط والتصحيح الذاتي')
@section('nav_active_learning', 'active')

@section('styles')
<style>
    .kpi-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 1.5rem;
        margin-bottom: 2.5rem;
    }
    
    .kpi-card {
        background: var(--card-bg);
        border: 1px solid var(--panel-border);
        border-radius: var(--border-radius-md);
        padding: 1.5rem;
        display: flex;
        align-items: center;
        gap: 1.25rem;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        position: relative;
        overflow: hidden;
    }
    
    .kpi-card:hover {
        transform: translateY(-5px);
        border-color: var(--accent-purple);
        box-shadow: 0 10px 25px var(--btn-shadow);
    }
    
    .kpi-icon {
        width: 54px;
        height: 54px;
        border-radius: 12px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1.5rem;
        background: var(--active-menu-bg);
        color: var(--accent-purple);
        transition: all 0.3s;
    }
    
    .kpi-card:hover .kpi-icon {
        background: var(--accent-gradient);
        color: #fff;
        transform: scale(1.1) rotate(5deg);
    }
    
    .kpi-value {
        font-size: 1.8rem;
        font-weight: 800;
        line-height: 1;
        margin-bottom: 0.25rem;
        font-family: 'Outfit', sans-serif;
    }
    
    .kpi-label {
        font-size: 0.85rem;
        color: var(--text-secondary);
        font-weight: 700;
    }

    .badge-reason {
        display: inline-block;
        padding: 3px 8px;
        border-radius: 6px;
        font-size: 0.75rem;
        font-weight: 800;
        margin: 2px;
    }
    
    .badge-reason.cropping {
        background: var(--danger-bg);
        color: var(--danger);
        border: 1px solid var(--panel-border);
    }
    
    .badge-reason.clutter {
        background: var(--warning-bg);
        color: var(--warning);
        border: 1px solid var(--panel-border);
    }
    
    .badge-reason.other {
        background: var(--info-bg);
        color: var(--info);
        border: 1px solid var(--panel-border);
    }

    .learned-rule-badge {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 4px 10px;
        border-radius: 30px;
        font-size: 0.75rem;
        font-weight: 800;
        border: 1px solid transparent;
    }
    
    .learned-rule-badge.alert-active {
        background: var(--success-bg);
        color: var(--success);
        border-color: var(--panel-border);
        animation: pulse-border 2s infinite;
    }
    
    .learned-rule-badge.alert-inactive {
        background: rgba(255, 255, 255, 0.03);
        color: var(--text-secondary);
        border-color: var(--panel-border);
    }
    
    @keyframes pulse-border {
        0%, 100% { border-color: var(--panel-border); }
        50% { border-color: var(--panel-border-hover); }
    }

    .table-view {
        width: 100%;
        border-collapse: collapse;
        text-align: right;
    }
    
    .table-view th {
        padding: 1rem 1.25rem;
        color: var(--text-secondary);
        font-size: 0.85rem;
        font-weight: 800;
        border-bottom: 1px solid var(--panel-border);
        text-transform: uppercase;
    }
    
    .table-view td {
        padding: 1rem 1.25rem;
        border-bottom: 1px solid rgba(255, 255, 255, 0.03);
        font-size: 0.9rem;
        color: var(--text-primary);
        font-weight: 500;
    }
    
    .table-view tr:hover td {
        background: rgba(255, 255, 255, 0.01);
    }
    
    .feedback-image-preview {
        width: 42px;
        height: 42px;
        border-radius: 8px;
        object-fit: cover;
        cursor: pointer;
        border: 1px solid var(--panel-border);
        transition: transform 0.2s;
    }
    
    .feedback-image-preview:hover {
        transform: scale(1.15);
        border-color: var(--accent-purple);
    }
</style>
@endsection

@section('content')
<!-- Page Header -->
<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2rem; direction: rtl;">
    <div>
        <h2 style="font-size: 1.75rem; font-weight: 900; margin-bottom: 0.5rem; background: var(--accent-gradient); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">
            🧠 لوحة تحكم التعلم النشط والتصحيح الذاتي
        </h2>
        <p style="color: var(--text-secondary); font-size: 0.9rem; font-weight: 500;">
            يقوم النظام بتحليل استبعادك للصور واستخراج الأنماط المتكررة للبراندات تلقائياً لتفادي الأخطاء المستقبلية.
        </p>
    </div>
    <button class="btn btn-secondary btn-sm" onclick="resetAllLearning()" style="background: var(--danger-bg); border-color: var(--panel-border); color: var(--danger); font-weight: bold;">
        <i class="fas fa-trash-alt"></i> إعادة تعيين ومسح الذاكرة بالكامل 🗑️
    </button>
</div>

<!-- KPI Stats Grid -->
<div class="kpi-grid" style="direction: rtl;">
    <!-- Card 1 -->
    <div class="kpi-card">
        <div class="kpi-icon"><i class="fas fa-history"></i></div>
        <div>
            <div class="kpi-value" id="kpi-total-rejections">{{ count($feedbackLogs) }}</div>
            <div class="kpi-label">إجمالي الصور المستبعدة</div>
        </div>
    </div>
    <!-- Card 2 -->
    <div class="kpi-card">
        <div class="kpi-icon" style="color: var(--success); background: var(--success-bg);"><i class="fas fa-shield-alt"></i></div>
        <div>
            <div class="kpi-value" id="kpi-self-correcting">{{ count(array_filter($brandStats, function($b) { return $b['cropping_alert'] || $b['clutter_alert']; })) }}</div>
            <div class="kpi-label">البراندات ذاتية التصحيح</div>
        </div>
    </div>
    <!-- Card 3 -->
    <div class="kpi-card">
        <div class="kpi-icon" style="color: var(--danger); background: var(--danger-bg);"><i class="fas fa-crop-alt"></i></div>
        <div>
            <div class="kpi-value" id="kpi-crop-rules">{{ count(array_filter($brandStats, function($b) { return $b['cropping_alert']; })) }}</div>
            <div class="kpi-label">قواعد قص الحواف المفعلة</div>
        </div>
    </div>
    <!-- Card 4 -->
    <div class="kpi-card">
        <div class="kpi-icon" style="color: var(--warning); background: var(--warning-bg);"><i class="fas fa-images"></i></div>
        <div>
            <div class="kpi-value" id="kpi-clutter-rules">{{ count(array_filter($brandStats, function($b) { return $b['clutter_alert']; })) }}</div>
            <div class="kpi-label">قواعد فرز الخلفيات النشطة</div>
        </div>
    </div>
</div>

<!-- Brand Issues Analysis Chart -->
<div class="glass-panel" style="direction: rtl; margin-bottom: 2rem;">
    <h3 style="font-size: 1.1rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.75rem; margin-bottom: 1.25rem; display: flex; align-items: center; gap: 0.5rem;">
        <i class="fas fa-chart-bar" style="color: var(--accent-purple-hover);"></i> توزيع مشكلات جودة الصور المتكررة حسب البراند (Visual Analytics)
    </h3>
    <div style="position: relative; height: 260px; max-width: 100%;">
        <canvas id="activeLearningChart"></canvas>
    </div>
</div>

<!-- Main Split Layout -->
<div style="display: grid; grid-template-columns: 1fr; gap: 2rem; direction: rtl;">
    
    <!-- Section 1: Self-Correcting Rules Grid -->
    <div class="glass-panel">
        <h3 style="font-size: 1.15rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.75rem; margin-bottom: 1.25rem; display: flex; align-items: center; gap: 0.5rem;">
            <i class="fas fa-cogs" style="color: var(--accent-cyan);"></i> قواعد المواءمة الذاتية للبراندات (Brand Overrides)
        </h3>
        
        @if(count($brandStats) == 0)
            <p style="text-align: center; color: var(--text-secondary); padding: 3rem 0; font-weight: bold;">
                🎉 لا توجد قواعد تعلم نشط مفعلة حالياً. يحتاج النظام لمزيد من التغذية الراجعة من عمليات الرفض.
            </p>
        @else
            <div style="overflow-x: auto;">
                <table class="table-view">
                    <thead>
                        <tr>
                            <th>البراند</th>
                            <th>عدد الاستبعادات</th>
                            <th>هامش الأمان المكتشف (Padding)</th>
                            <th>فحص تداخل الخلفية (Gemini)</th>
                            <th>حالة الذكاء الاصطناعي</th>
                            <th>الإجراءات</th>
                        </tr>
                    </thead>
                    <tbody>
                        @foreach($brandStats as $key => $brand)
                            <tr id="brand-row-{{ $key }}">
                                <td style="font-weight: 800; color: var(--text-primary);">{{ $brand['brand'] }}</td>
                                <td style="font-family: 'Outfit', sans-serif; font-weight: 700;">{{ $brand['total'] }} مرات استبعاد</td>
                                <td>
                                    <span class="learned-rule-badge {{ $brand['cropping_alert'] ? 'alert-active' : 'alert-inactive' }}">
                                        <i class="fas fa-crop-alt"></i> {{ $brand['padding_ratio'] }}
                                    </span>
                                </td>
                                <td>
                                    <span class="learned-rule-badge {{ $brand['clutter_alert'] ? 'alert-active' : 'alert-inactive' }}">
                                        <i class="fas fa-images"></i> {{ $brand['clutter_check'] }}
                                    </span>
                                </td>
                                <td>
                                    @if($brand['cropping_alert'] || $brand['clutter_alert'])
                                        <span style="color: var(--success); font-weight: 800; font-size: 0.8rem; display: flex; align-items: center; gap: 0.25rem;">
                                            <i class="fas fa-check-circle"></i> يتعلم ويصحح تلقائياً
                                        </span>
                                    @else
                                        <span style="color: var(--text-secondary); font-size: 0.8rem;">
                                            في مرحلة جمع العينات...
                                        </span>
                                    @endif
                                </td>
                                <td>
                                    <button class="btn btn-secondary btn-sm" onclick="resetBrandLearning('{{ $brand['brand'] }}', '{{ $key }}')" style="padding: 2px 8px; font-size: 0.75rem; color: var(--danger); background: var(--danger-bg);">
                                        <i class="fas fa-undo"></i> تصفير
                                    </button>
                                </td>
                            </tr>
                        @endforeach
                    </tbody>
                </table>
            </div>
        @endif
    </div>

    <!-- Section 2: Recent Rejections Feed -->
    <div class="glass-panel">
        <h3 style="font-size: 1.15rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.75rem; margin-bottom: 1.25rem; display: flex; align-items: center; gap: 0.5rem;">
            <i class="fas fa-history" style="color: var(--accent-purple);"></i> سجل عينات التغذية الراجعة المستبعدة مؤخراً
        </h3>
        
        @if(count($feedbackLogs) == 0)
            <p style="text-align: center; color: var(--text-secondary); padding: 3rem 0; font-weight: bold;">
                لا توجد سجلات استبعاد مراجعة حالياً.
            </p>
        @else
            <div style="overflow-x: auto;">
                <table class="table-view">
                    <thead>
                        <tr>
                            <th>الصورة</th>
                            <th>اسم المنتج</th>
                            <th>البراند</th>
                            <th>أسباب الاستبعاد المحددة</th>
                            <th>التاريخ والوقت</th>
                        </tr>
                    </thead>
                    <tbody>
                        @foreach($feedbackLogs as $log)
                            <tr>
                                <td>
                                    @if($log->image_url)
                                        <a href="{{ $log->image_url }}" target="_blank" title="افتح الرابط في تبويب جديد">
                                            <img src="/api/image-proxy?url={{ urlencode($log->image_url) }}" alt="Preview" class="feedback-image-preview" onerror="this.src='https://placehold.co/80x80?text=No+Img'">
                                        </a>
                                    @else
                                        <span style="color: var(--text-secondary);">لا يوجد</span>
                                    @endif
                                </td>
                                <td style="font-weight: 700; max-width: 320px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="{{ $log->product_name }}">
                                    {{ $log->product_name }}
                                </td>
                                <td style="font-weight: bold; color: var(--accent-cyan);">{{ $log->brand }}</td>
                                <td>
                                    @php
                                        $reasons = [];
                                        try {
                                            $reasons = json_decode($log->rejection_reasons, true) ?: [];
                                        } catch (\Exception $ex) {}
                                    @endphp
                                    @foreach($reasons as $reason)
                                        @php
                                            $reasonLower = strtolower($reason);
                                            $badgeClass = 'other';
                                            $reasonAr = $reason;
                                            
                                            if (strpos($reasonLower, 'cropping') !== false || strpos($reasonLower, 'margins') !== false) {
                                                $badgeClass = 'cropping';
                                                $reasonAr = 'قص جائر للأطراف ✂️';
                                            } elseif (strpos($reasonLower, 'clutter') !== false || strpos($reasonLower, 'background') !== false) {
                                                $badgeClass = 'clutter';
                                                $reasonAr = 'تداخل الخلفية 🖼️';
                                            } elseif (strpos($reasonLower, 'brand') !== false) {
                                                $badgeClass = 'cropping';
                                                $reasonAr = 'البراند خاطئ 🏷️';
                                            } elseif (strpos($reasonLower, 'resolution') !== false) {
                                                $badgeClass = 'other';
                                                $reasonAr = 'دقة منخفضة 📷';
                                            }
                                        @endphp
                                        <span class="badge-reason {{ $badgeClass }}">{{ $reasonAr }}</span>
                                    @endforeach
                                </td>
                                <td style="font-family: 'Outfit', sans-serif; font-size: 0.8rem; color: var(--text-secondary); direction: ltr; text-align: left;">
                                    {{ $log->timestamp }}
                                </td>
                            </tr>
                        @endforeach
                    </tbody>
                </table>
            </div>
        @endif
    </div>

</div>
@endsection

@section('scripts')
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
    // تهيئة الرسم البياني لتوزيع المشاكل البصرية
    window.addEventListener('load', () => {
        const brandStats = @json($brandStats);
        const labels = Object.values(brandStats).map(b => b.brand);
        const croppingData = Object.values(brandStats).map(b => b.cropping);
        const clutterData = Object.values(brandStats).map(b => b.clutter);

        if (labels.length === 0) {
            const chartPanel = document.getElementById('activeLearningChart')?.closest('.glass-panel');
            if (chartPanel) chartPanel.style.display = 'none';
            return;
        }

        const ctx = document.getElementById('activeLearningChart').getContext('2d');
        const isLight = document.body.classList.contains('light-theme');
        const gridColor = isLight ? 'rgba(0, 0, 0, 0.08)' : 'rgba(255, 255, 255, 0.05)';
        const textColor = isLight ? '#475569' : '#8e9bb0';
        const legendColor = isLight ? '#0f172a' : '#f3f4f6';
        const cropColor = isLight ? 'rgba(0, 0, 0, 0.65)' : 'rgba(255, 255, 255, 0.65)';
        const clutterColor = isLight ? 'rgba(0, 0, 0, 0.25)' : 'rgba(255, 255, 255, 0.25)';

        const chart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'قص جائر للأطراف (Cropping)',
                        data: croppingData,
                        backgroundColor: cropColor,
                        borderColor: 'var(--danger)',
                        borderWidth: 1.5,
                        borderRadius: 6
                    },
                    {
                        label: 'تداخل الخلفية (Background Clutter)',
                        data: clutterData,
                        backgroundColor: clutterColor,
                        borderColor: 'var(--warning)',
                        borderWidth: 1.5,
                        borderRadius: 6
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        labels: {
                            color: legendColor,
                            font: { family: 'Tajawal', weight: 'bold', size: 11 }
                        }
                    }
                },
                scales: {
                    x: {
                        ticks: { color: textColor, font: { family: 'Tajawal', size: 10 } },
                        grid: { color: gridColor }
                    },
                    y: {
                        ticks: { color: textColor, font: { family: 'Outfit', size: 10 }, stepSize: 1 },
                        grid: { color: gridColor }
                    }
                }
            }
        });

        document.querySelector('.theme-toggle-btn')?.addEventListener('click', () => {
            setTimeout(() => {
                const activeLight = document.body.classList.contains('light-theme');
                chart.options.scales.x.grid.color = activeLight ? 'rgba(0, 0, 0, 0.08)' : 'rgba(255, 255, 255, 0.05)';
                chart.options.scales.x.ticks.color = activeLight ? '#475569' : '#8e9bb0';
                chart.options.scales.y.grid.color = activeLight ? 'rgba(0, 0, 0, 0.08)' : 'rgba(255, 255, 255, 0.05)';
                chart.options.scales.y.ticks.color = activeLight ? '#475569' : '#8e9bb0';
                chart.options.plugins.legend.labels.color = activeLight ? '#0f172a' : '#f3f4f6';
                chart.data.datasets[0].backgroundColor = activeLight ? 'rgba(0, 0, 0, 0.65)' : 'rgba(255, 255, 255, 0.65)';
                chart.data.datasets[1].backgroundColor = activeLight ? 'rgba(0, 0, 0, 0.25)' : 'rgba(255, 255, 255, 0.25)';
                chart.update();
            }, 100);
        });
    });

    async function resetBrandLearning(brandName, key) {
        if (!confirm(`هل أنت متأكد من تصفير ذاكرة التعلم وإعادة إعدادات المعالجة الافتراضية للبراند: ${brandName}؟`)) {
            return;
        }
        
        try {
            const res = await fetch('/api/active-learning/reset', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content
                },
                body: JSON.stringify({ brand: brandName })
            });
            const data = await res.json();
            if (data.status === 'success') {
                alert(`🏁 تم مسح التغذية الراجعة للبراند ${brandName} بنجاح!`);
                const row = document.getElementById(`brand-row-${key}`);
                if (row) {
                    row.style.transition = 'all 0.4s ease';
                    row.style.opacity = '0';
                    setTimeout(() => { row.remove(); location.reload(); }, 400);
                } else {
                    location.reload();
                }
            } else {
                alert("❌ فشل تصفير بيانات البراند: " + data.error);
            }
        } catch (err) {
            console.error(err);
        }
    }

    async function resetAllLearning() {
        if (!confirm("🚨 تحذير: هل أنت متأكد تماماً من رغبتك في مسح ذاكرة الأتمتة بالكامل وتصفير جميع قواعد التصحيح الذاتي؟")) {
            return;
        }
        
        try {
            const res = await fetch('/api/active-learning/reset', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content
                }
            });
            const data = await res.json();
            if (data.status === 'success') {
                alert("🏁 تم تصفير وإعادة تعيين ذاكرة التعلم النشط بالكامل بنجاح!");
                location.reload();
            } else {
                alert("❌ فشل مسح الذاكرة: " + data.error);
            }
        } catch (err) {
            console.error(err);
        }
    }
</script>
@endsection
