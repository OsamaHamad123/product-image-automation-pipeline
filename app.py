# app.py
from flask import Flask, request, jsonify, render_template_string
import os
import sys
import image_search
import image_processor
import cloudinary_storage
import google_sheets
import config
import requests
import categories

app = Flask(__name__)

# Premium, modern dashboard with spreadsheet integration and manual override curation console
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🤖 لوحة إدارة وأتمتة صور المنتجات</title>
    <!-- Google Fonts & FontAwesome Icons -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Tajawal:wght@300;500;700;900&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    
    <style>
        :root {
            --bg-color: #060913;
            --panel-bg: rgba(15, 23, 42, 0.65);
            --panel-border: rgba(255, 255, 255, 0.08);
            --accent-purple: #8257e5;
            --accent-cyan: #00e5ff;
            --accent-gradient: linear-gradient(135deg, #8257e5, #00e5ff);
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
            --success: #10b981;
            --danger: #ef4444;
            --warning: #f59e0b;
            --info: #3b82f6;
            --success-bg: rgba(16, 185, 129, 0.1);
            --danger-bg: rgba(239, 68, 68, 0.1);
            --warning-bg: rgba(245, 158, 11, 0.1);
            --info-bg: rgba(59, 130, 246, 0.1);
        }

        body.light-theme {
            --bg-color: #f3f4f6;
            --panel-bg: rgba(255, 255, 255, 0.75);
            --panel-border: rgba(0, 0, 0, 0.08);
            --text-primary: #111827;
            --text-secondary: #4b5563;
            --success-bg: rgba(16, 185, 129, 0.15);
            --danger-bg: rgba(239, 68, 68, 0.15);
            --warning-bg: rgba(245, 158, 11, 0.15);
            --info-bg: rgba(59, 130, 246, 0.15);
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Tajawal', 'Outfit', sans-serif;
            background-color: var(--bg-color);
            background-image: 
                radial-gradient(circle at 10% 20%, rgba(130, 87, 229, 0.06) 0%, transparent 40%),
                radial-gradient(circle at 90% 80%, rgba(0, 229, 255, 0.05) 0%, transparent 40%);
            background-attachment: fixed;
            color: var(--text-primary);
            padding: 2rem;
            line-height: 1.6;
            transition: background-color 0.3s, color 0.3s;
        }

        .container {
            max-width: 1600px;
            margin: 0 auto;
        }

        /* Glassmorphism Header */
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2rem;
            padding: 1.5rem 2rem;
            background: var(--panel-bg);
            border: 1px solid var(--panel-border);
            border-radius: 16px;
            backdrop-filter: blur(12px);
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
        }

        header h1 {
            font-size: 2.2rem;
            font-weight: 900;
            background: var(--accent-gradient);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        header p {
            color: var(--text-secondary);
            font-size: 0.95rem;
            margin-top: 0.25rem;
        }

        /* Statistics Cards */
        .stats-row {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }

        .stat-card {
            background: var(--panel-bg);
            border: 1px solid var(--panel-border);
            border-radius: 16px;
            padding: 1.5rem;
            display: flex;
            align-items: center;
            gap: 1.25rem;
            backdrop-filter: blur(12px);
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.2);
            transition: transform 0.3s, border-color 0.3s;
        }

        .stat-card:hover {
            transform: translateY(-4px);
            border-color: rgba(255, 255, 255, 0.15);
        }

        .stat-icon {
            width: 54px;
            height: 54px;
            border-radius: 12px;
            background: rgba(130, 87, 229, 0.15);
            color: var(--accent-purple);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.6rem;
        }

        .stat-icon.success {
            background: rgba(16, 185, 129, 0.15);
            color: var(--success);
        }

        .stat-icon.danger {
            background: rgba(239, 68, 68, 0.15);
            color: var(--danger);
        }

        .stat-icon.warning {
            background: rgba(245, 158, 11, 0.15);
            color: var(--warning);
        }

        .stat-info {
            display: flex;
            flex-direction: column;
        }

        .stat-label {
            color: var(--text-secondary);
            font-size: 0.85rem;
            font-weight: 500;
        }

        .stat-value {
            font-size: 1.6rem;
            font-weight: 800;
            color: var(--text-primary);
        }

        .stat-value.success { color: var(--success); }
        .stat-value.danger { color: var(--danger); }
        .stat-value.warning { color: var(--warning); }

        /* Main Grid */
        .layout-grid {
            display: grid;
            grid-template-columns: 380px 1fr;
            gap: 2rem;
        }

        @media (max-width: 1200px) {
            .layout-grid {
                grid-template-columns: 1fr;
            }
        }

        /* Glass Panel Container */
        .glass-panel {
            background: var(--panel-bg);
            border: 1px solid var(--panel-border);
            border-radius: 20px;
            padding: 2rem;
            backdrop-filter: blur(12px);
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }

        /* Sidebar styling */
        .sidebar {
            max-height: 85vh;
            overflow-y: hidden;
            display: flex;
            flex-direction: column;
        }

        .sidebar-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--panel-border);
            padding-bottom: 1rem;
        }

        .sidebar-search-container {
            position: relative;
            width: 100%;
        }

        .sidebar-search-container input {
            width: 100%;
            padding: 0.75rem 1rem 0.75rem 2.5rem;
            background: rgba(8, 12, 20, 0.6);
            border: 1px solid var(--panel-border);
            border-radius: 10px;
            color: var(--text-primary);
            font-family: inherit;
            transition: border-color 0.3s;
        }

        .sidebar-search-container input:focus {
            outline: none;
            border-color: var(--accent-purple);
        }

        .sidebar-search-container i {
            position: absolute;
            left: 1rem;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-secondary);
        }

        /* Tabs Navigation */
        .tabs-nav {
            display: flex;
            background: rgba(8, 12, 20, 0.6);
            border: 1px solid var(--panel-border);
            padding: 0.25rem;
            border-radius: 10px;
        }

        .tab-btn {
            flex: 1;
            padding: 0.5rem;
            border: none;
            background: transparent;
            color: var(--text-secondary);
            font-family: inherit;
            font-weight: 700;
            font-size: 0.85rem;
            border-radius: 8px;
            cursor: pointer;
            transition: background 0.3s, color 0.3s;
            text-align: center;
        }

        .tab-btn.active {
            background: var(--accent-purple);
            color: #fff;
        }

        .product-list {
            overflow-y: auto;
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            padding-right: 0.25rem;
        }

        /* Customize Scrollbars */
        ::-webkit-scrollbar {
            width: 6px;
            height: 6px;
        }
        ::-webkit-scrollbar-track {
            background: transparent;
        }
        ::-webkit-scrollbar-thumb {
            background: var(--panel-border);
            border-radius: 4px;
        }
        ::-webkit-scrollbar-thumb:hover {
            background: rgba(255, 255, 255, 0.15);
        }

        .product-item {
            background: rgba(8, 12, 20, 0.4);
            border: 1px solid var(--panel-border);
            border-radius: 12px;
            padding: 1rem;
            cursor: pointer;
            transition: all 0.3s;
            position: relative;
        }

        .product-item:hover {
            border-color: var(--accent-purple);
            background: rgba(130, 87, 229, 0.05);
            transform: translateX(-3px);
        }

        .product-item.active {
            border-color: var(--accent-cyan);
            background: rgba(0, 229, 255, 0.05);
            box-shadow: 0 0 15px rgba(0, 229, 255, 0.2);
            animation: pulse-border 1.5s infinite alternate;
        }

        @keyframes pulse-border {
            0% { box-shadow: 0 0 10px rgba(0, 229, 255, 0.1); border-color: rgba(0, 229, 255, 0.5); }
            100% { box-shadow: 0 0 20px rgba(0, 229, 255, 0.3); border-color: rgba(0, 229, 255, 1); }
        }

        .product-item h4 {
            font-size: 0.95rem;
            font-weight: 700;
            margin-bottom: 0.35rem;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 250px;
        }

        .product-item p {
            font-size: 0.8rem;
            color: var(--text-secondary);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .badge-row-number {
            position: absolute;
            top: 0.5rem;
            left: 0.5rem;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--panel-border);
            color: var(--text-secondary);
            font-size: 0.75rem;
            font-weight: bold;
            padding: 0.1rem 0.4rem;
            border-radius: 4px;
        }

        /* Form styling */
        .form-grid {
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 1.5rem;
        }

        @media (max-width: 768px) {
            .form-grid {
                grid-template-columns: 1fr;
            }
        }

        .form-group {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }

        .form-group label {
            font-weight: 700;
            font-size: 0.9rem;
            color: var(--text-secondary);
        }

        .form-group input[type="text"], .form-group select {
            width: 100%;
            padding: 0.75rem 1rem;
            background: rgba(8, 12, 20, 0.6);
            border: 1px solid var(--panel-border);
            border-radius: 10px;
            color: var(--text-primary);
            font-family: inherit;
            font-size: 0.95rem;
            transition: border-color 0.3s;
        }

        .form-group input[type="text"]:focus, .form-group select:focus {
            outline: none;
            border-color: var(--accent-purple);
        }

        /* iOS Switch Toggle styling */
        .toggles-row {
            display: flex;
            gap: 2rem;
            margin-top: 0.5rem;
            flex-wrap: wrap;
        }

        .toggle-container {
            display: flex;
            align-items: center;
            gap: 0.75rem;
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
            transition: .3s;
            border-radius: 24px;
        }

        .slider:before {
            position: absolute;
            content: "";
            height: 18px;
            width: 18px;
            left: 3px;
            bottom: 3px;
            background-color: white;
            transition: .3s;
            border-radius: 50%;
        }

        input:checked + .slider {
            background-color: var(--accent-purple);
        }

        input:checked + .slider:before {
            transform: translateX(20px);
        }

        .btn {
            padding: 0.8rem 1.5rem;
            background: var(--accent-gradient);
            color: #fff;
            border: none;
            border-radius: 10px;
            font-size: 0.95rem;
            font-weight: 800;
            cursor: pointer;
            transition: all 0.3s;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
            box-shadow: 0 4px 15px rgba(130, 87, 229, 0.2);
        }

        .btn:hover {
            opacity: 0.95;
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(130, 87, 229, 0.3);
        }

        .btn.btn-secondary {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--panel-border);
            color: var(--text-primary);
            box-shadow: none;
        }

        .btn.btn-secondary:hover {
            background: rgba(255, 255, 255, 0.08);
            border-color: rgba(255, 255, 255, 0.2);
            box-shadow: none;
        }

        .btn.btn-success {
            background: linear-gradient(135deg, var(--success), #059669);
            box-shadow: 0 4px 15px rgba(16, 185, 129, 0.2);
        }

        .btn.btn-success:hover {
            box-shadow: 0 6px 20px rgba(16, 185, 129, 0.3);
        }

        .btn.btn-sm {
            padding: 0.45rem 0.9rem;
            font-size: 0.8rem;
            border-radius: 8px;
        }

        /* Results view */
        .results-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--panel-border);
            padding-bottom: 1rem;
        }

        /* Recommended Card (Premium Glass Glow) */
        .recommended-card {
            background: linear-gradient(135deg, rgba(16, 185, 129, 0.05), rgba(0, 229, 255, 0.03));
            border: 1px dashed var(--success);
            border-radius: 16px;
            padding: 1.5rem;
            display: flex;
            gap: 2rem;
            align-items: center;
            box-shadow: 0 0 25px rgba(16, 185, 129, 0.08);
        }

        @media (max-width: 768px) {
            .recommended-card {
                flex-direction: column;
                text-align: center;
                gap: 1rem;
            }
        }

        .recommended-card img {
            width: 160px;
            height: 160px;
            object-fit: contain;
            background-color: rgba(8, 12, 20, 0.6);
            border: 1px solid var(--panel-border);
            border-radius: 12px;
            padding: 0.5rem;
        }

        .recommended-details {
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }

        .recommended-details h3 {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            color: var(--success);
            font-weight: 800;
        }

        .recommended-details p {
            font-size: 0.9rem;
            color: var(--text-secondary);
        }

        /* Candidate Cards Grid */
        .candidates-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
            gap: 1.5rem;
        }

        .candidate-card {
            background: rgba(8, 12, 20, 0.4);
            border: 1px solid var(--panel-border);
            border-radius: 16px;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            transition: all 0.3s;
            position: relative;
        }

        .candidate-card:hover {
            transform: translateY(-5px);
            border-color: var(--accent-purple);
            box-shadow: 0 10px 25px rgba(130, 87, 229, 0.1);
        }

        .candidate-img-box {
            height: 180px;
            width: 100%;
            background: rgba(8, 12, 20, 0.8);
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 1rem;
            position: relative;
            border-bottom: 1px solid var(--panel-border);
        }

        .candidate-img-box img {
            max-height: 100%;
            max-width: 100%;
            object-fit: contain;
            transition: transform 0.3s;
        }

        .candidate-card:hover .candidate-img-box img {
            transform: scale(1.08);
        }

        /* Badges overlay */
        .candidate-badge {
            position: absolute;
            top: 0.75rem;
            right: 0.75rem;
            padding: 0.25rem 0.5rem;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: bold;
            z-index: 5;
        }

        .candidate-badge.accepted {
            background: var(--success);
            color: white;
        }

        .candidate-badge.rejected {
            background: var(--danger);
            color: white;
        }

        .candidate-score-tag {
            position: absolute;
            bottom: 0.75rem;
            right: 0.75rem;
            background: rgba(8, 12, 20, 0.85);
            border: 1px solid var(--panel-border);
            padding: 0.2rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: bold;
            color: var(--accent-cyan);
        }

        .candidate-uae-tag {
            position: absolute;
            bottom: 0.75rem;
            left: 0.75rem;
            background: rgba(0, 229, 255, 0.15);
            border: 1px solid rgba(0, 229, 255, 0.3);
            padding: 0.2rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: bold;
            color: var(--accent-cyan);
        }

        .candidate-info {
            padding: 1.25rem;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            flex: 1;
        }

        .candidate-title {
            font-weight: bold;
            font-size: 0.85rem;
            line-height: 1.4;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
            height: 2.4rem;
        }

        .candidate-meta {
            font-size: 0.75rem;
            color: var(--text-secondary);
            display: flex;
            justify-content: space-between;
        }

        .candidate-reasons {
            font-size: 0.75rem;
            color: var(--danger);
            background: rgba(239, 68, 68, 0.08);
            border: 1px solid rgba(239, 68, 68, 0.15);
            padding: 0.5rem;
            border-radius: 6px;
            margin-top: 0.25rem;
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
            width: 60px;
            height: 60px;
            margin-bottom: 1.5rem;
        }

        .spinner-ring {
            box-sizing: border-box;
            display: block;
            position: absolute;
            width: 60px;
            height: 60px;
            border: 5px solid transparent;
            border-radius: 50%;
            animation: spin-ring 1.2s cubic-bezier(0.5, 0, 0.5, 1) infinite;
            border-top-color: var(--accent-purple);
        }
        .spinner-ring:nth-child(1) { animation-delay: -0.45s; }
        .spinner-ring:nth-child(2) { animation-delay: -0.3s; }
        .spinner-ring:nth-child(3) { animation-delay: -0.15s; }

        @keyframes spin-ring {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }

        /* Accordion Terminal log view */
        .step-accordion {
            margin-top: 1rem;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
        }

        .step-item {
            border: 1px solid var(--panel-border);
            border-radius: 12px;
            overflow: hidden;
            background: rgba(8, 12, 20, 0.4);
        }

        .step-header {
            background: rgba(21, 28, 44, 0.4);
            padding: 1rem;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-weight: 700;
            font-size: 0.9rem;
            transition: background 0.3s;
        }

        .step-header:hover {
            background: rgba(21, 28, 44, 0.7);
        }

        .step-body {
            background: #04060b;
            padding: 1.25rem;
            display: none;
            border-top: 1px solid var(--panel-border);
        }

        .step-body.active {
            display: block;
        }

        /* Status colors */
        .status-text {
            font-weight: bold;
        }
        .status-text.active { color: var(--warning); }
        .status-text.success { color: var(--success); }
        .status-text.failed { color: var(--danger); }
    </style>
