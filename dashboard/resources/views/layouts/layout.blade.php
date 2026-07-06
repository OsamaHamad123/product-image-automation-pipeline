<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="csrf-token" content="{{ csrf_token() }}">
    <title>@yield('title', '🤖 لوحة الأتمتة الذكية للمنتجات')</title>
    <!-- Google Fonts & FontAwesome -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Tajawal:wght@300;500;700;900&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    
    <style>
        :root {
            --bg-color: #090b11;
            --sidebar-bg: #0f131f;
            --panel-bg: #121826;
            --panel-border: #1f293d;
            --accent-purple: #6366f1;
            --accent-purple-hover: #4f46e5;
            --accent-gradient: linear-gradient(135deg, #4f46e5, #6366f1);
            --bg-gradient: none;
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
            --active-menu-bg: rgba(99, 102, 241, 0.12);
            --success: #10b981;
            --danger: #ef4444;
            --warning: #f59e0b;
            --info: #3b82f6;
            --success-bg: rgba(16, 185, 129, 0.1);
            --danger-bg: rgba(239, 68, 68, 0.1);
            --warning-bg: rgba(245, 158, 11, 0.1);
            --info-bg: rgba(59, 130, 246, 0.1);
            --input-bg: #0f131f;
            --card-bg: #121826;
            --card-bg-hover: #1b2336;
            --img-box-bg: #090b11;
            --console-bg: #090b11;
            --accordion-header-bg: #121826;
        }

        body.light-theme {
            --bg-color: #f8fafc;
            --sidebar-bg: #ffffff;
            --panel-bg: #ffffff;
            --panel-border: #e2e8f0;
            --accent-purple: #4f46e5;
            --accent-purple-hover: #3730a3;
            --accent-gradient: linear-gradient(135deg, #4f46e5, #6366f1);
            --bg-gradient: none;
            --text-primary: #0f172a;
            --text-secondary: #64748b;
            --active-menu-bg: rgba(79, 70, 229, 0.08);
            --success: #059669;
            --danger: #dc2626;
            --warning: #d97706;
            --info: #2563eb;
            --success-bg: rgba(5, 150, 105, 0.08);
            --danger-bg: rgba(220, 38, 38, 0.08);
            --warning-bg: rgba(217, 119, 6, 0.08);
            --info-bg: rgba(37, 99, 235, 0.08);
            --input-bg: #ffffff;
            --card-bg: #ffffff;
            --card-bg-hover: #f1f5f9;
            --img-box-bg: #f8fafc;
            --console-bg: #f8fafc;
            --accordion-header-bg: #f8fafc;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Tajawal', 'Outfit', sans-serif;
            background-color: var(--bg-color);
            background-image: var(--bg-gradient);
            color: var(--text-primary);
            display: flex;
            min-height: 100vh;
            line-height: 1.6;
            transition: background-color 0.2s, color 0.2s;
        }

        /* Responsive Dashboard Sidebar Layout */
        .app-container {
            display: flex;
            width: 100%;
        }

        .sidebar {
            width: 260px;
            background: var(--sidebar-bg);
            border-left: 1px solid var(--panel-border);
            padding: 2rem 1.25rem;
            display: flex;
            flex-direction: column;
            position: fixed;
            height: 100vh;
            right: 0;
            top: 0;
            z-index: 100;
        }

        .sidebar-brand {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            margin-bottom: 2rem;
            padding: 0.5rem 0.25rem;
        }

        .sidebar-brand i {
            font-size: 1.5rem;
            color: var(--accent-purple);
        }

        .sidebar-brand h2 {
            font-size: 1.15rem;
            font-weight: 800;
            color: var(--text-primary);
        }

        .sidebar-menu {
            list-style: none;
            display: flex;
            flex-direction: column;
            gap: 0.25rem;
            flex: 1;
        }

        .sidebar-menu-item a {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            padding: 0.75rem 1rem;
            color: var(--text-secondary);
            text-decoration: none;
            border-radius: 8px;
            font-weight: 600;
            font-size: 0.9rem;
            transition: all 0.2s;
        }

        .sidebar-menu-item a:hover {
            color: var(--text-primary);
            background: var(--card-bg-hover);
        }

        .sidebar-menu-item.active a {
            background: var(--active-menu-bg);
            color: var(--accent-purple);
        }

        .sidebar-footer {
            margin-top: auto;
            border-top: 1px solid var(--panel-border);
            padding-top: 1.25rem;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
        }

        .theme-toggle-btn {
            background: var(--card-bg);
            border: 1px solid var(--panel-border);
            border-radius: 8px;
            padding: 0.5rem;
            color: var(--text-primary);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
            font-family: inherit;
            font-weight: 600;
            font-size: 0.85rem;
            transition: all 0.2s;
        }

        .theme-toggle-btn:hover {
            background: var(--card-bg-hover);
        }

        /* Main Content Viewport */
        .content-viewport {
            margin-right: 260px;
            padding: 2.5rem;
            width: calc(100% - 260px);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }

        /* Solid Panel Styles */
        .glass-panel {
            background: var(--panel-bg);
            border: 1px solid var(--panel-border);
            border-radius: 12px;
            padding: 2rem;
            box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05);
            margin-bottom: 2rem;
        }

        /* Global UI elements */
        .btn {
            background: var(--accent-purple);
            border: 1px solid rgba(0, 0, 0, 0.1);
            color: #fff;
            padding: 0.65rem 1.5rem;
            font-family: inherit;
            font-weight: 600;
            font-size: 0.9rem;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
        }

        .btn:hover {
            background: var(--accent-purple-hover);
        }

        .btn-secondary {
            background: var(--card-bg);
            border: 1px solid var(--panel-border);
            color: var(--text-primary);
            box-shadow: none;
        }

        .btn-secondary:hover {
            background: var(--card-bg-hover);
        }

        .btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
            transform: none !important;
            box-shadow: none !important;
        }

        /* Modal styling */
        .modal {
            display: none;
            position: fixed;
            z-index: 10000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            overflow: auto;
            background-color: rgba(8, 12, 20, 0.9);
            backdrop-filter: blur(8px);
            align-items: center;
            justify-content: center;
        }

        @media (max-width: 992px) {
            .sidebar {
                width: 70px;
                padding: 2rem 0.5rem;
                align-items: center;
            }
            .sidebar-brand h2, .sidebar-menu-item span, .theme-toggle-btn span, .sidebar-footer-credit {
                display: none;
            }
            .content-viewport {
                margin-right: 70px;
                width: calc(100% - 70px);
            }
        }
    </style>
    @yield('styles')
