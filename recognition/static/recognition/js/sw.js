// ============================================================
// Service Worker — سامانهٔ حضور و غیاب
// نسخهٔ ۲: نسخه‌های قبلی صفحاتِ وابسته به نشست کاربر (داشبورد،
// تغییر حالت ادمین/کاربر و ...) را stale-while-revalidate می‌کردند
// و همین باعث می‌شد بعد از چند بار تغییر حالت، صفحه «گیر کند» و
// همیشه نسخهٔ کشِ قدیمی را نشان دهد.
//
// اصلاحات این نسخه:
//  1) صفحات وابسته به نشست/نقش کاربر هرگز کش نمی‌شوند و همیشه
//     تازه از سرور می‌آیند (network-first بدون ذخیره).
//  2) برای بقیهٔ صفحات HTML هم network-first (کش فقط برای حالت
//     آفلاین به‌عنوان پشتیبان).
//  3) نام کش‌ها به v2 تغییر کرد تا همهٔ کش‌های قدیمی (که صفحهٔ
//     «گیرکرده» داخلشان بود) یک‌جا پاک شوند.
// ============================================================

const CACHE_NAME = 'attendance-v2';
const STATIC_CACHE = 'static-v2';
const DYNAMIC_CACHE = 'dynamic-v2';

const STATIC_ASSETS = [
    '/static/recognition/css/bootstrap.rtl.min.css',
    '/static/recognition/fonts/vazirmatn/vazirmatn.css',
    '/static/recognition/css/fontawesome.min.css',
    '/static/recognition/js/bootstrap.bundle.min.js',
    '/static/recognition/js/persian-datepicker.js',
    '/static/recognition/fonts/webfonts/Vazirmatn-Regular.woff2',
    '/static/recognition/fonts/webfonts/Vazirmatn-Medium.woff2',
    '/static/recognition/fonts/webfonts/Vazirmatn-Bold.woff2',
    '/static/recognition/webfonts/fa-solid-900.woff2',
    '/static/recognition/webfonts/fa-regular-400.woff2',
    '/static/recognition/img/logo-1.png',
];

// صفحاتی که محتوایشان به کاربرِ واردشده و نقش/حالت او بستگی دارد.
// این‌ها هرگز نباید کش شوند وگرنه «گیر می‌کنند» (مثلاً دکمهٔ تغییر
// حالت ادمین/کاربر دیگر اثر نمی‌کند).
const SESSION_PAGE_PREFIXES = [
    '/admin-mode/',
    '/dashboard/',
    '/users/',
    '/view_',
    '/checkout-errors/',
    '/manage/',
    '/not_authorised',
    '/admin/',
    '/edit-profile',
    '/change-password',
    '/logout',
    '/login',
];

function isSessionPage(pathname) {
    return SESSION_PAGE_PREFIXES.some((p) => pathname.startsWith(p));
}

const CACHE_STRATEGIES = {
    // Cache First - for static assets (CSS, JS, fonts, images)
    cacheFirst: async (request) => {
        const cached = await caches.match(request);
        if (cached) return cached;

        try {
            const response = await fetch(request);
            if (response.ok) {
                const cache = await caches.open(STATIC_CACHE);
                cache.put(request, response.clone());
            }
            return response;
        } catch (error) {
            return new Response('Offline', { status: 503 });
        }
    },

    // Network First - always fresh; store only non-session pages
    // (کش فقط به‌عنوان پشتیبان برای حالت آفلاین استفاده می‌شود)
    networkFirst: async (request) => {
        try {
            const response = await fetch(request);
            if (response.ok) {
                const pathname = new URL(request.url).pathname;
                if (!isSessionPage(pathname)) {
                    const cache = await caches.open(DYNAMIC_CACHE);
                    cache.put(request, response.clone());
                }
            }
            return response;
        } catch (error) {
            const cached = await caches.match(request);
            if (cached) return cached;
            return new Response(
                JSON.stringify({ error: 'Offline' }),
                { status: 503, headers: { 'Content-Type': 'application/json' } }
            );
        }
    },
};

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(STATIC_CACHE).then((cache) => {
            return cache.addAll(STATIC_ASSETS);
        }).then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames
                    .filter((name) => name !== STATIC_CACHE && name !== DYNAMIC_CACHE)
                    .map((name) => caches.delete(name))
            );
        }).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const { request } = event;
    const url = new URL(request.url);

    // Skip non-GET requests
    if (request.method !== 'GET') return;

    // Skip cross-origin requests
    if (url.origin !== location.origin) return;

    // صفحات وابسته به نشست/نقش کاربر: همیشه تازه از سرور (بدون کش)
    if (isSessionPage(url.pathname)) {
        event.respondWith(CACHE_STRATEGIES.networkFirst(request));
        return;
    }

    // Strategy based on resource type
    if (request.destination === 'style' ||
        request.destination === 'script' ||
        request.destination === 'font' ||
        request.destination === 'image' ||
        url.pathname.startsWith('/static/')) {
        event.respondWith(CACHE_STRATEGIES.cacheFirst(request));
    } else if (url.pathname.startsWith('/api/')) {
        event.respondWith(CACHE_STRATEGIES.networkFirst(request));
    } else if (request.destination === 'document') {
        // HTML صفحات: همیشه تازه (network-first) — کش فقط پشتیبان آفلاین
        event.respondWith(CACHE_STRATEGIES.networkFirst(request));
    }
});

// Handle messages from clients
self.addEventListener('message', (event) => {
    if (event.data === 'skipWaiting') {
        self.skipWaiting();
    }
});