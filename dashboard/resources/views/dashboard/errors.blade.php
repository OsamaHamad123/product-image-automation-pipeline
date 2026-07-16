@extends('layouts.layout')

@section('title', '⚠️ سجل الأخطاء والتحذيرات')
@section('nav_errors', 'active')

@section('styles')
<style>
    .errors-container {
        direction: rtl;
        text-align: right;
    }

    .error-table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 1.5rem;
    }

    .error-table th, .error-table td {
        padding: 1rem;
        border-bottom: 1px solid var(--panel-border);
        text-align: right;
    }

    .error-table th {
        color: var(--text-secondary);
        font-weight: 800;
        font-size: 0.9rem;
    }

    .error-table tbody tr {
        transition: background 0.25s ease;
    }

    .error-table tbody tr:hover {
        background: rgba(255, 255, 255, 0.02);
    }

    .error-message-cell {
        max-width: 350px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        cursor: pointer;
        color: var(--danger);
        font-weight: 600;
        text-decoration: underline;
    }

    .error-message-cell:hover {
        color: #ff788f;
    }

    .search-row {
        display: flex;
        gap: 1rem;
        align-items: center;
        margin-bottom: 1.5rem;
    }

    .search-row input {
        flex: 1;
        padding: 0.75rem 1.25rem;
        background: var(--input-bg);
        border: 1px solid var(--panel-border);
        border-radius: 12px;
        color: var(--text-primary);
        font-family: inherit;
        outline: none;
        transition: all 0.3s;
    }

    .search-row input:focus {
        border-color: var(--accent-purple);
        box-shadow: 0 0 15px rgba(124, 58, 237, 0.2);
    }

    .badge-error-row {
        background: rgba(244, 63, 94, 0.1);
        color: var(--danger);
        border: 1px solid rgba(244, 63, 94, 0.2);
        padding: 0.2rem 0.6rem;
        font-size: 0.75rem;
        font-weight: 800;
        border-radius: 6px;
    }

    .modal-body-content {
        background: var(--console-bg);
        border: 1px solid var(--panel-border);
        color: #ef4444;
        font-family: monospace;
        padding: 1.5rem;
        border-radius: 12px;
        white-space: pre-wrap;
        text-align: left;
        direction: ltr;
        overflow-x: auto;
        max-height: 300px;
    }
</style>
@endsection

