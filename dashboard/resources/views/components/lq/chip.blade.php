{{--
    Status pill with a dot. The default label is the plain-Arabic name of the status; the slot or `label` overrides it.
    <x-lq.chip status="proposed" />                    مقترحة
    <x-lq.chip status="warning" size="sm">مقترحة · تحذير</x-lq.chip>
    <x-lq.chip tone="success">متصل</x-lq.chip>          any text, coloured by what it means
    status: proposed | warning | none | not-found | approved | error    size: md | sm
    tone: success | warning | danger | info | neutral | teal (wins over status; give it a label or a slot)
--}}
@props([
    'status' => 'none',
    'tone' => null,
    'label' => null,
    'size' => 'md',
    'dot' => true,
])
@php
    $lqChips = [
        'proposed' => ['lq-chip--proposed', 'مقترحة'],
        'warning' => ['lq-chip--warning', 'فيها تحذير'],
        'none' => ['lq-chip--none', 'بلا اقتراح'],
        'not-found' => ['lq-chip--not-found', 'لم يُعثر عليها'],
        'approved' => ['lq-chip--approved', 'معتمدة'],
        'error' => ['lq-chip--error', 'عطل مؤقت'],
    ];
    $lqChip = $lqChips[str_replace('_', '-', (string) $status)] ?? $lqChips['none'];
    $lqTones = ['success', 'warning', 'danger', 'info', 'neutral', 'teal'];
    if ($tone !== null && in_array($tone, $lqTones, true)) {
        $lqChip[0] = 'lq-chip--' . $tone;
    }
    $lqChipText = $label !== null && $label !== '' ? $label : $lqChip[1];
@endphp
<span {{ $attributes->class(['lq-chip', $lqChip[0], $size === 'sm' ? 'lq-chip--sm' : '']) }}>@if ($dot)<span class="lq-chip__dot" aria-hidden="true"></span>@endif{{ $slot->isEmpty() ? $lqChipText : $slot }}</span>
