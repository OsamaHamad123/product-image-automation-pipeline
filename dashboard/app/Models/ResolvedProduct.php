<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class ResolvedProduct extends Model
{
    // اسم الجدول في قاعدة البيانات
    protected $table = 'resolved_products';

    // مفتاح الحقل الفريد التلقائي الزيادة
    protected $primaryKey = 'id';

    // تعطيل إدارة الطوابع الزمنية الافتراضية للارافيل (created_at و updated_at)
    // لأن الجدول يستخدم resolved_at فقط
    public $timestamps = false;

    // الحقول المسموح بتعديلها وإدخالها
    protected $fillable = [
        'barcode',
        'product_name',
        'brand',
        'original_url',
        'cloudinary_url',
        'clip_score',
        'metadata_json',
        'clip_embedding_json',
        'resolved_at'
    ];

    // تحويل الحقول النصية لـ JSON بشكل تلقائي
    protected $casts = [
        'metadata_json' => 'array',
        'clip_embedding_json' => 'array',
        'clip_score' => 'float',
        'resolved_at' => 'datetime'
    ];
}
