from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core.cache import cache
from django.utils import timezone
from .models import ExchangeAccount, SupportedExchange, AutobuyJob, JobToken, Trade
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
        account.refresh_from_db()
        self.assertEqual(account.api_secret, 'super_secret_secret')


class ExchangeServiceTest(TestCase):
    def setUp(self):
        cache.clear()
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
        mock_instance = MagicMock()
        mock_ccxt_class.return_value = mock_instance
        
        service = ExchangeService(self.account)
        self.assertIsNotNone(service.exchange)
        
        mock_ccxt_class.assert_called_once()
        args, kwargs = mock_ccxt_class.call_args
        config = args[0]
        self.assertEqual(config['apiKey'], 'key')
        self.assertEqual(config['secret'], 'secret')

    @patch('ccxt.binance')
    def test_validate_pair_and_get_available_pairs(self, mock_ccxt_class):
        mock_instance = MagicMock()
        mock_ccxt_class.return_value = mock_instance
        mock_instance.load_markets.return_value = {
            'BTC/USDT': {'symbol': 'BTC/USDT', 'base': 'BTC', 'quote': 'USDT', 'spot': True, 'active': True},
            'ETH/USDT': {'symbol': 'ETH/USDT', 'base': 'ETH', 'quote': 'USDT', 'spot': True, 'active': True},
            'SOL/USD': {'symbol': 'SOL/USD', 'base': 'SOL', 'quote': 'USD', 'spot': True, 'active': True},
            'INACTIVE/USDT': {'symbol': 'INACTIVE/USDT', 'base': 'INACTIVE', 'quote': 'USDT', 'spot': True, 'active': False},
            'BTC/USDT:USDT': {'symbol': 'BTC/USDT:USDT', 'base': 'BTC', 'quote': 'USDT', 'spot': False, 'contract': True, 'active': True},
        }

        service = ExchangeService(self.account)

        # 1. Test get_available_pairs filtered by quote
        usdt_pairs = service.get_available_pairs(quote_currency='USDT')
        symbols = [p['symbol'] for p in usdt_pairs]
        self.assertIn('BTC/USDT', symbols)
        self.assertIn('ETH/USDT', symbols)
        self.assertNotIn('SOL/USD', symbols)
        self.assertNotIn('BTC/USDT:USDT', symbols) # Contract ignored
        self.assertNotIn('INACTIVE/USDT', symbols) # Inactive filtered out

        # 2. Test validate_pair with base symbol 'BTC'
        is_valid, std_symbol, err = service.validate_pair('BTC', 'USDT')
        self.assertTrue(is_valid)
        self.assertEqual(std_symbol, 'BTC/USDT')

        # 3. Test validate_pair with full symbol 'BTC/USDT'
        is_valid, std_symbol, err = service.validate_pair('BTC/USDT', 'USDT')
        self.assertTrue(is_valid)
        self.assertEqual(std_symbol, 'BTC/USDT')

        # 4. Test validate_pair quote mismatch
        is_valid, std_symbol, err = service.validate_pair('BTC/USD', 'USDT')
        self.assertFalse(is_valid)
        self.assertIn('does not match', err)

        # 5. Test validate_pair non-existent pair
        is_valid, std_symbol, err = service.validate_pair('DOGE', 'USDT')
        self.assertFalse(is_valid)
        self.assertIn('not found', err)

        # 6. Test validate_pair inactive pair
        is_valid, std_symbol, err = service.validate_pair('INACTIVE', 'USDT')
        self.assertFalse(is_valid)
        self.assertIn('inactive/disabled', err)


class AccountPairsViewTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='alice', password='password123')
        self.other_user = User.objects.create_user(username='bob', password='password123')
        self.exchange = SupportedExchange.objects.create(name='Binance', slug='binance')
        self.account = ExchangeAccount.objects.create(
            user=self.user,
            exchange=self.exchange,
            nickname='Alice Binance',
            api_key='key',
            api_secret='secret'
        )

    @patch('ccxt.binance')
    def test_account_pairs_api(self, mock_ccxt_class):
        mock_instance = MagicMock()
        mock_ccxt_class.return_value = mock_instance
        mock_instance.load_markets.return_value = {
            'BTC/USDT': {'symbol': 'BTC/USDT', 'base': 'BTC', 'quote': 'USDT', 'spot': True, 'active': True},
            'ETH/USDT': {'symbol': 'ETH/USDT', 'base': 'ETH', 'quote': 'USDT', 'spot': True, 'active': True},
            'SOL/USD': {'symbol': 'SOL/USD', 'base': 'SOL', 'quote': 'USD', 'spot': True, 'active': True},
        }

        self.client.login(username='alice', password='password123')
        url = reverse('account_pairs', kwargs={'pk': self.account.pk})
        response = self.client.get(f"{url}?quote=USDT")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(len(data['pairs']), 2)
        symbols = [p['symbol'] for p in data['pairs']]
        self.assertIn('BTC/USDT', symbols)
        self.assertIn('ETH/USDT', symbols)

    def test_account_pairs_unauthorized_user(self):
        self.client.login(username='bob', password='password123')
        url = reverse('account_pairs', kwargs={'pk': self.account.pk})
        response = self.client.get(f"{url}?quote=USDT")
        self.assertEqual(response.status_code, 404)


