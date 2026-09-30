<?php

namespace App\Services;

use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * Real batch counters read from automation_queue: rows per status, and rows that are
 * not completed per failure_code. These replace the hard-coded dashboard tiles.
 */
class QueueStats
{
    public static function counters(): array
    {
        $byStatus = [];
        $byCode = [];
        try {
            $rows = DB::table('automation_queue')
                ->select('status', DB::raw('COUNT(*) AS n'))
                ->groupBy('status')
                ->get();
            foreach ($rows as $r) {
                $byStatus[(string) $r->status] = (int) $r->n;
            }
            if (Schema::hasColumn('automation_queue', 'failure_code')) {
                $codes = DB::table('automation_queue')
                    ->whereNotNull('failure_code')
                    ->where('failure_code', '<>', '')
                    ->where('status', '<>', 'completed')
                    ->select('failure_code', DB::raw('COUNT(*) AS n'))
                    ->groupBy('failure_code')
                    ->get();
                foreach ($codes as $r) {
                    $byCode[(string) $r->failure_code] = (int) $r->n;
                }
            }
        } catch (\Throwable $e) {
            // automation_queue does not exist yet (fresh install before the first run)
        }
        ksort($byCode);
        return ['by_status' => $byStatus, 'by_failure_code' => $byCode];
    }
}
