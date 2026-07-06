<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;

class ApiController extends Controller
{
    private $flaskUrl = 'http://127.0.0.1:5000';

    /**
     * بروكسي لبدء البحث البصري التلقائي لمنتج
     */
    public function search(Request $request)
    {
        try {
            $response = Http::post("{$this->flaskUrl}/api/search", $request->all());
            return response()->json($response->json(), $response->status());
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }

    /**
     * بروكسي لاعتماد صورة محددة يدوياً
     */
    public function selectImage(Request $request)
    {
        try {
            $response = Http::post("{$this->flaskUrl}/api/select_image", $request->all());
            return response()->json($response->json(), $response->status());
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }

    /**
     * بروكسي لرفع ملف صورة يدوياً ومعالجته تلقائياً بالذكاء الاصطناعي
     */
    public function uploadManualImage(Request $request)
    {
        try {
            if (!$request->hasFile('file')) {
                return response()->json(['error' => 'No file uploaded'], 400);
            }

            $file = $request->file('file');

            // تجميع جميع حقول النموذج بما فيها معايير التحجيم ولون الخلفية
            $formFields = $request->only([
                'row_number', 'product_name', 'brand', 'barcode',
                'target_width', 'target_height', 'padding_ratio', 'bg_color', 'upscale'
            ]);

            // إرسال الطلب لـ Flask مع إرفاق الملف كـ Multipart
            $response = Http::timeout(120)->attach(
                'file',
                file_get_contents($file->getPathname()),
                $file->getClientOriginalName()
            )->post("{$this->flaskUrl}/api/upload_manual_image", $formFields);

            return response()->json($response->json(), $response->status());
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }

    /**
     * بروكسي لجلب السجلات الحية لكونسول التشغيل
     */
    public function logs()
    {
        try {
            $response = Http::timeout(3)->get("{$this->flaskUrl}/api/logs");
            return response()->json($response->json(), $response->status())
                ->header('Cache-Control', 'no-store');
        } catch (\Exception $e) {
            // إرجاع مصفوفة فارغة بدلاً من خطأ كامل عند تعطل Flask
            return response()->json(['logs' => []], 200)
                ->header('Cache-Control', 'no-store');
        }
    }

    /**
     * مسح كاش قائمة المنتجات لإجبار التحديث من Google Sheets
     */
    public function clearProductsCache()
    {
        \Cache::forget('products_json_v1');
        return response()->json(['status' => 'success', 'message' => 'Products cache cleared']);
    }

    /**
     * بروكسي لتشغيل الأتمتة الكلية في الخلفية
     */
    public function runAll()
    {
        try {
            $response = Http::post("{$this->flaskUrl}/api/run_all");
            return response()->json($response->json(), $response->status());
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }

    /**
     * بروكسي لجلب حالة تشغيل الأتمتة في الخلفية
     */
    public function batchStatus()
    {
        try {
            $response = Http::get("{$this->flaskUrl}/api/batch_status");
            return response()->json($response->json(), $response->status());
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }
}
