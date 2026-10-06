<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * مطابقة لما ينشئه local_cache_db.init_db في بايثون:
 * - curation_candidates.provider: المزود الذي جاب المرشح (serper، local_index، page، serper_shopping ...)
 * - curation_candidates.query_id: الاستعلام (Q1..Q4، R1/R2، IDX، X0..X5)
 * - curation_candidates.phash: بصمة pHash لصورة المرشح (النسخ المتقاربة من الصورة نفسها)
 * تقارير التشغيل تنسب الاختيار لمصدره منها. كل إضافة محمية بـ hasColumn لأن بايثون قد يكون أضافها مسبقاً.
 */
return new class extends Migration
{
    private function addMissing(string $table, array $definitions): void
    {
        if (!Schema::hasTable($table)) {
            return;
        }
        foreach ($definitions as $column => $define) {
            if (!Schema::hasColumn($table, $column)) {
                Schema::table($table, function (Blueprint $t) use ($define) {
                    $define($t);
                });
            }
        }
    }

    public function up(): void
    {
        $this->addMissing('curation_candidates', [
            'provider' => fn (Blueprint $t) => $t->string('provider', 32)->nullable(),
            'query_id' => fn (Blueprint $t) => $t->string('query_id', 16)->nullable(),
            'phash' => fn (Blueprint $t) => $t->string('phash', 32)->nullable(),
        ]);
    }

    public function down(): void
    {
        if (!Schema::hasTable('curation_candidates')) {
            return;
        }
        foreach (['provider', 'query_id', 'phash'] as $column) {
            if (Schema::hasColumn('curation_candidates', $column)) {
                Schema::table('curation_candidates', function (Blueprint $t) use ($column) {
                    $t->dropColumn($column);
                });
            }
        }
    }
};
