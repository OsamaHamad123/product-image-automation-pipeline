# schedule_nightly.ps1
# تسجيل التشغيل الليلي (scripts\run_nightly.py) في جدولة مهام ويندوز، أو إلغاؤه.
#
#   powershell -ExecutionPolicy Bypass -File scripts\schedule_nightly.ps1                  # كل ليلة الساعة 02:00
#   powershell -ExecutionPolicy Bypass -File scripts\schedule_nightly.ps1 -Time 03:30
#   powershell -ExecutionPolicy Bypass -File scripts\schedule_nightly.ps1 -Unregister
#
# المهمة تعمل من مجلد المشروع ببايثون البيئة الافتراضية (.venv) وتكتب سجلها في temp\nightly\.
# التشغيل الليلي يضيف للطابور صفوف الشيت التي ليس لها رابط صورة نهائي ثم يشغل العامل حتى يفرغ الطابور،
# والنشر التلقائي معطل دائماً فيه: النتائج تنتظر المراجعة في لوحة التحكم.
# عند انقطاع (قاعدة البيانات، الشيت، محركات البحث) يعيد التشغيل كله بعد 15 ثم 60 دقيقة، ثم يكتب تقرير الليلة:
# temp\nightly\last_report.json، وصف في سجل التشغيلات (بطاقة «آخر تشغيل» في صفحة الصحة)، ورسالة Telegram إن
# ضُبط TELEGRAM_BOT_TOKEN و TELEGRAM_CHAT_ID. رمز النتيجة في جدولة المهام (Last Run Result):
#   0 اكتمل، أو كان تشغيل آخر يعمل · 1 فشل (إعداد الشيت أو خطأ) · 2 انقطاع بقي بعد إعادة المحاولتين
#   3 توقف قبل نهاية الطابور: زر الإيقاف في اللوحة (يكمل العامل المنتجات الجارية ثم يتوقف ويُكتب التقرير)،
#     أو حد (الميزانية اليومية، رصيد Serper، مهلة المهمة -MaxHours)
# عامل لم يتوقف خلال 90 ثانية من طلب الإيقاف تُنهيه اللوحة: تظهر النتيجة 1 في جدولة المهام، وتقرير الليلة
# (تكتبه اللوحة من ملف القفل) يقول «توقف».
# لا يحتاج هذا السكربت أي مفتاح: المفاتيح تُقرأ من .env وصفحة الإعدادات عند كل تشغيل.
# الملف محفوظ بترميز UTF-8 مع BOM: بدونه يقرأ Windows PowerShell 5.1 النص العربي بترميز ANSI فيفشل التحليل.
#
# افتراضياً تعمل المهمة فقط أثناء تسجيل دخولك إلى ويندوز (تظهر نافذة سوداء أثناء التشغيل).
# -RunWhenLoggedOff: تعمل حتى بدون تسجيل الدخول وبدون نافذة (قد يتطلب تشغيل PowerShell كمسؤول).

param(
    [string]$Time = "02:00",
    [string]$TaskName = "ProductImageAutomation-Nightly",
    [ValidateRange(1, 23)]
    [int]$MaxHours = 8,
    [switch]$RunWhenLoggedOff,
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoRoot ".venv\Scripts\python.exe"
$runnerPath = Join-Path $repoRoot "scripts\run_nightly.py"

if ($Unregister) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "✅ تم إلغاء التشغيل الليلي ($TaskName)." -ForegroundColor Green
    } else {
        Write-Host "لا توجد مهمة باسم $TaskName." -ForegroundColor Yellow
    }
    exit 0
}

if (-not (Test-Path $pythonPath)) {
    Write-Host "❌ لم يتم العثور على بايثون البيئة الافتراضية: $pythonPath" -ForegroundColor Red
    Write-Host "شغّل setup_and_launch.bat مرة واحدة أولاً لإنشاء .venv." -ForegroundColor Yellow
    exit 1
}
if (-not (Test-Path $runnerPath)) {
    Write-Host "❌ لم يتم العثور على $runnerPath" -ForegroundColor Red
    exit 1
}
if ($Time -notmatch '^([01]\d|2[0-3]):[0-5]\d$') {
    Write-Host "❌ الوقت '$Time' غير صالح. استخدم صيغة 24 ساعة مثل 02:00 أو 23:30." -ForegroundColor Red
    exit 1
}
$at = [datetime]::ParseExact($Time, "HH:mm", [System.Globalization.CultureInfo]::InvariantCulture)

# المسارات بين علامتي تنصيص (مجلد المشروع قد يحتوي مسافات). مجلد البدء (Start in) لا يقبل علامات التنصيص.
# -MaxHours يصل للتشغيل الليلي أيضاً (--max-hours): لا يأخذ صفاً جديداً قبل الحد بربع ساعة ولا يعيد محاولة لا تتسع لها
# المهلة، فيُكتب تقرير الليلة دائماً قبل أن تُنهي جدولة المهام المهمة.
$action = New-ScheduledTaskAction -Execute "`"$pythonPath`"" -Argument "-X utf8 `"$runnerPath`" --max-hours $MaxHours" -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $at
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours $MaxHours) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$logonType = if ($RunWhenLoggedOff) { "S4U" } else { "Interactive" }
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType $logonType -RunLevel Limited
$description = "Product image automation: queue the sheet rows without a final image link and run the worker " +
    "until the queue is empty (auto-publish off). Log: temp\nightly"

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Description $description -Force | Out-Null
} catch {
    Write-Host "❌ تعذر تسجيل المهمة: $($_.Exception.Message)" -ForegroundColor Red
    if ($RunWhenLoggedOff) {
        Write-Host "الخيار -RunWhenLoggedOff يحتاج غالباً PowerShell يعمل كمسؤول (Run as administrator)." -ForegroundColor Yellow
    }
    exit 1
}

$info = Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo
Write-Host "✅ تم تسجيل التشغيل الليلي ($TaskName) كل يوم الساعة $Time." -ForegroundColor Green
Write-Host "   التشغيل القادم: $($info.NextRunTime)"
Write-Host "   بايثون: $pythonPath"
Write-Host "   السجل: $(Join-Path $repoRoot 'temp\nightly')"
Write-Host "   تقرير آخر ليلة: $(Join-Path $repoRoot 'temp\nightly\last_report.json') (وفي صفحة الصحة: «آخر تشغيل» وتبويب «التشغيل الليلي»)"
Write-Host "   للتشغيل الآن: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "   للإلغاء: powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Unregister"
