<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * users.role: «admin» (كلشي) أو «reviewer» (المراجعة بس: ReviewerLimits). المستخدمين الموجودين بياخدوا admin من
 * القيمة الافتراضية، فما حدا بيخسر صلاحية. محمي بـ hasColumn متل باقي الإضافات.
 */
return new class extends Migration
{
    public function up(): void
    {
        if (Schema::hasTable('users') && !Schema::hasColumn('users', 'role')) {
            Schema::table('users', function (Blueprint $t) {
                $t->string('role', 20)->default('admin')->after('password');
            });
        }
    }

    public function down(): void
    {
        if (Schema::hasTable('users') && Schema::hasColumn('users', 'role')) {
            Schema::table('users', function (Blueprint $t) {
                $t->dropColumn('role');
            });
        }
    }
};
