# setup_and_launch.ps1
# سكربت الإعداد والتشغيل التلقائي بنقرة واحدة لفريق إدخال البيانات
# يقوم بالتحقق من المتطلبات وتنزيل المكونات المحمولة وتشغيل النظام وإغلاقه تلقائياً عند الانتهاء.

$PSScriptRoot = Split-Path -Parent -Path $MyInvocation.MyCommand.Definition
Set-Location $PSScriptRoot

# ترميز الإخراج لدعم اللغة العربية في الكونسول
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "============================================================" -ForegroundColor Green
Write-Host "   نظام أتمتة صور المنتجات - إعداد وتشغيل محلي تلقائي" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""

# ----------------- 1. التحقق من تثبيت بايثون -----------------
Write-Host "[1/6] جاري التحقق من بيئة بايثون (Python)..." -ForegroundColor Cyan
$pythonInSystem = Get-Command "python" -ErrorAction SilentlyContinue
if (-not $pythonInSystem) {
    # محاولة البحث في مسارات التثبيت الافتراضية للمستخدم
    $localPythonPath = "$env:USERPROFILE\AppData\Local\Programs\Python"
    if (Test-Path $localPythonPath) {
        $pythonExe = Get-ChildItem -Path $localPythonPath -Filter "python.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($pythonExe) {
            $pythonCmd = $pythonExe.FullName
            Write-Host "✅ تم العثور على بايثون في المسار المحلي: $pythonCmd" -ForegroundColor Green
        }
    }
} else {
    $pythonCmd = "python"
    Write-Host "✅ تم العثور على بايثون مثبت في النظام." -ForegroundColor Green
}

if (-not $pythonCmd) {
    Write-Host "❌ لم يتم العثور على بايثون (Python) مثبت على جهازك!" -ForegroundColor Red
    Write-Host "يرجى تحميل بايثون (إصدار 3.10 أو أحدث) وتثبيته من الموقع الرسمي:" -ForegroundColor Yellow
    Write-Host "https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "ملاحظة هامة: تأكد من تفعيل خيار 'Add python.exe to PATH' أثناء التثبيت." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "اضغط على أي مفتاح للخروج..."
    $null = [System.Console]::ReadKey()
    exit
}

# ----------------- 2. إعداد البيئة الافتراضية لبايثون -----------------
Write-Host ""
Write-Host "[2/6] جاري التحقق من البيئة الافتراضية للمكتبات البرمجية..." -ForegroundColor Cyan
$venvDir = Join-Path $PSScriptRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

if (-not (Test-Path $venvDir)) {
    Write-Host "⏳ جاري إنشاء البيئة الافتراضية (.venv)... قد يستغرق ذلك دقيقة..." -ForegroundColor Yellow
    & $pythonCmd -m venv $venvDir
    if (-not (Test-Path $venvPython)) {
        Write-Host "❌ فشل إنشاء البيئة الافتراضية!" -ForegroundColor Red
        exit
    }
    Write-Host "✅ تم إنشاء البيئة الافتراضية بنجاح." -ForegroundColor Green
} else {
    Write-Host "✅ البيئة الافتراضية موجودة مسبقاً." -ForegroundColor Green
}

# تثبيت متطلبات بايثون
Write-Host "⏳ جاري تحديث وتثبيت مكتبات بايثون المطلوبة (Requirements)..." -ForegroundColor Yellow
& $venvPython -m pip install --upgrade pip -ErrorAction SilentlyContinue | Out-Null
& $venvPython -m pip install -r requirements.txt
Write-Host "✅ تم إعداد مكتبات بايثون بنجاح." -ForegroundColor Green

# ----------------- 3. التحقق من PHP وتوفير نسخة محمولة -----------------
Write-Host ""
Write-Host "[3/6] جاري التحقق من بيئة PHP لتشغيل لوحة التحكم..." -ForegroundColor Cyan

$phpCmd = Get-Command "php" -ErrorAction SilentlyContinue
$localPhpDir = Join-Path $PSScriptRoot "php"
$localPhpExe = Join-Path $localPhpDir "php.exe"

