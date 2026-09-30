<?php

namespace App\Http\Controllers;

use App\Services\PythonBridge;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Cache;

/**
 * Search health and cost panel of the diagnostics page.
 *
 * The aggregation of automation_queue.trace_json lives in Python (ops_health.summarize,
 * covered by pytest) and is reached through the read-only cli_bridge action 'ops_health',
 * so the dashboard and the worker's outage notice share one set of rules. The result is
 * cached for a minute to keep the page cheap; ?refresh=1 reads the queue again.
 */
class HealthController extends Controller
{
    public const CACHE_KEY = 'ops_health_v1';
    public const CACHE_SECONDS = 60;

    public function summary(Request $request)
    {
        if (!$request->boolean('refresh')) {
            $cached = Cache::get(self::CACHE_KEY);
            if (is_array($cached)) {
                return response()->json($cached)->header('Cache-Control', 'no-store');
            }
        }

        $result = PythonBridge::run('ops_health');
        if (($result['status'] ?? '') !== 'success') {
            // Only the fixed message: the raw bridge output stays in the Laravel log.
            return response()->json([
                'status' => 'error',
                'error' => (string) ($result['error'] ?? 'ops_health failed'),
            ], 500)->header('Cache-Control', 'no-store');
        }
        Cache::put(self::CACHE_KEY, $result, self::CACHE_SECONDS);
        return response()->json($result)->header('Cache-Control', 'no-store');
    }
}
