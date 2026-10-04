# setup_and_launch.ps1
# سكربت الإعداد والتشغيل التلقائي بنقرة واحدة لفريق إدخال البيانات
# يقوم بالتحقق من المتطلبات وتنزيل المكونات المحمولة وتشغيل النظام وإغلاقه تلقائياً عند الانتهاء.

$PSScriptRoot = Split-Path -Parent -Path $MyInvocation.MyCommand.Definition
Set-Location $PSScriptRoot

# تفعيل بروتوكول TLS 1.2 لضمان تحميل الملفات بشكل آمن ودون مشاكل شبكة من سيرفرات مايكروسوفت أو PHP
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# ترميز الإخراج لدعم اللغة العربية في الكونسول
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# وضع UTF-8 لكل عمليات بايثون (جسر cli_bridge.py والعامل main.py) حتى لا تفشل الأسماء العربية
# والرموز في الطباعة مع ترميز ويندوز الافتراضي (cp1252 / cp1256). ترثه كل العمليات الفرعية.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

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
& $venvPython -m pip install --upgrade pip --quiet --disable-pip-version-check | Out-Null
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
    # .gitignore يبقى: الـ "*" كانت تحذفه مع الكاش فيظهر حذفه بأول git add
    Get-ChildItem -LiteralPath $laravelCacheDir -Force | Where-Object { $_.Name -ne ".gitignore" } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
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

