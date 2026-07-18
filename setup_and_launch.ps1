# setup_and_launch.ps1
# سكربت الإعداد والتشغيل التلقائي بنقرة واحدة لفريق إدخال البيانات
# يقوم بالتحقق من المتطلبات وتنزيل المكونات المحمولة وتشغيل النظام وإغلاقه تلقائياً عند الانتهاء.

$PSScriptRoot = Split-Path -Parent -Path $MyInvocation.MyCommand.Definition
Set-Location $PSScriptRoot

# تفعيل بروتوكول TLS 1.2 لضمان تحميل الملفات بشكل آمن ودون مشاكل شبكة من سيرفرات مايكروسوفت أو PHP
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

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
    exit 1
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
        exit 1
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

# التحقق من حزمة تشغيل Visual C++ Redistributable (مطلوبة لـ PHP)
Write-Host ""
Write-Host "⏳ جاري التحقق من حزمة تشغيل Visual C++ Redistributable..." -ForegroundColor Yellow
$vcruntime32 = Join-Path $env:SystemRoot "System32\vcruntime140.dll"
$vcruntimeSysWow64 = Join-Path $env:SystemRoot "SysWOW64\vcruntime140.dll"

if (-not (Test-Path $vcruntime32) -and -not (Test-Path $vcruntimeSysWow64)) {
    Write-Host "⚠️ لم يتم العثور على حزمة تشغيل Visual C++ Redistributable المطلوبة لتشغيل PHP." -ForegroundColor Yellow
    Write-Host "⏳ جاري تحميل حزمة التثبيت تلقائياً..." -ForegroundColor Yellow
    $vcRedistUrl = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
    $vcRedistPath = Join-Path $PSScriptRoot "vc_redist.x64.exe"
    
    try {
        Invoke-WebRequest -Uri $vcRedistUrl -OutFile $vcRedistPath
        Write-Host "📦 جاري تشغيل مثبت Visual C++... يرجى الموافقة على صلاحيات المسؤول (UAC) إذا ظهرت..." -ForegroundColor Yellow
        
        # تشغيل المثبت مع إظهار نافذة التثبيت المبسطة (Passive)
        $process = Start-Process -FilePath $vcRedistPath -ArgumentList "/install /passive /norestart" -Wait -PassThru
        Remove-Item $vcRedistPath -ErrorAction SilentlyContinue
        
        # إعادة التحقق بعد التثبيت
        if (Test-Path $vcruntime32) {
            Write-Host "✅ تم تثبيت حزمة Visual C++ Redistributable بنجاح!" -ForegroundColor Green
        } else {
            Write-Host "⚠️ يبدو أنه تم إلغاء التثبيت أو فشل. قد يواجه البرنامج مشاكل في التشغيل." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "❌ فشل تحميل أو تثبيت حزمة Visual C++ تلقائياً!" -ForegroundColor Red
        Write-Host "الرجاء تحميلها وتثبيتها يدوياً من الرابط التالي:" -ForegroundColor Yellow
        Write-Host $vcRedistUrl -ForegroundColor Yellow
    }
} else {
    Write-Host "✅ حزمة Visual C++ Redistributable مثبتة بالفعل." -ForegroundColor Green
}

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
                                   -replace ';extension=pdo_mysql', 'extension=pdo_mysql' `
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
        exit 1
    }
}

# ----------------- 4. التحقق من Composer وتثبيت حزم لارافيل -----------------
Write-Host ""
Write-Host "[4/6] جاري التحقق من أداة الملحقات Composer لوحة التحكم..." -ForegroundColor Cyan

$composerCmd = Get-Command "composer" -ErrorAction SilentlyContinue
$localComposerJar = Join-Path $PSScriptRoot "composer.phar"
$useLocalComposer = $false

if (Test-Path $localComposerJar) {
    $useLocalComposer = $true
    Write-Host "✅ تم العثور على ملف Composer المحلي." -ForegroundColor Green
} elseif ($composerCmd) {
    Write-Host "✅ تم العثور على Composer مثبت في النظام." -ForegroundColor Green
} else {
    Write-Host "⏳ جاري تحميل أداة Composer محلياً..." -ForegroundColor Yellow
    $composerUrl = "https://getcomposer.org/composer.phar"
    try {
        Invoke-WebRequest -Uri $composerUrl -OutFile $localComposerJar
        $useLocalComposer = $true
        Write-Host "✅ تم تحميل Composer بنجاح." -ForegroundColor Green
    } catch {
        Write-Host "❌ فشل تحميل Composer تلقائياً!" -ForegroundColor Red
        exit 1
    }
}

# تثبيت حزم Laravel
$vendorDir = Join-Path $PSScriptRoot "dashboard\vendor"
if (-not (Test-Path $vendorDir)) {
    Write-Host "⏳ جاري تثبيت حزم لوحة التحكم (Laravel Dependencies)... قد يستغرق ذلك بضع دقائق..." -ForegroundColor Yellow
    if ($useLocalComposer) {
        & $phpPath $localComposerJar install --working-dir="dashboard"
    } else {
        & composer install --working-dir="dashboard"
    }
    Write-Host "✅ تم تثبيت حزم لوحة التحكم بنجاح." -ForegroundColor Green
} else {
    Write-Host "✅ حزم لوحة التحكم مثبتة مسبقاً." -ForegroundColor Green
}

# تنظيف ملفات الكاش القديمة لضمان جلب بيانات حية وجديدة من جوجل شيت
Write-Host "🧹 جاري تنظيف ملفات الكاش المؤقتة لضمان جلب بيانات حية من الشيت..." -ForegroundColor Yellow
$pythonCaches = @(
    (Join-Path $PSScriptRoot "products_cache.json"),
    (Join-Path $PSScriptRoot "brand_mappings_cache.json"),
    (Join-Path $PSScriptRoot "search_cache.json")
)
foreach ($cacheFile in $pythonCaches) {
    if (Test-Path $cacheFile) {
        Remove-Item $cacheFile -Force -ErrorAction SilentlyContinue
    }
}
$laravelCacheDir = Join-Path $PSScriptRoot "dashboard\storage\framework\cache\data"
if (Test-Path $laravelCacheDir) {
    Remove-Item (Join-Path $laravelCacheDir "*") -Recurse -Force -ErrorAction SilentlyContinue
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
        & $phpPath dashboard/artisan key:generate | Out-Null
    }
}

# التحقق من ملف اعتمادات جوجل
$credentialsJson = Join-Path $PSScriptRoot "credentials.json"
$boulevardJson = Join-Path $PSScriptRoot "boulevard-a50a0-30a73e572083.json"

if (-not (Test-Path $credentialsJson)) {
    if (Test-Path $boulevardJson) {
        Copy-Item $boulevardJson $credentialsJson -Force
        Write-Host "⚙️ تم نسخ ملف اعتمادات Google Sheets المعتمد تلقائياً." -ForegroundColor Yellow
    } else {
        Write-Host ""
        Write-Host "❌ خطأ حرج: لم يتم العثور على ملف اعتمادات جوجل (credentials.json)!" -ForegroundColor Red
        Write-Host "الرجاء نسخ ملف الاعتمادات الخاص بجوجل شيت (بصيغة JSON) إلى مجلد المشروع الرئيسي وتسميته 'credentials.json'." -ForegroundColor Yellow
        Write-Host "بدون هذا الملف، لن يتمكن البرنامج من الاتصال بـ Google Sheets." -ForegroundColor Yellow
        Write-Host ""
        exit 1
    }
}

# إعداد وتحديث ملف .env الرئيسي للمشروع
$rootEnv = Join-Path $PSScriptRoot ".env"
$rootEnvExample = Join-Path $PSScriptRoot ".env.example"

if (Test-Path $rootEnv) {
    $envContent = Get-Content $rootEnv
    # إذا كان الملف يحتوي على قيم افتراضية مؤقتة، نقوم بتحديثه بالقيم الحقيقية المجهزة
    if ($envContent -match "YOUR_GOOGLE_SEARCH_API_KEY" -or $envContent -match "YOUR_CLOUDINARY_CLOUD_NAME") {
        Copy-Item $rootEnvExample $rootEnv -Force
        Write-Host "🔄 تم تحديث ملف الإعدادات .env الرئيسي بالقيم والاتصالات المجهزة تلقائياً." -ForegroundColor Yellow
    }
} else {
    if (Test-Path $rootEnvExample) {
        Copy-Item $rootEnvExample $rootEnv -Force
        Write-Host "⚙️ تم إنشاء ملف .env الرئيسي للمشروع." -ForegroundColor Yellow
    }
}

# تحديث مسار قاعدة البيانات المطلق SQLite في ملف .env الخاص بلوحة التحكم فقط إذا كان الاتصال هو SQLite
if (Test-Path $dashboardEnv) {
    $envContent = Get-Content $dashboardEnv
    
    # التحقق من نوع الاتصال الحالي
    $currentConn = ""
    if ($envContent -match '(?mi)^\s*DB_CONNECTION\s*=\s*(\w+)') {
        $currentConn = $Matches[1]
    }
    
    if ($currentConn -eq "sqlite" -or $currentConn -eq "") {
        $sqliteDbPath = Join-Path $PSScriptRoot "local_cache.db"
        $sqliteDbPathEscaped = $sqliteDbPath -replace '\\', '/'
        
        if ($envContent -match 'DB_DATABASE=') {
            $envContent = $envContent -replace '#?\s*DB_DATABASE=.*', "DB_DATABASE=`"$sqliteDbPathEscaped`""
        } else {
            $envContent += "DB_DATABASE=`"$sqliteDbPathEscaped`""
        }
        
        if ($envContent -match 'DB_CONNECTION=') {
            $envContent = $envContent -replace '#?\s*DB_CONNECTION=.*', "DB_CONNECTION=sqlite"
        } else {
            $envContent += "DB_CONNECTION=sqlite"
        }
        
        $envContent | Set-Content $dashboardEnv -Encoding UTF8
        Write-Host "✅ تم ربط قاعدة بيانات SQLite المحلية بنجاح." -ForegroundColor Green
    } else {
        Write-Host "ℹ️ تم الكشف عن اتصال قاعدة بيانات مخصص ($currentConn)، تم تخطي تهيئة SQLite التلقائية." -ForegroundColor Yellow
    }
}

# ----------------- 6. تشغيل النظام والواجهة -----------------
Write-Host ""
Write-Host "[6/6] جاري تشغيل النظام وفتح لوحة التحكم..." -ForegroundColor Cyan

# إيقاف أي خوادم سابقة لمنع التضارب
Write-Host "⏳ جاري إغلاق أي عمليات سابقة معلقة..." -ForegroundColor Yellow
Stop-Process -Name php -Force -ErrorAction SilentlyContinue
Stop-Process -Name python -Force -ErrorAction SilentlyContinue

# تأكيد وجود مجلد المؤقتات لنموذج بايثون
if (-not (Test-Path "temp")) { New-Item -ItemType Directory -Path "temp" | Out-Null }

Write-Host "🚀 جاري تشغيل خادم معالجة الصور FastAPI..." -ForegroundColor Yellow
$fastapiOut = Join-Path $PSScriptRoot "fastapi_stdout.log"
$fastapiErr = Join-Path $PSScriptRoot "fastapi_stderr.log"
$fastapiProcess = Start-Process -FilePath $venvPython -ArgumentList "fastapi_server.py" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput $fastapiOut -RedirectStandardError $fastapiErr -PassThru

Write-Host "🚀 جاري تشغيل خادم لوحة التحكم Laravel..." -ForegroundColor Yellow
$laravelOut = Join-Path $PSScriptRoot "laravel_stdout.log"
$laravelErr = Join-Path $PSScriptRoot "laravel_stderr.log"
# تشغيل خادم PHP المدمج مباشرة لتفادي مشاكل الحروف العربية في مسارات نظام التشغيل عند استدعاء artisan serve
$laravelProcess = Start-Process -FilePath $phpPath -ArgumentList "-S 127.0.0.1:8000 -t dashboard/public dashboard/server.php" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput $laravelOut -RedirectStandardError $laravelErr -PassThru

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
    
    $chromeProfile = Join-Path $PSScriptRoot "temp\chrome_profile"
    if (-not (Test-Path $chromeProfile)) { New-Item -ItemType Directory -Path $chromeProfile | Out-Null }

    Write-Host "🖥️ تم تشغيل التطبيق في وضع سطح المكتب المستقل (Chrome App Mode)." -ForegroundColor Green
    $chromeProcess = Start-Process -FilePath $chromeExe -ArgumentList "--app=http://127.0.0.1:8000/ --user-data-dir=`"$chromeProfile`"" -PassThru
    
    # الانتظار حتى يغلق المستخدم واجهة البرنامج (سيحظر هنا لأننا نستخدم ملف تعريفي مستقل للمستخدم)
    $chromeProcess.WaitForExit()
} else {
    # فتح المتصفح الافتراضي في حال عدم وجود Chrome
    Write-Host "🌐 لم يتم العثور على متصفح Chrome. جاري فتح الرابط في متصفحك الافتراضي..." -ForegroundColor Yellow
    Start-Process "http://127.0.0.1:8000/"
    
    Write-Host ""
    Write-Host "👉 تم فتح لوحة التحكم في متصفحك." -ForegroundColor Green
    Write-Host "👉 تنبيه هام: يرجى إبقاء هذه الشاشة السوداء مفتوحة أثناء العمل." -ForegroundColor Yellow
    Write-Host "👉 اضغط على أي مفتاح في هذه الشاشة السوداء لإيقاف الخوادم وإغلاق البرنامج بالكامل..." -ForegroundColor Cyan
    $null = [System.Console]::ReadKey()
}

# ----------------- 7. إيقاف الخوادم تلقائياً عند الإغلاق -----------------
Write-Host ""
Write-Host "🛑 جاري إيقاف خوادم الخلفية وتنظيف الموارد..." -ForegroundColor Yellow

Stop-Process -Id $fastapiProcess.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $laravelProcess.Id -Force -ErrorAction SilentlyContinue

Write-Host "👋 تم إغلاق النظام بنجاح. يومك سعيد!" -ForegroundColor Green
Start-Sleep -Seconds 2