class TradeExecutorSafetyTest(TestCase):
    def setUp(self):
        cache.clear()
        from django.utils import timezone
        self.user = User.objects.create_user(username='trader', password='password123')
        if hasattr(self.user, 'userprofile'):
            self.user.userprofile.manual_access_granted = True
            self.user.userprofile.save()
        self.exchange = SupportedExchange.objects.create(name='Binance', slug='binance')
        self.account = ExchangeAccount.objects.create(
            user=self.user,
            exchange=self.exchange,
            nickname='Trading Account',
            api_key='key',
            api_secret='secret'
        )
        self.job = AutobuyJob.objects.create(
            user=self.user,
            account=self.account,
            name='Test DCA',
            total_amount=100,
            quote_currency='USDT',
            interval='daily',
            is_active=True,
            start_time=timezone.now()
        )
        self.token = JobToken.objects.create(
            job=self.job,
            token_symbol='INVALID_COIN',
            percentage=100
        )

    @patch('ccxt.binance')
    def test_trade_executor_handles_invalid_pair_safely(self, mock_ccxt_class):
        from .services.trade_executor import TradeExecutor
        mock_instance = MagicMock()
        mock_ccxt_class.return_value = mock_instance
        mock_instance.load_markets.return_value = {
            'BTC/USDT': {'symbol': 'BTC/USDT', 'base': 'BTC', 'quote': 'USDT', 'spot': True, 'active': True},
        }

        executor = TradeExecutor()
        executor.execute_job(self.job.pk)

        self.job.refresh_from_db()
        self.assertFalse(self.job.is_active)
        self.assertEqual(self.job.last_status, 'failure')
        self.assertIn('Pair validation failed', self.job.last_error_message)

    @patch('ccxt.binance')
    def test_trade_executor_executes_valid_trade_successfully(self, mock_ccxt_class):
        from .services.trade_executor import TradeExecutor
        from .models import Trade
        mock_instance = MagicMock()
        mock_ccxt_class.return_value = mock_instance
        mock_instance.load_markets.return_value = {
            'BTC/USDT': {'symbol': 'BTC/USDT', 'base': 'BTC', 'quote': 'USDT', 'spot': True, 'active': True},
        }
        mock_instance.fetch_ticker.return_value = {'last': 50000.0}
        mock_instance.market.return_value = {'precision': {'amount': 6}}
        mock_instance.amount_to_precision.return_value = 0.002
        mock_instance.create_market_buy_order.return_value = {
            'id': 'ord_123',
            'amount': 0.002,
            'price': 50000.0,
            'cost': 100.0,
            'status': 'closed',
            'fee': {'cost': 0.1}
        }

        self.token.token_symbol = 'BTC'
        self.token.save()

        executor = TradeExecutor()
        executor.execute_job(self.job.pk)

        self.job.refresh_from_db()
        self.assertEqual(self.job.last_status, 'success')
        self.assertEqual(self.job.last_error_message, '')
        self.assertEqual(Trade.objects.filter(job=self.job).count(), 1)
        trade = Trade.objects.get(job=self.job)
        self.assertEqual(trade.symbol, 'BTC/USDT')
        self.assertEqual(trade.order_id, 'ord_123')


