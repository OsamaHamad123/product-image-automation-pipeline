{{--
    Progress bar, single or stacked, with an optional head row (title + meta), note and legend.
    Single:  <x-lq.progress :value="24" :max="40" label="تقدّم التشغيل" title="الصفوف 62–101" meta="24 / 40" note="متبقي تقريباً 6 دقائق" />
    Stacked: <x-lq.progress label="نتائج التشغيل" :max="40" :segments="[
                 ['label' => 'مقترحة', 'value' => 13, 'tone' => 'success'],
                 ['label' => 'بلا اقتراح', 'value' => 7, 'tone' => 'muted'],
             ]" />
    Stacked without `max` fills the whole track (segments are shares of their sum), like the sheet funnel.
    tone: teal | bright | success | warning | danger | info | muted | empty    size: sm | md | lg | xl
--}}
@props([
    'value' => 0,
    'max' => null,
    'label' => null,
    'tone' => 'teal',
    'size' => 'md',
    'segments' => null,
    'legend' => true,
    'title' => null,
    'meta' => null,
    'note' => null,
])
@php
    $lqTones = [
        'teal' => 'lq-tone--teal',
        'bright' => 'lq-tone--bright',
        'success' => 'lq-tone--success',
        'warning' => 'lq-tone--warning',
        'danger' => 'lq-tone--danger',
        'info' => 'lq-tone--info',
        'muted' => 'lq-tone--muted',
        'empty' => 'lq-tone--empty',
    ];
    $lqSizes = ['sm' => 'lq-progress--sm', 'md' => '', 'lg' => 'lq-progress--lg', 'xl' => 'lq-progress--xl'];
    $lqNum = static fn ($v) => is_numeric($v) ? max(0.0, (float) $v) : 0.0;
    $lqFmt = static fn ($v) => rtrim(rtrim(number_format((float) $v, 2, '.', ''), '0'), '.');
    $lqSegInput = $segments instanceof \Illuminate\Support\Collection ? $segments->all() : (is_array($segments) ? $segments : []);
    $lqStacked = count($lqSegInput) > 0;
    $lqSegments = [];
    if ($lqStacked) {
        foreach ($lqSegInput as $lqSeg) {
            $lqSeg = (array) $lqSeg;
            $lqSegments[] = [
                'label' => (string) ($lqSeg['label'] ?? ''),
                'value' => $lqNum($lqSeg['value'] ?? 0),
                'tone' => $lqTones[$lqSeg['tone'] ?? 'muted'] ?? $lqTones['muted'],
            ];
        }
        $lqSum = array_sum(array_column($lqSegments, 'value'));
        $lqMax = $lqNum($max) > 0 ? max($lqNum($max), $lqSum) : $lqSum;
        foreach ($lqSegments as $lqI => $lqSeg) {
            $lqSegments[$lqI]['pct'] = $lqMax > 0 ? round($lqSeg['value'] / $lqMax * 100, 2) : 0;
        }
        $lqSummary = implode('، ', array_map(
            static fn ($s) => trim($s['label'] . ' ' . $lqFmt($s['value'])),
            $lqSegments
        ));
        $lqAria = trim(($label ?? $title ?? '') . ': ' . $lqSummary, ': ');
    } else {
        $lqMax = $lqNum($max) > 0 ? $lqNum($max) : 100.0;
        $lqValue = min($lqNum($value), $lqMax);
        $lqPct = round($lqValue / $lqMax * 100, 2);
        $lqAria = (string) ($label ?? $title ?? '');
    }
    $lqHasHead = ($title !== null && $title !== '') || ($meta !== null && $meta !== '');
    $lqHasNote = $note !== null && $note !== '';
    $lqShowLegend = $lqStacked && $legend;
    $lqWrapped = $lqHasHead || $lqHasNote || $lqShowLegend;
    $lqBarClasses = array_filter(['lq-progress', $lqSizes[$size] ?? '', $lqStacked ? 'lq-progress--stacked' : '']);
@endphp
@if ($lqWrapped)
<div {{ $attributes->class(['lq-progress-block']) }}>
    @if ($lqHasHead)
        <div class="lq-progress-block__head">
            <span>{{ $title }}</span>
            @if ($meta !== null && $meta !== '')
                <span class="lq-progress-block__meta">{{ $meta }}</span>
            @endif
        </div>
    @endif
@endif
@if ($lqStacked)
    <div @if ($lqWrapped) class="{{ implode(' ', $lqBarClasses) }}" @else {{ $attributes->class($lqBarClasses) }} @endif role="img" aria-label="{{ $lqAria }}">
        @foreach ($lqSegments as $lqSeg)
            @if ($lqSeg['pct'] > 0)
                <span class="lq-progress__seg {{ $lqSeg['tone'] }}" style="width: {{ $lqSeg['pct'] }}%"></span>
            @endif
        @endforeach
    </div>
@else
    <div @if ($lqWrapped) class="{{ implode(' ', $lqBarClasses) }}" @else {{ $attributes->class($lqBarClasses) }} @endif role="progressbar" aria-valuemin="0" aria-valuemax="{{ $lqFmt($lqMax) }}" aria-valuenow="{{ $lqFmt($lqValue) }}" @if ($lqAria !== '') aria-label="{{ $lqAria }}" @endif>
        <div class="lq-progress__bar {{ $lqTones[$tone] ?? $lqTones['teal'] }}" style="width: {{ $lqPct }}%"></div>
    </div>
@endif
@if ($lqWrapped)
    @if ($lqShowLegend)
        <ul class="lq-legend" aria-hidden="true">
            @foreach ($lqSegments as $lqSeg)
                <li class="lq-legend__item"><span class="lq-legend__swatch {{ $lqSeg['tone'] }}"></span>{{ $lqSeg['label'] }}<span class="lq-legend__value">{{ $lqFmt($lqSeg['value']) }}</span></li>
            @endforeach
        </ul>
    @endif
    @if ($lqHasNote)
        <span class="lq-progress-block__note">{{ $note }}</span>
    @endif
</div>
@endif