</head>
<body>

    <div class="app-container">
        <!-- Sidebar Navigation -->
        <aside class="sidebar">
            <div class="sidebar-brand">
                <i class="fas fa-robot"></i>
                <h2>نظام الأتمتة</h2>
            </div>
            
            <ul class="sidebar-menu">
                <li class="sidebar-menu-item @yield('nav_home')">
                    <a href="{{ route('dashboard.index') }}">
                        <i class="fas fa-chart-pie" style="width: 20px; text-align: center;"></i>
                        <span>الرئيسية والإحصائيات</span>
                    </a>
                </li>
                <li class="sidebar-menu-item @yield('nav_catalog')">
                    <a href="{{ route('dashboard.catalog') }}">
                        <i class="fas fa-images" style="width: 20px; text-align: center;"></i>
                        <span>فرز واعتماد الصور</span>
                    </a>
                </li>
            </ul>
            
            <div class="sidebar-footer">
                <button class="theme-toggle-btn" onclick="toggleTheme()">
                    <i class="fas fa-sun" id="themeIcon"></i>
                    <span>تغيير المظهر</span>
                </button>
                <div class="sidebar-footer-credit" style="font-size: 0.75rem; color: var(--text-secondary); text-align: center;">
                    نظام أتمتة المنتجات v5.0
                </div>
            </div>
        </aside>

        <!-- Main Content Area -->
        <main class="content-viewport">
            @yield('content')
        </main>
    </div>

    <script>
        // تغيير الثيم (فاتح / غامق)
        function toggleTheme() {
            document.body.classList.toggle('light-theme');
            const isLight = document.body.classList.contains('light-theme');
            const icon = document.getElementById('themeIcon');
            localStorage.setItem('dashboard-theme', isLight ? 'light' : 'dark');
            if (isLight) {
                icon.className = 'fas fa-moon';
            } else {
                icon.className = 'fas fa-sun';
            }
        }

        // استرجاع الثيم المفضل للمستخدم عند التحميل
        window.addEventListener('load', () => {
            const savedTheme = localStorage.getItem('dashboard-theme');
            if (savedTheme === 'light') {
                document.body.classList.add('light-theme');
                document.getElementById('themeIcon').className = 'fas fa-moon';
            }
        });
    </script>
    @yield('scripts')
</body>
</html>
