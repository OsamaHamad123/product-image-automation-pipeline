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
        transition: grid-template-columns 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }

    .layout-grid.batch-mode-active {
        grid-template-columns: 1fr;
    }

    .layout-grid.batch-mode-active .sidebar-panel {
        display: none !important;
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
        margin-bottom: 1.25rem;
    }

    .sidebar-search-container input {
        width: 100%;
        padding: 0.75rem 2.5rem 0.75rem 1rem;
        background: var(--input-bg);
        border: 1px solid var(--panel-border);
        border-radius: 12px;
        color: var(--text-primary);
        font-family: inherit;
        font-size: 0.9rem;
        outline: none;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        text-align: right;
    }

    .sidebar-search-container input:focus {
        border-color: var(--accent-purple);
        box-shadow: 0 0 15px var(--btn-shadow);
    }

    .sidebar-search-container i {
        position: absolute;
        right: 1rem;
        left: auto;
        top: 50%;
        transform: translateY(-50%);
        color: var(--text-secondary);
        font-size: 0.95rem;
        transition: color 0.3s;
    }

    .sidebar-search-container input:focus ~ i {
        color: var(--accent-purple);
    }

    .tabs-nav {
        display: flex;
        flex-wrap: wrap;
        background: var(--tabs-bg);
        border: 1px solid var(--panel-border);
        padding: 4px;
        border-radius: 12px;
        gap: 4px;
        margin-bottom: 1rem;
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
        border-radius: 8px;
        cursor: pointer;
        transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
        text-align: center;
    }

    .tab-btn:hover {
        color: var(--text-primary);
        background: rgba(255, 255, 255, 0.05);
    }

    .tab-btn.active {
        background: var(--accent-gradient);
        color: var(--btn-text);
        box-shadow: 0 4px 15px var(--btn-shadow);
        text-shadow: 0 1px 2px rgba(0,0,0,0.15);
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
        border-inline-start: 4px solid transparent;
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
    }
    .product-item.completed { border-inline-start-color: var(--success); }
    .product-item.review { border-inline-start-color: var(--warning); }
    .product-item.error { border-inline-start-color: var(--danger); }
    .product-item.missing { border-inline-start-color: var(--info); }

    .product-item:hover {
        border-color: var(--panel-border-hover);
        background: var(--card-bg-hover);
        transform: translateY(-2px);
        box-shadow: var(--shadow-sm), 0 4px 15px var(--btn-shadow);
    }

    .product-item.completed.active { border-color: var(--success); background: var(--active-menu-bg); box-shadow: 0 0 15px var(--btn-shadow); }
    .product-item.review.active { border-color: var(--warning); background: var(--active-menu-bg); box-shadow: 0 0 15px var(--btn-shadow); }
    .product-item.error.active { border-color: var(--danger); background: var(--active-menu-bg); box-shadow: 0 0 15px var(--btn-shadow); }
    .product-item.missing.active { border-color: var(--accent-purple); background: var(--active-menu-bg); box-shadow: 0 0 15px var(--btn-shadow); }

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
        border-radius: 12px;
        color: var(--text-primary);
        font-family: inherit;
        font-size: 0.9rem;
        outline: none;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }

    .form-group input:focus, .form-group select:focus {
        border-color: var(--accent-purple);
        box-shadow: 0 0 15px var(--btn-shadow);
        background: rgba(255, 255, 255, 0.02);
    }

    #bgColorPicker {
        width: 38px;
        height: 38px;
        border-radius: 50% !important;
        border: 2px solid var(--panel-border) !important;
        padding: 0;
        cursor: pointer;
        background: none;
        overflow: hidden;
        transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    }

    #bgColorPicker:hover {
        transform: scale(1.1);
        border-color: var(--accent-purple) !important;
        box-shadow: 0 0 10px var(--btn-shadow);
    }

    /* Toggles */
    .toggles-row {
        display: flex;
        flex-wrap: wrap;
        gap: 1rem;
        align-items: center;
        height: 100%;
    }

    .toggle-container {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        padding: 0.5rem 0.85rem;
        background: rgba(255, 255, 255, 0.02);
        border: 1px solid var(--panel-border);
        border-radius: 12px;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        cursor: pointer;
    }

    .toggle-container:hover {
        border-color: var(--panel-border-hover);
        background: rgba(255, 255, 255, 0.04);
    }

    .toggle-container:has(input:checked) {
        border-color: var(--panel-border-hover);
        background: var(--success-bg);
        box-shadow: 0 4px 15px var(--btn-shadow);
    }

    .toggle-container span {
        font-size: 0.85rem;
        font-weight: 700;
        color: var(--text-secondary);
        transition: color 0.3s;
        user-select: none;
    }

    .toggle-container:has(input:checked) span {
        color: var(--text-primary);
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
        background: var(--accent-gradient);
        box-shadow: 0 0 10px var(--btn-shadow);
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
        box-shadow: var(--shadow-md), 0 8px 24px var(--btn-shadow);
    }

    .candidate-card.confirming-active, .glass-panel.confirming-active {
        border-color: var(--accent-purple) !important;
        box-shadow: 0 0 20px var(--btn-shadow) !important;
        animation: pulse-glowing-glow 1.5s infinite alternate;
    }

    @keyframes pulse-glowing-glow {
        0% { box-shadow: 0 0 10px var(--btn-shadow); }
        100% { box-shadow: 0 0 25px var(--btn-hover-shadow); }
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
        border: 1px solid var(--panel-border);
        color: var(--success);
    }
    .candidate-badge.rejected { 
        background: var(--danger-bg); 
        border: 1px solid var(--panel-border);
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
        border: 1px solid var(--panel-border);
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
        border: 1px solid var(--panel-border);
        padding: 0.5rem 0.75rem;
        border-radius: var(--border-radius-sm);
        margin-top: 0.25rem;
        line-height: 1.5;
        font-weight: 700;
    }

    .candidate-badge.eligible {
        background: var(--warning-bg);
        border: 1px solid var(--panel-border);
        color: var(--warning);
    }
    .candidate-badge-inline {
        padding: 0.2rem 0.65rem;
        border-radius: var(--border-radius-sm);
        font-size: 0.75rem;
        font-weight: 800;
        border: 1px solid var(--panel-border);
    }
    .candidate-badge-inline.accepted { background: var(--success-bg); color: var(--success); }
    .candidate-badge-inline.eligible { background: var(--warning-bg); color: var(--warning); }
    .candidate-badge-inline.rejected { background: var(--danger-bg); color: var(--danger); }
    .candidate-reasons.neutral {
        color: var(--text-secondary);
        background: var(--input-bg);
    }
    /* تحذيرات المراجعة على الصورة المرشحة: نقاط يتحقق منها المراجع قبل الاعتماد */
    .review-warnings {
        font-size: 0.8rem;
        color: var(--warning);
        background: var(--warning-bg);
        border: 1px solid var(--warning);
        padding: 0.55rem 0.75rem;
        border-radius: var(--border-radius-sm);
        line-height: 1.6;
        font-weight: 800;
        direction: rtl;
        text-align: right;
    }
    .review-warnings strong {
        display: block;
        margin-bottom: 0.2rem;
    }
    .evidence-chips {
        display: flex;
        flex-wrap: wrap;
        gap: 0.35rem;
    }
    .evidence-chip {
        font-size: 0.7rem;
        font-weight: 800;
        padding: 2px 7px;
        border-radius: 6px;
        border: 1px solid var(--panel-border);
        font-family: 'Outfit', sans-serif;
        unicode-bidi: isolate;
    }
    .evidence-chip.ok { background: var(--success-bg); color: var(--success); }
    .evidence-chip.bad { background: var(--danger-bg); color: var(--danger); }
    .evidence-chip.unknown { background: var(--input-bg); color: var(--text-secondary); }
    .evidence-chip.neutral { background: var(--active-menu-bg); color: var(--text-primary); }
    .vlm-read {
        font-size: 0.75rem;
        color: var(--text-secondary);
        line-height: 1.5;
    }
    .candidate-actions {
        display: flex;
        gap: 0.5rem;
        margin-top: auto;
    }
    .candidate-actions .btn {
        flex: 1;
        font-weight: bold;
    }
    .outcome-banner {
        border: 1px solid var(--panel-border);
        border-radius: 12px;
        padding: 1rem 1.15rem;
        margin-bottom: 1.25rem;
        font-weight: bold;
        line-height: 1.6;
    }
    .outcome-banner.success { background: var(--success-bg); color: var(--success); }
    .outcome-banner.warning { background: var(--warning-bg); color: var(--warning); }
    .outcome-banner.danger { background: var(--danger-bg); color: var(--danger); }
    .outcome-banner.info { background: var(--active-menu-bg); color: var(--text-primary); }
    .outcome-banner .banner-detail {
        display: block;
        font-weight: normal;
        font-size: 0.8rem;
        color: var(--text-primary);
        margin-top: 0.35rem;
    }
    .provider-health {
        display: flex;
        flex-wrap: wrap;
        gap: 0.35rem;
        margin-top: 0.5rem;
    }
    .recommended-image-box {
        background: #ffffff;
        border: 1px solid var(--panel-border);
        border-radius: 12px;
        height: 320px;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 1rem;
    }
    .recommended-image-box img {
        max-width: 100%;
        max-height: 100%;
        object-fit: contain;
    }

    #dropZone.drag-over {
        border-color: var(--accent-purple) !important;
        background: var(--active-menu-bg) !important;
        box-shadow: 0 0 15px var(--btn-shadow) !important;
    }

    /* Terminal Console Window styling */
    .terminal-console {
        background: #000000 !important;
        border: 1px solid var(--panel-border);
        border-radius: 16px;
        display: flex;
        flex-direction: column;
        overflow: hidden;
        box-shadow: var(--shadow-lg);
    }

    .terminal-header {
        background: #0a0a0a;
        padding: 0.75rem 1.25rem;
        border-bottom: 1px solid var(--panel-border);
        display: flex;
        align-items: center;
        justify-content: space-between;
        direction: rtl;
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
        color: var(--text-primary);
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
        box-shadow: 0 0 10px var(--btn-shadow);
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
        background-color: #111111 !important;
        background-image: 
            linear-gradient(45deg, #222222 25%, transparent 25%), 
            linear-gradient(-45deg, #222222 25%, transparent 25%), 
            linear-gradient(45deg, transparent 75%, #222222 75%), 
            linear-gradient(-45deg, transparent 75%, #222222 75%) !important;
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
        background: var(--card-bg);
        border: 1px solid var(--panel-border);
        border-radius: var(--border-radius-sm);
        transition: all 0.25s;
        font-weight: 700;
    }

    .feedback-checkbox:hover {
        background: var(--card-bg-hover);
        color: var(--text-primary);
        border-color: var(--accent-purple);
    }
    .feedback-checkbox:has(input:checked) {
        background: var(--active-menu-bg);
        border-color: var(--accent-purple);
        color: var(--text-primary);
        box-shadow: 0 0 10px var(--btn-shadow);
    }
    .feedback-checkbox input {
        cursor: pointer;
        accent-color: var(--accent-purple) !important;
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
        box-shadow: 0 8px 30px var(--btn-shadow);
        transform: translateY(-2px);
    }
    
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
        box-shadow: 0 0 15px var(--btn-shadow);
        transform: scale(1.05);
        background: var(--active-menu-bg);
    }
</style>
@endsection

@section('content')
<div class="layout-grid" id="layoutGrid">
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
        <div id="searchCriteriaWrapper" class="glass-panel" style="margin-bottom: 0;">
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
                                <span>مطابقة البراند الصارمة</span>
                                <label class="switch">
                                    <input type="checkbox" id="strictBrandMatch" checked>
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
                    <select id="outputPreset" onchange="applyPreset()" style="width:100%; font-size:0.85rem;">
                        <option value="dynamic" selected>800 × 800 — المقاس القياسي المعتمد (افتراضي)</option>
                        <option value="1000x1000">1000 × 1000 — مربع عالي الدقة</option>
                        <option value="900x1200">900 × 1200 — عمودي (ملابس، أزياء)</option>
                        <option value="1200x900">1200 × 900 — أفقي (أجهزة، إلكترونيات)</option>
                        <option value="custom">مخصص...</option>
                    </select>
                </div>
                {{-- اللوحة النهائية ثابتة: خلفية بيضاء معتمة والمنتج يملأ 88% وموسّط --}}
                <div class="form-group" style="grid-column: span 2;">
                    <label style="font-size:0.8rem; color:var(--text-secondary);">اللوحة النهائية</label>
                    <p style="font-size:0.8rem; color:var(--text-primary); margin:0; line-height:1.6;">
                        خلفية بيضاء معتمة دائماً، والمنتج كاملاً دون قص يملأ 88% من اللوحة وموسّط، وبالاتجاه الصحيح.
                    </p>
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
        <div class="glass-panel" style="min-height: 450px;" id="curationWorkspacePanel">
            <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.75rem; margin-bottom: 1.5rem;">
                <h3 style="font-size: 1.1rem; margin: 0;"><i class="fas fa-eye"></i> نتائج الفرز والمطابقة البصرية</h3>
                <span id="overallStatus" class="status-text active">في انتظار الإدخال</span>
            </div>

            <!-- Placeholder -->
            <div id="placeholder" style="text-align: center; color: var(--text-secondary); padding: 3rem 0;">
                <i class="far fa-image" style="font-size: 3.5rem; margin-bottom: 1.5rem; opacity: 0.3; display: block; margin: 0 auto 1.5rem auto;"></i>
                <div id="placeholderNormalText">
                    اختر منتجاً من القائمة الجانبية على اليمين للتحليل، أو ادخل البيانات يدوياً للبحث الفوري وعرض أدلة المطابقة (الباركود، البراند، الحجم، النوع) ونتيجة التحقق البصري.
                </div>
                
                <!-- Batch review alert card -->
                <div id="batchReviewAlertCard" style="display: none; background: var(--active-menu-bg); border: 1px solid var(--panel-border); border-radius: 16px; padding: 2rem; max-width: 500px; margin: 2rem auto 0 auto; flex-direction: column; align-items: center; gap: 1rem; text-align: center;">
                    <div style="font-size: 2.2rem; color: var(--text-primary);"><i class="fas fa-exclamation-triangle"></i></div>
                    <h4 style="font-size: 1.15rem; font-weight: 800; color: var(--text-primary); margin: 0;" id="batchReviewAlertTitle">توجد منتجات معلقة للمراجعة والتدقيق البصري</h4>
                    <p style="font-size: 0.85rem; line-height: 1.6; color: var(--text-secondary); margin: 0;" id="batchReviewAlertText">
                        لقد اكتشف نظام الأتمتة صوراً لهؤلاء المنتجات ولكنها بحاجة لمراجعتك وتأكيدك. يمكنك مراجعة كافة الصور واعتمادها دفعة واحدة الآن بلمح البصر!
                    </p>
                    <a href="{{ route('dashboard.batch_automation') }}" class="btn" style="background: var(--accent-gradient); color: var(--btn-text); font-weight: 800; padding: 0.65rem 1.75rem; width: 100%; border: none; text-align: center; text-decoration: none;">
                        <i class="fas fa-layer-group"></i> دخول مساحة المراجعة الجماعية
                    </a>
                </div>
            </div>

            <!-- Loading Spinner -->
            <div id="loading" class="loading-container">
                <div class="spinner-box">
                    <div class="spinner-ring"></div>
                    <div class="spinner-ring"></div>
                    <div class="spinner-ring"></div>
                </div>
                <p style="font-weight: 700; font-size: 1.1rem; margin-bottom: 0.25rem;">جاري البحث عن صورة المنتج والتحقق منها...</p>
                <p id="loadingDetails" style="font-size: 0.85rem; color: var(--text-secondary);">يرجى الانتظار: جلب المرشحين من محركات البحث، مطابقة الهوية، ثم التحقق البصري...</p>
            </div>

            <!-- Results Workspace -->
            <div id="resultsContent" style="display: none;">
                <!-- Outcome banner: success / review / not_found / provider_down / error -->
                <div id="outcomeBanner"></div>

                <!-- Recommended Image Card (only when the pipeline selected one) -->
                <div id="recommendedContainer"></div>

                <!-- Drag-drop & url overrides -->
                <div style="margin-top: 1.5rem; padding: 1.5rem; background: var(--card-bg); border: 1px solid var(--panel-border); border-radius: 16px;">
                    <h4 style="font-size: 1rem; margin-bottom: 0.75rem; color: var(--accent-cyan); display: flex; align-items: center; gap: 0.5rem;">
                        <i class="fas fa-edit"></i> خيارات الاعتماد اليدوي (Manual Override & Upload)
                    </h4>
                    <div class="form-grid" style="grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1.5rem;">
                        <div>
                            <h5 style="font-size: 0.85rem; margin-bottom: 0.5rem; color: var(--text-secondary);">ضع رابط الصورة المباشر هنا:</h5>
                            <div style="display: flex; gap: 0.75rem;">
                                <input type="text" id="manualImageUrl" placeholder="ضع رابط الصورة المباشر هنا..." style="flex: 1; padding: 0.75rem 1rem; background: var(--input-bg); border: 1px solid var(--panel-border); border-radius: 10px; color: var(--text-primary); font-family: inherit;">
                                <button class="btn" onclick="previewManualImage()"><i class="fas fa-eye"></i> معاينة</button>
                            </div>
                        </div>
                        <div>
                            <h5 style="font-size: 0.85rem; margin-bottom: 0.5rem; color: var(--text-secondary);">أو اسحب صورة للرفع والتجميل التلقائي:</h5>
                            <div id="dropZone" ondragover="event.preventDefault(); this.classList.add('drag-over')" ondragenter="event.preventDefault(); this.classList.add('drag-over')" ondragleave="this.classList.remove('drag-over')" ondrop="this.classList.remove('drag-over'); handleFileDrop(event)" onclick="triggerFileInput()" style="border: 2px dashed var(--panel-border); border-radius: 12px; padding: 0.75rem 1rem; text-align: center; cursor: pointer; transition: all 0.25s; background: rgba(255, 255, 255, 0.01); display: flex; flex-direction: column; align-items: center; justify-content: center;">
                                <i class="fas fa-cloud-upload-alt" style="font-size: 1.3rem; color: var(--text-secondary); margin-bottom: 0.25rem;"></i>
                                <p style="font-size: 0.75rem; margin: 0; color: var(--text-secondary);">اسحب وأسقط صورتك هنا أو انقر للتصفح</p>
                                <input type="file" id="manualFileInput" onchange="handleFileSelect(event)" style="display: none;" accept="image/*">
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Taxonomy editing dropdowns -->
                <div id="taxonomyEditorContainer" style="margin-top: 1.5rem; padding: 1.5rem; background: var(--card-bg); border: 1px solid var(--panel-border); border-radius: 16px;">
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
                <div class="terminal-console" style="margin-top: 2rem;">
                    <div class="terminal-header">
                        <div class="terminal-dots">
                            <div class="terminal-dot red"></div>
                            <div class="terminal-dot yellow"></div>
                            <div class="terminal-dot green"></div>
                        </div>
                        <div style="display: flex; align-items: center; gap: 0.75rem;">
                            <span style="font-size: 0.85rem; color: var(--text-secondary); font-family: 'Outfit', sans-serif; font-weight: 700;">
                                <i class="fas fa-terminal" style="color: var(--success); margin-inline-end: 0.25rem;"></i> LIVE TELEMETRY
                            </span>
                            <input type="text" id="terminalLogSearch" placeholder="تصفية السجلات..." oninput="filterTerminalLogs()" style="background: rgba(255,255,255,0.03); border: 1px solid var(--panel-border); border-radius: 6px; padding: 2px 8px; color: var(--text-primary); font-size: 0.75rem; width: 120px; outline: none; transition: all 0.2s;" onfocus="this.style.width='180px'; this.style.borderColor='var(--accent-purple)';" onblur="this.style.width='120px'; this.style.borderColor='var(--panel-border)';">
                            <button class="btn btn-secondary btn-sm" onclick="clearLiveConsoleLogs()" style="padding: 2px 8px; font-size: 0.75rem; background: rgba(255,255,255,0.05);">تفريغ السجلات</button>
                        </div>
                    </div>
                    <div id="liveConsoleLogs" style="font-family: 'Courier New', Courier, monospace; font-size: 0.85rem; color: #ffffff; background: #000000; padding: 1.25rem; border-radius: 0 0 16px 16px; max-height: 200px; overflow-y: auto; text-align: left; direction: ltr; line-height: 1.5; border: 1px solid rgba(255,255,255,0.05);">
                        <p style="color: var(--text-secondary); margin: 0;">[System] Initializing console logs listener...</p>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>



<!-- Reject Reason Modal -->
<div id="rejectReasonModal" class="modal" style="display: none; position: fixed; z-index: 10001; left: 0; top: 0; width: 100%; height: 100%; overflow: auto; background-color: rgba(3, 4, 10, 0.85); backdrop-filter: blur(15px); align-items: center; justify-content: center;">
    <div class="glass-panel" style="max-width: 560px; width: 92%; padding: 2rem; border-radius: 20px; border: 1px solid var(--panel-border); margin: 5% auto;">
        <h3 style="font-size: 1.15rem; margin-bottom: 0.5rem; color: var(--danger); display: flex; align-items: center; gap: 0.5rem;">
            <i class="fas fa-ban"></i> سبب رفض الصورة
        </h3>
        <p style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 0.25rem;">اختر السبب الأدق. تُستبعد الصورة من عمليات البحث القادمة لهذا المنتج.</p>
        <p id="rejectModalUrl" style="font-size: 0.75rem; color: var(--text-primary); margin-bottom: 1rem; word-break: break-all;"></p>
        <div class="feedback-reasons-grid" id="rejectReasonOptions">
            <label class="feedback-checkbox"><input type="radio" name="reject_reason_code" value="WRONG_PRODUCT"><span>منتج مختلف تماماً (WRONG_PRODUCT)</span></label>
            <label class="feedback-checkbox"><input type="radio" name="reject_reason_code" value="WRONG_BRAND"><span>علامة تجارية خاطئة (WRONG_BRAND)</span></label>
            <label class="feedback-checkbox"><input type="radio" name="reject_reason_code" value="WRONG_VARIANT"><span>نكهة / نوع خاطئ (WRONG_VARIANT)</span></label>
            <label class="feedback-checkbox"><input type="radio" name="reject_reason_code" value="WRONG_SIZE"><span>حجم / وزن خاطئ (WRONG_SIZE)</span></label>
            <label class="feedback-checkbox"><input type="radio" name="reject_reason_code" value="WRONG_PACK"><span>عدد العبوة خاطئ (WRONG_PACK)</span></label>
            <label class="feedback-checkbox"><input type="radio" name="reject_reason_code" value="NOT_PACKSHOT"><span>ليست صورة المنتج الأمامية (NOT_PACKSHOT)</span></label>
            <label class="feedback-checkbox"><input type="radio" name="reject_reason_code" value="LOW_QUALITY"><span>جودة رديئة (LOW_QUALITY)</span></label>
        </div>
        <label style="display: flex; align-items: center; gap: 0.5rem; margin-top: 1rem; font-size: 0.85rem; color: var(--text-primary); cursor: pointer;">
            <input type="checkbox" id="rejectResearch" checked style="width: 18px; height: 18px;"> إعادة البحث فوراً مع استبعاد هذه الصورة
        </label>
        <div style="display: flex; justify-content: space-between; gap: 1rem; margin-top: 1.5rem;">
            <button type="button" class="btn btn-secondary" onclick="closeRejectModal()" style="flex: 1;">إلغاء</button>
            <button type="button" class="btn" id="rejectConfirmBtn" onclick="submitReject()" style="flex: 2; background: var(--danger); color: #ffffff; font-weight: 900;">تأكيد الرفض</button>
        </div>
    </div>
</div>

<!-- Canvas Editor Modal -->
<div id="editorModal" class="modal" style="display: none; position: fixed; z-index: 10000; left: 0; top: 0; width: 100%; height: 100%; overflow: auto; background-color: rgba(3, 4, 10, 0.85); backdrop-filter: blur(15px); align-items: center; justify-content: center;">
    <div class="glass-panel" style="max-width: 600px; width: 90%; padding: 2rem; border-radius: 20px; border: 1px solid var(--panel-border); text-align: center; margin: 5% auto;">
        <h3 style="font-size: 1.2rem; margin-bottom: 1rem; display: flex; align-items: center; justify-content: center; gap: 0.5rem; color: var(--accent-cyan);">
            <i class="fas fa-crop-alt"></i> محرر ومعاين الصورة المرفوعة
        </h3>
        <div style="background: #000000; border-radius: 12px; padding: 1rem; margin-bottom: 1.5rem; display: flex; align-items: center; justify-content: center; min-height: 250px; border: 1px solid var(--panel-border);">
            <canvas id="editorCanvas" style="max-width: 100%; max-height: 350px; border-radius: 8px; box-shadow: 0 8px 30px rgba(0,0,0,0.5);"></canvas>
        </div>
        <div style="display: flex; justify-content: center; gap: 1rem; margin-bottom: 1.5rem;">
            <button class="btn btn-secondary" onclick="editorRotate()"><i class="fas fa-sync-alt"></i> تدوير 90° 🔄</button>
            <button class="btn btn-secondary" onclick="editorFlip()"><i class="fas fa-arrows-alt-h"></i> انعكاس أفقياً ↔️</button>
        </div>
        <div style="display: flex; justify-content: space-between; gap: 1rem;">
            <button class="btn btn-secondary" onclick="closeEditorModal()" style="flex: 1;">إلغاء</button>
            <button class="btn" onclick="commitEditorUpload()" style="flex: 2; background: var(--accent-gradient); color: var(--btn-text); font-weight: 900;"><i class="fas fa-check"></i> اعتماد الرفع والتجميل التلقائي</button>
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
    let lastCurrentProcessed = 0;

    // دالة لتمرير روابط الصور الخارجية عبر البروكسي الداخلي لتجاوز حماية الـ Hotlinking
    // (http/https فقط؛ أي رابط آخر لا يُعرض)
    function getImageUrl(url) {
        if (!url) return '';
        const safe = safeHttpUrl(url);
        if (!safe) return '';
        if (safe.startsWith(window.location.origin + '/') || /^https:\/\/res\.cloudinary\.com\//.test(safe)) {
            return safe;
        }
        return `/api/image-proxy?url=${encodeURIComponent(safe)}`;
    }

    // حفظ التغييرات تلقائياً في LocalStorage
    function saveSettingsToLocalStorage() {
        localStorage.setItem('strictBrandMatch', document.getElementById('strictBrandMatch').checked);
        localStorage.setItem('aiEnhance', document.getElementById('aiEnhance').checked);
        localStorage.setItem('skipCache', document.getElementById('skipCache').checked);
        localStorage.setItem('target_width', getOutputWidth());
        localStorage.setItem('target_height', getOutputHeight());
    }

    // On load
    window.addEventListener('load', () => {
        // استعادة الإعدادات المخزنة من LocalStorage إن وجدت
        if (localStorage.getItem('strictBrandMatch') !== null) {
            document.getElementById('strictBrandMatch').checked = localStorage.getItem('strictBrandMatch') === 'true';
        }
        if (localStorage.getItem('aiEnhance') !== null) {
            document.getElementById('aiEnhance').checked = localStorage.getItem('aiEnhance') === 'true';
        }
        if (localStorage.getItem('skipCache') !== null) {
            document.getElementById('skipCache').checked = localStorage.getItem('skipCache') === 'true';
        }

        // إضافة مستمعي الأحداث لحفظ التغييرات فوراً
        const inputs = ['strictBrandMatch', 'aiEnhance', 'skipCache', 'outputPreset', 'customWidth', 'customHeight'];
        inputs.forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                el.addEventListener('change', saveSettingsToLocalStorage);
            }
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && document.getElementById('rejectReasonModal').style.display === 'flex') {
                closeRejectModal();
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
            const url = forceRefresh ? '/api/products-json?refresh=true' : '/api/products-json';
            if (forceRefresh) {
                // مسح كاش السيرفر أولاً لإجبار التحديث من Google Sheets
                await fetch('/api/clear-products-cache', {
                    method: 'POST',
                    headers: { 'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content }
                });
            }
            const res = await fetch(url);
            const data = await res.json();
            const fromCache = res.headers.get('X-Cache') === 'HIT';
            if (res.ok && data.status === 'success') {
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
            } else {
                productList.innerHTML = `<p style="color: var(--danger); text-align: center; padding: 2rem; direction: rtl;">فشل جلب المنتجات من Google Sheets.<br><small style="color: var(--gray-light); font-size: 0.85rem;">الخطأ: ${escapeHtml(data.error || 'استجابة غير صالحة من السيرفر')}</small></p>`;
            }
        } catch (err) {
            console.error(err);
            productList.innerHTML = '<p style="color: var(--danger); text-align: center; padding: 2rem; direction: rtl;">فشل الاتصال بالسيرفر. يرجى التحقق من لوحة التحكم أو التيرمينال.</p>';
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
        
        document.getElementById('tab-review').innerHTML = `المراجعة ⚠️ <span style="background: var(--warning-bg); border: 1px solid var(--panel-border); color: var(--warning); padding: 2px 6px; border-radius: 8px; font-size: 0.75rem; margin-right: 4px; font-weight: 900;">${review}</span>`;
        document.getElementById('tab-errors').innerHTML = `أخطاء ❌ <span style="background: var(--danger-bg); border: 1px solid var(--panel-border); color: var(--danger); padding: 2px 6px; border-radius: 8px; font-size: 0.75rem; margin-right: 4px; font-weight: 900;">${errors}</span>`;
        
        // تحديث كارت التنبيه للمراجعة الجماعية
        const alertCard = document.getElementById('batchReviewAlertCard');
        const placeholderText = document.getElementById('placeholderNormalText');
        if (alertCard) {
            if (review > 0) {
                alertCard.style.display = 'flex';
                document.getElementById('batchReviewAlertTitle').innerText = `توجد ${review} صور معلقة للمراجعة والتدقيق البصري ⚠️`;
                if (placeholderText) placeholderText.style.display = 'none';
            } else {
                alertCard.style.display = 'none';
                if (placeholderText) placeholderText.style.display = 'block';
            }
        }
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
            let statusClass = 'missing';
            
            if (prod.has_error) {
                statusClass = 'error';
                linkIndicator = `<span style="color: var(--danger); font-size: 0.8rem; font-weight: bold;" title="${escapeHtml(prod.error_message || '')}"><i class="fas fa-exclamation-circle"></i> خطأ أتمتة ❌</span>`;
            } else if (prod.needs_review) {
                statusClass = 'review';
                linkIndicator = `<span style="color: var(--warning); font-size: 0.8rem; font-weight: bold;"><i class="fas fa-exclamation-triangle"></i> مراجعة معلقة ⚠️</span>`;
            } else if (hasLink) {
                statusClass = 'completed';
                linkIndicator = `<span style="color: var(--success); font-size: 0.8rem; font-weight: bold;"><i class="fas fa-check"></i> رابط موجود</span>`;
            } else {
                statusClass = 'missing';
                linkIndicator = `<span style="color: var(--info); font-size: 0.8rem; font-weight: bold;"><i class="fas fa-times"></i> بدون رابط</span>`;
            }
            
            const item = document.createElement('div');
            item.className = `product-item ${statusClass}`;
            if (activeRowNumber === prod.row_number) item.classList.add('active');
            
            // لا نسب مطابقة مختلقة: فقط شارة "مرشح مسبق" عندما اختار النظام مرشحاً موثقاً
            let scoreBadge = '';
            if (prod.preselected) {
                scoreBadge = `<span class="score-badge" style="background: var(--active-menu-bg); border: 1px solid var(--panel-border); color: var(--text-primary); font-size: 0.7rem; padding: 2px 6px; border-radius: 4px; font-weight: bold; margin-inline-start: 5px;">مرشح مسبق</span>`;
            }

            let badgeStyle = 'background-color: var(--input-bg); color: var(--text-secondary);';
            if (prod.has_error) badgeStyle = 'background-color: var(--danger-bg); border-color: var(--danger); color: var(--danger);';
            else if (prod.needs_review) badgeStyle = 'background-color: var(--warning-bg); border-color: var(--warning); color: var(--warning);';
            else if (hasLink) badgeStyle = 'background-color: var(--success-bg); border-color: var(--success); color: var(--success);';

            item.innerHTML = `
                <span class="badge-row-number" style="${badgeStyle} border: 1px solid var(--panel-border); font-weight: 800;">صف ${prod.row_number}</span>
                <h4 style="margin-top: 0.65rem; font-size: 1rem; font-weight: 800; color: var(--text-primary); text-overflow: ellipsis; overflow: hidden; white-space: nowrap;">${escapeHtml(prod.product_name)}</h4>
                <p style="display: flex; align-items: center; justify-content: space-between; font-size: 0.8rem; color: var(--text-secondary); font-weight: 600;">
                    <span>البراند: <strong style="color: var(--text-primary);">${escapeHtml(prod.brand)}</strong></span>
                    <span style="display: inline-flex; align-items: center; gap: 0.4rem;">
                        ${linkIndicator}
                        ${scoreBadge}
                    </span>
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

    // =============================================
    // أدوات بناء الواجهة بأمان: بيانات الويب (العناوين والروابط) تُعرض عبر textContent
    // وخصائص data-* ومستمعي الأحداث، وليس عبر innerHTML أو onclick مضمّن.
    // =============================================
    function escapeHtml(value) {
        return String(value === undefined || value === null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function el(tag, props = {}, children = []) {
        const node = document.createElement(tag);
        for (const [key, value] of Object.entries(props || {})) {
            if (value === undefined || value === null || value === false) continue;
            if (key === 'text') node.textContent = String(value);
            else if (key === 'className') node.className = value;
            else if (key === 'style') node.style.cssText = value;
            else if (key === 'dataset') Object.assign(node.dataset, value);
            else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
            else node.setAttribute(key, String(value));
        }
        (Array.isArray(children) ? children : [children]).forEach(child => {
            if (child === null || child === undefined || child === false) return;
            node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
        });
        return node;
    }

    function safeHttpUrl(url) {
        try {
            const u = new URL(String(url || ''), window.location.origin);
            return (u.protocol === 'http:' || u.protocol === 'https:') ? u.href : '';
        } catch (e) {
            return '';
        }
    }

    function hostOf(url) {
        try {
            return new URL(String(url || '')).hostname.replace(/^www\./, '');
        } catch (e) {
            return '';
        }
    }

    const CANDIDATE_STATUS_LABELS = {
        preselected: { text: 'مرشحة مسبقاً ✓', cls: 'accepted' },
        eligible: { text: 'مؤهلة — تحتاج مراجعة', cls: 'eligible' },
        rejected: { text: 'مستبعدة آلياً', cls: 'rejected' },
        excluded: { text: 'مستبعدة (رفض سابق)', cls: 'rejected' },
        accepted: { text: 'مقبولة (المحرك القديم)', cls: 'eligible' },
        unverified_fallback: { text: 'غير موثقة', cls: 'rejected' },
        pending: { text: 'بانتظار المراجعة', cls: 'eligible' }
    };

    const DECISION_LABELS = {
        AUTO_PUBLISH: 'مطابقة موثقة بالكامل (نشر تلقائي مسموح)',
        REVIEW_PRESELECTED: 'مراجعة: مرشح موثق مختار مسبقاً',
        REVIEW_UNSELECTED: 'مراجعة: لا يوجد مرشح مؤكد',
        NOT_FOUND: 'لا يوجد منتج مطابق',
        PROVIDER_DOWN: 'محركات البحث غير متاحة',
        VERIFIER_DOWN: 'التحقق البصري غير متاح'
    };

    // تحذيرات المراجعة (warnings في استجابة البحث): جملة عربية لكل رمز، والرمز غير المعروف يُعرض كما هو
    const REVIEW_WARNING_LABELS = {
        sheet_silent: 'الشيت ما حدد النوع',
        vlm_unsure: 'Gemini غير متأكد من المطابقة',
        low_resolution: 'صورة منخفضة الدقة (أقل من 500 بكسل)',
        chat_or_screenshot: 'صورة من واتساب أو لقطة شاشة',
        social_media: 'الصورة من مواقع التواصل الاجتماعي',
        foreign_store: 'الصورة من متجر خارج الإمارات (قد تختلف العبوة)'
    };

    const VARIANT_AXIS_LABELS = {
        fries_cut: 'طريقة التقطيع',
        cheese_form: 'شكل الجبن',
        fat: 'نسبة الدسم',
        sugar: 'السكر',
        caffeine: 'الكافيين',
        form: 'الشكل',
        medium: 'الزيت أو الماء',
        flavour: 'النكهة',
        tuna_meat: 'نوع لحم التونة',
        tuna_cut: 'تقطيع التونة'
    };

    function warningText(code) {
        code = String(code || '');
        const sep = code.indexOf(':');
        const name = sep >= 0 ? code.slice(0, sep) : code;
        const detail = sep >= 0 ? code.slice(sep + 1) : '';
        if (name === 'sheet_silent' && detail) {
            // sheet_silent:<axis>=<value>
            const eq = detail.indexOf('=');
            const axis = eq >= 0 ? detail.slice(0, eq) : '';
            const value = (eq >= 0 ? detail.slice(eq + 1) : detail).split('+').join(' / ');
            const label = VARIANT_AXIS_LABELS[axis] ? `الشيت ما حدد ${VARIANT_AXIS_LABELS[axis]}` : REVIEW_WARNING_LABELS.sheet_silent;
            return `${label}: ${value}`;
        }
        return REVIEW_WARNING_LABELS[name] || code;
    }

    function renderWarnings(c) {
        if (!c.warnings || !c.warnings.length) return null;
        return el('div', { className: 'review-warnings', role: 'alert' }, [
            el('strong', { text: '⚠️ تحقق من هذه النقاط قبل الاعتماد:' }),
            ...c.warnings.map(w => el('div', { text: '• ' + warningText(w) }))
        ]);
    }

    function normalizeCandidate(c) {
        c = c || {};
        const ev = (c.evidence && typeof c.evidence === 'object' && !Array.isArray(c.evidence)) ? c.evidence : {};
        let reasons = c.reasons;
        if (!Array.isArray(reasons)) reasons = reasons ? [String(reasons)] : [];
        reasons = reasons.map(r => String(r));
        // استجابة البحث ترسل warnings جاهزة؛ صفوف curation_candidates المحفوظة تحمل الأسباب فقط (warn:<code>)
        const warnings = Array.isArray(c.warnings) ? c.warnings.map(w => String(w))
            : reasons.filter(r => r.startsWith('warn:')).map(r => r.slice(5));
        return {
            url: String(c.url || c.image_url || ''),
            title: String(c.title || c.page_title || ev.page_title || ev.title || ''),
            page_url: String(c.page_url || ev.page_url || ''),
            domain: String(c.domain || c.source_domain || ev.domain || ''),
            status: String(c.status || 'eligible'),
            reasons: reasons,
            warnings: warnings,
            evidence: ev,
            conflicts: Array.isArray(c.conflicts) ? c.conflicts : [],
            vlm: (c.vlm && typeof c.vlm === 'object') ? c.vlm : null,
            width: c.width || null,
            height: c.height || null,
            identity_tier: c.identity_tier || ev.tier || (c.scores && c.scores.tier) || null,
            content_sha256: c.content_sha256 || ev.content_sha256 || null,
            source: String(c.source || '')
        };
    }

    // مرشحو الاستجابة: candidates حسب عقد cli_bridge، أو خطوات التتبع للمحرك القديم
    function collectCandidates(data) {
        let list = [];
        if (data && Array.isArray(data.candidates) && data.candidates.length) {
            list = data.candidates;
        } else if (data && data.trace && Array.isArray(data.trace.steps)) {
            data.trace.steps.forEach(step => (step.candidates || []).forEach(c => list.push(c)));
        }
        const seen = new Set();
        return list.map(normalizeCandidate).filter(c => {
            if (!c.url || seen.has(c.url)) return false;
            seen.add(c.url);
            return true;
        });
    }

    function triState(value) {
        if (value === true || value === 1) return true;
        if (value === false || value === 0) return false;
        if (Array.isArray(value)) return value.length > 0 ? true : null;
        if (typeof value === 'string') {
            const v = value.trim().toLowerCase();
            if (['match', 'yes', 'ok', 'true', 'matched'].includes(v)) return true;
            if (['conflict', 'no', 'mismatch', 'false'].includes(v)) return false;
            if (v === '' || ['unknown', 'unsure', 'ambiguous', 'none', 'n/a', 'missing'].includes(v)) return null;
            return true;
        }
        return null;
    }

    function evidenceChip(label, state, detail) {
        const mark = state === true ? '✓' : (state === false ? '✗' : '?');
        const cls = state === true ? 'ok' : (state === false ? 'bad' : 'unknown');
        const shown = (detail !== undefined && detail !== null && typeof detail !== 'object' && String(detail) !== '' && typeof detail !== 'boolean')
            ? `${label} ${mark} ${detail}` : `${label} ${mark}`;
        return el('span', { className: `evidence-chip ${cls}`, text: shown });
    }

    // شرائح الأدلة: GTIN / البراند / الحجم / النوع / نطاق الصفحة / مستوى الهوية
    function renderEvidenceChips(c) {
        const ev = c.evidence || {};
        const conflicts = [].concat(ev.conflicts || [], ev.hard_reject || [], c.conflicts || []).map(x => String(x).toLowerCase());
        const hasConflict = (word) => conflicts.some(x => x.includes(word));
        const wrap = el('div', { className: 'evidence-chips' });

        const gtin = ev.gtin_match !== undefined ? ev.gtin_match : ev.gtin;
        if ((gtin !== undefined && gtin !== null && gtin !== '') || hasConflict('gtin')) {
            wrap.appendChild(evidenceChip('GTIN', hasConflict('gtin') ? false : triState(gtin)));
        }
        const brand = ev.brand_match !== undefined ? ev.brand_match : ev.brand;
        wrap.appendChild(evidenceChip('Brand', (hasConflict('brand') || hasConflict('competitor')) ? false : triState(brand),
            typeof brand === 'string' ? brand : null));
        const size = ev.size_status !== undefined ? ev.size_status : (ev.size_match !== undefined ? ev.size_match : ev.size);
        wrap.appendChild(evidenceChip('Size', (hasConflict('size') || hasConflict('pack')) ? false : triState(size),
            (typeof ev.size === 'string' && ev.size_status !== undefined) ? ev.size : null));
        const variant = ev.variant_status !== undefined ? ev.variant_status : (ev.variant_match !== undefined ? ev.variant_match : ev.variants);
        wrap.appendChild(evidenceChip('Variant', hasConflict('variant') ? false : triState(variant),
            Array.isArray(ev.variants) && ev.variants.length ? ev.variants.join(', ') : null));

        const domain = c.domain || hostOf(c.page_url) || hostOf(c.url);
        if (domain) wrap.appendChild(el('span', { className: 'evidence-chip neutral', text: domain }));
        if (c.identity_tier) wrap.appendChild(el('span', { className: 'evidence-chip neutral', text: `T${c.identity_tier}` }));
        return wrap;
    }

    // النص الذي قرأه نموذج الرؤية فعلياً من الصورة (وليس نسبة مختلقة)
    function renderVlm(c) {
        const v = c.vlm;
        if (!v) return null;
        const parts = [];
        if (v.brand_text) parts.push(`البراند: ${v.brand_text}`);
        if (v.variant_text) parts.push(`النوع: ${v.variant_text}`);
        if (v.size_text) parts.push(`الحجم: ${v.size_text}`);
        if (v.pack_count) parts.push(`العدد: ${v.pack_count}`);
        if (v.view) parts.push(`الزاوية: ${v.view}`);
        return el('div', { className: 'vlm-read', title: 'النص الذي قرأه نموذج الرؤية من الصورة' }, [
            el('strong', { text: `VLM: ${v.decision || 'UNKNOWN'}` }),
            parts.length ? el('span', { text: ' — ' + parts.join(' | ') }) : null
        ]);
    }

    function renderReasons(c) {
        if (!c.reasons.length) return null;
        const negative = c.status === 'rejected' || c.status === 'excluded';
        return el('div', { className: 'candidate-reasons' + (negative ? '' : ' neutral') },
            c.reasons.map(r => el('div', { text: '• ' + r })));
    }

    // بيانات المنتج الحالي المطلوبة للاعتماد والرفض
    function currentProductContext() {
        const form = document.getElementById('searchForm');
        return {
            row_number: document.getElementById('rowNumber').value,
            product_name: document.getElementById('productName').value,
            brand: document.getElementById('brand').value,
            product_name_ar: document.getElementById('productNameAr').value,
            brand_ar: document.getElementById('brandAr').value,
            barcode: form.dataset.barcode || '',
            sku_key: form.dataset.skuKey || '',
            // قرار البحث المعروض الآن (يُسجل مع قرار المراجع في review_decisions)
            search_decision: form.dataset.searchDecision || '',
            category: form.dataset.category || '',
            // هوية الحجم والفئة الفرعية والمنشأ تُرسل دائماً: لا نعتمد على وجود صف في الطابور
            size: form.dataset.size || '',
            sub_category: form.dataset.subCategory || '',
            origin: form.dataset.origin || ''
        };
    }

    function showResultsWorkspace() {
        document.getElementById('placeholder').style.display = 'none';
        document.getElementById('loading').style.display = 'none';
        document.getElementById('resultsContent').style.display = 'block';
    }

    function setOverallStatus(text, cls) {
        const overallStatus = document.getElementById('overallStatus');
        overallStatus.textContent = text;
        overallStatus.className = 'status-text ' + cls;
    }

    function renderProviderHealth(health) {
        let items = [];
        if (Array.isArray(health)) items = health;
        else if (health && typeof health === 'object') {
            items = Object.entries(health).map(([name, v]) => (v && typeof v === 'object') ? Object.assign({ provider: name }, v) : { provider: name, status: v });
        }
        if (!items.length) return null;
        return el('div', { className: 'provider-health' }, items.map(h => {
            const status = String(h.status || 'unknown');
            const cls = status === 'ok' ? 'ok' : (status === 'empty' ? 'unknown' : 'bad');
            const name = String(h.provider || h.name || 'engine');
            const code = h.http_status ? ` (${h.http_status})` : '';
            return el('span', { className: `evidence-chip ${cls}`, text: `${name}: ${status}${code}` });
        }));
    }

    // لافتة نتيجة البحث لكل حالة: success / review / not_found / provider_down / error
    function renderOutcomeBanner(data, candidateCount, extraNote) {
        const container = document.getElementById('outcomeBanner');
        container.textContent = '';
        const status = String((data && data.status) || 'error');
        const decision = String((data && data.decision) || '');
        let cls = 'info';
        let title = '';
        let detail = '';

        if (status === 'provider_down' || decision === 'PROVIDER_DOWN') {
            cls = 'danger';
            title = '⚠️ محركات البحث غير متاحة حالياً (engines unavailable)';
            detail = 'هذه ليست نتيجة "غير موجود": لم تصل محركات البحث للنتائج (حصة منتهية، مفتاح غير صالح، أو حظر). أعد المحاولة لاحقاً.';
        } else if (decision === 'VERIFIER_DOWN') {
            cls = 'warning';
            title = '⚠️ التحقق البصري (Gemini) غير متاح — لا يوجد أي اعتماد تلقائي';
            detail = 'راجع المرشحين يدوياً باستخدام الأدلة النصية أدناه.';
        } else if (status === 'success' && decision === 'AUTO_PUBLISH') {
            cls = 'success';
            title = '✅ مطابقة موثقة بالكامل (GTIN/البراند/الحجم/النوع + تحقق بصري)';
            detail = 'راجع الصورة ثم اعتمدها للنشر.';
        } else if (status === 'success') {
            cls = 'success';
            title = '✅ تم العثور على صورة';
        } else if (status === 'review' && decision !== 'REVIEW_UNSELECTED' && data && data.selected_image) {
            cls = 'warning';
            title = '🔎 مراجعة مطلوبة: رشّح النظام صورة موثقة مسبقاً';
            detail = 'الصورة المرشحة لم تُنشر بعد. تأكد من الأدلة قبل الاعتماد أو اختر مرشحاً آخر.';
        } else if (status === 'review') {
            cls = 'warning';
            title = '🔎 مراجعة مطلوبة: لا يوجد مرشح مؤكد المطابقة';
            detail = 'لم يُختر أي مرشح مسبقاً. اختر يدوياً من المرشحين أدناه، أو عدّل الاستعلام المخصص وأعد البحث.';
        } else if (status === 'not_found') {
            cls = 'info';
            title = '🔍 لم يتم العثور على منتج مطابق (محركات البحث تعمل)';
            detail = 'جرّب استعلاماً مخصصاً (اسم عربي، حجم، أو موقع متجر) أو ارفع صورة يدوياً.';
        } else {
            cls = 'danger';
            title = '❌ فشل تنفيذ البحث';
            detail = String((data && (data.error || data.message)) || 'خطأ غير معروف من جسر بايثون.');
        }

        const banner = el('div', { className: `outcome-banner ${cls}`, dataset: { status: status } }, [
            el('div', { text: title }),
            detail ? el('span', { className: 'banner-detail', text: detail }) : null,
            extraNote ? el('span', { className: 'banner-detail', text: extraNote }) : null
        ]);
        const facts = [];
        if (decision) facts.push(`القرار: ${DECISION_LABELS[decision] || decision}`);
        if (data && data.failure_code) facts.push(`رمز السبب: ${data.failure_code}`);
        facts.push(`عدد المرشحين: ${candidateCount}`);
        banner.appendChild(el('span', { className: 'banner-detail', text: facts.join(' | ') }));
        const health = renderProviderHealth(data && data.provider_health);
        if (health) banner.appendChild(health);
        container.appendChild(banner);
    }

    // عرض استجابة البحث لكل الحالات (وليس فقط عند النجاح)
    function renderSearchResponse(data, extraNote) {
        data = data || {};
        if (data.sku_key) {
            document.getElementById('searchForm').dataset.skuKey = data.sku_key;
        }
        document.getElementById('searchForm').dataset.searchDecision = String(data.decision || '');
        showResultsWorkspace();
        const candidates = collectCandidates(data);
        renderOutcomeBanner(data, candidates.length, extraNote);

        const recommendedContainer = document.getElementById('recommendedContainer');
        recommendedContainer.textContent = '';
        if (data.selected_image && (data.selected_image.url || data.selected_image.image_url)) {
            const selected = normalizeCandidate(data.selected_image);
            const fromList = candidates.find(c => c.url === selected.url);
            const merged = fromList ? Object.assign({}, fromList, { source: selected.source || fromList.source }) : selected;
            renderRecommendedCard(merged, { decision: data.decision || '' });
        }
        renderCandidatesGrid(candidates);
        renderAccordionTrace(data.trace);

        const meta = data.selected_image && data.selected_image.metadata;
        if (meta) {
            preselectTaxonomy(meta.category_l1_en, meta.category_l2_en, meta.category_l3_en);
        } else {
            initTaxonomyDropdowns();
        }

        const status = String(data.status || 'error');
        if (status === 'success') setOverallStatus('تم العثور على صورة موثقة', 'success');
        else if (status === 'review') setOverallStatus('بانتظار المراجعة البشرية', 'active');
        else if (status === 'not_found') setOverallStatus('لا يوجد منتج مطابق', 'failed');
        else if (status === 'provider_down') setOverallStatus('محركات البحث غير متاحة', 'failed');
        else setOverallStatus('فشل البحث', 'failed');
    }

    // عند اختيار منتج من القائمة الجانبية
    function selectProduct(prod, element) {
        document.querySelectorAll('.product-item').forEach(el => el.classList.remove('active'));
        element.classList.add('active');

        // إزالة أي تنبيه أخطاء أتمتة سابقة
        const existingAlert = document.getElementById('automationErrorAlert');
        if (existingAlert) {
            existingAlert.remove();
        }

        document.getElementById('rowNumber').value = prod.row_number;
        document.getElementById('productName').value = prod.product_name || '';
        document.getElementById('brand').value = prod.brand || '';
        document.getElementById('productNameAr').value = prod.product_name_ar || '';
        document.getElementById('brandAr').value = prod.brand_ar || '';
        document.getElementById('customQuery').value = prod.search_query || '';
        document.getElementById('manualImageUrl').value = '';

        const form = document.getElementById('searchForm');
        form.dataset.barcode = prod.barcode || '';
        form.dataset.skuKey = prod.sku_key || '';
        form.dataset.searchDecision = '';
        form.dataset.category = prod.category || '';
        form.dataset.origin = prod.origin || '';
        form.dataset.size = prod.size || '';
        form.dataset.subCategory = prod.sub_category || '';

        activeRowNumber = prod.row_number;

        // إظهار بانر توضيحي لسبب فشل الأتمتة في حالة وجود خطأ
        if (prod.has_error) {
            const panel = document.getElementById('curationWorkspacePanel');
            const alertDiv = el('div', {
                id: 'automationErrorAlert',
                style: 'background-color: var(--danger-bg); border: 1px solid var(--panel-border); color: var(--danger); border-radius: 12px; padding: 1.15rem; margin-bottom: 1.5rem; font-weight: bold; direction: rtl; text-align: right; line-height: 1.5;'
            }, [
                el('strong', { text: '❌ خطأ الأتمتة التلقائية بالخلفية:' }),
                el('span', {
                    style: 'font-size: 0.85rem; font-weight: normal; color: var(--text-primary); font-family: monospace; display: block; margin-top: 0.35rem;',
                    text: prod.error_message || 'لا تتوفر تفاصيل إضافية للخطأ.'
                })
            ]);
            panel.insertBefore(alertDiv, panel.children[1]);
        }

        const stored = (prod.curation_candidates || []).map(normalizeCandidate).filter(c => c.url);
        if (prod.needs_review && (stored.length || prod.needs_review_url)) {
            showResultsWorkspace();
            setOverallStatus('بانتظار المراجعة البشرية', 'active');

            const recommendedContainer = document.getElementById('recommendedContainer');
            recommendedContainer.textContent = '';
            let note;
            if (prod.needs_review_url) {
                const selected = stored.find(c => c.url === prod.needs_review_url) || normalizeCandidate({
                    url: prod.needs_review_url,
                    status: prod.preselected ? 'preselected' : 'pending',
                    title: 'الرابط المعلّم بـ needs_review في الشيت'
                });
                renderRecommendedCard(selected, { decision: prod.preselected ? 'REVIEW_PRESELECTED' : '' });
                note = prod.preselected
                    ? 'رشّح النظام هذه الصورة من مرشحين موثقين، ولم تُنشر بعد. راجع الأدلة واعتمدها أو اختر غيرها.'
                    : 'الرابط في الشيت معلّم needs_review (لم يُراجع بعد أو لم تتم إزالة الخلفية). راجعه قبل الاعتماد.';
            } else {
                note = 'لا يوجد مرشح مؤكد المطابقة: اختر يدوياً من المرشحين أدناه أو اضغط البحث لإعادة البحث.';
            }
            form.dataset.searchDecision = prod.preselected ? 'REVIEW_PRESELECTED' : 'REVIEW_UNSELECTED';
            renderOutcomeBanner({ status: 'review', decision: prod.preselected ? 'REVIEW_PRESELECTED' : 'REVIEW_UNSELECTED',
                                  selected_image: prod.needs_review_url ? { url: prod.needs_review_url } : null },
                                stored.length, note);
            renderCandidatesGrid(stored);
            renderAccordionTrace(null);
            initTaxonomyDropdowns();
        } else {
            document.getElementById('submitBtn').click();
        }
    }

    // إرسال استعلام البحث (يرسل الاسم والبراند بالعربية والفئة والاستعلام المخصص)
    document.getElementById('searchForm').addEventListener('submit', async function(e) {
        e.preventDefault();

        // إزالة أي تنبيه أخطاء أتمتة سابقة عند بدء البحث الجديد
        const existingAlert = document.getElementById('automationErrorAlert');
        if (existingAlert) {
            existingAlert.remove();
        }

        const placeholder = document.getElementById('placeholder');
        const loading = document.getElementById('loading');
        const resultsContent = document.getElementById('resultsContent');
        const ctx = currentProductContext();
        const strictBrandMatch = document.getElementById('strictBrandMatch').checked;
        const skipCache = document.getElementById('skipCache') ? document.getElementById('skipCache').checked : false;

        placeholder.style.display = 'none';
        resultsContent.style.display = 'none';
        loading.style.display = 'flex';
        setOverallStatus('جاري المعالجة...', 'active');

        try {
            const res = await fetch('/api/search', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content
                },
                body: JSON.stringify({
                    product_name: ctx.product_name,
                    brand: ctx.brand,
                    product_name_ar: ctx.product_name_ar,
                    brand_ar: ctx.brand_ar,
                    category: ctx.category,
                    custom_query: document.getElementById('customQuery').value,
                    strict_brand_match: strictBrandMatch,
                    skip_cache: skipCache,
                    barcode: ctx.barcode,
                    sku_key: ctx.sku_key,
                    size: ctx.size,
                    sub_category: ctx.sub_category,
                    origin: ctx.origin,
                    row_number: ctx.row_number
                })
            });
            let data;
            try {
                data = await res.json();
            } catch (parseErr) {
                data = { status: 'error', error: `استجابة غير صالحة من الخادم (HTTP ${res.status})` };
            }
            loading.style.display = 'none';
            renderSearchResponse(data);
        } catch (err) {
            console.error(err);
            loading.style.display = 'none';
            placeholder.style.display = 'block';
            setOverallStatus('خطأ اتصال', 'failed');
        }
    });

    // بطاقة الصورة المرشحة: تعرض الصورة الأصلية ومصدرها وأدلتها. المعاينة بعد المعالجة
    // (عزل الخلفية على لوحة بيضاء) تظهر فقط بعد نجاح الاعتماد.
    function renderRecommendedCard(candidate, meta = {}) {
        const c = normalizeCandidate(candidate);
        const ctx = currentProductContext();
        const container = document.getElementById('recommendedContainer');
        container.textContent = '';

        const card = el('div', { className: 'glass-panel recommended-card', style: 'margin: 0 0 1.5rem 0; padding: 1.5rem;', dataset: { url: c.url } });
        const grid = el('div', { className: 'workbench-grid' });

        // العمود 1: الصورة الأصلية + مكان المعاينة بعد المعالجة
        const imageCol = el('div', { style: 'display: flex; flex-direction: column; gap: 0.75rem;' }, [
            el('div', { className: 'recommended-image-box' }, [
                el('img', { src: getImageUrl(c.url), alt: 'Source image', referrerpolicy: 'no-referrer' })
            ]),
            el('div', { className: 'form-help', style: 'font-size: 0.75rem; color: var(--text-secondary); text-align: center;',
                        text: 'الصورة الأصلية من المصدر. عزل الخلفية ووضعها على لوحة بيضاء يتم عند الاعتماد، وتظهر النتيجة هنا بعده.' }),
            el('div', { className: 'processed-preview', style: 'display: none;' })
        ]);

        // العمود 2: المصدر والأدلة والتحكم
        const provenance = el('div', { style: 'display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap;' });
        const st = CANDIDATE_STATUS_LABELS[c.status] || { text: c.status, cls: 'eligible' };
        provenance.appendChild(el('span', { className: `candidate-badge-inline ${st.cls}`, text: st.text }));
        if (meta.decision) provenance.appendChild(el('span', { className: 'evidence-chip neutral', text: DECISION_LABELS[meta.decision] || meta.decision }));
        const src = c.source.toLowerCase();
        if (src.includes('cache')) {
            provenance.appendChild(el('span', { className: 'evidence-chip unknown', text: 'المصدر: كاش اعتماد سابق' }));
        } else if (src === 'manual') {
            provenance.appendChild(el('span', { className: 'evidence-chip unknown', text: 'المصدر: رابط يدوي' }));
        }
        const domain = c.domain || hostOf(c.page_url) || hostOf(c.url);
        if (domain) provenance.appendChild(el('span', { className: 'evidence-chip neutral', text: `النطاق: ${domain}` }));
        const pageLink = safeHttpUrl(c.page_url);

        const methodSelect = el('select', { id: 'bgRemovalMethod', style: 'padding: 0.45rem 0.75rem; background: var(--input-bg); border: 1px solid var(--panel-border); border-radius: 6px; color: var(--text-primary); font-family: inherit; font-size: 0.8rem; width: 100%;' }, [
            el('option', { value: 'photoroom', selected: 'selected', text: 'PhotoRoom API (عزل سحابي — الافتراضي)' }),
            el('option', { value: 'remove_bg_api', text: 'Remove.bg API (عزل سحابي بديل)' }),
            el('option', { value: 'grabcut', text: 'GrabCut Local (عزل محلي تقريبي)' }),
            el('option', { value: 'none', text: 'بدون عزل (يُكتب الرابط بعلامة needs_review)' })
        ]);

        const approveBtn = el('button', { type: 'button', className: 'btn', id: 'confirmImageBtn', style: 'flex: 2; font-weight: 800;',
                                          onclick: (e) => approveCandidate(c, e.currentTarget) },
                              [el('i', { className: 'fas fa-check' }), ' اعتماد الصورة للشيت والرفع [A]']);
        const rejectBtn = el('button', { type: 'button', className: 'btn', id: 'rejectImageBtn',
                                         style: 'flex: 1; background: var(--danger-bg); border-color: var(--panel-border); color: var(--danger);',
                                         onclick: () => openRejectModal(c) },
                             [el('i', { className: 'fas fa-times' }), ' رفض [X]']);

        const infoCol = el('div', { style: 'display: flex; flex-direction: column; justify-content: space-between; gap: 0.75rem;' }, [
            el('div', { style: 'display: flex; flex-direction: column; gap: 0.6rem;' }, [
                provenance,
                el('h3', { style: 'font-size: 1.1rem; font-weight: 800; line-height: 1.45; margin: 0;', text: c.title || 'بدون عنوان' }),
                el('p', { style: 'color: var(--text-secondary); font-size: 0.85rem; margin: 0;' }, [
                    'المنتج: ', el('strong', { style: 'color: var(--text-primary);', text: ctx.product_name }),
                    ' — البراند: ', el('strong', { style: 'color: var(--text-primary);', text: ctx.brand })
                ]),
                renderWarnings(c),
                renderEvidenceChips(c),
                renderVlm(c),
                renderReasons(c),
                pageLink ? el('a', { href: pageLink, target: '_blank', rel: 'noopener noreferrer', style: 'font-size: 0.8rem; color: var(--accent-cyan);', text: 'فتح صفحة المصدر ↗' }) : null,
                el('div', { style: 'margin-top: 0.5rem; border-top: 1px solid var(--panel-border); padding-top: 0.75rem;' }, [
                    el('label', { for: 'bgRemovalMethod', style: 'font-size: 0.8rem; font-weight: 600; color: var(--text-secondary); display: block; margin-bottom: 0.35rem;', text: 'طريقة عزل الخلفية:' }),
                    methodSelect
                ])
            ]),
            el('div', { style: 'display: flex; gap: 1rem; border-top: 1px solid var(--panel-border); padding-top: 1rem;' }, [approveBtn, rejectBtn])
        ]);

        grid.appendChild(imageCol);
        grid.appendChild(infoCol);
        card.appendChild(grid);
        container.appendChild(card);
        return card;
    }

    // عرض الصورة بعد المعالجة (من رابط Cloudinary الذي أعاده الاعتماد)
    function showProcessedPreview(card, data) {
        if (!card) return;
        const box = card.querySelector('.processed-preview');
        if (!box) return;
        box.textContent = '';
        const link = String(data.image_link || data.cloudinary_url || '').replace(/^needs_review:/, '');
        if (link) {
            box.appendChild(el('div', { style: 'font-size: 0.8rem; font-weight: 800; color: var(--success);', text: 'المعاينة بعد المعالجة (المنشورة):' }));
            box.appendChild(el('div', { className: 'recommended-image-box' }, [el('img', { src: getImageUrl(link), alt: 'Processed image' })]));
        }
        if (data.warning === 'background_not_removed' || String(data.image_link || '').startsWith('needs_review:')) {
            box.appendChild(el('div', { className: 'outcome-banner warning', text: '⚠️ لم تتم إزالة الخلفية: كُتب الرابط في الشيت بعلامة needs_review ليراجع لاحقاً.' }));
        }
        box.style.display = box.childNodes.length ? 'block' : 'none';
    }

    // ما رآه المراجع عن الصورة التي يعتمدها أو يرفضها: يُحسب به دليل دقة الاختيار المسبق لكل براند
    function reviewedCandidateView(c, ctx) {
        const cacheHit = c.reasons.includes('cache_hit') || c.source === 'cache' || (c.evidence && c.evidence.source === 'cache');
        return {
            search_decision: ctx.search_decision,
            candidate_status: c.status,
            candidate_cache_hit: cacheHit,
            identity_tier: c.identity_tier === null ? '' : String(c.identity_tier),
            vlm_decision: (c.vlm && c.vlm.decision) ? String(c.vlm.decision) : ''
        };
    }

    // اعتماد صورة: يرسل الباركود و sku_key والتصنيف بمفاتيح category_l*_en
    async function approveCandidate(candidate, btn) {
        const c = normalizeCandidate(candidate);
        const ctx = currentProductContext();
        if (!ctx.row_number) {
            alert('يرجى اختيار منتج من الشيت أولاً.');
            return;
        }
        if ((c.status === 'rejected' || c.status === 'excluded') &&
            !confirm(`هذه الصورة مستبعدة آلياً:\n${c.reasons.join('\n') || c.status}\n\nهل أنت متأكد من اعتمادها يدوياً؟`)) {
            return;
        }
        const originalHtml = btn ? btn.innerHTML : '';
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'جاري المعالجة والرفع...';
        }
        const methodEl = document.getElementById('bgRemovalMethod');
        const aiEnhance = document.getElementById('aiEnhance') ? document.getElementById('aiEnhance').checked : false;

        try {
            const res = await fetch('/api/select_image', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content
                },
                body: JSON.stringify({
                    image_url: c.url,
                    page_url: c.page_url,
                    candidate_sha256: c.content_sha256,
                    product_name: ctx.product_name,
                    brand: ctx.brand,
                    row_number: ctx.row_number,
                    barcode: ctx.barcode,
                    sku_key: ctx.sku_key,
                    size: ctx.size,
                    ...reviewedCandidateView(c, ctx),
                    category_l1_en: document.getElementById('selectL1').value,
                    category_l2_en: document.getElementById('selectL2').value,
                    category_l3_en: document.getElementById('selectL3').value,
                    enhance: aiEnhance,
                    bg_removal_method: methodEl ? methodEl.value : 'photoroom',
                    target_width: getOutputWidth(),
                    target_height: getOutputHeight()
                })
            });
            let data;
            try {
                data = await res.json();
            } catch (parseErr) {
                data = { status: 'error', error: `استجابة غير صالحة من الخادم (HTTP ${res.status})` };
            }

            if (data.status === 'success') {
                const card = renderRecommendedCard(c, { decision: '' });
                showProcessedPreview(card, data);
                setOverallStatus('تم الاعتماد والرفع', 'success');
                alert(`🎉 تم رفع الصورة وتحديث الصف ${ctx.row_number} بنجاح!`);
                loadProducts();
            } else {
                alert(`❌ فشل الاعتماد: ${data.error || data.message || 'خطأ غير معروف'}`);
            }
        } catch (err) {
            console.error(err);
            alert('❌ خطأ اتصال بالخادم.');
        } finally {
            if (btn && btn.isConnected) {
                btn.disabled = false;
                btn.innerHTML = originalHtml;
            }
        }
    }

    // =============================================
    // نافذة سبب الرفض (رموز الهوية: WRONG_PRODUCT / WRONG_BRAND / WRONG_VARIANT ...)
    // =============================================
    let pendingRejectCandidate = null;

    function openRejectModal(candidate) {
        const ctx = currentProductContext();
        if (!ctx.row_number) {
            alert('يرجى اختيار منتج من الشيت أولاً.');
            return;
        }
        pendingRejectCandidate = normalizeCandidate(candidate);
        document.querySelectorAll('input[name="reject_reason_code"]').forEach(r => { r.checked = false; });
        document.getElementById('rejectModalUrl').textContent = pendingRejectCandidate.title || pendingRejectCandidate.url;
        document.getElementById('rejectReasonModal').style.display = 'flex';
    }

    function closeRejectModal() {
        document.getElementById('rejectReasonModal').style.display = 'none';
        pendingRejectCandidate = null;
    }

    async function submitReject() {
        const candidate = pendingRejectCandidate;
        if (!candidate) return;
        const checked = document.querySelector('input[name="reject_reason_code"]:checked');
        if (!checked) {
            alert('❌ يرجى اختيار سبب الرفض.');
            return;
        }
        const reasonCode = checked.value;
        const research = document.getElementById('rejectResearch').checked;
        const ctx = currentProductContext();
        const btn = document.getElementById('rejectConfirmBtn');
        btn.disabled = true;
        btn.textContent = research ? 'جاري الرفض وإعادة البحث...' : 'جاري التسجيل...';

        try {
            const res = await fetch('/api/reject_image', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content
                },
                body: JSON.stringify({
                    row_number: ctx.row_number,
                    image_url: candidate.url,
                    page_url: candidate.page_url,
                    candidate_sha256: candidate.content_sha256 || null,
                    product_name: ctx.product_name,
                    brand: ctx.brand,
                    barcode: ctx.barcode,
                    sku_key: ctx.sku_key,
                    reason_code: reasonCode,
                    rejection_reasons: [reasonCode],
                    ...reviewedCandidateView(candidate, ctx),
                    research: research,
                    product_name_ar: ctx.product_name_ar,
                    brand_ar: ctx.brand_ar,
                    category: ctx.category,
                    size: ctx.size,
                    sub_category: ctx.sub_category,
                    origin: ctx.origin,
                    custom_query: document.getElementById('customQuery').value
                })
            });
            let data;
            try {
                data = await res.json();
            } catch (parseErr) {
                data = { status: 'error', error: `استجابة غير صالحة من الخادم (HTTP ${res.status})` };
            }
            if (data.status === 'error' || data.status === 'failed') {
                alert('❌ فشل تسجيل الرفض: ' + (data.error || data.message || 'خطأ غير معروف'));
                return;
            }
            closeRejectModal();
            if (research) {
                // الرفض حذف مرشحات المنتج المحفوظة؛ نحفظ المرشحين الجدد كي يبقى المنتج في تبويب المراجعة
                await persistResearchCandidates(ctx, data, candidate.url);
            }
            loadProducts(); // تحديث حالة القائمة الجانبية بالخلفية

            const note = `تم رفض الصورة (${reasonCode}) واستبعادها من عمليات البحث القادمة لهذا المنتج.`;
            if (research && (Array.isArray(data.candidates) || data.decision)) {
                renderSearchResponse(data, note);
            } else {
                showRejectedState(note);
            }
        } catch (err) {
            console.error(err);
            alert('❌ خطأ اتصال بالخادم.');
        } finally {
            btn.disabled = false;
            btn.textContent = 'تأكيد الرفض';
        }
    }

    // حفظ مرشحي إعادة البحث بعد الرفض (مع page_url والطبقة) في curation_candidates
    async function persistResearchCandidates(ctx, data, rejectedUrl) {
        const fresh = collectCandidates(data).filter(c => c.url !== rejectedUrl);
        if (!fresh.length) return;
        try {
            await fetch('/api/v1/curation/save-candidates', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content
                },
                body: JSON.stringify({
                    row_number: parseInt(ctx.row_number, 10),
                    product_name: ctx.product_name,
                    brand: ctx.brand,
                    sku_key: data.sku_key || ctx.sku_key,
                    candidates: fresh
                })
            });
        } catch (err) {
            console.warn('[Curation Save Candidates Error]', err);
        }
    }

    // بعد الرفض بدون إعادة بحث: حقل استعلام مخصص لإعادة البحث
    function showRejectedState(note) {
        const ctx = currentProductContext();
        const recommendedContainer = document.getElementById('recommendedContainer');
        recommendedContainer.textContent = '';
        const queryInput = el('input', { type: 'text', id: 'rejectionSearchQuery',
            style: 'flex: 1; padding: 0.6rem 1rem; background: var(--input-bg); border: 1px solid var(--panel-border); border-radius: 10px; color: var(--text-primary); font-family: inherit; font-size: 0.9rem;' });
        queryInput.value = document.getElementById('customQuery').value || (ctx.brand + ' ' + ctx.product_name).trim();
        recommendedContainer.appendChild(el('div', {
            style: 'background-color: var(--danger-bg); border: 1px solid var(--panel-border); color: var(--text-primary); border-radius: 16px; padding: 1.5rem; text-align: center; display: flex; flex-direction: column; gap: 1rem; margin-bottom: 1.5rem;'
        }, [
            el('div', { style: 'font-size: 1.1rem; font-weight: bold; color: var(--danger);', text: note }),
            el('p', { style: 'font-size: 0.9rem; color: var(--text-secondary); margin: 0;', text: 'لتصحيح البحث اكتب استعلاماً مخصصاً ثم اضغط "إعادة البحث":' }),
            el('div', { style: 'display: flex; gap: 0.75rem; max-width: 500px; margin: 0 auto; width: 100%;' }, [
                queryInput,
                el('button', { type: 'button', className: 'btn', style: 'background: var(--accent-gradient); color: var(--btn-text); font-weight: bold; padding: 0.6rem 1.25rem;',
                               onclick: retrySearchWithCustomQuery, text: 'إعادة البحث' })
            ])
        ]));
        setOverallStatus('تم الرفض (في انتظار تحسين البحث)', 'failed');
    }

    // دالة إعادة البحث الفوري باستعلام مخصص بعد استبعاد صورة
    function retrySearchWithCustomQuery() {
        const queryVal = document.getElementById('rejectionSearchQuery').value.trim();
        if (!queryVal) {
            alert('❌ يرجى كتابة استعلام بحث أولاً.');
            return;
        }
        document.getElementById('customQuery').value = queryVal;
        document.getElementById('submitBtn').click();
    }

    // شبكة المرشحين: كل مرشح يعرض حالته وأسبابه وأدلته وما قرأه نموذج الرؤية
    function buildCandidateCard(c, idx) {
        const st = CANDIDATE_STATUS_LABELS[c.status] || { text: c.status, cls: 'eligible' };
        const card = el('div', { className: 'candidate-card', dataset: { url: c.url, status: c.status } });
        card.appendChild(el('div', { className: 'candidate-img-box' }, [
            el('span', { className: `candidate-badge ${st.cls}`, text: st.text }),
            el('img', { src: getImageUrl(c.url), alt: 'Candidate', loading: 'lazy', referrerpolicy: 'no-referrer' })
        ]));
        const meta = el('div', { className: 'candidate-meta' });
        if (c.width && c.height) meta.appendChild(el('span', { text: `${c.width}×${c.height}` }));
        const pageLink = safeHttpUrl(c.page_url);
        if (pageLink) meta.appendChild(el('a', { href: pageLink, target: '_blank', rel: 'noopener noreferrer', style: 'color: var(--accent-cyan);', text: 'صفحة المصدر ↗' }));

        card.appendChild(el('div', { className: 'candidate-info' }, [
            el('div', { className: 'candidate-title', title: c.title, text: c.title || 'بدون عنوان' }),
            renderWarnings(c),
            renderEvidenceChips(c),
            renderVlm(c),
            meta,
            renderReasons(c),
            el('div', { className: 'candidate-actions' }, [
                el('button', { type: 'button', className: 'btn btn-secondary btn-sm js-approve-candidate',
                               onclick: (e) => approveCandidate(c, e.currentTarget), text: `🎯 اعتماد [${idx + 1}]` }),
                el('button', { type: 'button', className: 'btn btn-secondary btn-sm js-reject-candidate',
                               style: 'color: var(--danger);', onclick: () => openRejectModal(c), text: '🚫 رفض' })
            ])
        ]));
        return card;
    }

    function renderCandidatesGrid(candidates) {
        const container = document.getElementById('candidatesContainer');
        container.textContent = '';
        if (!candidates || candidates.length === 0) {
            container.appendChild(el('p', { style: 'color: var(--text-secondary); grid-column: 1/-1; text-align: center; padding: 2rem;',
                                            text: 'لا توجد صور مرشحة لهذا البحث.' }));
            return;
        }
        candidates.forEach((c, idx) => container.appendChild(buildCandidateCard(c, idx)));
    }

    // رندرة أكورديون سجل التتبع (نص فقط عبر textContent)
    function renderAccordionTrace(trace) {
        const container = document.getElementById('accordionContainer');
        container.textContent = '';
        if (!trace || typeof trace !== 'object' || Object.keys(trace).length === 0) {
            container.appendChild(el('p', { style: 'color: var(--text-secondary); text-align: center;', text: 'لا يوجد سجل تتبع متاح.' }));
            return;
        }
        const items = [];
        if (Array.isArray(trace.steps)) {
            trace.steps.forEach((step, idx) => {
                const count = step.results_count !== undefined ? step.results_count : (step.candidates ? step.candidates.length : 0);
                items.push({ title: `الخطوة ${idx + 1}: ${step.name || step.query || ''} (${count} نتائج)`, body: step });
            });
        }
        const rest = Object.assign({}, trace);
        delete rest.steps;
        if (Object.keys(rest).length) {
            items.unshift({ title: 'ملخص التتبع (الاستعلامات، صحة المحركات، النتيجة)', body: rest });
        }
        items.forEach(item => {
            const header = el('div', { className: 'step-header', onclick: (e) => toggleAccordionItem(e.currentTarget) }, [
                el('span', { text: item.title }), el('i', { className: 'fas fa-chevron-down' })
            ]);
            const body = el('div', { className: 'step-body' }, [
                el('pre', { style: 'color: var(--accent-cyan); font-size: 0.8rem; overflow-x: auto; font-family: monospace; white-space: pre-wrap; direction: ltr; text-align: left;',
                            text: JSON.stringify(item.body, null, 2) })
            ]);
            container.appendChild(el('div', { className: 'step-item' }, [header, body]));
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
        if (!safeHttpUrl(url)) {
            alert('يرجى إدخال رابط يبدأ بـ http أو https.');
            return;
        }
        showResultsWorkspace();
        renderRecommendedCard({ url: url, title: 'صورة مدخلة يدوياً بواسطة المستخدم', status: 'pending', source: 'manual' }, {});
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
            const aiEnhance = document.getElementById('aiEnhance') ? document.getElementById('aiEnhance').checked : false;
            formData.append('file', blob, 'manual_upload.png');
            formData.append('row_number', row);
            formData.append('product_name', name);
            formData.append('brand', brand);
            formData.append('barcode', barcode);
            formData.append('sku_key', document.getElementById('searchForm').dataset.skuKey || '');
            formData.append('search_decision', document.getElementById('searchForm').dataset.searchDecision || '');
            formData.append('enhance', aiEnhance ? 'true' : 'false');
            formData.append('target_width',  getOutputWidth());
            formData.append('target_height', getOutputHeight());
            
            closeEditorModal();
            
            document.getElementById('placeholder').style.display = 'none';
            document.getElementById('resultsContent').style.display = 'none';
            const loading = document.getElementById('loading');
            loading.style.display = 'flex';
            document.getElementById('loadingDetails').innerText = 'جاري عزل خلفية الصورة المرفوعة ووضعها على اللوحة البيضاء...';
            
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
                    
                    showResultsWorkspace();
                    const card = renderRecommendedCard({ url: String(data.image_link || '').replace(/^needs_review:/, ''),
                                                         title: 'الصورة المرفوعة والمعالجة يدوياً', status: 'pending', source: 'manual' }, {});
                    showProcessedPreview(card, data);
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

    // تصفية السجلات في الطرفية يدوياً
    function filterTerminalLogs() {
        const query = document.getElementById('terminalLogSearch').value.toLowerCase().trim();
        const consoleDiv = document.getElementById('liveConsoleLogs');
        if (!lastLogs || lastLogs.length === 0) return;
        
        consoleDiv.innerHTML = lastLogs.map(log => {
            if (query && !log.toLowerCase().includes(query)) return null;
            
            let style = 'color: var(--text-primary);';
            if (log.includes('❌')) style = 'color: var(--danger);';
            if (log.includes('⚠️')) style = 'color: var(--warning);';
            if (log.includes('⚡') || log.includes('🎉')) style = 'color: var(--text-secondary);';
            return `<p style="${style} margin: 0; padding: 2px 0;">${escapeHtml(log)}</p>`;
        }).filter(Boolean).join('');
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
                    filterTerminalLogs();
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

    // =============================================
    // 🎛️ Image Output Settings Helpers
    // =============================================
    function applyPreset() {
        const preset = document.getElementById('outputPreset').value;
        const box = document.getElementById('customDimBox');
        box.style.display = preset === 'custom' ? 'block' : 'none';
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
        if (document.getElementById('rejectReasonModal').style.display === 'flex') {
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
            const rejectBtn = document.getElementById('rejectImageBtn');
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
                const actionBtn = candidates[idx].querySelector('.js-approve-candidate');
                if (actionBtn && !actionBtn.disabled) {
                    e.preventDefault();
                    actionBtn.click();
                }
            }
        }
    });
</script>
@endsection
