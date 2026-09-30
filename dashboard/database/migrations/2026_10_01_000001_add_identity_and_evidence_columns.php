<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * أعمدة الهوية والأدلة (مطابقة لما ينشئه local_cache_db.init_db في بايثون):
 * - automation_queue: sku_key, payload_json, worker_id, lease_until, failure_code, trace_json
 * - curation_candidates: sku_key, run_id, status, reasons_json, evidence_json, vlm_json,
 *   content_sha256, identity_tier، وتحويل title إلى TEXT
 * - curation_candidates.page_url و automation_state.notice
 * - resolved_products: sku_key, verification_status, approved_by
 * - جدول rejected_images لقرارات الرفض البشرية
 * كل إضافة محمية بـ hasColumn/hasTable لأن بايثون قد يكون أضاف العمود نفسه مسبقاً.
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
            'sku_key' => fn (Blueprint $t) => $t->string('sku_key', 64)->nullable()->index(),
            'payload_json' => fn (Blueprint $t) => $t->longText('payload_json')->nullable(),
            'worker_id' => fn (Blueprint $t) => $t->string('worker_id', 64)->nullable(),
            'lease_until' => fn (Blueprint $t) => $t->dateTime('lease_until')->nullable(),
            'failure_code' => fn (Blueprint $t) => $t->string('failure_code', 32)->nullable()->index(),
            'trace_json' => fn (Blueprint $t) => $t->longText('trace_json')->nullable(),
        ]);

        $this->addMissing('curation_candidates', [
            'sku_key' => fn (Blueprint $t) => $t->string('sku_key', 64)->nullable()->index(),
            'run_id' => fn (Blueprint $t) => $t->string('run_id', 64)->nullable(),
            'status' => fn (Blueprint $t) => $t->string('status')->default('pending'),
            'reasons_json' => fn (Blueprint $t) => $t->longText('reasons_json')->nullable(),
            'evidence_json' => fn (Blueprint $t) => $t->longText('evidence_json')->nullable(),
            'vlm_json' => fn (Blueprint $t) => $t->longText('vlm_json')->nullable(),
            'content_sha256' => fn (Blueprint $t) => $t->string('content_sha256', 64)->nullable(),
            'identity_tier' => fn (Blueprint $t) => $t->unsignedTinyInteger('identity_tier')->nullable(),
            'page_url' => fn (Blueprint $t) => $t->text('page_url')->nullable(),
        ]);

        // رسالة حالة العامل (فحص نموذج Gemini / انقطاع المزودين) كما يكتبها main.py
        $this->addMissing('automation_state', [
            'notice' => fn (Blueprint $t) => $t->string('notice', 255)->nullable(),
        ]);

        // عناوين صفحات المنتجات تتجاوز 255 حرفاً وكانت تُسقط حفظ الصف كاملاً (WF-M6)
        if (Schema::hasTable('curation_candidates') && Schema::hasColumn('curation_candidates', 'title')) {
            $type = strtolower((string) Schema::getColumnType('curation_candidates', 'title'));
            if (!in_array($type, ['text', 'mediumtext', 'longtext'], true)) {
                Schema::table('curation_candidates', function (Blueprint $t) {
                    $t->text('title')->nullable()->change();
                });
            }
        }

        $this->addMissing('resolved_products', [
            'sku_key' => fn (Blueprint $t) => $t->string('sku_key', 64)->nullable()->index(),
            'verification_status' => fn (Blueprint $t) => $t->enum(
                'verification_status',
                ['human_approved', 'auto_verified', 'superseded', 'legacy']
            )->default('legacy'),
            'approved_by' => fn (Blueprint $t) => $t->string('approved_by', 64)->nullable(),
        ]);

        if (!Schema::hasTable('rejected_images')) {
            Schema::create('rejected_images', function (Blueprint $table) {
                $table->id();
                $table->string('sku_key', 64)->nullable()->index();
                $table->text('url_norm');
                $table->text('original_url')->nullable();
                $table->text('page_url')->nullable();
                $table->string('phash', 32)->nullable();
                $table->string('reason_code', 32);
                $table->timestamp('created_at')->useCurrent();
            });
        }
    }

    public function down(): void
    {
        Schema::dropIfExists('rejected_images');

        $drops = [
            'automation_queue' => ['sku_key', 'payload_json', 'worker_id', 'lease_until', 'failure_code', 'trace_json'],
            'curation_candidates' => ['sku_key', 'run_id', 'reasons_json', 'evidence_json', 'vlm_json', 'content_sha256', 'identity_tier', 'page_url'],
            'automation_state' => ['notice'],
            'resolved_products' => ['sku_key', 'verification_status', 'approved_by'],
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
        // title يبقى TEXT: إرجاعه إلى VARCHAR(255) قد يقص بيانات موجودة.
    }
};
