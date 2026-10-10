<?php

use App\Models\User;
use Illuminate\Foundation\Inspiring;
use Illuminate\Support\Facades\Artisan;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

Artisan::command('inspire', function () {
    $this->comment(Inspiring::quote());
})->purpose('Display an inspiring quote')->hourly();

// مستخدمين اللوحة (صفحة الدخول): php artisan laqta:user NAME  -> بيعمله، أو بيغيّر كلمة سره إذا موجود، وبيطبع كلمة سر جديدة.
//   --role=reviewer|admin: مستخدم جديد بلاها مدير. على مستخدم موجود بتغيّر دوره بس (كلمة السر ما بتتغيّر).
//   --list: الأسماء مع الأدوار.  --delete: بيمسحه وبيطلّعه من كل الأجهزة.
Artisan::command('laqta:user {name?} {--list} {--delete} {--role=}', function () {
    if ($this->option('list')) {
        $users = User::query()->orderBy('name')->get();
        $this->line($users->isEmpty() ? 'ما في مستخدمين.'
            : $users->map(fn ($u) => $u->name . '  ' . ($u->isReviewer() ? User::REVIEWER : User::ADMIN))->implode("\n"));
        return 0;
    }
    $role = $this->option('role');
    if ($role !== null && !in_array($role, User::ROLES, true)) {
        $this->error('الدور لازم يكون ' . implode(' أو ', User::ROLES) . '.');
        return 1;
    }
    $name = trim((string) $this->argument('name'));
    if (!preg_match('/^[A-Za-z0-9._-]{2,40}$/', $name)) {
        $this->error('الاسم لازم يكون 2 لـ 40 حرف إنجليزي أو رقم أو . _ -');
        return 1;
    }
    $user = User::query()->where('name', $name)->first();
    if ($this->option('delete')) {
        if (!$user) {
            $this->error("ما في مستخدم اسمه {$name}.");
            return 1;
        }
        DB::table('sessions')->where('user_id', $user->id)->delete();
        $user->delete();
        $this->info("انمسح {$name}، وطلع من كل الأجهزة.");
        return 0;
    }
    if ($user !== null && $role !== null) {
        // تغيير الدور بس: الجلسات المفتوحة بتاخد الدور الجديد من أول طلب (ReviewerLimits بيقرأ users.role كل مرة)
        $user->role = $role;
        $user->save();
        $this->info("صار {$name} " . ($role === User::REVIEWER ? 'مراجع' : 'مدير') . '.');
        return 0;
    }
    $password = Str::password(20, symbols: false);
    $created = $user === null;
    $user ??= new User(['name' => $name, 'email' => $name . '@laqta.local'] + ($role !== null ? ['role' => $role] : []));
    $user->password = $password;               // بيتشفّر لحاله (casts: hashed)
    $user->remember_token = Str::random(60);   // كلمة سر جديدة = «تذكّرني» القديم ما عاد يشتغل
    $user->save();
    if (!$created) {
        DB::table('sessions')->where('user_id', $user->id)->delete();
    }
    $kind = $user->isReviewer() ? 'مراجع' : 'مدير';
    $this->info(($created ? "انعمل المستخدم {$name} ({$kind})." : "تغيّرت كلمة سر {$name}، وطلع من كل الأجهزة.")
        . "\nكلمة السر: {$password}");
    return 0;
})->purpose('إضافة مستخدم للوحة أو تغيير دوره أو كلمة سره (بيطبع كلمة سر جديدة)');
