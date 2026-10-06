# نشر النظام على سيرفر Ubuntu

هاد الدليل بيشرح كيف تنقل النظام من جهاز ويندوز لسيرفر Ubuntu (22.04 أو 24.04)، وكيف تحدّثه وترجع لنسخة قديمة.
كل الأوامر بتنفّذها على السيرفر إلا إذا انكتب غير هيك. الأوامر والأسماء بتضل بالإنجليزي.

## شو بينركّب

- **اللوحة** (Laravel): nginx + php-fpm، بمجلد `/opt/laqta/dashboard`.
- **بايثون**: بيئة `.venv` داخل `/opt/laqta`. اللوحة بتلاقيها لحالها (`PythonBridge` بيدوّر على `.venv/bin/python`)، ما في شي تضبطه.
- **قاعدة البيانات**: MariaDB على نفس السيرفر (مو مفتوحة للشبكة).
- **خدمات systemd**: `laqta-nightly.timer` (التشغيل الليلي مثل `schedule_nightly.ps1`)، `laqta-backup.timer` (نسخة احتياطية يومية)،
  و`laqta-sync-worker.service` (فقط إذا بتستعمل Redis؛ نفس منطق `start_all.bat`).
- كل شي بيشتغل بمستخدم نظام اسمه `laqta` (مو root). إزالة الخلفية بـ BiRefNet اختيارية (CPU افتراضياً، وGPU لو متوفر).

> **تحذير أمني:** اللوحة ما إلها تسجيل دخول خاص فيها. أي حدا بيوصل إلها بيقدر يعتمد صور وينشر ويكتب بالشيت.
> لهيك nginx **بيطلب كلمة سر (basic auth)** على كل صفحة، ولازم تشغّلها على **HTTPS**. ما تفتح اللوحة للإنترنت بدون الاثنين.
> وفي بديل بدون تعريض أي منفذ: localhost + نفق SSH أو Tailscale (القسم 7).

## 1. المتطلبات

| الشي | التفاصيل |
|---|---|
| السيرفر | Ubuntu 22.04 أو 24.04، وصول `sudo`، ورام 2GB على الأقل (4GB أو أكثر إذا بدك BiRefNet الكامل) |
| القرص | 10GB فاضية على الأقل (الموديل الكبير بياخد حوالي 1GB، والنسخ الاحتياطية بتكبر) |
| الشبكة | خروج للإنترنت (apt, pip, Google, Cloudinary, محركات البحث). منافذ 22 و80 و443 مفتوحة إذا بدك HTTPS |
| الدومين | اسم مثل `dash.example.com` بيشير (سجل A) لعنوان السيرفر. بدونه استعمل القسم 7 |
| من جهازك الحالي | الملفات `.env` و`dashboard/.env` و`credentials.json` (ما بتنرفع على GitHub أبداً) |
| كلمات سر | كلمة سر لقاعدة البيانات (12 حرف أو أكثر) وكلمة سر للوحة (بتكتبها لـ install.sh) |

الكرت الرسومي مو شرط. النظام بيشتغل على CPU، والـ GPU اختياري (القسم 4).

## 2. جيب الكود (clone)

```bash
sudo apt-get update && sudo apt-get install -y git
sudo git clone <رابط-الريبو-على-GitHub> /opt/laqta
```

- لا تحط المشروع تحت `/home` (php-fpm بيحميه). `/opt/laqta` هو المكان المقصود.
- إذا الريبو خاص: سوّي deploy key للقراءة فقط وأضفه من إعدادات الريبو بـ GitHub، واستعمل رابط SSH. لا تحط توكن شخصي بالرابط.

## 3. انسخ `.env` و `credentials.json` (بأمان)

من **جهاز ويندوز** (PowerShell، من مجلد المشروع). أسماء الوجهة مختلفة لأن في ملفين اسمهم `.env`:

```powershell
scp .env user@SERVER_IP:/tmp/laqta.env
scp dashboard\.env user@SERVER_IP:/tmp/laqta-dashboard.env
scp credentials.json user@SERVER_IP:/tmp/laqta-credentials.json
```

وعلى **السيرفر**:

