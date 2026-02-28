from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect
from django.views.generic import RedirectView
from core import auth_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', RedirectView.as_view(url='/core/dashboard/', permanent=False), name='home'),
    path('core/', include('core.urls')),
    path('accounts/login/', auth_views.CustomLoginView.as_view(), name='login'),
    path('accounts/', include('django.contrib.auth.urls')), # Login/Logout
]
