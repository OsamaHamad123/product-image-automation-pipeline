{{--
    هيكل شاشة المراجعة قبل تشغيل السكربت: نفس تقسيم التصميم مع هياكل تحميل، وعدد «بانتظار المراجعة» من الخادم
    (نفس رقم الشارة). app.js يستبدل ما بداخل #rvApp بالواجهة الحية.
    $config: ReviewController::page (mode, filter, row, canvas, db, readyForReview, urls, cutout, sheet)    $dbOnline: bool
    sheet: «الشيت: X · الورقة: Y» under the titles (RunController::sheetLine; app.js R.sheetLine draws it again)
    mode: single | bulk | cutout («فحص القص»، للمدير بس)
--}}
@php
    $rvReady = $config['readyForReview'] ?? null;
    $rvCount = $rvReady === null ? '—' : $rvReady . ' بانتظار المراجعة';
@endphp
<div id="rvApp" class="rv-app" data-mode="{{ $config['mode'] }}" data-config="{{ json_encode($config, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) }}" aria-busy="true">
    @if ($config['mode'] === 'cutout')
        {{-- «فحص القص» (المدير بس): cutout.js بيبني الواجهة بمكانه (R.cutout.mount) --}}
        <section class="cq-mode" aria-label="فحص القص">
            <div class="cq">
                <header class="cq__header">
                    <div class="cq__titles">
                        <h1 class="cq__h1">فحص القص</h1>
                    </div>
                </header>
                <div class="cq__grid" aria-hidden="true">
                    @for ($i = 0; $i < 4; $i++)
                        <div class="cq-card cq-card--skeleton"><span class="lq-skeleton lq-skeleton--text"></span><div class="cq-stages"><span class="lq-skeleton cq-stage"></span><span class="lq-skeleton cq-stage"></span><span class="lq-skeleton cq-stage"></span></div></div>
                    @endfor
                </div>
            </div>
        </section>
    @elseif ($config['mode'] === 'bulk')
        <section class="rv-bulk" aria-label="المراجعة بالجملة">
            <div class="rv-bulk__top">
                <header class="rv-bulk__head">
                    <div class="rv-bulk__titlebox">
                        <div class="rv-bulk__titles">
                            <h1 class="rv-bulk__h1">قائمة المراجعة</h1>
                            <span class="rv-bulk__count">{{ $rvCount }}</span>
                        </div>
                        <x-lq.sheet-line :line="$config['sheet'] ?? null" class="rv-sheetline" />
                    </div>
                </header>
                @unless ($dbOnline)
                    <x-lq.alert variant="danger" title="قاعدة البيانات مش متاحة:">ما بنقدر نعرف مين بانتظار المراجعة ولا نجيب الصور المقترحة هلق. قاعدة البيانات مش شغّالة. بلّغ المطوّر.</x-lq.alert>
                @endunless
            </div>
            <div class="rv-cards" aria-hidden="true">
                @for ($i = 0; $i < 4; $i++)
                    <div class="rv-card rv-card--skeleton"><div class="lq-skeleton rv-card__skel"></div><div class="rv-card__body"><span class="lq-skeleton lq-skeleton--text"></span><span class="lq-skeleton lq-skeleton--short"></span></div></div>
                @endfor
            </div>
        </section>
    @else
        <div class="rv-single">
            <section class="rv-ws" aria-label="مساحة المراجعة">
                <div class="rv-ws__body">
                    @unless ($dbOnline)
                        <x-lq.alert variant="danger" title="قاعدة البيانات مش متاحة:">ما بنقدر نعرف مين بانتظار المراجعة ولا نجيب الصور المقترحة هلق. قاعدة البيانات مش شغّالة. بلّغ المطوّر.</x-lq.alert>
                    @endunless
                    <div class="rv-skeleton" aria-hidden="true">
                        <div class="rv-panel rv-skeleton__head"><span class="lq-skeleton lq-skeleton--title"></span><span class="lq-skeleton lq-skeleton--short"></span></div>
                        <div class="rv-compare"><div class="lq-skeleton rv-skeleton__pick"></div><div class="lq-skeleton rv-skeleton__side"></div></div>
                    </div>
                </div>
            </section>
            <aside class="rv-queue" aria-label="قائمة المراجعة">
                <div class="rv-queue__head">
                    <div class="rv-queue__title">
                        <h1 class="rv-queue__h1">قائمة المراجعة</h1>
                        <span class="rv-queue__count">{{ $rvCount }}</span>
                    </div>
                    <x-lq.sheet-line :line="$config['sheet'] ?? null" class="rv-sheetline" />

                </div>
                <ul class="rv-list" aria-hidden="true">
                    @for ($i = 0; $i < 6; $i++)
                        <li class="rv-item rv-item--skeleton"><span class="lq-skeleton lq-skeleton--thumb"></span><span class="rv-item__text"><span class="lq-skeleton lq-skeleton--text"></span><span class="lq-skeleton lq-skeleton--short"></span></span></li>
                    @endfor
                </ul>
            </aside>
        </div>
    @endif
    <noscript>
        <x-lq.alert variant="warning" title="المراجعة بتحتاج JavaScript:">فعّل JavaScript بالمتصفح وحدّث الصفحة.</x-lq.alert>
    </noscript>
</div>
