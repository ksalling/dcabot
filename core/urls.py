from django.urls import path
from . import views

urlpatterns = [
    path('dashboard/', views.DashboardView.as_view(), name='dashboard'),
    path('jobs/add/', views.JobCreateView.as_view(), name='job_create'),
    path('jobs/<int:pk>/edit/', views.JobUpdateView.as_view(), name='job_edit'),
    path('jobs/<int:pk>/delete/', views.JobDeleteView.as_view(), name='job_delete'),
    path('jobs/<int:pk>/toggle/', views.JobToggleView.as_view(), name='job_toggle'),
    path('jobs/<int:pk>/run/', views.JobRunNowView.as_view(), name='job_run'),
    path('accounts/add/', views.AccountCreateView.as_view(), name='account_create'),
    path('accounts/<int:pk>/toggle/', views.AccountToggleView.as_view(), name='account_toggle'),
    path('accounts/<int:pk>/edit/', views.AccountUpdateView.as_view(), name='account_edit'),
    path('accounts/<int:pk>/delete/', views.AccountDeleteView.as_view(), name='account_delete'),
    path('profile/', views.ProfileView.as_view(), name='profile'),
    path('profile/password/', views.CustomPasswordChangeView.as_view(), name='password_change'),
    path('admin/exchanges/', views.ManageExchangesView.as_view(), name='manage_exchanges'),
]
