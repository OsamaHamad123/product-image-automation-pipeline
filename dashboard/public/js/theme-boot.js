/*
 * لقطة · theme boot. Loaded synchronously in <head>, before the CSS, by the layout and the standalone pages
 * (login, forbidden), so the chosen theme is on <html> before the first paint (no light flash in dark mode).
 * localStorage «laqta.theme»: 'light' | 'dark' | 'system' (default; also when storage is blocked or unreadable).
 * 'system' leaves <html> without data-theme: laqta.css follows prefers-color-scheme. layout.js setTheme changes it.
 */
(function () {
    'use strict';
    var choice = 'system';
    try {
        var stored = window.localStorage.getItem('laqta.theme');
        if (stored === 'light' || stored === 'dark') choice = stored;
    } catch (e) {
        choice = 'system';
    }
    var root = document.documentElement;
    if (!root) return;
    if (choice === 'system') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', choice);
    var scheme = document.querySelector('meta[name="color-scheme"]');
    if (scheme) scheme.setAttribute('content', choice === 'system' ? 'light dark' : choice);
})();
