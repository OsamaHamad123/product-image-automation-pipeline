<?php

namespace App\Services;

use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Redis;
use Illuminate\Support\Facades\Cache;
use Exception;
use Throwable;

class SelectiveSearchService
{
    protected string $fastApiEndpoint;

    public function __construct()
    {
        $this->fastApiEndpoint = env('FASTAPI_URL', 'http://127.0.0.1:8000/api');
    }

    /**
     * Relay rejection payload and coordinate session blacklist caching before forwarding to Python core.
     *
     * @param string $sessionId
     * @param array $queryVector
     * @param array $rejectedItem Includes product_id, vector, and phash
     * @return array
     */
    public function reSearchAndExclude(string $sessionId, array $queryVector, array $rejectedItem): array
    {
        $productId = $rejectedItem['product_id'] ?? '0';
        $vector = $rejectedItem['vector'] ?? [];
        $phash = $rejectedItem['phash'] ?? '0000000000000000';

        $cacheKey = "blacklist:{$sessionId}";
        $itemPayload = json_encode([
            'product_id' => $productId,
            'phash' => $phash,
            'vector' => $vector,
            'timestamp' => time()
        ]);

        try {
            Redis::sadd($cacheKey, $itemPayload);
            Redis::expire($cacheKey, 1800);
        } catch (Throwable $e) {
            try {
                $existing = Cache::get($cacheKey, []);
                $existing[] = json_decode($itemPayload, true);
                Cache::put($cacheKey, $existing, 1800);
            } catch (Throwable $cacheEx) {
                logger()->warning("Cache fallback write warning: " . $cacheEx->getMessage());
            }
        }

        try {
            $response = Http::withHeaders([
                'Accept' => 'application/json',
                'Content-Type' => 'application/json'
            ])->timeout(8)->post("{$this->fastApiEndpoint}/v1/curation/reject-and-research", [
                'session_id' => $sessionId,
                'query_vector' => $queryVector,
                'rejected_product_id' => strval($productId),
                'rejected_vector' => $vector,
                'rejected_phash' => $phash
            ]);

            if ($response->failed()) {
                throw new Exception("Downstream core visual engine failed with status " . $response->status());
            }

            return $response->json();

        } catch (Exception $e) {
            logger()->error("Failure in selective visual search service coordinate: " . $e->getMessage());
            return [
                'success' => false,
                'message' => $e->getMessage(),
                'passed_candidates' => []
            ];
        }
    }
}
