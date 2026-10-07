/**
 * Persian Datepicker - Custom & Lightweight
 * Supports date + time selection with modern UI
 * No dependencies required
 */
(function() {
    'use strict';

    const PERSIAN_MONTHS = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور', 'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند'];
    const PERSIAN_WEEKDAYS = ['ش', 'ی', 'د', 'س', 'چ', 'پ', 'ج'];

    function gregorianToJalali(gy, gm, gd) {
        let g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
        let gy2 = (gm > 2) ? (gy + 1) : gy;
        let days = 355666 + (365 * gy) + Math.floor((gy2 + 3) / 4) - Math.floor((gy2 + 99) / 100) + Math.floor((gy2 + 399) / 400) + gd + g_d_m[gm - 1];
        let jy = -1595 + (33 * Math.floor(days / 12053));
        days %= 12053;
        jy += 4 * Math.floor(days / 1461);
        days %= 1461;
        if (days > 365) {
            jy += Math.floor((days - 1) / 365);
            days = (days - 1) % 365;
        }
        let jm, jd;
        if (days < 186) {
            jm = 1 + Math.floor(days / 31);
            jd = 1 + (days % 31);
        } else {
            jm = 7 + Math.floor((days - 186) / 30);
            jd = 1 + ((days - 186) % 30);
        }
        return { year: jy, month: jm, day: jd };
    }

    function jalaliToGregorian(jy, jm, jd) {
        let jy2 = jy + 1595;
        let days = -355668 + (365 * jy2) + (Math.floor(jy2 / 33) * 8) + Math.floor(((jy2 % 33) + 3) / 4) + jd + ((jm < 7) ? (jm - 1) * 31 : ((jm - 7) * 30) + 186);
        let gy = 400 * Math.floor(days / 146097);
        days %= 146097;
        if (days > 36524) {
            days--;
            gy += 100 * Math.floor(days / 36524);
            days %= 36524;
            if (days >= 365) days++;
        }
        gy += 4 * Math.floor(days / 1461);
        days %= 1461;
        if (days > 365) {
            gy += Math.floor((days - 1) / 365);
            days = (days - 1) % 365;
        }
        let gd = days + 1;
        let sal_a = [0, 31, ((gy % 4 === 0 && gy % 100 !== 0) || (gy % 400 === 0)) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
        let gm = 1;
        while (gm < 13 && gd > sal_a[gm]) {
            gd -= sal_a[gm];
            gm++;
        }
        return { year: gy, month: gm, day: gd };
    }

    function getDaysInJalaliMonth(jy, jm) {
        if (jm <= 6) return 31;
        if (jm <= 11) return 30;
        return (((jy + 1) % 4 === 0) ? 30 : 29);
    }

    function toPersianNum(num) {
        const pd = ['۰', '۱', '۲', '۳', '۴', '۵', '۶', '۷', '۸', '۹'];
        return String(num).replace(/\d/g, function(d) { return pd[parseInt(d)]; });
    }

    function getTodayJalali() {
        var now = new Date();
        return gregorianToJalali(now.getFullYear(), now.getMonth() + 1, now.getDate());
    }

    function PersianDatepicker(input) {
        this.input = input;
        this.view = 'days';
        var today = getTodayJalali();
        this.currentYear = today.year;
        this.currentMonth = today.month;
        this.todayYear = today.year;
        this.todayMonth = today.month;
        this.todayDay = today.day;
        this.selectedYear = null;
        this.selectedMonth = null;
        this.selectedDay = null;
        // Time mode: enabled with data-time="true" on the input. When on, the
        // chosen Jalali date + time is mirrored into a sibling hidden input
        // (name given by data-hidden-name, e.g. "out_time") as Gregorian ISO.
        this.enableTime = input.getAttribute('data-time') === 'true';
        this.hiddenInput = null;
        var hiddenName = this.enableTime ? input.getAttribute('data-hidden-name') : null;
        if (hiddenName && input.parentElement) {
            this.hiddenInput = input.parentElement.querySelector('input[name="' + hiddenName + '"]');
        }
        var now = new Date();
        this.selectedHour = now.getHours();
        this.selectedMinute = now.getMinutes();
        this.container = null;
        this.init();
    }

    PersianDatepicker.prototype.init = function() {
        var self = this;
        this.input.setAttribute('readonly', 'readonly');
        this.input.style.cursor = 'pointer';
        this.input.style.userSelect = 'none';

        var val = this.input.value;
        if (this.enableTime) {
            // ۱) اول خودِ ورودی را بخوان («1405/07/15 17:30» — شمسی)
            var handled = false;
            if (val) {
                var digits = val
                    .replace(/[۰-۹]/g, function (d) { return String(d.charCodeAt(0) - 0x06F0); })
                    .replace(/[٠-٩]/g, function (d) { return String(d.charCodeAt(0) - 0x0660); });
                var jm = digits.match(/^(\d{4})\/(\d{1,2})\/(\d{1,2})[ T](\d{1,2}):(\d{1,2})/);
                if (jm) {
                    this.selectedYear = parseInt(jm[1], 10);
                    this.selectedMonth = parseInt(jm[2], 10);
                    this.selectedDay = parseInt(jm[3], 10);
                    this.currentYear = this.selectedYear;
                    this.currentMonth = this.selectedMonth;
                    this.selectedHour = parseInt(jm[4], 10) % 24;
                    this.selectedMinute = parseInt(jm[5], 10) % 60;
                    handled = true;
                }
            }
            // ۲) سپس مقدار میلادیِ hidden input (اگر طرح قدیمی استفاده می‌کند)
            if (!handled && this.hiddenInput && this.hiddenInput.value) {
                var isoMatch = this.hiddenInput.value.match(/^(\d{4})-(\d{1,2})-(\d{1,2})[T ](\d{1,2}):(\d{1,2})/);
                if (isoMatch) {
                    var jd = gregorianToJalali(parseInt(isoMatch[1], 10), parseInt(isoMatch[2], 10), parseInt(isoMatch[3], 10));
                    this.selectedYear = jd.year;
                    this.selectedMonth = jd.month;
                    this.selectedDay = jd.day;
                    this.currentYear = jd.year;
                    this.currentMonth = jd.month;
                    this.selectedHour = parseInt(isoMatch[4], 10);
                    this.selectedMinute = parseInt(isoMatch[5], 10);
                    handled = true;
                }
            }
            if (handled) {
                this.input.value = this._isoJalaliDisplay();
            }
        } else if (val && val.match(/^\d{4}\/\d{1,2}\/\d{1,2}$/)) {
            var parts = val.split('/');
            this.selectedYear = parseInt(parts[0]);
            this.selectedMonth = parseInt(parts[1]);
            this.selectedDay = parseInt(parts[2]);
            this.currentYear = this.selectedYear;
            this.currentMonth = this.selectedMonth;
        }

        this.input.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            if (self.container) {
                self.hide();
            } else {
                self.show();
            }
        });

        document.addEventListener('click', function(e) {
            if (self.container && !self.container.contains(e.target) && e.target !== self.input) {
                self.hide();
            }
        });
    };

    PersianDatepicker.prototype.show = function() {
        this.hideAll();
        this.container = document.createElement('div');
        this.container.className = 'pdp-container';

        // Append to body with fixed positioning to avoid overflow clipping
        document.body.appendChild(this.container);

        // Position relative to viewport using the input's bounding rect
        var rect = this.input.getBoundingClientRect();

        // Ensure container doesn't go off-screen right (RTL)
        this.container.style.position = 'fixed';
        this.container.style.top = (rect.bottom + 4) + 'px';
        this.container.style.left = rect.left + 'px';
        this.container.style.zIndex = '999999';

        // Adjust if going off left edge
        var self = this;
        setTimeout(function() {
            if (self.container) {
                var cRect = self.container.getBoundingClientRect();
                if (cRect.left < 8) {
                    self.container.style.left = '8px';
                }
                if (cRect.bottom > window.innerHeight - 8) {
                    self.container.style.top = (rect.top - cRect.height - 4) + 'px';
                }
            }
        }, 10);

        this.render();
    };

    PersianDatepicker.prototype.hideAll = function() {
        var els = document.querySelectorAll('.pdp-container');
        for (var i = 0; i < els.length; i++) { els[i].remove(); }
    };

    PersianDatepicker.prototype.hide = function() {
        if (this.container) {
            this.container.remove();
            this.container = null;
        }
    };

    PersianDatepicker.prototype.render = function() {
        if (this.view === 'days') this.renderDays();
        else if (this.view === 'months') this.renderMonths();
        else if (this.view === 'years') this.renderYears();
    };

    PersianDatepicker.prototype.renderDays = function() {
        var self = this;
        var daysInMonth = getDaysInJalaliMonth(this.currentYear, this.currentMonth);
        var firstGreg = jalaliToGregorian(this.currentYear, this.currentMonth, 1);
        var firstDayOfWeek = new Date(firstGreg.year, firstGreg.month - 1, firstGreg.day).getDay();
        var startDay = (firstDayOfWeek + 1) % 7;

        var html = '<div class="pdp-header">' +
            '<div class="pdp-nav"><button type="button" data-action="prev-month"><i class="fas fa-chevron-right"></i></button></div>' +
            '<div class="pdp-title" data-action="show-months">' + PERSIAN_MONTHS[this.currentMonth - 1] + ' ' + toPersianNum(this.currentYear) + '</div>' +
            '<div class="pdp-nav"><button type="button" data-action="next-month"><i class="fas fa-chevron-left"></i></button></div>' +
            '</div>';

        html += '<div class="pdp-weekdays">';
        for (var w = 0; w < 7; w++) {
            html += '<span>' + PERSIAN_WEEKDAYS[w] + '</span>';
        }
        html += '</div><div class="pdp-days">';

        for (var e = 0; e < startDay; e++) {
            html += '<div class="pdp-day pdp-disabled"></div>';
        }

        for (var d = 1; d <= daysInMonth; d++) {
            var isToday = (this.currentYear === this.todayYear && this.currentMonth === this.todayMonth && d === this.todayDay);
            var isSelected = (this.selectedYear === this.currentYear && this.selectedMonth === this.currentMonth && d === this.selectedDay);
            var dayOfWeek = (startDay + d - 1) % 7;
            var isFriday = (dayOfWeek === 6);
            var cls = 'pdp-day';
            if (isToday) cls += ' pdp-today';
            if (isSelected) cls += ' pdp-selected';
            if (isFriday) cls += ' pdp-friday';
            html += '<div class="' + cls + '" data-day="' + d + '">' + toPersianNum(d) + '</div>';
        }

        html += '</div>';
        if (this.enableTime) {
            html += this._renderTimeSection();
        }
        html += '<div class="pdp-footer">' +
            '<button type="button" data-action="today">امروز</button>' +
            '<button type="button" data-action="clear">پاک کردن</button>' +
            '</div>';

        this.container.innerHTML = html;
        this.bindEvents();
    };

    PersianDatepicker.prototype._renderTimeSection = function() {
        var hourOpts = '', minOpts = '';
        for (var h = 0; h < 24; h++) {
            var pad = h < 10 ? '0' + h : h;
            hourOpts += '<option value="' + h + '"' + (h === this.selectedHour ? ' selected' : '') + '>' + toPersianNum(pad) + '</option>';
        }
        for (var m = 0; m < 60; m++) {
            var pad = m < 10 ? '0' + m : m;
            minOpts += '<option value="' + m + '"' + (m === this.selectedMinute ? ' selected' : '') + '>' + toPersianNum(pad) + '</option>';
        }

        var quickTimes = [
            { h: 8, m: 0, label: '۸ صبح' },
            { h: 9, m: 0, label: '۹ صبح' },
            { h: 12, m: 0, label: '۱۲ ظهر' },
            { h: 14, m: 0, label: '۱۴ عصر' },
            { h: 17, m: 0, label: '۱۷ عصر' },
            { h: 20, m: 0, label: '۲۰ شب' },
            { h: 23, m: 59, label: '۲۳:۵۹' }
        ];
        var quickHtml = '';
        for (var q = 0; q < quickTimes.length; q++) {
            var qt = quickTimes[q];
            quickHtml += '<button type="button" class="pdp-quick-btn" data-qh="' + qt.h + '" data-qm="' + qt.m + '">' + qt.label + '</button>';
        }

        return '<div class="pdp-time">' +
            '<div class="pdp-time-row">' +
                '<span class="pdp-time-label">ساعت</span>' +
                '<div class="pdp-time-wheel"><select data-field="hour">' + hourOpts + '</select></div>' +
                '<span class="pdp-dot">:</span>' +
                '<div class="pdp-time-wheel"><select data-field="minute">' + minOpts + '</select></div>' +
                '<span class="pdp-time-label">دقیقه</span>' +
            '</div>' +
            '<div class="pdp-quick-times">' + quickHtml + '</div>' +
            '</div>';
    };

    PersianDatepicker.prototype.renderMonths = function() {
        var self = this;
        var html = '<div class="pdp-header">' +
            '<div class="pdp-nav"><button type="button" data-action="prev-year"><i class="fas fa-chevron-right"></i></button></div>' +
            '<div class="pdp-title" data-action="show-years">' + toPersianNum(this.currentYear) + '</div>' +
            '<div class="pdp-nav"><button type="button" data-action="next-year"><i class="fas fa-chevron-left"></i></button></div>' +
            '</div><div class="pdp-months-grid">';

        for (var m = 0; m < 12; m++) {
            var isCurrent = (this.currentYear === this.todayYear && (m + 1) === this.todayMonth);
            var cls = 'pdp-month-item';
            if (isCurrent) cls += ' pdp-current';
            html += '<div class="' + cls + '" data-month="' + (m + 1) + '">' + PERSIAN_MONTHS[m] + '</div>';
        }

        html += '</div>';
        this.container.innerHTML = html;
        this.bindEvents();
    };

    PersianDatepicker.prototype.renderYears = function() {
        var self = this;
        var startYear = this.currentYear - 5;
        var endYear = this.currentYear + 10;

        var html = '<div class="pdp-header">' +
            '<div class="pdp-nav"><button type="button" data-action="prev-years"><i class="fas fa-chevron-right"></i></button></div>' +
            '<div class="pdp-title">' + toPersianNum(startYear) + ' - ' + toPersianNum(endYear) + '</div>' +
            '<div class="pdp-nav"><button type="button" data-action="next-years"><i class="fas fa-chevron-left"></i></button></div>' +
            '</div><div class="pdp-years-grid">';

        for (var y = startYear; y <= endYear; y++) {
            var isCurrent = (y === this.todayYear);
            var cls = 'pdp-year-item';
            if (isCurrent) cls += ' pdp-current';
            html += '<div class="' + cls + '" data-year="' + y + '">' + toPersianNum(y) + '</div>';
        }

        html += '</div>';
        this.container.innerHTML = html;
        this.bindEvents();
    };

    PersianDatepicker.prototype.bindEvents = function() {
        var self = this;
        var container = this.container;

        container.onclick = function(e) {
            e.stopPropagation();
            var target = e.target;

            var btn = target.closest('[data-action]');
            if (btn) {
                var action = btn.getAttribute('data-action');
                if (action === 'prev-month') {
                    self.currentMonth--;
                    if (self.currentMonth < 1) { self.currentMonth = 12; self.currentYear--; }
                    self.render();
                } else if (action === 'next-month') {
                    self.currentMonth++;
                    if (self.currentMonth > 12) { self.currentMonth = 1; self.currentYear++; }
                    self.render();
                } else if (action === 'prev-year') {
                    self.currentYear--;
                    self.render();
                } else if (action === 'next-year') {
                    self.currentYear++;
                    self.render();
                } else if (action === 'prev-years') {
                    self.currentYear -= 15;
                    self.render();
                } else if (action === 'next-years') {
                    self.currentYear += 15;
                    self.render();
                } else if (action === 'show-months') {
                    self.view = 'months';
                    self.render();
                } else if (action === 'show-years') {
                    self.view = 'years';
                    self.render();
                } else if (action === 'today') {
                    var today = getTodayJalali();
                    var n = new Date();
                    self.selectedHour = n.getHours();
                    self.selectedMinute = n.getMinutes();
                    self.selectDate(today.year, today.month, today.day);
                } else if (action === 'clear') {
                    self.input.value = '';
                    if (self.hiddenInput) self.hiddenInput.value = '';
                    self.selectedYear = self.selectedMonth = self.selectedDay = null;
                    self.hide();
                }
                return;
            }

            var qBtn = target.closest('.pdp-quick-btn');
            if (qBtn && qBtn.getAttribute('data-qh') !== null) {
                self.selectedHour = parseInt(qBtn.getAttribute('data-qh'), 10);
                self.selectedMinute = parseInt(qBtn.getAttribute('data-qm'), 10);
                if (self.selectedDay !== null) {
                    self._syncHidden(self.selectedYear, self.selectedMonth, self.selectedDay);
                    self.input.value = self._isoJalaliDisplay();
                }
                // بازرندر کامل تا حالت انتخاب‌شدهٔ دکمه‌ها هم به‌روز شود
                self.render();
                return;
            }

            var sel = target.closest('.pdp-time-wheel select');
            if (sel && sel.getAttribute('data-field')) {
                self._setTimeField(sel.getAttribute('data-field'), parseInt(sel.value, 10));
                return;
            }

            var dayEl = target.closest('.pdp-day:not(.pdp-disabled)');
            if (dayEl && dayEl.getAttribute('data-day')) {
                self.selectDate(self.currentYear, self.currentMonth, parseInt(dayEl.getAttribute('data-day')));
                return;
            }

            var monthEl = target.closest('.pdp-month-item');
            if (monthEl && monthEl.getAttribute('data-month')) {
                self.currentMonth = parseInt(monthEl.getAttribute('data-month'));
                self.view = 'days';
                self.render();
                return;
            }

            var yearEl = target.closest('.pdp-year-item');
            if (yearEl && yearEl.getAttribute('data-year')) {
                self.currentYear = parseInt(yearEl.getAttribute('data-year'));
                self.view = 'months';
                self.render();
                return;
            }
        };

        var selects = container.querySelectorAll('.pdp-time-wheel select');
        for (var i = 0; i < selects.length; i++) {
            selects[i].addEventListener('change', function() {
                self._setTimeField(this.getAttribute('data-field'), parseInt(this.value, 10));
            });
        }
    };

    PersianDatepicker.prototype._setTimeField = function(field, value) {
        if (field === 'hour') {
            this.selectedHour = Math.max(0, Math.min(23, value || 0));
        } else {
            this.selectedMinute = Math.max(0, Math.min(59, value || 0));
        }
        if (this.selectedDay !== null) {
            this._syncHidden(this.selectedYear, this.selectedMonth, this.selectedDay);
            this.input.value = this._isoJalaliDisplay();
        }
    };

    PersianDatepicker.prototype.selectDate = function(year, month, day) {
        this.selectedYear = year;
        this.selectedMonth = month;
        this.selectedDay = day;
        this.currentYear = year;
        this.currentMonth = month;
        var value = year + '/' + String(month).padStart(2, '0') + '/' + String(day).padStart(2, '0');
        if (this.enableTime) {
            this._syncHidden(year, month, day);
            value += ' ' + this._pad(this.selectedHour) + ':' + this._pad(this.selectedMinute);
        }
        this.input.value = value;
        // در حالت «تاریخ + ساعت» پنجره باز می‌ماند تا ساعت هم تنظیم شود؛
        // بستن با کلیک بیرون یا دوباره روی ورودی انجام می‌شود.
        if (this.enableTime) {
            this.render();  // هایلایت روز انتخاب‌شده به‌روز شود
        } else {
            this.hide();
        }
        var evt = document.createEvent('Event');
        evt.initEvent('change', true, true);
        this.input.dispatchEvent(evt);
    };

    PersianDatepicker.prototype._pad = function(n) {
        return String(n).padStart(2, '0');
    };

    PersianDatepicker.prototype._isoJalaliDisplay = function() {
        return this.selectedYear + '/' + this._pad(this.selectedMonth) + '/' + this._pad(this.selectedDay) +
            ' ' + this._pad(this.selectedHour) + ':' + this._pad(this.selectedMinute);
    };

    PersianDatepicker.prototype._syncHidden = function(year, month, day) {
        if (!this.hiddenInput) return;
        var g = jalaliToGregorian(year, month, day);
        this.hiddenInput.value = g.year + '-' + this._pad(g.month) + '-' + this._pad(g.day) +
            'T' + this._pad(this.selectedHour) + ':' + this._pad(this.selectedMinute);
    };

    function initAll() {
        var inputs = document.querySelectorAll('.jalali-date');
        for (var i = 0; i < inputs.length; i++) {
            if (!inputs[i]._pdp) {
                inputs[i]._pdp = new PersianDatepicker(inputs[i]);
            }
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAll);
    } else {
        initAll();
    }

    window.initPersianDatepickers = initAll;
})();