<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Log;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Cache;

class CatalogHealingController extends Controller
{
    /**
     * استقبال ومعالجة طلبات الصور التالفة كبوابة وسيطة وتحديث الكتالوج.
     */
    public function processBrokenImage(Request $request)
    {
        $validated = $request->validate([
            'product_id' => 'required|integer',
            'broken_url' => 'required|url',
        ]);

        $productId = $validated['product_id'];
        $brokenUrl = $validated['broken_url'];

        try {
            // توجيه الطلب إلى البوابة الخدمية الخارجية لـ FastAPI
            $fastApiUrl = config('services.fastapi.url', 'http://127.0.0.1:8000') . '/api/v1/fix-broken-image-link';
            
            $response = Http::timeout(5.0)
                ->acceptJson()
                ->post($fastApiUrl, [
                    'product_id' => $productId,
                    'broken_url' => $brokenUrl,
                ]);

            if ($response->failed()) {
                Log::error("FastAPI image healing gateway returned a failure code for Product ID: {$productId}");
                return response()->json([
                    'product_id' => $productId,
                    'resolved_url' => "https://webcache.googleusercontent.com/search?q=cache:{$brokenUrl}",
                    'status' => 'GatewayErrorFallback',
                    'match_percentage' => 100.0
                ], 200);
            }

            $data = $response->json();

            // تحديث السجل المركزي في قاعدة بيانات Laravel إن وجد
            if (DB::getSchemaBuilder()->hasTable('products')) {
                DB::table('products')
                    ->where('id', $productId)
                    ->update([
                        'image_url' => $data['resolved_url'],
                        'status_label' => 'Cached',
                        'updated_at' => now()
                    ]);
            }

            Cache::forget("product_entity_view_{$productId}");

            return response()->json([
                'product_id' => $data['product_id'],
                'resolved_url' => $data['resolved_url'],
                'status' => $data['status'],
                'match_percentage' => $data['match_percentage']
            ], 200);

        } catch (\Exception $exception) {
            Log::critical("System failure during Laravel image self-healing proxying: " . $exception->getMessage());
            return response()->json([
                'product_id' => $productId,
                'resolved_url' => "https://webcache.googleusercontent.com/search?q=cache:{$brokenUrl}",
                'status' => 'ProxyFailureFallback',
                'match_percentage' => 100.0
            ], 200);
        }
    }
}
