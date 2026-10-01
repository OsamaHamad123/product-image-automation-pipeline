<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Http\JsonResponse;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;
use App\Services\CandidateRow;
use App\Services\PythonBridge;

class CurationController extends Controller
{
    /** رموز أسباب الرفض (هوية المنتج أولاً) — نفس القائمة المعتمدة في cli_bridge (D11). */
    public const REASON_CODES = [
        'WRONG_PRODUCT', 'WRONG_BRAND', 'WRONG_VARIANT', 'WRONG_SIZE', 'WRONG_PACK',
        'NOT_PACKSHOT', 'LOW_QUALITY',
        // رموز تجميلية قديمة ما زالت مقبولة
        'HALO_ARTIFACT', 'BACKGROUND_BLEED', 'CROP_MARGIN_CLIPPING',
    ];

    /**
     * رفض صورة مرشحة بسبب محدد وإعادة البحث الفعلي مع استبعادها.
     * يستدعي cli_bridge.reject_image مباشرة (research=true) ويعيد حالته الحقيقية كما هي.
     */
    public function rejectAndReSearch(Request $request): JsonResponse
    {
        $validated = $request->validate([
            'row_number' => 'required|integer',
            'image_url' => 'required|string',
            'reason_code' => 'required|string|in:' . implode(',', self::REASON_CODES),
            'product_name' => 'nullable|string',
            'brand' => 'nullable|string',
            'barcode' => 'nullable|string',
            'sku_key' => 'nullable|string',
            'product_name_ar' => 'nullable|string',
            'brand_ar' => 'nullable|string',
            'category' => 'nullable|string',
            'size' => 'nullable|string',
            'sub_category' => 'nullable|string',
            'origin' => 'nullable|string',
            'custom_query' => 'nullable|string',
        ]);

        $params = [
            'row_number' => (int) $validated['row_number'],
            'image_url' => $validated['image_url'],
            'product_name' => $validated['product_name'] ?? '',
            'brand' => $validated['brand'] ?? '',
            'barcode' => $validated['barcode'] ?? '',
            'sku_key' => $validated['sku_key'] ?? '',
            'reason_code' => $validated['reason_code'],
            'research' => true,
            'product_name_ar' => $validated['product_name_ar'] ?? '',
            'brand_ar' => $validated['brand_ar'] ?? '',
            'category' => $validated['category'] ?? '',
            // هوية الحجم تُرسل دائماً: الجسر لا يستعيدها إلا من صف طابور قد لا يوجد
            'size' => $validated['size'] ?? '',
            'sub_category' => $validated['sub_category'] ?? '',
            'origin' => $validated['origin'] ?? '',
            'custom_query' => $validated['custom_query'] ?? '',
            'skip_cache' => true,
        ];

        $result = PythonBridge::run('reject_image', $params);

        if (!PythonBridge::isError($result)) {
            ProductController::forgetProductCaches();
        }

        return response()->json($result, PythonBridge::httpStatus($result));
    }

    /**
     * Persist selected candidate thumb to DB & invalidate cache.
     */
    public function selectCandidate(Request $request): JsonResponse
    {
        $validated = $request->validate([
            'row_number' => 'required|integer',
            'selected_url' => 'required|string',
            'sku_key' => 'nullable|string',
        ]);

        $rowNumber = $validated['row_number'];
        $selectedUrl = $validated['selected_url'];
        $skuKey = trim((string) ($validated['sku_key'] ?? ''));

        try {
            if (Schema::hasTable('curation_candidates')) {
                $scope = function ($q) use ($rowNumber, $skuKey) {
                    if ($skuKey !== '' && Schema::hasColumn('curation_candidates', 'sku_key')) {
                        $q->where('sku_key', $skuKey);
                    } else {
                        $q->where('row_number', $rowNumber);
                    }
                };

                DB::table('curation_candidates')->where($scope)->update(['is_selected' => 0]);
                DB::table('curation_candidates')->where($scope)
                    ->where('image_url', $selectedUrl)
                    ->update(['is_selected' => 1]);
            }

            ProductController::forgetProductCaches();

            return response()->json([
                'status' => 'success',
                'message' => 'تم حفظ اختيارات المرشح في قاعدة البيانات بنجاح.'
            ]);
        } catch (\Exception $e) {
            return response()->json(['status' => 'error', 'message' => $e->getMessage()], 500);
        }
    }

