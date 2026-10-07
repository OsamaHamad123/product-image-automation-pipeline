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
//   --list: الأسماء بس.  --delete: بيمسحه وبيطلّعه من كل الأجهزة.
Artisan::command('laqta:user {name?} {--list} {--delete}', function () {
    if ($this->option('list')) {
        $names = User::query()->orderBy('name')->pluck('name');
        $this->line($names->isEmpty() ? 'ما في مستخدمين.' : $names->implode("\n"));
        return 0;
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
    $password = Str::password(20, symbols: false);
    $created = $user === null;
    $user ??= new User(['name' => $name, 'email' => $name . '@laqta.local']);
    $user->password = $password;               // بيتشفّر لحاله (casts: hashed)
    $user->remember_token = Str::random(60);   // كلمة سر جديدة = «تذكّرني» القديم ما عاد يشتغل
    $user->save();
    if (!$created) {
        DB::table('sessions')->where('user_id', $user->id)->delete();
    }
    $this->info(($created ? "انعمل المستخدم {$name}." : "تغيّرت كلمة سر {$name}، وطلع من كل الأجهزة.") . "\nكلمة السر: {$password}");
    return 0;
})->purpose('إضافة مستخدم للوحة أو تغيير كلمة سره (بيطبع كلمة سر جديدة)');
