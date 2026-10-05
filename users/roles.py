"""Helper functions for the user role system (user / admin / both).

جایگزینِ چک قدیمیِ `request.user.username == 'admin'`:
- superuser همیشه ادمین محسوب می‌شود (بدون نیاز به رکورد پروفایل).
- نقش «both» فقط وقتی دسترسی ادمین دارد که در session پرچم admin_mode روشن باشد.
"""
from .models import UserProfile

ROLE_USER = UserProfile.ROLE_USER
ROLE_ADMIN = UserProfile.ROLE_ADMIN
ROLE_BOTH = UserProfile.ROLE_BOTH

VALID_ROLES = (ROLE_USER, ROLE_ADMIN, ROLE_BOTH)


def get_role(user):
	"""نقش یک کاربر: 'user' | 'admin' | 'both'.

	برای superuser و کاربرِ «admin» (سازگاری با سیستم قبلی) همیشه 'admin'.
	"""
	if not user.is_authenticated:
		return ROLE_USER
	if user.is_superuser or user.username == 'admin':
		return ROLE_ADMIN
	profile, _ = UserProfile.objects.get_or_create(user=user)
	return profile.role


def set_role(user, role):
	"""نقش کاربر را تغییر می‌دهد (superuser را نمی‌شود تغییر داد)."""
	if user.is_superuser or role not in VALID_ROLES:
		return
	profile, _ = UserProfile.objects.get_or_create(user=user)
	if profile.role != role:
		profile.role = role
		profile.save(update_fields=['role'])


def is_admin_user(user):
	"""True اگر کاربر نقش ادمین یا «هر دو» داشته باشد (بدون درنظرگرفتن حالت session)."""
	if not user.is_authenticated:
		return False
	if user.is_superuser:
		return True
	return get_role(user) in (ROLE_ADMIN, ROLE_BOTH)


def is_pure_admin(user):
	"""True اگر کاربر فقط ادمین باشد (نه «هر دو»). برای صفحات مخصوص کارمند."""
	if not user.is_authenticated:
		return False
	if user.is_superuser:
		return True
	return get_role(user) == ROLE_ADMIN


def can_access_admin(request):
	"""True اگر کاربرِ جاری هم‌اکنون مجاز به مشاهدهٔ بخش مدیریت باشد.

	کاربران «both» فقط وقتی می‌توانند به بخش مدیریت بروند که دکمهٔ
	«ورود به حالت ادمین» را زده باشند (پرچم admin_mode در session).
	"""
	user = request.user
	if not user.is_authenticated:
		return False
	if not is_admin_user(user):
		return False
	if get_role(user) == ROLE_BOTH and not request.session.get('admin_mode'):
		return False
	return True