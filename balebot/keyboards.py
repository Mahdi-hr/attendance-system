"""صفحه‌کلیدهای ربات بله."""

_FA = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')


def _fa(value):
    return str(value).translate(_FA)


def inline(rows):
    return {'inline_keyboard': rows}


def reply(rows):
    """کیبورد ثابت پایین صفحه (ReplyKeyboardMarkup)."""
    return {'keyboard': rows}


def btn(text, data):
    return {'text': text, 'callback_data': data}


def main_reply_keyboard(admin):
    if admin:
        return reply([
            ['📊 داشبورد', '⏳ صف تأیید'],
            ['👥 کاربران', '📈 گزارش ماه'],
            ['⚙️ تنظیمات', '🏠 منو'],
        ])
    return reply([
        ['📊 وضعیت امروز', '📝 ثبت مأموریت'],
        ['📋 مأموریت‌های من', '📈 گزارش ماه'],
        ['⚙️ تنظیمات', '🏠 منو'],
    ])


def main_menu(account, admin, can_switch):
    """منوی اصلی inline بر اساس نقش فعال کاربر."""
    rows = []

    if can_switch:
        if account.active_role == 'admin':
            rows.append([btn('👤 بازگشت به حالت کاربر', 'role:toggle')])
        else:
            rows.append([btn('🔑 ورود به حالت مدیر', 'role:toggle')])

    if admin:
        rows += [
            [btn('📊 داشبورد امروز', 'adm:today'), btn('⏳ در انتظار تأیید', 'adm:pending')],
            [btn('⚠️ خطاهای خروج', 'adm:checkout'), btn('👥 کاربران', 'adm:users')],
            [btn('📈 گزارش ماه', 'rep:month'), btn('📢 پیام همگانی', 'adm:broadcast')],
            [btn('🤖 آمار ربات', 'adm:stats'), btn('📝 ردپای اقدامات', 'adm:audit:1')],
            [btn('📊 وضعیت امروز من', 'status')],
        ]
    else:
        rows += [
            [btn('📊 وضعیت امروز', 'status')],
            [btn('📝 ثبت مأموریت', 'err:new'), btn('📋 مأموریت‌های من', 'err:mine')],
            [btn('📈 گزارش ماه', 'rep:month'), btn('🗓 گزارش هفته', 'rep:week')],
            [btn('📥 خروجی اکسل ماه', 'rep:excel')],
        ]

    rows.append([btn('⚙️ تنظیمات', 'settings')])
    rows.append([btn('🚪 خروج از حساب', 'logout')])
    return inline(rows)


def back_only():
    return inline([[btn('🏠 منوی اصلی', 'menu:main')]])


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
        [btn('📝 مأموریت‌های من', 'err:mine'), btn('🏠 منوی اصلی', 'menu:main')],
    ])


def users_menu():
    return inline([
        [btn('🔍 جستجوی کاربر', 'usr:search'), btn('➕ کاربر جدید', 'usr:new')],
        [btn('🏠 منوی اصلی', 'menu:main')],
    ])


def user_card(user):
    return inline([
        [btn('🔑 ریست رمز', 'usr:reset:%d' % user.pk)],
        [btn('🎭 تغییر نقش', 'usr:role:%d' % user.pk)],
        [btn('⛔ غیرفعال کن' if user.is_active else '✅ فعال کن', 'usr:toggle:%d' % user.pk)],
        [btn('🏠 منوی اصلی', 'menu:main')],
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
        [btn('❌ لغو', 'menu:main')],
    ])


def settings_keyboard(account):
    def mark(value):
        return '✅' if value else '⛔'

    return inline([
        [btn('%s اعلان مأموریت' % mark(account.notify_errand),
             'settings:toggle:errand')],
        [btn('%s اعلان روزانه (یادآور)' % mark(account.notify_daily),
             'settings:toggle:daily')],
        [btn('%s اخبار و اطلاعیه‌ها' % mark(account.notify_news),
             'settings:toggle:news')],
        [btn('🔑 تغییر رمز عبور', 'settings:password')],
        [btn('🏠 منوی اصلی', 'menu:main')],
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
    rows.append([btn('🏠 منوی اصلی', 'menu:main')])
    return inline(rows)
