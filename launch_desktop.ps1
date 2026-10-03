# launch_desktop.ps1
# نص برمجى لتشغيل النظام كـ تطبيق سطح مكتب مستقل (Desktop App) بنقرة واحدة
# يقوم بتشغيل لوحة التحكم بالخلفية وفتح نافذة Chrome بدون أشرطة أدوات (App Mode) وإغلاق الخوادم تلقائياً عند خروجك.
# لا يوجد خادم FastAPI: لوحة التحكم تستدعي cli_bridge.py مباشرة.

Add-Type -Name Window -Namespace Win32 -MemberDefinition '[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);'

# إخفاء نافذة الـ PowerShell الحالية لتبدو كتطبيق خلفية صامت
$consolePtr = [System.Diagnostics.Process]::GetCurrentProcess().MainWindowHandle
if ($consolePtr -ne [IntPtr]::Zero) {
    [Win32.Window]::ShowWindow($consolePtr, 0) # 0 = SW_HIDE
}

# وضع UTF-8 لكل عمليات بايثون التي تطلقها لوحة التحكم (ترثه العمليات الفرعية)
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# 1. إيقاف خوادم هذا المشغل القديمة فقط لتجنب تضارب المنافذ (خادم لوحة التحكم وعامل مزامنة الشيت).
# لا نوقف أي بايثون آخر أبداً: عامل الأتمتة (main.py) أو التشغيل الليلي (run_nightly.py) قد يعمل الآن ويحمل القفل.
Get-CimInstance Win32_Process -Filter "Name = 'php.exe' OR Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $cmd = [string]$_.CommandLine
        ($cmd -like '*artisan serve*' -or $cmd -like '*dashboard/server.php*' -or $cmd -like '*dashboard\server.php*' -or $cmd -like '*sync_worker.py*') -and
        $cmd -notlike '*main.py*' -and $cmd -notlike '*run_nightly.py*'
    } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$pythonPath = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

# 2. عامل مزامنة الشيت (sync_worker) فقط عند توفر Redis؛ بدونه تُكتب التحديثات مباشرة
$syncWorkerProcess = $null
$redisPort = 6379
if ($env:REDIS_PORT) { $redisPort = [int]$env:REDIS_PORT }
$redisUp = $false
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $attempt = $tcp.BeginConnect("127.0.0.1", $redisPort, $null, $null)
    $redisUp = $attempt.AsyncWaitHandle.WaitOne(1000, $false) -and $tcp.Connected
    $tcp.Close()
} catch {
    $redisUp = $false
}
if ($redisUp) {
    $syncWorkerProcess = Start-Process -FilePath $pythonPath -ArgumentList "sync_worker.py" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru
}

# 3. تشغيل خادم لارافيل بالخلفية
$laravelProcess = Start-Process -FilePath "php" -ArgumentList "artisan serve --port=8000" -WorkingDirectory (Join-Path $PSScriptRoot "dashboard") -WindowStyle Hidden -PassThru

# الانتظار لتهيئة المنافذ
Start-Sleep -Seconds 3

# 4. تشغيل المتصفح في وضع التطبيق مستقل (Chrome App Mode) بملف تعريفي خاص به (--user-data-dir):
# بدونه، إذا كان كروم مفتوحاً أصلاً، يسلّم الرابط للنافذة الموجودة ويخرج فوراً فيوقف السكربت الخادم.
$chromeProfile = Join-Path $PSScriptRoot "temp\chrome_profile"
if (-not (Test-Path $chromeProfile)) { New-Item -ItemType Directory -Path $chromeProfile -Force | Out-Null }
$chromeApp = Start-Process -FilePath "chrome.exe" -ArgumentList "--app=http://127.0.0.1:8000/ --user-data-dir=`"$chromeProfile`"" -PassThru

# 5. مراقبة التطبيق: عند إغلاق واجهة البرنامج، قم بإغلاق خوادم الخلفية تلقائياً لمنع استهلاك الموارد
$chromeApp.WaitForExit()
# نافذة بنفس الملف التعريفي مفتوحة من تشغيل سابق: كروم يسلّمها الرابط ويخرج فوراً. ننتظر إغلاقها قبل إيقاف الخادم.
while (Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" -ErrorAction SilentlyContinue |
       Where-Object { ([string]$_.CommandLine).Contains($chromeProfile) }) {
    Start-Sleep -Seconds 5
}

# إغلاق الخوادم
Stop-Process -Id $laravelProcess.Id -Force -ErrorAction SilentlyContinue
if ($syncWorkerProcess) {
    Stop-Process -Id $syncWorkerProcess.Id -Force -ErrorAction SilentlyContinue
}
