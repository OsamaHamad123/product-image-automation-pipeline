@extends('layouts.layout')

@section('title', '🧠 التعلم النشط: دقة الاختيار المسبق من قرارات المراجعين')
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

    .kpi-sub {
        font-size: 0.75rem;
        color: var(--text-secondary);
        margin-top: 0.25rem;
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

    .status-badge {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 4px 10px;
        border-radius: 30px;
        font-size: 0.75rem;
        font-weight: 800;
        border: 1px solid var(--panel-border);
        white-space: nowrap;
    }

    .status-badge.ready {
        background: var(--success-bg);
        color: var(--success);
    }

    .status-badge.needs_reviews {
        background: var(--info-bg);
        color: var(--info);
    }

    .status-badge.low_precision {
        background: var(--danger-bg);
        color: var(--danger);
    }

    .suggested-line {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        flex-wrap: wrap;
        direction: ltr;
        justify-content: flex-end;
    }

    .suggested-line code {
        font-family: 'Outfit', monospace;
        font-size: 0.9rem;
        padding: 0.6rem 1rem;
        border-radius: 10px;
        background: var(--input-bg);
        border: 1px solid var(--panel-border);
        color: var(--text-primary);
        word-break: break-all;
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

    .table-view td.num {
        font-family: 'Outfit', sans-serif;
        font-weight: 700;
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
@php
    $overall = $reviewStats['overall'] ?? null;
    $thresholds = $reviewStats['thresholds'] ?? [];
    $brandRows = $reviewStats['brands'] ?? [];
    $domainRows = $reviewStats['domains'] ?? [];
    $readyBrands = $reviewStats['ready_brands'] ?? [];
    $suggestedBrands = $reviewStats['suggested_auto_publish_brands'] ?? '';
    $pct = fn ($value) => $value === null ? '—' : number_format(100 * (float) $value, 1) . '%';
@endphp

<!-- Page Header -->
<div style="margin-bottom: 2rem; direction: rtl;">
    <h2 style="font-size: 1.75rem; font-weight: 900; margin-bottom: 0.5rem; background: var(--accent-gradient); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">
        🧠 التعلم من قرارات المراجعين
    </h2>
    <p style="color: var(--text-secondary); font-size: 0.9rem; font-weight: 500;">
        كل اعتماد أو رفض أو رفع يدوي يُسجل مع ما عرضه المحرك على المراجع. تقيس هذه الصفحة كم مرة كانت الصورة التي اختارها المحرك مسبقاً هي الصورة التي اعتمدها المراجع؛ هذا هو الدليل المطلوب قبل فتح النشر الآلي لأي براند. الصفحة للقراءة فقط ولا تغير أي إعداد.
    </p>
</div>

@if(isset($error))
    <div class="glass-panel" style="background-color: var(--danger-bg); border-color: var(--danger); color: var(--danger); font-weight: bold; margin-bottom: 2rem; direction: rtl;">
        <i class="fas fa-exclamation-triangle" style="margin-inline-end: 0.5rem;"></i> {{ $error }}
    </div>
@endif

@if($overall !== null && (int) $overall['actions'] === 0)
    <!-- Empty state: no reviewer decisions yet -->
    <div class="glass-panel" style="direction: rtl; margin-bottom: 2rem; text-align: center; padding: 3rem 2rem;">
        <i class="fas fa-clipboard-check" style="font-size: 3rem; color: var(--text-secondary); margin-bottom: 1rem;"></i>
        <h3 style="font-size: 1.2rem; font-weight: 800; color: var(--text-primary);">لا توجد قرارات مراجعة مسجلة بعد</h3>
        <p style="font-size: 0.9rem; color: var(--text-secondary); margin-top: 0.5rem;">
            اعتمد الصور أو ارفضها من صفحة الكتالوج أو المراجعة الجماعية، وستظهر هنا دقة الاختيار المسبق لكل براند ونطاق.
            لا يُقترح أي براند للنشر الآلي قبل وجود مراجعات حقيقية كافية.
        </p>
    </div>
@elseif($overall !== null)
    <!-- KPI Stats Grid -->
    <div class="kpi-grid" style="direction: rtl;">
        <div class="kpi-card">
            <div class="kpi-icon"><i class="fas fa-history"></i></div>
            <div>
                <div class="kpi-value" id="kpi-review-actions">{{ $overall['actions'] }}</div>
                <div class="kpi-label">قرارات المراجعة</div>
                <div class="kpi-sub">اعتماد {{ $overall['approved'] }} · رفض {{ $overall['rejected'] }} · رفع يدوي {{ $overall['manual_upload'] }}</div>
            </div>
        </div>
        <div class="kpi-card">
            <div class="kpi-icon"><i class="fas fa-boxes"></i></div>
            <div>
                <div class="kpi-value" id="kpi-reviewed-skus">{{ $overall['reviewed_skus'] }}</div>
                <div class="kpi-label">منتجات مراجعة (SKU)</div>
            </div>
        </div>
        <div class="kpi-card">
            <div class="kpi-icon" style="color: var(--info); background: var(--info-bg);"><i class="fas fa-check-double"></i></div>
            <div>
                <div class="kpi-value" id="kpi-prechecked">{{ $overall['prechecked'] }}</div>
                <div class="kpi-label">اختيارات مسبقة مراجعة</div>
                <div class="kpi-sub">مقبولة: {{ $overall['accepted'] }}</div>
            </div>
        </div>
        <div class="kpi-card">
            <div class="kpi-icon" style="color: var(--success); background: var(--success-bg);"><i class="fas fa-bullseye"></i></div>
            <div>
                <div class="kpi-value" id="kpi-precision">{{ $pct($overall['precision']) }}</div>
                <div class="kpi-label">دقة الاختيار المسبق</div>
                <div class="kpi-sub">الحد الأدنى (ويلسون 95%): {{ $pct($overall['lower_bound']) }}</div>
            </div>
        </div>
        <div class="kpi-card">
            <div class="kpi-icon" style="color: var(--warning); background: var(--warning-bg);"><i class="fas fa-rocket"></i></div>
            <div>
                <div class="kpi-value" id="kpi-ready-brands">{{ count($readyBrands) }}</div>
                <div class="kpi-label">براندات جاهزة للنشر الآلي</div>
            </div>
        </div>
    </div>
@endif

@if($overall !== null && (int) $overall['actions'] > 0)
<div style="display: grid; grid-template-columns: 1fr; gap: 2rem; direction: rtl; margin-bottom: 2rem;">

    <!-- Section 1: Per-brand pre-check precision -->
    <div class="glass-panel">
        <h3 style="font-size: 1.15rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.75rem; margin-bottom: 1rem; display: flex; align-items: center; gap: 0.5rem;">
            <i class="fas fa-tags" style="color: var(--accent-cyan);"></i> دقة الاختيار المسبق لكل براند
        </h3>
        <p style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 1.25rem;">
            يجهز البراند للنشر الآلي عندما يبلغ عدد اختياراته المسبقة المراجعة {{ $thresholds['min_reviewed'] ?? '' }} على الأقل
            ويبلغ الحد الأدنى لفاصل ويلسون (95%) لدقتها {{ $pct($thresholds['min_lower_bound'] ?? null) }}.
            حتى مع قبول كل الاختيارات يلزم {{ $thresholds['perfect_record_reviews'] ?? '' }} مراجعة لبلوغ هذا الحد.
            الاختيار المسبق مقبول إذا اعتمد المراجع الصورة نفسها، ومرفوض إذا رفضها أو اعتمد صورة أخرى أو رفع صورة يدوياً.
        </p>
        <div style="overflow-x: auto;">
            <table class="table-view">
                <thead>
                    <tr>
                        <th>البراند</th>
                        <th>منتجات مراجعة</th>
                        <th>اختيارات مسبقة مراجعة</th>
                        <th>مقبولة</th>
                        <th>الدقة</th>
                        <th>الحد الأدنى (ويلسون 95%)</th>
                        <th>الحالة</th>
                        <th>أكثر أسباب الرفض</th>
                    </tr>
                </thead>
                <tbody>
                    @foreach($brandRows as $row)
                        <tr>
                            <td style="font-weight: 800; color: var(--text-primary);">{{ $row['brand'] !== '' ? $row['brand'] : '(بدون براند)' }}</td>
                            <td class="num">{{ $row['reviewed_skus'] }}</td>
                            <td class="num">{{ $row['prechecked'] }}</td>
                            <td class="num">{{ $row['accepted'] }}</td>
                            <td class="num">{{ $pct($row['precision']) }}</td>
                            <td class="num">{{ $pct($row['lower_bound']) }}</td>
                            <td>
                                @if($row['status'] === 'ready')
                                    <span class="status-badge ready"><i class="fas fa-check-circle"></i> جاهزة للنشر الآلي</span>
                                @elseif($row['status'] === 'low_precision')
                                    <span class="status-badge low_precision"><i class="fas fa-times-circle"></i> دقة أقل من المطلوب</span>
                                @else
                                    <span class="status-badge needs_reviews"><i class="fas fa-hourglass-half"></i> تحتاج مراجعات أكثر ({{ $row['prechecked'] }}/{{ $row['reviews_needed'] ?? '؟' }})</span>
                                @endif
                            </td>
                            <td>
                                @forelse($row['top_reject_reasons'] as $reason)
                                    <span class="badge-reason other">{{ $reason[0] }} × {{ $reason[1] }}</span>
                                @empty
                                    <span style="color: var(--text-secondary);">—</span>
                                @endforelse
                            </td>
                        </tr>
                    @endforeach
                </tbody>
            </table>
        </div>
    </div>

    <!-- Section 2: Suggested AUTO_PUBLISH_BRANDS -->
    <div class="glass-panel">
        <h3 style="font-size: 1.15rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.75rem; margin-bottom: 1rem; display: flex; align-items: center; gap: 0.5rem;">
            <i class="fas fa-rocket" style="color: var(--accent-purple);"></i> قيمة AUTO_PUBLISH_BRANDS المقترحة
        </h3>
        <div class="suggested-line">
            <code id="suggestedBrandsLine">AUTO_PUBLISH_BRANDS=<span id="suggestedBrandsValue">{{ $suggestedBrands }}</span></code>
            @if(count($readyBrands) > 0)
                <button type="button" class="btn btn-secondary btn-sm" onclick="copySuggestedBrands()">
                    <i class="fas fa-copy"></i> نسخ القيمة
                </button>
            @endif
        </div>
        <p style="font-size: 0.85rem; color: var(--text-secondary); margin-top: 1rem;">
            @if(count($readyBrands) > 0)
                انسخ القيمة إلى حقل "البراندات المسموح نشرها تلقائياً" في صفحة الإعدادات ثم فعّل النشر التلقائي هناك. هذه الصفحة لا تغير الإعدادات.
            @else
                لا يوجد براند جاهز بعد: اترك حقل "البراندات المسموح نشرها تلقائياً" في صفحة الإعدادات فارغاً حتى تكتمل المراجعات.
            @endif
        </p>
    </div>

    <!-- Section 3: Per-domain approvals and rejections -->
    <div class="glass-panel">
        <h3 style="font-size: 1.15rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.75rem; margin-bottom: 1.25rem; display: flex; align-items: center; gap: 0.5rem;">
            <i class="fas fa-globe" style="color: var(--accent-cyan);"></i> الاعتمادات والرفض حسب نطاق الصفحة
        </h3>
        @if(count($domainRows) === 0)
            <p style="text-align: center; color: var(--text-secondary); padding: 2rem 0; font-weight: bold;">
                لا توجد قرارات مرتبطة بنطاق صفحة معروف بعد (الرفع اليدوي لا يحمل نطاقاً).
            </p>
        @else
            <div style="overflow-x: auto;">
                <table class="table-view">
                    <thead>
                        <tr>
                            <th>النطاق</th>
                            <th>اعتمادات</th>
                            <th>رفض</th>
                        </tr>
                    </thead>
                    <tbody>
                        @foreach($domainRows as $row)
                            <tr>
                                <td style="font-weight: 700; direction: ltr; text-align: right;">{{ $row['domain'] }}</td>
                                <td class="num" style="color: var(--success);">{{ $row['approved'] }}</td>
                                <td class="num" style="color: var(--danger);">{{ $row['rejected'] }}</td>
                            </tr>
                        @endforeach
                    </tbody>
                </table>
            </div>
        @endif
    </div>
</div>
@endif

<!-- Recent Rejections Feed -->
<div class="glass-panel" style="direction: rtl;">
    <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.75rem; margin-bottom: 1.25rem; gap: 1rem;">
        <h3 style="font-size: 1.15rem; font-weight: 800; display: flex; align-items: center; gap: 0.5rem; margin: 0;">
            <i class="fas fa-history" style="color: var(--accent-purple);"></i> سجل الصور المرفوضة مؤخراً ({{ count($feedbackLogs) }})
        </h3>
        @if(count($feedbackLogs) > 0)
            <button class="btn btn-secondary btn-sm" onclick="resetAllLearning()" style="background: var(--danger-bg); border-color: var(--panel-border); color: var(--danger); font-weight: bold;">
                <i class="fas fa-trash-alt"></i> مسح سجل الرفض
            </button>
        @endif
    </div>

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
@endsection

@section('scripts')
<script>
    async function copySuggestedBrands() {
        const value = document.getElementById('suggestedBrandsValue')?.textContent.trim() || '';
        if (!value) return;
        try {
            await navigator.clipboard.writeText(value);
            alert('تم نسخ القيمة: ' + value);
        } catch (err) {
            console.error(err);
            prompt('انسخ القيمة يدوياً:', value);
        }
    }

    async function resetAllLearning() {
        if (!confirm("سيُحذف سجل الصور المرفوضة المعروض في هذا القسم فقط. قرارات المراجعة التي تُحسب منها الدقة واستبعادات البحث لكل منتج لا تتأثر. هل تريد المتابعة؟")) {
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
                alert("تم مسح سجل الرفض.");
                location.reload();
            } else {
                alert("❌ فشل مسح السجل: " + data.error);
            }
        } catch (err) {
            console.error(err);
        }
    }
</script>
@endsection
