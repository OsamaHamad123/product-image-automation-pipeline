# launch_desktop.ps1
# نص برمجى لتشغيل النظام كـ تطبيق سطح مكتب مستقل (Desktop App) بنقرة واحدة
# يقوم بتشغيل الخوادم بالخلفية وفتح نافذة Chrome بدون أشرطة أدوات (App Mode) وإغلاق الخوادم تلقائياً عند خروجك.

Add-Type -Name Window -Namespace Win32 -MemberDefinition '[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);'

# إخفاء نافذة الـ PowerShell الحالية لتبدو كتطبيق خلفية صامت
$consolePtr = [System.Diagnostics.Process]::GetCurrentProcess().MainWindowHandle
if ($consolePtr -ne [IntPtr]::Zero) {
    [Win32.Window]::ShowWindow($consolePtr, 0) # 0 = SW_HIDE
}

# 1. إيقاف أي خوادم قديمة لتجنب تضارب المنافذ
Stop-Process -Name "php" -Force -ErrorAction SilentlyContinue
$oldFlask = Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue
if ($oldFlask) {
    Stop-Process -Id $oldFlask.OwningProcess -Force -ErrorAction SilentlyContinue
}

# 2. تشغيل خادم بايثون FastAPI بالخلفية
$pythonPath = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$pythonProcess = Start-Process -FilePath $pythonPath -ArgumentList "fastapi_server.py" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru

# 3. تشغيل خادم لارافيل بالخلفية
$laravelProcess = Start-Process -FilePath "php" -ArgumentList "artisan serve --port=8000" -WorkingDirectory (Join-Path $PSScriptRoot "dashboard") -WindowStyle Hidden -PassThru

# الانتظار لتهيئة المنافذ
Start-Sleep -Seconds 3

# 4. تشغيل المتصفح في وضع التطبيق مستقل (Chrome App Mode)
$chromeApp = Start-Process -FilePath "chrome.exe" -ArgumentList "--app=http://127.0.0.1:8000/" -PassThru

# 5. مراقبة التطبيق: عند إغلاق واجهة البرنامج، قم بإغلاق خوادم الخلفية تلقائياً لمنع استهلاك الموارد
$chromeApp.WaitForExit()

# إغلاق الخوادم
Stop-Process -Id $pythonProcess.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $laravelProcess.Id -Force -ErrorAction SilentlyContinue
