"""صفحه‌کلیدهای ربات بله — فقط دکمه‌های داخل پیام (inline).

کیبورد ثابت پایین صفحه (ReplyKeyboard) عمداً استفاده نمی‌شود تا گفتگو
شلوغ نشود. منوی اصلی به چند دستهٔ کم‌دکمه تقسیم شده و هر صفحه دکمهٔ
بازگشت به والد خودش را دارد.
"""

_FA = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')


def _fa(value):
    return str(value).translate(_FA)


def inline(rows):
    return {'inline_keyboard': rows}


def btn(text, data):
    return {'text': text, 'callback_data': data}


# ============================================================
# منوی اصلی و دسته‌ها
# ============================================================

def main_menu(account, admin, can_switch):
    """منوی اصلی: ۳ تا ۴ دستهٔ بزرگ، نه ده‌ها دکمه."""
    rows = []

    if admin:
        rows += [
            [btn('👤 کارهای من', 'menu:work'), btn('🛡️ مدیریت', 'menu:admin')],
            [btn('📈 گزارش‌ها', 'menu:reports'), btn('⚙️ تنظیمات', 'menu:settings')],
        ]
    else:
        rows += [
            [btn('👤 کارهای من', 'menu:work')],
            [btn('📈 گزارش‌ها', 'menu:reports'), btn('⚙️ تنظیمات', 'menu:settings')],
        ]

    if can_switch:
        rows.append([btn('🔄 تغییر نقش (کاربر/مدیر)', 'role:toggle')])

    rows.append([btn('🚪 خروج از حساب', 'logout')])
    return inline(rows)


def work_menu(admin):
    rows = [
        [btn('📊 وضعیت امروز', 'status')],
        [btn('📝 ثبت مأموریت', 'err:new'), btn('📋 مأموریت‌های من', 'err:mine')],
    ]
    if admin:
        rows.append([btn('🛡️ مدیریت', 'menu:admin')])
    rows.append([btn('🏠 منوی اصلی', 'menu:main')])
    return inline(rows)


def reports_menu(admin):
    rows = [
        [btn('🗓 گزارش هفته', 'rep:week'), btn('📈 گزارش ماه', 'rep:month')],
        [btn('📥 خروجی اکسل ماه', 'rep:excel')],
    ]
    if admin:
        rows += [
            [btn('📊 گزارش تیمی', 'adm:report'), btn('📊 تحلیل ماه', 'adm:analytics')],
            [btn('📸 بازبینی عکس‌ها', 'adm:snapshots')],
        ]
    rows.append([btn('🏠 منوی اصلی', 'menu:main')])
    return inline(rows)


def admin_hub():
    """دستهٔ مدیریت — همهٔ ابزارهای مدیر با بازگشت به منوی اصلی."""
    return inline([
        [btn('📊 داشبورد امروز', 'adm:today'), btn('⏳ صف تأیید', 'adm:pending')],
        [btn('👥 کاربران', 'adm:users'), btn('⚠️ خطاهای خروج', 'adm:checkout')],
        [btn('📸 بازبینی عکس‌ها', 'adm:snapshots')],
        [btn('📢 پیام همگانی', 'adm:broadcast'), btn('📨 پیام زمان‌بندی', 'adm:schedule')],
        [btn('🤖 آمار ربات', 'adm:stats'), btn('📝 ردپای اقدامات', 'adm:audit:1')],
        [btn('🏠 منوی اصلی', 'menu:main')],
    ])


def settings_menu(account, can_switch):
    def mark(value):
        return '✅' if value else '⛔'

    rows = [
        [btn('%s اعلان مأموریت' % mark(account.notify_errand),
             'settings:toggle:errand')],
        [btn('%s اعلان روزانه (یادآور)' % mark(account.notify_daily),
             'settings:toggle:daily')],
        [btn('%s اخبار و اطلاعیه‌ها' % mark(account.notify_news),
             'settings:toggle:news')],
        [btn('🔑 تغییر رمز عبور', 'settings:password')],
    ]
    if can_switch:
        rows.append([btn('🔄 تغییر نقش (کاربر/مدیر)', 'role:toggle')])
    rows.append([btn('🚪 خروج از حساب', 'logout')])
    rows.append([btn('🏠 منوی اصلی', 'menu:main')])
    return inline(rows)


