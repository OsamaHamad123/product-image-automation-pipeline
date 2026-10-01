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
$action = New-ScheduledTaskAction -Execute "`"$pythonPath`"" -Argument "-X utf8 `"$runnerPath`"" -WorkingDirectory $repoRoot
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
Write-Host "   للتشغيل الآن: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "   للإلغاء: powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Unregister"