```bash
sudo install -m 600 /tmp/laqta.env /opt/laqta/.env
sudo install -m 600 /tmp/laqta-dashboard.env /opt/laqta/dashboard/.env
sudo install -m 600 /tmp/laqta-credentials.json /opt/laqta/credentials.json
sudo shred -u /tmp/laqta.env /tmp/laqta-dashboard.env /tmp/laqta-credentials.json
```

إذا نسختهم بطريقة ثانية تأكد إنهم للمالك بس: `sudo chmod 600 /opt/laqta/.env /opt/laqta/dashboard/.env /opt/laqta/credentials.json`
(install.sh وserver_check.py بيتأكدوا من هالصلاحيات كمان).

عدّل القيم بالنسخة اللي على السيرفر (`sudo nano /opt/laqta/.env`):

- `.env` و`dashboard/.env`، نفس قيم القاعدة بالاثنين: `DB_HOST=127.0.0.1`، `DB_DATABASE=automation_db`، `DB_USERNAME=laqta_app`،
  و`DB_PASSWORD` هي نفس كلمة السر اللي رح تعطيها لـ install.sh.
- `dashboard/.env`: `APP_ENV=production`، `APP_DEBUG=false` (مهم: `true` بيعرض المفاتيح بصفحة الخطأ)، `APP_URL=https://dash.example.com`،
  `SESSION_DRIVER=file`، `CACHE_STORE=file`. اترك `APP_KEY=` فاضي: install.sh بيولّده.
- `.env`: مفاتيح البحث والتحقق وCloudinary و`SPREADSHEET_NAME_OR_URL`، وطريقة إزالة الخلفية `BG_REMOVAL_METHOD`.

**دوّر (غيّر) أي مفتاح انحفظ يوم بالـ git**، حتى لو انحذف بعدين، لأن التاريخ بيضل محفوظ. للتأكد:

```bash
git -C /opt/laqta log --all --full-history --oneline -- .env credentials.json
```

إذا طلع شي: أنشئ مفتاح جديد لحساب خدمة Google (وامسح القديم من Google Cloud) وبدّل `credentials.json`، وغيّر كمان Cloudinary API secret
ومفاتيح Serper وGemini وPhotoRoom وAnthropic وتوكن Telegram. ما تبعت المفاتيح بإيميل أو شات.
**install.sh ما بيعمل `.env` ولا بيعدّله** (الاستثناء الوحيد: `artisan key:generate` بيكتب `APP_KEY` بـ `dashboard/.env` إذا كان فاضي).

## 4. شغّل install.sh

أول مرة (كلمة سر القاعدة بتنقرا بدون ما تنحفظ بسجل الأوامر):

```bash
read -rsp 'كلمة سر قاعدة البيانات (12 حرف أو أكثر): ' LAQTA_DB_PASSWORD; echo; export LAQTA_DB_PASSWORD
sudo --preserve-env=LAQTA_DB_PASSWORD bash /opt/laqta/deploy/ubuntu/install.sh /opt/laqta --server-name dash.example.com
```

بيسألك مرة وحدة عن كلمة سر اللوحة (القسم 5). بإمكانك تعيد تشغيله كل ما بدك: كل خطوة بتتأكد قبل ما تغيّر. جرّب أولاً بدون تغيير: `--dry-run`.

| الخيار | المعنى |
|---|---|
| `APP_DIR` (أول وسيط) | مجلد الكلون، الافتراضي المجلد اللي فيه السكربت |
| `--server-name HOST` | اسم الدومين لـ nginx (لازم لـ HTTPS) |
| `--local-only` | nginx بيسمع على `127.0.0.1:8080` بس (القسم 7) |
| `--with-birefnet` | بيركّب `rembg[cpu]` وبينزّل موديل `birefnet-general` |
| `--with-birefnet --lite` | نفس الشي بس الموديل الأصغر `birefnet-general-lite` (للرام القليلة) |
| `--with-birefnet --gpu` | `rembg[gpu]` (بدك كرت NVIDIA ودرايفر ومكتبات CUDA مناسبة) |
| `--with-redis` | بينصّب redis-server (فقط إذا بتستعمل Redis) |
| `--enable-units` | بيفعّل التايمرات (والـ sync worker مع `--with-redis`) |
| `--reset-auth`, `--auth-user NAME` | كلمة سر جديدة للوحة، واسم المستخدم (الافتراضي `admin`) |
| `--skip-apt`, `--php-version X.Y`, `--user NAME` | تخطّي apt، نسخة PHP (الافتراضي 8.3)، مستخدم النظام (الافتراضي `laqta`) |

