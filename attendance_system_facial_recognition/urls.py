"""attendance_system_facial_recognition URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from django.contrib.auth import views as auth_views
from recognition import views as recog_views
from users import views as users_views
from users import forms as users_forms
from errands import views as errands_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', recog_views.home, name='home'),
    path('dashboard/', recog_views.dashboard, name='dashboard'),
    path('train/', recog_views.train, name='train'),
    path('add_photos/', recog_views.add_photos, name='add-photos'),
    path('login/', auth_views.LoginView.as_view(
        template_name='users/login.html',
        authentication_form=users_forms.SmartLoginForm
    ), name='login'),
    path('logout/', users_views.custom_logout, name='logout'),
    path('register/', users_views.register, name='register'),
    path('register/photo/<int:user_id>/', recog_views.register_photo_capture, name='register-photo-capture'),
    path('users/', users_views.users_list, name='users-list'),
    path('users/<int:user_id>/edit/', users_views.user_edit, name='user-edit'),
    path('users/<int:user_id>/delete/', users_views.user_delete, name='user-delete'),
    path('profile/edit/', users_views.edit_profile, name='edit-profile'),
    path('profile/change_password/', users_views.change_password, name='change-password'),
    path('mark_your_attendance', recog_views.mark_your_attendance, name='mark-your-attendance'),
    path('mark_your_attendance_out', recog_views.mark_your_attendance_out, name='mark-your-attendance-out'),
    path('mark_break_out/', recog_views.mark_break_out, name='mark-break-out'),
    path('mark_break_in/', recog_views.mark_break_in, name='mark-break-in'),
    path('break_status', recog_views.break_status, name='break-status'),
    path('video_feed/<str:mode>', recog_views.video_feed, name='video-feed'),
    path('attendance_status', recog_views.attendance_status, name='attendance-status'),
    path('attendance_cancel', recog_views.attendance_cancel, name='attendance-cancel'),
    path('submit_exit_reason', recog_views.submit_exit_reason, name='submit-exit-reason'),
    # ---- صف انتظار دوربین ----
    # وقتی دوربین مشغول است، کاربر به اینجا می‌رود و در صف می‌ماند تا نوبتش
    # شود؛ لازم است قبل از video_feed تعریف شود.
    path('camera/queue/', recog_views.camera_queue, name='camera-queue'),
    path('camera/queue/status/', recog_views.camera_queue_status, name='camera-queue-status'),
    path('camera/queue/leave/', recog_views.camera_queue_leave, name='camera-queue-leave'),
    path('manage/exit-reasons/', recog_views.view_exit_reasons, name='view-exit-reasons'),
    path('manage/exit-reasons/add/', recog_views.add_exit_reason, name='add-exit-reason'),
    path('manage/exit-reasons/toggle/<int:reason_id>/', recog_views.toggle_exit_reason, name='toggle-exit-reason'),
    path('manage/exit-reasons/delete/<int:reason_id>/', recog_views.delete_exit_reason, name='delete-exit-reason'),
    path('manage/exit-reasons/reorder/', recog_views.reorder_exit_reasons, name='reorder-exit-reasons'),
    path('manage/exit-reasons/update-order/<int:reason_id>/', recog_views.update_exit_reason_order, name='update-exit-reason-order'),
    path('checkout-errors/', recog_views.view_checkout_errors, name='view-checkout-errors'),
    path('checkout-errors/<int:error_id>/resolve/', recog_views.resolve_checkout_error, name='resolve-checkout-error'),
    path('checkout-errors/<int:error_id>/reopen/', recog_views.reopen_checkout_error, name='reopen-checkout-error'),
    # ---- مأموریت (کارمند) ----
    path('errands/', errands_views.my_errands, name='errands-my'),
    path('errands/<int:pk>/cancel/', errands_views.cancel_errand, name='errands-cancel'),
    # ---- مأموریت (ادمین) ----
    path('manage/errands/', errands_views.admin_review, name='errands-admin'),
    path('manage/errands/review/<int:pk>/', errands_views.review_errand, name='errands-review'),
    path('manage/errands/bulk/', errands_views.bulk_review, name='errands-bulk'),
    path('manage/errands/<int:pk>/edit/', errands_views.admin_edit, name='errands-edit'),
    path('manage/errands/report/', errands_views.errand_list, name='errands-list'),
    path('manage/errands/export/', errands_views.errand_export, name='errands-export'),
    path('manage/snapshots/', users_views.snapshot_review, name='snapshot-review'),
    path('manage/snapshots/image/<int:snapshot_id>/', users_views.snapshot_image, name='snapshot-image'),
    path('manage/snapshots/delete/<int:snapshot_id>/', users_views.snapshot_delete, name='snapshot-delete'),
    path('dataset_status', recog_views.dataset_status, name='dataset-status'),
    path('dataset/start/', recog_views.dataset_camera_start, name='dataset-camera-start'),
    path('dataset/confirm/', recog_views.dataset_camera_confirm, name='dataset-camera-confirm'),
    path('dataset/cancel/', recog_views.dataset_camera_cancel, name='dataset-camera-cancel'),
    path('view_attendance_home', recog_views.view_attendance_home, name='view-attendance-home'),
    path('view_attendance_date', recog_views.view_attendance_date, name='view-attendance-date'),
    path('view_attendance_date/export', recog_views.view_attendance_date_export, name='view-attendance-date-export'),
    path('view_attendance_date/print', recog_views.view_attendance_date_print, name='view-attendance-date-print'),
    path('view_attendance_employee', recog_views.view_attendance_employee, name='view-attendance-employee'),
    path('view_attendance_employee/export', recog_views.view_attendance_employee_export, name='view-attendance-employee-export'),
    path('view_attendance_employee/print', recog_views.view_attendance_employee_print, name='view-attendance-employee-print'),
    path('view_my_attendance', recog_views.view_my_attendance_employee_login, name='view-my-attendance-employee-login'),
    path('view_my_attendance/export', recog_views.view_my_attendance_export, name='view-my-attendance-export'),
    path('view_my_attendance/print', recog_views.view_my_attendance_print, name='view-my-attendance-print'),
    path('not_authorised', recog_views.not_authorised, name='not-authorised'),
    path('admin-mode/on/', users_views.admin_mode_on, name='admin-mode-on'),
    path('admin-mode/off/', users_views.admin_mode_off, name='admin-mode-off'),
    path('api/current_time/', recog_views.current_time_api, name='current-time-api'),
]
