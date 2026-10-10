<?php

namespace App\Services;

use App\Http\Controllers\SettingsController;

/**
 * «روح لـ…» (public/js/jump.js, Ctrl+K or «/» from any page): every place in the dashboard in one list, so a card,
 * a setting or a review list is one search away instead of a nav click, a tab and a scroll. No new pages: each entry
 * is a link to what is already there (a page, ?tab=, ?filter=, or a card's #id).
 *
 * An entry is [title, group, href, words, admin]. `words` are extra search words (English names, other spellings).
 * admin = true: the reviewer gets no such entry (ReviewerLimits::ADMIN_PAGES blocks those pages on the server too).
 * The row number and free-text entries («افتح الصف 12», «دوّر على …») are built by jump.js from what is typed.
 */
class GotoIndex
{
    public const PAGES = 'صفحات';
    public const REVIEW = 'المراجعة';
    public const RUN = 'التشغيل';
    public const HEALTH = 'الصحة والتكلفة';
    public const SETTINGS = 'الإعدادات';

    public static function entries(bool $reviewer): array
    {
        $entries = [
            ['الرئيسية', self::PAGES, '/', 'home overview', false],
            ['قائمة المراجعة (منتج واحد)', self::PAGES, '/catalog?mode=single', 'review single', false],
            ['المراجعة بالجملة', self::PAGES, '/catalog?mode=bulk', 'review bulk grid', false],
            ['التشغيل', self::PAGES, '/batch-automation', 'run batch', true],
            ['الصحة والتكلفة', self::PAGES, '/system-diagnostics', 'health cost diagnostics', false],
            ['الإعدادات', self::PAGES, '/settings', 'settings', true],
            ['جهّز لقطة (خطوات أول مرة)', self::PAGES, '/setup', 'setup wizard onboarding', true],
            ['فحص القص: الصور المنشورة على الغامق والفاتح', self::PAGES, '/cutout-check', 'cutout recut background', true],

            ['مقترحة', self::REVIEW, '/catalog?filter=proposed', 'proposed suggested', false],
            ['مقترحة بلا تحذير', self::REVIEW, '/catalog?filter=eligible', 'eligible', false],
            ['فيها تحذير', self::REVIEW, '/catalog?filter=warning', 'warning', false],
            ['بلا اقتراح', self::REVIEW, '/catalog?filter=none', 'no pick none', false],
            ['ما انلقت', self::REVIEW, '/catalog?filter=not_found', 'not found', false],
            ['أعطال', self::REVIEW, '/catalog?filter=failed', 'failed errors retry', false],
            ['الخلفية لم تُعزل', self::REVIEW, '/catalog?filter=bg_failed', 'background', false],

            ['تشغيل جديد', self::RUN, '/batch-automation#run-new', 'start run new', true],
            ['التشغيل الحالي: إيقاف أو متابعة', self::RUN, '/batch-automation#run-current', 'stop pause resume progress', true],
            ['ماركات ناقصة من جدول الماركات', self::RUN, '/batch-automation#run-brands', 'brands mapping missing', true],
            ['باركودات من صفحات المتاجر', self::RUN, '/batch-automation#run-barcodes', 'barcode gtin', true],
            ['جودة بيانات الشيت', self::RUN, '/batch-automation#run-quality', 'sheet quality typo', true],
            ['تقرير للتحليل (تصدير التشغيل)', self::RUN, '/batch-automation#run-export', 'export report json', true],

            ['الخدمات وفحص الاتصالات', self::HEALTH, '/system-diagnostics#services', 'services connections check serper gemini', false],
            ['فحص النشر', self::HEALTH, '/system-diagnostics#publish-check', 'publish check cloudinary', false],
            ['آخر تشغيل', self::HEALTH, '/system-diagnostics#last-run', 'last run', false],
            ['دقة الاقتراحات الحقيقية', self::HEALTH, '/system-diagnostics#lanes', 'accuracy lanes', false],
            ['فهرس المتاجر المحلي', self::HEALTH, '/system-diagnostics#local-index', 'local index stores', false],
            ['عمليات البحث والتكلفة', self::HEALTH, '/system-diagnostics#search-ops', 'search cost providers reasons', false],
            ['السجل', self::HEALTH, '/system-diagnostics#logs', 'log nightly', false],
            ['مجموعة اختبار من مراجعاتك', self::HEALTH, '/system-diagnostics#eval-export', 'eval export', true],
            ['صور قديمة بخلفية بيضا (إعادة القص)', self::HEALTH, '/system-diagnostics#reprocess', 'recut reprocess', true],
        ];

        foreach (SettingsController::TABS as $tab => $meta) {
            $entries[] = [$meta['label'], self::SETTINGS, '/settings?tab=' . $tab, $meta['hint'], true];
        }
        foreach (SettingsController::PROVIDERS as $id => $provider) {
            $entries[] = ['مفتاح ' . $provider['name'], self::SETTINGS, '/settings?tab=keys#lq-key-' . $id,
                          $provider['use'] . ' key api', true];
        }

        $out = [];
        foreach ($entries as [$title, $group, $href, $words, $admin]) {
            if ($admin && $reviewer) {
                continue;
            }
            $out[] = ['title' => $title, 'group' => $group, 'href' => $href, 'words' => $words];
        }
        return $out;
    }
}
