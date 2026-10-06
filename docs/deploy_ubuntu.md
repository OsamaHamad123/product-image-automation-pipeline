# نشر النظام على سيرفر Ubuntu

هاد الدليل بيشرح كيف تنقل النظام من جهاز ويندوز لسيرفر Ubuntu (22.04 أو 24.04)، وكيف تحدّثه وترجع لنسخة قديمة.
كل الأوامر بتنفّذها على السيرفر إلا إذا انكتب غير هيك. الأوامر والأسماء بتضل بالإنجليزي.

## شو بينركّب

- **اللوحة** (Laravel): nginx + php-fpm، بمجلد `/opt/laqta/dashboard`.
- **بايثون**: بيئة `.venv` داخل `/opt/laqta`. اللوحة بتلاقيها لحالها (`PythonBridge` بيدوّر على `.venv/bin/python`)، ما في شي تضبطه.
- **قاعدة البيانات**: MariaDB على نفس السيرفر (مو مفتوحة للشبكة).
- **خدمات systemd**:
  - `laqta-nightly.timer`: التشغيل الليلي (مثل `schedule_nightly.ps1`).
  - `laqta-backup.timer`: نسخة احتياطية يومية (مشفّرة إذا ضبطت المفتاح).
  - `laqta-outbox-flush.timer`: كل دقيقتين بيفرّغ طابور الكتابة للشيت (القسم 8).
  - `laqta-run.path`: بيخلّي التشغيل اللي بتبدأه من اللوحة يضل شغّال حتى لو أعدت تشغيل php-fpm (القسم 8).
  - `laqta-alert@.service`: بيبعتلك رسالة Telegram إذا فشلت خدمة (القسم 8).
  - `laqta-sync-worker.service`: فقط إذا بتستعمل Redis (نفس منطق `start_all.bat`).
- **سجلات**: `logrotate` بيدوّر السجلات لحاله (القسم 12). **مراقبة**: عنوان `/healthz` لبرنامج مراقبة خارجي (القسم 11).
- كل شي بيشتغل بمستخدم نظام اسمه `laqta` (مو root). إزالة الخلفية بـ BiRefNet اختيارية (CPU افتراضياً، وGPU لو متوفر).

> **تحذير أمني:** اللوحة ما إلها تسجيل دخول خاص فيها. أي حدا بيوصل إلها بيقدر يعتمد صور وينشر ويكتب بالشيت.
> لهيك nginx **بيطلب كلمة سر (basic auth)** على كل صفحة، وما بيفتح اللوحة غير على **HTTPS** أو على **localhost** (نفق SSH أو Tailscale).
> `install.sh` **بيرفض يكمّل** إذا ما قلتله أي طريقة من الاثنتين: إما `--domain` أو `--local-only` (القسم 4). ما في طريقة لفتح اللوحة على HTTP عادي.

## 1. المتطلبات

| الشي | التفاصيل |
|---|---|
| السيرفر | Ubuntu 22.04 أو 24.04، وصول `sudo`، ورام 2GB على الأقل |
| الرام مع BiRefNet | الموديل الكامل بدو حوالي 6GB رام، و`--lite` حوالي 4GB. هي أرقام تقريبية للتخطيط مو قياس: شوف القسم 4 |
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

إذا نسخت `dashboard/.env` من جهازك، **الأحسن تبدأ من القالب الجاهز للسيرفر** بدل ملف ويندوز (القالب ما فيه أي سر): انسخه وعبّي الفراغات.

```bash
sudo install -m 600 /opt/laqta/deploy/ubuntu/dashboard.env.example /opt/laqta/dashboard/.env
sudo nano /opt/laqta/dashboard/.env
```

إذا نسخت الملفات بطريقة ثانية تأكد إنهم للمالك بس: `sudo chmod 600 /opt/laqta/.env /opt/laqta/dashboard/.env /opt/laqta/credentials.json`
(install.sh وserver_check.py بيتأكدوا من هالصلاحيات كمان).

عدّل القيم بالنسخة اللي على السيرفر (`sudo nano /opt/laqta/.env`):

- `.env` و`dashboard/.env`، نفس قيم القاعدة بالاثنين: `DB_HOST=127.0.0.1`، `DB_DATABASE=automation_db`، `DB_USERNAME=laqta_app`،
  و`DB_PASSWORD` هي نفس كلمة السر اللي رح تعطيها لـ install.sh.
- `dashboard/.env` (القالب فيه كل هدول جاهزين): `APP_ENV=production`، `APP_DEBUG=false` (مهم: `true` بيعرض المفاتيح بصفحة الخطأ)،
  `APP_URL=https://dash.example.com`، `SESSION_DRIVER=file`، `CACHE_STORE=file`، `LOG_STACK=daily` (ملف سجل لكل يوم، القسم 12)،
  و`SESSION_SECURE_COOKIE=true` (على HTTPS بس؛ مع `--local-only` على `http://127.0.0.1:8080` احذف هالسطر). اترك `APP_KEY=` فاضي: install.sh بيولّده.
