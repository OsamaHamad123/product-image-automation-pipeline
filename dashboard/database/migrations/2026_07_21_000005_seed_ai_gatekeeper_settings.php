<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\Schema;
use Illuminate\Support\Facades\DB;

return new class extends Migration
{
    /**
     * Run the migrations.
     */
    public function up(): void
    {
        if (Schema::hasTable('system_settings')) {
            $newSettings = [
                ['key' => 'clip_relevance_threshold', 'value' => '0.22'],
                ['key' => 'clip_grey_zone_threshold', 'value' => '0.18'],
                ['key' => 'strict_brand_match', 'value' => 'true'],
                ['key' => 'enable_gemini_pre_validation', 'value' => 'true'],
                ['key' => 'filter_competitors', 'value' => 'true'],
                ['key' => 'bypass_white_background_check', 'value' => 'false'],
            ];

            foreach ($newSettings as $setting) {
                DB::table('system_settings')->insertOrIgnore($setting);
            }
        }
    }

    /**
     * Reverse the migrations.
     */
    public function down(): void
    {
        // No down migration needed for seeding
    }
};
