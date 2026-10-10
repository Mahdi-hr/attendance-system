/* ============================================================
   اسکریپت پنل ادمین — بدون هیچ وابستگی خارجی (همه لوکال)
   ============================================================ */
(function () {
    'use strict';

    function on(el, ev, fn) {
        if (el) el.addEventListener(ev, fn, false);
    }
    function all(sel, root) {
        return Array.prototype.slice.call((root || document).querySelectorAll(sel));
    }

    /* ---------- نمونه‌های SQL ---------- */
    all('[data-ap-sql]').forEach(function (btn) {
        on(btn, 'click', function () {
            var box = document.getElementById('id_sql');
            if (!box) return;
            box.value = btn.getAttribute('data-ap-sql');
            box.focus();
            box.setSelectionRange(box.value.length, box.value.length);
        });
    });

    /* ---------- میان‌بر Ctrl+Enter در کنسول SQL ---------- */
    var sqlBox = document.getElementById('id_sql');
    on(sqlBox, 'keydown', function (e) {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
            var form = sqlBox.form;
            if (form) { e.preventDefault(); form.submit(); }
        }
    });

    /* ---------- جستجوی کاربران ---------- */
    var filter = document.getElementById('ap-user-filter');
    var table = document.getElementById('ap-user-table');
    on(filter, 'input', function () {
        if (!table) return;
        var q = filter.value.trim().toLowerCase();
        all('tbody tr', table).forEach(function (row) {
            row.style.display = !q || row.textContent.toLowerCase().indexOf(q) !== -1 ? '' : 'none';
        });
    });

    /* ---------- انتخاب همه ---------- */
    var checkAll = document.getElementById('ap-check-all');
    on(checkAll, 'change', function () {
        all('.ap-user-check').forEach(function (cb) {
            if (cb.closest('tr').style.display !== 'none') cb.checked = checkAll.checked;
        });
    });

    /* ---------- دکمهٔ رفتن به عملیات گروهی ---------- */
    var gotoBulk = document.getElementById('ap-goto-bulk');
    on(gotoBulk, 'click', function () {
        var ids = all('.ap-user-check').filter(function (cb) { return cb.checked; })
            .map(function (cb) { return cb.value; });
        if (!ids.length) {
            window.alert('ابتدا حداقل یک کاربر را تیک بزنید.');
            return;
        }
        window.location.href = gotoBulk.getAttribute('data-url') + '?ids=' + ids.join(',');
    });

    /* ---------- نمایش کادر رمز هنگام «ریست رمز عبور» ---------- */
    var actionSel = document.getElementById('ap-bulk-action');
    var pwBox = document.getElementById('ap-pw-box');
    function syncPw() {
        if (pwBox) pwBox.style.display =
            actionSel && actionSel.value === 'reset_password' ? '' : 'none';
    }
    on(actionSel, 'change', syncPw);
    syncPw();

    /* ---------- بسته‌شدن خودکار پیام‌ها ---------- */
    setTimeout(function () {
        all('.alert-dismissible').forEach(function (el) {
            if (el.closest('.ap-log-view')) return;
            if (window.bootstrap && bootstrap.Alert) {
                try {
                    var inst = bootstrap.Alert.getOrCreateInstance(el);
                    inst.close();
                    return;
                } catch (e) { /* ignore */ }
            }
            el.style.display = 'none';
        });
    }, 9000);

    /* ---------- خوانایی‌تر شدن لینک‌های خارجی پنل ---------- */
    all('a[href^="http://"], a[href^="https://"]').forEach(function (a) {
        if (a.hostname && a.hostname !== window.location.hostname) {
            a.setAttribute('rel', 'noopener noreferrer');
            a.setAttribute('target', '_blank');
        }
    });
})();