- `.env`: مفاتيح البحث والتحقق وCloudinary و`SPREADSHEET_NAME_OR_URL`، وطريقة إزالة الخلفية `BG_REMOVAL_METHOD`.

**install.sh بيرفض يكمّل** إذا لقى `APP_DEBUG=true` أو `APP_ENV` غير `production` (أو `SESSION_SECURE_COOKIE` مو `true` على HTTPS). بيوقف قبل ما يغيّر أي شي ويقلك أي سطر تعدّله، وبعدين بتعيد نفس الأمر.
وهو بنفسه بيثبّت `APP_DEBUG=false` و`APP_ENV=production` بالإعدادات المخزّنة (`config:cache`) حتى لو السطر ناقص من الملف.

**دوّر (غيّر) أي مفتاح انحفظ يوم بالـ git**، حتى لو انحذف بعدين، لأن التاريخ بيضل محفوظ. للتأكد:

```bash
git -C /opt/laqta log --all --full-history --oneline -- .env credentials.json
```

إذا طلع شي: أنشئ مفتاح جديد لحساب خدمة Google (وامسح القديم من Google Cloud) وبدّل `credentials.json`، وغيّر كمان Cloudinary API secret
ومفاتيح Serper وGemini وPhotoRoom وAnthropic وتوكن Telegram. ما تبعت المفاتيح بإيميل أو شات.
**install.sh ما بيعمل `.env` ولا بيعدّله** (الاستثناء الوحيد: `artisan key:generate` بيكتب `APP_KEY` بـ `dashboard/.env` إذا كان فاضي).

## 4. شغّل install.sh

لازم تختار **طريقة وصول وحدة من اتنتين** (بدونها بيوقف برسالة بتقلك شو تكتب):

- **A. موقع عام على HTTPS**: `--domain dash.example.com` (لازم دومين، القسم 6).
- **B. خاص بدون أي منفذ عام**: `--local-only` (نفق SSH أو Tailscale، القسم 7).

أول مرة، الطريقة A (كلمة سر القاعدة بتنقرا بدون ما تنحفظ بسجل الأوامر):

```bash
read -rsp 'كلمة سر قاعدة البيانات (12 حرف أو أكثر): ' LAQTA_DB_PASSWORD; echo; export LAQTA_DB_PASSWORD
sudo --preserve-env=LAQTA_DB_PASSWORD bash /opt/laqta/deploy/ubuntu/install.sh /opt/laqta --domain dash.example.com --with-fail2ban
```

والطريقة B:

```bash
sudo --preserve-env=LAQTA_DB_PASSWORD bash /opt/laqta/deploy/ubuntu/install.sh /opt/laqta --local-only
```

بيسألك مرة وحدة عن كلمة سر اللوحة (القسم 5). بإمكانك تعيد تشغيله كل ما بدك: كل خطوة بتتأكد قبل ما تغيّر. جرّب أولاً بدون تغيير: `--dry-run`.

| الخيار | المعنى |
|---|---|
| `APP_DIR` (أول وسيط) | مجلد الكلون، الافتراضي المجلد اللي فيه السكربت |
| `--domain HOST` | اسم الدومين: nginx بيخدم `HOST` على 443 بشهادة certbot (القسم 6). الاسم القديم `--server-name` ما زال شغّال |
| `--local-only` | nginx بيسمع على `127.0.0.1:8080` بس (القسم 7) |
| `--monitor-ip IP` | عنوان برنامج المراقبة اللي بيقدر يفتح `/healthz` بدون كلمة سر (ممكن تكرره، أو `IP/24`، أو قائمة بفواصل؛ القسم 11) |
| `--with-fail2ban` | بيركّب fail2ban وبيحظر العنوان اللي بيغلط بكلمة سر اللوحة 5 مرات (للموقع العام بس، القسم 6) |
| `--with-rclone` | بيركّب rclone (للنسخة الاحتياطية البعيدة، القسم 9) |
| `--with-birefnet` | بيركّب `rembg[cpu]` وبينزّل موديل `birefnet-general` |
| `--with-birefnet --lite` | نفس الشي بس الموديل الأصغر `birefnet-general-lite` (للرام القليلة) |
| `--with-birefnet --gpu` | `rembg[gpu]` (بدك كرت NVIDIA ودرايفر ومكتبات CUDA مناسبة) |
| `--with-redis` | بينصّب redis-server (فقط إذا بتستعمل Redis) |
| `--enable-units` | بيفعّل التايمرات (والـ sync worker مع `--with-redis`) |
| `--reset-auth`, `--auth-user NAME` | كلمة سر جديدة للوحة، واسم المستخدم (الافتراضي `admin`) |
| `--skip-apt`, `--php-version X.Y`, `--user NAME` | تخطّي apt، نسخة PHP (الافتراضي 8.3)، مستخدم النظام (الافتراضي `laqta`) |

