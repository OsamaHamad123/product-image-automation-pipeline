<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Auth;
use Illuminate\Support\Facades\RateLimiter;

/**
 * صفحة الدخول للوحة (RequireLogin بيحوّل لهون). الدخول باسم المستخدم (users.name) وكلمة السر؛ المستخدمين بينعملوا
 * من السيرفر بـ `php artisan laqta:user`. 5 محاولات غلط بالدقيقة لكل اسم وعنوان، وبعدها لازم يستنى.
 */
class LoginController extends Controller
{
    public const MAX_ATTEMPTS = 5;

    public function show()
    {
        if (Auth::check()) {
            return redirect()->route('dashboard.index');
        }
        return view('auth.login');
    }

    public function attempt(Request $request)
    {
        $name = trim((string) $request->input('username', ''));
        $password = (string) $request->input('password', '');
        $key = 'login:' . mb_strtolower($name) . '|' . $request->ip();

        if (RateLimiter::tooManyAttempts($key, self::MAX_ATTEMPTS)) {
            $seconds = RateLimiter::availableIn($key);
            return redirect()->route('login')->withInput(['username' => $name])
                ->withErrors(['username' => "محاولات كتير غلط. جرّب كمان {$seconds} ثانية."]);
        }
        if ($name === '' || $password === ''
            || !Auth::attempt(['name' => $name, 'password' => $password], $request->boolean('remember'))) {
            RateLimiter::hit($key, 60);
            return redirect()->route('login')->withInput(['username' => $name])
                ->withErrors(['username' => 'اسم المستخدم أو كلمة السر غلط.']);
        }

        RateLimiter::clear($key);
        $request->session()->regenerate();
        return redirect()->intended(route('dashboard.index'));
    }

    public function logout(Request $request)
    {
        Auth::logout();
        $request->session()->invalidate();
        $request->session()->regenerateToken();
        return redirect()->route('login');
    }
}
