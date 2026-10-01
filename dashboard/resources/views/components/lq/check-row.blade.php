{{--
    One "sheet vs image" comparison row: what the sheet says, what was read on the image, and whether they agree.
    <x-lq.check-row label="الحجم" sheet="1 كغ" image="1 kg" status="match" />
    <x-lq.check-row label="النوع" sheet="غير محدد" image="Thin French Fries" status="unsure" />
    status: match | unsure | mismatch | unknown    boxed: standalone bordered row (Identity sheet)
    Wrap several rows in <div class="lq-checks"> inside a card.
--}}
@props([
    'label' => '',
    'sheet' => '',
    'image' => '',
    'status' => 'match',
    'boxed' => false,
    'statusLabel' => null,
])
@php
    $lqStatuses = [
        'match' => ['lq-check-row__status--match', 'check', 'مطابق'],
        'unsure' => ['lq-check-row__status--unsure', 'exclamation', 'تأكد بنفسك'],
        'mismatch' => ['lq-check-row__status--mismatch', 'x', 'غير مطابق'],
        'unknown' => ['lq-check-row__status--unknown', 'minus', 'لا توجد معلومة'],
    ];
    $lqStatus = $lqStatuses[$status] ?? $lqStatuses['unknown'];
    $lqStatusLabel = $statusLabel !== null && $statusLabel !== '' ? $statusLabel : $lqStatus[2];
@endphp
<div {{ $attributes->class(['lq-check-row', $boxed ? 'lq-check-row--boxed' : '']) }}>
    <span class="lq-check-row__label">{{ $label }}</span>
    <span class="lq-check-row__sheet"><span class="lq-sr-only">في الشيت: </span><bdi dir="auto">{{ $sheet }}</bdi></span>
    <span class="lq-check-row__image"><span class="lq-sr-only">في الصورة: </span><bdi dir="auto">{{ $image }}</bdi></span>
    <span class="lq-check-row__status {{ $lqStatus[0] }}" role="img" aria-label="{{ $lqStatusLabel }}" title="{{ $lqStatusLabel }}"><x-lq.icon :name="$lqStatus[1]" :size="14" :stroke="2.2" /></span>
</div>
