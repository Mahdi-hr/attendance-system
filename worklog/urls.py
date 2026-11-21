from django.urls import path

from . import views

app_name = 'worklog'

urlpatterns = [
    path('', views.my_worklog, name='my'),
    path('submit/', views.submit_month, name='submit'),
    path('reopen/', views.reopen_month, name='reopen'),
    path('export/', views.my_export, name='my-export'),

    path('projects/', views.project_list, name='projects'),
    path('projects/add/', views.project_create, name='project-add'),
    path('projects/<int:pk>/edit/', views.project_edit, name='project-edit'),
    path('projects/<int:pk>/toggle/', views.project_toggle, name='project-toggle'),

    path('admin/', views.admin_list, name='admin-list'),
    path('admin/projects-export/', views.admin_projects_export,
         name='admin-projects-export'),
    path('admin/<int:user_id>/', views.admin_detail, name='admin-detail'),
    path('admin/<int:user_id>/export/', views.admin_export, name='admin-export'),
]
