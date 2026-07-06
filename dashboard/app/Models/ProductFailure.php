<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class ProductFailure extends Model
{
    // اسم الجدول في قاعدة البيانات
    protected $table = 'product_failures';

    // الباركود هو المفتاح الرئيسي
    protected $primaryKey = 'barcode';

    // المفتاح الرئيسي ليس زيادة تلقائية (Auto-increment) لأنه نص
    public $incrementing = false;

    // نوع المفتاح الرئيسي نص
    protected $keyType = 'string';

    // تعطيل إدارة الطوابع الزمنية الافتراضية للارافيل
    public $timestamps = false;

    // الحقول المسموح بتعديلها وإدخالها
    protected $fillable = [
        'barcode',
        'product_name',
        'brand',
        'error_message',
        'failed_at'
    ];

    protected $casts = [
        'failed_at' => 'datetime'
    ];
}
