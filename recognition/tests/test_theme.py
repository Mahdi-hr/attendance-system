"""تست انتخاب‌گر تم و وجود چهار تم در قالب پایه."""

from django.test import TestCase


class ThemePickerTests(TestCase):
    def test_login_page_has_all_four_themes(self):
        response = self.client.get('/login/')
        self.assertEqual(response.status_code, 200)

        for theme in ('dark', 'light', 'sand', 'ember'):
            self.assertContains(response, 'data-theme-choice="%s"' % theme)

        # انتخاب‌گر کشویی و کیبورد سادهٔ تغییر تم
        self.assertContains(response, 'theme-picker-btn')
        self.assertContains(response, 'id="themeToggle"')

    def test_new_theme_css_variables_exist(self):
        response = self.client.get('/login/')
        html = response.content.decode('utf-8')
        self.assertIn('[data-theme="sand"]', html)
        self.assertIn('[data-theme="ember"]', html)
        self.assertIn('--primary: #C76A2E', html)
        self.assertIn('--primary: #A3121B', html)
