// ============================================================
// حذف کامل Service Worker (پرچم «آن‌رجیستر و پاک‌سازی»)
//
// چرا حذف شد؟
// نسخهٔ قبلی یک Service Worker با scope کل سایت ثبت می‌کرد که صفحات
// HTML (از جمله داشبورد و دکمهٔ تغییر حالت ادمین/کاربر) را کش می‌کرد.
// همین باعث می‌شد بعد از چند بار تغییر حالت، مرورگر همیشه نسخهٔ قدیمی
// را نشان دهد و دکمه «گیر کند».
//
// حالا کشِ استاتیک (CSS/JS/فونت) با هدر HTTP استاندارد
// `Cache-Control: public, max-age=31536000, immutable` انجام می‌شود و
// صفحات داینامیک هم همیشه `no-store` هستند؛ بنابراین به هیچ Service
// Worker ای نیازی نیست. این اسکریپت مطمئن می‌شود هر SW قبلی که در
// مرورگر کاربر نصب شده، یک‌جا حذف و کش‌هایش پاک می‌شوند.
// ============================================================
if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
        // هر Service Worker ای که قبلاً ثبت شده را حذف کن (نسخهٔ ۱ یا ۲)
        navigator.serviceWorker.getRegistrations().then(function (registrations) {
            registrations.forEach(function (registration) {
                registration.unregister();
            });
        });

        // همهٔ کش‌های Service Worker را هم پاک کن
        if (window.caches) {
            caches.keys().then(function (keys) {
                keys.forEach(function (key) {
                    caches.delete(key);
                });
            });
        }
    });
}