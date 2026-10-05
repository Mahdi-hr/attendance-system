/* Quick Jalali range presets for report filters (today / week / month / current month).
 * Works with the PersianDatepicker on .jalali-date inputs. No dependencies. */
(function () {
    'use strict';

    function g2j(gy, gm, gd) {
        var g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
        var gy2 = (gm > 2) ? gy + 1 : gy;
        var days = 355666 + (365 * gy) + Math.floor((gy2 + 3) / 4) - Math.floor((gy2 + 99) / 100) + Math.floor((gy2 + 399) / 400) + gd + g_d_m[gm - 1];
        var jy = -1595 + (33 * Math.floor(days / 12053));
        days %= 12053;
        jy += 4 * Math.floor(days / 1461);
        days %= 1461;
        if (days > 365) { jy += Math.floor((days - 1) / 365); days = (days - 1) % 365; }
        var jm, jd;
        if (days < 186) { jm = 1 + Math.floor(days / 31); jd = 1 + (days % 31); }
        else { jm = 7 + Math.floor((days - 186) / 30); jd = 1 + ((days - 186) % 30); }
        return { y: jy, m: jm, d: jd };
    }

    function j2num(j) {
        return j.y + '/' + String(j.m).padStart(2, '0') + '/' + String(j.d).padStart(2, '0');
    }

    function shiftDays(date, days) {
        var d = new Date(date.getTime() + days * 86400000);
        return new Date(d.getFullYear(), d.getMonth(), d.getDate());
    }

    function g2greg(j) {
        // compute gregorian date of the 1st of the jalali month then add day-1
        return new Date(monthGregorian(j.y, j.m, 1).getTime() + (j.d - 1) * 86400000);
    }

    function monthGregorian(jy, jm, jd) {
        // reuse the picker's algorithm via building dates around a known anchor is complex;
        // instead we convert with the standard formula.
        var jy2 = jy + 1595;
        var days = -355668 + (365 * jy2) + (Math.floor(jy2 / 33) * 8) + Math.floor(((jy2 % 33) + 3) / 4) + jd + ((jm < 7) ? (jm - 1) * 31 : ((jm - 7) * 30) + 186);
        var gy = 400 * Math.floor(days / 146097);
        days %= 146097;
        if (days > 36524) { days--; gy += 100 * Math.floor(days / 36524); days %= 36524; if (days >= 365) days++; }
        gy += 4 * Math.floor(days / 1461);
        days %= 1461;
        if (days > 365) { gy += Math.floor((days - 1) / 365); days = (days - 1) % 365; }
        var gd = days + 1;
        var sal_a = [0, 31, ((gy % 4 === 0 && gy % 100 !== 0) || (gy % 400 === 0)) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
        var gm = 1;
        while (gm < 13 && gd > sal_a[gm]) { gd -= sal_a[gm]; gm++; }
        return new Date(gy, gm - 1, gd);
    }

    function presetRange(name) {
        var now = new Date();
        var today = g2j(now.getFullYear(), now.getMonth() + 1, now.getDate());
        if (name === 'today') return { from: today, to: today };
        if (name === 'month') {
            var f = shiftDays(now, -29);
            return { from: g2j(f.getFullYear(), f.getMonth() + 1, f.getDate()), to: today };
        }
        if (name === 'currentMonth') return { from: { y: today.y, m: today.m, d: 1 }, to: today };
        if (name === 'week') {
            var toGreg = new Date(g2greg(today));
            var wd = toGreg.getDay(); // 0=Sun ... 6=Sat
            var daysBack = (wd + 1) % 7; // Sat -> 0
            var wStart = shiftDays(toGreg, -daysBack);
            return {
                from: g2j(wStart.getFullYear(), wStart.getMonth() + 1, wStart.getDate()),
                to: today
            };
        }
        return null;
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('[data-jalali-preset]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var form = btn.closest('form');
                if (!form) return;
                var inputs = form.querySelectorAll('input.jalali-date');
                if (!inputs.length) return;
                var r = presetRange(btn.dataset.jalaliPreset);
                if (!r) return;
                inputs[0].value = j2num(r.from);
                if (inputs[1]) inputs[1].value = j2num(r.to);
                inputs.forEach(function (inp) { inp._pdp = null; });
                if (window.initPersianDatepickers) window.initPersianDatepickers();
                form.submit();
            });
        });
    });
})();