if (Test-Path $localPhpExe) {
    $phpPath = $localPhpExe
    Write-Host "✅ تم العثور على نسخة PHP المحمولة في مجلد المشروع." -ForegroundColor Green
} elseif ($phpCmd) {
    $phpPath = "php"
    Write-Host "✅ تم العثور على PHP مثبت في النظام." -ForegroundColor Green
} else {
    # تحميل نسخة PHP المحمولة
    Write-Host "⏳ لم يتم العثور على PHP. جاري تنزيل نسخة محمولة مستقرة (PHP 8.2)..." -ForegroundColor Yellow
    
    # رابط نسخة PHP المحمولة الرسمية لـ Windows
    $phpZipUrl = "https://windows.php.net/downloads/releases/archives/php-8.2.12-nts-Win32-vs16-x64.zip"
    $zipPath = Join-Path $PSScriptRoot "php.zip"
    
    try {
        Invoke-WebRequest -Uri $phpZipUrl -OutFile $zipPath
        Write-Host "📦 جاري فك ضغط ملفات PHP وتجهيزها..." -ForegroundColor Yellow
        if (-not (Test-Path $localPhpDir)) { New-Item -ItemType Directory -Path $localPhpDir | Out-Null }
        Expand-Archive -Path $zipPath -DestinationPath $localPhpDir -Force
        Remove-Item $zipPath -ErrorAction SilentlyContinue
        
        # إعداد ملف php.ini لتفعيل الإضافات المطلوبة لـ Laravel
        Write-Host "⚙️ جاري ضبط إعدادات PHP المحلية وتفعيل ملحقات SQLite و Curl..." -ForegroundColor Yellow
        $iniExample = Join-Path $localPhpDir "php.ini-development"
        $iniPath = Join-Path $localPhpDir "php.ini"
        
        if (Test-Path $iniExample) {
            Copy-Item $iniExample $iniPath -Force
            (Get-Content $iniPath) -replace ';extension_dir = "ext"', 'extension_dir = "ext"' `
                                   -replace ';extension=curl', 'extension=curl' `
                                   -replace ';extension=fileinfo', 'extension=fileinfo' `
                                   -replace ';extension=mbstring', 'extension=mbstring' `
                                   -replace ';extension=openssl', 'extension=openssl' `
                                   -replace ';extension=pdo_sqlite', 'extension=pdo_sqlite' `
                                   -replace ';extension=sqlite3', 'extension=sqlite3' `
                                   -replace ';extension=xml', 'extension=xml' | Set-Content $iniPath
        }
        
        $phpPath = $localPhpExe
        Write-Host "✅ تم تحميل وإعداد نسخة PHP المحمولة بنجاح." -ForegroundColor Green
    } catch {
        Write-Host "❌ فشل تنزيل PHP المحمول تلقائياً!" -ForegroundColor Red
        Write-Host "الخطأ: $_" -ForegroundColor Red
        Write-Host "يرجى تثبيت PHP 8.1 أو أحدث يدوياً وإضافته لمتغيرات البيئة (PATH)." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "اضغط على أي مفتاح للخروج..."
        $null = [System.Console]::ReadKey()
        exit
    }
}

# ----------------- 4. التحقق من Composer وتثبيت حزم لارافيل -----------------
Write-Host ""
Write-Host "[4/6] جاري التحقق من أداة الملحقات Composer لوحة التحكم..." -ForegroundColor Cyan

$composerCmd = Get-Command "composer" -ErrorAction SilentlyContinue
$localComposerJar = Join-Path $PSScriptRoot "composer.phar"

if (Test-Path $localComposerJar) {
    $composerRun = "$phpPath `"$localComposerJar`""
    Write-Host "✅ تم العثور على ملف Composer المحلي." -ForegroundColor Green
} elseif ($composerCmd) {
    $composerRun = "composer"
    Write-Host "✅ تم العثور على Composer مثبت في النظام." -ForegroundColor Green
} else {
    Write-Host "⏳ جاري تحميل أداة Composer محلياً..." -ForegroundColor Yellow
    $composerUrl = "https://getcomposer.org/composer.phar"
    try {
        Invoke-WebRequest -Uri $composerUrl -OutFile $localComposerJar
        $composerRun = "$phpPath `"$localComposerJar`""
        Write-Host "✅ تم تحميل Composer بنجاح." -ForegroundColor Green
    } catch {
        Write-Host "❌ فشل تحميل Composer تلقائياً!" -ForegroundColor Red
        exit
    }
}

# تثبيت حزم Laravel
$vendorDir = Join-Path $PSScriptRoot "dashboard\vendor"
if (-not (Test-Path $vendorDir)) {
    Write-Host "⏳ جاري تثبيت حزم لوحة التحكم (Laravel Dependencies)... قد يستغرق ذلك بضع دقائق..." -ForegroundColor Yellow
    Invoke-Expression "& $composerRun install --working-dir=`"dashboard`""
    Write-Host "✅ تم تثبيت حزم لوحة التحكم بنجاح." -ForegroundColor Green
} else {
    Write-Host "✅ حزم لوحة التحكم مثبتة مسبقاً." -ForegroundColor Green
}

# ----------------- 5. تهيئة ملفات الإعدادات وقاعدة البيانات -----------------
Write-Host ""
Write-Host "[5/6] جاري تهيئة ملفات التكوين والبيئة المحلية..." -ForegroundColor Cyan

# إعداد ملف .env للوحة التحكم
$dashboardEnv = Join-Path $PSScriptRoot "dashboard\.env"
$dashboardEnvExample = Join-Path $PSScriptRoot "dashboard\.env.example"

if (-not (Test-Path $dashboardEnv)) {
    if (Test-Path $dashboardEnvExample) {
        Copy-Item $dashboardEnvExample $dashboardEnv -Force
        Write-Host "⚙️ تم إنشاء ملف .env الخاص بلوحة التحكم." -ForegroundColor Yellow
    }
}

# توليد مفتاح التطبيق للوحة التحكم إن لم يكن موجوداً
if (Test-Path $dashboardEnv) {
    $envContent = Get-Content $dashboardEnv
    if (($envContent -match "APP_KEY=\s*$" -or $envContent -notmatch "APP_KEY=base64:")) {
        Write-Host "🔑 جاري توليد مفتاح الأمان للوحة التحكم..." -ForegroundColor Yellow
        Invoke-Expression "& $phpPath dashboard/artisan key:generate" | Out-Null
    }
}

# التحقق من ملف اعتمادات جوجل
$credentialsJson = Join-Path $PSScriptRoot "credentials.json"
$boulevardJson = Join-Path $PSScriptRoot "boulevard-a50a0-30a73e572083.json"

if (-not (Test-Path $credentialsJson)) {
    if (Test-Path $boulevardJson) {
        Copy-Item $boulevardJson $credentialsJson -Force
        Write-Host "⚙️ تم نسخ ملف اعتمادات Google Sheets المعتمد تلقائياً." -ForegroundColor Yellow
    }
}

# إعداد ملف .env الرئيسي للمشروع
$rootEnv = Join-Path $PSScriptRoot ".env"
$rootEnvExample = Join-Path $PSScriptRoot ".env.example"

if (-not (Test-Path $rootEnv)) {
    if (Test-Path $rootEnvExample) {
        Copy-Item $rootEnvExample $rootEnv -Force
        Write-Host "⚙️ تم إنشاء ملف .env الرئيسي للمشروع (تذكر تعبئة مفاتيح الـ API لاحقاً)." -ForegroundColor Yellow
    }
}

# تحديث مسار قاعدة البيانات المطلق SQLite في ملف .env الخاص بلوحة التحكم
$sqliteDbPath = Join-Path $PSScriptRoot "local_cache.db"
# تحويل السلاشات لتناسب صيغة ملف التكوين
$sqliteDbPathEscaped = $sqliteDbPath -replace '\\', '/'

if (Test-Path $dashboardEnv) {
    $envContent = Get-Content $dashboardEnv
    $envContent = $envContent -replace 'DB_DATABASE=.*', "DB_DATABASE=$sqliteDbPathEscaped"
    $envContent | Set-Content $dashboardEnv
    Write-Host "✅ تم ربط قاعدة بيانات SQLite المحلية بنجاح." -ForegroundColor Green
}

# ----------------- 6. تشغيل النظام والواجهة -----------------
Write-Host ""
Write-Host "[6/6] جاري تشغيل النظام وفتح لوحة التحكم..." -ForegroundColor Cyan

# إيقاف أي خوادم سابقة لمنع التضارب
Write-Host "⏳ جاري إغلاق أي عمليات سابقة معلقة..." -ForegroundColor Yellow
$oldFastApi = Get-NetTCPConnection -LocalPort 8001 -ErrorAction SilentlyContinue
if ($oldFastApi) {
    Stop-Process -Id $oldFastApi.OwningProcess -Force -ErrorAction SilentlyContinue
}
$oldLaravel = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
if ($oldLaravel) {
    Stop-Process -Id $oldLaravel.OwningProcess -Force -ErrorAction SilentlyContinue
}

# تأكيد وجود مجلد المؤقتات لنموذج بايثون
if (-not (Test-Path "temp")) { New-Item -ItemType Directory -Path "temp" | Out-Null }

Write-Host "🚀 جاري تشغيل خادم معالجة الصور FastAPI..." -ForegroundColor Yellow
$fastapiOut = Join-Path $PSScriptRoot "fastapi_stdout.log"
$fastapiErr = Join-Path $PSScriptRoot "fastapi_stderr.log"
$fastapiProcess = Start-Process -FilePath $venvPython -ArgumentList "fastapi_server.py" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput $fastapiOut -RedirectStandardError $fastapiErr -PassThru

Write-Host "🚀 جاري تشغيل خادم لوحة التحكم Laravel..." -ForegroundColor Yellow
$laravelOut = Join-Path $PSScriptRoot "laravel_stdout.log"
$laravelErr = Join-Path $PSScriptRoot "laravel_stderr.log"
$laravelProcess = Start-Process -FilePath $phpPath -ArgumentList "dashboard/artisan serve --port=8000" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput $laravelOut -RedirectStandardError $laravelErr -PassThru

# الانتظار حتى تهيئة الخدمات
Start-Sleep -Seconds 4

Write-Host ""
Write-Host "🎉 تم تشغيل كافة الخدمات بنجاح!" -ForegroundColor Green
Write-Host "------------------------------------------------------------" -ForegroundColor Green
Write-Host "  FastAPI Server runs on http://127.0.0.1:8001" -ForegroundColor Gray
Write-Host "  Laravel Dashboard runs on http://127.0.0.1:8000" -ForegroundColor Gray
Write-Host "------------------------------------------------------------" -ForegroundColor Green
Write-Host "جاري فتح لوحة التحكم..." -ForegroundColor Cyan

$chromePath = Get-Command "chrome.exe" -ErrorAction SilentlyContinue
if ($chromePath -or (Test-Path "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe") -or (Test-Path "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe")) {
    # فتح كروم في وضع التطبيق المستقل (App Mode)
    $chromeExe = "chrome.exe"
    if (-not $chromePath) {
        if (Test-Path "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe") {
            $chromeExe = "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe"
        } else {
            $chromeExe = "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
        }
    }
    
    Write-Host "🖥️ تم تشغيل التطبيق في وضع سطح المكتب المستقل (Chrome App Mode)." -ForegroundColor Green
    $chromeProcess = Start-Process -FilePath $chromeExe -ArgumentList "--app=http://127.0.0.1:8000/" -PassThru
    
    # الانتظار حتى يغلق المستخدم واجهة البرنامج
    $chromeProcess.WaitForExit()
} else {
    # فتح المتصفح الافتراضي في حال عدم وجود Chrome
    Write-Host "🌐 لم يتم العثور على متصفح Chrome. جاري فتح الرابط في متصفحك الافتراضي..." -ForegroundColor Yellow
    Start-Process "http://127.0.0.1:8000/"
    
    Write-Host ""
    Write-Host "👉 اضغط على أي مفتاح هنا لإيقاف الخوادم وإغلاق البرنامج بالكامل..." -ForegroundColor Cyan
    $null = [System.Console]::ReadKey()
}

# ----------------- 7. إيقاف الخوادم تلقائياً عند الإغلاق -----------------
Write-Host ""
Write-Host "🛑 جاري إيقاف خوادم الخلفية وتنظيف الموارد..." -ForegroundColor Yellow

Stop-Process -Id $fastapiProcess.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $laravelProcess.Id -Force -ErrorAction SilentlyContinue

Write-Host "👋 تم إغلاق النظام بنجاح. يومك سعيد!" -ForegroundColor Green
Start-Sleep -Seconds 2