اللي بيسويه بالترتيب: حزم apt (بما فيها `libgl1` و`libglib2.0-0` للصور؛ على 22.04 بيضيف PPA لـ PHP 8.3 لأن اللوحة بدها PHP 8.2+)،
المستخدم والمجلدات، `.venv` و`pip install -r requirements.txt`، `composer install --no-dev`، `key:generate` إذا `APP_KEY` فاضي، `config:cache` و`route:cache`،
صلاحيات `storage`، قاعدة البيانات والمستخدم (من `LAQTA_DB_NAME` و`LAQTA_DB_USER` و`LAQTA_DB_PASSWORD`) وبعدها `init_db()` (الـ schema)،
وحدات systemd، pool خاص بـ php-fpm بيشتغل بمستخدم `laqta`، وموقع nginx. بالآخر بيطبع **أسماء** المفاتيح الناقصة من `.env.example` (بدون قيمها).
إذا خلّص برمز 2 معناها في شي لازم تنتبه إله (مكتوب بالآخر).

**BiRefNet:** بعد `--with-birefnet` خلّي `BG_REMOVAL_METHOD=rembg` بـ `.env` (أو من صفحة الإعدادات). الموديلات بتنحفظ بـ `/var/lib/laqta/models`.
على CPU هو أبطأ بكثير من PhotoRoom، وأول تحميل بياخد رام كبيرة؛ إذا السيرفر ضعيف استعمل `--lite`.

## 5. مستخدم كلمة سر اللوحة (basic auth)

- install.sh بيعمل الملف `/etc/nginx/laqta.htpasswd` وبيطلب منك كلمة السر بالكتابة (ما بتنكتب بأي ملف بالريبو). بدون الملف الموقع ما بيتفعّل.
- مستخدم إضافي: `sudo htpasswd -B /etc/nginx/laqta.htpasswd ahmad`
- تغيير كلمة سر: `sudo htpasswd -B /etc/nginx/laqta.htpasswd admin` (أو `install.sh ... --reset-auth`)
- حذف مستخدم: `sudo htpasswd -D /etc/nginx/laqta.htpasswd ahmad`

## 6. HTTPS بـ certbot

1. سجل A للدومين لازم يشير للسيرفر. افتح المنافذ: `sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full' && sudo ufw enable`.
2. شغّل install.sh مع `--server-name dash.example.com` (القسم 4). **لا تفتح الموقع بالمتصفح بعد**: لسا HTTP وكلمة السر بتنبعت مكشوفة.
3. أصدر الشهادة (مسار التحقق مفتوح بدون كلمة سر، والباقي محمي):

```bash
sudo certbot certonly --webroot -w /var/www/letsencrypt -d dash.example.com -m you@example.com --agree-tos --deploy-hook "systemctl reload nginx"
```

4. أعد تشغيل install.sh بنفس الخيارات: لما بلاقي الشهادة بيضيف HTTPS وتحويل من 80 إلى 443 بنفسه (certbot ما بيعدّل ملف nginx).
5. اختبر: `curl -I https://dash.example.com/` لازم يرجع `401` بدون كلمة سر. التجديد تلقائي؛ جرّبه: `sudo certbot renew --dry-run`.

## 7. بدون إنترنت عام: localhost + نفق SSH أو Tailscale

شغّل `install.sh /opt/laqta --local-only`. nginx بيسمع على `127.0.0.1:8080` بس، وكلمة السر بتضل مطلوبة. ما تفتح منافذ 80/443.

