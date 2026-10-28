"""گفتگوی ربات بله.

جریان کار:
    /start  →  ورود با نام کاربری و رمز عبور  →  منوی اصلی
    کارمند : وضعیت امروز، ثبت/لغو مأموریت، گزارش ماه
    مدیر   : صف تأیید، داشبورد، خطاهای خروج، کاربران، پیام همگانی
    کاربر «both» با دکمهٔ تغییر نقش بین حالت کاربر و مدیر جابه‌جا می‌شود.

همهٔ حالت‌ها در `BaleAccount.state` ذخیره می‌شوند تا اگر ربات ری‌استارت شد،
گفتگو از همان‌جا ادامه پیدا کند.
"""

import datetime as dt
import logging

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.hashers import check_password as check_hash, make_password
from django.db.models import Q
from django.utils import timezone

from errands.models import Errand
from users.models import CheckOutError
from users.roles import ROLE_ADMIN, ROLE_BOTH, ROLE_USER, get_role, set_role
from adminpanel.utils import random_password

from . import services
from .client import BaleError
from .keyboards import (
    audit_keyboard,
    back_only,
    btn,
    confirm_keyboard,
    inline,
    main_menu,
    main_reply_keyboard,
    new_errand_keyboard,
    new_user_role_keyboard,
    pager,
    role_keyboard,
    settings_keyboard,
    user_card,
    users_menu,
)
from .models import BaleAccount, BotConfig

# دکمه‌های ثابت پایین صفحه ↔ عملیات داخلی
REPLY_ACTIONS = {
    '📊 داشبورد': 'adm:today',
    '⏳ صف تأیید': 'adm:pending',
    '👥 کاربران': 'adm:users',
    '📈 گزارش ماه': 'rep:month',
    '📊 وضعیت امروز': 'status',
    '📝 ثبت مأموریت': 'err:new',
    '📋 مأموریت‌های من': 'err:mine',
    '⚙️ تنظیمات': 'settings',
    '🏠 منو': 'menu:main',
}


logger = logging.getLogger('balebot')
User = get_user_model()

MAX_LOGIN_ATTEMPTS = 5
LOCK_MINUTES = 10


# ============================================================
# ورودی‌ها
# ============================================================

def handle_update(update, client):
    """نقطهٔ ورود هر update بله."""
    if update.get('message'):
        _handle_message(update['message'], client)
    elif update.get('callback_query'):
        _handle_callback(update['callback_query'], client)


def _profile(from_user, chat_id):
    """حساب بله را می‌سازد/به‌روز می‌کند."""
    uid = int(from_user['id'])
    account = BaleAccount.objects.filter(bale_user_id=uid).first()
    if account is None:
        account = BaleAccount(bale_user_id=uid)
    account.chat_id = int(chat_id) if chat_id else (account.chat_id or uid)
    account.username = (from_user.get('username') or '')[:100]
    account.first_name = (from_user.get('first_name') or '')[:100]
    account.last_name = (from_user.get('last_name') or '')[:100]
    account.save()
    return account


def _clear(account):
    account.state = ''
    account.state_data = {}
    account.save()


def _send(account, text, markup=None, client=None):
    return services.safe_send(client, account, text, reply_markup=markup)


# ============================================================
# پیام‌های متنی
# ============================================================

def _handle_message(message, client):
    from_user = message.get('from') or {}
    if not from_user.get('id'):
        return

    chat = message.get('chat') or {}
    account = _profile(from_user, chat.get('id'))
    if account.is_blocked:
        return
    if account.is_linked and not account.django_user.is_active:
        _send(
            account,
            '⛔ حساب شما در سامانه غیرفعال شده است. با مدیر تماس بگیرید.',
            client=client,
        )
        return

    if not BotConfig.get_solo().bot_enabled:
        if account.is_linked and services.is_admin_mode(account):
            _send(
                account,
                '⛔ ربات موقتاً غیرفعال است. برای فعال‌سازی از پنل مدیریت استفاده کنید.',
                client=client,
            )
        return

    if not services.rate_limit_ok(account):
        # ضد اسپم: پیام‌های بیش از حد در یک دقیقه نادیده گرفته می‌شوند.
        return

    text = (message.get('text') or '').strip()
    if text.startswith('/'):
        return _handle_command(account, text, client)

    # دکمه‌های ثابت پایین صفحه
    action = REPLY_ACTIONS.get(text)
    if action:
        _clear(account)
        return _handle_action(account, action, client)

    # در میانهٔ یک گفتگوی چندمرحله‌ای، حتی متن خالی هم باید به اعتبارسنجی
    # همان مرحله برسد (مثلاً «دلیل رد الزامی است»).
    if account.state:
        return _handle_state(account, message, text, client)

    if not text:
        _send(account, 'ℹ️ فقط پیام متنی پشتیبانی می‌شود.', client=client)
        return

    if not account.is_linked:
        return _ask_login(account, client)

    _show_main_menu(account, client)


def _handle_command(account, text, client):
    command = text.split()[0].split('@')[0].lower()

    if command == '/start':
        if account.is_linked:
            _clear(account)
            return _show_main_menu(account, client)
        return _ask_login(account, client)

    if command in ('/menu', '/help'):
        if not account.is_linked:
            return _ask_login(account, client)
        if command == '/help':
            return _send(account, _help_text(), back_only(), client=client)
        _clear(account)
        return _show_main_menu(account, client)

    if command == '/cancel':
        _clear(account)
        if account.is_linked:
            return _show_main_menu(account, client)
        return _ask_login(account, client)

    if command == '/logout':
        return _logout(account, client)

    if command == '/role':
        if not account.is_linked:
            return _ask_login(account, client)
        return _toggle_role(account, client, answer_only=False)

    if command == '/settings':
        if not account.is_linked:
            return _ask_login(account, client)
        return _settings_view(account, client)

    if command == '/password':
        if not account.is_linked:
            return _ask_login(account, client)
        return _password_start(account, client)

    if command == '/week':
        if not account.is_linked:
            return _ask_login(account, client)
        return _show_week(account, client)

    if command == '/stats':
        if not account.is_linked:
            return _ask_login(account, client)
        if not _require_admin(account, client):
            return
        return _admin_stats(account, client)

    if command == '/audit':
        if not account.is_linked:
            return _ask_login(account, client)
        if not _require_admin(account, client):
            return
        return _admin_audit(account, 1, client)

    _send(account, '❓ دستور ناشناخته است. /menu را بزنید.', client=client)


