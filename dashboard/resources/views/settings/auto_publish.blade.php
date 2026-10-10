{{--
    Settings · النشر الآلي (was /active-learning). $autoPublish = SettingsController::autoPublishData() over the
    review_stats bridge action (local_cache_db.review_stats, the same numbers as scripts/review_stats.py):
    per brand the reviewed suggestions (prechecked), precision, Wilson 95% lower bound and status.
    The global switch (AUTO_PUBLISH_ENABLED) cannot be turned on without a ready brand in AUTO_PUBLISH_BRANDS;
    «تفعيل» adds a ready brand, «إيقاف» removes any entry. The thresholds come from the bridge, never from here.
    «النشر الآلي لكل الماركات المؤكدة» ($autoPublish['lane'] = SettingsController::laneData, review_stats.lanes): the
    reviews of the strict lane (a pick every auto-publish rule passed but the brand setting) and its own switch
    (section strict-lane), on by default and free to switch either way: the worker publishes nothing from the lane
    before it is ready (catalog_match.decide.strict_lane_readiness), and it does not need the switch above (that one
    is the brand table's). $lane['state'] says whether it is on and after how many more approvals it publishes by
    itself; $lane['how'] how to switch it off (or back on). The unsure lane is information only.
--}}
@php
    $ap = $autoPublish;
    $readyNames = implode('، ', $ap['ready_listed']);
    $switchOnText = 'رح تنرفع صور الماركات المفعّلة (' . $readyNames . ') وتنكتب بالشيت بدون مراجعتك من التشغيل الجاي. باقي الماركات بتضل تستنى مراجعتك.';
    $switchOffText = 'صور الماركات المفعّلة بالجدول رح ترجع تستنى مراجعتك قبل ما توصل الشيت. قائمة الماركات المفعّلة بتضل محفوظة. (النشر الآلي لكل الماركات المؤكدة إله مفتاحه لحاله تحت.)';
    $listedCount = count($ap['listed']);
    $lane = $ap['lane'];
    $laneOnText = 'الاقتراحات اللي بتعدّي كل قواعد النشر الآلي (قارئ الملصق أكّده، متجر موثوق، بلا أي تعارض) لأي ماركة مربوطة رح تنرفع وتنكتب بالشيت بدون مراجعتك، حتى لو الماركة مش مفعّلة بالجدول، بس بعد ما تثبت دقة هالفئة بمراجعاتك. لحد هداك الوقت كل شي بيستنى مراجعتك.';
    $laneOffText = 'كل الاقتراحات المؤكدة رح ترجع تستنى مراجعتك. بس الماركات المفعّلة بالجدول بتضل تنرفع بدون مراجعة.';
@endphp
<section id="lq-settings-auto-publish" class="lq-card lq-settings-card" aria-labelledby="lq-settings-ap-title">
    <div class="lq-autopub__head">
        <div class="lq-settings-card__head">
            <h2 class="lq-section-title" id="lq-settings-ap-title">النشر الآلي</h2>
            <p class="lq-settings-card__intro">لما يكون مفعّل لماركة، الصورة المقترحة اللي Gemini أكدها ومن متجر موثوق بتنرفع وبتنكتب بالشيت بدون مراجعة. الماركة بتصير جاهزة بس لما تثبت دقتها بالمراجعات الحقيقية.</p>
        </div>
        <form method="POST" action="{{ route('dashboard.save_settings') }}" class="lq-autopub__switch" data-autopub-form>
            @csrf
            <input type="hidden" name="section" value="auto-publish">
            <x-lq.switch name="auto_publish_enabled" value="true" label="تفعيل النشر الآلي" :checked="$ap['enabled']"
                :disabled="(bool) $dbError || (!$ap['enabled'] && !$ap['can_enable'])"
                data-autopub-switch :data-confirm-on="$switchOnText" :data-confirm-off="$switchOffText" />
            <button type="submit" class="lq-btn lq-btn--secondary lq-btn--sm lq-autopub__save" data-autopub-save>حفظ</button>
        </form>
    </div>

    @if ($ap['status'] === 'error')
        <x-lq.alert variant="danger" title="ما قدرنا نحسب دقة الماركات:" :action-href="route('dashboard.settings') . '?tab=auto-publish'" action-label="جرّب مرة تانية">ما قدرنا نوصل لبيانات النظام، فالتفعيل موقّف لحتى نقدر نتأكد. جرّب بعد شوي، وإذا ضل بلّغ المطوّر.</x-lq.alert>
    @elseif (!$ap['enabled'] && !$ap['can_enable'])
        <p class="lq-autopub__hint">المفتاح بيتفعّل لما تفعّل ماركة جاهزة وحدة على الأقل من الجدول.</p>
    @endif
    @if ($ap['enabled'] && $ap['ready_listed'] === [] && $listedCount === 0)
        <x-lq.alert variant="warning" title="النشر الآلي شغّال بلا ماركات:">ما في ولا ماركة مفعّلة، فما رح ينرفع شي بدون مراجعة.</x-lq.alert>
    @endif
    @if (collect($ap['rows'])->contains('unproven', true))
        <x-lq.alert variant="warning" title="في ماركات مفعّلة بدون دليل كافٍ:">صورها بتنرفع بدون مراجعة @if (!$ap['enabled'])لما يشتغل النشر الآلي @endif مع إن دقتها ما ثبتت. وقّفها إذا ما بدك هيك.</x-lq.alert>
    @endif

    @if ($ap['status'] === 'ok' && $ap['rows'] === [])
        <x-lq.empty-state plain icon="review" title="لسا ما في مراجعات"
            text="اعتمد أو ارفض صور من شاشة المراجعة، وهون بتبيّن دقة الاقتراح لكل ماركة. ما في ماركة بتصير جاهزة قبل مراجعات حقيقية كافية.">
            <x-lq.button variant="secondary" icon="review" :href="route('dashboard.catalog')">شاشة المراجعة</x-lq.button>
        </x-lq.empty-state>
    @elseif ($ap['rows'] !== [])
        <div class="lq-table-wrap lq-autopub__table">
            <table class="lq-table">
                <thead>
                    <tr>
                        <th scope="col">الماركة</th>
                        <th scope="col">مراجعات الاقتراح</th>
                        <th scope="col">الدقة</th>
                        <th scope="col">أقل دقة متوقعة</th>
                        <th scope="col">الحالة</th>
                    </tr>
                </thead>
                <tbody>
                    @foreach ($ap['rows'] as $row)
                        <tr @class(['lq-autopub__row--listed' => $row['listed']])>
                            <td class="lq-table__strong lq-autopub__brand"><bdi dir="ltr">{{ $row['label'] }}</bdi></td>
                            <td class="lq-table__num" data-label="مراجعات">{{ $row['reviews'] ?? '—' }}</td>
                            <td class="lq-table__num" data-label="الدقة"><bdi dir="ltr">{{ $row['precision'] }}</bdi></td>
                            <td class="lq-table__num" data-label="أقل دقة متوقعة"><bdi dir="ltr">{{ $row['lower_bound'] }}</bdi></td>
                            <td class="lq-autopub__cell-status">
                                <span class="lq-autopub__status">
                                    <x-lq.chip :status="$row['tone']" size="sm" :dot="false" :label="$row['chip']" />
                                    @if ($row['action'] === 'enable')
                                        <form method="POST" action="{{ route('dashboard.save_settings') }}" class="lq-autopub__action">
                                            @csrf
                                            <input type="hidden" name="section" value="brand">
                                            <input type="hidden" name="op" value="enable">
                                            <input type="hidden" name="brand" value="{{ $row['brand'] }}">
                                            <x-lq.button type="submit" variant="soft" size="sm" :disabled="(bool) $dbError"
                                                :confirm="'صور «' . $row['brand'] . '» المؤكدة رح تنرفع وتنكتب بالشيت بدون مراجعتك (لما يكون النشر الآلي شغّال). باقي الماركات بتضل تستنى مراجعتك.'">تفعيل</x-lq.button>
                                        </form>
                                    @elseif ($row['action'] === 'disable')
                                        <form method="POST" action="{{ route('dashboard.save_settings') }}" class="lq-autopub__action">
                                            @csrf
                                            <input type="hidden" name="section" value="brand">
                                            <input type="hidden" name="op" value="disable">
                                            <input type="hidden" name="brand" value="{{ $row['brand'] }}">
                                            <x-lq.button type="submit" variant="ghost" size="sm" :disabled="(bool) $dbError"
                                                :confirm="'صور «' . $row['label'] . '» رح ترجع تستنى مراجعتك. الماركات التانية ما بتتأثر.' . ($listedCount === 1 && $ap['enabled'] ? ' وبما إنها آخر ماركة مفعّلة، النشر الآلي كله رح ينطفي.' : '')">إيقاف</x-lq.button>
                                        </form>
                                    @endif
                                </span>
                            </td>
                        </tr>
                    @endforeach
                </tbody>
            </table>
        </div>
    @endif

    <p class="lq-autopub__criterion">{{ $ap['criterion'] }}</p>
</section>

<section id="lq-settings-strict-lane" class="lq-card lq-settings-card" aria-label="النشر الآلي لكل الماركات المؤكدة" data-autopub-lane>
    <div class="lq-autopub__head">
        <div class="lq-settings-card__head">
            <h2 class="lq-section-title">النشر الآلي لكل الماركات المؤكدة</h2>
            <p class="lq-settings-card__intro">بدل ما تستنى كل ماركة لحالها: الاقتراح اللي بيعدّي كل قواعد النشر الآلي (قارئ الملصق أكّده، متجر موثوق، بلا تعارض) لماركة موجودة بـ Brands Mapping بينرفع بدون مراجعة، بس لما تثبت دقة هالفئة بمراجعاتك. شغّال من الأساس، وما بيحتاج مفتاح «تفعيل النشر الآلي» فوق.</p>
        </div>
        <form method="POST" action="{{ route('dashboard.save_settings') }}" class="lq-autopub__switch" data-autopub-form>
            @csrf
            <input type="hidden" name="section" value="strict-lane">
            <x-lq.switch name="auto_publish_strict_lane" value="true" label="النشر الآلي لكل الماركات المؤكدة" :checked="$lane['enabled']"
                :disabled="(bool) $dbError || (!$lane['enabled'] && !$lane['can_enable'])"
                data-autopub-switch :data-confirm-on="$laneOnText" :data-confirm-off="$laneOffText" />
            <button type="submit" class="lq-btn lq-btn--secondary lq-btn--sm lq-autopub__save" data-autopub-save>حفظ</button>
        </form>
    </div>
    @if ($lane['status'] === 'ok')
        <div class="lq-autopub__lane">
            <x-lq.chip :status="$lane['tone']" size="sm" :dot="false" :label="$lane['chip']" />
            <strong>{{ $lane['text'] }}</strong>
            @if ($lane['detail'] !== '')<span class="lq-autopub__lane-detail">{{ $lane['detail'] }}</span>@endif
        </div>
        <p class="lq-autopub__lane-detail" data-autopub-lane-state>{{ $lane['state'] }}</p>
        <p class="lq-autopub__hint">{{ $lane['how'] }}</p>
        <p class="lq-autopub__lane-detail">{{ $lane['unsure_text'] }}</p>
    @else
        <p class="lq-autopub__lane-detail" data-autopub-lane-state>{{ $lane['state'] }}</p>
        <p class="lq-autopub__hint">ما قدرنا نحسب دقة هالفئة هلق، فما رح ينشر منها شي لحالها لحتى نتأكد إنها جاهزة. {{ $lane['how'] }}</p>
    @endif
</section>
