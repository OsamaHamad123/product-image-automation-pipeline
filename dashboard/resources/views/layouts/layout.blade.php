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
            --bg-color: #080a16;
            --sidebar-bg: #0c0e1e;
            --panel-bg: rgba(16, 20, 39, 0.45);
            --panel-border: rgba(255, 255, 255, 0.05);
            --panel-border-hover: rgba(139, 92, 246, 0.25);
            --accent-purple: #8b5cf6;
            --accent-purple-hover: #a78bfa;
            --accent-cyan: #00d2ff;
            --accent-gradient: linear-gradient(135deg, #7c3aed, #00f2fe);
            --bg-gradient: radial-gradient(circle at 80% 10%, rgba(124, 58, 237, 0.08), transparent 50%), radial-gradient(circle at 20% 90%, rgba(0, 242, 254, 0.05), transparent 50%);
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --active-menu-bg: linear-gradient(90deg, rgba(124, 58, 237, 0.15) 0%, rgba(0, 242, 254, 0.05) 100%);
            --success: #10b981;
            --danger: #f43f5e;
            --warning: #f59e0b;
            --info: #06b6d4;
            --success-bg: rgba(16, 185, 129, 0.06);
            --danger-bg: rgba(244, 63, 94, 0.06);
            --warning-bg: rgba(245, 158, 11, 0.06);
            --info-bg: rgba(6, 182, 212, 0.06);
            --input-bg: rgba(8, 10, 22, 0.65);
            --card-bg: #0f132b;
            --card-bg-hover: #151b3d;
            --img-box-bg: #060815;
            --console-bg: #050711;
            --accordion-header-bg: #111634;
            
            --shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.15);
            --shadow-md: 0 8px 30px rgba(0, 0, 0, 0.3);
            --shadow-lg: 0 15px 45px rgba(0, 0, 0, 0.4);
            --border-radius-sm: 8px;
            --border-radius-md: 16px;
            --border-radius-lg: 24px;
        }

        body.light-theme {
            --bg-color: #f8fafc;
            --sidebar-bg: #ffffff;
            --panel-bg: rgba(255, 255, 255, 0.75);
            --panel-border: rgba(0, 0, 0, 0.06);
            --panel-border-hover: rgba(79, 70, 229, 0.2);
            --accent-purple: #4f46e5;
            --accent-purple-hover: #4338ca;
            --accent-cyan: #06b6d4;
            --accent-gradient: linear-gradient(135deg, #4f46e5, #06b6d4);
            --bg-gradient: radial-gradient(circle at 80% 10%, rgba(79, 70, 229, 0.04), transparent 50%);
            --text-primary: #0f172a;
            --text-secondary: #64748b;
            --active-menu-bg: linear-gradient(90deg, rgba(79, 70, 229, 0.08) 0%, rgba(6, 182, 212, 0.03) 100%);
            --success: #059669;
            --danger: #e11d48;
            --warning: #d97706;
            --info: #2563eb;
            --success-bg: rgba(5, 150, 105, 0.05);
            --danger-bg: rgba(225, 29, 72, 0.05);
            --warning-bg: rgba(217, 119, 6, 0.05);
            --info-bg: rgba(37, 99, 235, 0.05);
            --input-bg: #ffffff;
            --card-bg: #ffffff;
            --card-bg-hover: #f1f5f9;
            --img-box-bg: #f8fafc;
            --console-bg: #f8fafc;
            --accordion-header-bg: #f1f5f9;
            
            --shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.05);
            --shadow-md: 0 8px 30px rgba(0, 0, 0, 0.08);
            --shadow-lg: 0 15px 45px rgba(0, 0, 0, 0.1);
        }

        /* Scrollbar customization */
        ::-webkit-scrollbar {
            width: 6px;
            height: 6px;
        }
        ::-webkit-scrollbar-track {
            background: transparent;
        }
        ::-webkit-scrollbar-thumb {
            background: var(--panel-border);
            border-radius: 20px;
        }
        ::-webkit-scrollbar-thumb:hover {
            background: var(--accent-purple);
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
            transition: background-color 0.4s ease, color 0.4s ease;
            overflow-x: hidden;
        }

        .app-container {
            display: flex;
            width: 100%;
        }

        .sidebar {
            width: 270px;
            background: var(--sidebar-bg);
            border-left: 1px solid var(--panel-border);
            padding: 2.25rem 1.5rem;
            display: flex;
            flex-direction: column;
            position: fixed;
            height: 100vh;
            right: 0;
            top: 0;
            z-index: 100;
            box-shadow: var(--shadow-sm);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }

        .sidebar-brand {
            display: flex;
            align-items: center;
            gap: 0.85rem;
            margin-bottom: 2.5rem;
            padding: 0.5rem 0.25rem;
            text-decoration: none;
        }

        .sidebar-brand i {
            font-size: 1.8rem;
            background: var(--accent-gradient);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            filter: drop-shadow(0 0 10px rgba(139, 92, 246, 0.35));
            animation: pulse-robot 4s ease-in-out infinite;
        }

        @keyframes pulse-robot {
            0%, 100% { transform: scale(1); filter: drop-shadow(0 0 10px rgba(139, 92, 246, 0.35)); }
            50% { transform: scale(1.08); filter: drop-shadow(0 0 18px rgba(0, 242, 254, 0.6)); }
        }

        .sidebar-brand h2 {
            font-size: 1.25rem;
            font-weight: 900;
            letter-spacing: 0.5px;
            background: var(--accent-gradient);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            display: inline-block;
        }

        .sidebar-menu {
            list-style: none;
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
            flex: 1;
        }

        .sidebar-menu-item a {
            display: flex;
            align-items: center;
            gap: 1rem;
            padding: 0.85rem 1.15rem;
            color: var(--text-secondary);
            text-decoration: none;
            border-radius: var(--border-radius-sm);
            font-weight: 700;
            font-size: 0.95rem;
            transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
            overflow: hidden;
            border: 1px solid transparent;
        }

        .sidebar-menu-item a:hover {
            color: var(--text-primary);
            background: var(--card-bg-hover);
            border-color: var(--panel-border);
            transform: translateX(-4px);
        }

        .sidebar-menu-item.active a {
            background: var(--active-menu-bg);
            color: var(--text-primary);
            border-color: rgba(139, 92, 246, 0.25);
            box-shadow: inset -4px 0 0 var(--accent-purple), var(--shadow-sm);
        }
        
        .sidebar-menu-item.active a i {
            color: var(--accent-purple);
            filter: drop-shadow(0 0 6px var(--accent-purple));
        }

        .sidebar-footer {
            margin-top: auto;
            border-top: 1px solid var(--panel-border);
            padding-top: 1.5rem;
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }

        .theme-toggle-btn {
            background: var(--input-bg);
            border: 1px solid var(--panel-border);
            border-radius: var(--border-radius-sm);
            padding: 0.75rem;
            color: var(--text-primary);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.6rem;
            font-family: inherit;
            font-weight: 700;
            font-size: 0.85rem;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            box-shadow: var(--shadow-sm);
        }

        .theme-toggle-btn:hover {
            background: var(--card-bg-hover);
            border-color: var(--accent-purple);
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(139, 92, 246, 0.15);
        }

        .theme-toggle-btn i {
            font-size: 1.05rem;
            transition: transform 0.5s ease;
        }
        
        .theme-toggle-btn:hover i {
            transform: rotate(30deg);
        }

        /* Content Viewport */
        .content-viewport {
            margin-right: 270px;
            padding: 3rem;
            width: calc(100% - 270px);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }

        /* Glass Panel base */
        .glass-panel {
            background: var(--panel-bg);
            border: 1px solid var(--panel-border);
            border-radius: var(--border-radius-md);
            padding: 2rem;
            box-shadow: var(--shadow-md);
            backdrop-filter: blur(20px) saturate(180%);
            -webkit-backdrop-filter: blur(20px) saturate(180%);
            margin-bottom: 2rem;
            transition: border-color 0.3s ease, box-shadow 0.3s ease, transform 0.3s ease;
        }

        .glass-panel:hover {
            border-color: var(--panel-border-hover);
            box-shadow: var(--shadow-lg), 0 0 30px rgba(139, 92, 246, 0.02);
        }

        /* Premium Buttons */
        .btn {
            background: var(--accent-gradient);
            border: none;
            color: #fff;
            padding: 0.75rem 1.75rem;
            font-family: inherit;
            font-weight: 800;
            font-size: 0.95rem;
            border-radius: var(--border-radius-sm);
            cursor: pointer;
            transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.6rem;
            box-shadow: 0 4px 20px rgba(139, 92, 246, 0.3);
            text-decoration: none;
        }

        .btn:hover:not(:disabled) {
            transform: translateY(-2px) scale(1.01);
            box-shadow: 0 8px 30px rgba(139, 92, 246, 0.45);
        }

        .btn:active:not(:disabled) {
            transform: translateY(0) scale(1);
        }

        .btn-secondary {
            background: var(--input-bg);
            border: 1px solid var(--panel-border);
            color: var(--text-primary);
            box-shadow: var(--shadow-sm);
        }

        .btn-secondary:hover:not(:disabled) {
            background: var(--card-bg-hover);
            border-color: var(--accent-purple);
            color: var(--text-primary);
        }

        .btn-sm {
            padding: 0.45rem 1rem;
            font-size: 0.8rem;
        }

        .btn:disabled {
            opacity: 0.4;
            cursor: not-allowed;
            transform: none !important;
            box-shadow: none !important;
        }

        /* Modals */
        .modal {
            display: none;
            position: fixed;
            z-index: 10000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            overflow: auto;
            background-color: rgba(4, 6, 15, 0.85);
            backdrop-filter: blur(12px);
            align-items: center;
            justify-content: center;
            padding: 1.5rem;
        }

        /* Responsive Design */
        @media (max-width: 992px) {
            .sidebar {
                width: 80px;
                padding: 2rem 0.5rem;
                align-items: center;
            }
            .sidebar-brand h2, .sidebar-menu-item span, .theme-toggle-btn span, .sidebar-footer-credit {
                display: none;
            }
            .sidebar-brand {
                justify-content: center;
                margin-bottom: 2rem;
            }
            .content-viewport {
                margin-right: 80px;
                width: calc(100% - 80px);
                padding: 2rem;
            }
            .sidebar-menu-item a {
                padding: 0.85rem;
                justify-content: center;
            }
        }
        
        @media (max-width: 576px) {
            .content-viewport {
                padding: 1.5rem 1rem;
            }
            .glass-panel {
                padding: 1.5rem 1rem;
            }
        }
    </style>
    @yield('styles')
</head>
<body>

    <div class="app-container">
        <!-- Sidebar Navigation -->
        <aside class="sidebar">
            <a href="{{ route('dashboard.index') }}" class="sidebar-brand">
                <i class="fas fa-robot"></i>
                <h2>نظام الأتمتة</h2>
            </a>
            
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
                <div class="sidebar-footer-credit" style="font-size: 0.75rem; color: var(--text-secondary); text-align: center; font-weight: bold;">
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
        // Theme switcher logic
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

        // Restore saved theme on load
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
