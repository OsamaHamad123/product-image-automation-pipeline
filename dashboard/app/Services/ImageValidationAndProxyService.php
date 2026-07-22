<?php

namespace App\Services;

use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Log;
use Illuminate\Support\Facades\Cache;

class ImageValidationAndProxyService
{
    protected string $engineBaseUrl;

    public function __construct()
    {
        $this->engineBaseUrl = config('services.image_engine.url', 'http://localhost:8000');
    }

    /**
     * حساب RRF واستدعى الاسترداد الذاتي إذا كانت النسبة أقل من 65%.
     */
    public function resolveProductImages(int $productId, array $denseImageIds, array $lexicalImageIds): array
    {
        $cacheKey = "product_resolved_images:{$productId}";

        return Cache::remember($cacheKey, now()->addHours(12), function () use ($productId, $denseImageIds, $lexicalImageIds) {
            try {
                $response = Http::timeout(5.0)
                    ->post("{$this->engineBaseUrl}/api/v1/rank-fusion", [
                        'dense_results' => $denseImageIds,
                        'lexical_results' => $lexicalImageIds
                    ]);

                if ($response->failed()) {
                    throw new \Exception("Python rank fusion service error.");
                }

                $data = $response->json();

                if ($data['self_healing_required'] ?? false) {
                    $this->triggerSelfHealingPipeline($productId);
                }

                return [
                    'candidates' => $data['passed_candidates'] ?? [],
                    'rejected' => $data['rejected_candidates'] ?? [],
                    'self_healing_active' => $data['self_healing_required'] ?? false
                ];

            } catch (\Exception $e) {
                Log::error("Rank fusion error for product {$productId}: " . $e->getMessage());
                $this->triggerSelfHealingPipeline($productId);

                return [
                    'candidates' => [],
                    'rejected' => [],
                    'self_healing_active' => true,
                    'error_fallback' => true
                ];
            }
        });
    }

    protected function triggerSelfHealingPipeline(int $productId): void
    {
        Log::warning("Self-Healing Pipeline triggered for product {$productId}");
        if (class_exists('\App\Jobs\SelfHealingProductImageJob')) {
            \App\Jobs\SelfHealingProductImageJob::dispatch($productId);
        }
    }
}
