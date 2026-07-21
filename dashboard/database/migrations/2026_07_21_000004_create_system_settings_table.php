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
        if (!Schema::hasTable('system_settings')) {
            Schema::create('system_settings', function (Blueprint $table) {
                $table->string('key')->primary();
                $table->text('value')->nullable();
                $table->timestamp('updated_at')->useCurrent()->useCurrentOnUpdate();
            });

            // Seeding default settings from Laravel's environment config
            $defaultSettings = [
                ['key' => 'photoroom_api_key', 'value' => env('PHOTOROOM_API_KEY', '')],
                ['key' => 'gemini_api_key', 'value' => env('GEMINI_API_KEY', '')],
                ['key' => 'gemini_model', 'value' => env('GEMINI_MODEL', 'gemini-3.1-flash-lite')],
                ['key' => 'cloudinary_cloud_name', 'value' => env('CLOUDINARY_CLOUD_NAME', '')],
                ['key' => 'cloudinary_api_key', 'value' => env('CLOUDINARY_API_KEY', '')],
                ['key' => 'cloudinary_api_secret', 'value' => env('CLOUDINARY_API_SECRET', '')],
                ['key' => 'google_search_api_key', 'value' => env('GOOGLE_SEARCH_API_KEY', '')],
                ['key' => 'google_search_cx', 'value' => env('GOOGLE_SEARCH_CX', '')],
                ['key' => 'clip_relevance_threshold', 'value' => '0.22'],
                ['key' => 'clip_grey_zone_threshold', 'value' => '0.18'],
                ['key' => 'strict_brand_match', 'value' => 'true'],
                ['key' => 'enable_gemini_pre_validation', 'value' => 'true'],
                ['key' => 'filter_competitors', 'value' => 'true'],
                ['key' => 'bypass_white_background_check', 'value' => 'false'],
                ['key' => 'proxy_url', 'value' => env('PROXY_URL', '')],
            ];

            foreach ($defaultSettings as $setting) {
                DB::table('system_settings')->insertOrIgnore($setting);
            }
        }
    }

    /**
     * Reverse the migrations.
     */
    public function down(): void
    {
        Schema::dropIfExists('system_settings');
    }
};