@section('content')
<div class="errors-container">
    
    <!-- Header -->
    <div class="glass-panel" style="padding: 1.5rem 2rem; display: flex; justify-content: space-between; align-items: center;">
        <div>
            <h2 style="font-size: 1.4rem; font-weight: 900; margin: 0; color: var(--text-primary); display: flex; align-items: center; gap: 0.65rem;">
                <i class="fas fa-exclamation-triangle" style="color: var(--danger);"></i> سجل الأخطاء التقنية المكتشفة بالخلفية
            </h2>
            <p style="font-size: 0.85rem; color: var(--text-secondary); margin-top: 0.25rem;">
                المنتجات التي تعذر معالجتها بسبب انقطاع الشبكة، أخطاء الـ APIs، أو شروط الجودة الفنية.
            </p>
        </div>
        <span class="score-badge" style="background: rgba(244, 63, 94, 0.15); border-color: rgba(244, 63, 94, 0.25); color: var(--danger); font-size: 1rem; padding: 0.5rem 1.25rem; border-radius: 12px; font-weight: 900;">
            إجمالي الأخطاء: {{ count($failures) }}
        </span>
    </div>

    <!-- Main Errors panel -->
    <div class="glass-panel">
        
        <!-- Search and Action tools -->
        <div class="search-row">
            <input type="text" id="errorSearchInput" onkeyup="filterErrorTable()" placeholder="🔍 ابحث عن الخطأ بالاسم، البراند، الباركود أو رسالة الفشل...">
            <button type="button" class="btn" id="bulkRetryBtn" onclick="retrySelectedFailures()" style="background: linear-gradient(135deg, var(--accent-purple) 0%, var(--accent-cyan) 100%); font-weight: 800;">
                <i class="fas fa-redo-alt"></i> إعادة معالجة المنتجات المحددة 🔄
            </button>
        </div>

        @if(isset($error))
            <div style="background: rgba(244, 63, 94, 0.1); border: 1px solid var(--danger); color: var(--danger); border-radius: 12px; padding: 1rem; margin-bottom: 1.5rem; font-weight: bold;">
                {{ $error }}
            </div>
        @endif

        @if(count($failures) === 0)
            <div style="text-align: center; padding: 4rem 2rem; color: var(--text-secondary);">
                <i class="fas fa-check-circle" style="font-size: 4rem; color: var(--success); margin-bottom: 1.5rem; filter: drop-shadow(0 0 10px rgba(16,185,129,0.3));"></i>
                <h3 style="font-size: 1.3rem; font-weight: 800; color: var(--text-primary);">السجل نظيف تماماً! 🎉</h3>
                <p style="font-size: 0.9rem; margin-top: 0.25rem;">لم يتم رصد أي أخطاء تقنية أو فشل في معالجة الصور حالياً.</p>
            </div>
        @else
            <div style="overflow-x: auto;">
                <table class="error-table" id="errorTable">
                    <thead>
                        <tr>
                            <th style="width: 40px; text-align: center;">
                                <input type="checkbox" id="selectAllCheckbox" onchange="toggleSelectAllErrors(this)" style="width: 18px; height: 18px; cursor: pointer;">
                            </th>
                            <th style="width: 100px;">الباركود / المعرف</th>
                            <th>اسم المنتج</th>
                            <th>البراند</th>
                            <th>رسالة الخطأ والسبب</th>
                            <th style="width: 180px;">وقت الفشل</th>
                            <th style="width: 100px; text-align: center;">الإجراء</th>
                        </tr>
                    </thead>
                    <tbody>
                        @foreach($failures as $f)
                            <tr id="row-{{ $f->barcode }}">
                                <td style="text-align: center;">
                                    <input type="checkbox" class="error-select-checkbox" value="{{ $f->barcode }}" style="width: 18px; height: 18px; cursor: pointer;">
                                </td>
                                <td>
                                    <span class="badge-error-row" style="font-family: monospace;">{{ $f->barcode }}</span>
                                </td>
                                <td style="font-weight: bold; color: var(--text-primary);">{{ $f->product_name }}</td>
                                <td>{{ $f->brand }}</td>
                                <td>
                                    <div class="error-message-cell" onclick="showErrorDetailsModal('{{ addslashes($f->error_message) }}', '{{ addslashes($f->product_name) }}')" title="اضغط للتفاصيل الكاملة">
                                        {{ $f->error_message }}
                                    </div>
                                </td>
                                <td style="color: var(--text-secondary); font-size: 0.85rem; font-family: 'Outfit', sans-serif;">
                                    {{ $f->failed_at->setTimezone('Asia/Riyadh')->format('Y-m-d H:i:s') }}
                                </td>
                                <td style="text-align: center;">
                                    <button type="button" class="btn btn-secondary btn-sm" onclick="retrySingleFailure('{{ $f->barcode }}', this)" style="padding: 0.35rem 0.65rem; font-size: 0.75rem; border-radius: 8px;">
                                        <i class="fas fa-sync-alt"></i> أعد المحاولة
                                    </button>
                                </td>
                            </tr>
                        @endforeach
                    </tbody>
                </table>
            </div>
        @endif
    </div>
</div>

<!-- Detailed Error Modal -->
<div id="errorDetailsModal" class="modal">
    <div class="glass-panel" style="max-width: 600px; width: 100%; border-radius: 20px; padding: 2rem; position: relative;">
        <h3 id="modalProductName" style="font-size: 1.25rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.75rem; margin-bottom: 1.25rem; display: flex; align-items: center; gap: 0.5rem; color: var(--danger);">
            <i class="fas fa-bug"></i> تفاصيل الخطأ البرمجي
        </h3>
        <p style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 0.75rem;">الرسالة التقنية كاملة المسترجعة من الكونسول:</p>
        <div id="modalErrorContent" class="modal-body-content"></div>
        <div style="display: flex; justify-content: flex-end; margin-top: 1.5rem; border-top: 1px solid var(--panel-border); padding-top: 1rem;">
            <button type="button" class="btn" onclick="closeErrorDetailsModal()" style="padding: 0.5rem 1.5rem; font-weight: bold;">إغلاق النافذة</button>
        </div>
    </div>
