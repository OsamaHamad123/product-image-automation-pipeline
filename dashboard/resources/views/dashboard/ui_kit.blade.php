@extends('layouts.laqta')

@section('title', 'مكوّنات الواجهة')

@php
    $kitPalette = [
        ['name' => 'الأساسي: فيروزي الواحة (الأزرار والتحديد)', 'items' => [
            ['فيروزي 700', '--lq-teal-700', '#0F766E'],
            ['فيروزي 800 · تمرير', '--lq-teal-800', '#0B5F58'],
            ['فيروزي 500', '--lq-teal-500', '#14B8A6'],
            ['فيروزي فاتح', '--lq-teal-soft', '#E7F1F0'],
            ['فيروزي 50', '--lq-teal-50', '#F0FDFA'],
        ]],
        ['name' => 'الانتباه: زعفران (التحذيرات والعدّادات)', 'items' => [
            ['زعفران 500', '--lq-saffron-500', '#F59E0B'],
            ['زعفران 700', '--lq-saffron-700', '#B45309'],
            ['زعفران 50', '--lq-saffron-50', '#FFF7E6'],
            ['حدود التحذير', '--lq-saffron-200', '#F8DDAA'],
            ['نص على زعفران', '--lq-saffron-ink', '#2B1A02'],
        ]],
        ['name' => 'الحبر: الشريط الجانبي واللوحات الداكنة', 'items' => [
            ['حبر 900', '--lq-ink-900', '#0B2226', '#060D10'],
            ['حبر 800', '--lq-ink-800', '#12313A', '#10232A'],
            ['حبر 700', '--lq-ink-700', '#1C4450', '#1A3640'],
            ['نص الشريط', '--lq-ink-text', '#B8CDD1'],
            ['حبر 300', '--lq-ink-muted', '#8FB0B6'],
        ]],
        ['name' => 'المحايد: خلفية هادئة ونص واضح', 'items' => [
            ['خلفية', '--lq-bg', '#F3F6F8', '#0B1418'],
            ['سطح', '--lq-surface', '#FFFFFF', '#121E23'],
            ['سطح 2', '--lq-surface-2', '#F8FAFB', '#17252B'],
            ['حدود', '--lq-border', '#E2E8EC', '#24363E'],
            ['حدود قوية', '--lq-border-strong', '#CBD5DB', '#355058'],
            ['نص', '--lq-text', '#0E1C24', '#E6EEF0'],
            ['نص ثانوي', '--lq-text-2', '#4F6270', '#B4C4CA'],
            ['نص خافت', '--lq-text-3', '#5E6F7C', '#93A7AF'],
        ]],
        ['name' => 'الحالات', 'items' => [
            ['نجاح', '--lq-success', '#15803D', '#4CC97A'],
            ['تحذير', '--lq-warning', '#B45309', '#F0A84A'],
            ['خطأ', '--lq-danger', '#C8313A', '#F0646B'],
            ['معلومة', '--lq-info', '#2F6FEB', '#6FA0FF'],
            ['محايد', '--lq-neutral', '#6B7C89', '#8FA0AB'],
        ]],
    ];
    $kitPrinciples = [
        ['الصورة أولاً', 'أول شي بتشوفه هو الصورة المقترحة كبيرة، وجنبها شو بيقول الشيت. الإعدادات بمكانها، مش بطريقك.'],
        ['كل رقم من مصدر واحد', 'نفس الرقم بنفس الاسم بكل الصفحات. ما في عدّادين متناقضين.'],
        ['ولا زر بيمسح شغلك بصمت', 'الإيقاف بيوقف بس. أي شي بيحذف بيقول بالضبط شو رح ينحذف وبيطلب تأكيد.'],
        ['عربي واضح بدل الرموز', 'بدل الرموز التقنية: جملة قصيرة بتقول شو صار وشو تعمل.'],
    ];
    $kitIcons = ['home', 'review', 'run', 'health', 'settings', 'bell', 'user', 'search', 'check', 'x', 'alert', 'external',
        'refresh', 'play', 'pause', 'stop', 'grid', 'upload', 'link', 'shield', 'barcode', 'sparkle',
        'store', 'weight', 'text', 'image', 'info', 'exclamation', 'minus', 'trash', 'arrow-left'];
    $kitTypeScale = [
        ['2xs', '--lq-fs-2xs', '12', 'تلميح، مفتاح، عدّاد صغير'],
        ['xs', '--lq-fs-xs', '13', 'نص ثانوي، شارة، سطر تفاصيل، جدول'],
        ['sm', '--lq-fs-sm', '14', 'زر، حقل، وصف الصفحة'],
        ['md', '--lq-fs-md', '15', 'النص العادي'],
        ['lg', '--lq-fs-lg', '16', 'عنوان بطاقة'],
        ['xl', '--lq-fs-xl', '20', 'عنوان قسم، عنوان سؤال'],
        ['2xl', '--lq-fs-2xl', '26', 'عنوان الصفحة'],
        ['3xl', '--lq-fs-3xl', '30', 'رقم كبير بالتشغيل'],
        ['kpi', '--lq-fs-kpi', '34', 'رقم بطاقة المؤشر'],
    ];
    $kitLineHeights = [['none', '--lq-lh-none', '1'], ['tight', '--lq-lh-tight', '1.2'], ['snug', '--lq-lh-snug', '1.4'],
        ['normal', '--lq-lh-normal', '1.5'], ['base', '--lq-lh-base', '1.6'], ['relaxed', '--lq-lh-relaxed', '1.7'],
        ['loose', '--lq-lh-loose', '1.8']];
    $kitSpaces = [['--lq-space-1', '4'], ['--lq-space-2', '8'], ['--lq-space-3', '12'], ['--lq-space-4', '16'],
        ['--lq-space-5', '20'], ['--lq-space-6', '24'], ['--lq-space-8', '32'], ['--lq-space-10', '40']];
    $kitTones = [
        ['success', 'نجاح', 'متصل'],
        ['warning', 'تحذير', 'بطيء'],
        ['danger', 'خطر', 'متوقف'],
        ['info', 'معلومة', 'قيد الفحص'],
        ['neutral', 'محايد', 'ما انفحص'],
        ['teal', 'الأساسي', 'معتمد'],
    ];
    $kitBrands = [
        ['AL ALALI', '12', '100%', '76%', 'تحتاج 177 مراجعة', 'none'],
        ['MCCAIN', '6', '100%', '61%', 'تحتاج 183 مراجعة', 'none'],
        ['SUNBULAH', '3', '100%', '44%', 'تحتاج 186 مراجعة', 'none'],
        ['VIRGINIA', '4', '75%', '30%', 'دقة أقل من المطلوب', 'error'],
    ];
@endphp

