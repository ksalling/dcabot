from django.test import TestCase
from django.contrib.auth import get_user_model
from .models import ExchangeAccount, SupportedExchange
from .services.exchange_service import ExchangeService
from unittest.mock import MagicMock, patch

User = get_user_model()

class EncryptionTest(TestCase):
    def test_encryption(self):
        user = User.objects.create(username='testuser')
        exchange = SupportedExchange.objects.create(name='Binance', slug='binance')
        account = ExchangeAccount.objects.create(
            user=user,
            exchange=exchange,
            nickname='Test',
            api_key='public_key',
            api_secret='super_secret_secret'
        )
        
        # Reload from DB to verify decryption on access
        # Note: In Django tests, DB is usually rolled back, but saved data persists during test
        account.refresh_from_db()
        self.assertEqual(account.api_secret, 'super_secret_secret')
        
class ExchangeServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create(username='testuser')
        self.exchange = SupportedExchange.objects.create(name='Binance', slug='binance')
        self.account = ExchangeAccount.objects.create(
            user=self.user,
            exchange=self.exchange,
            nickname='Test Bin',
            api_key='key',
            api_secret='secret'
        )

    @patch('ccxt.binance')
    def test_init(self, mock_ccxt_class):
        # Setup mock return
        mock_instance = MagicMock()
        mock_ccxt_class.return_value = mock_instance
        
        service = ExchangeService(self.account)
        self.assertIsNotNone(service.exchange)
        
        # Check that ccxt.binance was initialized with correct config
        mock_ccxt_class.assert_called_once()
        args, kwargs = mock_ccxt_class.call_args
        config = args[0]
        self.assertEqual(config['apiKey'], 'key')
        self.assertEqual(config['secret'], 'secret')

from .models import UserProfile
from .forms import ExchangeAccountForm

class SubscriptionTierTest(TestCase):
    def setUp(self):
        self.user = User.objects.create(username='sub_user')
        self.exchange_toobit = SupportedExchange.objects.create(name='Toobit', slug='toobit', is_enabled=True)
        self.exchange_binance = SupportedExchange.objects.create(name='Binance', slug='binance', is_enabled=True)

    def test_has_access_none(self):
        self.assertFalse(self.user.userprofile.has_access)

    def test_has_access_paid(self):
        self.user.userprofile.subscription_status = 'active'
        self.user.userprofile.subscription_tier = 'paid'
        self.assertTrue(self.user.userprofile.has_access)

    def test_has_access_affiliate(self):
        self.user.userprofile.subscription_tier = 'affiliate'
        self.assertTrue(self.user.userprofile.has_access)

class ExchangeAccountFormTest(TestCase):
    def setUp(self):
        self.user = User.objects.create(username='form_user')
        self.exchange_toobit = SupportedExchange.objects.create(name='Toobit', slug='toobit', is_enabled=True)
        self.exchange_binance = SupportedExchange.objects.create(name='Binance', slug='binance', is_enabled=True)

    def test_form_queryset_regular_paid(self):
        # Paid user gets all enabled
        self.user.userprofile.subscription_status = 'active'
        self.user.userprofile.subscription_tier = 'paid'
        self.user.userprofile.save()
        form = ExchangeAccountForm(user=self.user)
        self.assertEqual(list(form.fields['exchange'].queryset), [self.exchange_toobit, self.exchange_binance])

    def test_form_queryset_affiliate(self):
        # Affiliate user gets only referral exchange
        self.user.userprofile.subscription_tier = 'affiliate'
        self.user.userprofile.referral_exchange = self.exchange_toobit
        self.user.userprofile.save()
        form = ExchangeAccountForm(user=self.user)
        self.assertEqual(list(form.fields['exchange'].queryset), [self.exchange_toobit])

from django.urls import reverse
from django.utils import timezone
from .models import AutobuyJob

class JobToggleTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='job_user', password='password123')
        self.user.userprofile.subscription_status = 'active'
        self.user.userprofile.subscription_tier = 'paid'
        self.user.userprofile.save()
        
        self.exchange = SupportedExchange.objects.create(name='Binance', slug='binance', is_enabled=False)
        self.account = ExchangeAccount.objects.create(
            user=self.user,
            exchange=self.exchange,
            nickname='Test Bin',
            api_key='key',
            api_secret='secret',
            is_active=True
        )
        self.job = AutobuyJob.objects.create(
            user=self.user,
            account=self.account,
            name="Test Job",
            quote_currency="USDT",
            total_amount=10,
            interval='hourly',
            start_time=timezone.now(),
            next_run=timezone.now(),
            is_active=False,
        )
        self.client.login(username='job_user', password='password123')

    @patch('core.services.exchange_service.ExchangeService.validate_job_funds')
    def test_job_toggle_restrictions(self, mock_validate):
        mock_validate.return_value = (True, "")
        
        # 1. Exchange disabled
        response = self.client.post(reverse('job_toggle', kwargs={'pk': self.job.pk}))
        self.job.refresh_from_db()
        self.assertFalse(self.job.is_active)
        
        # 2. Account disabled
        self.exchange.is_enabled = True
        self.exchange.save()
        self.account.is_active = False
        self.account.save()
        
        response = self.client.post(reverse('job_toggle', kwargs={'pk': self.job.pk}))
        self.job.refresh_from_db()
        self.assertFalse(self.job.is_active)

        # 3. Both enabled -> Should activate
        self.account.is_active = True
        self.account.save()
        
        response = self.client.post(reverse('job_toggle', kwargs={'pk': self.job.pk}))
        self.job.refresh_from_db()
        self.assertTrue(self.job.is_active)
