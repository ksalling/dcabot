from .base import *

DEBUG = True

ALLOWED_HOSTS = ['*']

# Optional: Override database for development if needed, 
# but base.py already handles local sqlite default.

# Email backend for development
# EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
EMAIL_BACKEND = 'core.backends.DatabaseEmailBackend'
