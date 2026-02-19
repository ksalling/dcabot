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
