"""Context processor: متغیرهای نقش کاربر برای همهٔ تمپلیت‌ها.

- user_role        : 'user' | 'admin' | 'both'
- admin_mode       : آیا کاربر «both» هم‌اکنون در حالت ادمین است؟
- can_access_admin : آیا کاربر مجاز به دیدن بخش مدیریت است؟
"""
from .roles import get_role, can_access_admin


def role_context(request):
    user = request.user
    return {
        'user_role': get_role(user),
        'admin_mode': bool(request.session.get('admin_mode')),
        'can_access_admin': can_access_admin(request),
    }