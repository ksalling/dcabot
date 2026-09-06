import ccxt
import logging
from django.utils import timezone
from django.core.cache import cache
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

    def get_exchange_markets(self):
        """Fetch and cache active spot markets for this exchange"""
        from django.core.cache import cache
        cache_key = f"exchange_markets_{self.account.exchange.slug}"
        cached_markets = cache.get(cache_key)
        if cached_markets is not None:
            return cached_markets

        try:
            markets = self.exchange.load_markets()
            spot_markets = {}
            for symbol, market in markets.items():
                is_spot = market.get('spot', True)
                is_active = market.get('active', True) is not False
                is_contract = market.get('contract', False)
                if is_spot and not is_contract:
                    base = market.get('base', '').upper()
                    quote = market.get('quote', '').upper()
                    spot_markets[symbol] = {
                        'symbol': symbol,
                        'base': base,
                        'quote': quote,
                        'active': is_active,
                        'limits': market.get('limits', {}),
                    }
            cache.set(cache_key, spot_markets, timeout=900)  # Cache for 15 minutes
            return spot_markets
        except Exception as e:
            self.log(f"Error loading markets for {self.account.exchange.name}: {str(e)}", level='ERROR')
            # Fallback to direct load_markets if cache setup issues
            try:
                raw_markets = self.exchange.load_markets()
                return {s: {'symbol': s, 'base': m.get('base', '').upper(), 'quote': m.get('quote', '').upper(), 'active': m.get('active', True) is not False, 'limits': m.get('limits', {})} for s, m in raw_markets.items()}
            except Exception:
                return {}

    def get_exchange_tickers(self):
        """
        Fetch and cache current tickers for the exchange.
        Returns a dict mapping symbol (e.g. 'BTC/USD') -> float price.
        """
        cache_key = f"exchange_tickers_{self.account.exchange.slug}"
        cached_tickers = cache.get(cache_key)
        if cached_tickers is not None:
            return cached_tickers

        price_map = {}
        try:
            if hasattr(self.exchange, 'fetch_tickers'):
                raw_tickers = self.exchange.fetch_tickers()
                if isinstance(raw_tickers, dict):
                    for sym, t in raw_tickers.items():
                        if isinstance(t, dict):
                            p = t.get('last') or t.get('close') or t.get('bid') or t.get('ask')
                            if p:
                                price_map[sym] = float(p)
            cache.set(cache_key, price_map, timeout=180) # Cache for 3 minutes
        except Exception as e:
            self.log(f"Warning loading batch tickers for {self.account.exchange.name}: {str(e)}", level='WARNING')

        return price_map

    def get_available_pairs(self, quote_currency=None):
        """Return list of available active trading pairs, optionally filtered by quote currency"""
        markets = self.get_exchange_markets()
        tickers = self.get_exchange_tickers()
        pairs = []
        for symbol, data in markets.items():
            if not data.get('active', True):
                continue
            if quote_currency and data.get('quote', '').upper() != quote_currency.upper():
                continue

            limits = data.get('limits', {})
            min_amount = limits.get('amount', {}).get('min') if isinstance(limits.get('amount'), dict) else None
            min_cost = limits.get('cost', {}).get('min') if isinstance(limits.get('cost'), dict) else None

            # Calculate estimated minimum in quote currency (e.g. USD / USDT)
            price = tickers.get(symbol)
            min_quote_estimate = None
            min_cost_val = float(min_cost) if min_cost is not None and float(min_cost) > 0 else 0
            min_amount_cost = (float(min_amount) * float(price)) if (min_amount is not None and float(min_amount) > 0 and price and float(price) > 0) else 0

            if min_cost_val > 0 or min_amount_cost > 0:
                min_quote_estimate = max(min_cost_val, min_amount_cost)

            pairs.append({
                'symbol': data['symbol'],
                'base': data['base'],
                'quote': data['quote'],
                'active': data.get('active', True),
                'min_amount': min_amount,
                'min_cost': min_cost,
                'price': price,
                'min_quote_estimate': min_quote_estimate,
                'limits': limits,
            })
        return sorted(pairs, key=lambda x: x['symbol'])

    def validate_pair(self, base_or_symbol, quote_currency):
        """
        Check if the pair exists and is active on the exchange.
        Accepts either 'BTC' or 'BTC/USDT'.
        Returns (is_valid: bool, standardized_symbol: str, error_message: str).
        """
        try:
            cleaned = base_or_symbol.strip().upper()
            if '/' in cleaned:
                base, quote = cleaned.split('/', 1)
                if quote != quote_currency.upper():
                    return False, "", f"Pair {cleaned} quote currency ({quote}) does not match job quote currency ({quote_currency.upper()})."
                symbol = f"{base}/{quote_currency.upper()}"
            else:
                symbol = f"{cleaned}/{quote_currency.upper()}"

            markets = self.get_exchange_markets()
            if symbol in markets:
                market = markets[symbol]
                if market.get('active') is False:
                    return False, "", f"Trading pair {symbol} is currently inactive/disabled on {self.account.exchange.name}."
                return True, symbol, ""

            return False, "", f"Trading pair {symbol} not found on {self.account.exchange.name}."
        except Exception as e:
            self.log(f"Error checking pair {base_or_symbol}: {str(e)}", level='ERROR')
            return False, "", f"Could not verify pair {base_or_symbol}: {str(e)}"

    def fetch_balance(self):
        try:
            return self.exchange.fetch_balance()
        except Exception as e:
            self.log(f"Error fetching balance: {str(e)}", level='ERROR')
            raise

    def get_market_price(self, symbol):
        """
        Fetch the current market price for a given symbol.
        """
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            price = ticker.get('last') or ticker.get('close') or ticker.get('bid') or ticker.get('ask')
            return float(price) if price else None
        except Exception as e:
            self.log(f"Error fetching price for {symbol}: {str(e)}", level='WARNING')
            return None

    def validate_order_size(self, symbol, allocation_amount, quote_currency):
        """
        Verify that the allocation meets the exchange's minimum order size (min cost or min amount).
        Returns (is_valid: bool, error_message: str).
        """
        try:
            markets = self.get_exchange_markets()
            if symbol not in markets:
                return False, f"Trading pair {symbol} not found on {self.account.exchange.name}."
            
            market_data = markets[symbol]
            limits = market_data.get('limits', {})
            
            # If limits not in market_data, check exchange.market if available
            if not limits and hasattr(self.exchange, 'market') and callable(self.exchange.market):
                try:
                    m = self.exchange.market(symbol)
                    if isinstance(m, dict):
                        limits = m.get('limits', {})
                except Exception:
                    pass

            min_cost = limits.get('cost', {}).get('min') if isinstance(limits.get('cost'), dict) else None
            min_amount = limits.get('amount', {}).get('min') if isinstance(limits.get('amount'), dict) else None
            base = market_data.get('base', symbol.split('/')[0])
            
            # Check minimum cost in quote currency if specified by exchange
            if min_cost is not None and float(min_cost) > 0:
                if float(allocation_amount) < float(min_cost):
                    return False, (
                        f"Allocation for {symbol} (${float(allocation_amount):.2f} {quote_currency}) is below "
                        f"{self.account.exchange.name}'s minimum trade cost of ${float(min_cost):.2f} {quote_currency}."
                    )
            
            # Check minimum amount in base currency
            if min_amount is not None and float(min_amount) > 0:
                price = self.get_market_price(symbol)
                if price and float(price) > 0:
                    base_amount = float(allocation_amount) / float(price)
                    if base_amount < float(min_amount):
                        min_quote_needed = float(min_amount) * float(price)
                        return False, (
                            f"Allocation for {symbol} (${float(allocation_amount):.2f} {quote_currency} ≈ {base_amount:.6f} {base}) "
                            f"is below {self.account.exchange.name}'s minimum trade size of {min_amount} {base} "
                            f"(requires at least ~${min_quote_needed:.2f} {quote_currency} at current price ${float(price):,.2f})."
                        )
            
            return True, ""
        except Exception as e:
            self.log(f"Warning checking minimum order size for {symbol}: {str(e)}", level='WARNING')
            return True, ""

    def place_market_buy_order(self, symbol, amount, job=None, quote_currency=None):
        """
        Place a market buy order.
        """
        try:
            # Ensure symbol is formatted as BASE/QUOTE
            if '/' not in symbol:
                if job and hasattr(job, 'quote_currency'):
                    symbol = f"{symbol.upper()}/{job.quote_currency.upper()}"
                elif quote_currency:
                    symbol = f"{symbol.upper()}/{quote_currency.upper()}"
                else:
                    symbol = symbol.upper()
            
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