- **نفق SSH** (من جهازك): `ssh -L 8080:127.0.0.1:8080 user@SERVER_IP` وخلّي النافذة مفتوحة، وافتح `http://127.0.0.1:8080/`.
- **Tailscale**: ركّب Tailscale على السيرفر وعلى جهازك بنفس الحساب، وبعدها إما نفس نفق SSH على عنوان Tailscale للسيرفر،
  أو `sudo tailscale serve --bg 8080` (الأمر بيتغيّر بين النسخ، شوف `tailscale serve --help`) فبتصير اللوحة بالشبكة الخاصة بس.

## 8. فعّل التايمرات

install.sh بيركّب التايمرات بس **ما بيفعّلها** (إلا مع `--enable-units`). افحص أولاً (القسم 10) وبعدين:

```bash
sudo systemctl enable --now laqta-nightly.timer laqta-backup.timer
systemctl list-timers 'laqta-*'
```

- التشغيل الليلي الساعة 02:00 بتوقيت السيرفر (`sudo timedatectl set-timezone Asia/Dubai`)، والنسخة الاحتياطية 01:30.
- عدد ساعات الليلة (مثل `-MaxHours`): `sudo systemctl edit laqta-nightly` وأضف `[Service]` ثم `Environment=NIGHTLY_MAX_HOURS=6`.
  فوق 9 ساعات ارفع `TimeoutStartSec` كمان. (قيمة `NIGHTLY_MAX_HOURS` بملف `.env` ما بتأثر هون: الوحدة بتمرّر `--max-hours` صريحة.) وقت التشغيل: `sudo systemctl edit laqta-nightly.timer`.
- تشغيل ليلة يدوياً (بيصرف على مزودات مدفوعة، والنشر التلقائي مطفي دايماً): `sudo systemctl start laqta-nightly.service`.
  السجل: `journalctl -u laqta-nightly -e` ومجلد `/opt/laqta/temp/nightly`. التقرير بيطلع بصفحة الصحة وبـ Telegram إذا مضبوط.
- عامل المزامنة (فقط مع Redis): `install.sh ... --with-redis --enable-units` أو `sudo systemctl enable --now laqta-sync-worker`.
  بدون Redis ما تفعّله: كتابة الشيت بتمرّ مباشرة عبر طابور MariaDB.

## 9. النسخ الاحتياطي لقاعدة البيانات

النسخة اليومية (14 يوم محفوظين بصيغة gzip) بتشتغل بـ root وبتقرا كلمة سر القاعدة من `/root/.my.cnf`، **مو من الريبو**. أنشئه مرة وحدة:

```bash
sudo install -m 600 /dev/null /root/.my.cnf
sudo nano /root/.my.cnf
```

```ini
[client]
user=laqta_app
password=ضع-كلمة-سر-القاعدة-هون
```

جرّب: `sudo /usr/local/sbin/laqta-backup && sudo ls -lh /var/backups/laqta`. الإعدادات (اختيارية) بـ `/etc/default/laqta-backup`:
`DB_NAME` و`BACKUP_DIR` و`KEEP_DAYS`. الاستعادة: `gunzip -c /var/backups/laqta/FILE.sql.gz | sudo mariadb automation_db`.

انتبه: الملف فيه جدول `system_settings` يعني **مفاتيح API**. المجلد صلاحياته 700. إذا نسخته لبره السيرفر شفّره، لأن النسخة على نفس القرص ما بتحميك من ضياع القرص.

## 10. فحص السيرفر

```bash
sudo -u laqta /opt/laqta/.venv/bin/python /opt/laqta/scripts/server_check.py
```

بيطبع ✅ (تمام) و⚠️ (نصيحة) و❌ (مشكلة) بالعربي، وبيطلع برمز غير صفر إذا في ❌. هو للقراءة فقط وما بيطبع قيم المفاتيح (أسماءها بس).
بيفحص: نسخة بايثون و`.venv`، مفاتيح `.env` المطلوبة، اتصال القاعدة والجداول، فتح الشيت بـ credentials (قراءة فقط)، Cloudinary،
طريقة إزالة الخلفية (وهل rembg/BiRefNet والموديل موجودين)، مساحة القرص، التايمرات والخدمات، وإن nginx عليه كلمة سر وHTTPS.
خيارات: `--skip-sheet` (بدون اتصال بـ Google)، `--only env`، `--only db`، `--no-systemd`. شغّله بعد كل تحديث.
فحص مزودات البحث المدفوعة بشكل منفصل: `sudo -u laqta /opt/laqta/.venv/bin/python /opt/laqta/scripts/smoke_live.py --probe`.