متغيرات بيئة (بتكتبها قبل الأمر، ومع `sudo --preserve-env=الاسم`):

| المتغير | المعنى |
|---|---|
| `LAQTA_DB_PASSWORD` | كلمة سر القاعدة، 12 حرف أو أكثر (أول مرة بس) |
| `LAQTA_MEMORY_MAX` | سقف ذاكرة التشغيل الليلي (مثل `3G` أو `70%`). الافتراضي 70% من رام السيرفر (80% مع BiRefNet) وما بينزل عن 1GB |
| `LAQTA_RAM_MB` | حجم الرام يدوياً إذا الحاوية بتقرا رام الجهاز الأم |
| `LAQTA_MONITOR_IPS` | نفس `--monitor-ip` |
| `LAQTA_DB_NAME`, `LAQTA_DB_USER` | اسم القاعدة والمستخدم |

اللي بيسويه بالترتيب: حزم apt (بما فيها `libgl1` و`libglib2.0-0` للصور و`age` و`logrotate`؛ على 22.04 بيضيف PPA لـ PHP 8.3 لأن اللوحة بدها PHP 8.2+)،
المستخدم والمجلدات، `.venv` و`pip install -r requirements.txt`، `composer install --no-dev`، `key:generate` إذا `APP_KEY` فاضي، `config:cache` و`route:cache`،
صلاحيات `storage`، قاعدة البيانات والمستخدم (من `LAQTA_DB_NAME` و`LAQTA_DB_USER` و`LAQTA_DB_PASSWORD`) وبعدها `init_db()` (الـ schema)،
وحدات systemd (وبيفعّل `laqta-run.path` دايماً)، `logrotate`، pool خاص بـ php-fpm بيشتغل بمستخدم `laqta`، وموقع nginx. بالآخر بيطبع **أسماء** المفاتيح الناقصة من `.env.example` (بدون قيمها).
إذا خلّص برمز 2 معناها في شي لازم تنتبه إله (مكتوب بالآخر).

**BiRefNet والذاكرة:** بعد `--with-birefnet` خلّي `BG_REMOVAL_METHOD=rembg` بـ `.env` (أو من صفحة الإعدادات). الموديلات بتنحفظ بـ `/var/lib/laqta/models`.
على CPU هو أبطأ بكثير من PhotoRoom، وبياخد رام كبيرة: **حوالي 6GB للموديل الكامل وحوالي 4GB لـ `--lite`** (أرقام تقريبية للتخطيط، مو قياس؛ تحمّل أول مرة بياخد أكتر).
`install.sh` بيحذّرك إذا رام السيرفر أقل. إذا الرام ما بتكفي: استعمل `--lite`، أو ضيف رام/swap، أو خلّي PhotoRoom. لتشوف إذا ليلة انقتلت لقلة الذاكرة: `journalctl -u laqta-nightly -e` ودوّر على كلمة oom.

## 5. مستخدم كلمة سر اللوحة (basic auth)

- install.sh بيعمل الملف `/etc/nginx/laqta.htpasswd` وبيطلب منك كلمة السر بالكتابة (ما بتنكتب بأي ملف بالريبو). بدون الملف الموقع ما بيتفعّل.
- مستخدم إضافي: `sudo htpasswd -B /etc/nginx/laqta.htpasswd ahmad`
- تغيير كلمة سر: `sudo htpasswd -B /etc/nginx/laqta.htpasswd admin` (أو `install.sh ... --reset-auth`)
- حذف مستخدم: `sudo htpasswd -D /etc/nginx/laqta.htpasswd ahmad`

## 6. HTTPS بـ certbot (الطريقة A)

1. سجل A للدومين لازم يشير للسيرفر. افتح المنافذ: `sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full' && sudo ufw enable`.
2. شغّل install.sh مع `--domain dash.example.com` (القسم 4). **قبل ما تطلع الشهادة اللوحة مو مفتوحة من برّا أبداً**: بتسمع على `127.0.0.1:8080` بس، والمنفذ 80 بيرد على تحقق certbot لحاله وما بيعرض لوحة ولا كلمة سر.
3. أصدر الشهادة (مسار التحقق مفتوح بدون كلمة سر، والباقي محمي):

```bash
sudo certbot certonly --webroot -w /var/www/letsencrypt -d dash.example.com -m you@example.com --agree-tos --deploy-hook "systemctl reload nginx"
```

