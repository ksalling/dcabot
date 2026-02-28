from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from core.models import AppSettings

User = get_user_model()

class AuthFeatureTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='test_auth_user', email='auth@test.com', password='password123')
        self.settings = AppSettings.load()

    def test_2fa_setup_redirects_unauthenticated(self):
        response = self.client.get(reverse('2fa_setup'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('2fa_setup')}")

    def test_2fa_setup_access_authenticated(self):
        self.client.login(username='test_auth_user', password='password123')
        response = self.client.get(reverse('2fa_setup'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('qr_code_data_uri', response.context)

    def test_api_key_protection_requires_2fa(self):
        # User without 2FA tries to add exchange account
        self.client.login(username='test_auth_user', password='password123')
        self.assertFalse(self.user.userprofile.totp_enabled)
        
        response = self.client.get(reverse('account_create'))
        # Should redirect to 2fa_setup
        self.assertRedirects(response, reverse('2fa_setup'))

    def test_api_key_protection_allows_with_2fa(self):
        # User with 2FA tries to add exchange account
        self.user.userprofile.totp_enabled = True
        self.user.userprofile.save()
        self.client.login(username='test_auth_user', password='password123')
        
        response = self.client.get(reverse('account_create'))
        self.assertEqual(response.status_code, 200)

    def test_global_2fa_enforcement_on_login(self):
        # Enable global 2FA requirement
        self.settings.require_2fa_globally = True
        self.settings.save()
        
        self.assertFalse(self.user.userprofile.totp_enabled)
        
        # Login should redirect to 2FA setup
        response = self.client.post(reverse('login'), {
            'username': 'test_auth_user',
            'password': 'password123'
        })
        self.assertRedirects(response, reverse('2fa_setup'))

    def test_google_oauth_disabled(self):
        self.settings.allow_google_oauth = False
        self.settings.save()
        
        response = self.client.get(reverse('google_login'))
        self.assertRedirects(response, reverse('login'))
        
        response = self.client.get(reverse('google_callback'))
        self.assertRedirects(response, reverse('login'))