@push('styles')
<style>
    .kit-toc { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: var(--lq-fs-xs); }
    .kit-sub { margin: 0; font-size: var(--lq-fs-xs); font-weight: 600; color: var(--lq-text-2); }
    .kit-note { margin: 0; font-size: var(--lq-fs-2xs); color: var(--lq-text-3); line-height: var(--lq-lh-relaxed); }
    .kit-body { font-size: var(--lq-fs-xs); line-height: var(--lq-lh-relaxed); }
    .kit-small { font-size: var(--lq-fs-xs); }
    .kit-caption { font-size: var(--lq-fs-2xs); }
    .kit-row-wide { gap: var(--lq-space-3) var(--lq-space-6); }
    .kit-tiles { gap: var(--lq-space-2); }
    .kit-input-end { text-align: end; }   /* a left-to-right value sits on the page's start side */
    .kit-brand { gap: 28px; padding: 36px 32px; border-radius: 24px; }
    .kit-brand__row { display: flex; align-items: center; gap: 20px; }
    .kit-brand__words { display: flex; flex-direction: column; gap: 6px; }
    /* the logo lock-up is the one size off the scale: it shows the mark, not a page */
    .kit-brand__name { font-family: var(--lq-font-display); font-weight: 700; font-size: 52px; line-height: var(--lq-lh-none); color: var(--lq-text-on-dark); }
    .kit-brand__latin { font-size: var(--lq-fs-md); color: var(--lq-ink-muted); letter-spacing: 0.04em; }
    .kit-brand__slogan { font-family: var(--lq-font-display); font-weight: 500; font-size: var(--lq-fs-xl); line-height: var(--lq-lh-normal); color: var(--lq-text-on-dark); }
    .kit-brand__facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; font-size: var(--lq-fs-xs); line-height: var(--lq-lh-relaxed); color: var(--lq-ink-text); }
    .kit-brand__facts strong { color: var(--lq-text-on-dark); font-weight: 600; }
    .kit-principle { display: flex; align-items: flex-start; gap: 14px; }
    .kit-principle__n { display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px; flex-shrink: 0; border-radius: var(--lq-radius-sm); background: var(--lq-accent-soft); color: var(--lq-accent); font-size: var(--lq-fs-sm); font-weight: 700; }
    .kit-principle__text { display: flex; flex-direction: column; gap: 3px; }
    .kit-principle__title { font-size: var(--lq-fs-sm); font-weight: 600; }
    .kit-principle__desc { font-size: var(--lq-fs-xs); color: var(--lq-text-2); line-height: var(--lq-lh-base); }
    .kit-theme-switch { max-width: 360px; }
    .kit-image-demo { display: flex; align-items: center; gap: var(--lq-space-3); }
    .kit-image-demo__frame { display: block; width: 64px; height: 64px; flex-shrink: 0; border: 1px solid var(--lq-border); border-radius: var(--lq-radius-md); }
    .kit-swatches { display: grid; grid-template-columns: repeat(auto-fill, minmax(118px, 1fr)); gap: 12px; }
    .kit-swatch { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
    .kit-swatch__chip { height: 52px; margin-bottom: 3px; border: 1px solid var(--lq-swatch-edge); border-radius: var(--lq-radius-md); }
    .kit-swatch__name { font-size: var(--lq-fs-2xs); font-weight: 500; }
    .kit-swatch__hex,
    .kit-swatch__token { overflow: hidden; font-size: var(--lq-fs-2xs); color: var(--lq-text-3); direction: ltr; text-align: end; white-space: nowrap; text-overflow: ellipsis; }
    .kit-radii { display: flex; flex-wrap: wrap; gap: 16px 20px; }
    .kit-radius { display: flex; flex-direction: column; align-items: center; gap: 8px; }
    .kit-radius__box { width: 64px; height: 48px; border: 1.5px solid var(--lq-accent); background: var(--lq-accent-soft); }
    .kit-spaces { display: flex; flex-direction: column; gap: var(--lq-space-2); }
    .kit-space { display: grid; grid-template-columns: 10em minmax(0, 1fr); align-items: center; gap: var(--lq-space-3); }
    .kit-space__bar { display: block; height: 12px; border-radius: var(--lq-radius-pill); background: var(--lq-accent); }
    .kit-type-list { display: flex; flex-direction: column; margin: 0; padding: 0; list-style: none; }
    .kit-type-item { display: grid; grid-template-columns: 10em minmax(0, 1fr); align-items: baseline; gap: var(--lq-space-2) var(--lq-space-4); padding-block: var(--lq-space-2); border-block-start: 1px solid var(--lq-border-soft); }
    .kit-type-item:first-child { border-block-start: 0; }
    .kit-type-item__token { font-size: var(--lq-fs-2xs); color: var(--lq-text-3); direction: ltr; text-align: end; }
    .kit-type-item__sample { min-width: 0; line-height: var(--lq-lh-snug); overflow-wrap: anywhere; }
    .kit-fs--2xs { font-size: var(--lq-fs-2xs); }
    .kit-fs--xs { font-size: var(--lq-fs-xs); }
    .kit-fs--sm { font-size: var(--lq-fs-sm); }
    .kit-fs--md { font-size: var(--lq-fs-md); }
    .kit-fs--lg { font-size: var(--lq-fs-lg); font-weight: 600; }
    .kit-fs--xl { font-size: var(--lq-fs-xl); font-weight: 600; }
    .kit-fs--2xl { font-family: var(--lq-font-display); font-size: var(--lq-fs-2xl); font-weight: 600; }
    .kit-fs--3xl { font-size: var(--lq-fs-3xl); font-weight: 600; }
    .kit-fs--kpi { font-size: var(--lq-fs-kpi); font-weight: 600; }
    .kit-lh { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: var(--lq-space-3); }
    .kit-lh__sample { max-width: 14em; margin: 0; padding: var(--lq-space-2); border-radius: var(--lq-radius-sm); background: var(--lq-accent-soft); font-size: var(--lq-fs-xs); }
    .kit-lh__sample--none { line-height: var(--lq-lh-none); }
    .kit-lh__sample--tight { line-height: var(--lq-lh-tight); }
    .kit-lh__sample--snug { line-height: var(--lq-lh-snug); }
    .kit-lh__sample--normal { line-height: var(--lq-lh-normal); }
    .kit-lh__sample--base { line-height: var(--lq-lh-base); }
    .kit-lh__sample--relaxed { line-height: var(--lq-lh-relaxed); }
    .kit-lh__sample--loose { line-height: var(--lq-lh-loose); }
    .kit-dialog-stage { display: flex; justify-content: center; padding: var(--lq-space-8) var(--lq-space-4); border-radius: var(--lq-radius-xl); background: var(--lq-scrim); }
    .kit-dialog-stage .lq-dialog { max-height: none; }
    .kit-type-row { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 8px 16px; padding-bottom: 12px; border-bottom: 1px solid var(--lq-border-soft); }
    .kit-type-display { font-family: var(--lq-font-display); font-weight: 600; font-size: var(--lq-fs-3xl); }
    .kit-dark { display: grid; grid-template-columns: repeat(auto-fill, minmax(208px, 1fr)); gap: 14px; padding: 20px; border-radius: var(--lq-radius-xl); background: var(--lq-ink-900); }
    .kit-icons { display: grid; grid-template-columns: repeat(auto-fill, minmax(92px, 1fr)); gap: 10px; }
    .kit-icon { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 14px 8px 10px; border: 1px solid var(--lq-border); border-radius: var(--lq-radius-lg); color: var(--lq-text); }
    .kit-icon__name { font-size: var(--lq-fs-2xs); color: var(--lq-text-3); direction: ltr; }
    .kit-actionbar { position: static; border: 1px solid var(--lq-border); border-radius: var(--lq-radius-lg); padding: 14px 16px; }
    .kit-toast-stack { display: flex; flex-direction: column; align-items: flex-start; gap: 10px; }
    .kit-skeleton-row { display: flex; align-items: center; gap: 12px; }
    .kit-skeleton-lines { display: flex; flex-direction: column; flex-grow: 1; gap: 8px; }
    @media (max-width: 980px) {
        .kit-swatches { grid-template-columns: repeat(auto-fill, minmax(90px, 1fr)); }
        .kit-brand { padding: 28px 22px; }
        .kit-brand__name { font-size: 40px; }
        .kit-brand__facts { grid-template-columns: minmax(0, 1fr); }
        .kit-type-item,
        .kit-space { grid-template-columns: minmax(0, 1fr); }
        .kit-type-item__token { text-align: start; }
    }
</style>
@endpush

@section('content')
    <x-lq.page-header eyebrow="مرجع التصميم · لقطة" title="مكوّنات الواجهة"
        description="كل مكوّن وكل حالة بمكان واحد. الصفحات الجديدة بتنبني من هون، وأي تعديل على الهوية بيبان هون أول.">
        <x-slot:actions>
            <x-lq.button variant="secondary" icon="play" :href="route('dashboard.batch_automation')">تشغيل جديد</x-lq.button>
            <x-lq.button :href="route('dashboard.catalog')" count="33">افتح قائمة المراجعة</x-lq.button>
        </x-slot:actions>
    </x-lq.page-header>

    <nav class="kit-toc" aria-label="أقسام الصفحة">
        <a class="lq-link" href="#kit-identity">الهوية</a>
        <a class="lq-link" href="#kit-colors">الألوان والخطوط</a>
        <a class="lq-link" href="#kit-theme">المظهر</a>
        <a class="lq-link" href="#kit-type">مقاسات الخط</a>
        <a class="lq-link" href="#kit-spacing">المسافات والزوايا</a>
        <a class="lq-link" href="#kit-buttons">الأزرار</a>
        <a class="lq-link" href="#kit-status">الحالات</a>
        <a class="lq-link" href="#kit-tones">الألوان حسب المعنى</a>
        <a class="lq-link" href="#kit-cards">البطاقات</a>
        <a class="lq-link" href="#kit-dialog">السؤال قبل التنفيذ</a>
        <a class="lq-link" href="#kit-alerts">التنبيهات</a>
        <a class="lq-link" href="#kit-numbers">الأرقام</a>
        <a class="lq-link" href="#kit-progress">التقدّم</a>
        <a class="lq-link" href="#kit-forms">النماذج</a>
        <a class="lq-link" href="#kit-table">الجدول</a>
        <a class="lq-link" href="#kit-checks">الشيت مقابل الصورة</a>
        <a class="lq-link" href="#kit-feedback">الإشعارات والتحميل</a>
        <a class="lq-link" href="#kit-runcard">بطاقة التشغيل</a>
        <a class="lq-link" href="#kit-icons">الأيقونات</a>
    </nav>

    {{-- Identity --}}
    <div class="lq-grid lq-grid--2" id="kit-identity">
        <x-lq.card variant="dark" class="kit-brand" aria-label="الشعار">
            <div class="kit-brand__row">
                <x-lq.logo :size="84" />
                <div class="kit-brand__words">
                    <span class="kit-brand__name">لقطة</span>
                    <span class="kit-brand__latin" dir="ltr">Laqta Studio</span>
                </div>
            </div>
            <p class="kit-brand__slogan">الصورة الصحيحة لكل منتج،<br>بدون تخمين.</p>
            <div class="kit-brand__facts">
                <div><strong>الاسم.</strong> «لقطة» هي الصورة اللي بنلتقطها لكل منتج: كلمة عربية قصيرة، سهلة، وبتوصف الشغل.</div>
                <div><strong>الرمز.</strong> إطار الكاميرا حول نقطة زعفران: النظام بيحصر البحث حتى يلاقي المنتج الصح بالضبط.</div>
            </div>
        </x-lq.card>

        <x-lq.card title="مبادئ التصميم">
            @foreach ($kitPrinciples as $kitIndex => $kitPrinciple)
                <div class="kit-principle">
                    <span class="kit-principle__n">{{ $kitIndex + 1 }}</span>
                    <div class="kit-principle__text">
                        <span class="kit-principle__title">{{ $kitPrinciple[0] }}</span>
                        <span class="kit-principle__desc">{{ $kitPrinciple[1] }}</span>
                    </div>
                </div>
            @endforeach
        </x-lq.card>
    </div>

    {{-- Theme: the same switch as the sidebar foot; every card below repaints with it --}}
    <x-lq.card title="المظهر" meta="فاتح، غامق، أو تلقائي (متل الجهاز)" id="kit-theme">
        <x-lq.segmented label="المظهر" :options="['light' => 'فاتح', 'dark' => 'غامق', 'system' => 'تلقائي']" value="system" size="sm" class="lq-theme-switch kit-theme-switch" data-lq-theme-switch />
        <p class="kit-note">نفس زر أسفل الشريط الجانبي (وبقائمة «حسابي» عالموبايل): بيحفظ الاختيار بهالمتصفح (<bdi dir="ltr">localStorage laqta.theme</bdi>) وبيطبّقه قبل ما ترسم الصفحة. بدّل هون وشوف كل المكوّنات تحت بالمظهر الغامق؛ الألوان بتتغيّر من المتغيّرات بس (<bdi dir="ltr">:root[data-theme="dark"]</bdi>).</p>
        <div class="kit-image-demo">
            <span class="lq-image-frame kit-image-demo__frame" aria-hidden="true"></span>
            <p class="kit-note">إطار صورة المنتج (<bdi dir="ltr">.lq-image-frame</bdi>): سطح فاتح ومربعات خلف الصور الشفافة بالمظهرين، والصورة نفسها ما بتنقلب.</p>
        </div>
    </x-lq.card>

    {{-- Colours and type --}}
    <x-lq.card title="الألوان" meta="تحت كل عيّنة اسم متغيّر CSS تبعها" id="kit-colors">
        @foreach ($kitPalette as $kitGroup)
            <div class="lq-stack lq-stack--sm">
                <h3 class="kit-sub">{{ $kitGroup['name'] }}</h3>
                <div class="kit-swatches">
                    @foreach ($kitGroup['items'] as $kitSwatch)
                        <div class="kit-swatch">
                            <span class="kit-swatch__chip" style="background: var({{ $kitSwatch[1] }})"></span>
                            <span class="kit-swatch__name">{{ $kitSwatch[0] }}</span>
                            <span class="kit-swatch__hex">{{ $kitSwatch[2] }}@isset($kitSwatch[3]) · {{ $kitSwatch[3] }}@endisset</span>
                            <span class="kit-swatch__token" title="{{ $kitSwatch[1] }}">{{ $kitSwatch[1] }}</span>
                        </div>
                    @endforeach
                </div>
            </div>
        @endforeach
        <p class="kit-note">العيّنة بتاخد لون المظهر الحالي؛ تحتها قيمة الفاتح، وبعد « · » قيمة الغامق إذا بتختلف (الفيروزي والزعفران ونصوص الشريط نفسها بالمظهرين).</p>
        <p class="kit-note">«نص خافت» أغمق بدرجة بسيطة من التصميم (<bdi dir="ltr">#6B7C89</bdi>) حتى يوصل التباين 4.5:1 على الخلفية.</p>
    </x-lq.card>

    <div class="lq-grid lq-grid--2">
        <x-lq.card title="الخطوط">
            <div class="kit-type-row">
                <span class="kit-type-display">قائمة المراجعة</span>
                <span class="lq-subtle">Alexandria · العناوين والشعار</span>
            </div>
            <div class="kit-type-row">
                <span>الصورة مطابقة للماركة والحجم. <strong>1,280 منتج · 98.4%</strong></span>
                <span class="lq-subtle">Readex Pro · الواجهة والأرقام</span>
            </div>
            <p class="kit-note">كل مقاس خط من سلّم واحد (<a class="lq-link" href="#kit-type">مقاسات الخط</a>): عنوان الصفحة، ثم عنوان القسم، ثم عنوان البطاقة، وما في صفحة بتغيّرهم.</p>
        </x-lq.card>

        <x-lq.card title="المسافات والزوايا" id="kit-spacing">
            <p class="kit-note">شبكة 8 نقاط (4، 8، 12، 16، 20، 24، 32، 40) وزوايا 8 / 10 / 12 / 16. البطاقات بحدود رفيعة وظل خفيف جداً، وكلها بنفس الحشوة (20، و16 عالموبايل).</p>
            <div class="kit-spaces">
                @foreach ($kitSpaces as $kitSpace)
                    <div class="kit-space">
                        <span class="kit-swatch__token" title="{{ $kitSpace[0] }}">{{ $kitSpace[0] }} · {{ $kitSpace[1] }}</span>
                        <span class="kit-space__bar" style="inline-size: var({{ $kitSpace[0] }})"></span>
                    </div>
                @endforeach
            </div>
            <div class="kit-radii">
                @foreach ([['--lq-radius-sm', '8'], ['--lq-radius-md', '10'], ['--lq-radius-lg', '12'], ['--lq-radius-xl', '16'], ['--lq-radius-pill', '999']] as $kitRadius)
                    <div class="kit-radius">
                        <span class="kit-radius__box" style="border-radius: var({{ $kitRadius[0] }})"></span>
                        <span class="kit-swatch__token" title="{{ $kitRadius[0] }}">{{ $kitRadius[1] }}px</span>
                    </div>
                @endforeach
            </div>
        </x-lq.card>
    </div>

    {{-- Type scale --}}
    <x-lq.card title="مقاسات الخط" meta="سلّم واحد لكل الصفحات" id="kit-type">
        <ul class="kit-type-list">
            @foreach ($kitTypeScale as $kitType)
                <li class="kit-type-item">
                    <span class="kit-type-item__token">{{ $kitType[1] }} · {{ $kitType[2] }}px</span>
                    <span class="kit-type-item__sample kit-fs--{{ $kitType[0] }}">{{ $kitType[3] }}</span>
                </li>
            @endforeach
        </ul>
        <h3 class="kit-sub">ارتفاع السطر</h3>
        <div class="kit-lh">
            @foreach ($kitLineHeights as $kitLh)
                <div class="lq-stack lq-stack--sm">
                    <span class="kit-swatch__token">{{ $kitLh[1] }} · {{ $kitLh[2] }}</span>
                    <p class="kit-lh__sample kit-lh__sample--{{ $kitLh[0] }}">الصورة مطابقة للماركة والحجم، والشيت ما حدد النوع.</p>
                </div>
            @endforeach
        </div>
        <h3 class="kit-sub">العناوين</h3>
        <div class="lq-stack lq-stack--sm">
            <span class="lq-page-title">عنوان الصفحة</span>
            <span class="lq-section-title">عنوان القسم</span>
            <span class="lq-card__title">عنوان البطاقة</span>
            <p class="lq-card__lead">سطر الشرح تحت العنوان: ما بيطول أكتر من 68 حرف تقريباً حتى يضل مريح للقراءة، مهما عرضت الشاشة.</p>
        </div>
    </x-lq.card>

    {{-- Buttons --}}
    <x-lq.card title="الأزرار" meta="أساسي، ثانوي، خطر، ناعم، شفاف · ثلاثة أحجام" id="kit-buttons">
        <h3 class="kit-sub">الأنواع</h3>
        <div class="lq-row">
            <x-lq.button>اعتماد ونشر</x-lq.button>
            <x-lq.button variant="secondary">تشغيل جديد</x-lq.button>
            <x-lq.button variant="danger">رفض</x-lq.button>
            <x-lq.button variant="danger-solid">حذف نهائي</x-lq.button>
            <x-lq.button variant="soft">اعتماد</x-lq.button>
            <x-lq.button variant="ghost">ما لقيت الصورة؟</x-lq.button>
        </div>

        <h3 class="kit-sub">الأحجام</h3>
        <div class="lq-row">
            <x-lq.button size="sm">صغير</x-lq.button>
            <x-lq.button>متوسط</x-lq.button>
            <x-lq.button size="lg">كبير</x-lq.button>
            <x-lq.button variant="secondary" size="sm" icon="refresh">تحديث</x-lq.button>
            <x-lq.button variant="secondary" icon="play">تشغيل جديد</x-lq.button>
            <x-lq.button variant="secondary" size="lg" icon="refresh">فحص الاتصالات الآن</x-lq.button>
        </div>

        <h3 class="kit-sub">شريط الإجراءات مع اختصارات لوحة المفاتيح</h3>
        <div class="lq-actionbar kit-actionbar">
            <x-lq.button size="lg" icon="check" kbd="Enter">اعتماد ونشر</x-lq.button>
            <x-lq.button variant="danger" size="lg" icon="x" kbd="X">رفض</x-lq.button>
            <x-lq.button variant="secondary" size="lg" kbd="S">تخطي</x-lq.button>
            <x-lq.button variant="ghost" size="lg">ما لقيت الصورة الصحيحة؟</x-lq.button>
            <span class="lq-actionbar__aside">
                <span>بعد الاعتماد بننتقل تلقائياً للمنتج التالي</span>
                <span>التالي: <bdi dir="ltr">VIRGINIA L/MEAT TUNA S/F OIL 170GM</bdi></span>
            </span>
        </div>

        <h3 class="kit-sub">الحالات</h3>
        <div class="lq-row">
            <x-lq.button disabled>معطّل</x-lq.button>
            <x-lq.button variant="secondary" disabled>معطّل</x-lq.button>
            <x-lq.button loading>جاري الاعتماد</x-lq.button>
            <x-lq.button variant="secondary" icon="pause">إيقاف مؤقت</x-lq.button>
            <x-lq.button variant="danger" icon="stop">إيقاف</x-lq.button>
            <x-lq.button variant="secondary" icon="refresh" label="تحديث القائمة" />
            <x-lq.button variant="ghost" icon="external" label="فتح صفحة المصدر" href="#kit-buttons" />
            <x-lq.button variant="danger-solid" icon="trash" confirm="رح تنحذف 3 صور مرفوضة من السجل ولا يمكن استرجاعها. متأكد؟">حذف 3 صور مرفوضة</x-lq.button>
        </div>
        <p class="kit-note">زر الحذف بيسأل قبل ما ينفّذ (خاصية confirm). الإيقاف بيوقف بس، ما بيحذف شي.</p>

        <h3 class="kit-sub">مفاتيح لوحة المفاتيح</h3>
        <div class="lq-row lq-muted">
            <kbd class="lq-kbd">Enter</kbd><kbd class="lq-kbd">X</kbd><kbd class="lq-kbd">S</kbd><kbd class="lq-kbd">1</kbd><kbd class="lq-kbd">5</kbd>
            <span>↑ ↓ للتنقل بين المنتجات</span>
        </div>
    </x-lq.card>

    {{-- Status --}}
    <x-lq.card title="الحالات والمرشّحات" id="kit-status">
        <h3 class="kit-sub">حالة المنتج</h3>
        <div class="lq-row lq-row--sm">
            <x-lq.chip status="proposed" />
            <x-lq.chip status="warning" />
            <x-lq.chip status="none" />
            <x-lq.chip status="not-found" />
            <x-lq.chip status="approved" />
            <x-lq.chip status="error" />
        </div>
        <div class="lq-row lq-row--sm">
            <x-lq.chip status="proposed" size="sm" />
            <x-lq.chip status="warning" size="sm">مقترحة · تحذير</x-lq.chip>
            <x-lq.chip status="none" size="sm" :dot="false" />
            <x-lq.chip status="not-found" size="sm" label="ما انلقت" />
            <x-lq.chip status="approved" size="sm" />
            <x-lq.chip status="error" size="sm" />
        </div>

        <h3 class="kit-sub">مرشّحات قائمة المراجعة</h3>
        <div class="lq-row lq-row--sm" role="group" aria-label="تصفية">
            <x-lq.filter count="33">الكل</x-lq.filter>
            <x-lq.filter :active="true" count="19">مقترحة</x-lq.filter>
            <x-lq.filter count="5">فيها تحذير</x-lq.filter>
            <x-lq.filter count="11">بلا اقتراح</x-lq.filter>
            <x-lq.filter count="3">لم يُعثر</x-lq.filter>
        </div>

        <h3 class="kit-sub">حالة الخدمات</h3>
        <div class="lq-row kit-row-wide">
            <span class="lq-status"><span class="lq-dot lq-dot--success" aria-hidden="true"></span>Google Sheet <span class="lq-status__note">متصل</span></span>
            <span class="lq-status"><span class="lq-dot lq-dot--success" aria-hidden="true"></span>Serper <span class="lq-status__note">يعمل</span></span>
            <span class="lq-status"><span class="lq-dot lq-dot--success" aria-hidden="true"></span>Gemini <span class="lq-status__note">يعمل</span></span>
            <span class="lq-status"><span class="lq-dot lq-dot--warning" aria-hidden="true"></span>PhotoRoom <span class="lq-status__note">بطيء</span></span>
            <span class="lq-status"><span class="lq-dot lq-dot--danger" aria-hidden="true"></span>Cloudinary <span class="lq-status__note">متوقف</span></span>
        </div>
    </x-lq.card>

    {{-- Tones --}}
    <x-lq.card title="الألوان حسب المعنى" meta="شارة ونص بنفس اللون لنفس المعنى بكل الصفحات" id="kit-tones">
        <h3 class="kit-sub">شارات حسب المعنى (lq-chip--success، warning، danger، info، neutral، teal)</h3>
        <div class="lq-row lq-row--sm">
            @foreach ($kitTones as $kitTone)
                <x-lq.chip :tone="$kitTone[0]">{{ $kitTone[2] }}</x-lq.chip>
            @endforeach
        </div>
        <div class="lq-row lq-row--sm">
            @foreach ($kitTones as $kitTone)
                <x-lq.chip :tone="$kitTone[0]" size="sm" :dot="false">{{ $kitTone[1] }}</x-lq.chip>
            @endforeach
        </div>
        <h3 class="kit-sub">نص حسب المعنى (lq-text--success، warning، danger، info، muted)</h3>
        <div class="lq-row kit-row-wide">
            <span class="lq-text--success">انحفظ</span>
            <span class="lq-text--warning">بدها انتباه</span>
            <span class="lq-text--danger">ما انحفظ</span>
            <span class="lq-text--info">قيد الفحص</span>
            <span class="lq-text--muted">ما في شي جديد</span>
        </div>
        <p class="kit-note">أسماء حالات المنتج (proposed، none، not-found، approved، error) لسا شغالة وبتعطي نفس الألوان.</p>
    </x-lq.card>

    {{-- Cards --}}
    <div class="lq-grid lq-grid--2" id="kit-cards">
        <x-lq.card title="بطاقة عمل (مرفوعة)" meta="lq-card">
            <p class="lq-card__lead">للكتل اللي فيها شي تعمله: زر، نموذج، قرار. بيضا بظل خفيف.</p>
            <div class="lq-row">
                <x-lq.button size="sm" icon="play">ابدأ</x-lq.button>
                <x-lq.button variant="secondary" size="sm">لاحقاً</x-lq.button>
            </div>
        </x-lq.card>
        <x-lq.card variant="flat" title="بطاقة مرجع (مسطّحة)" meta="lq-card--flat">
            <p class="lq-card__lead">للكتل اللي بتقرأها بس: شرح، ملخص، معلومة. بلا ظل وعلى سطح هادي، حتى تبيّن بطاقات العمل أوضح.</p>
        </x-lq.card>
    </div>

    {{-- Laqta.ask --}}
    <x-lq.card title="السؤال قبل التنفيذ" meta="Laqta.ask بدل window.confirm" id="kit-dialog">
        <div class="kit-dialog-stage" aria-hidden="true">
            <div class="lq-dialog">
                <span class="lq-dialog__title">نحذف 3 صور مرفوضة؟</span>
                <p class="lq-dialog__text">رح تنحذف من السجل ولا يمكن استرجاعها.</p>
                <div class="lq-dialog__actions">
                    <span class="lq-btn lq-btn--danger-solid">أكيد</span>
                    <span class="lq-btn lq-btn--secondary">لا، رجوع</span>
                </div>
            </div>
        </div>
        <div class="lq-row">
            <x-lq.button variant="secondary" size="sm" data-kit-ask>جرّب السؤال</x-lq.button>
            <span class="kit-note" data-kit-ask-out aria-live="polite"></span>
        </div>
    </x-lq.card>

    {{-- Alerts --}}
    <x-lq.card title="التنبيهات" meta="جملة وحدة واضحة بتقول شو صار وشو تعمل" id="kit-alerts">
        <x-lq.alert variant="danger" banner title="PhotoRoom بطيء اليوم:" :action-href="route('dashboard.diagnostics')" action-label="التفاصيل">
            3 صور ما انعزلت خلفيتها وانحفظت للمراجعة. الاعتماد شغال، بس تأكد من رصيد PhotoRoom.
        </x-lq.alert>
        <x-lq.alert variant="warning" title="تأكد قبل الاعتماد:">
            الصورة لبطاطا «رفيعة» والشيت ما حدد النوع. إذا المنتج عندك رفيع، اعتمد وأضف «THIN» للاسم بالشيت.
        </x-lq.alert>
        <x-lq.alert variant="info" title="معلومة:">
            النتائج بتنزل على قائمة المراجعة والتشغيل شغال. ما في داعي تستنى.
        </x-lq.alert>
        <x-lq.alert variant="success" title="تم:">
            انعتمدت 5 صور وانكتبت روابطها بالشيت.
        </x-lq.alert>
        <x-lq.alert variant="neutral">
            النشر الآلي مطفأ: كل النتائج بتستنى مراجعتك قبل ما توصل الشيت. <a class="lq-link" href="{{ route('dashboard.settings') }}">تغيير</a>
        </x-lq.alert>
    </x-lq.card>

    {{-- Numbers --}}
    <div class="lq-stack" id="kit-numbers">
        <div class="lq-grid lq-grid--4">
            <x-lq.kpi label="بانتظار مراجعتك" value="33" note="19 منها مقترحة وجاهزة" tone="success" dot="saffron" :href="route('dashboard.catalog')" />
            <x-lq.kpi label="منشورة بالشيت" value="18" note="كلها معتمدة من مراجع" dot="teal" />
            <x-lq.kpi label="ما انلقت إلها صورة" value="3" note="بدها بحث بكلمات أخرى" dot="info" />
            <x-lq.kpi label="أعطال مؤقتة" value="7" note="بتنعاد بالتشغيل الجاي" tone="danger" dot="danger" :href="route('dashboard.diagnostics')" />
        </div>

        <div class="lq-grid lq-grid--2">
            <x-lq.card title="آخر تشغيل" meta="اليوم 09:40 · الصفوف 2–61 · 21 دقيقة · 0.19 دولار">
                <div class="lq-grid lq-grid--4 kit-tiles">
                    <x-lq.stat tone="success" value="32" label="مقترحة" />
                    <x-lq.stat tone="muted" value="17" label="بلا اقتراح" />
                    <x-lq.stat tone="info" value="3" label="ما انلقت" />
                    <x-lq.stat tone="danger" value="8" label="أعطال مؤقتة" />
                </div>
                <p class="lq-muted kit-body">الأعطال المؤقتة الثمانية (الصفوف 46–53) سببها انقطاع الإنترنت. رجعت للطابور وبتنعاد لحالها بالتشغيل الجاي.</p>
            </x-lq.card>

            <x-lq.card title="التكلفة التقديرية">
                <x-slot:actions><a class="lq-link" href="{{ route('dashboard.settings') }}">الأسعار</a></x-slot:actions>
                <span class="lq-kpi__value lq-ltr">$0.19</span>
                <div class="lq-stack lq-stack--sm lq-muted kit-small">
                    <div class="lq-row lq-row--between"><span>Serper · 118 استعلام</span><span class="lq-num lq-ltr">$0.12</span></div>
                    <div class="lq-row lq-row--between"><span>Gemini · 70 فحص</span><span class="lq-num lq-ltr">$0.07</span></div>
                </div>
                <x-slot:footer>التكلفة لكل منتج تقريباً <bdi dir="ltr">$0.003</bdi>.</x-slot:footer>
            </x-lq.card>
        </div>
    </div>

    {{-- Progress --}}
    <div class="lq-grid lq-grid--2" id="kit-progress">
        <x-lq.card title="التقدّم">
            <x-lq.progress :value="24" :max="40" label="تقدّم التشغيل" title="الصفوف 62–101" meta="24 / 40" note="متبقي تقريباً 6 دقائق" />
            <x-lq.progress label="نتائج التشغيل الحالي" :max="40" size="lg" title="الصفوف 62–101" meta="24 من 40" note="متبقي تقريباً 6 دقائق" :segments="[
                ['label' => 'مقترحة', 'value' => 13, 'tone' => 'success'],
                ['label' => 'بلا اقتراح', 'value' => 7, 'tone' => 'muted'],
                ['label' => 'ما انلقت', 'value' => 2, 'tone' => 'info'],
                ['label' => 'أعطال مؤقتة', 'value' => 2, 'tone' => 'danger'],
            ]" />
            <h3 class="kit-sub">الألوان والأحجام</h3>
            <x-lq.progress :value="76" size="sm" tone="teal" label="AL ALALI" />
            <x-lq.progress :value="45" tone="warning" label="بطيء" />
            <x-lq.progress :value="30" size="lg" tone="danger" label="دقة أقل من المطلوب" />
            <x-lq.progress :value="0" label="لم يبدأ" />
        </x-lq.card>

        <x-lq.card title="وين وصلت منتجات الشيت" meta="100 منتج">
            <x-lq.progress label="منتجات الشيت" size="xl" :segments="[
                ['label' => 'منشورة', 'value' => 18, 'tone' => 'teal'],
                ['label' => 'بانتظار مراجعتك', 'value' => 33, 'tone' => 'warning'],
                ['label' => 'ما انلقت', 'value' => 3, 'tone' => 'info'],
                ['label' => 'أعطال مؤقتة', 'value' => 7, 'tone' => 'danger'],
                ['label' => 'لسا ما انبحث عنها', 'value' => 39, 'tone' => 'empty'],
            ]" />
            <h3 class="kit-sub">جاهزية النشر الآلي</h3>
            <x-lq.progress :value="6" size="sm" label="AL ALALI" title="AL ALALI" meta="12 مراجعة صحيحة · مضمون 76%" />
            <x-lq.progress :value="2" size="sm" tone="danger" label="VIRGINIA" title="VIRGINIA" meta="3 من 4 صحيحة · دقة أقل من المطلوب" />
        </x-lq.card>
    </div>

    {{-- Forms --}}
    <div class="lq-grid lq-grid--2" id="kit-forms">
        <x-lq.card title="الحقول">
            <label class="lq-field">
                <span class="lq-field__label">الصفوف</span>
                <input class="lq-input kit-input-end" type="text" value="62-101" dir="ltr" inputmode="numeric">
                <span class="lq-field__hint">مثلاً 62-101 أو 5، 9، 14</span>
            </label>
            <label class="lq-field">
                <span class="lq-field__label">الماركة</span>
                <select class="lq-select">
                    <option>AL ALALI (6 منتجات)</option>
                    <option>VIRGINIA (4 منتجات)</option>
                    <option>MCCAIN (2 منتج)</option>
                </select>
            </label>
            <label class="lq-search">
                <x-lq.icon name="search" :size="18" />
                <span class="lq-sr-only">بحث</span>
                <input class="lq-search__input" type="search" placeholder="اسم، باركود، أو رقم صف">
            </label>
            <label class="lq-field">
                <span class="lq-field__label">رابط صورة من موقع</span>
                <input class="lq-input" type="url" dir="ltr" placeholder="https://" aria-invalid="true" aria-describedby="kit-url-error">
                <span class="lq-field__error" id="kit-url-error">الرابط لازم يبدأ بـ <bdi dir="ltr">https://</bdi></span>
            </label>
            <label class="lq-field">
                <span class="lq-field__label">ملاحظة للمراجع</span>
                <textarea class="lq-textarea" placeholder="اكتب ملاحظة قصيرة"></textarea>
            </label>
            <label class="lq-field">
                <span class="lq-field__label">حقل معطّل</span>
                <input class="lq-input" type="text" value="gemini-3.1-flash-lite" dir="ltr" disabled>
            </label>
        </x-lq.card>

        <x-lq.card title="الاختيارات">
            <h3 class="kit-sub">مربعات الاختيار</h3>
            <label class="lq-check"><input type="checkbox" checked> إعادة البحث حتى للمنتجات اللي إلها صورة نهائية</label>
            <label class="lq-check"><input type="checkbox"> تجاهل النتائج المحفوظة والبحث من جديد</label>
            <label class="lq-check"><input type="checkbox" disabled> <span>خيار غير متاح الآن</span></label>

            <h3 class="kit-sub">مفتاح التشغيل</h3>
            <div class="lq-row kit-row-wide">
                <x-lq.switch label="تفعيل النشر الآلي" />
                <x-lq.switch label="عزل الخلفية" checked />
                <x-lq.switch label="الرجوع للنظام القديم" disabled />
                <x-lq.switch label="عزل الخلفية بـ PhotoRoom" show-label checked />
            </div>

            <h3 class="kit-sub">اختيار واحد من عدة</h3>
            <x-lq.segmented label="النطاق" name="kit_scope" fill value="rows" :options="['all' => 'كل الشيت', 'brand' => 'ماركة', 'rows' => 'صفوف محددة']" />
            <div class="lq-row">
                <x-lq.segmented label="الفترة" size="sm" value="24h" :options="['24h' => 'آخر 24 ساعة', '7d' => 'آخر 7 أيام']" id="kit-period" />
                <span class="kit-note" data-kit-period-out aria-live="polite">المختار: 24h</span>
            </div>
            <nav class="lq-segmented" aria-label="طريقة العرض">
                <a class="lq-segmented__item" href="{{ route('dashboard.catalog') }}">منتج واحد</a>
                <a class="lq-segmented__item" href="#kit-forms" aria-current="page">بالجملة</a>
            </nav>

            <h3 class="kit-sub">روح على (روابط لكروت الصفحة)</h3>
            <x-lq.jump-links :links="[['#kit-forms', 'النماذج'], ['#kit-forms', 'للمدير بس', true]]" />
        </x-lq.card>
    </div>

    {{-- Table --}}
    <x-lq.card title="الجدول" meta="الماركات الجاهزة للنشر الآلي" id="kit-table">
        <div class="lq-table-wrap">
            <table class="lq-table">
                <thead>
                    <tr><th scope="col">الماركة</th><th scope="col">مراجعات الاقتراح</th><th scope="col">الدقة</th><th scope="col">أقل دقة متوقعة</th><th scope="col">الحالة</th></tr>
                </thead>
                <tbody>
                    @foreach ($kitBrands as $kitBrand)
                        <tr>
                            <td class="lq-table__strong"><bdi dir="ltr">{{ $kitBrand[0] }}</bdi></td>
                            <td class="lq-table__num">{{ $kitBrand[1] }}</td>
                            <td class="lq-table__num">{{ $kitBrand[2] }}</td>
                            <td class="lq-table__num">{{ $kitBrand[3] }}</td>
                            <td><x-lq.chip :status="$kitBrand[5]" :dot="false" size="sm">{{ $kitBrand[4] }}</x-lq.chip></td>
                        </tr>
                    @endforeach
                </tbody>
            </table>
        </div>
        <x-slot:footer>المعيار: الحد الأدنى المضمون لدقة الاقتراح لازم يوصل 98%. لما توصل ماركة، بيطلع زر «تفعيل» جنبها.</x-slot:footer>
    </x-lq.card>

    {{-- Sheet vs image --}}
    <div class="lq-grid lq-grid--2" id="kit-checks">
        <x-lq.card title="الشيت مقابل الصورة" padding="compact">
            <x-slot:actions><span class="lq-row lq-row--sm lq-muted kit-caption"><x-lq.icon name="sparkle" :size="14" />قراءة الملصق</span></x-slot:actions>
            <div class="lq-checks">
                <x-lq.check-row label="الماركة" sheet="SUNBULAH" image="Sunbulah" status="match" />
                <x-lq.check-row label="الحجم" sheet="1 كغ" image="1 kg" status="match" />
                <x-lq.check-row label="النوع" sheet="غير محدد" image="Thin French Fries" status="unsure" />
                <x-lq.check-row label="الزاوية" sheet="واجهة المنتج" image="front packshot" status="match" />
            </div>
            <x-lq.alert variant="warning" title="تأكد قبل الاعتماد:">
                الصورة لبطاطا «رفيعة» والشيت ما حدد النوع. إذا المنتج عندك رفيع، اعتمد وأضف «THIN» للاسم بالشيت.
            </x-lq.alert>
        </x-lq.card>

        <x-lq.card title="كل حالات المطابقة" padding="compact">
            <x-lq.check-row boxed label="الحجم" sheet="1 كغ" image="1 kg" status="match" />
            <x-lq.check-row boxed label="الحجم" sheet="1 كغ" image="2.5 kg" status="mismatch" />
            <x-lq.check-row boxed label="النوع" sheet="غير محدد" image="Crinkle Cut" status="unsure" />
            <x-lq.check-row boxed label="الباركود" sheet="غير موجود بالشيت" image="" status="unknown" />
        </x-lq.card>
    </div>

    {{-- Toasts, empty state, skeleton --}}
    <div class="lq-grid lq-grid--2" id="kit-feedback">
        <x-lq.card title="الإشعارات">
            <div class="kit-toast-stack">
                <div class="lq-toast"><span class="lq-spinner" aria-hidden="true"></span><span class="lq-toast__text">جاري اعتماد 5 صور بالخلفية · <strong>2 من 5</strong> جاهزة. بتقدر تكمل شغلك.</span></div>
                <div class="lq-toast lq-toast--success"><x-lq.icon name="check" :size="18" :stroke="2" class="lq-toast__icon" /><span class="lq-toast__text">انعتمدت الصورة وانكتب رابطها بالشيت.</span><button type="button" class="lq-toast__close" aria-label="سكّر"><x-lq.icon name="x" :size="16" :stroke="2" /></button></div>
                <div class="lq-toast lq-toast--danger"><x-lq.icon name="alert" :size="18" :stroke="2" class="lq-toast__icon" /><span class="lq-toast__text">ما قدرنا نرفع الصورة على Cloudinary. جرّب كمان مرة.</span></div>
            </div>
            <div class="lq-row">
                <x-lq.button variant="secondary" size="sm" data-kit-toast="success">اعرض إشعار نجاح</x-lq.button>
                <x-lq.button variant="secondary" size="sm" data-kit-toast="danger">اعرض إشعار خطأ</x-lq.button>
            </div>
        </x-lq.card>

        <div class="lq-stack">
            <x-lq.empty-state icon="review" title="ما في منتجات بانتظار مراجعتك" text="كل الصور المقترحة انعتمدت أو انرفضت. شغّل بحث جديد لتجيب نتائج جديدة.">
                <x-lq.button variant="secondary" icon="play" :href="route('dashboard.batch_automation')">تشغيل جديد</x-lq.button>
            </x-lq.empty-state>

            <x-lq.card title="جاري التحميل" padding="compact">
                <div class="lq-stack lq-stack--sm" aria-busy="true">
                    <span class="lq-sr-only">جارٍ تحميل القائمة…</span>
                    @for ($kitI = 0; $kitI < 3; $kitI++)
                        <div class="kit-skeleton-row" aria-hidden="true">
                            <span class="lq-skeleton lq-skeleton--thumb"></span>
                            <div class="kit-skeleton-lines">
                                <span class="lq-skeleton lq-skeleton--text"></span>
                                <span class="lq-skeleton lq-skeleton--short"></span>
                            </div>
                        </div>
                    @endfor
                </div>
            </x-lq.card>
        </div>
    </div>

    {{-- Run card --}}
    <x-lq.card title="بطاقة التشغيل بالشريط الجانبي" meta="بتتحدّث كل 5 ثواني من حالة التشغيل" id="kit-runcard">
        <div class="kit-dark">
            <x-lq.run-card state="idle" />
            <x-lq.run-card state="running" text="الصفوف 62–101" count="24 / 40" :progress="60" />
            <x-lq.run-card state="paused" text="الصفوف 62–101" count="24 / 40" :progress="60" />
            <x-lq.run-card state="error" text="رصيد Serper انتهى أو المفتاح مرفوض." />
            <x-lq.run-card state="unknown" />
        </div>
        <x-lq.card variant="dark" padding="compact" title="السجل" level="3">
            <pre class="lq-log">10:31:04  row 85  TASTY FOOD MEAT MASALA 160GM  → REVIEW_PRESELECTED
