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
            
            // إرسال الطلب لـ Flask مع إرفاق الملف كـ Multipart
            $response = Http::attach(
                'file', 
                file_get_contents($file->getPathname()), 
                $file->getClientOriginalName()
            )->post("{$this->flaskUrl}/api/upload_manual_image", $request->only([
                'row_number', 'product_name', 'brand', 'barcode'
            ]));

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
            $response = Http::get("{$this->flaskUrl}/api/logs");
            return response()->json($response->json(), $response->status());
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
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
