from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', RedirectView.as_view(url='/core/dashboard/', permanent=False), name='home'),
    path('core/', include('core.urls')),
    path('accounts/', include('django.contrib.auth.urls')), # Login/Logout
]
