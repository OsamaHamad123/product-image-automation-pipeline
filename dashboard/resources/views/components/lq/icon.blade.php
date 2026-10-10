{{--
    Inline stroke icon from a fixed set (24x24 grid, stroke = currentColor, so it takes the text colour).
    <x-lq.icon name="review" />                 decorative (aria-hidden)
    <x-lq.icon name="alert" label="تحذير" />    meaningful: role="img" with an accessible name
    An unknown name renders nothing. The paths are trusted constants and are the only unescaped output.
--}}
@props([
    'name' => '',
    'size' => 20,
    'stroke' => 1.8,
    'label' => null,
])
@php
    $lqIconPaths = [
        // navigation
        'home' => 'M3 10.5 12 3l9 7.5M5.5 9v11.5h13V9',
        'review' => 'M10 6h10M10 12h10M10 18h10M3.5 6l1.5 1.5L7.5 5M3.5 12l1.5 1.5 2.5-2.5M3.5 18l1.5 1.5 2.5-2.5',
        'run' => 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18zM10 8.5v7l5.5-3.5z',
        'health' => 'M3 12h4l3 7 4-14 3 7h4',
        'settings' => 'M4 7h9M17 7h3M4 17h3M11 17h9M15 5v4M9 15v4',
        'user' => 'M12 4a4 4 0 1 0 0 8a4 4 0 1 0 0-8zM4.5 20.5c1.2-3.6 4-5.5 7.5-5.5s6.3 1.9 7.5 5.5',
        // actions
        'search' => 'M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14zM20 20l-4-4',
        'check' => 'M5 12.5 10 17.5 19 7',
        'x' => 'M6 6l12 12M18 6 6 18',
        'alert' => 'M12 4 2.5 20h19zM12 10v4.5M12 17.5v.5',
        'external' => 'M14 4h6v6M20 4l-9 9M18 14v5H5V6h5',
        'refresh' => 'M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6',
        'play' => 'M8 5v14l11-7z',
        'pause' => 'M8 5v14M16 5v14',
        'stop' => 'M6 6h12v12H6z',
        'grid' => 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
        'upload' => 'M12 16V4M7 9l5-5 5 5M4 15v5h16v-5',
        'link' => 'M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1',
        'shield' => 'M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z',
        'barcode' => 'M4 5v14M7.5 5v14M10 5v14M13.5 5v14M17 5v14M20 5v14',
        'sparkle' => 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z',
        // product facts and states (from the review screen)
        'store' => 'M4 9l1.5-5h13L20 9M4 9v11h16V9M4 9h16M9 20v-6h6v6',
        'weight' => 'M5 20h14M7 20l1-11h8l1 11M9 9a3 3 0 0 1 6 0',
        'text' => 'M5 5h14M5 12h10M5 19h6',
        'image' => 'M4 5h16v14H4zM8 15l3-3 2 2 3-4 2 3',
        'info' => 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18zM12 11v5M12 7.5v.5',
        'exclamation' => 'M12 7v6M12 16.5v.5',
        'minus' => 'M6 12h12',
        'trash' => 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13',
        'arrow-left' => 'M19 12H5M11 6l-6 6 6 6',
    ];
    $lqIconPath = $lqIconPaths[$name] ?? null;
    $lqIconSize = is_numeric($size) ? 0 + $size : 20;
    $lqIconStroke = is_numeric($stroke) ? 0 + $stroke : 1.8;
    $lqIconLabel = is_string($label) && trim($label) !== '' ? $label : null;
@endphp
@if ($lqIconPath !== null)
<svg {{ $attributes->class(['lq-icon']) }} width="{{ $lqIconSize }}" height="{{ $lqIconSize }}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="{{ $lqIconStroke }}" stroke-linecap="round" stroke-linejoin="round" focusable="false" @if ($lqIconLabel !== null) role="img" aria-label="{{ $lqIconLabel }}" @else aria-hidden="true" @endif><path d="{!! $lqIconPath !!}"></path></svg>
@endif