</div>

@endsection

@section('scripts')
<script>
    function filterErrorTable() {
        const query = document.getElementById('errorSearchInput').value.toLowerCase().trim();
        const rows = document.querySelectorAll('#errorTable tbody tr');
        
        rows.forEach(row => {
            const cells = Array.from(row.cells).map(c => c.innerText.toLowerCase());
            const textMatch = cells.some(txt => txt.includes(query));
            row.style.display = textMatch ? '' : 'none';
        });
    }

    function toggleSelectAllErrors(selectAllCb) {
        document.querySelectorAll('.error-select-checkbox').forEach(cb => {
            cb.checked = selectAllCb.checked;
        });
    }

    function showErrorDetailsModal(msg, name) {
        document.getElementById('modalProductName').innerHTML = `<i class="fas fa-bug"></i> تفاصيل الخطأ لـ: <strong style="color: var(--text-primary); margin-inline-start: 0.25rem;">${name}</strong>`;
        document.getElementById('modalErrorContent').innerText = msg;
        document.getElementById('errorDetailsModal').style.display = 'flex';
    }

    function closeErrorDetailsModal() {
        document.getElementById('errorDetailsModal').style.display = 'none';
    }

    async function retrySingleFailure(barcode, btn) {
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> جاري إدراج...';
        
        try {
            const res = await fetch('/api/failures/retry', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content
                },
                body: JSON.stringify({ barcodes: [barcode] })
            });
            const data = await res.json();
            btn.disabled = false;
            btn.innerHTML = originalText;
            
            if (data.status === 'success') {
                alert('🎉 تم إعادة جدولة المنتج بنجاح في طابور الأتمتة وسيبدأ تشغيله فوراً.');
                const tr = document.getElementById(`row-${barcode}`);
                if (tr) {
                    tr.style.opacity = '0.3';
                    tr.style.background = 'rgba(16, 185, 129, 0.05)';
                    tr.querySelectorAll('input, button').forEach(el => el.disabled = true);
                }
            } else {
                alert('❌ فشل إعادة الجدولة: ' + data.error);
            }
        } catch (err) {
            console.error(err);
            btn.disabled = false;
            btn.innerHTML = originalText;
            alert('❌ خطأ اتصال بالخادم.');
        }
    }

    async function retrySelectedFailures() {
        const checkedCbs = Array.from(document.querySelectorAll('.error-select-checkbox:checked'));
        if (checkedCbs.length === 0) {
            alert('❌ يرجى تحديد منتج واحد على الأقل لإعادة المحاولة.');
            return;
        }

        const barcodes = checkedCbs.map(cb => cb.value);
        if (!confirm(`هل أنت متأكد من إعادة جدولة ${barcodes.length} منتجات فاشلة دفعة واحدة؟`)) {
            return;
        }

        const btn = document.getElementById('bulkRetryBtn');
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> جاري إعادة المحاولة الجماعية...';

        try {
            const res = await fetch('/api/failures/retry', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content
                },
                body: JSON.stringify({ barcodes: barcodes })
            });
            const data = await res.json();
            btn.disabled = false;
            btn.innerHTML = originalText;

            if (data.status === 'success') {
                alert(`🎉 تم بنجاح تصفير وجدولة ${barcodes.length} منتجات في طابور الخلفية!`);
                barcodes.forEach(b => {
                    const tr = document.getElementById(`row-${b}`);
                    if (tr) {
                        tr.style.opacity = '0.3';
                        tr.style.background = 'rgba(16, 185, 129, 0.05)';
                        tr.querySelectorAll('input, button').forEach(el => el.disabled = true);
                        const cb = tr.querySelector('.error-select-checkbox');
                        if (cb) cb.checked = false;
                    }
                });
                document.getElementById('selectAllCheckbox').checked = false;
            } else {
                alert('❌ فشل عملية إعادة المحاولة الجماعية: ' + data.error);
            }
        } catch (err) {
            console.error(err);
            btn.disabled = false;
            btn.innerHTML = originalText;
            alert('❌ خطأ اتصال بالخادم.');
        }
    }
</script>
@endsection