4. أعد تشغيل install.sh **بنفس الخيارات**: لما بلاقي الشهادة بيفتح HTTPS على 443 وبيحوّل من 80 إلى 443 بنفسه (certbot ما بيعدّل ملف nginx).
5. اختبر: `curl -I https://dash.example.com/` لازم يرجع `401` بدون كلمة سر، و`curl -I http://dash.example.com/` لازم يحوّلك (`301`). التجديد تلقائي؛ جرّبه: `sudo certbot renew --dry-run`.

**تحديد الطلبات:** nginx بيحدّ طلبات كل عنوان: 10 بالثانية (مع دفعة 60 لصفحة بتحمّل صور كتير)، وطلبات بدون كلمة سر أصلاً حد أقل بكتير. إذا زاد العنوان عن الحد بيرجعله `429` لحظات وبيرجع يشتغل. العادي ما بتحسّ فيه.

**fail2ban (اختياري، `--with-fail2ban`):** بيقرا سجل أخطاء nginx (`/var/log/nginx/laqta.error.log`) وإذا عنوان غلط بكلمة السر 5 مرات خلال 10 دقايق بيحظره ساعة (والحظر الثاني لنفس العنوان أطول). الطلب بدون كلمة سر (المتصفح قبل ما يسألك) ما بيتعدّ غلط.

```bash
sudo fail2ban-client status laqta-nginx-auth                   # مين محظور
sudo fail2ban-client set laqta-nginx-auth unbanip 203.0.113.9  # فكّ حظر عنوان (مثلاً عنوانك إذا غلطت)
```

## 7. بدون إنترنت عام: localhost + نفق SSH أو Tailscale (الطريقة B)

شغّل `install.sh /opt/laqta --local-only`. nginx بيسمع على `127.0.0.1:8080` بس، وكلمة السر بتضل مطلوبة. ما تفتح منافذ 80/443. (ما بينفع معه `--with-fail2ban`: كل الزوار بيجوا من `127.0.0.1`.)

- **نفق SSH** (من جهازك): `ssh -L 8080:127.0.0.1:8080 user@SERVER_IP` وخلّي النافذة مفتوحة، وافتح `http://127.0.0.1:8080/`.
- **Tailscale**: ركّب Tailscale على السيرفر وعلى جهازك بنفس الحساب، وبعدها إما نفس نفق SSH على عنوان Tailscale للسيرفر،
  أو `sudo tailscale serve --bg 8080` (الأمر بيتغيّر بين النسخ، شوف `tailscale serve --help`) فبتصير اللوحة بالشبكة الخاصة بس.
- على `http://127.0.0.1:8080` احذف `SESSION_SECURE_COOKIE=true` من `dashboard/.env` (الكوكي الآمن بدو HTTPS)، وبعدها `sudo -u laqta php8.3 /opt/laqta/dashboard/artisan config:cache`.

## 8. فعّل التايمرات، والخدمات التانية

install.sh بيركّب التايمرات بس **ما بيفعّلها** (إلا مع `--enable-units`). افحص أولاً (القسم 10) وبعدين:

```bash
sudo systemctl enable --now laqta-nightly.timer laqta-backup.timer laqta-outbox-flush.timer
systemctl list-timers 'laqta-*'
```

**التشغيل الليلي والنسخة:**

- التشغيل الليلي الساعة 02:00 بتوقيت السيرفر (`sudo timedatectl set-timezone Asia/Dubai`)، والنسخة الاحتياطية 01:30.
- عدد ساعات الليلة (مثل `-MaxHours`): `sudo systemctl edit laqta-nightly` وأضف `[Service]` ثم `Environment=NIGHTLY_MAX_HOURS=6`.
  فوق 9 ساعات ارفع `TimeoutStartSec` كمان. (قيمة `NIGHTLY_MAX_HOURS` بملف `.env` ما بتأثر هون: الوحدة بتمرّر `--max-hours` صريحة.) وقت التشغيل: `sudo systemctl edit laqta-nightly.timer`.
- تشغيل ليلة يدوياً (بيصرف على مزودات مدفوعة، والنشر التلقائي مطفي دايماً): `sudo systemctl start laqta-nightly.service`.
  السجل: `journalctl -u laqta-nightly -e` ومجلد `/opt/laqta/temp/nightly`. التقرير بيطلع بصفحة الصحة وبـ Telegram إذا مضبوط.

**سقف الذاكرة:** كل وحدة بايثون (`laqta-nightly` و`laqta-run` و`laqta-outbox-flush` و`laqta-sync-worker`) إلها `MemoryMax` بيحسبه install.sh من رام السيرفر.
إذا الوحدة تعدّته بتنقتل **هي لحالها** (`OOMPolicy=kill`) ومش MariaDB ولا nginx، وبتوصلك رسالة Telegram. شوفه وغيّره:

```bash
systemctl show laqta-nightly -p MemoryMax
sudo systemctl edit laqta-nightly     # أضف:  [Service]  ثم  MemoryMax=3G
sudo systemctl edit laqta-run         # نفس الشي للتشغيل من اللوحة
```

