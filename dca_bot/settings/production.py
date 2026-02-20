from .base import *

DEBUG = env.bool('DEBUG', default=False)

# In production, ALLOWED_HOSTS should come from environment
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost'])

# Security settings
SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=False)
SESSION_COOKIE_SECURE = env.bool('SESSION_COOKIE_SECURE', default=False)
CSRF_COOKIE_SECURE = env.bool('CSRF_COOKIE_SECURE', default=False)

# Ensure correct database config is enforced (though base.py tries to read env)
DATABASES['default'] = env.db('DATABASE_URL')
