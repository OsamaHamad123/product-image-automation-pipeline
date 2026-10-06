/*
 * لقطة · المراجعة — DOM helpers. Web data (titles, URLs, names) only ever goes through textContent, attributes and
 * event listeners: never innerHTML, never an inline handler.
 */
(function (root) {
    'use strict';

    const R = root.LaqtaReview = root.LaqtaReview || {};
    const SVG_NS = 'http://www.w3.org/2000/svg';

    // Same stroke paths as <x-lq.icon> (components/lq/icon.blade.php)
    const ICONS = {
        check: 'M5 12.5 10 17.5 19 7',
        x: 'M6 6l12 12M18 6 6 18',
        alert: 'M12 4 2.5 20h19zM12 10v4.5M12 17.5v.5',
        external: 'M14 4h6v6M20 4l-9 9M18 14v5H5V6h5',
        refresh: 'M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6',
        search: 'M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14zM20 20l-4-4',
        grid: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
        upload: 'M12 16V4M7 9l5-5 5 5M4 15v5h16v-5',
        link: 'M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1',
        barcode: 'M4 5v14M7.5 5v14M10 5v14M13.5 5v14M17 5v14M20 5v14',
        sparkle: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z',
        store: 'M4 9l1.5-5h13L20 9M4 9v11h16V9M4 9h16M9 20v-6h6v6',
        weight: 'M5 20h14M7 20l1-11h8l1 11M9 9a3 3 0 0 1 6 0',
        text: 'M5 5h14M5 12h10M5 19h6',
        image: 'M4 5h16v14H4zM8 15l3-3 2 2 3-4 2 3',
        info: 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18zM12 11v5M12 7.5v.5',
        exclamation: 'M12 7v6M12 16.5v.5',
        minus: 'M6 12h12',
        play: 'M8 5v14l11-7z',
        review: 'M10 6h10M10 12h10M10 18h10M3.5 6l1.5 1.5L7.5 5M3.5 12l1.5 1.5 2.5-2.5M3.5 18l1.5 1.5 2.5-2.5',
        'arrow-left': 'M19 12H5M11 6l-6 6 6 6'
    };

    function icon(name, size, stroke, extraClass) {
        const svg = document.createElementNS(SVG_NS, 'svg');
        const px = String(size || 18);
        svg.setAttribute('class', 'lq-icon' + (extraClass ? ' ' + extraClass : ''));
        svg.setAttribute('width', px);
        svg.setAttribute('height', px);
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('fill', 'none');
        svg.setAttribute('stroke', 'currentColor');
        svg.setAttribute('stroke-width', String(stroke || 1.8));
        svg.setAttribute('stroke-linecap', 'round');
        svg.setAttribute('stroke-linejoin', 'round');
        svg.setAttribute('aria-hidden', 'true');
        svg.setAttribute('focusable', 'false');
        const path = document.createElementNS(SVG_NS, 'path');
        path.setAttribute('d', ICONS[name] || '');
        svg.appendChild(path);
        return svg;
    }

    // el('button', { className, text, type, dataset, onclick, hidden, disabled, ... }, [children])
    function el(tag, props, children) {
        const node = document.createElement(tag);
        props = props || {};
        Object.keys(props).forEach(key => {
            const value = props[key];
            if (value === undefined || value === null || value === false) return;
            if (key === 'text') node.textContent = String(value);
            else if (key === 'className') node.className = value;
            else if (key === 'dataset') Object.keys(value).forEach(k => { node.dataset[k] = String(value[k]); });
            else if (key === 'hidden') node.hidden = true;
            else if (key === 'disabled') node.disabled = true;
            else if (key === 'checked') node.checked = true;
            else if (key === 'value') node.value = String(value);
            else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
            else node.setAttribute(key, String(value));
        });
        (Array.isArray(children) ? children : [children]).forEach(child => {
            if (child === null || child === undefined || child === false) return;
            node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
        });
        return node;
    }

    // نص لاتيني (اسم المنتج، عنوان الصفحة) داخل واجهة عربية
    function bdi(text, className, dir) {
        return el('bdi', { className: className || null, dir: dir || 'ltr', text: text });
    }

    function clear(node) {
        if (node) node.textContent = '';
        return node;
    }

    function safeHttpUrl(url) {
        try {
            const u = new URL(String(url || ''), root.location ? root.location.origin : 'http://localhost');
            return (u.protocol === 'http:' || u.protocol === 'https:') ? u.href : '';
        } catch (e) {
            return '';
        }
    }

    // صور المصادر عبر /api/image-proxy (http/https فقط)؛ صور Cloudinary والصفحة نفسها مباشرة، ومعاينة الملف المحلي كما هي
    function imageUrl(url, proxy) {
        const raw = String(url || '');
        if (raw.startsWith('blob:')) return raw;
        const safe = safeHttpUrl(raw);
        if (!safe) return '';
        const origin = root.location ? root.location.origin : '';
        if ((origin && safe.startsWith(origin + '/')) || /^https:\/\/res\.cloudinary\.com\//.test(safe)) return safe;
        return (proxy || '/api/image-proxy') + '?url=' + encodeURIComponent(safe);
    }

    // صورة مع حالة فشل صادقة: إذا ما انعرضت تظهر جملة بدل أيقونة مكسورة
    function img(url, alt, proxy, className) {
        const src = imageUrl(url, proxy);
        if (!src) return el('span', { className: 'rv-img-missing', text: 'ما في صورة' });
        const node = el('img', { src: src, alt: alt || '', loading: 'lazy', referrerpolicy: 'no-referrer', className: className || null });
        node.addEventListener('error', () => {
            const note = el('span', { className: 'rv-img-missing', text: 'ما قدرنا نعرض الصورة' });
            if (node.parentNode) node.parentNode.replaceChild(note, node);
        });
        return node;
    }

    function csrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? (meta.getAttribute('content') || meta.content || '') : '';
    }

    // طلب JSON بمهلة وبنتيجة موحدة: { ok, status, data } — لا يرمي إلا عند الإلغاء (AbortError).
    // options.keepalive: الطلب بيكمل ولو انسكّرت الصفحة (اعتماد محجوز للتراجع وقت الصفحة تختفي)
    async function requestJson(url, options) {
        options = options || {};
        const headers = Object.assign({ Accept: 'application/json', 'X-CSRF-TOKEN': csrfToken() }, options.headers || {});
        let body = options.body;
        if (body && !(typeof FormData !== 'undefined' && body instanceof FormData) && typeof body !== 'string') {
            body = JSON.stringify(body);
            headers['Content-Type'] = 'application/json';
        }
        let res;
        try {
            const init = { method: options.method || (body ? 'POST' : 'GET'), headers: headers, body: body,
                           signal: options.signal, credentials: 'same-origin', cache: 'no-store' };
            if (options.keepalive) init.keepalive = true;
            res = await fetch(url, init);
        } catch (err) {
            if (err && err.name === 'AbortError') throw err;
            return { ok: false, status: 0, data: { status: 'error', error: 'network' }, network: true };
        }
        let data;
        try {
            data = await res.json();
        } catch (e) {
            data = { status: 'error', error: `HTTP ${res.status}` };
        }
        if (!data || typeof data !== 'object' || Array.isArray(data)) data = { status: 'error', error: `HTTP ${res.status}` };
        return { ok: res.ok, status: res.status, data: data };
    }

    function toast(message, variant, timeout) {
        if (root.Laqta && typeof root.Laqta.toast === 'function') {
            return root.Laqta.toast(message, { variant: variant || 'info', timeout: timeout });
        }
        return null;
    }

    // شارة فئة اختيار المحرك (R.laneOf): «مؤكدة تماماً» أخضر، «القارئ مش متأكد» عنبري، ولا شي لباقي الاقتراحات
    function laneBadge(c) {
        const lane = R.laneOf(c);
        if (!lane || !R.LANE_TEXT[lane]) return null;
        return el('span', { className: `rv-lane rv-lane--${lane}`, dataset: { lane: lane }, title: R.LANE_TITLE[lane], text: R.LANE_TEXT[lane] });
    }

    Object.assign(R, { ICONS, icon, el, bdi, clear, safeHttpUrl, imageUrl, img, csrfToken, requestJson, toast, laneBadge });
})(typeof window !== 'undefined' ? window : globalThis);
