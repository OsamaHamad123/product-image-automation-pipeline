{{--
    «الشيت: X · الورقة: Y»: which sheet and tab the dashboard reads (RunController::sheetLine, never a Google read).
    <x-lq.sheet-line :line="RunController::sheetLine($owner)" />
    The owner's line links to «ربط الشيت» (/system?tab=sheet); a reviewer's is plain text (href '').
    The same line is drawn by public/js/review/app.js (R.sheetLine) in the review headers.
--}}
@props(['line'])
@if (is_array($line ?? null))
    <p {{ $attributes->merge(['class' => 'lq-sheet-line lq-text--muted']) }} data-sheet-line>
        @if (($line['href'] ?? '') !== '')
            <a class="lq-link" href="{{ $line['href'] }}" title="غيّر الشيت أو الورقة">الشيت: <bdi>{{ $line['sheet'] }}</bdi> · الورقة: <bdi>{{ $line['tab'] }}</bdi></a>
        @else
            <span>الشيت: <bdi>{{ $line['sheet'] }}</bdi> · الورقة: <bdi>{{ $line['tab'] }}</bdi></span>
        @endif
    </p>
@endif