def _help_text():
    return (
        '❓ راهنمای ربات\n\n'
        '/start شروع و منوی اصلی\n'
        '/menu منوی اصلی\n'
        '/role تغییر نقش (برای کاربران دوگانه)\n'
        '/cancel لغو عملیات جاری\n'
        '/logout خروج از حساب\n\n'
        'کارمند: مشاهدهٔ وضعیت امروز، ثبت و لغو مأموریت، گزارش ماه.\n'
        'مدیر: تأیید/رد مأموریت، داشبورد، خطاهای خروج، مدیریت کاربران و پیام همگانی.'
    )


# ============================================================
# ورود
# ============================================================

def _ask_login(account, client):
    account.state = 'login_username'
    account.state_data = {}
    account.save()
    _send(
        account,
        '👋 به ربات حضور و غیاب خوش آمدید.\n\n'
        'برای اتصال حساب، نام کاربری سامانه را بفرستید.\n'
        'اگر حساب کاربری ندارید با مدیر تماس بگیرید.',
        client=client,
    )


def _login_username(account, text, client):
    username = text.strip().lstrip('@')
    if not username:
        _send(account, 'نام کاربری نمی‌تواند خالی باشد. دوباره بفرستید.', client=client)
        return
    account.state_data = {'username': username[:150]}
    account.state = 'login_password'
    account.save()
    _send(
        account,
        '🔒 حالا رمز عبور را بفرستید.\n'
        'پیام رمز بعد از خواندن از گفتگو حذف می‌شود.',
        client=client,
    )