# سازگاری با کدهای قدیمی
def settings_keyboard(account):
    return settings_menu(account, False)


# ============================================================
# ناوبری و لیست‌ها
# ============================================================

def back_only(parent='menu:main', label='🏠 منوی اصلی'):
    return inline([[btn(label, parent)]])


def back_work():
    return inline([[btn('👤 کارهای من', 'menu:work')]])


def back_reports():
    return inline([[btn('📈 گزارش‌ها', 'menu:reports')]])


def back_admin():
    return inline([[btn('🛡️ مدیریت', 'menu:admin')]])


def pager(prefix, page, pages, back='menu:main', extra_rows=None):
    """ردیف ناوبری صفحه‌بندی + بازگشت."""
    nav = []
    if page > 1:
        nav.append(btn('⬅️ قبلی', '%s:%d' % (prefix, page - 1)))
    nav.append(btn('صفحه %s/%s' % (_fa(page), _fa(pages)), 'noop'))
    if page < pages:
        nav.append(btn('بعدی ➡️', '%s:%d' % (prefix, page + 1)))
    rows = list(extra_rows or [])
    if nav:
        rows.append(nav)
    rows.append([btn('🏠 منوی اصلی', back)])
    return inline(rows)


def new_errand_keyboard():
    return inline([
        [btn('📋 مأموریت‌های من', 'err:mine'), btn('👤 کارهای من', 'menu:work')],
    ])


def users_menu():
    return inline([
        [btn('🔍 جستجوی کاربر', 'usr:search'), btn('➕ کاربر جدید', 'usr:new')],
        [btn('🛡️ مدیریت', 'menu:admin')],
    ])


def user_card(user):
    return inline([
        [btn('🔑 ریست رمز', 'usr:reset:%d' % user.pk)],
        [btn('🎭 تغییر نقش', 'usr:role:%d' % user.pk)],
        [btn('🕒 اصلاح رکورد حضور', 'usr:time:%d' % user.pk)],
        [btn('⛔ غیرفعال کن' if user.is_active else '✅ فعال کن', 'usr:toggle:%d' % user.pk)],
        [btn('🛡️ مدیریت', 'menu:admin')],
    ])


def role_keyboard(user):
    return inline([
        [btn('👤 کاربر', 'usr:setrole:%d:user' % user.pk)],
        [btn('🛡️ مدیر', 'usr:setrole:%d:admin' % user.pk)],
        [btn('🎭 هر دو (کاربر + مدیر)', 'usr:setrole:%d:both' % user.pk)],
        [btn('↩️ بازگشت', 'usr:%d' % user.pk)],
    ])


def new_user_role_keyboard():
    return inline([
        [btn('👤 کاربر', 'usr:newrole:user')],
        [btn('🛡️ مدیر', 'usr:newrole:admin')],
        [btn('🎭 هر دو (کاربر + مدیر)', 'usr:newrole:both')],
        [btn('🛡️ مدیریت', 'menu:admin')],
    ])


def confirm_keyboard(yes_data, no_data='menu:main', yes='✅ بله', no='❌ انصراف'):
    return inline([[btn(yes, yes_data), btn(no, no_data)]])


def audit_keyboard(page, pages):
    rows = []
    nav = []
    if page > 1:
        nav.append(btn('⬅️ قبلی', 'adm:audit:%d' % (page - 1)))
    nav.append(btn('صفحه %s/%s' % (_fa(page), _fa(pages)), 'noop'))
    if page < pages:
        nav.append(btn('بعدی ➡️', 'adm:audit:%d' % (page + 1)))
    if nav:
        rows.append(nav)
    rows.append([btn('🛡️ مدیریت', 'menu:admin')])
    return inline(rows)
