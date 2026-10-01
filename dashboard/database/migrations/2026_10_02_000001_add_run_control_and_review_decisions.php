<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * مطابقة لما ينشئه local_cache_db.init_db في بايثون:
 * - automation_queue.run_id: التشغيل الذي يعالج الصف (تقدم التشغيل يُحسب من صفوفه فقط)
 * - automation_state.run_id و stop_requested: التشغيل الحالي وطلب الإيقاف الآمن من اللوحة
 * - جدول review_decisions: كل موافقة/رفض/رفع يدوي من المراجع (دليل جاهزية النشر التلقائي)
 * كل إضافة محمية بـ hasColumn/hasTable لأن بايثون قد يكون أنشأها مسبقاً.
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
        $this->addMissing('automation_queue', [
            'run_id' => fn (Blueprint $t) => $t->string('run_id', 64)->nullable()->index(),
        ]);

        $this->addMissing('automation_state', [
            'run_id' => fn (Blueprint $t) => $t->string('run_id', 64)->nullable(),
            'stop_requested' => fn (Blueprint $t) => $t->integer('stop_requested')->default(0),
        ]);

        if (!Schema::hasTable('review_decisions')) {
            Schema::create('review_decisions', function (Blueprint $table) {
                $table->id();
                $table->timestamp('created_at')->useCurrent()->index();
                $table->string('action', 16);
                $table->string('sku_key', 64)->nullable()->index();
                $table->integer('row_number')->nullable();
                $table->string('brand', 255)->nullable()->index();
                $table->string('product_name', 255)->nullable();
                $table->text('image_url')->nullable();
                $table->string('page_domain', 255)->nullable();
                $table->string('identity_tier', 8)->nullable();
                $table->string('engine_decision', 32)->nullable();
                $table->boolean('was_preselected')->nullable();
                $table->string('vlm_decision', 16)->nullable();
                $table->string('reason_code', 32)->nullable();
            });
        }
    }

    public function down(): void
    {
        Schema::dropIfExists('review_decisions');

        $drops = [
            'automation_queue' => ['run_id'],
            'automation_state' => ['run_id', 'stop_requested'],
        ];
        foreach ($drops as $table => $columns) {
            if (!Schema::hasTable($table)) {
                continue;
            }
            $indexNames = array_column(Schema::getIndexes($table), 'name');
            foreach ($columns as $column) {
                if (Schema::hasColumn($table, $column)) {
                    $index = "{$table}_{$column}_index";
                    Schema::table($table, function (Blueprint $t) use ($column, $index, $indexNames) {
                        if (in_array($index, $indexNames, true)) {
                            $t->dropIndex($index);
                        }
                        $t->dropColumn($column);
                    });
                }
            }
        }
    }
};