def _login_password(account, message, text, client):
    # رمز نباید در تاریخچهٔ گفتگو بماند (best-effort).
    try:
        client.delete_message(account.chat_id, message.get('message_id'))
    except BaleError:
        pass

    now = timezone.now()
    if account.locked_until and account.locked_until > now:
        wait = int((account.locked_until - now).total_seconds() // 60) + 1
        _send(
            account,
            '⛔ به دلیل تلاش‌های ناموفق، %s دقیقهٔ دیگر دوباره تلاش کنید.'
            % services.fa_digits(wait),
            client=client,
        )
        return

    username = (account.state_data or {}).get('username', '')
    user = authenticate(username=username, password=text)

    if user is None:
        account.failed_attempts += 1
        if account.failed_attempts >= MAX_LOGIN_ATTEMPTS:
            account.failed_attempts = 0
            account.locked_until = now + dt.timedelta(minutes=LOCK_MINUTES)
            account.save()
            _send(
                account,
                '⛔ تلاش‌های ناموفق زیاد شد. %s دقیقه صبر کنید.'
                % services.fa_digits(LOCK_MINUTES),
                client=client,
            )
        else:
            left = MAX_LOGIN_ATTEMPTS - account.failed_attempts
            account.save()
            _send(
                account,
                '❌ نام کاربری یا رمز عبور اشتباه است.\n'
                '%s تلاش دیگر باقی مانده.' % services.fa_digits(left),
                client=client,
            )
        return

    account.django_user = user
    account.linked_at = now
    account.failed_attempts = 0
    account.locked_until = None
    account.state = ''
    account.state_data = {}
    account.active_role = (
        ROLE_ADMIN
        if (user.is_superuser or get_role(user) == ROLE_ADMIN)
        else ROLE_USER
    )
    account.save()

    services.audit(user, 'login', 'bale_account', account.pk, 'ورود از ربات بله')

    _send(
        account,
        '✅ اتصال با موفقیت انجام شد.\nخوش آمدید %s' % services.user_label(user),
        client=client,
    )
    _show_main_menu(account, client, with_reply=True)


def _logout(account, client):
    account.django_user = None
    account.linked_at = None
    account.active_role = ROLE_USER
    account.state = 'login_username'
    account.state_data = {}
    account.save()
    _send(
        account,
        '👋 از حساب خارج شدید.\nبرای ورود دوباره نام کاربری را بفرستید.',
        client=client,
    )


# ============================================================
# منو و نقش
# ============================================================

def _show_main_menu(account, client, with_reply=False):
    if not account.is_linked:
        return _ask_login(account, client)

    admin = services.is_admin_mode(account)
    can_switch = services.can_switch_role(account)
    text = (
        '🏠 منوی اصلی\n\n'
        '👤 %s\n'
        '🎭 نقش فعال: %s\n'
        '📅 %s'
    ) % (
        services.user_label(account.django_user),
        services.role_title(account),
        services.today_jalali(),
    )
    if with_reply:
        # کیبورد ثابت پایین صفحه (یک‌بار پس از ورود/تغییر نقش)
        services.safe_send(
            client, account, text,
            reply_markup=main_reply_keyboard(admin),
        )
    _send(account, text, main_menu(account, admin, can_switch), client=client)


def _toggle_role(account, client, answer_only=False):
    if not services.can_switch_role(account):
        _send(
            account,
            'ℹ️ حساب شما نقش دوگانه (کاربر + مدیر) ندارد؛ نقش فعال شما ثابت است.',
            back_only(),
            client=client,
        )
        return

    account.active_role = (
        ROLE_USER if account.active_role == ROLE_ADMIN else ROLE_ADMIN
    )
    account.state = ''
    account.state_data = {}
    account.save()

    label = 'مدیر' if account.active_role == ROLE_ADMIN else 'کاربر'
    services.audit(
        account.django_user, 'role_switch', 'bale_account',
        account.pk, 'نقش فعال: %s' % label,
    )
    _send(
        account,
        '🔄 نقش فعال شما به «%s» تغییر کرد.' % label,
        client=client,
    )
    _show_main_menu(account, client, with_reply=True)


# ============================================================
# نمایش وضعیت و گزارش
# ============================================================

def _show_status(account, client):
    _clear(account)
    _send(account, services.today_status_text(account.django_user), back_only(), client=client)


def _show_month(account, client):
    _clear(account)
    _send(account, services.month_summary_text(account.django_user), back_only(), client=client)


def _show_week(account, client):
    _clear(account)
    _send(account, services.week_summary_text(account.django_user), back_only(), client=client)


def _send_excel(account, client):
    _clear(account)
    start, today, label = services.month_bounds()
    book, filename = services.build_user_report_xlsx(
        account.django_user, start, today,
    )
    mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    try:
        client.send_document(
            account.chat_id or account.bale_user_id,
            filename,
            book,
            caption='📥 گزارش ماه %s' % label,
            mime_type=mime,
        )
    except BaleError as exc:
        return _send(
            account, '⚠️ ارسال فایل ممکن نشد: %s' % exc,
            back_only(), client=client,
        )
    services.audit(
        account.django_user, 'report_excel', 'user',
        account.django_user_id, label,
    )
    _send(
        account,
        '✅ فایل اکسل گزارش %s ارسال شد.' % label,
        back_only(),
        client=client,
    )


# ============================================================
# تنظیمات و رمز عبور
# ============================================================

def _settings_view(account, client):
    _clear(account)
    _send(
        account,
        '⚙️ تنظیمات\n\n'
        'اعلان‌های دلخواه را روشن/خاموش کنید. تغییرات فوری اعمال می‌شوند.',
        settings_keyboard(account),
        client=client,
    )


def _toggle_setting(account, which, client):
    field_map = {
        'errand': 'notify_errand',
        'daily': 'notify_daily',
        'news': 'notify_news',
    }
    field = field_map.get(which)
    if not field:
        return
    setattr(account, field, not getattr(account, field))
    account.save(update_fields=[field])
    _settings_view(account, client)


def _password_start(account, client):
    account.state = 'password:current'
    account.state_data = {}
    account.save()
    _send(
        account,
        '🔑 تغییر رمز عبور\n\n'
        'رمز فعلی را بفرستید. پیام رمز بعد از خواندن حذف می‌شود.',
        inline([[btn('❌ لغو', 'menu:main')]]),
        client=client,
    )


def _delete_secret(account, message, client):
    try:
        client.delete_message(account.chat_id, message.get('message_id'))
    except BaleError:
        pass


def _password_current(account, message, text, client):
    _delete_secret(account, message, client)
    if not account.django_user.check_password(text):
        _send(
            account,
            '❌ رمز فعلی اشتباه است. دوباره بفرستید یا /cancel بزنید.',
            client=client,
        )
        return
    account.state = 'password:new'
    account.state_data = {}
    account.save()
    _send(
        account,
        '🔒 رمز جدید را بفرستید (حداقل ۸ نویسه).',
        inline([[btn('❌ لغو', 'menu:main')]]),
        client=client,
    )


def _password_new(account, message, text, client):
    _delete_secret(account, message, client)
    if len(text) < 8:
        _send(account, '⚠️ رمز جدید باید حداقل ۸ نویسه باشد.', client=client)
        return
    account.state = 'password:confirm'
    # فقط هش نگه می‌داریم تا رمز خام در دیتابیس ذخیره نشود.
    account.state_data = {'hash': make_password(text)}
    account.save()
    _send(
        account,
        '🔁 برای اطمینان، رمز جدید را دوباره بفرستید.',
        client=client,
    )


def _password_confirm(account, message, text, client):
    _delete_secret(account, message, client)
    stored = (account.state_data or {}).get('hash')
    if not stored or not check_hash(text, stored):
        _clear(account)
        _send(
            account,
            '❌ تکرار رمز مطابقت نداشت. از /password دوباره شروع کنید.',
            back_only(),
            client=client,
        )
        return

    user = account.django_user
    user.password = stored
    user.save(update_fields=['password'])
    _clear(account)
    services.audit(user, 'password_change', 'user', user.pk)
    _send(account, '✅ رمز عبور با موفقیت تغییر کرد.', client=client)
    _show_main_menu(account, client, with_reply=True)


# ============================================================
# ثبت مأموریت
# ============================================================

def _errand_start(account, client):
    account.state = 'errand:start'
    account.state_data = {'draft': {}}
    account.save()
    _send(
        account,
        '📝 ثبت مأموریت\n\n'
        'تاریخ و ساعت شروع را به این شکل بفرستید:\n'
        '«۱۴۰۵/۰۸/۰۲ ۰۹:۰۰»\n\n'
        'یا یکی از دکمه‌های زیر را بزنید:',
        inline([
            [btn('🕘 امروز ۰۹:۰۰', 'err:preset:today9'),
             btn('⏰ همین حالا', 'err:preset:now')],
            [btn('❌ لغو', 'menu:main')],
        ]),
        client=client,
    )


def _preset_start(account, which, client):
    now = timezone.localtime()
    if which == 'now':
        start = now.replace(second=0, microsecond=0)
    elif which == 'today9':
        start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    else:
        return
    _set_start(account, start, client)


def _set_start(account, start, client):
    draft = dict((account.state_data or {}).get('draft') or {})
    draft['start'] = start.isoformat()
    if draft.get('end'):
        end = dt.datetime.fromisoformat(draft['end'])
        if end <= start:
            draft.pop('end', None)
    account.state_data = {'draft': draft}
    account.state = 'errand:end'
    account.save()

    _send(
        account,
        '✅ شروع: %s\n\n'
        'حالا پایان مأموریت را بفرستید (مثل «۱۴۰۵/۰۸/۰۲ ۱۲:۳۰»):'
        % services.dt_label(start),
        inline([
            [btn('🕔 امروز ۱۷:۰۰', 'err:preset:today17'),
             btn('➕ ۲ ساعت بعد', 'err:preset:plus2h')],
            [btn('❌ لغو', 'menu:main')],
        ]),
        client=client,
    )


def _set_end(account, end, client):
    draft = dict((account.state_data or {}).get('draft') or {})
    start_raw = draft.get('start')
    if not start_raw:
        return _errand_start(account, client)

    start = dt.datetime.fromisoformat(start_raw)
    if end <= start:
        _send(
            account,
            '⚠️ پایان مأموریت باید بعد از شروع آن باشد. دوباره بفرستید.',
            client=client,
        )
        return

    draft['end'] = end.isoformat()
    account.state_data = {'draft': draft}
    account.state = 'errand:reason'
    account.save()

    _send(
        account,
        '✅ پایان: %s\n\n'
        'دلیل یا موضوع مأموریت را بنویسید (مثلاً: مراجعه به بانک).'
        % services.dt_label(end),
        client=client,
    )


def _preset_end(account, which, client):
    draft = (account.state_data or {}).get('draft') or {}
    start_raw = draft.get('start')
    if not start_raw:
        return _errand_start(account, client)

    start = dt.datetime.fromisoformat(start_raw)
    now = timezone.localtime()
    if which == 'today17':
        end = now.replace(hour=17, minute=0, second=0, microsecond=0)
    elif which == 'plus2h':
        end = timezone.localtime(start) + dt.timedelta(hours=2)
    else:
        return
    _set_end(account, end, client)


def _submit_errand(account, client):
    draft = (account.state_data or {}).get('draft') or {}
    try:
        start = dt.datetime.fromisoformat(draft['start'])
        end = dt.datetime.fromisoformat(draft['end'])
    except (KeyError, ValueError):
        _clear(account)
        return _send(
            account,
            '⚠️ اطلاعات ناقص است. لطفاً از «ثبت مأموریت» دوباره شروع کنید.',
            new_errand_keyboard(),
            client=client,
        )

    payload = {
        'start': services.to_jalali_string(start),
        'end': services.to_jalali_string(end),
        'reason': draft.get('reason', ''),
        'destination': draft.get('destination', ''),
        'contact': draft.get('contact', ''),
    }
    errand, error = services.create_errand(account.django_user, payload)
    _clear(account)

    if error:
        _send(
            account,
            '⚠️ ثبت نشد: %s\n\nمی‌توانید دوباره تلاش کنید.' % error,
            inline([
                [btn('📝 ثبت دوباره', 'err:new')],
                [btn('🏠 منوی اصلی', 'menu:main')],
            ]),
            client=client,
        )
        return

    _send(
        account,
        '✅ درخواست مأموریت ثبت شد و در انتظار تأیید مدیر است.\n\n'
        '🕒 %s → %s\n'
        '📝 %s\n'
        '🚗 %s'
        % (
            services.dt_label(errand.start_time),
            services.fa_digits(services.clock(errand.end_time)),
            errand.reason,
            errand.destination or '—',
        ),
        new_errand_keyboard(),
        client=client,
    )

    services.notify_admins(
        client,
        '🆕 درخواست مأموریت جدید\n\n'
        '👤 %s\n'
        '🕒 %s → %s\n'
        '📝 %s\n'
        '🚗 %s\n\n'
        'برای بررسی: «در انتظار تأیید» را بزنید.'
        % (
            services.user_label(account.django_user),
            services.dt_label(errand.start_time),
            services.fa_digits(services.clock(errand.end_time)),
            errand.reason,
            errand.destination or '—',
        ),
        exclude_account=account,
    )


def _errand_list(account, client, page=1):
    _clear(account)
    qs = Errand.objects.filter(user=account.django_user).order_by('-start_time')
    total = qs.count()
    if not total:
        return _send(
            account,
            '📋 هنوز مأموریتی ثبت نکرده‌اید.',
            new_errand_keyboard(),
            client=client,
        )

    errands, page, pages = services.paginate(qs, page, per_page=5)
    _send(
        account,
        '📋 مأموریت‌های شما — صفحهٔ %s از %s\n(%s مورد)'
        % (
            services.fa_digits(page),
            services.fa_digits(pages),
            services.fa_digits(total),
        ),
        pager('err:mine', page, pages),
        client=client,
    )
    for errand in errands:
        markup = None
        if errand.is_pending:
            markup = inline([[btn('🚫 لغو این مأموریت', 'err:cancel:%d' % errand.pk)]])
        _send(
            account,
            '%s\n🕒 %s → %s\n📝 %s\n🚗 %s'
            % (
                services.errand_status_badge(errand),
                services.dt_label(errand.start_time),
                services.fa_digits(services.clock(errand.end_time)),
                errand.reason,
                errand.destination or '—',
            ),
            markup,
            client=client,
        )


def _cancel_errand(account, pk, client):
    errand = Errand.objects.filter(pk=pk).select_related('user').first()
    if not errand:
        return _send(account, '⚠️ مأموریت پیدا نشد.', back_only(), client=client)
    if errand.user_id != account.django_user_id and not account.django_user.is_superuser:
        return _send(account, '⛔ این مأموریت متعلق به شما نیست.', back_only(), client=client)
    if errand.cancel(account.django_user):
        _send(
            account,
            '🚫 مأموریت لغو شد.\n📝 %s' % errand.reason,
            back_only(),
            client=client,
        )
    else:
        _send(
            account,
            'ℹ️ این مأموریت دیگر قابل لغو نیست (قبلاً بررسی شده است).',
            back_only(),
            client=client,
        )


# ============================================================
# ابزارهای مدیر
# ============================================================

def _require_admin(account, client):
    if services.is_admin_mode(account):
        return True
    _send(
        account,
        '⛔ این بخش فقط برای مدیر است.\n'
        'اگر نقش دوگانه دارید، اول با دکمهٔ «ورود به حالت مدیر» وارد شوید.',
        back_only(),
        client=client,
    )
    return False


def _admin_pending(account, client, page=1):
    _clear(account)
    qs = Errand.objects.pending().select_related('user').order_by('start_time')
    total = qs.count()
    if not total:
        return _send(
            account, '✅ هیچ درخواست مأموریتی در انتظار بررسی نیست.',
            back_only(), client=client,
        )

    errands, page, pages = services.paginate(qs, page, per_page=5)
    extra = []
    if total > 1:
        extra.append([
            btn('✅ تأیید همهٔ %s مورد' % services.fa_digits(total), 'adm:approve_all'),
        ])
    _send(
        account,
        '⏳ %s درخواست در انتظار — صفحهٔ %s از %s'
        % (services.fa_digits(total), services.fa_digits(page), services.fa_digits(pages)),
        pager('adm:pending', page, pages, extra_rows=extra),
        client=client,
    )
    for errand in errands:
        _send(
            account,
            '👤 %s\n🕒 %s → %s\n📝 %s\n🚗 %s'
            % (
                services.user_label(errand.user),
                services.dt_label(errand.start_time),
                services.fa_digits(services.clock(errand.end_time)),
                errand.reason,
                errand.destination or '—',
            ),
            inline([
                [btn('✅ تأیید', 'err:approve:%d' % errand.pk),
                 btn('❌ رد', 'err:reject:%d' % errand.pk)],
            ]),
            client=client,
        )


def _approve_errand(account, pk, client):
    errand = Errand.objects.filter(pk=pk).select_related('user').first()
    if not errand:
        return _send(account, '⚠️ مأموریت پیدا نشد.', back_only(), client=client)
    if not errand.is_pending:
        return _send(
            account, 'ℹ️ این مأموریت قبلاً بررسی شده است.',
            back_only(), client=client,
        )

    errand.approve(account.django_user)
    services.audit(
        account.django_user, 'errand_approve', 'errand', errand.pk, errand.reason,
    )
    _send(
        account,
        '✅ تأیید شد:\n👤 %s\n📝 %s'
        % (services.user_label(errand.user), errand.reason),
        back_only(),
        client=client,
    )
    services.notify_user(
        client, errand.user,
        '✅ مأموریت شما تأیید شد.\n\n'
        '🕒 %s → %s\n📝 %s'
        % (
            services.dt_label(errand.start_time),
            services.fa_digits(services.clock(errand.end_time)),
            errand.reason,
        ),
    )


def _reject_start(account, pk, client):
    errand = Errand.objects.filter(pk=pk).select_related('user').first()
    if not errand:
        return _send(account, '⚠️ مأموریت پیدا نشد.', back_only(), client=client)
    if not errand.is_pending:
        return _send(
            account, 'ℹ️ این مأموریت قبلاً بررسی شده است.',
            back_only(), client=client,
        )

    account.state = 'reject:%d' % pk
    account.state_data = {'pk': pk}
    account.save()
    _send(
        account,
        '❌ رد مأموریت «%s»\n\nدلیل رد را بنویسید (الزامی؛ کارمند آن را می‌بیند):'
        % errand.reason,
        inline([[btn('❌ لغو', 'menu:main')]]),
        client=client,
    )


def _reject_finish(account, pk, note, client):
    errand = Errand.objects.filter(pk=pk).select_related('user').first()

    note = (note or '').strip()[:500]
    if not note:
        # حالت حفظ می‌شود تا مدیر بتواند همان‌جا دلیل را دوباره بفرستد.
        _send(
            account,
            '⚠️ دلیل رد الزامی است. دوباره بنویسید:',
            inline([[btn('❌ لغو', 'menu:main')]]),
            client=client,
        )
        return

    _clear(account)
    if not errand:
        return _send(account, '⚠️ مأموریت پیدا نشد.', back_only(), client=client)

    if not errand.reject(account.django_user, note=note):
        return _send(
            account, 'ℹ️ این مأموریت قبلاً بررسی شده است.',
            back_only(), client=client,
        )

    services.audit(
        account.django_user, 'errand_reject', 'errand', errand.pk, note,
    )

    _send(
        account,
        '❌ رد شد:\n👤 %s\n📝 %s\n💬 %s'
        % (services.user_label(errand.user), errand.reason, note),
        back_only(),
        client=client,
    )
    services.notify_user(
        client, errand.user,
        '❌ مأموریت شما رد شد.\n\n'
        '🕒 %s → %s\n📝 %s\n💬 دلیل: %s'
        % (
            services.dt_label(errand.start_time),
            services.fa_digits(services.clock(errand.end_time)),
            errand.reason,
            note,
        ),
    )


def _admin_today(account, client):
    _clear(account)
    _send(account, services.admin_today_text(), back_only(), client=client)


def _admin_stats(account, client):
    _clear(account)
    _send(account, services.bot_stats_text(), back_only(), client=client)


def _admin_audit(account, page, client):
    _clear(account)
    text, page, pages = services.audit_list_text(page, per_page=8)
    _send(account, text, audit_keyboard(page, pages), client=client)


def _approve_all_start(account, client):
    count = Errand.objects.pending().count()
    if not count:
        return _send(
            account, '✅ هیچ مأموریتی در انتظار نیست.',
            back_only(), client=client,
        )
    _send(
        account,
        '⚠️ همهٔ %s مأموریت در انتظار تأیید شوند؟\n'
        'این عمل قابل بازگشت نیست (ولی بعداً قابل تغییر وضعیت است).'
        % services.fa_digits(count),
        confirm_keyboard('adm:approve_all:yes'),
        client=client,
    )


def _approve_all_confirm(account, client):
    errands = list(Errand.objects.pending().select_related('user'))
    done = 0
    for errand in errands:
        if errand.approve(account.django_user):
            done += 1
            services.notify_user(
                client, errand.user,
                '✅ مأموریت شما تأیید شد.\n\n'
                '🕒 %s → %s\n📝 %s'
                % (
                    services.dt_label(errand.start_time),
                    services.fa_digits(services.clock(errand.end_time)),
                    errand.reason,
                ),
            )
    services.audit(
        account.django_user, 'errand_approve_all',
        'errand', '', '%d مورد' % done,
    )
    _send(
        account,
        '✅ %s مأموریت تأیید شد.' % services.fa_digits(done),
        back_only(),
        client=client,
    )


def _admin_checkout(account, client, page=1):
    _clear(account)
    qs = (
        CheckOutError.objects.filter(status=CheckOutError.STATUS_PENDING)
        .select_related('user').order_by('-date')
    )
    total = qs.count()
    if not total:
        return _send(
            account, '✅ خطای خروج در انتظاری وجود ندارد.',
            back_only(), client=client,
        )

    errors, page, pages = services.paginate(qs, page, per_page=5)
    _send(
        account,
        '⚠️ %s خطای خروج — صفحهٔ %s از %s'
        % (services.fa_digits(total), services.fa_digits(page), services.fa_digits(pages)),
        pager('adm:checkout', page, pages),
        client=client,
    )
    for error in errors:
        _send(
            account,
            '👤 %s\n📅 %s\n🕒 خروج خودکار: %s'
            % (
                services.user_label(error.user),
                services.jalali_full(error.date),
                services.dt_label(error.auto_out_time),
            ),
            inline([
                [btn('✅ رفع شد', 'cerr:resolve:%d' % error.pk)],
            ]),
            client=client,
        )


def _resolve_checkout(account, pk, client):
    error = CheckOutError.objects.filter(
        pk=pk, status=CheckOutError.STATUS_PENDING,
    ).first()
    if not error:
        return _send(account, 'ℹ️ این خطا قبلاً رفع شده است.', back_only(), client=client)

    error.status = CheckOutError.STATUS_RESOLVED
    error.resolved_by = account.django_user
    error.resolved_at = timezone.now()
    error.note = ((error.note or '') + '\nرفع از ربات بله').strip()
    error.save(update_fields=['status', 'resolved_by', 'resolved_at', 'note'])
    services.audit(
        account.django_user, 'checkout_resolve', 'checkout_error',
        error.pk, error.user.get_username(),
    )
    _send(
        account,
        '✅ خطای خروج %s رفع شد.' % services.user_label(error.user),
        back_only(),
        client=client,
    )


# ---------- کاربران ----------

def _users_menu(account, client):
    _clear(account)
    _send(account, '👥 مدیریت کاربران', users_menu(), client=client)


def _user_search_start(account, client):
    account.state = 'user_search'
    account.state_data = {}
    account.save()
    _send(
        account,
        '🔍 نام یا نام کاربری را بنویسید:',
        inline([[btn('❌ لغو', 'menu:main')]]),
        client=client,
    )


def _user_search(account, query, client):
    _clear(account)
    q = (query or '').strip()
    users = (
        User.objects.filter(
            Q(username__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
        )
        .order_by('username')[:10]
    )
    users = list(users)
    if not users:
        return _send(account, '🔍 کاربری پیدا نشد.', users_menu(), client=client)

    _send(
        account,
        '🔍 %s نتیجه:' % services.fa_digits(len(users)),
        users_menu(),
        client=client,
    )
    for user in users:
        _send(
            account,
            '👤 %s\n🎭 %s | %s'
            % (
                services.user_label(user),
                _role_label(user),
                '✅ فعال' if user.is_active else '⛔ غیرفعال',
            ),
            inline([[btn('مشاهده و مدیریت', 'usr:%d' % user.pk)]]),
            client=client,
        )


def _role_label(user):
    if user.is_superuser:
        return 'سوپرادمین'
    role = get_role(user)
    return {
        ROLE_USER: 'کاربر',
        ROLE_ADMIN: 'مدیر',
        ROLE_BOTH: 'کاربر + مدیر',
    }.get(role, role)


def _user_card_view(account, pk, client):
    _clear(account)
    user = User.objects.filter(pk=pk).first()
    if not user:
        return _send(account, '⚠️ کاربر پیدا نشد.', users_menu(), client=client)
    _send(
        account,
        '👤 %s\n'
        '🆔 نام کاربری: %s\n'
        '🎭 نقش: %s\n'
        'وضعیت: %s\n'
        '📅 آخرین ورود: %s'
        % (
            services.user_label(user),
            user.get_username(),
            _role_label(user),
            '✅ فعال' if user.is_active else '⛔ غیرفعال',
            services.dt_label(user.last_login) if user.last_login else '—',
        ),
        user_card(user),
        client=client,
    )


def _reset_password(account, pk, client):
    user = User.objects.filter(pk=pk).first()
    if not user:
        return _send(account, '⚠️ کاربر پیدا نشد.', users_menu(), client=client)

    password = random_password(12)
    user.set_password(password)
    user.save(update_fields=['password'])
    services.audit(
        account.django_user, 'user_reset_password', 'user', user.pk,
        user.get_username(),
    )
    _send(
        account,
        '🔑 رمز عبور %s به این مقدار تغییر کرد:\n\n'
        '`%s`\n\n'
        '⚠️ این رمز فقط یک‌بار نمایش داده می‌شود؛ آن را به کاربر برسانید '
        'و از او بخواهید بعد از ورود تغییرش دهد.'
        % (services.user_label(user), password),
        inline([[btn('↩️ بازگشت', 'usr:%d' % user.pk)]]),
        client=client,
    )


def _role_menu(account, pk, client):
    user = User.objects.filter(pk=pk).first()
    if not user:
        return _send(account, '⚠️ کاربر پیدا نشد.', users_menu(), client=client)
    _send(
        account,
        '🎭 نقش جدید %s را انتخاب کنید:\nنقش فعلی: %s'
        % (services.user_label(user), _role_label(user)),
        role_keyboard(user),
        client=client,
    )


def _set_user_role(account, pk, role, client):
    user = User.objects.filter(pk=pk).first()
    if not user:
        return _send(account, '⚠️ کاربر پیدا نشد.', users_menu(), client=client)
    if user.is_superuser:
        return _send(
            account, '⛔ نقش سوپرادمین قابل تغییر نیست.',
            inline([[btn('↩️ بازگشت', 'usr:%d' % user.pk)]]), client=client,
        )
    if role not in (ROLE_USER, ROLE_ADMIN, ROLE_BOTH):
        return _send(account, '⚠️ نقش نامعتبر است.', users_menu(), client=client)

    set_role(user, role)
    services.audit(
        account.django_user, 'user_set_role', 'user', user.pk, role,
    )
    _send(
        account,
        '✅ نقش %s به «%s» تغییر کرد.'
        % (services.user_label(user), _role_label(user)),
        inline([[btn('↩️ بازگشت', 'usr:%d' % user.pk)]]),
        client=client,
    )


def _toggle_active(account, pk, client):
    user = User.objects.filter(pk=pk).first()
    if not user:
        return _send(account, '⚠️ کاربر پیدا نشد.', users_menu(), client=client)
    if user.pk == account.django_user_id:
        return _send(
            account, '⛔ نمی‌توانید حساب خودتان را غیرفعال کنید.',
            inline([[btn('↩️ بازگشت', 'usr:%d' % user.pk)]]), client=client,
        )
    if user.is_superuser:
        return _send(
            account, '⛔ حساب سوپرادمین قابل غیرفعال‌سازی نیست.',
            inline([[btn('↩️ بازگشت', 'usr:%d' % user.pk)]]), client=client,
        )

    user.is_active = not user.is_active
    user.save(update_fields=['is_active'])
    services.audit(
        account.django_user, 'user_toggle_active', 'user', user.pk,
        'فعال' if user.is_active else 'غیرفعال',
    )
    _send(
        account,
        '%s %s شد.'
        % (
            '✅' if user.is_active else '⛔',
            services.user_label(user),
        ),
        inline([[btn('↩️ بازگشت', 'usr:%d' % user.pk)]]),
        client=client,
    )


def _new_user_start(account, client):
    account.state = 'user_new:username'
    account.state_data = {'new_user': {}}
    account.save()
    _send(
        account,
        '➕ ساخت کاربر جدید\n\nنام کاربری را بفرستید '
        '(فقط حروف انگلیسی/عدد/نقطه):',
        inline([[btn('❌ لغو', 'menu:main')]]),
        client=client,
    )


def _new_user_username(account, text, client):
    from adminpanel.utils import get_valid_username

    username = get_valid_username(text)
    if not username or username == 'user':
        _send(account, '⚠️ نام کاربری معتبر نیست. دوباره بفرستید.', client=client)
        return
    if User.objects.filter(username=username).exists():
        _send(account, '⚠️ این نام کاربری قبلاً گرفته شده. دیگری بفرستید.', client=client)
        return

    data = dict((account.state_data or {}).get('new_user') or {})
    data['username'] = username
    account.state_data = {'new_user': data}
    account.state = 'user_new:name'
    account.save()
    _send(
        account,
        '✅ نام کاربری: %s\n\nنام و نام خانوادگی را با فاصله بفرستید '
        '(یا «رد کردن»).' % username,
        inline([[btn('⏭ رد کردن', 'usr:newskip:name')]]),
        client=client,
    )


def _new_user_name(account, text, client):
    text = (text or '').strip()
    parts = text.split(maxsplit=1)
    data = dict((account.state_data or {}).get('new_user') or {})
    data['first_name'] = parts[0][:100] if parts else ''
    data['last_name'] = parts[1][:100] if len(parts) > 1 else ''
    account.state_data = {'new_user': data}
    account.state = 'user_new:role'
    account.save()
    _send(
        account,
        '✅ نام ثبت شد.\n\nنقش کاربر را انتخاب کنید:',
        new_user_role_keyboard(),
        client=client,
    )


def _new_user_create(account, role, client):
    data = (account.state_data or {}).get('new_user') or {}
    username = data.get('username')
    if not username or User.objects.filter(username=username).exists():
        _clear(account)
        return _send(
            account, '⚠️ ساخت کاربر ممکن نشد؛ دوباره تلاش کنید.',
            users_menu(), client=client,
        )

    password = random_password(12)
    user = User(
        username=username,
        first_name=data.get('first_name', ''),
        last_name=data.get('last_name', ''),
        is_active=True,
    )
    user.set_password(password)
    user.save()
    if role in (ROLE_USER, ROLE_ADMIN, ROLE_BOTH):
        set_role(user, role)

    services.audit(
        account.django_user, 'user_create', 'user', user.pk,
        'نقش: %s' % role,
    )
    _clear(account)
    _send(
        account,
        '✅ کاربر ساخته شد:\n\n'
        '👤 %s\n'
        '🆔 نام کاربری: %s\n'
        '🔑 رمز عبور: %s\n'
        '🎭 نقش: %s\n\n'
        '⚠️ رمز فقط یک‌بار نمایش داده می‌شود.'
        % (
            services.user_label(user),
            user.get_username(),
            password,
            _role_label(user),
        ),
        inline([[btn('👤 مشاهدهٔ کاربر', 'usr:%d' % user.pk)],
                [btn('🏠 منوی اصلی', 'menu:main')]]),
        client=client,
    )


# ---------- پیام همگانی ----------

def _broadcast_start(account, client):
    account.state = 'broadcast'
    account.state_data = {}
    account.save()
    _send(
        account,
        '📢 متن پیام همگانی را بنویسید.\n'
        'پیام برای همهٔ حساب‌های متصل به ربات (به‌جز خودتان) ارسال می‌شود.',
        inline([[btn('❌ لغو', 'menu:main')]]),
        client=client,
    )


def _broadcast_send(account, text, client):
    _clear(account)
    text = (text or '').strip()
    if not text:
        return _send(account, '⚠️ متن پیام خالی است.', back_only(), client=client)

    sent = 0
    failed = 0
    qs = (
        BaleAccount.objects
        .filter(is_blocked=False, django_user__isnull=False)
        .exclude(pk=account.pk)
    )
    for target in qs:
        if services.safe_send(client, target, '📢 پیام از مدیریت:\n\n' + text) is not None:
            sent += 1
        else:
            failed += 1

    services.audit(
        account.django_user, 'broadcast', '', '',
        'ارسال به %d حساب' % sent,
    )
    _send(
        account,
        '📢 پیام همگانی ارسال شد.\n✅ %s | ⛔ %s'
        % (services.fa_digits(sent), services.fa_digits(failed)),
        back_only(),
        client=client,
    )


# ============================================================
# وضعیت‌های گفتگو
# ============================================================

def _handle_state(account, message, text, client):
    state = account.state

    if state == 'login_username':
        return _login_username(account, text, client)
    if state == 'login_password':
        return _login_password(account, message, text, client)

    if state == 'password:current':
        return _password_current(account, message, text, client)
    if state == 'password:new':
        return _password_new(account, message, text, client)
    if state == 'password:confirm':
        return _password_confirm(account, message, text, client)

    if not account.is_linked:
        return _ask_login(account, client)

    if state == 'errand:start':
        value, error = services.parse_jalali_datetime(text)
        if error:
            return _send(account, '⚠️ %s\nمثال: ۱۴۰۵/۰۸/۰۲ ۰۹:۰۰' % error, client=client)
        return _set_start(account, value, client)

    if state == 'errand:end':
        value, error = services.parse_jalali_datetime(text)
        if error:
            return _send(account, '⚠️ %s\nمثال: ۱۴۰۵/۰۸/۰۲ ۱۲:۳۰' % error, client=client)
        return _set_end(account, value, client)

    if state == 'errand:reason':
        reason = text.strip()
        if not reason:
            return _send(account, '⚠️ دلیل مأموریت نمی‌تواند خالی باشد.', client=client)
        if len(reason) > 500:
            return _send(account, '⚠️ دلیل حداکثر ۵۰۰ نویسه است.', client=client)
        draft = dict((account.state_data or {}).get('draft') or {})
        draft['reason'] = reason
        account.state_data = {'draft': draft}
        account.state = 'errand:destination'
        account.save()
        return _send(
            account,
            '✅ دلیل ثبت شد.\n\nمقصد را بنویسید (اختیاری):',
            inline([
                [btn('⏭ رد کردن', 'err:skip:destination')],
                [btn('❌ لغو', 'menu:main')],
            ]),
            client=client,
        )

    if state == 'errand:destination':
        destination = text.strip()
        if len(destination) > 200:
            return _send(account, '⚠️ مقصد حداکثر ۲۰۰ نویسه است.', client=client)
        draft = dict((account.state_data or {}).get('draft') or {})
        draft['destination'] = destination
        account.state_data = {'draft': draft}
        account.state = 'errand:contact'
        account.save()
        return _send(
            account,
            '✅ مقصد ثبت شد.\n\nشمارهٔ تماس (اختیاری) را بفرستید:',
            inline([
                [btn('⏭ رد کردن', 'err:skip:contact')],
                [btn('❌ لغو', 'menu:main')],
            ]),
            client=client,
        )

    if state == 'errand:contact':
        contact = text.strip()
        if len(contact) > 100:
            return _send(account, '⚠️ شمارهٔ تماس حداکثر ۱۰۰ نویسه است.', client=client)
        draft = dict((account.state_data or {}).get('draft') or {})
        draft['contact'] = contact
        account.state_data = {'draft': draft}
        account.save()
        return _submit_errand(account, client)

    if state.startswith('reject:'):
        pk = int(state.split(':', 1)[1])
        return _reject_finish(account, pk, text, client)

    if state == 'user_search':
        return _user_search(account, text, client)

    if state == 'user_new:username':
        return _new_user_username(account, text, client)

    if state == 'user_new:name':
        return _new_user_name(account, text, client)

    if state == 'broadcast':
        return _broadcast_send(account, text, client)

    # حالت ناشناخته — پاک‌سازی و بازگشت به منو
    _clear(account)
    _send(account, 'ℹ️ عملیات قبلی لغو شد.', back_only(), client=client)


# ============================================================
# دکمه‌های inline
# ============================================================

def _page_arg(data, default=1):
    try:
        return max(1, int(data.rsplit(':', 1)[1]))
    except (IndexError, ValueError):
        return default


def _handle_callback(cq, client):
    from_user = cq.get('from') or {}
    if not from_user.get('id'):
        return

    message = cq.get('message') or {}
    chat = message.get('chat') or {}
    account = _profile(from_user, chat.get('id'))

    try:
        client.answer_callback_query(cq.get('id'))
    except BaleError:
        pass

    if account.is_blocked:
        return
    if account.is_linked and not account.django_user.is_active:
        _send(
            account,
            '⛔ حساب شما در سامانه غیرفعال شده است. با مدیر تماس بگیرید.',
            client=client,
        )
        return

    if not BotConfig.get_solo().bot_enabled:
        if account.is_linked and services.is_admin_mode(account):
            _send(
                account,
                '⛔ ربات موقتاً غیرفعال است.',
                client=client,
            )
        return

    if not services.rate_limit_ok(account):
        return

    data = (cq.get('data') or '').strip()
    if not account.is_linked:
        return _ask_login(account, client)
    return _handle_action(account, data, client)


def _handle_action(account, data, client):
    # ---------- عمومی ----------
    if data in ('', 'noop'):
        return
    if data in ('menu', 'menu:main', 'back'):
        _clear(account)
        return _show_main_menu(account, client)
    if data == 'role:toggle':
        return _toggle_role(account, client)
    if data == 'logout':
        return _logout(account, client)
    if data == 'status':
        return _show_status(account, client)
    if data == 'rep:month':
        return _show_month(account, client)
    if data == 'rep:week':
        return _show_week(account, client)
    if data == 'rep:excel':
        return _send_excel(account, client)

    # ---------- تنظیمات ----------
    if data == 'settings':
        return _settings_view(account, client)
    if data.startswith('settings:toggle:'):
        return _toggle_setting(account, data.split(':', 2)[2], client)
    if data == 'settings:password':
        return _password_start(account, client)

    # ---------- مأموریت کارمند ----------
    if data == 'err:new':
        return _errand_start(account, client)
    if data == 'err:mine':
        return _errand_list(account, client, 1)
    if data.startswith('err:mine:'):
        return _errand_list(account, client, _page_arg(data))
    if data.startswith('err:preset:'):
        which = data.split(':', 2)[2]
        if account.state == 'errand:start':
            return _preset_start(account, which, client)
        if account.state == 'errand:end':
            return _preset_end(account, which, client)
        return
    if data.startswith('err:skip:'):
        field = data.split(':', 2)[2]
        draft = dict((account.state_data or {}).get('draft') or {})
        draft[field] = ''
        account.state_data = {'draft': draft}
        if field == 'destination':
            account.state = 'errand:contact'
            account.save()
            return _send(
                account,
                'مقصد رد شد.\n\nشمارهٔ تماس (اختیاری) را بفرستید:',
                inline([
                    [btn('⏭ رد کردن', 'err:skip:contact')],
                    [btn('❌ لغو', 'menu:main')],
                ]),
                client=client,
            )
        account.save()
        return _submit_errand(account, client)
    if data.startswith('err:cancel:'):
        return _cancel_errand(account, int(data.split(':')[2]), client)

    # ---------- مدیریت مأموریت ----------
    if data.startswith('err:approve:'):
        if not _require_admin(account, client):
            return
        return _approve_errand(account, int(data.split(':')[2]), client)
    if data.startswith('err:reject:'):
        if not _require_admin(account, client):
            return
        return _reject_start(account, int(data.split(':')[2]), client)

    # ---------- مدیر ----------
    if data.startswith('adm:'):
        if not _require_admin(account, client):
            return
        if data == 'adm:pending':
            return _admin_pending(account, client, 1)
        if data.startswith('adm:pending:'):
            return _admin_pending(account, client, _page_arg(data))
        if data == 'adm:today':
            return _admin_today(account, client)
        if data == 'adm:checkout':
            return _admin_checkout(account, client, 1)
        if data.startswith('adm:checkout:'):
            return _admin_checkout(account, client, _page_arg(data))
        if data == 'adm:users':
            return _users_menu(account, client)
        if data == 'adm:broadcast':
            return _broadcast_start(account, client)
        if data == 'adm:stats':
            return _admin_stats(account, client)
        if data.startswith('adm:audit:'):
            return _admin_audit(account, _page_arg(data), client)
        if data == 'adm:approve_all':
            return _approve_all_start(account, client)
        if data == 'adm:approve_all:yes':
            return _approve_all_confirm(account, client)
        return

    if data.startswith('cerr:resolve:'):
        if not _require_admin(account, client):
            return
        return _resolve_checkout(account, int(data.split(':')[2]), client)

    # ---------- کاربران ----------
    if data.startswith('usr:'):
        if not _require_admin(account, client):
            return
        parts = data.split(':')
        if data == 'usr:search':
            return _user_search_start(account, client)
        if data == 'usr:new':
            return _new_user_start(account, client)
        if data == 'usr:newskip:name':
            draft = (account.state_data or {}).get('new_user') or {}
            draft['first_name'] = ''
            draft['last_name'] = ''
            account.state_data = {'new_user': draft}
            account.state = 'user_new:role'
            account.save()
            return _send(
                account,
                '✅ نام رد شد.\n\nنقش کاربر را انتخاب کنید:',
                new_user_role_keyboard(),
                client=client,
            )
        if parts[1] == 'newrole' and len(parts) >= 3:
            return _new_user_create(account, parts[2], client)
        if parts[1] == 'reset' and len(parts) >= 3:
            return _reset_password(account, int(parts[2]), client)
        if parts[1] == 'role' and len(parts) >= 3:
            return _role_menu(account, int(parts[2]), client)
        if parts[1] == 'setrole' and len(parts) >= 4:
            return _set_user_role(account, int(parts[2]), parts[3], client)
        if parts[1] == 'toggle' and len(parts) >= 3:
            return _toggle_active(account, int(parts[2]), client)
        if len(parts) >= 2 and parts[1].isdigit():
            return _user_card_view(account, int(parts[1]), client)
        return

    _send(account, '❓ گزینهٔ ناشناخته.', back_only(), client=client)
