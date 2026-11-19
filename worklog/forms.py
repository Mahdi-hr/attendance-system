"""فرم تعریف/ویرایش پروژه."""

from django import forms

from .models import Project


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['name', 'code', 'description', 'sort_order', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'مثلاً zabbix',
            }),
            'code': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'zabbix (اختیاری)',
            }),
            'description': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'توضیح کوتاه (اختیاری)',
            }),
            'sort_order': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 0,
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'form-check-input',
            }),
        }