</head>
<body>

    <div class="container">
        <!-- Dashboard Header -->
        <header>
            <div>
                <h1><i class="fas fa-robot"></i> لوحة إدارة وأتمتة صور المنتجات</h1>
                <p>البحث البصري الذكي المتقدم بـ Gemini 3.5 Flash & CLIP للصور وتحديث Google Sheet تلقائياً</p>
            </div>
            <div style="display: flex; gap: 1rem; align-items: center;">
                <button class="btn btn-secondary btn-sm" onclick="toggleTheme()"><i class="fas fa-sun" id="themeIcon"></i> وضع المظهر</button>
                <button class="btn btn-success" id="runAllBtn" onclick="runAllAutomation()"><i class="fas fa-play"></i> تشغيل الكل (أتمتة الشيت بالكامل)</button>
                <button class="btn btn-secondary" onclick="loadProducts()"><i class="fas fa-sync"></i> تحديث من Google Sheet</button>
            </div>
        </header>

        <!-- Real-time Batch Progress Panel -->
        <div id="batchProgressPanel" class="stat-card" style="display: none; background: linear-gradient(135deg, rgba(130, 87, 229, 0.15), rgba(0, 229, 255, 0.15)); border: 1px solid rgba(0, 229, 255, 0.3); margin-bottom: 1.5rem; flex-direction: column; align-items: stretch; gap: 0.8rem; padding: 1.25rem; width: 100%;">
            <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
                <span style="font-weight: 800; font-size: 1.1rem; color: var(--accent-cyan); display: flex; align-items: center; gap: 0.5rem;">
                    <i class="fas fa-spinner fa-spin"></i> جاري تشغيل الأتمتة بالخلفية...
                </span>
                <span id="batchProgressPercent" style="font-weight: 800; font-size: 1.1rem; color: var(--text-primary);">0%</span>
            </div>
            
            <!-- Progress bar outer -->
            <div style="width: 100%; height: 10px; background: rgba(255, 255, 255, 0.1); border-radius: 5px; overflow: hidden; position: relative;">
                <div id="batchProgressBar" style="width: 0%; height: 100%; background: var(--accent-gradient); border-radius: 5px; transition: width 0.4s ease;"></div>
            </div>
            
            <div style="display: flex; justify-content: space-between; font-size: 0.85rem; color: var(--text-secondary); width: 100%;">
                <span id="batchProgressText">جاري فحص المنتجات المفقودة...</span>
                <span id="batchProgressCounts">0 / 0</span>
            </div>
        </div>

        <!-- Live KPI summary row -->
        <div class="stats-row">
            <div class="stat-card">
                <div class="stat-icon"><i class="fas fa-boxes"></i></div>
                <div class="stat-info">
                    <span class="stat-label">إجمالي المنتجات بالشيت</span>
                    <span class="stat-value" id="statTotal">0</span>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-icon success"><i class="fas fa-check-circle"></i></div>
                <div class="stat-info">
                    <span class="stat-label">منتجات بروابط صور</span>
                    <span class="stat-value success" id="statLinked">0</span>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-icon danger"><i class="fas fa-image-slash"></i></div>
                <div class="stat-info">
                    <span class="stat-label">صور مفقودة</span>
                    <span class="stat-value danger" id="statMissing">0</span>
                </div>
            </div>
            <div class="stat-card">
                <div class="stat-icon warning"><i class="fas fa-chart-line"></i></div>
                <div class="stat-info">
                    <span class="stat-label">نسبة الإنجاز الكلية</span>
                    <span class="stat-value warning" id="statPercentage">0%</span>
                </div>
            </div>
        </div>

        <!-- Live Webhook & API Usage metrics row -->
        <div class="stats-row" style="margin-top: 1rem; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem;">
            <div class="stat-card" style="background: rgba(30, 41, 59, 0.4);">
                <div class="stat-icon" style="color: #00e5ff;"><i class="fas fa-network-wired"></i></div>
                <div class="stat-info">
                    <span class="stat-label">حالة مستمع الشيت (Webhook)</span>
                    <span class="stat-value" style="font-size: 1.2rem; color: #00e5ff; font-weight: bold;">نشط ومتصل</span>
                </div>
            </div>
            <div class="stat-card" style="background: rgba(30, 41, 59, 0.4);">
                <div class="stat-icon" style="color: #e040fb;"><i class="fas fa-brain"></i></div>
                <div class="stat-info">
                    <span class="stat-label">مطالبات Gemini API الكلية</span>
                    <span class="stat-value" id="metricsGemini" style="font-size: 1.2rem; color: #e040fb; font-weight: bold;">0</span>
                </div>
            </div>
            <div class="stat-card" style="background: rgba(30, 41, 59, 0.4);">
                <div class="stat-icon" style="color: #00e676;"><i class="fas fa-cloud-upload-alt"></i></div>
                <div class="stat-info">
                    <span class="stat-label">رفع كلويديناري الناجح</span>
                    <span class="stat-value" id="metricsCloudinary" style="font-size: 1.2rem; color: #00e676; font-weight: bold;">0</span>
                </div>
            </div>
            <div class="stat-card" style="background: rgba(30, 41, 59, 0.4);">
                <div class="stat-icon" style="color: #ff9100;"><i class="fas fa-wallet"></i></div>
                <div class="stat-info">
                    <span class="stat-label">تكلفة الاستهلاك المقدرة</span>
                    <span class="stat-value" id="metricsCost" style="font-size: 1.2rem; color: #ff9100; font-weight: bold;">$0.0000 USD</span>
                </div>
            </div>
        </div>

        <div class="layout-grid">
            <!-- Sidebar: Products from Google Sheet -->
            <div class="glass-panel sidebar">
                <div class="sidebar-header">
                    <h3><i class="fas fa-file-spreadsheet"></i> المنتجات في الشيت</h3>
                    <span id="sheetProductCount" class="score-badge" style="font-size: 0.95rem;">0</span>
                </div>
                
                <!-- Search bar & filter tabs -->
                <div class="sidebar-search-container">
                    <i class="fas fa-search"></i>
                    <input type="text" id="sidebarSearch" placeholder="ابحث باسم المنتج أو البراند..." oninput="filterProducts()">
                </div>
                
                <div class="tabs-nav">
                    <button class="tab-btn active" id="tab-all" onclick="setFilterTab('all')">الكل</button>
                    <button class="tab-btn" id="tab-missing" onclick="setFilterTab('missing')">المفقودة</button>
                    <button class="tab-btn" id="tab-linked" onclick="setFilterTab('linked')">المكتملة</button>
                </div>
                
                <div id="productList" class="product-list">
                    <p style="color: var(--text-secondary); text-align: center; padding: 2rem;">جاري تحميل المنتجات...</p>
                </div>
            </div>

            <!-- Main Panel: Search Curation & Logs -->
            <div class="main-content">
                <!-- Row 1: Search Form -->
                <div class="glass-panel">
                    <h3 style="font-size: 1.15rem; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.5rem; display: flex; align-items: center; gap: 0.5rem;">
                        <i class="fas fa-sliders-h"></i> معايير البحث الذكي والفرز
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
                                </div>
                            </div>
                        </div>
                        <button type="submit" class="btn" id="submitBtn" style="margin-top: 1.2rem; width: 100%;">
                            <i class="fas fa-search-plus"></i> ابدأ الفحص البصري والبحث الذكي
                        </button>
                    </form>
                </div>

                <!-- Row 2: Search Curation Results -->
                <div class="glass-panel" style="min-height: 450px;">
                    <div class="results-header">
                        <h3><i class="fas fa-eye"></i> نتائج الفرز والمطابقة البصرية</h3>
                        <span id="overallStatus" class="status-text active">في انتظار الإدخال</span>
                    </div>

                    <!-- Placeholder -->
                    <div id="placeholder" style="text-align: center; color: var(--text-secondary); padding: 5rem 0;">
                        <i class="far fa-image" style="font-size: 3.5rem; margin-bottom: 1.5rem; opacity: 0.3; display: block;"></i>
                        اختر منتجاً من الشيت على اليمين للتحليل، أو ادخل البيانات يدوياً للبحث الفوري وعرض تفاصيل الفحص بـ Gemini Vision و CLIP.
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

                    <!-- Results Panel -->
                    <div id="resultsContent" style="display: none;">
                        <!-- Recommended Image Card -->
                        <div id="recommendedContainer"></div>

                        <!-- Manual Image URL Override Section -->
                        <div style="margin-top: 1.5rem; padding: 1.5rem; background: rgba(255, 255, 255, 0.03); border: 1px solid var(--panel-border); border-radius: 16px;">
                            <h4 style="font-size: 1rem; margin-bottom: 0.75rem; color: var(--accent-cyan); display: flex; align-items: center; gap: 0.5rem;">
                                <i class="fas fa-link"></i> تجاوز الرابط يدوياً (Manual URL Override)
                            </h4>
                            <div style="display: flex; gap: 0.75rem;">
                                <input type="text" id="manualImageUrl" placeholder="ضع رابط الصورة المباشر هنا..." style="flex: 1; padding: 0.75rem 1rem; background: rgba(8, 12, 20, 0.6); border: 1px solid var(--panel-border); border-radius: 10px; color: var(--text-primary); font-family: inherit;">
                                <button class="btn" onclick="previewManualImage()"><i class="fas fa-eye"></i> معاينة وتطبيق</button>
                            </div>
                        </div>

                        <!-- Taxonomy Editor Dropdowns Section -->
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

                        <!-- Grid Candidates -->
                        <h3 style="margin-top: 2rem; margin-bottom: 1rem; font-size: 1.1rem; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.5rem; display: flex; align-items: center; gap: 0.5rem;">
                            <i class="fas fa-images"></i> الصور المرشحة المفحوصة
                        </h3>
                        <div id="candidatesContainer" class="candidates-grid"></div>

                        <!-- Accordion Log steps -->
                        <h3 style="margin-top: 2rem; margin-bottom: 1rem; font-size: 1.1rem; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.5rem; display: flex; align-items: center; gap: 0.5rem;">
                            <i class="fas fa-terminal"></i> سجل البحث التتبعي خطوة بخطوة (Trace)
                        </h3>
                        <div id="accordionContainer" class="step-accordion"></div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        const taxonomyData = {{ taxonomy_json | safe }};
        const searchCache = {};
        let currentProducts = [];
        let activeRowNumber = null;
        let currentFilterTab = 'all';

        // On document load
        window.addEventListener('load', () => {
            loadProducts();
            pollBatchStatus();
            initTaxonomyDropdowns();
            setInterval(pollBatchStatus, 3000); // تحديث دوري كل 3 ثوانٍ
        });

        function toggleTheme() {
            document.body.classList.toggle('light-theme');
            const isLight = document.body.classList.contains('light-theme');
            const icon = document.getElementById('themeIcon');
            if (isLight) {
                icon.className = 'fas fa-moon';
            } else {
                icon.className = 'fas fa-sun';
            }
        }

        function initTaxonomyDropdowns() {
            const selectL1 = document.getElementById('selectL1');
            selectL1.innerHTML = '<option value="">-- اختر التصنيف L1 --</option>';
            for (const l1 in taxonomyData) {
                const option = document.createElement('option');
                option.value = l1;
                option.innerText = `${l1} (${taxonomyData[l1].ar})`;
                selectL1.appendChild(option);
            }
            
            document.getElementById('selectL2').innerHTML = '<option value="">-- اختر L1 أولاً --</option>';
            document.getElementById('selectL3').innerHTML = '<option value="">-- اختر L2 أولاً --</option>';
        }

        function onL1Change() {
            const l1 = document.getElementById('selectL1').value;
            const selectL2 = document.getElementById('selectL2');
            const selectL3 = document.getElementById('selectL3');
            
            selectL2.innerHTML = '<option value="">-- اختر التصنيف L2 --</option>';
            selectL3.innerHTML = '<option value="">-- اختر L2 أولاً --</option>';
            
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
            
            // Find L1
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

        async function pollBatchStatus() {
            try {
                const res = await fetch('/api/batch_status');
                const data = await res.json();
                
                const panel = document.getElementById('batchProgressPanel');
                if (data.is_running) {
                    panel.style.display = 'flex';
                    
                    // حساب نسبة الإنجاز للتشغيل الحالي بالخلفية
                    const percent = data.total > 0 ? Math.round((data.current / data.total) * 100) : 0;
                    document.getElementById('batchProgressPercent').innerText = percent + '%';
                    document.getElementById('batchProgressBar').style.width = percent + '%';
                    
                    document.getElementById('batchProgressText').innerHTML = `جاري معالجة: <strong style="color: var(--accent-cyan); font-family: 'Tajawal', sans-serif;">${data.current_product || 'جاري البحث...'}</strong>`;
                    document.getElementById('batchProgressCounts').innerText = `${data.current} من أصل ${data.total} (نجاح: ${data.success} | فشل: ${data.failed})`;
                    
                    // تعطيل زر "تشغيل الكل" لمنع الإطلاق المتكرر
                    const runBtn = document.getElementById('runAllBtn');
                    if (runBtn) {
                        runBtn.disabled = true;
                        runBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> جاري التشغيل بالخلفية...';
                        runBtn.style.opacity = '0.6';
                        runBtn.style.cursor = 'not-allowed';
                    }
                } else {
                    panel.style.display = 'none';
                    const runBtn = document.getElementById('runAllBtn');
                    if (runBtn && runBtn.disabled) {
                        runBtn.disabled = false;
                        runBtn.innerHTML = '<i class="fas fa-play"></i> تشغيل الكل (أتمتة الشيت بالكامل)';
                        runBtn.style.opacity = '1';
                        runBtn.style.cursor = 'pointer';
                        loadProducts(); // تحديث القائمة فور انتهاء التشغيل
                    }
                }
            } catch (err) {
                console.error("Error polling batch status:", err);
            }
        }

        // Run All Batch Automation in background
        async function runAllAutomation() {
            if (!confirm("هل أنت متأكد من رغبتك في تشغيل الأتمتة الكاملة لكافة منتجات الشيت المتبقية بالخلفية؟")) {
                return;
            }
            
            const btn = document.getElementById('runAllBtn');
            const originalHTML = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> جاري التشغيل بالخلفية...';
            
            try {
                const res = await fetch('/api/run_all', { method: 'POST' });
                const data = await res.json();
                if (data.status === 'success') {
                    alert("🎉 تم تشغيل عملية الأتمتة الكلية بالخلفية بنجاح! يمكنك متابعة حالة التشغيل عبر التنبيهات في تليجرام أو تحديث الصفحة بعد قليل.");
                } else {
                    alert("❌ فشل تشغيل الأتمتة الكلية: " + data.error);
                }
            } catch (err) {
                console.error(err);
                alert("حدث خطأ أثناء الاتصال بالخادم.");
            } finally {
                btn.disabled = false;
                btn.innerHTML = originalHTML;
                loadProducts(); // أعد تحميل المنتجات لتحديث النسب
            }
        }

        // Load Products from sheet
        async function loadProducts() {
            const productList = document.getElementById('productList');
            productList.innerHTML = '<p style="color: var(--text-secondary); text-align: center; padding: 2rem;">جاري جلب البيانات من Google Sheet...</p>';
            
            try {
                loadMetrics(); // جلب الإحصائيات الفورية للاستهلاك
                const res = await fetch('/api/products');
                const data = await res.json();
                
                if (data.status === 'success') {
                    currentProducts = data.products;
                    updateKPIStats();
                    renderProductList();
                }
            } catch (err) {
                console.error(err);
                productList.innerHTML = '<p style="color: var(--danger); text-align: center; padding: 2rem;">فشل جلب المنتجات. تأكد من إعداد الشيت.</p>';
            }
        }

        // Fetch Live API and Webhook metrics
        async function loadMetrics() {
            try {
                const res = await fetch('/api/metrics');
                const data = await res.json();
                document.getElementById('metricsGemini').innerText = data.gemini_api_calls;
                document.getElementById('metricsCloudinary').innerText = data.cloudinary_uploads;
                
                // حساب وحفظ التكلفة التقديرية بالـ USD
                const estimatedCost = (data.gemini_api_calls * 0.00015).toFixed(4);
                document.getElementById('metricsCost').innerText = `$${estimatedCost} USD`;
            } catch (err) {
                console.error("Error loading metrics:", err);
            }
        }

        // Render products into sidebar based on filters
        function renderProductList() {
            const productList = document.getElementById('productList');
            const searchVal = document.getElementById('sidebarSearch').value.toLowerCase().trim();
            
            productList.innerHTML = '';
            
            const filtered = currentProducts.filter(prod => {
                // Tab filter
                const hasLink = prod.existing_image_link && prod.existing_image_link.trim() !== '';
                if (currentFilterTab === 'missing' && hasLink) return false;
                if (currentFilterTab === 'linked' && !hasLink) return false;
                
                // Search text filter
                if (searchVal !== '') {
                    const nameMatch = prod.product_name && prod.product_name.toLowerCase().includes(searchVal);
                    const brandMatch = prod.brand && prod.brand.toLowerCase().includes(searchVal);
                    return nameMatch || brandMatch;
                }
                
                return true;
            });
            
            document.getElementById('sheetProductCount').innerText = filtered.length;
            
            if (filtered.length === 0) {
                productList.innerHTML = '<p style="color: var(--text-secondary); text-align: center; padding: 2rem;">لا توجد منتجات مطابقة للبحث.</p>';
                return;
            }
            
            filtered.forEach(prod => {
                const hasLink = prod.existing_image_link && prod.existing_image_link.trim() !== '';
                const linkIndicator = hasLink 
                    ? `<span style="color: var(--success); font-size: 0.8rem; font-weight: bold;"><i class="fas fa-check"></i> رابط موجود</span>` 
                    : `<span style="color: var(--danger); font-size: 0.8rem; font-weight: bold;"><i class="fas fa-times"></i> بدون رابط</span>`;
                    
                const item = document.createElement('div');
                item.className = 'product-item';
                if (activeRowNumber === prod.row_number) item.classList.add('active');
                
                item.innerHTML = `
                    <span class="badge-row-number">صف ${prod.row_number}</span>
                    <h4 style="margin-top: 0.5rem;">${prod.product_name}</h4>
                    <p>
                        <span>البراند: <strong>${prod.brand}</strong></span>
                        ${linkIndicator}
                    </p>
                `;
                
                item.onclick = () => selectProduct(prod, item);
                productList.appendChild(item);
            });
        }

        // Update statistics bar
        function updateKPIStats() {
            const total = currentProducts.length;
            const linked = currentProducts.filter(p => p.existing_image_link && p.existing_image_link.trim() !== '').length;
            const missing = total - linked;
            const percentage = total > 0 ? Math.round((linked / total) * 100) : 0;
            
            document.getElementById('statTotal').innerText = total;
            document.getElementById('statLinked').innerText = linked;
            document.getElementById('statMissing').innerText = missing;
            document.getElementById('statPercentage').innerText = percentage + '%';
        }

        // Search text trigger filter
        function filterProducts() {
            renderProductList();
        }

        // Set Tab filter
        function setFilterTab(tabName) {
            currentFilterTab = tabName;
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            document.getElementById('tab-' + tabName).classList.add('active');
            renderProductList();
        }

        // Select Product from List
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
            
            // تخزين البيانات الإضافية في سمات النموذج لإرسالها
            document.getElementById('searchForm').dataset.barcode = prod.barcode || '';
            document.getElementById('searchForm').dataset.category = prod.category || '';
            document.getElementById('searchForm').dataset.origin = prod.origin || '';
            
            activeRowNumber = prod.row_number;
            
            // Auto Trigger Search
            document.getElementById('submitBtn').click();
        }

        // Search Form Submit
        document.getElementById('searchForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            
            const submitBtn = document.getElementById('submitBtn');
            const placeholder = document.getElementById('placeholder');
            const loading = document.getElementById('loading');
            const resultsContent = document.getElementById('resultsContent');
            const overallStatus = document.getElementById('overallStatus');
            
            const rowNumber = document.getElementById('rowNumber').value;
            const productName = document.getElementById('productName').value;
            const brand = document.getElementById('brand').value;
            const productNameAr = document.getElementById('productNameAr').value;
            const brandAr = document.getElementById('brandAr').value;
            const customQuery = document.getElementById('customQuery').value;
            const ignoreUnitClash = document.getElementById('ignoreUnitClash').checked;
            const strictBrandMatch = document.getElementById('strictBrandMatch').checked;
            
            // Check cache
            const cacheKey = `${rowNumber}_${ignoreUnitClash}_${strictBrandMatch}_${customQuery}`;
            if (searchCache[cacheKey]) {
                console.log("⚡ [Cache Hit] Loading cached search results for row", rowNumber);
                displaySearchResults(searchCache[cacheKey], productName, brand, rowNumber);
                return;
            }
            
            submitBtn.disabled = true;
            placeholder.style.display = 'none';
            resultsContent.style.display = 'none';
            loading.style.display = 'flex';
            overallStatus.innerText = 'جاري البحث والفرز البصري...';
            overallStatus.className = 'status-text active';
            
            const barcode = document.getElementById('searchForm').dataset.barcode || '';
            const category = document.getElementById('searchForm').dataset.category || '';
            const origin = document.getElementById('searchForm').dataset.origin || '';
            
            try {
                const response = await fetch('/api/search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        product_name: productName,
                        brand: brand,
                        product_name_ar: productNameAr,
                        brand_ar: brandAr,
                        custom_query: customQuery,
                        ignore_unit_clash: ignoreUnitClash,
                        strict_brand_match: strictBrandMatch,
                        barcode: barcode,
                        category: category,
                        origin: origin
                    })
                });
                
                const data = await response.json();
                
                if (data.brand && !document.getElementById('brand').value.trim()) {
                    document.getElementById('brand').value = data.brand;
                }
                
                // Save to cache
                searchCache[cacheKey] = data;
                
                displaySearchResults(data, productName, brand, rowNumber);
                
            } catch (err) {
                console.error(err);
                loading.style.display = 'none';
                submitBtn.disabled = false;
                overallStatus.innerText = 'خطأ بالنظام';
                overallStatus.className = 'status-text failed';
                alert('حدث خطأ أثناء معالجة الطلب.');
            }
        });

        function displaySearchResults(data, productName, brand, rowNumber) {
            const submitBtn = document.getElementById('submitBtn');
            const loading = document.getElementById('loading');
            const resultsContent = document.getElementById('resultsContent');
            const overallStatus = document.getElementById('overallStatus');
            
            loading.style.display = 'none';
            resultsContent.style.display = 'block';
            submitBtn.disabled = false;
            
            if (data.status === 'success') {
                overallStatus.innerText = 'تم العثور على صورة مطابقة';
                overallStatus.className = 'status-text success';
                
                const img = data.selected_image;
                renderRecommendedCard(img, productName, brand, rowNumber);
                
                // Preselect categories from Gemini metadata if exists
                if (img.metadata) {
                    preselectTaxonomy(
                        img.metadata.category_l1_en,
                        img.metadata.category_l2_en,
                        img.metadata.category_l3_en
                    );
                } else if (data.trace && data.trace.category_l1_en) {
                    preselectTaxonomy(
                        data.trace.category_l1_en,
                        data.trace.category_l2_en,
                        data.trace.category_l3_en
                    );
                } else {
                    initTaxonomyDropdowns();
                }
            } else {
                overallStatus.innerText = 'لم يتم العثور على مطابقة للبراند';
                overallStatus.className = 'status-text failed';
                renderNoMatchCard(productName, brand, rowNumber);
                
                initTaxonomyDropdowns();
            }
            
            // Build Candidates Grid
            renderCandidatesGrid(data.trace, productName, brand, rowNumber);
            
            // Build Steps
            const accordionContainer = document.getElementById('accordionContainer');
            accordionContainer.innerHTML = '';
            
            if (data.trace && data.trace.steps) {
                data.trace.steps.forEach((step, index) => {
                    const stepItem = document.createElement('div');
                    stepItem.className = 'step-item';
                    
                    const isSuccess = step.candidates && step.candidates.some(c => c.status === 'accepted');
                    const statusIndicator = isSuccess 
                        ? '<span style="color: var(--success); font-weight: bold;"><i class="fas fa-check"></i> تطابق</span>' 
                        : '<span style="color: var(--text-secondary); font-weight: bold;"><i class="fas fa-times"></i> لم تُقبل نتائج</span>';
                        
                    stepItem.innerHTML = `
                        <div class="step-header" onclick="toggleAccordion(this)">
                            <span><i class="fas fa-chevron-left" style="font-size: 0.8rem; margin-left: 0.5rem;"></i> الخطوة ${index + 1}: الاستعلام '${step.query}' (${step.results_count} صورة مرشحة)</span>
                            <span>${statusIndicator}</span>
                        </div>
                        <div class="step-body">
                            <div style="color: #00e5ff; font-family: monospace; font-size: 0.85rem; margin-bottom: 0.5rem;">
                                $ fetch_candidates --query="${step.query}" --source="${step.source || 'hybrid'}" --found=${step.results_count}
                            </div>
                            <div style="font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 0.75rem;">
                                ملاحظات المحرك: تم جلب ${step.results_count} نتيجة بنجاح وجاري فحص الشروط والتصنيفات البصرية...
                            </div>
                        </div>
                    `;
                    accordionContainer.appendChild(stepItem);
                });
            }
        }

        function toggleAccordion(header) {
            header.nextElementSibling.classList.toggle('active');
            const icon = header.querySelector('i');
            if (header.nextElementSibling.classList.contains('active')) {
                icon.className = 'fas fa-chevron-down';
            } else {
                icon.className = 'fas fa-chevron-left';
            }
        }

        // Render recommended image card
        function renderRecommendedCard(img, productName, brand, rowNumber) {
            document.getElementById('recommendedContainer').innerHTML = `
                <div class="recommended-card">
                    <img src="${img.url}" alt="Recommended image" onerror="this.src='https://placehold.co/160x160?text=Error'">
                    <div class="recommended-details">
                        <h3><i class="fas fa-award"></i> الصورة المعتمدة تلقائياً (أعلى دقة ومطابقة)</h3>
                        <p><strong>العنوان:</strong> ${img.title || 'بدون عنوان'}</p>
                        <p><strong>الأبعاد الحالية:</strong> ${img.width}x${img.height} بكسل</p>
                        <p><strong>الرابط الأصلي:</strong> <a href="${img.url}" target="_blank" style="color: var(--accent-cyan); text-decoration: none;">${img.url.substring(0, 70)}... <i class="fas fa-external-link-alt" style="font-size: 0.8rem;"></i></a></p>
                        ${rowNumber ? `
                            <button class="btn btn-success" style="align-self: flex-start; margin-top: 0.75rem;" onclick="selectImage('${img.url}', '${productName.replace(/'/g, "\\'")}', '${brand.replace(/'/g, "\\'")}', ${rowNumber})">
                                <i class="fas fa-cloud-upload-alt"></i> اعتمد الصورة للشيت
                            </button>
                        ` : ''}
                    </div>
                </div>
            `;
        }

        function renderNoMatchCard(productName, brand, rowNumber) {
            document.getElementById('recommendedContainer').innerHTML = `
                <div style="background-color: var(--danger-bg); border: 1px solid var(--danger); color: var(--danger); border-radius: 16px; padding: 1.5rem; display: flex; align-items: center; gap: 1rem; font-weight: 700; margin-bottom: 1.5rem;">
                    <i class="fas fa-exclamation-triangle" style="font-size: 1.5rem;"></i>
                    <div>
                        <div>تنبيه تصفية العلامة التجارية</div>
                        <div style="font-size: 0.85rem; font-weight: normal; margin-top: 0.25rem; color: var(--text-primary);">
                            لم يعثر الفحص التلقائي على صورة تطابق البراند بنسبة 100% للبراند <strong>(${brand})</strong>. يمكنك اختيار صورة يدوياً من قائمة النتائج بالأسفل أو وضع رابط وتعديله.
                        </div>
                    </div>
                </div>
            `;
        }

        // Action: Upload selected image to Sheet with Taxonomy overrides
        async function selectImage(imageUrl, productName, brand, rowNumber) {
            if (!rowNumber) {
                alert('الصف غير معروف. يرجى اختيار منتج من الشيت أولاً.');
                return;
            }
            
            const selectL1 = document.getElementById('selectL1').value;
            const selectL2 = document.getElementById('selectL2').value;
            const selectL3 = document.getElementById('selectL3').value;
            
            const btn = event.currentTarget;
            const originalText = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> جاري التحميل، معالجة الحجم والرفع لـ Cloudinary...';
            
            try {
                const res = await fetch('/api/select_image', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        image_url: imageUrl,
                        product_name: productName,
                        brand: brand,
                        row_number: rowNumber,
                        category_l1_en: selectL1,
                        category_l2_en: selectL2,
                        category_l3_en: selectL3
                    })
                });
                
                const data = await res.json();
                btn.disabled = false;
                btn.innerHTML = originalText;
                
                if (data.status === 'success') {
                    alert(`🎉 تم رفع الصورة وتحديث الصف ${rowNumber} بنجاح! الرابط الجديد: ${data.image_link}`);
                    loadProducts(); // Refresh list to show success indicator
                } else {
                    alert(`❌ فشل رفع وتحديث الصورة: ${data.error}`);
                }
            } catch (err) {
                console.error(err);
                btn.disabled = false;
                btn.innerHTML = originalText;
                alert('حدث خطأ أثناء الاتصال بالخادم لمزامنة الصورة.');
            }
        }

        // Render Candidate Cards Grid
        function renderCandidatesGrid(trace, productName, brand, rowNumber) {
            const container = document.getElementById('candidatesContainer');
            container.innerHTML = '';
            
            // Extract all candidates from steps
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
                    ? `<div class="candidate-uae-tag">الإمارات 🇦🇪</div>` 
                    : '';
                    
                const reasonsText = c.reasons && c.reasons.length > 0 
                    ? `<div class="candidate-reasons">${c.reasons.map(r => `• ${r}`).join('<br>')}</div>` 
                    : '';
                    
                const actionButton = rowNumber 
                    ? `<button class="btn btn-secondary btn-sm" style="width: 100%; font-weight: bold; margin-top: 0.5rem;" onclick="selectImage('${c.url}', '${productName.replace(/'/g, "\\'")}', '${brand.replace(/'/g, "\\'")}', ${rowNumber})">🎯 اعتمد الصورة يدوياً</button>` 
                    : '';
                
                card.innerHTML = `
                    <div class="candidate-img-box">
                        <span class="candidate-badge ${statusClass}">${statusText}</span>
                        <img src="${c.url}" alt="Candidate thumbnail" onerror="this.src='https://placehold.co/180x180?text=Error'">
                        ${scoreTag}
                        ${uaeTag}
                    </div>
                    <div class="candidate-info">
                        <div class="candidate-title" title="${c.title || ''}">${c.title || 'بدون عنوان'}</div>
                        <div class="candidate-meta">
                            <span>الأبعاد: <strong>${c.width}x${c.height}</strong></span>
                            <span>الرابط: <a href="${c.url}" target="_blank" style="color: var(--accent-cyan); text-decoration: none;"><i class="fas fa-link"></i> فتح</a></span>
                        </div>
                        ${reasonsText}
                        ${actionButton}
                    </div>
                `;
                
                container.appendChild(card);
            });
        }
    </script>