# الجلسات والكاش في ملفات لا في MariaDB: لو قاعدة البيانات واقفة تفتح اللوحة وتقول «قاعدة البيانات مش متاحة» بدل خطأ 500
if (Test-Path $dashboardEnv) {
    $envText = [System.IO.File]::ReadAllText($dashboardEnv)
    $newText = $envText -replace '(?m)^SESSION_DRIVER=database(\r?)$', 'SESSION_DRIVER=file$1'
    $newText = $newText -replace '(?m)^CACHE_STORE=database(\r?)$', 'CACHE_STORE=file$1'
    if ($newText -ne $envText) {
        [System.IO.File]::WriteAllText($dashboardEnv, $newText, (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "⚙️ الجلسات والكاش صاروا ملفات بدل قاعدة البيانات." -ForegroundColor Yellow
    }
}

# توليد مفتاح التطبيق للوحة التحكم إن لم يكن موجوداً
if (Test-Path $dashboardEnv) {
    # نص الملف كاملاً لا مصفوفة أسطر: -match على مصفوفة يصفّي الأسطر ولا يجيب بنعم/لا (كان يولّد مفتاحاً جديداً كل مرة)
    $envText = [System.IO.File]::ReadAllText($dashboardEnv)
    if ($envText -notmatch '(?m)^APP_KEY=base64:\S+') {
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

# قاعدة بيانات لوحة التحكم = نفس قاعدة MariaDB التي يستعملها بايثون (قيم DB_* في .env الرئيسي).
# نسخ سابقة من هذا الملف كانت تحوّل اللوحة خطأً إلى SQLite (local_cache.db) فتقرأ قاعدة غير قاعدة العامل؛ هنا تُصلَح.
if (Test-Path $dashboardEnv) {
    $dbKeys = @('DB_CONNECTION', 'DB_HOST', 'DB_PORT', 'DB_DATABASE', 'DB_USERNAME', 'DB_PASSWORD')
    $rootDb = @{ DB_CONNECTION = 'mariadb'; DB_HOST = '127.0.0.1'; DB_PORT = '3306'; DB_DATABASE = 'automation_db';
                 DB_USERNAME = 'root'; DB_PASSWORD = '' }
    if (Test-Path $rootEnv) {
        foreach ($line in [System.IO.File]::ReadAllLines($rootEnv)) {
            # سطر واحد في كل مرة: هنا -match يملأ $Matches
            if ($line -match '^\s*(DB_(?:CONNECTION|HOST|PORT|DATABASE|USERNAME|PASSWORD))\s*=\s*(.*?)\s*$') {
                $rootDb[$Matches[1]] = $Matches[2].Trim('"', "'")
            }
        }
    }
    # بايثون يتصل عبر pymysql دائماً: أي قيمة غير mysql تعني mariadb
    if ($rootDb['DB_CONNECTION'] -ne 'mysql') { $rootDb['DB_CONNECTION'] = 'mariadb' }

    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($line in [System.IO.File]::ReadAllLines($dashboardEnv)) { $lines.Add($line) }
    $wasSqlite = $false
    $changed = $false
    foreach ($key in $dbKeys) {
        $value = [string]$rootDb[$key]
        if ($value -match '[\s#"]') { $value = '"' + ($value -replace '"', '\"') + '"' }
        $want = "$key=$value"
        # السطر الفعّال أولاً، وإن لم يوجد فالسطر المعلَّق
        $idx = -1
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match "^\s*$key\s*=") { $idx = $i; break }
        }
        if ($idx -lt 0) {
            for ($i = 0; $i -lt $lines.Count; $i++) {
                if ($lines[$i] -match "^\s*#\s*$key\s*=") { $idx = $i; break }
            }
        }
        if ($idx -ge 0) {
            if ($key -eq 'DB_CONNECTION' -and $lines[$idx] -match '^\s*DB_CONNECTION\s*=\s*"?sqlite') { $wasSqlite = $true }
            if ($lines[$idx] -ne $want) { $lines[$idx] = $want; $changed = $true }
        } else {
            $lines.Add($want)
            $changed = $true
        }
    }
    if ($changed) {
        [System.IO.File]::WriteAllText($dashboardEnv, (($lines -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))
        & $phpPath dashboard/artisan config:clear | Out-Null
    }
    if ($wasSqlite) {
        Write-Host "✅ أصلحنا اتصال لوحة التحكم: كانت على SQLite، وصارت على MariaDB ($($rootDb['DB_DATABASE'])) مثل العامل." -ForegroundColor Green
    } else {
        Write-Host "✅ لوحة التحكم مربوطة بقاعدة MariaDB ($($rootDb['DB_DATABASE'])) نفسها التي يستعملها العامل." -ForegroundColor Green
    }
}

# ----------------- 6. تشغيل النظام والواجهة -----------------
Write-Host ""
Write-Host "[6/6] جاري تشغيل النظام وفتح لوحة التحكم..." -ForegroundColor Cyan

# إيقاف خوادم هذا المشغل السابقة فقط لمنع التضارب: خادم لوحة التحكم (لهذا المشروع أو على المنفذ 8000) وعامل
# مزامنة الشيت لهذا المشروع. لا نوقف أي بايثون آخر أبداً: عامل الأتمتة (main.py) أو التشغيل الليلي (run_nightly.py)
# قد يعمل الآن ويحمل القفل، ولا خادماً أو عامل مزامنة لمشروع آخر على الجهاز.
Write-Host "⏳ جاري إغلاق خوادم لوحة التحكم السابقة (عامل الأتمتة والتشغيل الليلي لا يُوقفان)..." -ForegroundColor Yellow
$repoPath = (Resolve-Path $PSScriptRoot).Path
Get-CimInstance Win32_Process -Filter "Name = 'php.exe' OR Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $cmd = [string]$_.CommandLine
        $exe = [string]$_.ExecutablePath
        # من هذا المشروع: سطر الأوامر أو البرنامج (بايثون .venv أو php المحلي) داخل مجلده
        $ours = ($cmd.IndexOf($repoPath, [StringComparison]::OrdinalIgnoreCase) -ge 0) -or ($exe.IndexOf($repoPath, [StringComparison]::OrdinalIgnoreCase) -ge 0)
        # خادم لوحة التحكم: لهذا المشروع، أو على المنفذ 8000 الذي سيأخذه الخادم الجديد
        $server = ($cmd -like '*artisan serve*' -or $cmd -like '*dashboard/server.php*' -or $cmd -like '*dashboard\server.php*') -and ($ours -or $cmd -like '*:8000*' -or $cmd -like '*--port=8000*')
        # عامل مزامنة الشيت لهذا المشروع فقط
        $sync = ($cmd -like '*sync_worker.py*') -and $ours
        ($server -or $sync) -and
        $cmd -notlike '*main.py*' -and $cmd -notlike '*run_nightly.py*'
    } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# ----------------- فحص الخدمات السحابية والاشتراكات قبل البدء -----------------
Write-Host "🚦 جاري التحقق من حالة الاشتراكات والخدمات السحابية..." -ForegroundColor Cyan
& $venvPython verify_cloud_services.py
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️" -ForegroundColor Red
    Write-Host " خطأ: فشل التحقق من صلاحية أو اشتراك بعض الخدمات السحابية الحيوية!" -ForegroundColor Red
    Write-Host " يرجى الاطلاع على التفاصيل ورموز الأخطاء المعروضة بالأعلى وحلها." -ForegroundColor Red
    Write-Host "⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️" -ForegroundColor Red
    Write-Host ""
    
    $choice = Read-Host "هل تريد تشغيل خوادم الأتمتة على أي حال بالرغم من هذا الفشل؟ (Y/N)"
    if ($choice.Trim().ToUpper() -ne "Y") {
        Write-Host "🛑 تم إلغاء تشغيل النظام بناءً على طلبك لتصحيح الأخطاء." -ForegroundColor Yellow
        Start-Sleep -Seconds 2
        exit 1
    }
}

# تأكيد وجود مجلد المؤقتات لنموذج بايثون
if (-not (Test-Path "temp")) { New-Item -ItemType Directory -Path "temp" | Out-Null }

# لا يوجد خادم FastAPI: لوحة التحكم تستدعي cli_bridge.py مباشرة.
# عامل مزامنة الشيت (sync_worker) يعمل فقط عند توفر Redis؛ بدونه تُكتب التحديثات مباشرة.
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
    Write-Host "🔁 تم اكتشاف Redis على المنفذ ${redisPort} - جاري تشغيل عامل مزامنة الشيت (sync_worker)..." -ForegroundColor Yellow
    $syncOut = Join-Path $PSScriptRoot "temp\sync_worker_stdout.log"
    $syncErr = Join-Path $PSScriptRoot "temp\sync_worker_stderr.log"
    $syncWorkerProcess = Start-Process -FilePath $venvPython -ArgumentList "sync_worker.py" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput $syncOut -RedirectStandardError $syncErr -PassThru
} else {
    Write-Host "ℹ️ Redis غير متوفر: تُكتب تحديثات الشيت مباشرة دون عامل مزامنة." -ForegroundColor Gray
}

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
Write-Host "  Python bridge: cli_bridge.py (CLI, UTF-8)" -ForegroundColor Gray
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
    # نافذة بنفس الملف التعريفي مفتوحة من تشغيل سابق: كروم يسلّمها الرابط ويخرج فوراً. ننتظر إغلاقها قبل إيقاف الخادم.
    while (Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" -ErrorAction SilentlyContinue |
           Where-Object { ([string]$_.CommandLine).Contains($chromeProfile) }) {
        Start-Sleep -Seconds 5
    }
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

if ($syncWorkerProcess) {
    Stop-Process -Id $syncWorkerProcess.Id -Force -ErrorAction SilentlyContinue
}
Stop-Process -Id $laravelProcess.Id -Force -ErrorAction SilentlyContinue

Write-Host "👋 تم إغلاق النظام بنجاح. يومك سعيد!" -ForegroundColor Green
Start-Sleep -Seconds 2