10:30:44  row 84  SUPER T SOLID TUNA SUNFL OIL 3X185GM  → REVIEW_UNSELECTED
<span class="lq-log__error">10:29:12  photoroom  timeout after 30s (row 81), kept for review</span></pre>
        </x-lq.card>
    </x-lq.card>

    {{-- Icons --}}
    <x-lq.card title="الأيقونات" meta="خط 1.8 · مربع 24 · بلون النص" id="kit-icons">
        <div class="kit-icons">
            @foreach ($kitIcons as $kitIcon)
                <div class="kit-icon">
                    <x-lq.icon :name="$kitIcon" :size="24" />
                    <span class="kit-icon__name">{{ $kitIcon }}</span>
                </div>
            @endforeach
        </div>
    </x-lq.card>
@endsection

@push('scripts')
<script>
    (function () {
        var messages = {
            success: 'انعتمدت الصورة وانكتب رابطها بالشيت.',
            danger: 'ما قدرنا نرفع الصورة على Cloudinary. جرّب كمان مرة.'
        };
        document.querySelectorAll('[data-kit-toast]').forEach(function (button) {
            button.addEventListener('click', function () {
                var variant = button.getAttribute('data-kit-toast');
                window.Laqta.toast(messages[variant], { variant: variant });
            });
        });
        var askButton = document.querySelector('[data-kit-ask]');
        var askOut = document.querySelector('[data-kit-ask-out]');
        if (askButton && askOut) {
            askButton.addEventListener('click', function () {
                window.Laqta.ask({ title: 'نحذف 3 صور مرفوضة؟', text: 'رح تنحذف من السجل ولا يمكن استرجاعها.', danger: true })
                    .then(function (ok) { askOut.textContent = ok ? 'اخترت: أكيد' : 'اخترت: لا، رجوع'; });
            });
        }
        var period = document.getElementById('kit-period');
        var out = document.querySelector('[data-kit-period-out]');
        if (period && out) {
            period.addEventListener('lq:change', function (e) {
                out.textContent = 'المختار: ' + e.detail.value;
            });
        }
    })();
</script>
@endpush
