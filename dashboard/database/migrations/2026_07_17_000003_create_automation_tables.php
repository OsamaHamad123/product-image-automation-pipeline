<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;
use Illuminate\Support\Facades\DB;

return new class extends Migration
{
    /**
     * Run the migrations.
     */
    public function up(): void
    {
        // 1. الجدول الأساسي: resolved_products
        if (!Schema::hasTable('resolved_products')) {
            Schema::create('resolved_products', function (Blueprint $table) {
                $table->id();
                $table->string('barcode')->nullable()->index('idx_barcode');
                $table->string('product_name');
                $table->string('brand')->nullable();
                $table->text('original_url')->nullable();
                $table->text('cloudinary_url');
                $table->double('clip_score')->nullable();
                $table->text('metadata_json')->nullable();
                $table->text('clip_embedding_json')->nullable();
                $table->string('perceptual_hash')->nullable();
                $table->timestamp('resolved_at')->useCurrent();

                // الفهرس المركب لتسريع البحث بالاسم والبراند
                $table->index(['product_name', 'brand'], 'idx_name_brand');
            });
        }

        // 2. جدول تتبع أخطاء الأتمتة: product_failures
        if (!Schema::hasTable('product_failures')) {
            Schema::create('product_failures', function (Blueprint $table) {
                $table->string('barcode')->primary();
                $table->string('product_name');
                $table->string('brand')->nullable();
                $table->text('error_message')->nullable();
                $table->timestamp('failed_at')->useCurrent();
            });
        }

        // 3. جدول التغذية الراجعة للتعلم النشط: active_learning_feedback
        if (!Schema::hasTable('active_learning_feedback')) {
            Schema::create('active_learning_feedback', function (Blueprint $table) {
                $table->id();
                $table->string('feedback_id')->unique();
                $table->string('asset_id')->nullable();
                $table->integer('row_number')->nullable();
                $table->string('product_name')->nullable();
                $table->string('brand')->nullable();
                $table->text('image_url')->nullable();
                $table->text('rejection_reasons')->nullable();
                $table->timestamp('timestamp')->useCurrent();
            });
        }

        // 4. جدول طابور الأتمتة: automation_queue
        if (!Schema::hasTable('automation_queue')) {
            Schema::create('automation_queue', function (Blueprint $table) {
                $table->id();
                $table->integer('row_number')->unique();
                $table->string('barcode')->nullable();
                $table->string('product_name');
                $table->string('brand')->nullable();
                $table->text('search_query')->nullable();
                $table->string('status')->default('pending')->index('idx_queue_status');
                $table->text('error_message')->nullable();
                $table->integer('attempts')->default(0);
                $table->timestamp('created_at')->useCurrent();
                $table->timestamp('updated_at')->useCurrent();
            });
        }

        // 5. جدول مرشحي الفرز والاعتماد البصري: curation_candidates
        if (!Schema::hasTable('curation_candidates')) {
            Schema::create('curation_candidates', function (Blueprint $table) {
                $table->id();
                $table->integer('row_number')->index('idx_curation_row');
                $table->string('product_name');
                $table->string('brand')->nullable();
                $table->text('image_url');
                $table->string('title')->nullable();
                $table->integer('width')->nullable();
                $table->integer('height')->nullable();
                $table->double('clip_score')->nullable();
                $table->string('source_domain')->nullable();
                $table->integer('is_selected')->default(0);
                $table->string('status')->default('pending');
                $table->timestamp('created_at')->useCurrent();
            });
        }

        // 6. جدول حالة الأتمتة بالخلفية: automation_state
        if (!Schema::hasTable('automation_state')) {
            Schema::create('automation_state', function (Blueprint $table) {
                $table->string('key')->primary();
                $table->string('status')->default('idle');
                $table->integer('total_items')->default(0);
                $table->integer('processed_items')->default(0);
                $table->integer('success_count')->default(0);
                $table->integer('failed_count')->default(0);
                $table->string('current_product_name')->nullable();
                $table->integer('pause_requested')->default(0);
                $table->timestamp('updated_at')->useCurrent();
            });

            // إدراج سجل الجلسة الافتراضي الأولي
            DB::table('automation_state')->insertOrIgnore([
                'key' => 'active_session',
                'status' => 'idle',
                'total_items' => 0,
                'processed_items' => 0,
                'success_count' => 0,
                'failed_count' => 0,
                'current_product_name' => '',
                'pause_requested' => 0
            ]);
        }
    }

    /**
     * Reverse the migrations.
     */
    public function down(): void
    {
        Schema::dropIfExists('automation_state');
        Schema::dropIfExists('curation_candidates');
        Schema::dropIfExists('automation_queue');
        Schema::dropIfExists('active_learning_feedback');
        Schema::dropIfExists('product_failures');
        Schema::dropIfExists('resolved_products');
    }
};
