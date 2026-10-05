from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm


class SmartRegisterForm(UserCreationForm):
    first_name = forms.CharField(
        max_length=30, label='نام',
        widget=forms.TextInput(attrs={'class': 'form-control', 'dir': 'rtl', 'placeholder': 'نام'}),
        required=False
    )
    last_name = forms.CharField(
        max_length=30, label='نام خانوادگی',
        widget=forms.TextInput(attrs={'class': 'form-control', 'dir': 'rtl', 'placeholder': 'نام خانوادگی'}),
        required=False
    )

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'password1', 'password2']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({
            'class': 'form-control', 'dir': 'ltr', 'placeholder': 'نام کاربری (انگلیسی)'
        })
        self.fields['password1'].widget.attrs.update({
            'class': 'form-control', 'dir': 'ltr', 'placeholder': 'رمز عبور'
        })
        self.fields['password2'].widget.attrs.update({
            'class': 'form-control', 'dir': 'ltr', 'placeholder': 'تکرار رمز عبور'
        })


class SmartLoginForm(AuthenticationForm):
    """فرم ورود با پشتیبانی کامل از autocomplete مرورگرها."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Username field - username autocomplete
        self.fields['username'].widget.attrs.update({
            'class': 'form-control',
            'dir': 'ltr',
            'placeholder': 'نام کاربری',
            'autocomplete': 'username',      # مهم برای ذخیره/پیشنهاد کاربر
            'autocorrect': 'off',
            'autocapitalize': 'off',
            'spellcheck': 'false',
        })
        self.fields['username'].label = 'نام کاربری'
        
        # Password field - current-password autocomplete
        self.fields['password'].widget.attrs.update({
            'class': 'form-control',
            'dir': 'ltr',
            'placeholder': 'رمز عبور',
            'autocomplete': 'current-password',  # مهم برای ذخیره/پیشنهاد رمز
            'autocorrect': 'off',
            'autocapitalize': 'off',
            'spellcheck': 'false',
        })
        self.fields['password'].label = 'رمز عبور'


class UserEditForm(forms.ModelForm):
    first_name = forms.CharField(
        max_length=30, label='نام',
        widget=forms.TextInput(attrs={'class': 'form-control', 'dir': 'rtl'}),
        required=False
    )
    last_name = forms.CharField(
        max_length=30, label='نام خانوادگی',
        widget=forms.TextInput(attrs={'class': 'form-control', 'dir': 'rtl'}),
        required=False
    )
    username = forms.CharField(
        max_length=30, label='نام کاربری',
        widget=forms.TextInput(attrs={'class': 'form-control', 'dir': 'ltr'}),
        disabled=True
    )
    new_password = forms.CharField(
        label='رمز عبور جدید (اختیاری)',
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'dir': 'ltr', 'placeholder': 'رمز جدید را وارد کنید'}),
        required=False,
        help_text='خالی بگذارید اگر می‌خواهید رمز عبور تغییر نکند.'
    )
    confirm_password = forms.CharField(
        label='تکرار رمز عبور جدید',
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'dir': 'ltr', 'placeholder': 'رمز جدید را تکرار کنید'}),
        required=False,
    )

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name']

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get('new_password')
        confirm_password = cleaned_data.get('confirm_password')
        
        if new_password or confirm_password:
            if not new_password:
                raise forms.ValidationError('رمز عبور جدید الزامی است.')
            if not confirm_password:
                raise forms.ValidationError('تکرار رمز عبور الزامی است.')
            if new_password != confirm_password:
                raise forms.ValidationError('رمز عبور و تکرار آن یکسان نیستند.')
            if len(new_password) < 8:
                raise forms.ValidationError('رمز عبور باید حداقل ۸ کاراکتر باشد.')
        return cleaned_data
