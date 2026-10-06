/*
 * لقطة · المراجعة — «غامق / فاتح / مربعات» behind the published result image.
 *
 * The published image is a transparent PNG (OUTPUT_BACKGROUND = transparent) that the app shows on its dark and light
 * themes. This toggle changes only the background of the preview stage (.rv-final__stage): dark (#121212, shows light
 * fringes), light (white) or a checkerboard (shows what is transparent). It never requests anything: the picture is the
 * one already on the page. The choice is remembered per viewer in localStorage; every read and write is in try/catch, so
 * a private window or blocked storage simply starts on the default.
 *
 * single.js calls R.themePreview.control(stage) where it shows the approved / published image. Loaded by
 * catalog.blade.php before the other review scripts; the node tests load it on its own (module.exports).
 */
(function (root) {
    'use strict';

    const R = root.LaqtaReview = root.LaqtaReview || {};
    const KEY = 'laqta.review.previewTheme';
    const MODES = [['dark', 'غامق'], ['light', 'فاتح'], ['checker', 'مربعات']];
    const NAMES = MODES.map(m => m[0]);
    const DEFAULT = 'dark';

    function valid(mode) {
        return NAMES.includes(String(mode || ''));
    }

    function load() {
        try {
            const value = root.localStorage ? root.localStorage.getItem(KEY) : null;
            return valid(value) ? value : DEFAULT;
        } catch (e) {
            return DEFAULT;
        }
    }

    function save(mode) {
        try {
            if (root.localStorage) root.localStorage.setItem(KEY, mode);
        } catch (e) {
            // storage blocked: the choice lasts until the page closes
        }
    }

    function apply(stage, mode) {
        if (!stage) return;
        NAMES.forEach(name => stage.classList.toggle('rv-theme--' + name, name === mode));
        stage.dataset.previewTheme = mode;
    }

    // The toggle for one stage (the next stage drawn starts on the latest choice)
    function control(stage) {
        const doc = root.document;
        const group = doc.createElement('div');
        group.setAttribute('class', 'rv-theme');
        group.setAttribute('role', 'group');
        group.setAttribute('aria-label', 'خلفية المعاينة');
        const buttons = MODES.map(([name, label]) => {
            const btn = doc.createElement('button');
            btn.setAttribute('type', 'button');
            btn.setAttribute('class', 'rv-theme__btn');
            btn.dataset.previewTheme = name;
            btn.textContent = label;
            btn.addEventListener('click', () => choose(name));
            group.appendChild(btn);
            return btn;
        });

        function paint(name) {
            apply(stage, name);
            buttons.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.previewTheme === name)));
        }

        function choose(name) {
            if (!valid(name)) return;
            save(name);
            paint(name);
        }

        paint(load());
        return group;
    }

    R.themePreview = { KEY: KEY, MODES: NAMES, DEFAULT: DEFAULT, load: load, save: save, apply: apply, control: control };
    if (typeof module !== 'undefined' && module.exports) module.exports = R.themePreview;
})(typeof window !== 'undefined' ? window : globalThis);
