from django.db import models
from django.conf import settings
from cryptography.fernet import Fernet
import base64
import hashlib

def get_fernet():
    # Derive a 32-byte key from SECRET_KEY
    # This ensures we have a valid Fernet key regardless of SECRET_KEY format
    key = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    key_b64 = base64.urlsafe_b64encode(key)
    return Fernet(key_b64)

class EncryptedCharField(models.CharField):
    description = "Encrypted CharField"

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        try:
            f = get_fernet()
            return f.decrypt(value.encode()).decode()
        except Exception:
            # If decryption fails (e.g. key changed or bad data), return raw or empty?
            # Creating a robust system, we might want to log this but returning value allows us to see it's broken.
            return value

    def get_prep_value(self, value):
        if value is None:
            return value
        # If it's already encrypted (unlikely in normal flow but possible), we might double encrypt if not careful?
        # Typically get_prep_value is called before saving.
        # We assume value passed in is the plaintext.
        f = get_fernet()
        return f.encrypt(value.encode()).decode()