    /**
     * Persist updated candidate list to DB & invalidate cache.
     * يحفظ الحالة والأسباب والأدلة لكل مرشح، ولا يحدد أي مرشح مسبقاً إلا إذا كانت حالته preselected
     * أو اختاره المراجع بنفسه.
     */
    public function saveCandidates(Request $request): JsonResponse
    {
        $validated = $request->validate([
            'row_number' => 'required|integer',
            'product_name' => 'nullable|string',
            'brand' => 'nullable|string',
            'sku_key' => 'nullable|string',
            'candidates' => 'required|array',
        ]);

        $rowNumber = $validated['row_number'];
        $productName = $validated['product_name'] ?? 'منتج';
        $brand = $validated['brand'] ?? '';
        $skuKey = trim((string) ($validated['sku_key'] ?? ''));
        $candidates = $validated['candidates'];

        try {
            if (Schema::hasTable('curation_candidates')) {
                $columns = array_flip(Schema::getColumnListing('curation_candidates'));
                $hasSku = isset($columns['sku_key']);
                $runId = 'ui-' . date('YmdHis') . '-' . bin2hex(random_bytes(4));

                $rowsToInsert = [];
                foreach ($candidates as $c) {
                    if (!is_array($c)) {
                        continue;
                    }
                    $status = (string) ($c['status'] ?? 'pending');
                    $isSelected = array_key_exists('is_selected', $c)
                        ? (int) $c['is_selected']
                        : ($status === 'preselected' ? 1 : 0);
                    $row = [
                        'row_number' => $rowNumber,
                        'product_name' => $productName,
                        'brand' => $brand,
                        'image_url' => (string) ($c['image_url'] ?? $c['url'] ?? ''),
                        // title قد يتجاوز 255 حرفاً في الجداول القديمة
                        'title' => mb_substr((string) ($c['title'] ?? ''), 0, 250),
                        'width' => isset($c['width']) && is_numeric($c['width']) ? (int) $c['width'] : null,
                        'height' => isset($c['height']) && is_numeric($c['height']) ? (int) $c['height'] : null,
                        'source_domain' => mb_substr((string) ($c['source_domain'] ?? $c['domain'] ?? ''), 0, 250),
                        'is_selected' => $isSelected === 1 ? 1 : 0,
                        'status' => mb_substr($status, 0, 32),
                        'created_at' => now(),
                    ];
                    if ($row['image_url'] === '') {
                        continue;
                    }
                    // page_url والطبقة (evidence.tier) تُحفظ كما يحفظها كاتب بايثون
                    $optional = CandidateRow::optionalColumns($c, $skuKey, $runId);
                    foreach ($optional as $col => $value) {
                        if (isset($columns[$col])) {
                            $row[$col] = $value;
                        }
                    }
                    $rowsToInsert[] = $row;
                }

                DB::transaction(function () use ($rowNumber, $skuKey, $hasSku, $rowsToInsert) {
                    DB::table('curation_candidates')->where(function ($q) use ($rowNumber, $skuKey, $hasSku) {
                        $q->where('row_number', $rowNumber);
                        if ($hasSku && $skuKey !== '') {
                            $q->orWhere('sku_key', $skuKey);
                        }
                    })->delete();

                    if (!empty($rowsToInsert)) {
                        DB::table('curation_candidates')->insert($rowsToInsert);
                    }
                });
            }

            ProductController::forgetProductCaches();

            return response()->json([
                'status' => 'success',
                'message' => 'تم تحديث وحفظ المرشحات الجديدة في قاعدة البيانات بنجاح.'
            ]);
        } catch (\Exception $e) {
            return response()->json(['status' => 'error', 'message' => $e->getMessage()], 500);
        }
    }
}