class JobViewValidationTest(TestCase):
    def setUp(self):
        cache.clear()
        from django.utils import timezone
        self.user = User.objects.create_user(username='jobtester', password='password123')
        if hasattr(self.user, 'userprofile'):
            self.user.userprofile.manual_access_granted = True
            self.user.userprofile.save()
        self.exchange = SupportedExchange.objects.create(name='Binance', slug='binance')
        self.account = ExchangeAccount.objects.create(
            user=self.user,
            exchange=self.exchange,
            nickname='Job Account',
            api_key='key',
            api_secret='secret'
        )
        self.client.login(username='jobtester', password='password123')

    @patch('ccxt.binance')
    def test_job_create_rejects_invalid_token(self, mock_ccxt_class):
        mock_instance = MagicMock()
        mock_ccxt_class.return_value = mock_instance
        mock_instance.load_markets.return_value = {
            'BTC/USDT': {'symbol': 'BTC/USDT', 'base': 'BTC', 'quote': 'USDT', 'spot': True, 'active': True},
        }
        mock_instance.fetch_balance.return_value = {'USDT': {'free': 1000}}

        url = reverse('job_create')
        post_data = {
            'name': 'DCA Bitcoin',
            'account': self.account.pk,
            'total_amount': '50',
            'quote_currency': 'USDT',
            'interval': 'daily',
            'start_time': '2026-09-10T12:00',
            'tokens-TOTAL_FORMS': '1',
            'tokens-INITIAL_FORMS': '0',
            'tokens-MIN_NUM_FORMS': '0',
            'tokens-MAX_NUM_FORMS': '1000',
            'tokens-0-token_symbol': 'UNKNOWN_COIN',
            'tokens-0-percentage': '100',
        }
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 200) # Form re-rendered with error
        self.assertEqual(AutobuyJob.objects.filter(name='DCA Bitcoin').count(), 0)

    @patch('ccxt.binance')
    def test_job_create_accepts_valid_token_and_standardizes_symbol(self, mock_ccxt_class):
        mock_instance = MagicMock()
        mock_ccxt_class.return_value = mock_instance
        mock_instance.load_markets.return_value = {
            'BTC/USDT': {'symbol': 'BTC/USDT', 'base': 'BTC', 'quote': 'USDT', 'spot': True, 'active': True},
        }
        mock_instance.fetch_balance.return_value = {'USDT': {'free': 1000}}

        url = reverse('job_create')
        post_data = {
            'name': 'DCA Bitcoin Valid',
            'account': self.account.pk,
            'total_amount': '50',
            'quote_currency': 'USDT',
            'interval': 'daily',
            'start_time': '2026-09-10T12:00',
            'tokens-TOTAL_FORMS': '1',
            'tokens-INITIAL_FORMS': '0',
            'tokens-MIN_NUM_FORMS': '0',
            'tokens-MAX_NUM_FORMS': '1000',
            'tokens-0-token_symbol': 'BTC',
            'tokens-0-percentage': '100',
        }
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302) # Redirects to dashboard
        job = AutobuyJob.objects.get(name='DCA Bitcoin Valid')
        self.assertEqual(job.tokens.count(), 1)
        token = job.tokens.first()
        self.assertEqual(token.token_symbol, 'BTC/USDT')

    @patch('ccxt.binance')
    def test_job_create_rejects_undersized_order(self, mock_ccxt_class):
        mock_instance = MagicMock()
        mock_ccxt_class.return_value = mock_instance
        mock_instance.load_markets.return_value = {
            'BTC/USDT': {
                'symbol': 'BTC/USDT', 'base': 'BTC', 'quote': 'USDT', 'spot': True, 'active': True,
                'limits': {'cost': {'min': 10.0}, 'amount': {'min': 0.0001}}
            },
        }
        mock_instance.market.return_value = {
            'symbol': 'BTC/USDT', 'base': 'BTC', 'quote': 'USDT',
            'limits': {'cost': {'min': 10.0}, 'amount': {'min': 0.0001}}
        }
        mock_instance.fetch_balance.return_value = {'USDT': {'free': 1000}}

        url = reverse('job_create')
        # Total amount $5 is below $10 min cost limit
        post_data = {
            'name': 'DCA Too Small',
            'account': self.account.pk,
            'total_amount': '5',
            'quote_currency': 'USDT',
            'interval': 'daily',
            'start_time': '2026-09-10T12:00',
            'tokens-TOTAL_FORMS': '1',
            'tokens-INITIAL_FORMS': '0',
            'tokens-MIN_NUM_FORMS': '0',
            'tokens-MAX_NUM_FORMS': '1000',
            'tokens-0-token_symbol': 'BTC',
            'tokens-0-percentage': '100',
        }
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 200) # Form re-rendered with error
        self.assertEqual(AutobuyJob.objects.filter(name='DCA Too Small').count(), 0)

    @patch('ccxt.binance')
    def test_job_update_rejects_undersized_order(self, mock_ccxt_class):
        from django.utils import timezone
        mock_instance = MagicMock()
        mock_ccxt_class.return_value = mock_instance
        mock_instance.load_markets.return_value = {
            'BTC/USDT': {
                'symbol': 'BTC/USDT', 'base': 'BTC', 'quote': 'USDT', 'spot': True, 'active': True,
                'limits': {'cost': {'min': 10.0}, 'amount': {'min': 0.0001}}
            },
        }
        mock_instance.market.return_value = {
            'symbol': 'BTC/USDT', 'base': 'BTC', 'quote': 'USDT',
            'limits': {'cost': {'min': 10.0}, 'amount': {'min': 0.0001}}
        }
        mock_instance.fetch_balance.return_value = {'USDT': {'free': 1000}}

        existing_job = AutobuyJob.objects.create(
            user=self.user,
            account=self.account,
            name='Existing Job',
            total_amount=50,
            quote_currency='USDT',
            interval='daily',
            is_active=True,
            start_time=timezone.now()
        )
        existing_token = JobToken.objects.create(
            job=existing_job,
            token_symbol='BTC/USDT',
            percentage=100
        )

        url = reverse('job_edit', kwargs={'pk': existing_job.pk})
        # Try updating total amount to $3 (below min cost of 10)
        post_data = {
            'name': 'Existing Job',
            'account': self.account.pk,
            'total_amount': '3',
            'quote_currency': 'USDT',
            'interval': 'daily',
            'start_time': '2026-09-10T12:00',
            'tokens-TOTAL_FORMS': '1',
            'tokens-INITIAL_FORMS': '1',
            'tokens-MIN_NUM_FORMS': '0',
            'tokens-MAX_NUM_FORMS': '1000',
            'tokens-0-id': existing_token.pk,
            'tokens-0-job': existing_job.pk,
            'tokens-0-token_symbol': 'BTC',
            'tokens-0-percentage': '100',
        }
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 200) # Form re-rendered with error
        existing_job.refresh_from_db()
        self.assertEqual(existing_job.total_amount, 50) # Amount not changed to 3

    @patch('ccxt.binance')
    def test_job_create_displays_multiple_errors_simultaneously(self, mock_ccxt_class):
        mock_instance = MagicMock()
        mock_ccxt_class.return_value = mock_instance
        mock_instance.load_markets.return_value = {
            'BTC/USDT': {
                'symbol': 'BTC/USDT', 'base': 'BTC', 'quote': 'USDT', 'spot': True, 'active': True,
                'limits': {'cost': {'min': 50.0}} # Requires at least $50
            },
            'ETH/USDT': {
                'symbol': 'ETH/USDT', 'base': 'ETH', 'quote': 'USDT', 'spot': True, 'active': True,
                'limits': {'cost': {'min': 10.0}}
            }
        }
        mock_instance.fetch_balance.return_value = {'USDT': {'free': 1000}}

        url = reverse('job_create')
        # Token 0: BTC with 50% ($10 total -> $5 allocation, below $50 min)
        # Token 1: UNKNOWN_COIN with 50% (invalid pair)
        post_data = {
            'name': 'DCA Multi Error',
            'account': self.account.pk,
            'total_amount': '10',
            'quote_currency': 'USDT',
            'interval': 'daily',
            'start_time': '2026-09-10T12:00',
            'tokens-TOTAL_FORMS': '2',
            'tokens-INITIAL_FORMS': '0',
            'tokens-MIN_NUM_FORMS': '0',
            'tokens-MAX_NUM_FORMS': '1000',
            'tokens-0-token_symbol': 'BTC',
            'tokens-0-percentage': '50',
            'tokens-1-token_symbol': 'UNKNOWN_COIN',
            'tokens-1-percentage': '50',
        }
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 200)

        # Verify all errors are in messages
        messages_list = list(response.context['messages'])
        error_texts = [str(m) for m in messages_list]
        self.assertTrue(any('minimum trade cost' in t for t in error_texts))
        self.assertTrue(any('UNKNOWN_COIN' in t for t in error_texts))
        self.assertEqual(AutobuyJob.objects.filter(name='DCA Multi Error').count(), 0)


class OrderSizeValidationServiceTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create(username='testuser')
        self.exchange = SupportedExchange.objects.create(name='Kraken', slug='kraken')
        self.account = ExchangeAccount.objects.create(
            user=self.user,
            exchange=self.exchange,
            nickname='Test Kraken',
            api_key='key',
            api_secret='secret'
        )

    @patch('ccxt.kraken')
    def test_validate_order_size_cost_limit(self, mock_ccxt_class):
        mock_instance = MagicMock()
        mock_ccxt_class.return_value = mock_instance
        mock_instance.load_markets.return_value = {
            'BTC/USD': {
                'symbol': 'BTC/USD', 'base': 'BTC', 'quote': 'USD', 'spot': True, 'active': True,
                'limits': {'cost': {'min': 10.0}}
            }
        }
        mock_instance.market.return_value = {
            'symbol': 'BTC/USD', 'base': 'BTC', 'quote': 'USD',
            'limits': {'cost': {'min': 10.0}}
        }

        service = ExchangeService(self.account)
        # Below $10 min cost
        is_valid, msg = service.validate_order_size('BTC/USD', 5.0, 'USD')
        self.assertFalse(is_valid)
        self.assertIn('minimum trade cost', msg)

        # Equal or above $10 min cost
        is_valid, msg = service.validate_order_size('BTC/USD', 15.0, 'USD')
        self.assertTrue(is_valid)
        self.assertEqual(msg, "")

    @patch('ccxt.kraken')
    def test_validate_order_size_amount_limit(self, mock_ccxt_class):
        mock_instance = MagicMock()
        mock_ccxt_class.return_value = mock_instance
        mock_instance.load_markets.return_value = {
            'SOL/USD': {
                'symbol': 'SOL/USD', 'base': 'SOL', 'quote': 'USD', 'spot': True, 'active': True,
                'limits': {'amount': {'min': 0.06}} # e.g. Kraken min 0.06 SOL
            }
        }
        mock_instance.market.return_value = {
            'symbol': 'SOL/USD', 'base': 'SOL', 'quote': 'USD',
            'limits': {'amount': {'min': 0.06}}
        }
        mock_instance.fetch_ticker.return_value = {'last': 100.0} # $100 per SOL

        service = ExchangeService(self.account)
        # 0.06 SOL @ $100 = $6.00 min. $3 is ~0.03 SOL (< 0.06 min)
        is_valid, msg = service.validate_order_size('SOL/USD', 3.0, 'USD')
        self.assertFalse(is_valid)
        self.assertIn('minimum trade size of 0.06 SOL', msg)

        # $10 is 0.1 SOL (> 0.06 min)
        is_valid, msg = service.validate_order_size('SOL/USD', 10.0, 'USD')
        self.assertTrue(is_valid)
        self.assertEqual(msg, "")


class JobScheduleNextRunTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create(username='testuser')
        self.exchange = SupportedExchange.objects.create(name='Binance', slug='binance')
        self.account = ExchangeAccount.objects.create(
            user=self.user,
            exchange=self.exchange,
            nickname='Schedule Test',
            api_key='key',
            api_secret='secret'
        )

    def test_calculate_next_run_preserves_time_of_day_when_start_time_in_past(self):
        from datetime import datetime, timezone as dt_timezone
        from django.utils import timezone
        
        # Start time was set at 23:18 UTC 2 hours ago
        past_start = timezone.now() - timezone.timedelta(hours=2)
        job = AutobuyJob(
            user=self.user,
            account=self.account,
            name='Daily Job',
            total_amount=100,
            interval='daily',
            start_time=past_start,
        )

        next_run = job.calculate_next_run()
        self.assertGreater(next_run, timezone.now())
        # Next run should preserve exact minute, second, microsecond
        self.assertEqual(next_run.minute, past_start.minute)
        self.assertEqual(next_run.second, past_start.second)
        self.assertEqual(next_run.hour, past_start.hour)

    def test_calculate_next_run_uses_future_start_time(self):
        from django.utils import timezone
        future_start = timezone.now() + timezone.timedelta(days=2, hours=3)
        job = AutobuyJob(
            user=self.user,
            account=self.account,
            name='Future Job',
            total_amount=100,
            interval='daily',
            start_time=future_start,
        )
        next_run = job.calculate_next_run()
        self.assertEqual(next_run, future_start)


class DashboardLiveUpdateTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='testuser', password='password123')
        self.client.login(username='testuser', password='password123')
        self.exchange = SupportedExchange.objects.create(name='Binance', slug='binance')
        self.account = ExchangeAccount.objects.create(
            user=self.user,
            exchange=self.exchange,
            nickname='Live Test',
            api_key='key',
            api_secret='secret'
        )

    def test_dashboard_full_page_render(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'core/dashboard.html')
        self.assertTemplateUsed(response, 'core/partials/dashboard_live_content.html')
        self.assertContains(response, 'id="dashboard-live-container"')
        self.assertContains(response, 'auto-refresh-select')
        self.assertContains(response, '300000" selected>5m (Default)')
        self.assertContains(response, '30000">30s')
        self.assertContains(response, '60000">1m')
        self.assertContains(response, '600000">10m')
        self.assertContains(response, '1800000">30m')
        self.assertContains(response, '↻ Refresh')

    def test_dashboard_htmx_partial_render(self):
        response = self.client.get(reverse('dashboard'), HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'core/partials/dashboard_live_content.html')
        self.assertTemplateNotUsed(response, 'core/dashboard.html')
        self.assertContains(response, 'Portfolio Value')

    @patch('core.services.trade_executor.TradeExecutor.execute_job')
    def test_job_run_htmx_triggers_live_refresh(self, mock_execute):
        job = AutobuyJob.objects.create(
            user=self.user,
            account=self.account,
            name='Manual Run Test',
            total_amount=50,
            interval='daily',
            start_time=timezone.now()
        )
        url = reverse('job_run', kwargs={'pk': job.pk})
        response = self.client.post(url, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        mock_execute.assert_called_once_with(job.pk)
        self.assertTemplateUsed(response, 'core/partials/dashboard_live_content.html')
        self.assertTemplateNotUsed(response, 'core/dashboard.html')
        self.assertContains(response, 'Manual Run Test')


class TradeListPerformanceAndExportTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='testuser', password='password123')
        self.client.login(username='testuser', password='password123')
        self.exchange = SupportedExchange.objects.create(name='Binance', slug='binance')
        self.account = ExchangeAccount.objects.create(
            user=self.user,
            exchange=self.exchange,
            nickname='Trade Test Account',
            api_key='key',
            api_secret='secret'
        )
        self.job1 = AutobuyJob.objects.create(
            user=self.user,
            account=self.account,
            name='Bitcoin DCA',
            total_amount=50,
            interval='daily',
            start_time=timezone.now()
        )
        self.job2 = AutobuyJob.objects.create(
            user=self.user,
            account=self.account,
            name='Solana DCA',
            total_amount=25,
            interval='daily',
            start_time=timezone.now()
        )
        
        # Create trades
        self.trade1 = Trade.objects.create(
            user=self.user,
            job=self.job1,
            job_name='Bitcoin DCA',
            exchange_name='Binance',
            symbol='BTC/USDT',
            amount_spent=50.0,
            amount_received=0.001,
            purchase_price=50000.0,
            fee_incurred=0.05
        )
        self.trade2 = Trade.objects.create(
            user=self.user,
            job=self.job2,
            job_name='Solana DCA',
            exchange_name='Binance',
            symbol='SOL/USDT',
            amount_spent=25.0,
            amount_received=0.25,
            purchase_price=100.0,
            fee_incurred=0.025
        )

    def test_trade_list_unfiltered_renders_summary_and_charts(self):
        response = self.client.get(reverse('trade_list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_trades'], 2)
        self.assertEqual(float(response.context['total_spent']), 75.0)
        self.assertEqual(float(response.context['total_fees']), 0.075)
        self.assertEqual(float(response.context['avg_trade_size']), 37.5)
        self.assertContains(response, 'Total Trades')
        self.assertContains(response, 'Total Invested')
        self.assertContains(response, 'Cumulative Investment Growth')
        self.assertContains(response, 'Asset Allocation')
        self.assertContains(response, 'Bitcoin DCA')
        self.assertContains(response, 'Solana DCA')

    def test_trade_list_filtered_by_job(self):
        response = self.client.get(reverse('trade_list'), {'job': 'Bitcoin DCA'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_trades'], 1)
        self.assertEqual(float(response.context['total_spent']), 50.0)
        self.assertContains(response, 'BTC/USDT')
        self.assertNotContains(response, 'SOL/USDT')
        self.assertTrue(response.context['is_filtered'])
        self.assertContains(response, 'Export Filtered Dataset')

    def test_export_trades_csv_unfiltered(self):
        response = self.client.get(reverse('trade_export'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        content = response.content.decode('utf-8')
        self.assertIn('Bitcoin DCA', content)
        self.assertIn('Solana DCA', content)

    def test_export_trades_csv_filtered_by_job(self):
        response = self.client.get(reverse('trade_export'), {'job': 'Bitcoin DCA'})
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('Bitcoin DCA', content)
        self.assertNotIn('Solana DCA', content)


class NotificationAndAlertsTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='alertuser',
            email='alertuser@example.com',
            password='password123',
            first_name='Alex'
        )
        self.client.login(username='alertuser', password='password123')
        self.exchange = SupportedExchange.objects.create(name='Binance', slug='binance')
        self.account = ExchangeAccount.objects.create(
            user=self.user,
            exchange=self.exchange,
            nickname='Notification Account',
            api_key='key',
            api_secret='secret'
        )
        self.job = AutobuyJob.objects.create(
            user=self.user,
            account=self.account,
            name='Daily DCA',
            total_amount=50,
            interval='daily',
            start_time=timezone.now(),
            is_active=False # Paused
        )

    def test_profile_form_saves_notification_preferences(self):
        url = reverse('profile')
        post_data = {
            'first_name': 'Alex',
            'last_name': 'User',
            'email': 'alertuser@example.com',
            'notify_trade_success': 'on',
            'notify_trade_failed': '', # unchecked
            'notify_trade_skipped_paused': 'on',
        }
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)
        
        self.user.userprofile.refresh_from_db()
        self.assertTrue(self.user.userprofile.notify_trade_success)
        self.assertFalse(self.user.userprofile.notify_trade_failed)
        self.assertTrue(self.user.userprofile.notify_trade_skipped_paused)

    @patch('core.services.notification_service.send_mail')
    def test_send_trade_success_email(self, mock_send_mail):
        from core.services.notification_service import NotificationService
        trade = Trade.objects.create(
            user=self.user,
            job=self.job,
            job_name=self.job.name,
            exchange_name='Binance',
            symbol='BTC/USDT',
            amount_spent=50.0,
            amount_received=0.001,
            purchase_price=50000.0,
            fee_incurred=0.05
        )
        NotificationService.send_trade_success_email(self.job, [trade])
        mock_send_mail.assert_called_once()
        call_kwargs = mock_send_mail.call_args[1]
        self.assertIn("Daily DCA", call_kwargs['subject'])
        self.assertIn("BTC/USDT", call_kwargs['message'])
        self.assertEqual(call_kwargs['recipient_list'], ['alertuser@example.com'])

    @patch('core.services.notification_service.send_mail')
    def test_send_trade_failed_email(self, mock_send_mail):
        from core.services.notification_service import NotificationService
        NotificationService.send_trade_failed_email(self.job, "Insufficient funds on Kraken", symbol="BTC/USD")
        mock_send_mail.assert_called_once()
        call_kwargs = mock_send_mail.call_args[1]
        self.assertIn("Trade Failed: Daily DCA", call_kwargs['subject'])
        self.assertIn("Insufficient funds", call_kwargs['message'])

    @patch('core.services.notification_service.send_mail')
    def test_send_trade_skipped_paused_email(self, mock_send_mail):
        from core.services.notification_service import NotificationService
        NotificationService.send_trade_skipped_paused_email(self.job)
        mock_send_mail.assert_called_once()
        call_kwargs = mock_send_mail.call_args[1]
        self.assertIn("Trade Skipped: Daily DCA", call_kwargs['subject'])
        self.assertIn("paused", call_kwargs['message'].lower())

    @patch('core.services.notification_service.send_mail')
    def test_scheduler_handles_paused_job_and_sends_email(self, mock_send_mail):
        from core.management.commands.run_scheduler import check_and_run_jobs
        # Set next_run in the past for paused job
        past_time = timezone.now() - timezone.timedelta(minutes=5)
        self.job.next_run = past_time
        self.job.save()

        check_and_run_jobs()
        self.job.refresh_from_db()
        self.assertEqual(self.job.last_status, 'warning')
        self.assertIn('paused', self.job.last_error_message.lower())
        self.assertGreater(self.job.next_run, timezone.now())
        mock_send_mail.assert_called_once()

    def test_dashboard_renders_countdown_and_paused_warning(self):
        self.job.last_status = 'warning'
        self.job.last_error_message = "Scheduled trade was skipped because job 'Daily DCA' is currently paused."
        self.job.save()

        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="refresh-countdown"')
        self.assertContains(response, "Scheduled trade for <strong>Daily DCA</strong> was skipped because the job is currently paused.")


class PausedJobUpdateAndCardColorTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='pausedtestuser', password='password123')
        self.client.login(username='pausedtestuser', password='password123')
        self.exchange = SupportedExchange.objects.create(name='Binance', slug='binance')
        self.account = ExchangeAccount.objects.create(
            user=self.user,
            exchange=self.exchange,
            nickname='Paused Account',
            api_key='key',
            api_secret='secret'
        )
        self.job = AutobuyJob.objects.create(
            user=self.user,
            account=self.account,
            name='Paused Job',
            total_amount=50,
            interval='daily',
            start_time=timezone.now(),
            is_active=False
        )
        JobToken.objects.create(job=self.job, token_symbol='BTC/USDT', percentage=100)

    def test_job_card_border_yellow_when_paused(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'border-yellow-500')
        self.assertContains(response, 'Paused')

    def test_job_card_border_red_and_error_modal_when_failing(self):
        self.job.last_status = 'failure'
        self.job.last_error_message = 'Insufficient funds on Binance'
        self.job.save()

        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'border-red-500')
        self.assertContains(response, f'jobErrorModal-{self.job.pk}')
        self.assertContains(response, 'View Error Details')

    @patch('ccxt.binance')
    def test_update_paused_job_enable(self, mock_ccxt):
        mock_inst = MagicMock()
        mock_ccxt.return_value = mock_inst
        mock_inst.load_markets.return_value = {
            'BTC/USDT': {'symbol': 'BTC/USDT', 'base': 'BTC', 'quote': 'USDT', 'spot': True, 'active': True},
        }
        mock_inst.fetch_balance.return_value = {'USDT': {'free': 500}}

        url = reverse('job_edit', kwargs={'pk': self.job.pk})
        post_data = {
            'name': 'Paused Job Renamed',
            'account': self.account.pk,
            'total_amount': '50',
            'quote_currency': 'USDT',
            'interval': 'daily',
            'start_time': timezone.now().isoformat(),
            'tokens-TOTAL_FORMS': '1',
            'tokens-INITIAL_FORMS': '1',
            'tokens-MIN_NUM_FORMS': '0',
            'tokens-MAX_NUM_FORMS': '1000',
            'tokens-0-id': self.job.tokens.first().pk,
            'tokens-0-token_symbol': 'BTC',
            'tokens-0-percentage': '100',
            'action_active': 'enable'
        }
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)
        self.job.refresh_from_db()
        self.assertTrue(self.job.is_active)
        self.assertEqual(self.job.name, 'Paused Job Renamed')

    @patch('ccxt.binance')
    def test_update_paused_job_keep_paused(self, mock_ccxt):
        mock_inst = MagicMock()
        mock_ccxt.return_value = mock_inst
        mock_inst.load_markets.return_value = {
            'BTC/USDT': {'symbol': 'BTC/USDT', 'base': 'BTC', 'quote': 'USDT', 'spot': True, 'active': True},
        }
        mock_inst.fetch_balance.return_value = {'USDT': {'free': 500}}

        url = reverse('job_edit', kwargs={'pk': self.job.pk})
        post_data = {
            'name': 'Still Paused Job',
            'account': self.account.pk,
            'total_amount': '50',
            'quote_currency': 'USDT',
            'interval': 'daily',
            'start_time': timezone.now().isoformat(),
            'tokens-TOTAL_FORMS': '1',
            'tokens-INITIAL_FORMS': '1',
            'tokens-MIN_NUM_FORMS': '0',
            'tokens-MAX_NUM_FORMS': '1000',
            'tokens-0-id': self.job.tokens.first().pk,
            'tokens-0-token_symbol': 'BTC',
            'tokens-0-percentage': '100',
            'action_active': 'keep_paused'
        }
        response = self.client.post(url, post_data)
        self.job.refresh_from_db()
        self.assertFalse(self.job.is_active)
        self.assertEqual(self.job.name, 'Still Paused Job')

    def test_job_card_renders_resume_modal_when_paused(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'id="resumeJobModal-{self.job.pk}"')
        self.assertContains(response, 'Resume Job: Paused Job?')
        self.assertContains(response, 'Confirm & Resume')

    def test_job_card_renders_pause_button_with_indicator_when_active(self):
        self.job.is_active = True
        self.job.save()

        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'htmx-spinner-show')
        self.assertContains(response, 'Pause')
        self.assertContains(response, 'Updating...')
        self.assertNotContains(response, f'id="resumeJobModal-{self.job.pk}"')

    def test_trade_list_renders_graph_dropdown_and_profit_over_time(self):
        # Create a trade for the user
        Trade.objects.create(
            user=self.user,
            job=self.job,
            job_name='Paused Job',
            exchange_name='Binance',
            symbol='BTC/USDT',
            amount_spent=100.0,
            amount_received=0.002,
            purchase_price=50000.0,
            fee_incurred=0.1
        )
        response = self.client.get(reverse('trade_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="chart-metric-select"')
        self.assertContains(response, 'Profit Over Time ($)')
        self.assertContains(response, 'Cumulative Investment Growth ($)')
        self.assertIn('profit_values_json', response.context)
        self.assertIn('timeline_values_json', response.context)

    def test_job_card_renders_run_immediate_modal(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'id="runJobModal-{self.job.pk}"')
        self.assertContains(response, 'Execute Immediate Trade?')
        self.assertContains(response, 'Confirm & Execute Now')

    def test_dashboard_renders_holdings_link_and_recent_activity_cards(self):
        # Create test trades
        Trade.objects.create(
            user=self.user,
            job=self.job,
            job_name='DCA BTC Job',
            exchange_name='Binance',
            symbol='BTC/USDT',
            amount_spent=Decimal('100.00'),
            amount_received=Decimal('0.002'),
            purchase_price=Decimal('50000.00'),
            fee_incurred=Decimal('0.10'),
            status='completed'
        )

        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        # Check View All Trades in holdings header
        self.assertContains(response, 'View All Trades')
        self.assertContains(response, 'Portfolio Holdings')
        # Check Activity Card stack
        self.assertContains(response, 'id="recent-trades-stack"')
        self.assertContains(response, 'id="recent-jobs-stack"')
        self.assertContains(response, 'id="recent-cards-limit-select"')
        self.assertContains(response, 'value="3"')
        self.assertContains(response, 'value="6"')
        self.assertContains(response, 'value="10"')
        self.assertIn('recent_trades', response.context)
        self.assertIn('recent_jobs', response.context)
        self.assertEqual(len(response.context['recent_trades']), 1)
        self.assertEqual(len(response.context['recent_jobs']), 1)


import json
import io
from decimal import Decimal
from django.core.files.uploadedfile import SimpleUploadedFile
from .services.trade_backup_service import TradeBackupService


class TradeBackupAndImportTest(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(username='usera', password='password123', email='usera@example.com')
        self.user_b = User.objects.create_user(username='userb', password='password123', email='userb@example.com')
        
        self.exchange = SupportedExchange.objects.create(name='Binance', slug='binance')
        self.account_a = ExchangeAccount.objects.create(
            user=self.user_a,
            exchange=self.exchange,
            nickname='User A Account',
            api_key='key_a',
            api_secret='secret_a'
        )
        self.job_a = AutobuyJob.objects.create(
            user=self.user_a,
            account=self.account_a,
            name='Daily BTC & ETH',
            total_amount=Decimal('100.00'),
            quote_currency='USDT',
            interval='daily',
            start_time=timezone.now()
        )

        # Create trades for User A
        self.trade_1 = Trade.objects.create(
            user=self.user_a,
            job=self.job_a,
            job_name=self.job_a.name,
            exchange_name='Binance',
            symbol='BTC/USDT',
            order_id='ORD-1001',
            order_type='limit',
            amount_spent=Decimal('60.00'),
            amount_received=Decimal('0.001'),
            purchase_price=Decimal('60000.00'),
            fee_incurred=Decimal('0.06'),
            status='completed'
        )
        self.trade_2 = Trade.objects.create(
            user=self.user_a,
            job=self.job_a,
            job_name=self.job_a.name,
            exchange_name='Binance',
            symbol='ETH/USDT',
            order_id='ORD-1002',
            order_type='market',
            amount_spent=Decimal('40.00'),
            amount_received=Decimal('0.015'),
            purchase_price=Decimal('2666.6666'),
            fee_incurred=Decimal('0.04'),
            status='completed'
        )

    def test_export_trades_json(self):
        export_data = TradeBackupService.export_trades_json(self.user_a)
        self.assertEqual(export_data['schema_version'], '1.0')
        self.assertEqual(export_data['exported_by'], self.user_a.username)
        self.assertEqual(export_data['total_trades'], 2)
        self.assertEqual(len(export_data['trades']), 2)

        btc_trade = next(t for t in export_data['trades'] if t['symbol'] == 'BTC/USDT')
        self.assertEqual(btc_trade['order_id'], 'ORD-1001')
        self.assertEqual(btc_trade['amount_spent'], '60.00000000')
        self.assertEqual(btc_trade['job_name'], 'Daily BTC & ETH')

    def test_import_trades_json_to_second_account(self):
        # Export from User A
        export_data = TradeBackupService.export_trades_json(self.user_a)
        json_bytes = json.dumps(export_data).encode('utf-8')
        uploaded_file = SimpleUploadedFile("backup.json", json_bytes, content_type="application/json")

        # Import into User B
        self.assertEqual(Trade.objects.filter(user=self.user_b).count(), 0)
        result = TradeBackupService.import_trades(self.user_b, uploaded_file)

        self.assertTrue(result['success'])
        self.assertEqual(result['imported_count'], 2)
        self.assertEqual(result['skipped_duplicates'], 0)
        self.assertEqual(result['total_records'], 2)

        # Verify User B now has 2 trades
        user_b_trades = Trade.objects.filter(user=self.user_b).order_by('order_id')
        self.assertEqual(user_b_trades.count(), 2)
        self.assertEqual(user_b_trades[0].order_id, 'ORD-1001')
        self.assertEqual(user_b_trades[0].symbol, 'BTC/USDT')
        self.assertEqual(user_b_trades[0].user, self.user_b)
        self.assertEqual(user_b_trades[1].order_id, 'ORD-1002')

    def test_import_trades_deduplication(self):
        export_data = TradeBackupService.export_trades_json(self.user_a)
        json_bytes = json.dumps(export_data).encode('utf-8')

        # First import
        file_1 = SimpleUploadedFile("backup.json", json_bytes, content_type="application/json")
        result_1 = TradeBackupService.import_trades(self.user_b, file_1)
        self.assertEqual(result_1['imported_count'], 2)

        # Second import (exact duplicate file)
        file_2 = SimpleUploadedFile("backup.json", json_bytes, content_type="application/json")
        result_2 = TradeBackupService.import_trades(self.user_b, file_2)
        self.assertTrue(result_2['success'])
        self.assertEqual(result_2['imported_count'], 0)
        self.assertEqual(result_2['skipped_duplicates'], 2)
        self.assertEqual(Trade.objects.filter(user=self.user_b).count(), 2)

    def test_import_trades_csv(self):
        csv_content = (
            "Timestamp,Job Name,Exchange,Pair,Side,Amount Spent,Amount Received,Price,Fee,Status,Order ID\n"
            "2026-08-01 12:00:00,Manual CSV Job,Kraken,SOL/USD,buy,50.00,0.35,142.8571,0.05,Success,ORD-CSV-1\n"
        ).encode('utf-8')
        uploaded_file = SimpleUploadedFile("trades.csv", csv_content, content_type="text/csv")

        result = TradeBackupService.import_trades(self.user_b, uploaded_file)
        self.assertTrue(result['success'])
        self.assertEqual(result['imported_count'], 1)
        
        imported_trade = Trade.objects.filter(user=self.user_b, order_id='ORD-CSV-1').first()
        self.assertIsNotNone(imported_trade)
        self.assertEqual(imported_trade.symbol, 'SOL/USD')
        self.assertEqual(imported_trade.exchange_name, 'Kraken')
        self.assertEqual(imported_trade.amount_spent, Decimal('50.00'))

    def test_export_backup_view_http(self):
        self.client.force_login(self.user_a)
        response = self.client.get(reverse('trade_backup_export'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertIn('attachment; filename="moondrip_portfolio_backup_usera_', response['Content-Disposition'])

        payload = json.loads(response.content.decode('utf-8'))
        self.assertEqual(payload['schema_version'], '1.0')
        self.assertEqual(len(payload['trades']), 2)

    def test_import_view_http(self):
        self.client.force_login(self.user_b)
        export_data = TradeBackupService.export_trades_json(self.user_a)
        json_bytes = json.dumps(export_data).encode('utf-8')
        uploaded_file = SimpleUploadedFile("backup.json", json_bytes, content_type="application/json")

        response = self.client.post(reverse('trade_import'), {'backup_file': uploaded_file}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Trade.objects.filter(user=self.user_b).count(), 2)








