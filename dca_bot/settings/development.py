from .base import *

DEBUG = True

ALLOWED_HOSTS = ['*']
CSRF_TRUSTED_ORIGINS = ['https://*.ngrok-free.app', 'http://127.0.0.1:7060', 'http://localhost:7060']

# Optional: Override database for development if needed, 
# but base.py already handles local sqlite default.

# Email backend for development
# EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
EMAIL_BACKEND = 'core.backends.DatabaseEmailBackend'
