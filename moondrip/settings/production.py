from .base import *

DEBUG = env.bool('DEBUG', default=False)

# In production, ALLOWED_HOSTS should come from environment
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost'])

# Security settings
SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=False)
SESSION_COOKIE_SECURE = env.bool('SESSION_COOKIE_SECURE', default=False)
CSRF_COOKIE_SECURE = env.bool('CSRF_COOKIE_SECURE', default=False)

# CSRF Trusted Origins (Required for Django 4.0+)
# Example: https://example.com,https://www.example.com
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])

# Proxy SSL Header (Required if behind a reverse proxy terminating SSL)
# SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Ensure correct database config is enforced (though base.py tries to read env)
DATABASES['default'] = env.db('DATABASE_URL')

# Email Configuration
EMAIL_BACKEND = 'core.backends.DatabaseEmailBackend'
# EMAIL_HOST etc are now handled by the backend reading from DB
# But we might want to keep defaults in env just in case, or remove them
# The Backend implementation overrides them anyway.
EMAIL_HOST = env('EMAIL_HOST', default='smtp.sendgrid.net')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='webmaster@localhost')