## 11. التحديث لنسخة جديدة

```bash
sudo systemctl start laqta-backup.service                 # نسخة احتياطية قبل التحديث
systemctl is-active laqta-nightly.service                 # إذا "active" استنى تخلص الليلة
cd /opt/laqta
sudo -u laqta git fetch origin && sudo -u laqta git log --oneline HEAD..origin/main    # شو الجديد
sudo -u laqta git pull --ff-only
sudo bash deploy/ubuntu/install.sh /opt/laqta --server-name dash.example.com           # نفس خيارات أول مرة
sudo -u laqta /opt/laqta/.venv/bin/python scripts/server_check.py
```

- `install.sh` بيحدّث المكتبات و`composer` والـ cache وترقيات الـ schema (idempotent) والوحدات، وبيعمل `reload` لـ php-fpm وnginx.
  ما بتحتاج `LAQTA_DB_PASSWORD` بالتحديث (الترقية بتستعمل بيانات `.env`).
- **لا تعمل `restart` لـ php-fpm** والعامل شغّال من اللوحة: بيقتله. `install.sh` بيستعمل `reload`.
- عامل المزامنة (إذا شغّال) بيتعمله restart لحاله. الليلة الجاية بتاخد الكود الجديد؛ وما بنعيد تشغيل ليلة جارية.
- إذا غيّرت `dashboard/.env`: `sudo -u laqta php8.3 /opt/laqta/dashboard/artisan config:cache`.

## 12. الرجوع لنسخة قديمة (rollback)

```bash
cd /opt/laqta
sudo -u laqta git log --oneline -10                       # اعرف النسخة اللي كانت تشتغل
sudo -u laqta git reset --hard <الكوميت-القديم>
sudo bash deploy/ubuntu/install.sh /opt/laqta --server-name dash.example.com
sudo -u laqta /opt/laqta/.venv/bin/python scripts/server_check.py
```

- ترقيات القاعدة بتضيف أعمدة وجداول بس (`IF NOT EXISTS`)، فالكود القديم غالباً بيشتغل مع قاعدة جديدة. إذا لأ، استعد نسخة ما قبل التحديث
  (القسم 9). **تحذير:** الاستعادة بتمسح كل شي انعمل بعد النسخة (اعتمادات المراجعين مثلاً).
  قبل الاستعادة: `sudo systemctl stop laqta-nightly.timer laqta-sync-worker`.
- للرجوع للأحدث: `sudo -u laqta git checkout main && sudo -u laqta git pull --ff-only` وبعدها install.sh.

## 13. لما يصير خطأ

| العَرَض | شو تفحص |
|---|---|
| 502 Bad Gateway | `systemctl status php8.3-fpm` وإن `/run/php/laqta.sock` موجود |
| 401 وكلمة السر ما بتفتح | `sudo htpasswd -B /etc/nginx/laqta.htpasswd admin` أو `--reset-auth` |
| 500 بصفحة اللوحة | `/opt/laqta/dashboard/storage/logs/laravel.log`، و`server_check.py --only env` |
| "Invalid JSON output from Python bridge" | شغّل الجسر بإيدك بمستخدم `laqta` وشوف الخطأ، وفحص `server_check.py --only python` |
| الليلة ما اشتغلت | `systemctl list-timers 'laqta-*'` و`journalctl -u laqta-nightly -e` |
| القرص عم يمتلي | `df -h` ومجلد `/var/backups/laqta` و`/opt/laqta/temp` |
| rembg ما بيشتغل | `server_check.py --only bg` وإن `/var/lib/laqta/models` فيه ملف `.onnx` |

سجلات مفيدة: `journalctl -u nginx -e`، `/var/log/nginx/laqta.error.log`، `journalctl -u laqta-sync-worker -e`.
