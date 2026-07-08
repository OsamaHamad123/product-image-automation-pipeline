@extends('layouts.layout')

@section('title', '🎯 فرز واعتماد صور المنتجات')
@section('nav_catalog', 'active')

@section('styles')
<style>
    .layout-grid {
        display: grid;
        grid-template-columns: 350px 1fr;
        gap: 2rem;
        height: calc(100vh - 6.5rem);
    }

    /* Sidebar Catalog */
    .sidebar-panel {
        background: var(--panel-bg);
        border: 1px solid var(--panel-border);
        border-radius: var(--border-radius-md);
        padding: 1.75rem;
        display: flex;
        flex-direction: column;
        gap: 1.5rem;
        height: 100%;
        box-shadow: var(--shadow-md);
        backdrop-filter: blur(25px);
        -webkit-backdrop-filter: blur(25px);
        overflow: hidden;
    }

    .sidebar-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-bottom: 1px solid var(--panel-border);
        padding-bottom: 1rem;
    }

    .sidebar-header h3 {
        font-size: 1.1rem;
        font-weight: 800;
        display: flex;
        align-items: center;
        gap: 0.6rem;
        color: var(--text-primary);
    }

    .score-badge {
        background: var(--active-menu-bg);
        border: 1px solid var(--panel-border);
        color: var(--accent-purple);
        padding: 0.25rem 0.65rem;
        font-family: 'Outfit', sans-serif;
        border-radius: var(--border-radius-sm);
        font-size: 0.8rem;
        font-weight: 800;
    }

    .sidebar-search-container {
        position: relative;
        width: 100%;
    }

    .sidebar-search-container input {
        width: 100%;
        padding: 0.75rem 1rem 0.75rem 2.5rem;
        background: var(--input-bg);
        border: 1px solid var(--panel-border);
        border-radius: var(--border-radius-sm);
        color: var(--text-primary);
        font-family: inherit;
        font-size: 0.9rem;
        outline: none;
        transition: all 0.3s;
    }

    .sidebar-search-container input:focus {
        border-color: var(--accent-purple);
        box-shadow: 0 0 10px rgba(139, 92, 246, 0.2);
    }

    .sidebar-search-container i {
        position: absolute;
        left: 1rem;
        top: 50%;
        transform: translateY(-50%);
        color: var(--text-secondary);
        font-size: 0.95rem;
    }

    .tabs-nav {
        display: flex;
        flex-wrap: wrap;
        background: rgba(0, 0, 0, 0.15);
        border: 1px solid var(--panel-border);
        padding: 4px;
        border-radius: var(--border-radius-sm);
        gap: 4px;
    }

    .tab-btn {
        flex: 1;
        padding: 0.6rem 0.4rem;
        border: none;
        background: transparent;
        color: var(--text-secondary);
        font-family: inherit;
        font-weight: 800;
        font-size: 0.8rem;
        border-radius: 6px;
        cursor: pointer;
        transition: all 0.25s ease;
        text-align: center;
    }

    .tab-btn:hover {
        color: var(--text-primary);
        background: rgba(255, 255, 255, 0.03);
    }

    .tab-btn.active {
        background: var(--accent-gradient);
        color: #ffffff;
        box-shadow: var(--shadow-sm);
    }

    .product-list {
        overflow-y: auto;
        flex: 1;
        display: flex;
        flex-direction: column;
        gap: 0.75rem;
        padding-left: 0.25rem;
    }

    .pagination-container {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding-top: 1rem;
        border-top: 1px solid var(--panel-border);
        font-size: 0.85rem;
        gap: 0.5rem;
        direction: rtl;
    }

    .pagination-btn {
        background: var(--input-bg);
        border: 1px solid var(--panel-border);
        color: var(--text-secondary);
        padding: 0.5rem 1rem;
        border-radius: var(--border-radius-sm);
        cursor: pointer;
        font-weight: 700;
        font-family: inherit;
        display: flex;
        align-items: center;
        gap: 0.35rem;
        transition: all 0.25s ease;
    }

    .pagination-btn:hover:not(:disabled) {
        border-color: var(--accent-purple);
        color: var(--text-primary);
        background: var(--card-bg-hover);
        transform: translateY(-1px);
    }

    .pagination-btn:disabled {
        opacity: 0.35;
        cursor: not-allowed;
    }

    .pagination-info {
        color: var(--text-secondary);
        font-weight: bold;
        font-size: 0.8rem;
    }

    .product-item {
        background: var(--card-bg);
        border: 1px solid var(--panel-border);
        border-radius: var(--border-radius-sm);
        padding: 1.25rem;
        cursor: pointer;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        position: relative;
    }

    .product-item:hover {
        border-color: var(--panel-border-hover);
        background: var(--card-bg-hover);
        transform: translateY(-2px);
        box-shadow: var(--shadow-sm), 0 4px 15px rgba(139, 92, 246, 0.05);
    }

    .product-item.active {
        border-color: var(--accent-purple);
        background: var(--active-menu-bg);
        box-shadow: var(--shadow-sm), 0 0 15px rgba(139, 92, 246, 0.12);
    }

    .product-item h4 {
        font-size: 0.95rem;
        font-weight: 800;
        margin-bottom: 0.4rem;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        max-width: 250px;
        color: var(--text-primary);
    }

    .product-item p {
        font-size: 0.8rem;
        color: var(--text-secondary);
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-weight: bold;
    }

    .badge-row-number {
        position: absolute;
        top: 0.5rem;
        left: 0.5rem;
        background: var(--input-bg);
        border: 1px solid var(--panel-border);
        color: var(--text-secondary);
        font-size: 0.75rem;
        font-weight: 700;
        padding: 0.1rem 0.45rem;
        border-radius: 4px;
        font-family: 'Outfit', sans-serif;
    }

    /* Main curation panel */
    .curation-panel {
        display: flex;
        flex-direction: column;
        gap: 1.75rem;
        height: 100%;
        overflow-y: auto;
        padding-left: 0.5rem;
    }

    .form-grid {
        display: grid;
        grid-template-columns: 2fr 1fr;
        gap: 1.5rem;
    }

    .form-group {
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
    }

    .form-group label {
        font-size: 0.85rem;
        font-weight: 700;
        color: var(--text-secondary);
    }

    .form-group input, .form-group select {
        padding: 0.75rem 1rem;
        background: var(--input-bg);
        border: 1px solid var(--panel-border);
        border-radius: var(--border-radius-sm);
        color: var(--text-primary);
        font-family: inherit;
        font-size: 0.9rem;
        outline: none;
        transition: all 0.3s;
    }

    .form-group input:focus, .form-group select:focus {
        border-color: var(--accent-purple);
        box-shadow: 0 0 10px rgba(139, 92, 246, 0.2);
    }

    /* Toggles */
    .toggles-row {
        display: flex;
        flex-wrap: wrap;
        gap: 1.5rem;
        align-items: center;
        height: 100%;
    }

    .toggle-container {
        display: flex;
        align-items: center;
        gap: 0.6rem;
    }

    .toggle-container span {
        font-size: 0.85rem;
        font-weight: 700;
        color: var(--text-secondary);
    }

    .switch {
        position: relative;
        display: inline-block;
        width: 44px;
        height: 24px;
    }

    .switch input {
        opacity: 0;
        width: 0;
        height: 0;
    }

    .slider {
        position: absolute;
        cursor: pointer;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background-color: var(--panel-border);
        transition: .3s cubic-bezier(0.4, 0, 0.2, 1);
        border-radius: 20px;
    }

    .slider:before {
        position: absolute;
        content: "";
        height: 18px;
        width: 18px;
        left: 3px;
        bottom: 3px;
        background-color: white;
        transition: .3s cubic-bezier(0.4, 0, 0.2, 1);
        border-radius: 50%;
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.25);
    }

    input:checked + .slider {
        background-image: var(--accent-gradient);
    }

    input:checked + .slider:before {
        transform: translateX(20px);
    }

    /* Loading Spinner */
    .loading-container {
        display: none;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: 5rem 0;
        text-align: center;
    }

    .spinner-box {
        position: relative;
        width: 52px;
        height: 52px;
        margin-bottom: 1.25rem;
    }

    .spinner-ring {
        box-sizing: border-box;
        display: block;
        position: absolute;
        width: 52px;
        height: 52px;
        border: 4px solid transparent;
        border-radius: 50%;
        animation: spin-ring 1s cubic-bezier(0.5, 0, 0.5, 1) infinite;
        border-top-color: var(--accent-purple);
    }
    .spinner-ring:nth-child(1) { animation-delay: -0.3s; }
    .spinner-ring:nth-child(2) { animation-delay: -0.15s; }

    @keyframes spin-ring {
        0% { transform: rotate(0deg); }
        100% { transform: rotate(360deg); }
    }

    /* Candidates Grid */
    .candidates-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
        gap: 1.5rem;
        margin-top: 1.5rem;
    }

    .candidate-card {
        background: var(--card-bg);
        border: 1px solid var(--panel-border);
        border-radius: var(--border-radius-md);
        overflow: hidden;
        display: flex;
        flex-direction: column;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        position: relative;
        box-shadow: var(--shadow-sm);
    }

    .candidate-card:hover {
        border-color: var(--accent-purple);
        transform: translateY(-5px);
        box-shadow: var(--shadow-md), 0 8px 24px rgba(139, 92, 246, 0.12);
    }

    .candidate-img-box {
        height: 200px;
        width: 100%;
        background: var(--img-box-bg);
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 1.5rem;
        position: relative;
        border-bottom: 1px solid var(--panel-border);
    }

    .candidate-img-box img {
        max-height: 100%;
        max-width: 100%;
        object-fit: contain;
        transition: transform 0.4s ease;
    }

    .candidate-card:hover .candidate-img-box img {
        transform: scale(1.06);
    }

    .candidate-badge {
        position: absolute;
        top: 0.75rem;
        right: 0.75rem;
        padding: 0.25rem 0.75rem;
        border-radius: var(--border-radius-sm);
        font-size: 0.75rem;
        font-weight: 800;
        z-index: 5;
    }

    .candidate-badge.accepted { 
        background: var(--success-bg); 
        border: 1px solid rgba(16, 185, 129, 0.2);
        color: var(--success);
    }
    .candidate-badge.rejected { 
        background: var(--danger-bg); 
        border: 1px solid rgba(239, 68, 68, 0.2);
        color: var(--danger);
    }

    .candidate-score-tag {
        position: absolute;
        bottom: 0.75rem;
        right: 0.75rem;
        background: var(--input-bg);
        border: 1px solid var(--panel-border);
        padding: 0.2rem 0.6rem;
        border-radius: 6px;
        font-size: 0.75rem;
        font-weight: 800;
        color: var(--accent-cyan);
        font-family: 'Outfit', sans-serif;
    }

    .candidate-uae-tag {
        position: absolute;
        bottom: 0.75rem;
        left: 0.75rem;
        background: var(--success-bg);
        border: 1px solid rgba(16, 185, 129, 0.2);
        padding: 0.2rem 0.6rem;
        border-radius: 6px;
        font-size: 0.75rem;
        font-weight: 800;
        color: var(--success);
    }

    .candidate-info {
        padding: 1.25rem;
        display: flex;
        flex-direction: column;
        gap: 0.75rem;
        flex: 1;
    }

    .candidate-title {
        font-weight: 700;
        font-size: 0.85rem;
        line-height: 1.5;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
        height: 2.5rem;
        color: var(--text-primary);
    }

    .candidate-meta {
        font-size: 0.75rem;
        color: var(--text-secondary);
        display: flex;
        justify-content: space-between;
        font-family: 'Outfit', sans-serif;
        font-weight: 600;
    }

    .candidate-reasons {
        font-size: 0.75rem;
        color: var(--danger);
        background: var(--danger-bg);
        border: 1px solid rgba(244, 63, 94, 0.15);
        padding: 0.5rem 0.75rem;
        border-radius: var(--border-radius-sm);
        margin-top: 0.25rem;
        line-height: 1.5;
        font-weight: 700;
    }

    /* Accordion Logs */
    .step-accordion {
        margin-top: 1.25rem;
        display: flex;
        flex-direction: column;
        gap: 0.75rem;
    }

    .step-item {
        border: 1px solid var(--panel-border);
        border-radius: var(--border-radius-md);
        overflow: hidden;
        background: var(--card-bg);
        box-shadow: var(--shadow-sm);
        transition: border-color 0.3s;
    }

    .step-item:hover {
        border-color: var(--panel-border-hover);
    }

    .step-header {
        background: var(--accordion-header-bg);
        padding: 1rem 1.5rem;
        cursor: pointer;
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-weight: 800;
        font-size: 0.9rem;
        transition: all 0.2s ease;
    }

    .step-header:hover { 
        background: var(--card-bg-hover); 
    }

    .step-body {
        background: var(--console-bg);
        padding: 1.25rem 1.5rem;
        display: none;
        border-top: 1px solid var(--panel-border);
        font-family: 'Courier New', Courier, monospace;
        font-size: 0.8rem;
        line-height: 1.6;
        color: #38edf6;
    }

    .step-body.active { 
        display: block; 
    }
    
    .status-text { 
        font-weight: 800; 
    }
    .status-text.active { color: var(--warning); }
    .status-text.success { color: var(--success); }
    .status-text.failed { color: var(--danger); }

    /* Slide-to-Compare Styles */
    .compare-container {
        position: relative;
        width: 320px;
        height: 320px;
        background: #04060e;
        border: 1px solid var(--panel-border);
        border-radius: var(--border-radius-md);
        overflow: hidden;
        user-select: none;
        flex-shrink: 0;
        box-shadow: var(--shadow-md);
    }

    .compare-img {
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 0.5rem;
    }

    .compare-img img {
        max-width: 90%;
        max-height: 90%;
        object-fit: contain;
        pointer-events: none;
    }

    /* Processed image layer sits on top, initially clipped */
    .compare-overlay {
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        overflow: hidden;
        width: 50%;
        border-right: 2px solid var(--accent-cyan);
    }

    .compare-overlay .compare-img {
        width: 320px; /* Lock width to container size so image doesn't scale */
    }

    .compare-handle {
        position: absolute;
        top: 0;
        bottom: 0;
        left: 50%;
        width: 2px;
        background: var(--accent-gradient);
        box-shadow: 0 0 10px rgba(0, 242, 254, 0.8);
        cursor: ew-resize;
        z-index: 10;
    }

    .compare-handle:before {
        content: "\f07d";
        font-family: "Font Awesome 6 Free";
        font-weight: 900;
        position: absolute;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%) rotate(90deg);
        width: 32px;
        height: 32px;
        background: var(--accent-gradient);
        border: 2px solid #fff;
        color: #fff;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 0.85rem;
        box-shadow: 0 4px 12px rgba(0,0,0,0.4);
    }

    /* Alpha Checkerboard Background */
    .bg-checkerboard {
        background-color: #121620 !important;
        background-image: 
            linear-gradient(45deg, #181d28 25%, transparent 25%), 
            linear-gradient(-45deg, #181d28 25%, transparent 25%), 
            linear-gradient(45deg, transparent 75%, #181d28 75%), 
            linear-gradient(-45deg, transparent 75%, #181d28 75%) !important;
        background-size: 20px 20px !important;
        background-position: 0 0, 0 10px, 10px -10px, -10px 0px !important;
    }

    .bg-white {
        background-color: #ffffff !important;
    }

    .bg-gray {
        background-color: #f5f5f7 !important;
    }

    /* Split-Screen Workbench Grid layout */
    .workbench-grid {
        display: grid;
        grid-template-columns: auto 1fr;
        gap: 2rem;
        align-items: stretch;
    }

    @media (max-width: 768px) {
        .workbench-grid {
            grid-template-columns: 1fr;
        }
    }

    .feedback-reasons-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 0.65rem;
        margin-top: 0.75rem;
    }

    .feedback-checkbox {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        font-size: 0.85rem;
        color: var(--text-secondary);
        cursor: pointer;
        padding: 0.5rem 0.75rem;
        background: rgba(255,255,255,0.02);
        border: 1px solid var(--panel-border);
        border-radius: var(--border-radius-sm);
        transition: all 0.25s;
        font-weight: 700;
    }

    .feedback-checkbox:hover {
        background: rgba(255,255,255,0.04);
        color: var(--text-primary);
        border-color: var(--accent-purple);
    }
    .feedback-checkbox input {
        cursor: pointer;
    }
</style>
@endsection

@section('content')
<div class="layout-grid">
    <!-- Sidebar: Product List -->
    <div class="sidebar-panel">
        <div class="sidebar-header">
            <h3><i class="fas fa-file-spreadsheet"></i> منتجات الشيت</h3>
            <div style="display:flex; align-items:center; gap:0.5rem;">
                <i id="cacheIndicator" class="fas fa-bolt" title="⚡ من الكاش" style="font-size:0.8rem; color:var(--accent-cyan); cursor:help;"></i>
                <span id="sheetProductCount" class="score-badge">0</span>
                <button id="refreshBtn" onclick="refreshProducts()" title="تحديث من Google Sheets مباشرة" style="background:none; border:1px solid var(--panel-border); color:var(--text-secondary); border-radius:6px; padding:3px 8px; cursor:pointer; font-size:0.8rem; transition:all 0.2s;" onmouseover="this.style.color='var(--accent-cyan)'" onmouseout="this.style.color='var(--text-secondary)'">
                    <i class="fas fa-sync-alt"></i>
                </button>
            </div>
        </div>

        
        <div class="sidebar-search-container">
            <i class="fas fa-search"></i>
            <input type="text" id="sidebarSearch" placeholder="ابحث باسم المنتج أو البراند..." oninput="filterProducts()">
        </div>
        
        <div class="tabs-nav">
            <button class="tab-btn active" id="tab-all" onclick="setFilterTab('all')">الكل</button>
            <button class="tab-btn" id="tab-missing" onclick="setFilterTab('missing')">المفقودة</button>
            <button class="tab-btn" id="tab-review" onclick="setFilterTab('review')">المراجعة ⚠️</button>
            <button class="tab-btn" id="tab-errors" onclick="setFilterTab('errors')">أخطاء ❌</button>
            <button class="tab-btn" id="tab-linked" onclick="setFilterTab('linked')">المكتملة</button>
        </div>
        
        <div id="productList" class="product-list">
            <p style="color: var(--text-secondary); text-align: center; padding: 2rem;">جاري تحميل المنتجات...</p>
        </div>

        <div id="paginationContainer" class="pagination-container" style="display: none;">
            <!-- سيتم توليد أزرار التنقل ديناميكياً هنا -->
        </div>
    </div>

    <!-- Main Work Panel -->
    <div class="curation-panel">
        <!-- Search Criteria -->
        <div class="glass-panel" style="margin-bottom: 0;">
            <h3 style="font-size: 1.1rem; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.5rem; display: flex; align-items: center; gap: 0.5rem; margin-bottom: 1.5rem;">
                <i class="fas fa-sliders-h"></i> معايير البحث والفرز الذكي
            </h3>
            <form id="searchForm">
                <input type="hidden" id="rowNumber">
                <input type="hidden" id="productNameAr">
                <input type="hidden" id="brandAr">
                <div class="form-grid">
                    <div class="form-group">
                        <label for="productName">اسم المنتج المكتوب</label>
                        <input type="text" id="productName" required placeholder="مثال: Organic Full Fat Milk 1L">
                    </div>
                    <div class="form-group">
                        <label for="brand">العلامة التجارية (البراند)</label>
                        <input type="text" id="brand" placeholder="مثال: Meliha">
                    </div>
                </div>
                <div class="form-grid" style="margin-top: 1rem;">
                    <div class="form-group">
                        <label for="customQuery">استعلام البحث المخصص (تلقائي إن تُرِك فارغاً)</label>
                        <input type="text" id="customQuery" placeholder="مثال: Mleiha Long Life Milk 1 Litre">
                    </div>
                    <div class="form-group" style="justify-content: flex-end;">
                        <div class="toggles-row">
                            <div class="toggle-container">
                                <span>تخطي تعارض الأحجام</span>
                                <label class="switch">
                                    <input type="checkbox" id="ignoreUnitClash">
                                    <span class="slider"></span>
                                </label>
                            </div>
                            <div class="toggle-container">
                                <span>مطابقة البراند الصارمة</span>
                                <label class="switch">
                                    <input type="checkbox" id="strictBrandMatch" checked>
                                    <span class="slider"></span>
                                </label>
                            </div>
                            <div class="toggle-container">
                                <span>تكبير ذكي (AI Upscale)</span>
                                <label class="switch">
                                    <input type="checkbox" id="aiUpscale" checked>
                                    <span class="slider"></span>
                                </label>
                            </div>
                            <div class="toggle-container" title="تحسين جودة وتفاصيل الصورة (وقد يسبب تغيير خفيف في الألوان)">
                                <span>تحسين جودة الصورة (AI Enhance)</span>
                                <label class="switch">
                                    <input type="checkbox" id="aiEnhance">
                                    <span class="slider"></span>
                                </label>
                            </div>
                            <div class="toggle-container" title="البحث مباشرة من محرك البحث وتجنب نتائج الكاش المخزنة">
                                <span>تجاوز الكاش المحلي</span>
                                <label class="switch">
                                    <input type="checkbox" id="skipCache">
                                    <span class="slider"></span>
                                </label>
                            </div>
                        </div>
                    </div>
                </div>
                <button type="submit" class="btn" id="submitBtn" style="margin-top: 1.5rem; width: 100%;">
                    <i class="fas fa-search-plus"></i> ابدأ الفحص البصري والبحث الذكي
                </button>
            </form>
        </div>

        {{-- ====== Image Output Settings Panel ====== --}}
        <div class="glass-panel" style="margin-bottom: 0; padding: 1.25rem 1.5rem;">
            <h3 style="font-size: 1rem; font-weight: 700; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.65rem; margin-bottom: 1.1rem; display: flex; align-items: center; gap: 0.5rem;">
                <i class="fas fa-crop-alt" style="color: var(--accent-cyan);"></i> إعدادات مخرجات الصورة (Cloudinary)
            </h3>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 1rem;">
                {{-- الأبعاد --}}
                <div class="form-group">
                    <label for="outputPreset" style="font-size:0.8rem; color:var(--text-secondary);">الأبعاد ونسبة العرض إلى الارتفاع</label>
                    <select id="outputPreset" onchange="applyPreset()" style="width:100%; padding:0.6rem 0.75rem; background:var(--input-bg); border:1px solid var(--panel-border); border-radius:6px; color:var(--text-primary); font-family:inherit; font-size:0.85rem;">
                        <option value="dynamic" selected>تلقائي ذكي (Dynamic AI — يحافظ على جودة الصورة الأصلية)</option>
                        <option value="800x800">800 × 800 — مربع قياسي (بقالة، FMCG)</option>
                        <option value="1000x1000">1000 × 1000 — مربع عالي الدقة</option>
                        <option value="900x1200">900 × 1200 — عمودي (ملابس، أزياء)</option>
                        <option value="1200x900">1200 × 900 — أفقي (أجهزة، إلكترونيات)</option>
                        <option value="custom">مخصص...</option>
                    </select>
                </div>
                {{-- هامش الأمان --}}
                <div class="form-group">
                    <label for="paddingRatio" style="font-size:0.8rem; color:var(--text-secondary);">هامش الأمان البصري (Safety Margin)</label>
                    <select id="paddingRatio" style="width:100%; padding:0.6rem 0.75rem; background:var(--input-bg); border:1px solid var(--panel-border); border-radius:6px; color:var(--text-primary); font-family:inherit; font-size:0.85rem;">
                        <option value="0.90">10% — هامش ضيق</option>
                        <option value="0.85" selected>15% — هامش متناسق (افتراضي)</option>
                        <option value="0.80">20% — هامش واسع</option>
                        <option value="0.75">25% — هامش فضفاض (للمنتجات الطولية)</option>
                    </select>
                </div>
                {{-- لون الخلفية --}}
                <div class="form-group">
                    <label for="bgColor" style="font-size:0.8rem; color:var(--text-secondary);">لون خلفية Canvas</label>
                    <div style="display:flex; gap:0.5rem; align-items:center;">
                        <select id="bgColorPreset" onchange="applyBgColor()" style="flex:1; padding:0.6rem 0.75rem; background:var(--input-bg); border:1px solid var(--panel-border); border-radius:6px; color:var(--text-primary); font-family:inherit; font-size:0.85rem;">
                            <option value="ffffff">أبيض (#FFFFFF)</option>
                            <option value="f5f5f7">رمادي استوديو (#F5F5F7)</option>
                            <option value="fafafa">أبيض دافئ (#FAFAFA)</option>
                            <option value="transparent">شفاف (PNG)</option>
                        </select>
                        <input type="color" id="bgColorPicker" value="#ffffff" oninput="document.getElementById('bgColor').value = this.value.replace('#', '')" style="width:36px; height:36px; border-radius:6px; border:1px solid var(--panel-border); padding:2px; cursor:pointer; background:transparent;">
                    </div>
                    <input type="hidden" id="bgColor" value="ffffff">
                </div>
                {{-- الأبعاد المخصصة --}}
                <div id="customDimBox" style="display:none; grid-column: 1 / -1;">
                    <div style="display:flex; gap:0.75rem; align-items:center;">
                        <div class="form-group" style="flex:1;">
                            <label for="customWidth" style="font-size:0.8rem; color:var(--text-secondary);">العرض (px)</label>
                            <input type="number" id="customWidth" value="800" min="100" max="3000" style="width:100%; padding:0.6rem 0.75rem; background:var(--input-bg); border:1px solid var(--panel-border); border-radius:6px; color:var(--text-primary); font-family:inherit; font-size:0.85rem;">
                        </div>
                        <div class="form-group" style="flex:1;">
                            <label for="customHeight" style="font-size:0.8rem; color:var(--text-secondary);">الارتفاع (px)</label>
                            <input type="number" id="customHeight" value="800" min="100" max="3000" style="width:100%; padding:0.6rem 0.75rem; background:var(--input-bg); border:1px solid var(--panel-border); border-radius:6px; color:var(--text-primary); font-family:inherit; font-size:0.85rem;">
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Curation workspace status -->
        <div class="glass-panel" style="min-height: 450px;">
            <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.75rem; margin-bottom: 1.5rem;">
                <h3 style="font-size: 1.1rem; margin: 0;"><i class="fas fa-eye"></i> نتائج الفرز والمطابقة البصرية</h3>
                <span id="overallStatus" class="status-text active">في انتظار الإدخال</span>
            </div>

            <!-- Placeholder -->
            <div id="placeholder" style="text-align: center; color: var(--text-secondary); padding: 5rem 0;">
                <i class="far fa-image" style="font-size: 3.5rem; margin-bottom: 1.5rem; opacity: 0.3; display: block;"></i>
                اختر منتجاً من القائمة الجانبية على اليمين للتحليل، أو ادخل البيانات يدوياً للبحث الفوري وعرض تفاصيل الفحص بـ Gemini Vision و CLIP.
            </div>

            <!-- Loading Spinner -->
            <div id="loading" class="loading-container">
                <div class="spinner-box">
                    <div class="spinner-ring"></div>
                    <div class="spinner-ring"></div>
                    <div class="spinner-ring"></div>
                </div>
                <p style="font-weight: 700; font-size: 1.1rem; margin-bottom: 0.25rem;">جاري جلب وتقييم الصور بالذكاء الاصطناعي...</p>
                <p id="loadingDetails" style="font-size: 0.85rem; color: var(--text-secondary);">يرجى الانتظار، قنوات الفحص البصري بـ Gemini و CLIP مفعلة...</p>
            </div>

            <!-- Results Workspace -->
            <div id="resultsContent" style="display: none;">
                <!-- Recommended Image Card -->
                <div id="recommendedContainer"></div>

                <!-- Drag-drop & url overrides -->
                <div style="margin-top: 1.5rem; padding: 1.5rem; background: rgba(255, 255, 255, 0.03); border: 1px solid var(--panel-border); border-radius: 16px;">
                    <h4 style="font-size: 1rem; margin-bottom: 0.75rem; color: var(--accent-cyan); display: flex; align-items: center; gap: 0.5rem;">
                        <i class="fas fa-edit"></i> خيارات الاعتماد اليدوي (Manual Override & Upload)
                    </h4>
                    <div class="form-grid" style="grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1.5rem;">
                        <div>
                            <h5 style="font-size: 0.85rem; margin-bottom: 0.5rem; color: var(--text-secondary);">ضع رابط الصورة المباشر هنا:</h5>
                            <div style="display: flex; gap: 0.75rem;">
                                <input type="text" id="manualImageUrl" placeholder="ضع رابط الصورة المباشر هنا..." style="flex: 1; padding: 0.75rem 1rem; background: rgba(8, 12, 20, 0.6); border: 1px solid var(--panel-border); border-radius: 10px; color: var(--text-primary); font-family: inherit;">
                                <button class="btn" onclick="previewManualImage()"><i class="fas fa-eye"></i> معاينة</button>
                            </div>
                        </div>
                        <div>
                            <h5 style="font-size: 0.85rem; margin-bottom: 0.5rem; color: var(--text-secondary);">أو اسحب صورة للرفع والتجميل التلقائي:</h5>
                            <div id="dropZone" ondragover="event.preventDefault()" ondrop="handleFileDrop(event)" onclick="triggerFileInput()" style="border: 2px dashed var(--panel-border); border-radius: 12px; padding: 0.75rem 1rem; text-align: center; cursor: pointer; transition: all 0.25s; background: rgba(0, 229, 255, 0.01); display: flex; flex-direction: column; align-items: center; justify-content: center;">
                                <i class="fas fa-cloud-upload-alt" style="font-size: 1.3rem; color: var(--accent-cyan); margin-bottom: 0.25rem;"></i>
                                <p style="font-size: 0.75rem; margin: 0; color: var(--text-secondary);">اسحب وأسقط صورتك هنا أو انقر للتصفح</p>
                                <input type="file" id="manualFileInput" onchange="handleFileSelect(event)" style="display: none;" accept="image/*">
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Taxonomy editing dropdowns -->
                <div id="taxonomyEditorContainer" style="margin-top: 1.5rem; padding: 1.5rem; background: rgba(255, 255, 255, 0.03); border: 1px solid var(--panel-border); border-radius: 16px;">
                    <h4 style="font-size: 1rem; margin-bottom: 0.75rem; color: var(--accent-cyan); display: flex; align-items: center; gap: 0.5rem;">
                        <i class="fas fa-tags"></i> تعديل تصنيف الفئات المعتمد (Taxonomy Editor)
                    </h4>
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem;">
                        <div class="form-group">
                            <label style="font-size: 0.8rem; color: var(--text-secondary);">التصنيف الرئيسي L1</label>
                            <select id="selectL1" onchange="onL1Change()"></select>
                        </div>
                        <div class="form-group">
                            <label style="font-size: 0.8rem; color: var(--text-secondary);">التصنيف الفرعي L2</label>
                            <select id="selectL2" onchange="onL2Change()"></select>
                        </div>
                        <div class="form-group">
                            <label style="font-size: 0.8rem; color: var(--text-secondary);">التصنيف الفرعي الفرعي L3</label>
                            <select id="selectL3"></select>
                        </div>
                    </div>
                </div>

                <!-- Tested candidate cards grid -->
                <h3 style="margin-top: 2rem; margin-bottom: 1rem; font-size: 1.1rem; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.5rem; display: flex; align-items: center; gap: 0.5rem;">
                    <i class="fas fa-images"></i> الصور المرشحة المفحوصة
                </h3>
                <div id="candidatesContainer" class="candidates-grid"></div>

                <!-- Step-by-step query expansion trace -->
                <h3 style="margin-top: 2rem; margin-bottom: 1rem; font-size: 1.1rem; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.5rem; display: flex; align-items: center; gap: 0.5rem;">
                    <i class="fas fa-terminal"></i> سجل البحث التتبعي خطوة بخطوة (Trace)
                </h3>
                <div id="accordionContainer" class="step-accordion"></div>

                <!-- Live logs Console terminal -->
                <div class="glass-panel" style="margin-top: 2rem; background: rgba(8, 12, 20, 0.95); border: 1px solid var(--panel-border); padding: 1.5rem;">
                    <h3 style="font-size: 1.1rem; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.5rem; display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; margin-bottom: 1rem;">
                        <span style="color: #00ff66; font-family: 'Courier New', monospace;"><i class="fas fa-terminal"></i> سجل الأتمتة المباشر (Live runner Console)</span>
                        <button class="btn btn-secondary btn-sm" onclick="clearLiveConsoleLogs()" style="padding: 2px 8px; font-size: 0.75rem;">تفريغ السجلات</button>
                    </h3>
                    <div id="liveConsoleLogs" style="font-family: 'Courier New', Courier, monospace; font-size: 0.85rem; color: #00ff66; background: #080c14; padding: 1rem; border-radius: 8px; max-height: 200px; overflow-y: auto; text-align: left; direction: ltr; line-height: 1.5; border: 1px solid rgba(255,255,255,0.05);">
                        <p style="color: var(--text-secondary);">[System] Initializing console logs listener...</p>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>

<!-- Canvas Editor Modal -->
<div id="editorModal" class="modal" style="display: none; position: fixed; z-index: 10000; left: 0; top: 0; width: 100%; height: 100%; overflow: auto; background-color: rgba(8, 12, 20, 0.9); backdrop-filter: blur(8px); align-items: center; justify-content: center;">
    <div class="glass-panel" style="max-width: 600px; width: 90%; padding: 2rem; border-radius: 20px; border: 1px solid var(--panel-border); text-align: center; margin: 5% auto;">
        <h3 style="font-size: 1.2rem; margin-bottom: 1rem; display: flex; align-items: center; justify-content: center; gap: 0.5rem; color: var(--accent-cyan);">
            <i class="fas fa-crop-alt"></i> محرر ومعاين الصورة المرفوعة
        </h3>
        <div style="background: #080c14; border-radius: 12px; padding: 1rem; margin-bottom: 1.5rem; display: flex; align-items: center; justify-content: center; min-height: 250px; border: 1px solid var(--panel-border);">
            <canvas id="editorCanvas" style="max-width: 100%; max-height: 350px; border-radius: 8px; box-shadow: 0 8px 30px rgba(0,0,0,0.5);"></canvas>
        </div>
        <div style="display: flex; justify-content: center; gap: 1rem; margin-bottom: 1.5rem;">
            <button class="btn btn-secondary" onclick="editorRotate()"><i class="fas fa-sync-alt"></i> تدوير 90° 🔄</button>
            <button class="btn btn-secondary" onclick="editorFlip()"><i class="fas fa-arrows-alt-h"></i> انعكاس أفقياً ↔️</button>
        </div>
        <div style="display: flex; justify-content: space-between; gap: 1rem;">
            <button class="btn btn-secondary" onclick="closeEditorModal()" style="flex: 1;">إلغاء</button>
            <button class="btn" onclick="commitEditorUpload()" style="flex: 2; background: linear-gradient(135deg, #00e5ff 0%, #00e676 100%); color: #080c14; font-weight: 900;"><i class="fas fa-check"></i> اعتماد الرفع والتجميل التلقائي</button>
        </div>
    </div>
</div>
@endsection

@section('scripts')
<script>
    // Taxonomy details mapped for rendering L1 L2 L3
    const taxonomyData = {
        "Grocery": {
            "ar": "البقالة",
            "subs": {
                "Dairy & Eggs": {
                    "ar": "الألبان والبيض",
                    "sub_subs": {
                        "Milk": "الحليب",
                        "Cheese": "الجبن",
                        "Butter & Cream": "الزبدة والقشطة",
                        "Eggs": "البيض"
                    }
                },
                "Snacks & Sweets": {
                    "ar": "السناكس والحلويات",
                    "sub_subs": {
                        "Chips & Crackers": "المقرمشات والشيبس",
                        "Chocolates": "الشوكولاتة",
                        "Biscuits & Cookies": "البسكويت والكوكيز",
                        "Candy & Gum": "الحلوى واللبان"
                    }
                },
                "Beverages": {
                    "ar": "المشروبات",
                    "sub_subs": {
                        "Water": "المياه",
                        "Juices": "العصائر",
                        "Soft Drinks": "المشروبات الغازية",
                        "Tea & Coffee": "الشاي والقهوة"
                    }
                }
            }
        }
    };

    const searchCache = {};
    let currentProducts = [];
    let activeRowNumber = null;
    let currentFilterTab = 'all';
    let currentPage = 1;
    const itemsPerPage = 50;

    // دالة لتمرير روابط الصور الخارجية عبر البروكسي الداخلي لتجاوز حماية الـ Hotlinking
    function getImageUrl(url) {
        if (!url) return '';
        if (url.includes('cloudinary.com') || url.startsWith('http://127.0.0.1') || url.startsWith('localhost') || url.includes('/api/image-proxy')) {
            return url;
        }
        return `/api/image-proxy?url=${encodeURIComponent(url)}`;
    }

    // حفظ التغييرات تلقائياً في LocalStorage
    function saveSettingsToLocalStorage() {
        localStorage.setItem('ignoreUnitClash', document.getElementById('ignoreUnitClash').checked);
        localStorage.setItem('strictBrandMatch', document.getElementById('strictBrandMatch').checked);
        localStorage.setItem('aiUpscale', document.getElementById('aiUpscale').checked);
        localStorage.setItem('aiEnhance', document.getElementById('aiEnhance').checked);
        localStorage.setItem('skipCache', document.getElementById('skipCache').checked);
        localStorage.setItem('target_width', getOutputWidth());
        localStorage.setItem('target_height', getOutputHeight());
        localStorage.setItem('padding_ratio', document.getElementById('paddingRatio').value);
        localStorage.setItem('bg_color', document.getElementById('bgColor').value);
    }

    // On load
    window.addEventListener('load', () => {
        // استعادة الإعدادات المخزنة من LocalStorage إن وجدت
        if (localStorage.getItem('ignoreUnitClash') !== null) {
            document.getElementById('ignoreUnitClash').checked = localStorage.getItem('ignoreUnitClash') === 'true';
        }
        if (localStorage.getItem('strictBrandMatch') !== null) {
            document.getElementById('strictBrandMatch').checked = localStorage.getItem('strictBrandMatch') === 'true';
        }
        if (localStorage.getItem('aiUpscale') !== null) {
            document.getElementById('aiUpscale').checked = localStorage.getItem('aiUpscale') === 'true';
        }
        if (localStorage.getItem('aiEnhance') !== null) {
            document.getElementById('aiEnhance').checked = localStorage.getItem('aiEnhance') === 'true';
        }
        if (localStorage.getItem('skipCache') !== null) {
            document.getElementById('skipCache').checked = localStorage.getItem('skipCache') === 'true';
        }
        if (localStorage.getItem('padding_ratio') !== null) {
            document.getElementById('paddingRatio').value = localStorage.getItem('padding_ratio');
        }
        if (localStorage.getItem('bg_color') !== null) {
            const storedBg = localStorage.getItem('bg_color');
            document.getElementById('bgColor').value = storedBg;
            const bgPreset = document.getElementById('bgColorPreset');
            if (bgPreset) {
                // التحقق مما إذا كانت الخلفية مخصصة أو من القائمة
                let found = false;
                for (let i = 0; i < bgPreset.options.length; i++) {
                    if (bgPreset.options[i].value === storedBg) {
                        bgPreset.selectedIndex = i;
                        found = true;
                        break;
                    }
                }
                if (!found) bgPreset.value = 'custom';
            }
        }
        
        // إضافة مستمعي الأحداث لحفظ التغييرات فوراً
        const inputs = [
            'ignoreUnitClash', 'strictBrandMatch', 'aiUpscale', 'aiEnhance', 'skipCache', 
            'outputPreset', 'paddingRatio', 'bgColorPreset', 'customWidth', 'customHeight'
        ];
        inputs.forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                el.addEventListener('change', saveSettingsToLocalStorage);
            }
        });

        loadProducts();
        initTaxonomyDropdowns();
    });

    // جلب قائمة المنتجات (مع كاش 60 ثانية في السيرفر)
    async function loadProducts(forceRefresh = false) {
        const productList = document.getElementById('productList');
        productList.innerHTML = '<p style="text-align:center; color:var(--text-secondary); padding:2rem;"><i class="fas fa-spinner fa-spin"></i> جاري تحميل المنتجات...</p>';
        try {
            if (forceRefresh) {
                // مسح كاش السيرفر أولاً لإجبار التحديث من Google Sheets
                await fetch('/api/clear-products-cache', {
                    method: 'POST',
                    headers: { 'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content }
                });
            }
            const res = await fetch('/api/products-json');
            const data = await res.json();
            const fromCache = res.headers.get('X-Cache') === 'HIT';
            if (data.status === 'success') {
                currentProducts = data.products;
                currentPage = 1;
                updateKPIStats();
                renderProductList();
                // إظهار مؤشر الكاش
                const cacheIndicator = document.getElementById('cacheIndicator');
                if (cacheIndicator) {
                    cacheIndicator.title = fromCache ? '⚡ من الكاش (60 ث)' : '🔄 بيانات حية من Google Sheets';
                    cacheIndicator.style.color = fromCache ? 'var(--accent-cyan)' : 'var(--success)';
                }
            }
        } catch (err) {
            console.error(err);
            productList.innerHTML = '<p style="color: var(--danger); text-align: center; padding: 2rem;">فشل جلب المنتجات. تأكد من تشغيل Flask.</p>';
        }
    }

    // تحديث قسري من Google Sheets مع مسح الكاش
    async function refreshProducts() {
        const btn = document.getElementById('refreshBtn');
        if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>'; }
        await loadProducts(true);
        if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-sync-alt"></i>'; }
    }

    // تفريغ إحصائيات عداد الكروت الجانبية
    function updateKPIStats() {
        const total = currentProducts.length;
        const linked = currentProducts.filter(p => p.existing_image_link && p.existing_image_link.trim() !== '').length;
        const review = currentProducts.filter(p => p.needs_review).length;
        const errors = currentProducts.filter(p => p.has_error).length;
        const missing = total - linked - review - errors;
        
        document.getElementById('sheetProductCount').innerText = currentProducts.length;
        
        document.getElementById('tab-review').innerHTML = `المراجعة ⚠️ <span style="background: #ff9100; color: #080c14; padding: 2px 6px; border-radius: 8px; font-size: 0.75rem; margin-right: 4px; font-weight: 900;">${review}</span>`;
        document.getElementById('tab-errors').innerHTML = `أخطاء ❌ <span style="background: var(--danger); color: white; padding: 2px 6px; border-radius: 8px; font-size: 0.75rem; margin-right: 4px; font-weight: 900;">${errors}</span>`;
    }

    // فلترة المنتجات بناء على التبويب المختار
    function renderProductList() {
        const productList = document.getElementById('productList');
        const searchVal = document.getElementById('sidebarSearch').value.toLowerCase().trim();
        
        productList.innerHTML = '';
        
        const filtered = currentProducts.filter(prod => {
            const hasLink = prod.existing_image_link && prod.existing_image_link.trim() !== '';
            
            if (currentFilterTab === 'missing' && (hasLink || prod.needs_review || prod.has_error)) return false;
            if (currentFilterTab === 'linked' && !hasLink) return false;
            if (currentFilterTab === 'review' && !prod.needs_review) return false;
            if (currentFilterTab === 'errors' && !prod.has_error) return false;
            
            if (searchVal !== '') {
                const nameMatch = prod.product_name && prod.product_name.toLowerCase().includes(searchVal);
                const brandMatch = prod.brand && prod.brand.toLowerCase().includes(searchVal);
                return nameMatch || brandMatch;
            }
            
            return true;
        });
        
        if (filtered.length === 0) {
            productList.innerHTML = '<p style="color: var(--text-secondary); text-align: center; padding: 2rem;">لا توجد منتجات مطابقة.</p>';
            renderPagination(0);
            return;
        }

        // حساب التقسيم لصفحات
        const totalItems = filtered.length;
        const totalPages = Math.ceil(totalItems / itemsPerPage);
        if (currentPage > totalPages) {
            currentPage = totalPages || 1;
        }

        const startIndex = (currentPage - 1) * itemsPerPage;
        const endIndex = Math.min(startIndex + itemsPerPage, totalItems);
        const pageProducts = filtered.slice(startIndex, endIndex);
        
        pageProducts.forEach(prod => {
            const hasLink = prod.existing_image_link && prod.existing_image_link.trim() !== '';
            let linkIndicator = '';
            
            if (prod.has_error) {
                linkIndicator = `<span style="color: var(--danger); font-size: 0.8rem; font-weight: bold;" title="${prod.error_message || ''}"><i class="fas fa-exclamation-circle"></i> خطأ أتمتة ❌</span>`;
            } else if (prod.needs_review) {
                linkIndicator = `<span style="color: #ff9100; font-size: 0.8rem; font-weight: bold;"><i class="fas fa-exclamation-triangle"></i> مراجعة معلقة ⚠️</span>`;
            } else if (hasLink) {
                linkIndicator = `<span style="color: var(--success); font-size: 0.8rem; font-weight: bold;"><i class="fas fa-check"></i> رابط موجود</span>`;
            } else {
                linkIndicator = `<span style="color: var(--danger); font-size: 0.8rem; font-weight: bold;"><i class="fas fa-times"></i> بدون رابط</span>`;
            }
            
            const item = document.createElement('div');
            item.className = 'product-item';
            if (activeRowNumber === prod.row_number) item.classList.add('active');
            
            if (prod.has_error) {
                item.style.borderLeft = '4px solid var(--danger)';
                item.style.background = 'rgba(239, 68, 68, 0.02)';
            } else if (prod.needs_review) {
                item.style.borderLeft = '4px solid #ff9100';
            }
            
            let scoreBadge = '';
            if (prod.clip_score) {
                const scorePercent = Math.round(prod.clip_score * 100);
                scoreBadge = `<span class="score-badge" style="background: rgba(124, 58, 237, 0.1); border: 1px solid rgba(124, 58, 237, 0.2); color: #c084fc; font-size: 0.7rem; padding: 2px 4px; border-radius: 4px; font-weight: bold; margin-right: 5px;">${scorePercent}% Match</span>`;
            }

            item.innerHTML = `
                <span class="badge-row-number" style="${prod.has_error ? 'background-color: var(--danger); color: white;' : (prod.needs_review ? 'background-color: #ff9100; color: #080c14;' : '')}">صف ${prod.row_number}</span>
                <h4 style="margin-top: 0.5rem;">${prod.product_name}</h4>
                <p>
                    <span>البراند: <strong>${prod.brand}</strong></span>
                    ${linkIndicator}
                    ${scoreBadge}
                </p>
            `;
            
            item.onclick = () => selectProduct(prod, item);
            productList.appendChild(item);
        });

        renderPagination(totalItems);
    }

    // توليد واجهة أزرار التصفح لصفحات
    function renderPagination(totalItems) {
        const container = document.getElementById('paginationContainer');
        if (!container) return;
        
        const totalPages = Math.ceil(totalItems / itemsPerPage);
        if (totalPages <= 1) {
            container.style.display = 'none';
            return;
        }
        
        container.style.display = 'flex';
        
        const startItem = (currentPage - 1) * itemsPerPage + 1;
        const endItem = Math.min(currentPage * itemsPerPage, totalItems);
        
        container.innerHTML = `
            <button class="pagination-btn" id="prevPageBtn" ${currentPage === 1 ? 'disabled' : ''} onclick="changePage(${currentPage - 1})">
                <i class="fas fa-chevron-right"></i> السابق
            </button>
            <span class="pagination-info">
                ${startItem}-${endItem} من ${totalItems}
            </span>
            <button class="pagination-btn" id="nextPageBtn" ${currentPage === totalPages ? 'disabled' : ''} onclick="changePage(${currentPage + 1})">
                التالي <i class="fas fa-chevron-left"></i>
            </button>
        `;
    }

    function changePage(page) {
        currentPage = page;
        renderProductList();
        
        // تمرير القائمة لأعلى
        const productList = document.getElementById('productList');
        if (productList) {
            productList.scrollTop = 0;
        }
    }

    function filterProducts() {
        currentPage = 1;
        renderProductList();
    }

    function setFilterTab(tabName) {
        currentFilterTab = tabName;
        currentPage = 1;
        document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
        document.getElementById('tab-' + tabName).classList.add('active');
        renderProductList();
    }

    // تهيئة القوائم المنسدلة للـ Taxonomy
    function initTaxonomyDropdowns() {
        const selectL1 = document.getElementById('selectL1');
        selectL1.innerHTML = '<option value="">-- اختر التصنيف L1 --</option>';
        for (const l1 in taxonomyData) {
            const option = document.createElement('option');
            option.value = l1;
            option.innerText = `${l1} (${taxonomyData[l1].ar})`;
            selectL1.appendChild(option);
        }
    }

    function onL1Change() {
        const l1 = document.getElementById('selectL1').value;
        const selectL2 = document.getElementById('selectL2');
        const selectL3 = document.getElementById('selectL3');
        
        selectL2.innerHTML = '<option value="">-- اختر التصنيف L2 --</option>';
        selectL3.innerHTML = '<option value="">-- اختر التصنيف L3 --</option>';
        
        if (!l1 || !taxonomyData[l1]) return;
        
        const subs = taxonomyData[l1].subs;
        for (const l2 in subs) {
            const option = document.createElement('option');
            option.value = l2;
            option.innerText = `${l2} (${subs[l2].ar})`;
            selectL2.appendChild(option);
        }
    }

    function onL2Change() {
        const l1 = document.getElementById('selectL1').value;
        const l2 = document.getElementById('selectL2').value;
        const selectL3 = document.getElementById('selectL3');
        
        selectL3.innerHTML = '<option value="">-- اختر التصنيف L3 --</option>';
        
        if (!l1 || !l2 || !taxonomyData[l1] || !taxonomyData[l1].subs[l2]) return;
        
        const sub_subs = taxonomyData[l1].subs[l2].sub_subs;
        for (const l3 in sub_subs) {
            const option = document.createElement('option');
            option.value = l3;
            option.innerText = `${l3} (${sub_subs[l3]})`;
            selectL3.appendChild(option);
        }
    }

    function preselectTaxonomy(l1, l2, l3) {
        const selectL1 = document.getElementById('selectL1');
        const selectL2 = document.getElementById('selectL2');
        const selectL3 = document.getElementById('selectL3');
        
        let matchedL1 = findBestKeyMatch(l1, Object.keys(taxonomyData));
        if (matchedL1) {
            selectL1.value = matchedL1;
            onL1Change();
            
            let matchedL2 = findBestKeyMatch(l2, Object.keys(taxonomyData[matchedL1].subs));
            if (matchedL2) {
                selectL2.value = matchedL2;
                onL2Change();
                
                let matchedL3 = findBestKeyMatch(l3, Object.keys(taxonomyData[matchedL1].subs[matchedL2].sub_subs));
                if (matchedL3) {
                    selectL3.value = matchedL3;
                }
            }
        }
    }

    function findBestKeyMatch(val, list) {
        if (!val) return "";
        val = val.trim().toLowerCase();
        for (const k of list) {
            if (k.toLowerCase() === val) return k;
        }
        for (const k of list) {
            if (k.toLowerCase().includes(val) || val.includes(k.toLowerCase())) return k;
        }
        return list[0] || "";
    }

    // عند اختيار منتج من القائمة الجانبية
    function selectProduct(prod, element) {
        document.querySelectorAll('.product-item').forEach(el => el.classList.remove('active'));
        element.classList.add('active');
        
        document.getElementById('rowNumber').value = prod.row_number;
        document.getElementById('productName').value = prod.product_name;
        document.getElementById('brand').value = prod.brand;
        document.getElementById('productNameAr').value = prod.product_name_ar || '';
        document.getElementById('brandAr').value = prod.brand_ar || '';
        document.getElementById('customQuery').value = prod.search_query || '';
        document.getElementById('ignoreUnitClash').checked = false;
        document.getElementById('manualImageUrl').value = '';
        
        document.getElementById('searchForm').dataset.barcode = prod.barcode || '';
        document.getElementById('searchForm').dataset.category = prod.category || '';
        document.getElementById('searchForm').dataset.origin = prod.origin || '';
        
        activeRowNumber = prod.row_number;
        
        if (prod.needs_review && prod.needs_review_url) {
            const overallStatus = document.getElementById('overallStatus');
            overallStatus.innerText = 'مراجعة معلقة لصور المنطقة الرمادية';
            overallStatus.className = 'status-text active';
            document.getElementById('placeholder').style.display = 'none';
            document.getElementById('loading').style.display = 'none';
            document.getElementById('resultsContent').style.display = 'block';
            
            const imgObj = {
                url: prod.needs_review_url,
                title: "الصورة المقترحة في الفحص التلقائي (المنطقة الرمادية)",
                width: 800,
                height: 800
            };
            renderRecommendedCard(imgObj, prod.product_name, prod.brand, prod.row_number);
            initTaxonomyDropdowns();
            
            const recommendedContainer = document.getElementById('recommendedContainer');
            const warningDiv = document.createElement('div');
            warningDiv.style = "background-color: rgba(255, 145, 0, 0.1); border: 1px solid #ff9100; color: #ff9100; border-radius: 12px; padding: 1rem; margin-bottom: 1rem; font-weight: bold;";
            warningDiv.innerHTML = `⚠️ تم إيقاف الصورة للمراجعة البصرية لأن تقييم مطابقتها الدلالية CLIP كان متوسطاً. يرجى مراجعة الصورة واعتمادها أو البحث عن غيرها بالزر بالأسفل.`;
            recommendedContainer.insertBefore(warningDiv, recommendedContainer.firstChild);
            
            document.getElementById('candidatesContainer').innerHTML = '<p style="color: var(--text-secondary); text-align: center; padding: 2rem;">اضغط على زر البحث بالأسفل للاستعلام يدوياً وجلب صور بديلة.</p>';
            document.getElementById('accordionContainer').innerHTML = '';
        } else {
            document.getElementById('submitBtn').click();
        }
    }

    // إرسال استعلام الفحص البصري والبحث
    document.getElementById('searchForm').addEventListener('submit', async function(e) {
        e.preventDefault();
        
        const placeholder = document.getElementById('placeholder');
        const loading = document.getElementById('loading');
        const resultsContent = document.getElementById('resultsContent');
        const overallStatus = document.getElementById('overallStatus');
        
        const row = document.getElementById('rowNumber').value;
        const name = document.getElementById('productName').value;
        const brand = document.getElementById('brand').value;
        const customQuery = document.getElementById('customQuery').value;
        const ignoreUnitClash = document.getElementById('ignoreUnitClash').checked;
        const strictBrandMatch = document.getElementById('strictBrandMatch').checked;
        const skipCache = document.getElementById('skipCache') ? document.getElementById('skipCache').checked : false;
        
        const barcode = document.getElementById('searchForm').dataset.barcode;
        const category = document.getElementById('searchForm').dataset.category;
        const origin = document.getElementById('searchForm').dataset.origin;
        
        placeholder.style.display = 'none';
        resultsContent.style.display = 'none';
        loading.style.display = 'flex';
        overallStatus.innerText = 'جاري المعالجة...';
        overallStatus.className = 'status-text active';
        
        try {
            const res = await fetch('/api/search', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content
                },
                body: JSON.stringify({
                    product_name: name,
                    brand: brand,
                    custom_query: customQuery,
                    ignore_unit_clash: ignoreUnitClash,
                    strict_brand_match: strictBrandMatch,
                    skip_cache: skipCache,
                    barcode: barcode,
                    category: category,
                    origin: origin,
                    row_number: row
                })
            });
            const data = await res.json();
            loading.style.display = 'none';
            
            if (data.status === 'success' && data.selected_image) {
                resultsContent.style.display = 'block';
                overallStatus.innerText = 'تم المطابقة والعثور على صورة';
                overallStatus.className = 'status-text success';
                
                renderRecommendedCard(data.selected_image, name, data.brand || brand, row);
                renderCandidatesGrid(data.trace, name, data.brand || brand, row);
                renderAccordionTrace(data.trace);
                
                // Preselect Taxonomy from AI extraction
                if (data.selected_image.metadata) {
                    preselectTaxonomy(
                        data.selected_image.metadata.category_l1_en,
                        data.selected_image.metadata.category_l2_en,
                        data.selected_image.metadata.category_l3_en
                    );
                } else {
                    initTaxonomyDropdowns();
                }
            } else {
                overallStatus.innerText = 'فشل العثور على صورة مناسبة';
                overallStatus.className = 'status-text failed';
                alert('⚠️ لم يتم العثور على صورة تطابق معايير القبول والجودة البصرية للبراند.');
                placeholder.style.display = 'block';
            }
        } catch (err) {
            console.error(err);
            loading.style.display = 'none';
            placeholder.style.display = 'block';
            overallStatus.innerText = 'خطأ اتصال';
            overallStatus.className = 'status-text failed';
        }
    });

    // رندرة بطاقة الصورة الموصى بها كرت كبير (Visual Curation Workbench)
    function renderRecommendedCard(img, name, brand, row) {
        const container = document.getElementById('recommendedContainer');
        container.innerHTML = '';
        
        const card = document.createElement('div');
        card.className = 'glass-panel';
        card.style.margin = '0 0 1.5rem 0';
        card.style.padding = '1.5rem';
        
        const scoreInfo = img.clip_score 
            ? `<div style="font-family: 'Outfit', sans-serif; font-size: 0.9rem; margin-top: 0.25rem;">تشابه بصرى CLIP: <strong style="color: var(--accent-cyan); font-size: 1.15rem;">${img.clip_score}</strong></div>`
            : '';
            
        const isGreyArea = img.needs_review ? `<div class="score-badge" style="background: #ff9100; color: #080c14; margin-top: 0.5rem; display: inline-block;">⚠️ مراجعة المنطقة الرمادية</div>` : '';
        const semanticCacheBadge = img.semantic_similarity
            ? `<div class="score-badge" style="background: linear-gradient(135deg, #00f5ff 0%, #00b0ff 100%); color: #080c14; font-weight: 900; margin-top: 0.5rem; display: inline-block;"><i class="fas fa-bolt"></i> كاش دلالي ذكي</div>`
            : '';

        // Generate the markup with split-screen workbench
        card.innerHTML = `
            <div class="workbench-grid">
                <!-- Column 1: Draggable Slide compare -->
                <div style="display: flex; flex-direction: column; gap: 0.75rem; align-items: center;">
                    <div class="compare-container bg-checkerboard" id="compareContainer">
                        <!-- Layer 1 Underneath (Raw/Original image) -->
                        <div class="compare-img">
                            <img src="${getImageUrl(img.url)}" alt="Original Raw Source">
                        </div>
                        <!-- Layer 2 Overlay (Padded/Isolated view) -->
                        <div class="compare-overlay" id="compareOverlay">
                            <div class="compare-img">
                                <img src="${getImageUrl(img.url)}" alt="Isolated Target Layout">
                            </div>
                        </div>
                        <!-- Draggable divider handle -->
                        <div class="compare-handle" id="compareHandle"></div>
                    </div>
                    <!-- Background switcher toolbar -->
                    <div style="display: flex; gap: 0.5rem; width: 100%; justify-content: center;">
                        <button type="button" class="btn btn-secondary" onclick="switchPreviewBg('checkerboard')" style="flex: 1; font-size: 0.75rem; padding: 0.35rem 0.5rem;"><i class="fas fa-chess-board"></i> الشفافية</button>
                        <button type="button" class="btn btn-secondary" onclick="switchPreviewBg('white')" style="flex: 1; font-size: 0.75rem; padding: 0.35rem 0.5rem;"><i class="fas fa-square" style="color: #fff;"></i> أبيض</button>
                        <button type="button" class="btn btn-secondary" onclick="switchPreviewBg('gray')" style="flex: 1; font-size: 0.75rem; padding: 0.35rem 0.5rem;"><i class="fas fa-square" style="color: #888;"></i> رمادي</button>
                    </div>
                </div>

                <!-- Column 2: Control and Active Learning validation details -->
                <div style="display: flex; flex-direction: column; justify-content: space-between;">
                    <div>
                        <div style="display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap;">
                            <span class="score-badge" style="background: linear-gradient(135deg, #8b5cf6 0%, #3b82f6 100%); color: #fff;"><i class="fas fa-robot"></i> التقييم التلقائي</span>
                            ${isGreyArea}
                            ${semanticCacheBadge}
                        </div>
                        <h3 style="font-size: 1.2rem; font-weight: 800; margin-top: 0.75rem; line-height: 1.45;">${name}</h3>
                        <p style="color: var(--text-secondary); font-size: 0.85rem; margin-top: 0.15rem;">العلامة التجارية: <strong style="color: var(--text-primary);">${brand}</strong></p>
                        ${scoreInfo}
                        
                        <!-- Manual Override controls -->
                        <div style="margin-top: 1rem; border-top: 1px solid var(--panel-border); padding-top: 0.75rem;">
                            <label style="font-size: 0.8rem; font-weight: 600; color: var(--text-secondary); display: block; margin-bottom: 0.35rem;">خوارزمية تفريغ وعزل الخلفية:</label>
                            <select id="bgRemovalMethod" style="padding: 0.45rem 0.75rem; background: var(--input-bg); border: 1px solid var(--panel-border); border-radius: 6px; color: var(--text-primary); font-family: inherit; font-size: 0.8rem; width: 100%;">
                                <option value="bria">Bria RMBG v2 (ذكاء اصطناعي فائق)</option>
                                <option value="rembg">Rembg Local (محلي افتراضي)</option>
                                <option value="grabcut">GrabCut (قص مستطيل يدوي)</option>
                                <option value="none">بدون عزل (استخدام الصورة كما هي)</option>
                            </select>
                        </div>

                        <!-- Active Learning Rejection feedback -->
                        <div style="margin-top: 0.75rem; border-top: 1px solid var(--panel-border); padding-top: 0.75rem;">
                            <label style="font-size: 0.8rem; font-weight: 600; color: var(--text-secondary); display: block; margin-bottom: 0.35rem;">أسباب الرفض (لتدريب الذكاء الاصطناعي في حال الاستبعاد):</label>
                            <div class="feedback-reasons-grid">
                                <label class="feedback-checkbox">
                                    <input type="checkbox" name="rejection_reason" value="HALO_ARTIFACT">
                                    <span>هالة لونية متبقية</span>
                                </label>
                                <label class="feedback-checkbox">
                                    <input type="checkbox" name="rejection_reason" value="BACKGROUND_BLEED">
                                    <span>تداخل الخلفية</span>
                                </label>
                                <label class="feedback-checkbox">
                                    <input type="checkbox" name="rejection_reason" value="CROP_MARGIN_CLIPPING">
                                    <span>قص جائر بالأطراف</span>
                                </label>
                                <label class="feedback-checkbox">
                                    <input type="checkbox" name="rejection_reason" value="BRAND_STYLE_MISMATCH">
                                    <span>مخالفة هوية البراند</span>
                                </label>
                            </div>
                        </div>
                    </div>

                    <!-- Action buttons -->
                    <div style="display: flex; gap: 1rem; margin-top: 1.25rem; border-top: 1px solid var(--panel-border); padding-top: 1rem;">
                        <button type="button" class="btn" id="confirmImageBtn" onclick="confirmRecommendedImage('${img.url}', '${name.replace(/'/g, "\\'")}', '${brand.replace(/'/g, "\\'")}', ${row}, this)" style="flex: 2; font-weight: 800;">
                            <i class="fas fa-check"></i> اعتماد الصورة للشيت والرفع [A]
                        </button>
                        <button type="button" class="btn" onclick="rejectImage('${img.url}', '${name.replace(/'/g, "\\'")}', '${brand.replace(/'/g, "\\'")}', ${row}, this)" style="flex: 1; background: rgba(239, 68, 68, 0.08); border-color: rgba(239, 68, 68, 0.25); color: var(--danger);" onmouseover="this.style.background='var(--danger)'; this.style.color='#fff'" onmouseout="this.style.background='rgba(239, 68, 68, 0.08)'; this.style.color='var(--danger)'">
                            <i class="fas fa-times"></i> استبعاد [X]
                        </button>
                    </div>
                </div>
            </div>
        `;
        
        container.appendChild(card);
        initSlideCompare(card);
    }

    // تهيئة شريط التمرير الانزلاقي للمقارنة
    function initSlideCompare(card) {
        const handle = card.querySelector('#compareHandle');
        const overlay = card.querySelector('#compareOverlay');
        const container = card.querySelector('#compareContainer');
        if (!handle || !overlay || !container) return;
        
        let isDragging = false;
        
        function moveSlider(clientX) {
            const rect = container.getBoundingClientRect();
            let x = clientX - rect.left;
            if (x < 0) x = 0;
            if (x > rect.width) x = rect.width;
            
            const percent = (x / rect.width) * 100;
            handle.style.left = percent + '%';
            overlay.style.width = percent + '%';
        }
        
        handle.addEventListener('mousedown', () => { isDragging = true; });
        window.addEventListener('mouseup', () => { isDragging = false; });
        window.addEventListener('mousemove', (e) => {
            if (!isDragging) return;
            moveSlider(e.clientX);
        });
        
        // دعم شاشات اللمس الهواتف والتابلت
        handle.addEventListener('touchstart', () => { isDragging = true; });
        window.addEventListener('touchend', () => { isDragging = false; });
        window.addEventListener('touchmove', (e) => {
            if (!isDragging) return;
            moveSlider(e.touches[0].clientX);
        });
    }

    // تبديل خلفيات لوحة المعاينة
    function switchPreviewBg(type) {
        const compareContainer = document.getElementById('compareContainer');
        if (!compareContainer) return;
        compareContainer.className = 'compare-container';
        if (type === 'checkerboard') {
            compareContainer.classList.add('bg-checkerboard');
        } else if (type === 'white') {
            compareContainer.classList.add('bg-white');
        } else if (type === 'gray') {
            compareContainer.classList.add('bg-gray');
        }
    }

    // اعتماد الصورة للشيت
    async function confirmRecommendedImage(url, name, brand, row, btn) {
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> جاري تحديث الصف والكاش...';
        
        const l1 = document.getElementById('selectL1').value;
        const l2 = document.getElementById('selectL2').value;
        const l3 = document.getElementById('selectL3').value;
        const aiUpscale = document.getElementById('aiUpscale') ? document.getElementById('aiUpscale').checked : true;
        const aiEnhance = document.getElementById('aiEnhance') ? document.getElementById('aiEnhance').checked : false;
        const bgRemovalMethod = document.getElementById('bgRemovalMethod') ? document.getElementById('bgRemovalMethod').value : 'bria';
        
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
                    barcode: document.getElementById('searchForm').dataset.barcode,
                    category_l1: l1,
                    category_l2: l2,
                    category_l3: l3,
                    upscale: aiUpscale,
                    enhance: aiEnhance,
                    bg_removal_method: bgRemovalMethod,
                    target_width:  getOutputWidth(),
                    target_height: getOutputHeight(),
                    padding_ratio: parseFloat(document.getElementById('paddingRatio').value),
                    bg_color:      document.getElementById('bgColor').value
                })
            });
            const data = await res.json();
            btn.disabled = false;
            btn.innerHTML = originalText;
            
            if (data.status === 'success') {
                alert(`🎉 تم رفع الصورة وتحديث الصف ${row} بنجاح!`);
                loadProducts();
            } else {
                alert(`❌ فشل الرفع: ${data.error}`);
            }
        } catch (err) {
            console.error(err);
            btn.disabled = false;
            btn.innerHTML = originalText;
            alert('❌ خطأ اتصال بالخادم.');
        }
    }

    // استبعاد ورفض الصورة وتسجيل التغذية الراجعة للتعلم النشط
    async function rejectImage(url, name, brand, row, btn) {
        const checkedReasons = Array.from(document.querySelectorAll('input[name="rejection_reason"]:checked')).map(cb => cb.value);
        if (checkedReasons.length === 0) {
            alert('❌ يرجى تحديد سبب واحد للرفض على الأقل لتسجيل التغذية الراجعة للتعلم النشط.');
            return;
        }
        
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> جاري التسجيل...';
        
        try {
            const res = await fetch('/api/reject_image', {
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
                    rejection_reasons: checkedReasons
                })
            });
            const data = await res.json();
            btn.disabled = false;
            btn.innerHTML = originalText;
            
            if (data.status === 'success') {
                alert('🚫 تم تسجيل الرفض وحفظ التغذية الراجعة لتدريب مصنف الذكاء الاصطناعي بنجاح!');
                loadProducts();
            } else {
                alert('❌ فشل تسجيل الرفض: ' + data.error);
            }
        } catch (err) {
            console.error(err);
            btn.disabled = false;
            btn.innerHTML = originalText;
            alert('❌ خطأ اتصال بالخادم.');
        }
    }

    // رندرة شبكة الصور المرشحة المفحوصة
    function renderCandidatesGrid(trace, productName, brand, rowNumber) {
        const container = document.getElementById('candidatesContainer');
        container.innerHTML = '';
        
        let allCandidates = [];
        let seenUrls = new Set();
        
        if (trace && trace.steps) {
            trace.steps.forEach(step => {
                if (step.candidates) {
                    step.candidates.forEach(c => {
                        if (!seenUrls.has(c.url)) {
                            seenUrls.add(c.url);
                            allCandidates.push(c);
                        }
                    });
                }
            });
        }
        
        if (allCandidates.length === 0) {
            container.innerHTML = '<p style="color: var(--text-secondary); grid-column: 1/-1; text-align: center; padding: 2rem;">لا توجد صور مرشحة مفحوصة.</p>';
            return;
        }
        
        allCandidates.forEach(c => {
            const card = document.createElement('div');
            card.className = 'candidate-card';
            
            const statusClass = c.status === 'accepted' ? 'accepted' : 'rejected';
            const statusText = c.status === 'accepted' ? 'مقبولة تلقائياً' : 'مستبعدة تلقائياً';
            const scoreTag = c.scores && c.scores.relevance_score !== undefined 
                ? `<div class="candidate-score-tag">صلة: ${c.scores.relevance_score}</div>` 
                : '';
            const uaeTag = c.scores && c.scores.is_uae_source 
                ? `<div class="candidate-uae-tag">العمارات 🇦🇪</div>` 
                : '';
            const reasonsText = c.reasons && c.reasons.length > 0 
                ? `<div class="candidate-reasons">${c.reasons.map(r => `• ${r}`).join('<br>')}</div>` 
                : '';
            const actionButton = rowNumber 
                ? `<button class="btn btn-secondary btn-sm" style="width: 100%; font-weight: bold; margin-top: 0.5rem;" onclick="confirmRecommendedImage('${c.url}', '${productName.replace(/'/g, "\\'")}', '${brand.replace(/'/g, "\\'")}', ${rowNumber}, this)">🎯 اعتمد يدوياً</button>` 
                : '';
            
            card.innerHTML = `
                <div class="candidate-img-box">
                    <span class="candidate-badge ${statusClass}">${statusText}</span>
                    <img src="${getImageUrl(c.url)}" alt="Candidate" onerror="this.src='https://placehold.co/180x180?text=Error'">
                    ${scoreTag}
                    ${uaeTag}
                </div>
                <div class="candidate-info">
                    <div class="candidate-title" title="${c.title || ''}">${c.title || 'بدون عنوان'}</div>
                    <div class="candidate-meta">
                        <span>الأبعاد: <strong>${c.width}x${c.height}</strong></span>
                    </div>
                    ${reasonsText}
                    ${actionButton}
                </div>
            `;
            container.appendChild(card);
        });
    }

    // رندرة أكورديون سجل الخطوات التتبع
    function renderAccordionTrace(trace) {
        const container = document.getElementById('accordionContainer');
        container.innerHTML = '';
        
        if (!trace || !trace.steps) {
            container.innerHTML = '<p style="color: var(--text-secondary); text-align: center;">لا يوجد سجل تتبع متاح.</p>';
            return;
        }
        
        trace.steps.forEach((step, idx) => {
            const item = document.createElement('div');
            item.className = 'step-item';
            
            const resultsCount = step.results_count !== undefined ? step.results_count : (step.candidates ? step.candidates.length : 0);
            
            item.innerHTML = `
                <div class="step-header" onclick="toggleAccordionItem(this)">
                    <span>الخطوة ${idx + 1}: ${step.name} (${resultsCount} نتائج)</span>
                    <i class="fas fa-chevron-down"></i>
                </div>
                <div class="step-body">
                    <pre style="color: var(--accent-cyan); font-size: 0.8rem; overflow-x: auto; font-family: monospace; white-space: pre-wrap; direction: ltr; text-align: left;">${JSON.stringify(step, null, 2)}</pre>
                </div>
            `;
            container.appendChild(item);
        });
    }

    function toggleAccordionItem(header) {
        const body = header.nextElementSibling;
        const icon = header.querySelector('i');
        body.classList.toggle('active');
        if (body.classList.contains('active')) {
            icon.className = 'fas fa-chevron-up';
        } else {
            icon.className = 'fas fa-chevron-down';
        }
    }

    // معاينة رابط صورة مدخل يدوياً
    function previewManualImage() {
        const url = document.getElementById('manualImageUrl').value.trim();
        if (!url) {
            alert('يرجى إدخال رابط الصورة أولاً.');
            return;
        }
        const name = document.getElementById('productName').value;
        const brand = document.getElementById('brand').value;
        const row = document.getElementById('rowNumber').value;
        
        const tempImgObj = {
            url: url,
            title: "صورة مدخلة يدوياً بواسطة المستخدم",
            width: "Unknown",
            height: "Unknown"
        };
        renderRecommendedCard(tempImgObj, name, brand, row);
    }

    // ==========================================
    // 🛠️ DRAG AND DROP & CANVAS EDITOR & LIVE LOGS FUNCTIONS
    // ==========================================
    let uploadFile = null;
    let rotationAngle = 0;
    let flipHorizontal = false;
    let currentImageObject = null;
    let lastLogs = [];

    function triggerFileInput() {
        document.getElementById('manualFileInput').click();
    }

    function handleFileSelect(e) {
        const file = e.target.files[0];
        if (file) openEditor(file);
    }

    function handleFileDrop(e) {
        e.preventDefault();
        const file = e.dataTransfer.files[0];
        if (file) openEditor(file);
    }

    function openEditor(file) {
        uploadFile = file;
        rotationAngle = 0;
        flipHorizontal = false;
        
        const reader = new FileReader();
        reader.onload = function(event) {
            const img = new Image();
            img.onload = function() {
                currentImageObject = img;
                renderCanvas();
                document.getElementById('editorModal').style.display = 'flex';
            };
            img.src = event.target.result;
        };
        reader.readAsDataURL(file);
    }

    function renderCanvas() {
        if (!currentImageObject) return;
        const canvas = document.getElementById('editorCanvas');
        const ctx = canvas.getContext('2d');
        
        const angle = rotationAngle % 360;
        const is90or270 = angle === 90 || angle === 270;
        
        const w = is90or270 ? currentImageObject.height : currentImageObject.width;
        const h = is90or270 ? currentImageObject.width : currentImageObject.height;
        
        const maxDim = 400;
        let scale = 1;
        if (w > maxDim || h > maxDim) {
            scale = maxDim / Math.max(w, h);
        }
        
        canvas.width = w * scale;
        canvas.height = h * scale;
        
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.save();
        
        ctx.translate(canvas.width / 2, canvas.height / 2);
        ctx.rotate((angle * Math.PI) / 180);
        
        if (flipHorizontal) {
            ctx.scale(-1, 1);
        }
        
        const dw = currentImageObject.width * scale;
        const dh = currentImageObject.height * scale;
        ctx.drawImage(currentImageObject, -dw / 2, -dh / 2, dw, dh);
        
        ctx.restore();
    }

    function editorRotate() {
        rotationAngle = (rotationAngle + 90) % 360;
        renderCanvas();
    }

    function editorFlip() {
        flipHorizontal = !flipHorizontal;
        renderCanvas();
    }

    function closeEditorModal() {
        document.getElementById('editorModal').style.display = 'none';
        document.getElementById('manualFileInput').value = '';
    }

    // اعتماد رفع الصورة المصححة Canvas
    async function commitEditorUpload() {
        if (!uploadFile) return;
        
        const row = document.getElementById('rowNumber').value;
        const name = document.getElementById('productName').value;
        const brand = document.getElementById('brand').value;
        const barcode = document.getElementById('searchForm').dataset.barcode || '';
        
        if (!row) {
            alert('يرجى اختيار منتج من الشيت أولاً للرفع عليه.');
            return;
        }
        
        const canvas = document.getElementById('editorCanvas');
        canvas.toBlob(async function(blob) {
            const formData = new FormData();
            const aiUpscale = document.getElementById('aiUpscale') ? document.getElementById('aiUpscale').checked : true;
            const aiEnhance = document.getElementById('aiEnhance') ? document.getElementById('aiEnhance').checked : false;
            formData.append('file', blob, uploadFile.name);
            formData.append('row_number', row);
            formData.append('product_name', name);
            formData.append('brand', brand);
            formData.append('barcode', barcode);
            formData.append('upscale', aiUpscale ? 'true' : 'false');
            formData.append('enhance', aiEnhance ? 'true' : 'false');
            formData.append('target_width',  getOutputWidth());
            formData.append('target_height', getOutputHeight());
            formData.append('padding_ratio', document.getElementById('paddingRatio').value);
            formData.append('bg_color',      document.getElementById('bgColor').value);
            
            closeEditorModal();
            
            document.getElementById('placeholder').style.display = 'none';
            document.getElementById('resultsContent').style.display = 'none';
            const loading = document.getElementById('loading');
            loading.style.display = 'flex';
            document.getElementById('loadingDetails').innerText = 'جاري عزل خلفية الصورة المرفوعة، تحجيمها وتطبيق الظلال سحابياً...';
            
            try {
                const res = await fetch('/api/upload_manual_image', {
                    method: 'POST',
                    headers: { 'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content },
                    body: formData
                });
                const data = await res.json();
                if (data.status === 'success') {
                    alert('🎉 تم معالجة ورفع الصورة وتحديث الشيت بنجاح!');
                    loadProducts();
                    
                    const imgObj = {
                        url: data.image_link,
                        title: "الصورة المرفوعة والمعالجة يدوياً",
                        width: 800,
                        height: 800
                    };
                    document.getElementById('loading').style.display = 'none';
                    document.getElementById('resultsContent').style.display = 'block';
                    renderRecommendedCard(imgObj, name, brand, row);
                } else {
                    alert('❌ فشل معالجة الصورة: ' + (data.error || 'خطأ غير معروف'));
                    document.getElementById('loading').style.display = 'none';
                    document.getElementById('placeholder').style.display = 'block';
                }
            } catch (err) {
                console.error(err);
                alert('❌ حدث خطأ أثناء الاتصال بالخادم.');
                document.getElementById('loading').style.display = 'none';
                document.getElementById('placeholder').style.display = 'block';
            }
        }, 'image/png');
    }

    // سحب السجلات الحية بشكل دوري
    async function pollLiveLogs() {
        try {
            const res = await fetch('/api/logs');
            const data = await res.json();
            if (data.logs) {
                const consoleDiv = document.getElementById('liveConsoleLogs');
                if (JSON.stringify(data.logs) !== JSON.stringify(lastLogs)) {
                    lastLogs = data.logs;
                    consoleDiv.innerHTML = data.logs.map(log => {
                        let style = 'color: #00ff66;';
                        if (log.includes('❌')) style = 'color: #ef4444;';
                        if (log.includes('⚠️')) style = 'color: #ff9100;';
                        if (log.includes('⚡') || log.includes('🎉')) style = 'color: #00e5ff;';
                        return `<p style="${style} margin: 0; padding: 2px 0;">${log}</p>`;
                    }).join('');
                    consoleDiv.scrollTop = consoleDiv.scrollHeight;
                }
            }
        } catch (err) {
            console.error("Error polling logs:", err);
        }
    }

    function clearLiveConsoleLogs() {
        document.getElementById('liveConsoleLogs').innerHTML = '<p style="color: var(--text-secondary);">[System] Console cleared.</p>';
        lastLogs = [];
    }

    setInterval(pollLiveLogs, 2000);

    // =============================================
    // 🎛️ Image Output Settings Helpers
    // =============================================
    function applyPreset() {
        const preset = document.getElementById('outputPreset').value;
        const box = document.getElementById('customDimBox');
        box.style.display = preset === 'custom' ? 'block' : 'none';
    }

    function applyBgColor() {
        const val = document.getElementById('bgColorPreset').value;
        document.getElementById('bgColor').value = val;
        document.getElementById('bgColorPicker').value = val === 'transparent' ? '#ffffff' : ('#' + val);
    }

    function getOutputWidth() {
        const preset = document.getElementById('outputPreset').value;
        if (preset === 'dynamic') return 0;
        if (preset === 'custom') return parseInt(document.getElementById('customWidth').value) || 800;
        return parseInt(preset.split('x')[0]);
    }

    function getOutputHeight() {
        const preset = document.getElementById('outputPreset').value;
        if (preset === 'dynamic') return 0;
        if (preset === 'custom') return parseInt(document.getElementById('customHeight').value) || 800;
        return parseInt(preset.split('x')[1]);
    }

    // مستمع اختصارات لوحة المفاتيح والفرز الحركي والسريع لزيادة الإنتاجية
    window.addEventListener('keydown', (e) => {
        const activeTag = document.activeElement.tagName.toLowerCase();
        if (activeTag === 'input' || activeTag === 'textarea' || activeTag === 'select') {
            return;
        }
        
        const key = e.key.toLowerCase();
        
        // [A] الاعتماد السريع
        if (key === 'a') {
            e.preventDefault();
            const confirmBtn = document.getElementById('confirmImageBtn');
            if (confirmBtn && !confirmBtn.disabled) {
                confirmBtn.click();
            }
        }
        
        // [X] الرفض السريع
        if (key === 'x') {
            e.preventDefault();
            const rejectBtn = document.querySelector('button[onclick^="rejectImage"]');
            if (rejectBtn && !rejectBtn.disabled) {
                rejectBtn.click();
            }
        }
        
        // [Space] تخطي مؤقت والذهاب للمنتج التالي
        if (e.key === ' ' || e.code === 'Space') {
            e.preventDefault();
            const activeItem = document.querySelector('.product-item.active');
            if (activeItem) {
                const nextItem = activeItem.nextElementSibling;
                if (nextItem && nextItem.classList.contains('product-item')) {
                    nextItem.click();
                    nextItem.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }
            }
        }
        
        // [Q] إعادة تحديث من الكواش أو الشيت
        if (key === 'q') {
            e.preventDefault();
            const refreshBtn = document.getElementById('refreshBtn');
            if (refreshBtn && !refreshBtn.disabled) {
                refreshBtn.click();
            }
        }
        
        // مفاتيح الأرقام لاعتماد الصور المرشحة مباشرة من الشبكة (Choice Auto-Accept)
        if (key >= '1' && key <= '9') {
            const candidates = document.querySelectorAll('#candidatesContainer .candidate-card');
            const idx = parseInt(key) - 1;
            if (candidates && candidates[idx]) {
                const actionBtn = candidates[idx].querySelector('button[onclick^="confirmRecommendedImage"]');
                if (actionBtn && !actionBtn.disabled) {
                    e.preventDefault();
                    actionBtn.click();
                }
            }
        }
    });
</script>
@endsection