</body>
</html>
"""


@app.route('/')
def home():
    import json
    import categories
    return render_template_string(HTML_TEMPLATE, taxonomy_json=json.dumps(categories.CATEGORIES))

import threading

def async_process_webhook_product(row_number, product_name, brand, product_name_ar="", brand_ar="", barcode="", category="", origin=""):
    """
    معالجة المنتج المستلم من Webhook بشكل غير متزامن في الخلفية لمنع حظر الطلب.
    """
    print(f"🧵 [Webhook Thread] بدء المعالجة للمنتج: '{product_name}' (صف {row_number})")
    try:
        # أ. البحث عن أفضل صورة
        query = f"{brand} {product_name}"
        best_image = image_search.search_best_product_image(
            query, 
            product_name, 
            brand, 
            product_name_ar=product_name_ar, 
            brand_ar=brand_ar,
            barcode=barcode,
            category=category,
            origin=origin
        )
        
        if not best_image:
            msg = (
                f"<b>⚠️ فشل أتمتة منتج من الشيت (تلقائي)!</b>\n\n"
                f"📦 <b>المنتج:</b> {product_name}\n"
                f"🏷️ <b>الماركة:</b> {brand}\n"
                f"❌ <b>السبب:</b> لم يتم العثور على أي صورة تطابق معايير القبول والجودة البصرية."
            )
            image_processor.send_telegram_notification(msg)
            config.METRICS["failed_runs"] += 1
            return
            
        image_url = best_image["url"]
        
        # ب. معالجة وتجميل الصورة محلياً
        processed_image_path = image_processor.process_product_image(image_url, product_name, brand)
        if not processed_image_path or not os.path.exists(processed_image_path):
            msg = (
                f"<b>⚠️ فشل أتمتة منتج من الشيت (تلقائي)!</b>\n\n"
                f"📦 <b>المنتج:</b> {product_name}\n"
                f"🏷️ <b>الماركة:</b> {brand}\n"
                f"❌ <b>السبب:</b> فشل تحميل الصورة المرشحة أو فشل عزل الخلفية وتنعيم الحواف."
            )
            image_processor.send_telegram_notification(msg)
            config.METRICS["failed_runs"] += 1
            return
            
        # ج. استخراج البيانات الوصفية (القيم الغذائية والمكونات والتصنيفات) أولاً لتنظيم المجلدات سحابياً
        metadata = image_processor.extract_metadata_from_image(processed_image_path, product_name, brand)
        
        folder = "products"
        tags = []
        sheets_client = google_sheets.get_sheets_client()
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        
        if metadata:
            google_sheets.update_product_metadata(worksheet, row_number, metadata)
            cat1 = metadata.get("category_l1_en", "").strip().lower().replace(" ", "_").replace("&", "and")
            cat2 = metadata.get("category_l2_en", "").strip().lower().replace(" ", "_").replace("&", "and")
            if cat1:
                if cat2:
                    folder = f"products/{cat1}/{cat2}"
                else:
                    folder = f"products/{cat1}"
            tags_str = metadata.get("tags_en", "")
            if tags_str:
                tags = [t.strip() for t in tags_str.split(",") if t.strip()]
                
        # د. رفع الصورة المعالجة محلياً إلى Cloudinary وتوليد الرابط الآمن بالمجلد والوسوم المستهدفة
        image_link = cloudinary_storage.upload_product_image_to_cloudinary(
            processed_image_path, 
            product_name, 
            brand,
            folder=folder,
            tags=tags
        )
        
        # هـ. تنظيف ملف الصورة المعالجة
        try:
            if os.path.exists(processed_image_path):
                os.remove(processed_image_path)
        except Exception:
            pass
            
        if not image_link:
            msg = (
                f"<b>⚠️ فشل أتمتة منتج من الشيت (تلقائي)!</b>\n\n"
                f"📦 <b>المنتج:</b> {product_name}\n"
                f"🏷️ <b>الماركة:</b> {brand}\n"
                f"❌ <b>السبب:</b> فشل رفع الصورة المعالجة إلى Cloudinary."
            )
            image_processor.send_telegram_notification(msg)
            config.METRICS["failed_runs"] += 1
            return
            
        # و. تحديث الشيت بالرابط الجديد
        _, link_column_index = google_sheets.get_products(worksheet)
        update_success = google_sheets.update_image_link(
            worksheet, 
            row_number, 
            link_column_index, 
            image_link
        )
        
        if update_success:
            msg = (
                f"<b>🎉 تم أتمتة منتج جديد من الشيت (تلقائياً)!</b>\n\n"
                f"📦 <b>المنتج:</b> {product_name}\n"
                f"🏷️ <b>الماركة:</b> {brand}\n"
                f"📂 <b>المجلد:</b> <code>{folder}</code>\n"
                f"🏷️ <b>الوسوم:</b> {metadata.get('tags_ar', '') if metadata else ''}\n"
                f"🔗 <a href='{image_link}'>رابط الصورة النهائي</a>"
            )
            image_processor.send_telegram_notification(msg)
            config.METRICS["successful_runs"] += 1
        else:
            msg = (
                f"<b>⚠️ فشل أتمتة منتج من الشيت (تلقائي)!</b>\n\n"
                f"📦 <b>المنتج:</b> {product_name}\n"
                f"🏷️ <b>الماركة:</b> {brand}\n"
                f"❌ <b>السبب:</b> فشل كتابة الرابط النهائي داخل ورقة Google Sheets."
            )
            image_processor.send_telegram_notification(msg)
            config.METRICS["failed_runs"] += 1
            
    except Exception as e:
        print(f"❌ [Webhook Thread Error] {e}")
        msg = (
            f"<b>⚠️ خطأ غير متوقع أثناء معالجة المنتج!</b>\n\n"
            f"📦 <b>المنتج:</b> {product_name}\n"
            f"❌ <b>الخطأ:</b> {str(e)}"
        )
        image_processor.send_telegram_notification(msg)
        config.METRICS["failed_runs"] += 1

@app.route('/api/webhook/sheets', methods=['POST'])
def sheets_webhook():
    """
    استقبال التنبيهات اللحظية عند إضافة أو تعديل منتج في Google Sheets
    """
    data = request.json
    if not data:
        return jsonify({"error": "Missing JSON payload"}), 400
        
    row_number = data.get("row_number")
    product_name = data.get("product_name")
    brand = data.get("brand")
    product_name_ar = data.get("product_name_ar", "")
    brand_ar = data.get("brand_ar", "")
    barcode = data.get("barcode", "")
    category = data.get("category", "")
    origin = data.get("origin", "")
    
    if not row_number or not product_name or not brand:
        return jsonify({"error": "Missing required fields (row_number, product_name, brand)"}), 400
        
    # تشغيل خط الأنابيب في خيط منفصل (Thread) لعدم تجميد طلب الويب هوك
    thread = threading.Thread(
        target=async_process_webhook_product,
        args=(row_number, product_name, brand, product_name_ar, brand_ar, barcode, category, origin)
    )
    thread.daemon = True
    thread.start()
    
    return jsonify({
        "status": "queued",
        "message": "Product processing has been queued in the background."
    }), 200

BATCH_PROGRESS = {
    "is_running": False,
    "total": 0,
    "current": 0,
    "current_product": "",
    "success": 0,
    "failed": 0
}

def run_all_automation_thread():
    """
    تشغيل الأتمتة الكاملة لكافة المنتجات غير المكتملة في الشيت بالخلفية.
    """
    global BATCH_PROGRESS
    print("🧵 [Batch Thread] بدء الأتمتة الكاملة لكافة منتجات الشيت المفقودة...")
    try:
        sheets_client = google_sheets.get_sheets_client()
        if not sheets_client:
            print("❌ [Batch Thread] فشل الاتصال بـ Google Sheets API")
            BATCH_PROGRESS["is_running"] = False
            return
            
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        if not worksheet:
            print("❌ [Batch Thread] فشل فتح الشيت")
            BATCH_PROGRESS["is_running"] = False
            return
            
        products, link_column_index = google_sheets.get_products(worksheet)
        missing_products = [p for p in products if not p["existing_image_link"]]
        
        print(f"🧵 [Batch Thread] تم العثور على {len(missing_products)} منتج بحاجة لأتمتة الصور من أصل {len(products)}.")
        
        BATCH_PROGRESS.update({
            "is_running": True,
            "total": len(missing_products),
            "current": 0,
            "current_product": "",
            "success": 0,
            "failed": 0
        })
        
        success_count = 0
        failed_count = 0
        
        for idx, prod in enumerate(missing_products, start=1):
            row_num = prod["row_number"]
            name = prod["product_name"]
            brand = prod["brand"]
            query = prod["search_query"]
            product_name_ar = prod.get("product_name_ar", "")
            brand_ar = prod.get("brand_ar", "")
            
            BATCH_PROGRESS["current"] = idx
            BATCH_PROGRESS["current_product"] = name
            
            print(f"🔄 [Batch Thread] ({idx}/{len(missing_products)}) جاري معالجة: '{name}' | صف {row_num}")
            
            # أ. البحث عن أفضل صورة
            best_image = image_search.search_best_product_image(
                query, 
                name, 
                brand, 
                product_name_ar=product_name_ar, 
                brand_ar=brand_ar,
                barcode=prod.get("barcode", ""),
                category=prod.get("category", ""),
                origin=prod.get("origin", "")
            )
            
            if not best_image:
                print(f"⚠️ [Batch Thread] تخطي: لم نعثر على صورة للمنتج '{name}'")
                failed_count += 1
                msg = (
                    f"<b>⚠️ فشل أتمتة منتج من الشيت (Batch)!</b>\n\n"
                    f"📦 <b>المنتج:</b> {name}\n"
                    f"🏷️ <b>الماركة:</b> {brand}\n"
                    f"❌ <b>السبب:</b> لم يتم العثور على أي صورة تطابق معايير القبول والجودة البصرية."
                )
                image_processor.send_telegram_notification(msg)
                config.METRICS["failed_runs"] += 1
                continue
                
            image_url = best_image["url"]
            
            # ب. تحميل ومعالجة الصورة محلياً
            processed_image_path = image_processor.process_product_image(image_url, name, brand)
            if not processed_image_path or not os.path.exists(processed_image_path):
                print(f"⚠️ [Batch Thread] فشل تحميل أو معالجة الصورة للمنتج '{name}'")
                failed_count += 1
                msg = (
                    f"<b>⚠️ فشل أتمتة منتج من الشيت (Batch)!</b>\n\n"
                    f"📦 <b>المنتج:</b> {name}\n"
                    f"🏷️ <b>الماركة:</b> {brand}\n"
                    f"❌ <b>السبب:</b> فشل تحميل الصورة أو عزل الخلفية."
                )
                image_processor.send_telegram_notification(msg)
                config.METRICS["failed_runs"] += 1
                continue
                
            # ج. استخراج البيانات الوصفية
            metadata = image_processor.extract_metadata_from_image(processed_image_path, name, brand)
            folder = "products"
            tags = []
            if metadata:
                google_sheets.update_product_metadata(worksheet, row_num, metadata)
                
                cat1 = metadata.get("category_l1_en", "").strip().lower().replace(" ", "_").replace("&", "and")
                cat2 = metadata.get("category_l2_en", "").strip().lower().replace(" ", "_").replace("&", "and")
                if cat1:
                    if cat2:
                        folder = f"products/{cat1}/{cat2}"
                    else:
                        folder = f"products/{cat1}"
                        
                tags_str = metadata.get("tags_en", "")
                if tags_str:
                    tags = [t.strip() for t in tags_str.split(",") if t.strip()]
                    
            # د. الرفع لكلويديناري
            image_link = cloudinary_storage.upload_product_image_to_cloudinary(
                processed_image_path, 
                name, 
                brand,
                folder=folder,
                tags=tags
            )
            
            # هـ. تحديث الشيت
            update_success = google_sheets.update_image_link(
                worksheet, 
                row_num, 
                link_column_index, 
                image_link
            )
            
            # تنظيف الصورة المؤقتة
            try:
                if os.path.exists(processed_image_path):
                    os.remove(processed_image_path)
            except Exception:
                pass
                
            if update_success:
                success_count += 1
                BATCH_PROGRESS["success"] += 1
                msg = (
                    f"<b>🎉 تم أتمتة منتج جديد بنجاح (Batch)!</b>\n\n"
                    f"📦 <b>المنتج:</b> {name}\n"
                    f"🏷️ <b>الماركة:</b> {brand}\n"
                    f"📂 <b>المجلد:</b> <code>{folder}</code>\n"
                    f"🔗 <a href='{image_link}'>رابط الصورة النهائي</a>"
                )
                image_processor.send_telegram_notification(msg)
                config.METRICS["successful_runs"] += 1
            else:
                failed_count += 1
                BATCH_PROGRESS["failed"] += 1
                config.METRICS["failed_runs"] += 1
                
        print(f"🏁 [Batch Thread] اكتملت عملية الأتمتة الكلية. نجاح: {success_count} | فشل: {failed_count}")
        BATCH_PROGRESS["is_running"] = False
        
    except Exception as e:
        print(f"❌ [Batch Thread Error] {e}")
        BATCH_PROGRESS["is_running"] = False

@app.route('/api/batch_status', methods=['GET'])
def api_batch_status():
    """
    إرجاع حالة تقدم تشغيل الأتمتة في الخلفية
    """
    return jsonify(BATCH_PROGRESS)

@app.route('/api/run_all', methods=['POST'])
def api_run_all():
    """
    بدء الأتمتة الكاملة لكافة المنتجات المتبقية في الشيت في الخلفية.
    """
    global BATCH_PROGRESS
    BATCH_PROGRESS["is_running"] = True
    thread = threading.Thread(target=run_all_automation_thread)
    thread.daemon = True
    thread.start()
    return jsonify({
        "status": "success",
        "message": "Full automation run started in the background."
    })

@app.route('/api/metrics', methods=['GET'])
def api_metrics():
    """
    إرجاع إحصائيات استهلاك الـ API والتشغيل الحالي
    """
    return jsonify(config.METRICS)

@app.route('/api/products', methods=['GET'])
def api_products():
    """
    جلب كافة المنتجات من Google Sheets
    """
    try:
        sheets_client = google_sheets.get_sheets_client()
        if not sheets_client:
            return jsonify({'error': 'Google Sheets API connection failed'}), 500
            
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        if not worksheet:
            return jsonify({'error': 'Sheet not found'}), 404
            
        products, _ = google_sheets.get_products(worksheet)
        return jsonify({
            'status': 'success',
            'products': products
        })
    except Exception as e:
        print(f"[Flask API Error] Failed to read products: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/search', methods=['POST'])
def api_search():
    data = request.json or {}
    product_name = data.get('product_name', '').strip()
    brand = data.get('brand', '').strip()
    product_name_ar = data.get('product_name_ar', '').strip()
    brand_ar = data.get('brand_ar', '').strip()
    custom_query = data.get('custom_query', '').strip()
    ignore_unit_clash = bool(data.get('ignore_unit_clash', False))
    strict_brand_match = data.get('strict_brand_match')
    if strict_brand_match is not None:
        strict_brand_match = bool(strict_brand_match)
    
    barcode = data.get('barcode', '').strip()
    category = data.get('category', '').strip()
    origin = data.get('origin', '').strip()
    
    if not product_name:
        return jsonify({'error': 'Product name is required'}), 400
        
    # جلب مرادفات البراندات لجميع عمليات البحث لتوفيرها لخطوات التحقق والفرز الذكي
    brand_mappings = {}
    try:
        sheets_client = google_sheets.get_sheets_client()
        if sheets_client:
            brand_mappings = google_sheets.get_brand_mappings(sheets_client, config.SPREADSHEET_NAME_OR_URL)
    except Exception as e:
        print(f"⚠️ فشل جلب مرادفات البراندات في API: {e}")
        
    # استخراج اسم البراند تلقائياً إذا كان الحقل فارغاً
    if product_name and not brand:
        extracted = google_sheets.extract_brand_from_name(product_name, brand_mappings)
        if extracted:
            brand = extracted
            print(f"💡 [API Auto Brand] تم استخراج البراند '{brand}' تلقائياً لـ '{product_name}' من المرادفات.")
        else:
            extracted = google_sheets.extract_brand_from_start(product_name, brand_mappings)
            if extracted:
                brand = extracted
                print(f"💡 [API Auto Brand] تم استخراج البراند '{brand}' تلقائياً لـ '{product_name}' من بداية الاسم.")
            else:
                extracted = google_sheets.extract_brand_via_gemini(product_name)
                if extracted:
                    brand = extracted
                    print(f"💡 [API Auto Brand] تم استخراج البراند '{brand}' تلقائياً لـ '{product_name}' عبر Gemini.")
                
    search_query = custom_query if custom_query else (f"{product_name} {brand}".strip() if brand else product_name)
    
    trace = {}
    print(f"\n[Flask API] Localized Search query: '{search_query}' for Product: '{product_name}', Brand: '{brand}'")
    
    best_image = image_search.search_best_product_image(
        search_query, product_name, brand, 
        product_name_ar=product_name_ar, brand_ar=brand_ar,
        trace=trace, strict_brand_match=strict_brand_match,
        barcode=barcode, category=category, origin=origin,
        brand_mappings=brand_mappings
    )
    
    if best_image:
        return jsonify({
            'status': 'success',
            'selected_image': best_image,
            'trace': trace,
            'brand': brand
        })
    else:
        return jsonify({
            'status': 'failed',
            'trace': trace,
            'brand': brand
        })

@app.route('/api/select_image', methods=['POST'])
def api_select_image():
    """
    الموافقة اليدوية (Override) وتحميل وتعديل الصورة ورفعها لـ Cloudinary ثم تحديث Google Sheet
    """
    data = request.json or {}
    image_url = data.get('image_url', '').strip()
    product_name = data.get('product_name', '').strip()
    brand = data.get('brand', '').strip()
    row_number = data.get('row_number')
    
    if not image_url or not product_name or not brand or not row_number:
        return jsonify({'error': 'Missing parameters'}), 400
        
    row_number = int(row_number)
    
    print(f"\n[Flask API] Manual override selected for Row {row_number}")
    print(f"🔗 URL: {image_url}")
    
    try:
        # 1. تحميل ومعالجة الصورة محلياً (إزالة خلفية وحجم وتوسيط)
        processed_image_path = image_processor.process_product_image(image_url, product_name, brand)
        if not processed_image_path or not os.path.exists(processed_image_path):
            msg = (
                f"<b>⚠️ فشل اعتماد صورة يدوياً للمنتج!</b>\n\n"
                f"📦 <b>المنتج:</b> {product_name}\n"
                f"❌ <b>السبب:</b> فشل تحميل الصورة أو معالجتها محلياً."
            )
            image_processor.send_telegram_notification(msg)
            config.METRICS["failed_runs"] += 1
            return jsonify({'error': 'Failed to download or process image locally'}), 500
            
        # 2. استخراج البيانات الوصفية من الصورة وتحديث الشيت أولاً لتنظيم المجلدات سحابياً
        metadata = image_processor.extract_metadata_from_image(processed_image_path, product_name, brand)
        
        folder = "products"
        tags = []
        
        # التحقق من وجود تعديلات تصنيف الفئات يدوياً من لوحة التحكم
        override_l1_en = data.get('category_l1_en', '').strip()
        override_l2_en = data.get('category_l2_en', '').strip()
        override_l3_en = data.get('category_l3_en', '').strip()
        
        if override_l1_en:
            import categories
            norm = categories.normalize_category_path(override_l1_en, override_l2_en, override_l3_en)
            if not metadata:
                metadata = {}
            metadata.update(norm)

        if metadata:
            sheets_client = google_sheets.get_sheets_client()
            worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
            google_sheets.update_product_metadata(worksheet, row_number, metadata)
            
            cat1 = metadata.get("category_l1_en", "").strip().lower().replace(" ", "_").replace("&", "and")
            cat2 = metadata.get("category_l2_en", "").strip().lower().replace(" ", "_").replace("&", "and")
            if cat1:
                if cat2:
                    folder = f"products/{cat1}/{cat2}"
                else:
                    folder = f"products/{cat1}"
            tags_str = metadata.get("tags_en", "")
            if tags_str:
                tags = [t.strip() for t in tags_str.split(",") if t.strip()]
                
        # 3. رفع الصورة إلى Cloudinary بالمجلد والوسوم المستهدفة
        image_link = cloudinary_storage.upload_product_image_to_cloudinary(
            processed_image_path,
            product_name,
            brand,
            folder=folder,
            tags=tags
        )
        
        # تنظيف الملف المؤقت
        try:
            if os.path.exists(processed_image_path):
                os.remove(processed_image_path)
        except Exception:
            pass
            
        if not image_link:
            msg = (
                f"<b>⚠️ فشل اعتماد صورة يدوياً للمنتج!</b>\n\n"
                f"📦 <b>المنتج:</b> {product_name}\n"
                f"❌ <b>السبب:</b> فشل رفع الصورة المعالجة إلى Cloudinary."
            )
            image_processor.send_telegram_notification(msg)
            config.METRICS["failed_runs"] += 1
            return jsonify({'error': 'Failed to upload processed image to Cloudinary'}), 500
            
        # 4. تحديث Google Sheets بالرابط الجديد
        sheets_client = google_sheets.get_sheets_client()
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        _, link_column_index = google_sheets.get_products(worksheet)
        
        update_success = google_sheets.update_image_link(
            worksheet,
            row_number,
            link_column_index,
            image_link
        )
        
        if update_success:
            print(f"🎉 [Flask API] Row {row_number} updated with: {image_link}")
            msg = (
                f"<b>🎉 تم اعتماد صورة منتج يدوياً بنجاح!</b>\n\n"
                f"📦 <b>المنتج:</b> {product_name}\n"
                f"🏷️ <b>الماركة:</b> {brand}\n"
                f"📂 <b>المجلد:</b> <code>{folder}</code>\n"
                f"🏷️ <b>الوسوم:</b> {metadata.get('tags_ar', '') if metadata else ''}\n"
                f"🔗 <a href='{image_link}'>رابط الصورة النهائي</a>"
            )
            image_processor.send_telegram_notification(msg)
            config.METRICS["successful_runs"] += 1
            return jsonify({
                'status': 'success',
                'image_link': image_link
            })
        else:
            msg = (
                f"<b>⚠️ فشل اعتماد صورة يدوياً للمنتج!</b>\n\n"
                f"📦 <b>المنتج:</b> {product_name}\n"
                f"❌ <b>السبب:</b> فشل كتابة الرابط النهائي داخل ورقة Google Sheets."
            )
            image_processor.send_telegram_notification(msg)
            config.METRICS["failed_runs"] += 1
            return jsonify({'error': 'Failed to write link to Google Sheet'}), 500
            
    except Exception as e:
        print(f"[Flask API Error] Failed during manual override update: {e}")
        msg = (
            f"<b>⚠️ خطأ غير متوقع أثناء معالجة الاعتماد اليدوي!</b>\n\n"
            f"📦 <b>المنتج:</b> {product_name}\n"
            f"❌ <b>الخطأ:</b> {str(e)}"
        )
        image_processor.send_telegram_notification(msg)
        config.METRICS["failed_runs"] += 1
        return jsonify({'error': str(e)}), 500

# ==========================================
# 🤖 INTERACTIVE TELEGRAM BOT CONTROL SECTION
# ==========================================

LATEST_SEARCHES = {} # حفظ حالة آخر بحث تفاعلي لكل مستخدم: {chat_id: {"row_number": rn, "candidates": [...]}}
USER_STATES = {}     # حفظ حالة المحادثة المتعددة الخطوات: {chat_id: {"state": "waiting...", "row_number": rn}}

def send_telegram_msg(chat_id, text, reply_markup=None):
    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        import json
        payload["reply_markup"] = json.dumps(reply_markup) if isinstance(reply_markup, dict) else reply_markup
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending Telegram message: {e}")

def send_telegram_photo(chat_id, photo, caption, reply_markup=None):
    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    import json
    data = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": "HTML"
    }
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup) if isinstance(reply_markup, dict) else reply_markup
        
    try:
        if isinstance(photo, str) and (photo.startswith("http://") or photo.startswith("https://")):
            data["photo"] = photo
            requests.post(url, json=data, timeout=15)
        else:
            # ملف محلي
            file_path = photo
            if os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    requests.post(url, data=data, files={"photo": f}, timeout=15)
            else:
                print(f"❌ Local photo path not found: {file_path}")
    except Exception as e:
        print(f"Error sending Telegram photo: {e}")

def answer_telegram_callback(callback_query_id, text):
    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    payload = {
        "callback_query_id": callback_query_id,
        "text": text
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error answering callback query: {e}")

def parse_message_intent_with_gemini(message_text):
    """
    تحليل نية الرسائل النصية للمستخدم بلغة طبيعية وتحديد الإجراء المناسب بـ Gemini.
    """
    if not getattr(config, "GEMINI_API_KEY", ""):
        return {"intent": "unknown", "row_number": None, "query": None}
        
    prompt = (
        "You are an AI assistant parsing user commands for an e-commerce catalog bot.\n"
        "Analyze the user's message and categorize it into one of these intents:\n"
        "- 'status': the user wants to check catalog progress, sheet status, or API metrics.\n"
        "- 'run_all': the user wants to start the full automation/batch processing of the sheet.\n"
        "- 'search_row': the user wants to look up or process a specific row number in the sheet.\n"
        "- 'search_catalog': the user wants to view or search uploaded assets for a brand in Cloudinary.\n"
        "- 'unknown': none of the above.\n\n"
        "Rules:\n"
        "1. Return ONLY a valid JSON object in this format:\n"
        "{\n"
        "  \"intent\": \"intent_name\",\n"
        "  \"row_number\": null or integer (if intent is search_row),\n"
        "  \"query\": null or string (if intent is search_catalog or query refers to a search string)\n"
        "}\n"
        "2. Do not include markdown formatting, backticks, or wrapping. Just the raw JSON string.\n\n"
        f"User message: '{message_text}'"
    )
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={config.GEMINI_API_KEY}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            config.METRICS["gemini_api_calls"] += 1
            res_data = response.json()
            text_out = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
            if text_out.startswith("```"):
                text_out = text_out.replace("```json", "").replace("```", "").strip()
            import json
            parsed = json.loads(text_out)
            return parsed
    except Exception as e:
        print(f"Error parsing NLP with Gemini: {e}")
    return {"intent": "unknown", "row_number": None, "query": None}

def handle_bot_status(chat_id):
    try:
        send_telegram_msg(chat_id, "⏳ جاري الاتصال بـ Google Sheets وجلب إحصائيات الكتالوج الحالية...")
        sheets_client = google_sheets.get_sheets_client()
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        if not worksheet:
            send_telegram_msg(chat_id, "❌ فشل الوصول لورقة Google Sheets.")
            return
        products, _ = google_sheets.get_products(worksheet)
        total = len(products)
        linked = sum(1 for p in products if p["existing_image_link"])
        missing = total - linked
        percentage = f"{(linked / total * 100):.1f}%" if total > 0 else "0%"
        
        gemini_calls = config.METRICS.get("gemini_api_calls", 0)
        cost = gemini_calls * 0.000075
        
        msg = (
            f"📊 <b>إحصائيات الكتالوج الحالية:</b>\n\n"
            f"📦 <b>إجمالي المنتجات بالشيت:</b> {total}\n"
            f"✅ <b>المنتجات المكتملة:</b> {linked}\n"
            f"❌ <b>المنتجات المفقودة:</b> {missing}\n"
            f"📈 <b>نسبة الإنجاز الكلية:</b> {percentage}\n\n"
            f"🧠 <b>مطالبات Gemini API:</b> {gemini_calls}\n"
            f"💰 <b>التكلفة الإجمالية المقدرة:</b> ${cost:.4f} USD"
        )
        send_telegram_msg(chat_id, msg)
    except Exception as e:
        send_telegram_msg(chat_id, f"❌ حدث خطأ أثناء جلب الحالة: {e}")

def handle_bot_run_all(chat_id):
    thread = threading.Thread(target=run_all_automation_thread)
    thread.daemon = True
    thread.start()
    send_telegram_msg(
        chat_id, 
        "⚡ <b>تم تشغيل الأتمتة الكاملة لكافة منتجات الشيت بالخلفية بنجاح!</b>\n"
        "سيقوم الخادم الآن بالبحث والمعالجة والرفع مباشرة وتحديث الخلايا تلقائياً."
    )

def handle_bot_catalog(chat_id, query):
    """
    تصفح المجلدات السحابية لكلاوديناري والبحث فيها وإعادتها على تليجرام.
    """
    try:
        import cloudinary.api
        send_telegram_msg(chat_id, f"📂 جاري الاستعلام في كلاوديناري عن المجلد/البراند: '{query}'...")
        
        prefix_str = "products"
        if query:
            prefix_str = f"products/{query.lower().strip()}"
            
        res = cloudinary.api.resources(type="upload", prefix=prefix_str, max_results=5)
        resources = res.get("resources", [])
        
        if not resources and query:
            # تجربة فلترة المجلد العام
            res = cloudinary.api.resources(type="upload", prefix="products", max_results=10)
            resources = [r for r in res.get("resources", []) if query.lower() in r.get("public_id", "").lower()]
            
        if not resources:
            send_telegram_msg(chat_id, f"❌ لم يتم العثور على أي أصول أو صور سحابية مطابقة للبراند '{query}'.")
            return
            
        send_telegram_msg(chat_id, f"🖼️ <b>صور المنتج المرفوعة سحابياً (أول {len(resources[:5])} نتائج):</b>")
        for idx, r in enumerate(resources[:5]):
            url = r.get("secure_url")
            public_id = r.get("public_id")
            created_at = r.get("created_at", "")
            
            caption = (
                f"🖼️ <b>صورة سحابية #{idx+1}</b>\n"
                f"📝 <b>المعرف السحابي:</b> <code>{public_id}</code>\n"
                f"📅 <b>تاريخ الرفع:</b> {created_at}\n"
                f"🔗 <a href='{url}'>رابط مباشر</a>"
            )
            send_telegram_photo(chat_id, url, caption)
    except Exception as e:
        send_telegram_msg(chat_id, f"❌ خطأ أثناء تصفح كلاوديناري: {e}")

def send_bot_candidate(chat_id, row_number, idx):
    """
    عرض مرشح بصري فردي مع لوحة تحكم تفاعلية (التنقل، التعديل، البحث المخصص، والاعتماد).
    """
    search_data = LATEST_SEARCHES.get(chat_id)
    if not search_data:
        send_telegram_msg(chat_id, "❌ لا توجد جلسة بحث نشطة حالياً.")
        return
        
    candidates = search_data["candidates"]
    if idx >= len(candidates):
        send_telegram_msg(
            chat_id,
            f"🏁 <b>انتهت الصور المرشحة لهذا المنتج (الصف {row_number})!</b>\n\n"
            "يمكنك النقر على زر <code>بحث مخصص</code> لتغيير استعلام البحث يدوياً وكتابة جملة بحث جديدة.",
            {
                "inline_keyboard": [
                    [{"text": "✏️ جرب كلمة بحث أخرى مخصصة", "callback_data": f"customquery_{row_number}"}]
                ]
            }
        )
        return
        
    cand = candidates[idx]
    
    # التحقق من وجود تعديل محلي لهذه الصورة
    temp_path = f"temp_edit_{chat_id}.png"
    if os.path.exists(temp_path):
        photo_source = temp_path
        caption_suffix = " (الصورة معدلة محلياً ⚙️)"
    else:
        photo_source = cand["url"]
        caption_suffix = ""
        
    title = cand.get("title", "بدون عنوان")
    domain = cand.get("domain", "موقع عام")
    status = cand.get("status", "accepted")
    reasons = cand.get("reasons", [])
    
    status_label = "✅ مطابقة ومقبولة" if status == "accepted" else "⚠️ مستبعدة تلقائياً"
    reasons_text = "\n❌ <b>أسباب الاستبعاد:</b>\n" + "\n".join([f"• {r}" for r in reasons]) if (status == "rejected" and reasons) else ""
    
    caption = (
        f"📦 <b>المنتج:</b> {search_data['product_name']}\n"
        f"🚦 <b>الحالة بالأداة:</b> {status_label}{reasons_text}\n"
        f"🖼️ <b>صورة مرشحة ({idx+1}/{len(candidates)}){caption_suffix}</b>\n"
        f"📝 <b>العنوان:</b> {title}\n"
        f"🌐 <b>المصدر:</b> {domain}\n"
        f" صف رقم {row_number}"
    )
    
    markup = {
        "inline_keyboard": [
            [
                {"text": "✅ اعتماد وتثبيت", "callback_data": f"approve_{row_number}_{idx}"},
                {"text": "➡️ الصورة التالية", "callback_data": f"next_{row_number}_{idx}"}
            ],
            [
                {"text": "⚙️ تعديل الصورة", "callback_data": f"editmenu_{row_number}_{idx}"},
                {"text": "✏️ بحث مخصص", "callback_data": f"customquery_{row_number}"}
            ]
        ]
    }
    
    send_telegram_photo(chat_id, photo_source, caption, markup)

def handle_bot_row_search(chat_id, row_num):
    try:
        # حذف أي ملفات تعديل مؤقتة قديمة
        temp_path = f"temp_edit_{chat_id}.png"
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except:
            pass
            
        sheets_client = google_sheets.get_sheets_client()
        worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
        if not worksheet:
            send_telegram_msg(chat_id, "❌ فشل الوصول لورقة Google Sheets.")
            return
        products, _ = google_sheets.get_products(worksheet)
        
        prod = None
        for p in products:
            if p["row_number"] == row_num:
                prod = p
                break
                
        if not prod:
            send_telegram_msg(chat_id, f"❌ لم يتم العثور على منتج في الصف رقم {row_num}.")
            return
            
        name = prod["product_name"]
        brand = prod["brand"]
        query = prod["search_query"]
        product_name_ar = prod.get("product_name_ar", "")
        brand_ar = prod.get("brand_ar", "")
        barcode = prod.get("barcode", "")
        category = prod.get("category", "")
        origin = prod.get("origin", "")
        
        send_telegram_msg(chat_id, f"🔍 جاري تشغيل البحث المتقدم وتطبيق فلاتر الجودة لـ:\n<b>{name}</b> (الماركة: {brand}) | صف {row_num}...")
        
        trace = {}
        # استدعاء خط فحص الصور الذكي المتطابق مع لوحة الويب
        best_image = image_search.search_best_product_image(
            query, 
            name, 
            brand, 
            product_name_ar=product_name_ar, 
            brand_ar=brand_ar,
            trace=trace,
            barcode=barcode,
            category=category,
            origin=origin
        )
        
        # استخراج كافة المرشحات الفريدة التي فحصها الخوارزمية
        candidates = []
        seen_urls = set()
        if trace and "steps" in trace:
            for step in trace["steps"]:
                if "candidates" in step:
                    for c in step["candidates"]:
                        if c["url"] not in seen_urls:
                            seen_urls.add(c["url"])
                            candidates.append(c)
                            
        # فرز الصور المقبولة لتظهر أولاً
        candidates.sort(key=lambda x: 0 if x.get("status") == "accepted" else 1)
        
        if not candidates:
            send_telegram_msg(chat_id, f"❌ لم يتم العثور على أي صور مرشحة للمنتج '{name}' صف {row_num}.")
            return
            
        LATEST_SEARCHES[chat_id] = {
            "row_number": row_num,
            "product_name": name,
            "brand": brand,
            "candidates": candidates
        }
        
        send_bot_candidate(chat_id, row_num, 0)
            
    except Exception as e:
        send_telegram_msg(chat_id, f"❌ خطأ أثناء معالجة البحث: {e}")

def handle_bot_editaction(chat_id, row_num, idx, action):
    """
    تحرير الصورة محلياً بواسطة Pillow من أزرار تليجرام المباشرة.
    """
    search_data = LATEST_SEARCHES.get(chat_id)
    if not search_data:
        send_telegram_msg(chat_id, "❌ انتهت الجلسة الحالية.")
        return
        
    candidates = search_data["candidates"]
    cand = candidates[idx]
    image_url = cand["url"]
    
    temp_path = f"temp_edit_{chat_id}.png"
    
    try:
        # 1. تنزيل الصورة محلياً للتعديل إن لم تكن منزلة
        if not os.path.exists(temp_path):
            resp = requests.get(image_url, timeout=15)
            if resp.status_code == 200:
                with open(temp_path, "wb") as f:
                    f.write(resp.content)
            else:
                send_telegram_msg(chat_id, "❌ فشل تحميل الملف الأصلي للتعديل.")
                return
                
        # 2. تطبيق الفلتر المطلوب
        from PIL import Image, ImageEnhance
        
        if action == "bg":
            send_telegram_msg(chat_id, "⏳ جاري عزل خلفية الصورة بالذكاء الاصطناعي...")
            # عزل الخلفية
            processed_bg = image_processor.remove_background(temp_path)
            if processed_bg and os.path.exists(processed_bg):
                import shutil
                shutil.copy(processed_bg, temp_path)
                try:
                    os.remove(processed_bg)
                except:
                    pass
                send_telegram_msg(chat_id, "✅ تم إزالة الخلفية والضجيج بنجاح!")
            else:
                send_telegram_msg(chat_id, "⚠️ فشل عزل الخلفية. قد تكون الخلفية معزولة مسبقاً أو تعذر تحديد الحواف.")
                
        elif action == "color":
            img = Image.open(temp_path)
            enhancer = ImageEnhance.Color(img.convert("RGB"))
            img_enhanced = enhancer.enhance(1.4) # زيادة التشبع اللوني بـ 40%
            img_enhanced.save(temp_path)
            send_telegram_msg(chat_id, "✅ تم زيادة تشبع الألوان وتحسين المظهر الجمالي للعبوة!")
            
        elif action == "wm":
            img = Image.open(temp_path).convert("RGBA")
            from PIL import ImageDraw
            draw = ImageDraw.Draw(img)
            w, h = img.size
            # وضع نص علامة مائية بالركن
            draw.text((15, h - 35), "PREMIUM CATALOG", fill=(200, 200, 200, 150))
            img.convert("RGB").save(temp_path)
            send_telegram_msg(chat_id, "✅ تم إدراج علامة مائية للمتجر الإلكتروني بنجاح!")
            
        # إرسال المعاينة المحدثة للمستخدم
        send_bot_candidate(chat_id, row_num, idx)
        
    except Exception as e:
        send_telegram_msg(chat_id, f"❌ خطأ أثناء تطبيق الفلتر: {e}")

def handle_bot_approve(chat_id, row_num, idx):
    search_data = LATEST_SEARCHES.get(chat_id)
    if not search_data or search_data["row_number"] != row_num:
        send_telegram_msg(chat_id, "⚠️ انتهت صلاحية جلسة البحث الحالية.")
        return
        
    candidates = search_data["candidates"]
    if idx >= len(candidates):
        send_telegram_msg(chat_id, "⚠️ الصورة المحددة غير صالحة.")
        return
        
    cand = candidates[idx]
    product_name = search_data["product_name"]
    brand = search_data["brand"]
    
    # فحص إذا تم استخدام الصورة المعدلة محلياً
    temp_path = f"temp_edit_{chat_id}.png"
    if os.path.exists(temp_path):
        image_source = temp_path
        is_local = True
    else:
        image_source = cand["url"]
        is_local = False
        
    send_telegram_msg(chat_id, f"⏳ جاري رفع واعتماد الصورة للمنتج '{product_name}' صف {row_num}...")
    
    def worker():
        try:
            # 1. المعالجة البصرية
            processed_path = image_processor.process_product_image(image_source, product_name, brand)
            if not processed_path or not os.path.exists(processed_path):
                send_telegram_msg(chat_id, f"❌ فشل تحميل أو معالجة الصورة للمنتج '{product_name}' صف {row_num}.")
                return
                
            # 2. البيانات الوصفية والرفع السحابي
            metadata = image_processor.extract_metadata_from_image(processed_path, product_name, brand)
            folder = "products"
            tags = []
            
            sheets_client = google_sheets.get_sheets_client()
            worksheet = google_sheets.open_worksheet(sheets_client, config.SPREADSHEET_NAME_OR_URL)
            
            if metadata:
                google_sheets.update_product_metadata(worksheet, row_num, metadata)
                cat1 = metadata.get("category_l1_en", "").strip().lower().replace(" ", "_").replace("&", "and")
                cat2 = metadata.get("category_l2_en", "").strip().lower().replace(" ", "_").replace("&", "and")
                if cat1:
                    if cat2:
                        folder = f"products/{cat1}/{cat2}"
                    else:
                        folder = f"products/{cat1}"
                tags_str = metadata.get("tags_en", "")
                if tags_str:
                    tags = [t.strip() for t in tags_str.split(",") if t.strip()]
                    
            image_link = cloudinary_storage.upload_product_image_to_cloudinary(
                processed_path,
                product_name,
                brand,
                folder=folder,
                tags=tags
            )
            
            # تنظيف الصور المؤقتة
            try:
                if os.path.exists(processed_path):
                    os.remove(processed_path)
            except:
                pass
            if is_local:
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except:
                    pass
                    
            # 3. تحديث Google Sheets بالرابط الجديد
            _, link_column_index = google_sheets.get_products(worksheet)
            update_success = google_sheets.update_image_link(
                worksheet,
                row_num,
                link_column_index,
                image_link
            )
            
            if update_success:
                send_telegram_msg(
                    chat_id, 
                    f"<b>🎉 تم اعتماد وتثبيت الصورة وتحديث الشيت بنجاح!</b>\n\n"
                    f"📦 <b>المنتج:</b> {product_name}\n"
                    f"🔗 <a href='{image_link}'>رابط كلويديناري النهائي</a>"
                )
                config.METRICS["successful_runs"] += 1
            else:
                send_telegram_msg(chat_id, f"❌ فشل تحديث خلية الشيت بالرابط النهائي للصف {row_num}.")
                config.METRICS["failed_runs"] += 1
                
        except Exception as e:
            send_telegram_msg(chat_id, f"❌ خطأ غير متوقع أثناء معالجة الاعتماد: {e}")
            config.METRICS["failed_runs"] += 1

    t = threading.Thread(target=worker)
    t.daemon = True
    t.start()

def telegram_bot_polling_loop():
    import time
    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("⚠️ [Telegram Bot] لم يتم تحديد توكن البوت في config.py")
        return
        
    print("🤖 [Telegram Bot] بدء مستمع الأوامر التفاعلي (Long Polling)...")
    offset = 0
    
    while True:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=15"
            response = requests.get(url, timeout=20)
            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        
                        # 1. معالجة الرسائل المستلمة
                        if "message" in update:
                            message = update["message"]
                            chat_id = message["chat"]["id"]
                            text = message.get("text", "").strip()
                            
                            state_data = USER_STATES.get(chat_id, {})
                            
                            # حالة انتظار إدخال كلمة بحث مخصصة للمنتج
                            if state_data.get("state") == "waiting_for_custom_query":
                                row_num = state_data["row_number"]
                                send_telegram_msg(chat_id, f"🔍 جاري البحث المخصص عن '{text}' في الويب للمنتج...")
                                
                                results = image_search.execute_hybrid_search(text)
                                if not results:
                                    send_telegram_msg(chat_id, "❌ لم يتم العثور على نتائج. يرجى كتابة كلمة بحث أخرى مخصصة:")
                                else:
                                    # تحديث القائمة بالنتائج الجديدة وعرض أول مرشح
                                    LATEST_SEARCHES[chat_id]["candidates"] = results[:5]
                                    # حذف التعديلات المؤقتة القديمة
                                    temp_path = f"temp_edit_{chat_id}.png"
                                    try:
                                        if os.path.exists(temp_path):
                                            os.remove(temp_path)
                                    except:
                                        pass
                                    USER_STATES[chat_id] = {}
                                    send_bot_candidate(chat_id, row_num, 0)
                                    
                            # الأوامر الرئيسية والرسائل اللغوية الطبيعية بـ Gemini
                            elif text:
                                if text == "/start" or text == "/help":
                                    menu = {
                                        "inline_keyboard": [
                                            [
                                                {"text": "📊 الحالة العامة (Status)", "callback_data": "cmd_status"},
                                                {"text": "⚡ تشغيل الأتمتة (Run All)", "callback_data": "cmd_run_all"}
                                            ]
                                        ]
                                    }
                                    send_telegram_msg(
                                        chat_id, 
                                        "🤖 <b>مرحباً بك في لوحة تحكم الكتالوج وأتمتة صور المنتجات الذكية!</b>\n\n"
                                        "يمكنك التحدث معي بالعامية واللغة الطبيعية (Gemini AI), أو الضغط على الأزرار بالأسفل, أو كتابة الأوامر:\n"
                                        "• <code>/status</code> - لمعرفة إحصائيات الشيت والاستهلاك.\n"
                                        "• <code>/run_all</code> - لبدء تشغيل أتمتة الشيت بالخلفية.\n"
                                        "• <code>/row [رقم الصف]</code> - للبحث والتحكم اليدوي بصف معين (مثل: <code>/row 13</code>).\n"
                                        "• <code>/catalog [البراند]</code> - لتصفح الصور المرفوعة مسبقاً بكلاوديناري.",
                                        menu
                                    )
                                elif text.startswith("/row "):
                                    parts = text.split()
                                    if len(parts) >= 2 and parts[1].isdigit():
                                        handle_bot_row_search(chat_id, int(parts[1]))
                                    else:
                                        send_telegram_msg(chat_id, "⚠️ يرجى استخدام صيغة صف صحيحة، مثال: <code>/row 12</code>")
                                        
                                elif text.startswith("/catalog "):
                                    query = text[9:].strip()
                                    handle_bot_catalog(chat_id, query)
                                    
                                elif text == "/status":
                                    handle_bot_status(chat_id)
                                    
                                elif text == "/run_all":
                                    handle_bot_run_all(chat_id)
                                    
                                else:
                                    # التفسير الذكي للنصوص باللغة الطبيعية بواسطة Gemini (NLP)
                                    intent_data = parse_message_intent_with_gemini(text)
                                    intent = intent_data.get("intent", "unknown")
                                    
                                    if intent == "status":
                                        handle_bot_status(chat_id)
                                    elif intent == "run_all":
                                        handle_bot_run_all(chat_id)
                                    elif intent == "search_row" and intent_data.get("row_number"):
                                        handle_bot_row_search(chat_id, intent_data["row_number"])
                                    elif intent == "search_catalog" and intent_data.get("query"):
                                        handle_bot_catalog(chat_id, intent_data["query"])
                                    else:
                                        send_telegram_msg(
                                            chat_id, 
                                            "😅 <i>عذراً، لم أفهم قصدك تماماً.</i>\n\n"
                                            "يرجى الضغط على الأزرار بالأسفل، أو كتابة كلمة واضحة مثل:\n"
                                            "• 'حالة الشيت الحين'\n"
                                            "• 'شغل الأتمتة بالكامل'\n"
                                            "• 'ابحث عن صف 13'\n"
                                            "• 'تصفح صور شركة لاكنور في السحابة'"
                                        )
                                        
                        # 2. معالجة Callback Queries
                        elif "callback_query" in update:
                            cq = update["callback_query"]
                            cq_id = cq["id"]
                            chat_id = cq["message"]["chat"]["id"]
                            cq_data = cq.get("data", "")
                            
                            if cq_data == "cmd_status":
                                answer_telegram_callback(cq_id, "جاري جلب الإحصائيات الحية...")
                                handle_bot_status(chat_id)
                                
                            elif cq_data == "cmd_run_all":
                                answer_telegram_callback(cq_id, "جاري إطلاق الأتمتة...")
                                handle_bot_run_all(chat_id)
                                
                            elif cq_data.startswith("approve_"):
                                answer_telegram_callback(cq_id, "جاري معالجة الصورة واعتمادها...")
                                parts = cq_data.split("_")
                                if len(parts) >= 3:
                                    handle_bot_approve(chat_id, int(parts[1]), int(parts[2]))
                                    
                            elif cq_data.startswith("next_"):
                                parts = cq_data.split("_")
                                if len(parts) >= 3:
                                    row_num = int(parts[1])
                                    next_idx = int(parts[2]) + 1
                                    answer_telegram_callback(cq_id, "جاري تحميل الصورة التالية...")
                                    # حذف أي تعديلات مؤقتة عند التنقل
                                    temp_path = f"temp_edit_{chat_id}.png"
                                    try:
                                        if os.path.exists(temp_path):
                                            os.remove(temp_path)
                                    except:
                                        pass
                                    send_bot_candidate(chat_id, row_num, next_idx)
                                    
                            elif cq_data.startswith("customquery_"):
                                parts = cq_data.split("_")
                                if len(parts) >= 2:
                                    row_num = int(parts[1])
                                    answer_telegram_callback(cq_id, "بانتظار كلمة البحث الجديدة...")
                                    USER_STATES[chat_id] = {
                                        "state": "waiting_for_custom_query",
                                        "row_number": row_num
                                    }
                                    send_telegram_msg(chat_id, "📝 <b>يرجى كتابة كلمة أو جملة البحث المخصصة التي تريد استخدامها لهذا المنتج بالكامل:</b>")
                                    
                            elif cq_data.startswith("editmenu_"):
                                parts = cq_data.split("_")
                                if len(parts) >= 3:
                                    row_num = int(parts[1])
                                    idx = int(parts[2])
                                    answer_telegram_callback(cq_id, "جاري فتح قائمة محرر الصور البصري...")
                                    
                                    markup = {
                                        "inline_keyboard": [
                                            [
                                                {"text": "✨ عزل الخلفية", "callback_data": f"editaction_{row_num}_{idx}_bg"},
                                                {"text": "🎨 تحسين الألوان", "callback_data": f"editaction_{row_num}_{idx}_color"}
                                            ],
                                            [
                                                {"text": "🏷️ ختم علامة مائية", "callback_data": f"editaction_{row_num}_{idx}_wm"},
                                                {"text": "↩️ العودة للمرشح الأصلي", "callback_data": f"backto_{row_num}_{idx}"}
                                            ]
                                        ]
                                    }
                                    send_telegram_msg(chat_id, "⚙️ <b>قائمة محرر الصور التفاعلي (In-Chat Editor):</b>\nاختر التعديل لتطبيقه على الصورة الحالية مباشرة:", markup)
                                    
                            elif cq_data.startswith("editaction_"):
                                parts = cq_data.split("_")
                                if len(parts) >= 4:
                                    row_num = int(parts[1])
                                    idx = int(parts[2])
                                    action = parts[3]
                                    answer_telegram_callback(cq_id, "جاري معالجة وتعديل الصورة...")
                                    handle_bot_editaction(chat_id, row_num, idx, action)
                                    
                            elif cq_data.startswith("backto_"):
                                parts = cq_data.split("_")
                                if len(parts) >= 3:
                                    row_num = int(parts[1])
                                    idx = int(parts[2])
                                    answer_telegram_callback(cq_id, "جاري العودة للمرشح البصري...")
                                    temp_path = f"temp_edit_{chat_id}.png"
                                    try:
                                        if os.path.exists(temp_path):
                                            os.remove(temp_path)
                                    except:
                                        pass
                                    send_bot_candidate(chat_id, row_num, idx)
                                    
            elif response.status_code == 401:
                print("⚠️ [Telegram Bot] التوكن المدخل للبوت غير صالح!")
                time.sleep(10)
            else:
                print(f"⚠️ [Telegram Bot] خطأ في جلب التحديثات (HTTP {response.status_code})")
                time.sleep(5)
        except Exception as e:
            print(f"⚠️ [Telegram Bot Error] {e}")
            time.sleep(3)

if __name__ == '__main__':
    print("🚀 Starting localized test dashboard on http://127.0.0.1:5000")
    
    # تشغيل مستمع البوت التفاعلي لتليجرام بالخلفية مرة واحدة فقط لمنع التكرار بفعل Flask Reloader
    import os
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        t_bot = threading.Thread(target=telegram_bot_polling_loop)
        t_bot.daemon = True
        t_bot.start()
        
    app.run(debug=True, port=5000)
