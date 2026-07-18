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
            /* LUXURY DARK MONOCHROME THEME */
            --bg-color: #050505;
            --sidebar-bg: rgba(10, 10, 10, 0.85);
            --panel-bg: rgba(15, 15, 15, 0.7);
            --panel-border: rgba(255, 255, 255, 0.08);
            --panel-border-hover: rgba(255, 255, 255, 0.3);
            
            --accent-purple: #ffffff;
            --accent-purple-hover: #e5e5e5;
            --accent-cyan: #737373;
            --accent-gradient: linear-gradient(135deg, #ffffff 0%, #a3a3a3 100%);
            --bg-gradient: radial-gradient(at 0% 0%, rgba(255, 255, 255, 0.03) 0px, transparent 50%),
                           radial-gradient(at 100% 100%, rgba(255, 255, 255, 0.02) 0px, transparent 50%);
                           
            --text-primary: #ffffff;
            --text-secondary: #a3a3a3;
            --active-menu-bg: linear-gradient(90deg, rgba(255, 255, 255, 0.08) 0%, rgba(255, 255, 255, 0.01) 100%);
            
            --success: #ffffff;
            --danger: #737373;
            --warning: #a3a3a3;
            --info: #737373;
            
            --success-bg: rgba(255, 255, 255, 0.08);
            --danger-bg: rgba(255, 255, 255, 0.03);
            --warning-bg: rgba(255, 255, 255, 0.05);
            --info-bg: rgba(255, 255, 255, 0.04);
            
            --input-bg: rgba(0, 0, 0, 0.8);
            --card-bg: rgba(18, 18, 18, 0.6);
            --card-bg-hover: rgba(25, 25, 25, 0.8);
            --img-box-bg: rgba(0, 0, 0, 0.9);
            --console-bg: #000000;
            --accordion-header-bg: rgba(20, 20, 20, 0.8);
            --tabs-bg: rgba(0, 0, 0, 0.6);
            
            --shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.5);
            --shadow-md: 0 10px 40px rgba(0, 0, 0, 0.7), inset 0 1px 0 rgba(255, 255, 255, 0.05);
            --shadow-lg: 0 20px 60px rgba(0, 0, 0, 0.9), inset 0 1px 0 rgba(255, 255, 255, 0.08);
            
            --border-radius-sm: 8px;
            --border-radius-md: 12px;
            --border-radius-lg: 20px;
            
            --btn-text: #000000;
            --btn-shadow: rgba(255, 255, 255, 0.1);
            --btn-hover-shadow: rgba(255, 255, 255, 0.2);
        }

        body.light-theme {
            /* LUXURY LIGHT MONOCHROME THEME */
            --bg-color: #ffffff;
            --sidebar-bg: rgba(250, 250, 250, 0.9);
            --panel-bg: rgba(245, 245, 245, 0.85);
            --panel-border: rgba(0, 0, 0, 0.08);
            --panel-border-hover: rgba(0, 0, 0, 0.3);
            
            --accent-purple: #000000;
            --accent-purple-hover: #1c1c1c;
            --accent-cyan: #525252;
            --accent-gradient: linear-gradient(135deg, #000000 0%, #525252 100%);
            --bg-gradient: radial-gradient(at 0% 0%, rgba(0, 0, 0, 0.02) 0px, transparent 50%),
                           radial-gradient(at 100% 100%, rgba(0, 0, 0, 0.01) 0px, transparent 50%);
                           
            --text-primary: #000000;
            --text-secondary: #525252;
            --active-menu-bg: linear-gradient(90deg, rgba(0, 0, 0, 0.06) 0%, rgba(0, 0, 0, 0.01) 100%);
            
            --success: #000000;
            --danger: #8c8c8c;
            --warning: #525252;
            --info: #8c8c8c;

            --success-bg: rgba(0, 0, 0, 0.06);
            --danger-bg: rgba(0, 0, 0, 0.02);
            --warning-bg: rgba(0, 0, 0, 0.04);
            --info-bg: rgba(0, 0, 0, 0.03);
            
            --input-bg: rgba(255, 255, 255, 0.95);
            --card-bg: rgba(248, 248, 248, 0.9);
            --card-bg-hover: rgba(0, 0, 0, 0.03);
            --img-box-bg: rgba(255, 255, 255, 0.95);
            --console-bg: #f5f5f5;
            --accordion-header-bg: rgba(0, 0, 0, 0.02);
            --tabs-bg: rgba(0, 0, 0, 0.02);
            
            --shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.05);
            --shadow-md: 0 10px 40px rgba(0, 0, 0, 0.04), inset 0 1px 0 rgba(255, 255, 255, 0.9);
            --shadow-lg: 0 20px 60px rgba(0, 0, 0, 0.06), inset 0 1px 0 rgba(255, 255, 255, 0.95);
            
            --btn-text: #ffffff;
            --btn-shadow: rgba(0, 0, 0, 0.15);
            --btn-hover-shadow: rgba(0, 0, 0, 0.25);
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

        input[type="checkbox"], input[type="radio"] {
            accent-color: var(--accent-purple) !important;
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
            background-attachment: fixed;
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
            border: 1px solid var(--panel-border);
            padding: 2.25rem 1.5rem;
            display: flex;
            flex-direction: column;
            position: fixed;
            height: calc(100vh - 2rem);
            margin: 1rem;
            border-radius: var(--border-radius-md);
            right: 0;
            top: 0;
            z-index: 100;
            box-shadow: 0 15px 45px rgba(0, 0, 0, 0.45);
            backdrop-filter: blur(25px) saturate(180%);
            -webkit-backdrop-filter: blur(25px) saturate(180%);
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
            filter: drop-shadow(0 0 10px rgba(255, 255, 255, 0.25));
            animation: pulse-robot 4s ease-in-out infinite;
        }

        @keyframes pulse-robot {
            0%, 100% { transform: scale(1); filter: drop-shadow(0 0 10px rgba(255, 255, 255, 0.25)); }
            50% { transform: scale(1.08); filter: drop-shadow(0 0 18px rgba(255, 255, 255, 0.55)); }
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
            border-color: var(--panel-border);
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
            box-shadow: 0 4px 12px var(--btn-shadow);
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
            margin-right: 290px;
            padding: 3rem;
            width: calc(100% - 290px);
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
            backdrop-filter: blur(25px) saturate(180%);
            -webkit-backdrop-filter: blur(25px) saturate(180%);
            margin-bottom: 2rem;
            transition: border-color 0.3s ease, box-shadow 0.3s ease, transform 0.3s ease;
        }

        .glass-panel:hover {
            border-color: var(--panel-border-hover);
            box-shadow: var(--shadow-lg), 0 0 20px rgba(255, 255, 255, 0.02);
        }

        /* Premium Buttons */
        .btn {
            background: var(--accent-gradient);
            border: 1px solid rgba(255, 255, 255, 0.08);
            color: var(--btn-text);
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
            box-shadow: 0 4px 20px var(--btn-shadow);
            text-decoration: none;
            position: relative;
            overflow: hidden;
        }

        .btn::after {
            content: '';
            position: absolute;
            top: 0; left: -50%; width: 200%; height: 100%;
            background: linear-gradient(to right, transparent, rgba(255, 255, 255, 0.12), transparent);
            transform: skewX(-25deg);
            transition: 0.75s;
            opacity: 0;
        }

        .btn:hover::after {
            left: 125%;
            opacity: 1;
        }

        .btn:hover:not(:disabled) {
            transform: translateY(-3px);
            box-shadow: 0 8px 30px var(--btn-hover-shadow), 0 0 15px rgba(255, 255, 255, 0.05);
            border-color: rgba(255, 255, 255, 0.15);
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
            border-color: var(--accent-cyan);
            color: var(--text-primary);
            box-shadow: 0 4px 15px rgba(255, 255, 255, 0.05);
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
            background-color: rgba(3, 4, 10, 0.85);
            backdrop-filter: blur(15px);
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
                <li class="sidebar-menu-item @yield('nav_batch')">
                    <a href="{{ route('dashboard.batch_automation') }}">
                        <i class="fas fa-magic" style="width: 20px; text-align: center;"></i>
                        <span>التحكم والأتمتة الجماعية</span>
                    </a>
                </li>
                <li class="sidebar-menu-item @yield('nav_active_learning')">
                    <a href="{{ route('dashboard.active_learning') }}">
                        <i class="fas fa-brain" style="width: 20px; text-align: center;"></i>
                        <span>التعلم النشط 🧠</span>
                    </a>
                </li>
                <li class="sidebar-menu-item @yield('nav_errors')">
                    <a href="{{ route('dashboard.errors') }}">
                        <i class="fas fa-exclamation-triangle" style="width: 20px; text-align: center;"></i>
                        <span>سجل الأخطاء والتحذيرات</span>
                    </a>
                </li>
                <li class="sidebar-menu-item @yield('nav_rich_catalog')">
                    <a href="{{ route('dashboard.rich_catalog') }}">
                        <i class="fas fa-store" style="width: 20px; text-align: center;"></i>
                        <span>معرض المنتجات الغني</span>
                    </a>
                </li>
            </ul>
            
            <!-- مؤشر حالة النظام المتوهج -->
            <div id="systemStateIndicator" style="margin: 1.5rem 1rem; padding: 1rem; border-radius: 12px; background: rgba(255,255,255,0.03); border: 1px solid var(--panel-border); transition: all 0.3s ease;">
                <div style="display: flex; align-items: center; gap: 0.6rem; margin-bottom: 0.4rem;">
                    <span id="stateDot" class="status-dot fas fa-check-circle" style="width: 14px; height: 14px; border-radius: 50%; color: var(--success); display: inline-flex; align-items: center; justify-content: center;"></span>
                    <span id="stateLabel" style="font-size: 0.85rem; font-weight: bold; color: var(--text-primary);">النظام خامل</span>
                </div>
                <div id="stateProgressContainer" style="display: none; margin-top: 0.5rem;">
                    <div style="display: flex; justify-content: space-between; font-size: 0.75rem; color: var(--text-secondary); margin-bottom: 0.25rem;">
                        <span id="stateProgressText">0/0 منتجات</span>
                        <span id="statePercentText">0%</span>
                    </div>
                    <div style="width: 100%; height: 6px; background: rgba(0,0,0,0.3); border-radius: 3px; overflow: hidden;">
                        <div id="stateProgressBar" style="width: 0%; height: 100%; background: var(--accent-gradient); transition: width 0.3s ease;"></div>
                    </div>
                    <div id="stateActiveProduct" style="font-size: 0.7rem; color: var(--text-secondary); margin-top: 0.4rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%;" title=""></div>
                </div>
            </div>
            
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

        async function updateSidebarStatus() {
            try {
                const res = await fetch('/api/batch-status');
                const data = await res.json();
                
                const dot = document.getElementById('stateDot');
                const label = document.getElementById('stateLabel');
                const progressContainer = document.getElementById('stateProgressContainer');
                const panel = document.getElementById('systemStateIndicator');
                
                if (data.status === 'pre_caching' || data.is_running) {
                    if (data.pause_requested === 1) {
                        dot.style.color = 'var(--warning)';
                        dot.className = 'status-dot fas fa-pause-circle';
                        label.innerText = 'الأتمتة موقوفة مؤقتاً';
                        panel.style.boxShadow = '0 0 15px rgba(245, 158, 11, 0.15)';
                        panel.style.borderColor = 'rgba(245, 158, 11, 0.3)';
                    } else {
                        dot.style.color = 'var(--accent-cyan)';
                        dot.className = 'status-dot fas fa-spinner fa-spin';
                        label.innerText = 'جاري التحضير المسبق...';
                        panel.style.boxShadow = '0 0 15px rgba(0, 210, 255, 0.15)';
                        panel.style.borderColor = 'rgba(0, 210, 255, 0.3)';
                    }
                    
                    progressContainer.style.display = 'block';
                    const total = data.total || 0;
                    const current = data.current || 0;
                    const percent = total > 0 ? Math.round((current / total) * 100) : 0;
                    
                    document.getElementById('stateProgressText').innerText = `${current}/${total} منتج`;
                    document.getElementById('statePercentText').innerText = `${percent}%`;
                    document.getElementById('stateProgressBar').style.width = `${percent}%`;
                    
                    const activeP = document.getElementById('stateActiveProduct');
                    if (data.current_product) {
                        activeP.innerText = `المنتج: ${data.current_product}`;
                        activeP.title = data.current_product;
                    } else {
                        activeP.innerText = '';
                    }
                } else if (data.status === 'curation_pending') {
                    dot.style.color = 'var(--warning)';
                    dot.className = 'status-dot fas fa-clock';
                    label.innerText = 'بانتظار الفرز والاعتماد البشري';
                    panel.style.boxShadow = '0 0 15px rgba(245, 158, 11, 0.15)';
                    panel.style.borderColor = 'rgba(245, 158, 11, 0.3)';
                    progressContainer.style.display = 'none';
                } else if (data.status === 'ingesting') {
                    dot.style.color = 'var(--accent-purple-hover)';
                    dot.className = 'status-dot fas fa-circle-notch fa-spin';
                    label.innerText = 'جاري رفع الصور والبيانات...';
                    panel.style.boxShadow = '0 0 15px rgba(139, 92, 246, 0.15)';
                    panel.style.borderColor = 'rgba(139, 92, 246, 0.3)';
                    progressContainer.style.display = 'none';
                } else {
                    dot.style.color = 'var(--success)';
                    dot.className = 'status-dot fas fa-check-circle';
                    label.innerText = 'نظام الأتمتة جاهز وخامل';
                    panel.style.boxShadow = 'none';
                    panel.style.borderColor = 'var(--panel-border)';
                    progressContainer.style.display = 'none';
                }
            } catch (e) {
                // Ignore API errors
            }
        }
        
        setInterval(updateSidebarStatus, 4000);
        window.addEventListener('load', updateSidebarStatus);
    </script>
    @yield('scripts')
</body>
</html>
