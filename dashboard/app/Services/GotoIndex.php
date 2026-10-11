<?php

namespace App\Services;

use App\Http\Controllers\SettingsController;

/**
 * «روح لـ…» (public/js/jump.js, Ctrl+K or «/» from any page): every place in the dashboard in one list, so a card,
 * a setting or a review list is one search away instead of a nav click, a tab and a scroll. No new pages: each entry
 * is a link to what is already there (a page, ?tab=, ?filter=, or a card's #id).
 *
 * An entry is [title, group, href, words, admin]. `words` are extra search words (English names, other spellings).
 * admin = true: the reviewer gets no such entry (ReviewerLimits::ADMIN_PAGES and the admin tabs of «النظام»,
 * SystemController::tabFor, keep them from the reviewer on the server too).
 * The row number and free-text entries («افتح الصف 12», «دوّر على …») are built by jump.js from what is typed.
 */
class GotoIndex
{
    public const PAGES = 'صفحات';
    public const REVIEW = 'المراجعة';
    public const RUN = 'التشغيل';
    public const HEALTH = 'النظام';
    public const SETTINGS = 'الإعدادات';

    public static function entries(bool $reviewer): array
    {
        $entries = [
            ['اليوم', self::PAGES, '/', 'home overview today run', false],
            ['قائمة المراجعة (منتج واحد)', self::PAGES, '/catalog?mode=single', 'review single', false],
            ['المراجعة بالجملة', self::PAGES, '/catalog?mode=bulk', 'review bulk grid', false],
            ['النظام: الحالة الآن', self::PAGES, '/system?tab=status', 'system health cost diagnostics الصحة والتكلفة', false],
            ['الإعدادات', self::PAGES, '/system?tab=sheet', 'settings', true],
            ['جهّز لقطة (خطوات أول مرة)', self::PAGES, '/system?tab=setup', 'setup wizard onboarding', true],
            ['فحص القص: الصور المنشورة على الغامق والفاتح', self::PAGES, '/catalog?mode=cutout', 'cutout recut background', true],

            ['مقترحة', self::REVIEW, '/catalog?filter=proposed', 'proposed suggested', false],
            ['مقترحة بلا تحذير', self::REVIEW, '/catalog?filter=eligible', 'eligible', false],
            ['فيها تحذير', self::REVIEW, '/catalog?filter=warning', 'warning', false],
            ['بلا اقتراح', self::REVIEW, '/catalog?filter=none', 'no pick none', false],
            ['ما انلقت', self::REVIEW, '/catalog?filter=not_found', 'not found', false],
            ['أعطال', self::REVIEW, '/catalog?filter=failed', 'failed errors retry', false],
            ['الخلفية لم تُعزل', self::REVIEW, '/catalog?filter=bg_failed', 'background', false],

            ['تشغيل جديد', self::RUN, '/#run-new', 'start run new', true],
            ['التشغيل الحالي: إيقاف أو متابعة', self::RUN, '/#run-current', 'run batch stop pause resume progress', true],
            ['ماركات ناقصة من جدول الماركات', self::RUN, '/#run-brands', 'brands mapping missing', true],
            ['باركودات من صفحات المتاجر', self::RUN, '/#run-barcodes', 'barcode gtin', true],
            ['جودة بيانات الشيت', self::RUN, '/#run-quality', 'sheet quality typo', true],
            ['تقرير للتحليل (تصدير التشغيل)', self::RUN, '/#run-export', 'export report json', true],

            ['الخدمات وفحص الاتصالات', self::HEALTH, '/system?tab=status#services', 'services connections check serper gemini', false],
            ['آخر تشغيل', self::HEALTH, '/system?tab=status#last-run', 'last run', false],
            ['دقة الاقتراحات الحقيقية', self::HEALTH, '/system?tab=status#lanes', 'accuracy lanes', false],
            ['عمليات البحث والتكلفة', self::HEALTH, '/system?tab=search#search-ops', 'search cost providers reasons', false],
            ['السجل', self::HEALTH, '/system?tab=logs#logs', 'log nightly', false],
            // «أدوات» بـ«النظام»: للمدير بس (SystemController::GROUPS)
            ['فحص النشر', self::HEALTH, '/system?tab=publish#publish-check', 'publish check cloudinary', true],
            ['فهرس المتاجر المحلي', self::HEALTH, '/system?tab=index#local-index', 'local index stores', true],
            ['مجموعة اختبار من مراجعاتك', self::HEALTH, '/system?tab=eval#eval-export', 'eval export', true],
            ['صور قديمة بخلفية بيضا (إعادة القص)', self::HEALTH, '/system?tab=recut#reprocess', 'recut reprocess', true],
        ];

        foreach (SettingsController::TABS as $tab => $meta) {
            $entries[] = [$meta['label'], self::SETTINGS, '/system?tab=' . $tab, $meta['hint'], true];
        }
        foreach (SettingsController::PROVIDERS as $id => $provider) {
            $entries[] = ['مفتاح ' . $provider['name'], self::SETTINGS, '/system?tab=keys#lq-key-' . $id,
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
