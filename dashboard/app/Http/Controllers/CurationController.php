<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Http\JsonResponse;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Redis;
use Illuminate\Support\Facades\Log;
use Exception;

class CurationController extends Controller
{
    /**
     * معالجة تحديثات المراجعة البشرية وإرسال التغفيل والحدث إلى Redis Pub/Sub.
     */
    public function mutate(Request $request, string $productId): JsonResponse
    {
        $validated = $request->validate([
            'decision' => 'required|in:approved,rejected',
            'session_id' => 'required|string',
        ]);

        $decision = $validated['decision'];
        $sessionId = $validated['session_id'];

        try {
            // 1. تحديث قاعدة بيانات الكتالوج المركزية في Laravel
            if (DB::getSchemaBuilder()->hasTable('products')) {
                DB::table('products')
                    ->where('id', $productId)
                    ->update([
                        'status' => $decision,
                        'updated_at' => now()
                    ]);
            }

            // 2. إعداد الحدث وتأمين البث عبر Redis Pub/Sub و Redis List
            $eventPayload = [
                'event_id' => 'evt_' . bin2hex(random_bytes(8)),
                'event_type' => 'curation_pending',
                'id' => (string)$productId,
                'status' => $decision,
                'timestamp' => microtime(true)
            ];

            $jsonPayload = json_encode($eventPayload);
            $channel = "curation:channel:{$sessionId}";
            $historyKey = "curation:history:{$sessionId}";

            try {
                Redis::publish($channel, $jsonPayload);
                Redis::pipeline(function ($pipe) use ($historyKey, $jsonPayload) {
                    $pipe->rpush($historyKey, $jsonPayload);
                    $pipe->ltrim($historyKey, -150, -1);
                    $pipe->expire($historyKey, 7200);
                });
            } catch (Exception $redisEx) {
                Log::warning("Redis Pub/Sub broadcast skipped or unreachable: " . $redisEx->getMessage());
            }

            return response()->json([
                'status' => 'success',
                'message' => 'تم تسجيل القرار البشري وبث التحديثات للمنظومة بنجاح.',
                'data' => [
                    'id' => $productId,
                    'status' => $decision
                ]
            ], 200);

        } catch (Exception $e) {
            Log::error("Curation mutation error for product {$productId}: " . $e->getMessage());
            return response()->json([
                'status' => 'error',
                'message' => $e->getMessage()
            ], 422);
        }
    }
}
