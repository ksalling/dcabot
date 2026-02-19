from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', lambda request: redirect('dashboard'), name='home'),
    path('core/', include('core.urls')),
    path('accounts/', include('django.contrib.auth.urls')), # Login/Logout
]