أو من أول، وقت تشغيل install.sh:

```bash
export LAQTA_MEMORY_MAX=3G
sudo --preserve-env=LAQTA_MEMORY_MAX bash /opt/laqta/deploy/ubuntu/install.sh /opt/laqta --domain dash.example.com
```

**الإيقاف بلطف:** `systemctl stop` (أو إعادة تشغيل السيرفر) بيبعت SIGTERM، والتشغيل بيخلّص المنتج اللي بإيده (لحد 45 ثانية) وبيكتب التقرير ورسالة Telegram وبيطلع برمز 3 («وقف عن قصد»).
systemd بيستنى 120 ثانية (`TimeoutStopSec=120`) قبل ما يقتله، ورمز 3 ما بيُحسب فشل فما بتوصلك رسالة «خدمة فشلت» على إيقاف عادي.

**تفريغ طابور الشيت بين التشغيلات (`laqta-outbox-flush.timer`):** بدون Redis ما في شي بيفرّغ طابور الكتابة للشيت بين تشغيلتين، فاعتماد صورة وقت انقطاع Google كان بيستنى للّيلة الجاية.
هلق كل دقيقتين بيشتغل `scripts/flush_sheets_sync.py`: ما بيفتح Google إلا إذا في كتابة مستحقة، وما بيتداخل مع نفسه ولا مع التشغيل الليلي (نفس القفل).
وإذا زاد عدد الكتابات اللي فشلت نهائياً (`DEAD`، يعني الرابط ما وصل للشيت بعد كل المحاولات) بيبعتلك **رسالة Telegram وحدة لكل زيادة** (أرقام بس، بدون أسماء منتجات). السجل: `journalctl -u laqta-outbox-flush -e`.
تشغيلة جديدة بتعيد كتابة رابط الصف الميت. لتعيد عدّ التنبيهات من الصفر: `sudo rm /opt/laqta/temp/outbox_dead_state.json`.

**التشغيل من اللوحة بيضل شغّال (`laqta-run.path`):** لما بتضغط «شغّل» اللوحة بتكتب ملف طلب `temp/run_request.json` وبتشغّله خدمة systemd `laqta-run.service` بمستخدم `laqta` (نفس الأمرين: `--enqueue` ثم `--worker`).
قبل هيك php-fpm كان بيشغّله جواته، فإعادة تشغيل أو ترقية php-fpm بدون إشراف كانت تقتل التشغيل. هلق ما بتأثر. زر الإيقاف والحالة بيشتغلوا متل قبل.
إذا الخدمة ما ردّت خلال 10 ثواني اللوحة بتسحب الطلب وبتشغّله بالطريقة القديمة (ما بيصير تشغيل مرتين). افحص:

```bash
systemctl status laqta-run.path             # لازم active (waiting)
journalctl -u laqta-run -e                  # آخر تشغيل من اللوحة
sudo systemctl stop laqta-run               # إيقاف تشغيل اللوحة من الشل (بديل زر الإيقاف)
```

**تنبيهات الفشل (`laqta-alert@.service`):** إذا فشلت وحدة التشغيل الليلي أو عامل المزامنة أو النسخة الاحتياطية بتوصلك رسالة Telegram قصيرة فيها اسم الوحدة واسم السيرفر وسبب systemd (مثل «انقتلت لأن الذاكرة خلصت»)، بدون أي مفتاح.
لازم `TELEGRAM_BOT_TOKEN` و`TELEGRAM_CHAT_ID` بـ `.env`. جرّبها: `sudo systemctl start laqta-alert@laqta-backup.service`.

- عامل المزامنة (فقط مع Redis): `install.sh ... --with-redis --enable-units` أو `sudo systemctl enable --now laqta-sync-worker`.
  بدون Redis ما تفعّله: كتابة الشيت بتمرّ عبر طابور MariaDB، وتفريغها كل دقيقتين من `laqta-outbox-flush`.

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
`DB_NAME` و`BACKUP_DIR` و`KEEP_DAYS` و`BACKUP_AGE_RECIPIENT` و`BACKUP_RCLONE_REMOTE`.

انتبه: الملف فيه جدول `system_settings` يعني **مفاتيح API**. المجلد صلاحياته 700. **بدون تشفير السكربت بيطبع تحذير كبير بكل مرة** (`BACKUP_AGE_RECIPIENT is not set`). النسخة على نفس القرص ما بتحميك من ضياع القرص، فالأحسن تشفّر وتنسخ لبرّا.

### تشفير النسخة بـ age (مفتاح عام على السيرفر، والمفتاح الخاص عندك بس)

1. على **جهازك أنت** (مو السيرفر) ركّب age (`sudo apt install age` على لينكس، `brew install age` على ماك) وسوّي مفتاح:

```bash
age-keygen -o laqta-backup.key
# بيطبع:  Public key: age1xxxxxxxx...   <- هاد المفتاح العام
```

2. احفظ ملف `laqta-backup.key` **بعيد عن السيرفر** (مدير كلمات السر أو فلاشة). بدونه ما في استعادة. **لا ترفعه للسيرفر ولا للريبو.**
3. على السيرفر حط المفتاح **العام** بس:

```bash
sudo nano /etc/default/laqta-backup      # أضف سطر:  BACKUP_AGE_RECIPIENT=age1xxxxxxxx...
sudo /usr/local/sbin/laqta-backup && sudo ls -lh /var/backups/laqta
```

لازم يطلع ملف `automation_db_..._.sql.gz.age` وما يطلع التحذير. (إذا حطيت مفتاح غلط، أو المفتاح الخاص `AGE-SECRET-KEY-...` بالغلط، السكربت بيوقف قبل ما ينسخ أي شي.) أكتر من شخص: حط مسار ملف فيه مفاتيحهم عامة سطر لكل واحد: `BACKUP_AGE_RECIPIENT=/etc/laqta-recipients.txt`.

4. **الاستعادة** (الملف المشفّر بتنزله لجهازك أو بتحط المفتاح الخاص مؤقتاً وبتمسحه):

```bash
age -d -i laqta-backup.key automation_db_2026-10-06_013000.sql.gz.age | gunzip | sudo mariadb automation_db
```

### نسخة بعيدة بـ rclone (اختياري)

```bash
sudo apt-get install -y rclone                     # أو شغّل install.sh مع --with-rclone
sudo rclone config                                 # سوّي remote (Backblaze B2 أو Google Drive أو S3...) واحفظ اسمه
sudo nano /etc/default/laqta-backup                # أضف:  BACKUP_RCLONE_REMOTE=b2:laqta-backups/db
sudo /usr/local/sbin/laqta-backup                  # جرّب
```

- الاعدادات بتنحفظ بإعدادات root (`/root/.config/rclone/rclone.conf`) لأن النسخة بتشتغل بـ root.
- **ما بيرفع نسخة غير مشفّرة**: إذا `BACKUP_AGE_RECIPIENT` مو مضبوط بيرفض الرفع (وبيرجع خطأ) وبتضل النسخة المحلية. (بس لو بدك فعلاً: `BACKUP_RCLONE_ALLOW_PLAINTEXT=1`، مو منصوح.)
- إذا فشل الرفع بتفشل الوحدة وبتوصلك رسالة Telegram، بس النسخة المحلية وتنظيف القديم بيتمّوا قبل.
- الحذف القديم عالبعيد: حط قاعدة انتهاء (lifecycle) على الـ bucket نفسه. السكربت بيحذف بس من مجلد السيرفر.

## 10. فحص السيرفر

```bash
sudo -u laqta /opt/laqta/.venv/bin/python /opt/laqta/scripts/server_check.py
```

بيطبع ✅ (تمام) و⚠️ (نصيحة) و❌ (مشكلة) بالعربي، وبيطلع برمز غير صفر إذا في ❌. هو للقراءة فقط وما بيطبع قيم المفاتيح (أسماءها بس).
بيفحص: نسخة بايثون و`.venv`، مفاتيح `.env` المطلوبة، `APP_DEBUG` بالملف **وبالإعدادات المخزّنة** (❌ إذا `true`)، اتصال القاعدة والجداول، فتح الشيت بـ credentials (قراءة فقط)، Cloudinary،
طريقة إزالة الخلفية (وهل rembg/BiRefNet والموديل موجودين)، مساحة القرص، التايمرات (بما فيها تفريغ الشيت) و`laqta-run.path` والخدمات، وإن nginx عليه كلمة سر وHTTPS وكوكي الجلسة Secure.
خيارات: `--skip-sheet` (بدون اتصال بـ Google)، `--only env`، `--only db`، `--no-systemd`. شغّله بعد كل تحديث.
فحص مزودات البحث المدفوعة بشكل منفصل: `sudo -u laqta /opt/laqta/.venv/bin/python /opt/laqta/scripts/smoke_live.py --probe`.

## 11. مراقبة من برّا: `/healthz`

nginx بيعرض `/healthz`: JSON قصير بدون أي سر ولا كلمة سر (اللي بيقدر يفتحه بس هالسيرفر نفسه والعناوين اللي حطيتها بـ `--monitor-ip`). **200** إذا كل شي سليم، **503** إذا لا:

```bash
curl -s --resolve dash.example.com:443:127.0.0.1 https://dash.example.com/healthz     # من السيرفر نفسه (موقع عام)
curl -s http://127.0.0.1:8080/healthz                                                  # مع --local-only
```

