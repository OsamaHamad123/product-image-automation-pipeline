{{--
    Status pill with a dot. The default label is the plain-Arabic name of the status; the slot or `label` overrides it.
    <x-lq.chip status="proposed" />                    مقترحة
    <x-lq.chip status="warning" size="sm">مقترحة · تحذير</x-lq.chip>
    status: proposed | warning | none | not-found | approved | error    size: md | sm
--}}
@props([
    'status' => 'none',
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
    $lqChipText = $label !== null && $label !== '' ? $label : $lqChip[1];
@endphp
<span {{ $attributes->class(['lq-chip', $lqChip[0], $size === 'sm' ? 'lq-chip--sm' : '']) }}>@if ($dot)<span class="lq-chip__dot" aria-hidden="true"></span>@endif{{ $slot->isEmpty() ? $lqChipText : $slot }}</span>
