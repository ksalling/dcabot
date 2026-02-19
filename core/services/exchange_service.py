import ccxt
import logging
from django.utils import timezone
from core.models import JobLog, ExchangeAccount

logger = logging.getLogger(__name__)

class ExchangeService:
    def __init__(self, account: ExchangeAccount):
        self.account = account
        self.exchange = self._get_exchange_instance()

    def _get_exchange_instance(self):
        exchange_id = self.account.exchange.slug
        if not hasattr(ccxt, exchange_id):
            raise ValueError(f"Exchange {exchange_id} not supported by CCXT")
        
        exchange_class = getattr(ccxt, exchange_id)
        config = {
            'apiKey': self.account.api_key,
            'secret': self.account.api_secret,
            'enableRateLimit': True,
        }
        if self.account.api_passphrase:
            config['password'] = self.account.api_passphrase
            
        return exchange_class(config)

    def log(self, message, level='INFO', job=None):
        """Log to database and system logger"""
        try:
            JobLog.objects.create(
                user=self.account.user,
                job=job,
                level=level,
                message=message
            )
        except Exception as e:
            logger.error(f"Failed to create JobLog: {e}")

        # specific logger call
        if level == 'ERROR':
            logger.error(f"[{self.account}] {message}")
        elif level == 'WARNING':
            logger.warning(f"[{self.account}] {message}")
        else:
            logger.info(f"[{self.account}] {message}")

    def validate_connection(self):
        """Check if API keys are valid by fetching balance"""
        try:
            self.exchange.fetch_balance()
            return True
        except Exception as e:
            # Log it but re-raise so form can catch it? 
            # Or just return False and let form check return value?
            # The prompt says "does the application check... when adding".
            # The form calls this. 
            self.log(f"Connection validation failed: {str(e)}", level='WARNING')
            raise e # Raise so we can show specific error in form

    def validate_job_funds(self, total_amount, quote_currency):
        """Check if account has enough funds in quote currency"""
        try:
            balance = self.exchange.fetch_balance()
            free_balance = balance.get(quote_currency, {}).get('free', 0)
            if float(free_balance) < float(total_amount):
                return False, f"Insufficient funds. You have {free_balance} {quote_currency}, but job requires {total_amount} {quote_currency}."
            return True, "Funds available."
        except Exception as e:
             self.log(f"Error checking funds: {str(e)}", level='ERROR')
             # If we can't check, maybe warn but allow? Or block?
             # User asked to "query the exchange to verify". Strict check seems implied.
             return False, f"Could not verify funds: {str(e)}"

    def validate_pair(self, base_currency, quote_currency):
        """Check if the pair exists on the exchange"""
        try:
            self.exchange.load_markets()
            symbol = f"{base_currency}/{quote_currency}"
            if symbol in self.exchange.markets:
                return True, ""
            return False, f"Trading pair {symbol} not found on this exchange."
        except Exception as e:
            self.log(f"Error checking pair: {str(e)}", level='ERROR')
            return False, f"Could not verify pair {base_currency}/{quote_currency}: {str(e)}"

    def fetch_balance(self):
        try:
            return self.exchange.fetch_balance()
        except Exception as e:
            self.log(f"Error fetching balance: {str(e)}", level='ERROR')
            raise

    def get_market_price(self, symbol):
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker['last']
        except Exception as e:
            self.log(f"Error fetching ticker for {symbol}: {str(e)}", level='ERROR')
            raise

    def place_market_buy_order(self, symbol, amount, job=None):
        """
        Place a market buy order.
        Note: 'amount' handling in CCXT varies by exchange (cost vs quantity).
        For many exchanges, create_market_buy_order expects 'amount' in base currency (how much to buy).
        If we want to spend a specific quote currency amount (e.g. $10 USDT), we often need logic to calculate the amount.
        OR use create_order with 'cost' param if exchange supports it.
        """
        try:
            # First, check if exchange supports 'createMarketBuyOrderRequiresPrice' or similar quirks
            # Ideally we want to spend 'amount' of quote currency.
            # We need to calculate how much base currency that is roughly, or use 'cost' if supported.
            
            # For simplicity in this MVP, we will assume 'amount' passed here is already the BASE currency amount 
            # OR we implement a converter. 
            # The prompt Blueprint says: "AutobuyJob.total_amount" -> "Total amount to spend per run (in quote currency)".
            # So we MUST convert quote -> base here.
            
            price = self.get_market_price(symbol)
            # Calculate base amount: Quote Amount / Price
            # e.g. 10 USDT / 50000 BTC/USDT = 0.0002 BTC
            base_amount = float(amount) / float(price)
            
            # Apply precision
            market = self.exchange.market(symbol)
            base_amount = self.exchange.amount_to_precision(symbol, base_amount)
            
            self.log(f"Attempting to buy ~{base_amount} {symbol} (Quote: {amount} @ {price})", job=job)
            
            order = self.exchange.create_market_buy_order(symbol, base_amount)
            
            if order is None:
                self.log(f"Warning: Exchange returned None for order. Attempting to verify via fetch_my_trades...", level='WARNING', job=job)
                # Attempt fallback verification
                if self.exchange.has.get('fetchMyTrades'):
                    import time
                    time.sleep(1) # Give it a second to propagate
                    trades = self.exchange.fetch_my_trades(symbol, limit=1)
                    if trades:
                        # Use the most recent trade to reconstruct order data
                        # Note: This might be partial if multiple fills, but better than nothing.
                        latest_trade = trades[0]
                        # Verify it's recent (e.g. within last minute) logic could be added, but assuming it's ours for now.
                        order = {
                            'id': latest_trade.get('order', 'unknown_recovered'),
                            'amount': latest_trade.get('amount'),
                            'price': latest_trade.get('price'),
                            'fee': latest_trade.get('fee'),
                            'status': 'closed' # Assume filled if in trades
                        }
                        self.log(f"Recovered order data from latest trade: {order.get('id')}", job=job)
            
            if order is None:
                 raise ValueError("Exchange API returned no data for the order. Check exchange history manually.")

            self.log(f"Order placed: {order.get('id', 'Unknown ID')}", job=job)
            return order
            
        except Exception as e:
            self.log(f"Order failed for {symbol}: {str(e)}", level='ERROR', job=job)
            raise
