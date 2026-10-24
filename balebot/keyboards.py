"""صفحه‌کلیدهای ربات بله."""


def inline(rows):
    return {'inline_keyboard': rows}


def btn(text, data):
    return {'text': text, 'callback_data': data}


def main_menu(account, admin, can_switch):
    """منوی اصلی بر اساس نقش فعال کاربر."""
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
            [btn('📊 وضعیت امروز من', 'status')],
        ]
    else:
        rows += [
            [btn('📊 وضعیت امروز', 'status')],
            [btn('📝 ثبت مأموریت', 'err:new'), btn('📋 مأموریت‌های من', 'err:mine')],
            [btn('📈 گزارش ماه', 'rep:month')],
        ]

    rows.append([btn('🚪 خروج از حساب', 'logout')])
    return inline(rows)


def back_only():
    return inline([[btn('🏠 منوی اصلی', 'menu:main')]])


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
    active = '⛔ غیرفعال' if not user.is_active else '✅ فعال'
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