```json
{"ok":true,"checks":{"database":{"ok":true},
 "nightly":{"ok":true,"status":"ok","age_hours":5.1,"max_hours":26},
 "outbox":{"ok":true,"dead":0,"dead_recent":0,"window_days":7},
 "disk":{"ok":true,"free_gb":41.2,"min_gb":2}},"checked_at":"2026-10-06T08:00:00+00:00"}
```

| الفحص | سليم لما | إذا أحمر |
|---|---|---|
| `database` | القاعدة بتردّ | `systemctl status mariadb` |
| `nightly` | آخر ليلة ناجحة بدأت من أقل من 26 ساعة (خلصت، أو وصلت حدّ ساعاتها أو ميزانيتها). سيرفر جديد بلا ليالي = `never`، مو عطل | `systemctl list-timers 'laqta-*'` و`journalctl -u laqta-nightly -e` |
| `outbox` | ما في كتابات شيت ميتة (`DEAD`) من آخر 7 أيام (الأقدم بتظهر بـ `dead` وما بتخلّيه أحمر للأبد) | صفحة الصحة، وتشغيلة جديدة بتعيد كتابة الرابط |
| `disk` | 2GB فاضية أو أكتر | `df -h`، `/var/backups/laqta`، `/opt/laqta/temp` |

**UptimeRobot:** Add New Monitor ← النوع **HTTP(s)** ← الرابط `https://dash.example.com/healthz` ← الفترة 5 دقايق. هو بيعتبر أي رد غير 2xx عطل، فـ 503 بيطلعلك تنبيه.
وبيطلب إنو عناوينه تكون مسموحة: بينشر قائمة عناوين المراقبة بصفحة المساعدة، حطّها كلها `--monitor-ip` (أو بقائمة بفواصل بـ `LAQTA_MONITOR_IPS`) وأعد تشغيل install.sh بنفس خياراتك.

**Uptime Kuma** (على سيرفر ثاني عندك): Add New Monitor ← **HTTP(s)** ← الرابط نفسه ← Accepted Status Codes `200-299`. عنوان سيرفر Kuma هو الوحيد اللي بتحطه بـ `--monitor-ip`، فهاد الأسهل.
ممكن كمان **Keyword**: الكلمة `"ok":true`.

أي مراقب بيجي من عنوان مو بالقائمة بيرجعله `403`. هي القاعدة: ما في كلمة سر على هالمسار، فالعنوان هو الحماية.

**ملخص التنبيهات اللي بتوصلك على Telegram:**

| الحدث | من وين |
|---|---|
| فشلت وحدة (ليلة، عامل مزامنة، نسخة احتياطية، رفع بعيد) | `laqta-alert@` |
| زادت كتابات الشيت الميتة `DEAD` | `laqta-outbox-flush` (مرة لكل زيادة) |
| تقرير كل تشغيل (خلص، توقف، انقطاع) | `run_report` (متل قبل) |
| السيرفر أو الليلة أو القرص مو سليم | برنامج المراقبة اللي حطيته على `/healthz` |

## 12. السجلات

| الملف | شو فيه | التدوير |
|---|---|---|
| `dashboard/storage/logs/laravel-YYYY-MM-DD.log` | أخطاء اللوحة (ملف لكل يوم مع `LOG_STACK=daily`) | لارافيل بيحذف القديم لحاله بعد `LOG_DAILY_DAYS` (14) |
| `dashboard/storage/logs/laravel.log` | أخطاء بايثون اللي بتنكتب لصفحة الأخطاء | `logrotate` أسبوعياً أو فوق 50MB، بيحتفظ بـ 8 نسخ |
| `temp/pipeline.log` | سجل التشغيل من اللوحة | اللوحة بتحتفظ بآخر 5 تشغيلات (`pipeline.log.1` .. `.5`) بدل ما تمسحه، و`logrotate` بيقصّه إذا كبر فوق 50MB |
| `temp/nightly/nightly_التاريخ.log` | سجل كل ليلة | `logrotate` بيحذف الليلة بعد 60 يوم (ما بيقصّها ولا بيضغطها: صفحة السجل بتقراها كما هي) |

صفحة الصحة بتعرض أحدث ملف بين `laravel.log` وملفات اليوم. جرّب قاعدة التدوير بدون تنفيذ: `sudo logrotate -d /etc/logrotate.d/laqta`.

## 13. التحديث لنسخة جديدة

```bash
sudo systemctl start laqta-backup.service                 # نسخة احتياطية قبل التحديث
systemctl is-active laqta-nightly.service                 # إذا "active" استنى تخلص الليلة
cd /opt/laqta
sudo -u laqta git fetch origin && sudo -u laqta git log --oneline HEAD..origin/main    # شو الجديد
sudo -u laqta git pull --ff-only
sudo bash deploy/ubuntu/install.sh /opt/laqta --domain dash.example.com                # نفس خيارات أول مرة
sudo -u laqta /opt/laqta/.venv/bin/python scripts/server_check.py
```

