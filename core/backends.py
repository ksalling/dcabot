from django.core.mail.backends.smtp import EmailBackend
from .models import AppSettings

class DatabaseEmailBackend(EmailBackend):
    """
    Custom EmailBackend that reads configuration from the AppSettings model
    instead of django.conf.settings.
    """
    def __init__(self, fail_silently=False, **kwargs):
        settings = AppSettings.load()
        
        kwargs['host'] = settings.smtp_host
        kwargs['port'] = settings.smtp_port
        kwargs['username'] = settings.smtp_user
        kwargs['password'] = settings.smtp_password
        kwargs['use_tls'] = settings.use_tls
        # use_ssl is usually mutually exclusive with use_tls for standard django backend init, 
        # but EmailBackend accepts both. We'll default to False for SSL if TLS is True.
        kwargs['use_ssl'] = False 
        
        
        super().__init__(fail_silently=fail_silently, **kwargs)

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

class EmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        try:
            user = UserModel.objects.get(email=username)
        except UserModel.DoesNotExist:
            return None
        else:
            if user.check_password(password):
                return user
        return None
