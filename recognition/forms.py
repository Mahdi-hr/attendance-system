from django.forms import ModelForm
from django.contrib.auth.models import User
from django import forms
from django.forms.widgets import MultiWidget, Select
import jdatetime
import datetime


class usernameForm(forms.Form):
	username=forms.CharField(max_length=30)


class JalaliDateWidget(MultiWidget):
	def __init__(self, attrs=None):
		today = jdatetime.date.today()
		years = [(y, y) for y in range(today.year - 10, today.year + 2)]
		months = [(m, m) for m in range(1, 13)]
		days = [(d, d) for d in range(1, 32)]
		widgets = [
			Select(attrs=attrs, choices=[('', 'سال')] + years),
			Select(attrs=attrs, choices=[('', 'ماه')] + months),
			Select(attrs=attrs, choices=[('', 'روز')] + days),
		]
		super().__init__(widgets, attrs)

	def decompress(self, value):
		if value:
			j = jdatetime.date.fromgregorian(date=value)
			return [j.year, j.month, j.day]
		return [None, None, None]


class JalaliDateField(forms.MultiValueField):
	widget = JalaliDateWidget

	def __init__(self, *args, **kwargs):
		fields = (
			forms.IntegerField(),
			forms.IntegerField(),
			forms.IntegerField(),
		)
		super().__init__(fields=fields, require_all_fields=True, *args, **kwargs)

	def compress(self, data_list):
		if data_list and all(x not in (None, '') for x in data_list):
			y, m, d = int(data_list[0]), int(data_list[1]), int(data_list[2])
			try:
				jd = jdatetime.date(y, m, d)
				return jd.togregorian()
			except ValueError:
				raise forms.ValidationError("تاریخ نامعتبر است")
		return None


class DateForm(forms.Form):
	date = JalaliDateField(label="تاریخ")


class UsernameAndDateForm(forms.Form):
	username=forms.CharField(max_length=30)
	date_from = JalaliDateField(label="از تاریخ", required=False)
	date_to = JalaliDateField(label="تا تاریخ", required=False)


class DateForm_2(forms.Form):
	date_from = JalaliDateField(label="از تاریخ", required=False)
	date_to = JalaliDateField(label="تا تاریخ", required=False)


class JalaliDatePickerInput(forms.TextInput):
	def __init__(self, attrs=None):
		default = {
			'readonly': 'readonly',
			'class': 'jalali-date form-control',
			'placeholder': '۱۴۰۳/۰۵/۰۷',
			'autocomplete': 'off',
		}
		if attrs:
			default.update(attrs)
		super().__init__(attrs=default)


class JalaliDateTimePickerInput(forms.TextInput):
	"""Text input for Jalali date + time (e.g. ۱۴۰۳/۰۵/۰۷ ۱۷:۳۰)."""
	def __init__(self, attrs=None):
		default = {
			'readonly': 'readonly',
			'class': 'jalali-date form-control',
			'data-time': 'true',
			'placeholder': '۱۴۰۳/۰۵/۰۷ ۱۷:۳۰',
			'autocomplete': 'off',
		}
		if attrs:
			default.update(attrs)
		super().__init__(attrs=default)


class JalaliDatePickerField(forms.CharField):
	"""Text input holding a Jalali date like ۱۴۰۳/۰۵/۰۷.

	clean() converts it to a gregorian datetime.date, matching JalaliDateField.
	"""
	widget = JalaliDatePickerInput

	def __init__(self, *args, **kwargs):
		kwargs.setdefault('label', 'تاریخ')
		kwargs.setdefault('max_length', 32)
		super().__init__(*args, **kwargs)

	def clean(self, value):
		value = super().clean(value)
		if not value:
			return None
		import re
		parts = re.split(r'[/\-]', value.strip())
		if len(parts) != 3:
			raise forms.ValidationError("تاریخ نامعتبر است")
		try:
			digits = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
			parts = [p.translate(digits) for p in parts]
			jd = jdatetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
		except ValueError:
			raise forms.ValidationError("تاریخ نامعتبر است")
		return jd.togregorian()


class JalaliDateTimePickerField(forms.CharField):
	"""Text input holding a Jalali datetime like ۱۴۰۳/۰۵/۰۷ ۱۷:۳۰.

	clean() converts it to a timezone-aware gregorian datetime.
	"""
	widget = JalaliDateTimePickerInput

	def __init__(self, *args, **kwargs):
		kwargs.setdefault('label', 'تاریخ و ساعت')
		kwargs.setdefault('max_length', 40)
		super().__init__(*args, **kwargs)

	def clean(self, value):
		value = super().clean(value)
		if not value:
			return None
		import re
		from django.utils import timezone
		# Parse Jalali datetime: "۱۴۰۳/۰۵/۰۷ ۱۷:۳۰" or "1403/05/07 17:30"
		digits = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
		text = value.translate(digits).strip()
		# Split date and time parts
		m = re.match(r'^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})[ T](\d{1,2}):(\d{2})$', text)
		if not m:
			raise forms.ValidationError("قالب تاریخ و ساعت نامعتبر است. مثال: ۱۴۰۳/۰۵/۰۷ ۱۷:۳۰")
		jy, jm, jd, hh, mn = map(int, m.groups())
		try:
			jd = jdatetime.datetime(jy, jm, jd, hh, mn)
		except ValueError:
			raise forms.ValidationError("تاریخ یا ساعت نامعتبر است")
		greg = jd.togregorian()
		if timezone.is_naive(greg):
			greg = timezone.make_aware(greg, timezone.get_current_timezone())
		return greg


class DateRangeForm(forms.Form):
	date_from = JalaliDatePickerField(label="از تاریخ", required=False)
	date_to = JalaliDatePickerField(label="تا تاریخ", required=False)


class EmployeeDateRangeForm(forms.Form):
	username = forms.CharField(max_length=30)
	date_from = JalaliDatePickerField(label="از تاریخ", required=False)
	date_to = JalaliDatePickerField(label="تا تاریخ", required=False)


class BreakForm(forms.Form):
	username = forms.CharField(max_length=30, widget=forms.HiddenInput())
	reason = forms.CharField(
		max_length=255,
		label="علت خروج موقت",
		widget=forms.TextInput(attrs={'placeholder': 'مثلاً: ناهار، جلسه، ...', 'class': 'form-control'}),
		required=True
	)