- `install.sh` بيحدّث المكتبات و`composer` والـ cache وترقيات الـ schema (idempotent) والوحدات، وبيعمل `reload` لـ php-fpm وnginx.
  ما بتحتاج `LAQTA_DB_PASSWORD` بالتحديث (الترقية بتستعمل بيانات `.env`). لازم تعطيه `--domain` (أو `--local-only`) كل مرة.
- **لا تعمل `restart` لـ php-fpm** والعامل شغّال من اللوحة بدون `laqta-run.path`: بيقتله. `install.sh` بيستعمل `reload`.
- عامل المزامنة (إذا شغّال) بيتعمله restart لحاله. الليلة الجاية بتاخد الكود الجديد؛ وما بنعيد تشغيل ليلة جارية.
- إذا غيّرت `dashboard/.env`: `sudo -u laqta php8.3 /opt/laqta/dashboard/artisan config:cache`.

## 14. الرجوع لنسخة قديمة (rollback)

```bash
cd /opt/laqta
sudo -u laqta git log --oneline -10                       # اعرف النسخة اللي كانت تشتغل
sudo -u laqta git reset --hard <الكوميت-القديم>
sudo bash deploy/ubuntu/install.sh /opt/laqta --domain dash.example.com
sudo -u laqta /opt/laqta/.venv/bin/python scripts/server_check.py
```

- ترقيات القاعدة بتضيف أعمدة وجداول بس (`IF NOT EXISTS`)، فالكود القديم غالباً بيشتغل مع قاعدة جديدة. إذا لأ، استعد نسخة ما قبل التحديث
  (القسم 9). **تحذير:** الاستعادة بتمسح كل شي انعمل بعد النسخة (اعتمادات المراجعين مثلاً).
  قبل الاستعادة: `sudo systemctl stop laqta-nightly.timer laqta-outbox-flush.timer laqta-sync-worker`.
- للرجوع للأحدث: `sudo -u laqta git checkout main && sudo -u laqta git pull --ff-only` وبعدها install.sh.

## 15. لما يصير خطأ

| العَرَض | شو تفحص |
|---|---|
| install.sh بيقول «refusing to install» | ما حطيت `--domain` ولا `--local-only` (القسم 4) |
| install.sh بيقول «is not set for production» | عدّل السطر اللي ذكره بـ `dashboard/.env` (القسم 3) وأعد الأمر |
| 502 Bad Gateway | `systemctl status php8.3-fpm` وإن `/run/php/laqta.sock` موجود |
| 401 وكلمة السر ما بتفتح | `sudo htpasswd -B /etc/nginx/laqta.htpasswd admin` أو `--reset-auth` |
| 429 Too Many Requests | عنوانك زاد عن حد الطلبات، استنى لحظات. إذا كنت غلطت بكلمة السر وانحظرت: `fail2ban-client set laqta-nginx-auth unbanip عنوانك` |
| 403 على `/healthz` | عنوان المراقب مو بالقائمة: أعد install.sh مع `--monitor-ip` (القسم 11) |
| 500 بصفحة اللوحة | `/opt/laqta/dashboard/storage/logs/` (آخر ملف)، و`server_check.py --only env` |
| رفع صورة يدوي أكبر من 2MB كان بيفشل («No file uploaded») | المحرّك هلق بيقبل لـ 20MB (`upload_max_filesize=20M`، `post_max_size=25M` بالـ pool). إذا لسا: `sudo bash install.sh ...` ليتحدّث الـ pool |
| «شغّل» من اللوحة ما بلّش أو تأخر 10 ثواني | `systemctl status laqta-run.path` لازم active؛ وإلا بيشتغل بالطريقة القديمة |
| وحدة انقتلت لقلة الذاكرة | `journalctl -u laqta-nightly -e` (دوّر على كلمة oom)، ارفع `MemoryMax` أو استعمل `--lite` (القسم 8) |
| "Invalid JSON output from Python bridge" | شغّل الجسر بإيدك بمستخدم `laqta` وشوف الخطأ، وفحص `server_check.py --only python` |
| الليلة ما اشتغلت | `systemctl list-timers 'laqta-*'` و`journalctl -u laqta-nightly -e` |
| اعتمدت صورة وما وصلت للشيت | `journalctl -u laqta-outbox-flush -e` وصفحة الصحة (الكتابات `DEAD`) |
| القرص عم يمتلي | `df -h` ومجلد `/var/backups/laqta` و`/opt/laqta/temp` (القسم 12 للسجلات) |
| rembg ما بيشتغل | `server_check.py --only bg` وإن `/var/lib/laqta/models` فيه ملف `.onnx` |

سجلات مفيدة: `journalctl -u nginx -e`، `/var/log/nginx/laqta.error.log`، `journalctl -u laqta-sync-worker -e`، `journalctl -u laqta-run -e`.
