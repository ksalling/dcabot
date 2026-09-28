from decimal import Decimal
from django.db.models import Sum
from core.models import Trade, ExchangeAccount
import ccxt
import logging

logger = logging.getLogger(__name__)

class PortfolioService:
    def __init__(self, user):
        self.user = user

    def get_portfolio_summary(self):
        """
        Aggregates trades to calculate holdings, cost basis, and current portfolio value.
        Returns:
            {
                'total_cost_basis': Decimal,
                'total_current_value': Decimal,
                'total_pnl': Decimal,
                'total_pnl_percent': Decimal,
                'holdings': [
                    {
                        'symbol': 'BTC',
                        'quantity': Decimal,
                        'cost_basis': Decimal,
                        'avg_price': Decimal,
                        'current_price': Decimal,
                        'current_value': Decimal,
                        'pnl': Decimal,
                        'pnl_percent': Decimal
                    }, ...
                ]
            }
        """
        # 1. Aggregate Trades
        trades = Trade.objects.filter(user=self.user, status='completed', amount_received__gt=0)
        holdings_map = {}

        for trade in trades:
            # Symbol is e.g. "BTC/USDT"
            # We want to group by the Asset (BTC).
            # But cost basis is in Quote (USDT).
            # If user mixes quotes (BTC/USDT, BTC/USD), this gets messy.
            # For now, we assume the 'symbol' is the key we track.
            
            symbol = trade.symbol
            if symbol not in holdings_map:
                holdings_map[symbol] = {
                    'quantity': Decimal(0),
                    'cost': Decimal(0),
                    'fees': Decimal(0)
                }
            
            holdings_map[symbol]['quantity'] += trade.amount_received
            holdings_map[symbol]['cost'] += trade.amount_spent
            holdings_map[symbol]['fees'] += trade.fee_incurred

        # 2. Fetch Current Prices & 24h Changes
        # We need an exchange instance to fetch prices.
        # Check user's preferred pricing exchange account, or fallback to the first active account found for the user.
        profile = getattr(self.user, 'userprofile', None)
        account = None
        if profile and profile.preferred_pricing_account and profile.preferred_pricing_account.is_active:
            account = profile.preferred_pricing_account
        if not account:
            account = ExchangeAccount.objects.filter(user=self.user, is_active=True).first()
        ticker_map = {}

        def extract_ticker_data(data):
            if not isinstance(data, dict):
                return {'price': None, 'change_24h': Decimal('0')}
            price = None
            if data.get('last') is not None and float(data['last']) > 0:
                price = Decimal(str(data['last']))
            elif data.get('close') is not None and float(data['close']) > 0:
                price = Decimal(str(data['close']))

            change_24h = Decimal('0')
            if data.get('percentage') is not None:
                try:
                    change_24h = Decimal(str(data['percentage']))
                except Exception:
                    change_24h = Decimal('0')
            elif data.get('open') and data.get('last') and float(data['open']) > 0:
                try:
                    change_24h = ((Decimal(str(data['last'])) - Decimal(str(data['open']))) / Decimal(str(data['open']))) * Decimal('100')
                except Exception:
                    change_24h = Decimal('0')
            elif data.get('previousClose') and data.get('last') and float(data['previousClose']) > 0:
                try:
                    change_24h = ((Decimal(str(data['last'])) - Decimal(str(data['previousClose']))) / Decimal(str(data['previousClose']))) * Decimal('100')
                except Exception:
                    change_24h = Decimal('0')
            return {'price': price, 'change_24h': change_24h}
        
        if account and holdings_map:
            try:
                # Initialize exchange securely
                exchange_class = getattr(ccxt, account.exchange.slug)
                exchange = exchange_class({
                    'apiKey': account.api_key,
                    'secret': account.api_secret,
                })
                if account.api_passphrase:
                    exchange.password = account.api_passphrase
                
                # Fetch tickers for all held symbols
                # Some exchanges support fetchTickers (plural), others don't.
                symbols_to_fetch = list(holdings_map.keys())
                try:
                    tickers = exchange.fetch_tickers(symbols_to_fetch)
                    for s, data in tickers.items():
                        ticker_map[s] = extract_ticker_data(data)
                except Exception:
                    # Fallback to loop if fetchTickers fails or not supported
                    for s in symbols_to_fetch:
                        try:
                            ticker = exchange.fetch_ticker(s)
                            ticker_map[s] = extract_ticker_data(ticker)
                        except Exception as e:
                            logger.error(f"Failed to fetch ticker for {s}: {e}")
                            ticker_map[s] = {'price': None, 'change_24h': Decimal('0')}

            except Exception as e:
                logger.error(f"Failed to initialize exchange for price checks: {e}")

        # 3. Calculate PnL
        portfolio_total_cost = Decimal(0)
        portfolio_current_value = Decimal(0)
        
        holdings_list = []
        
        for symbol, data in holdings_map.items():
            qty = data['quantity']
            cost = data['cost'] # Total spent in quote currency
            
            # Skip if quantity is 0 (sold everything? not handling sells yet though)
            if qty == 0:
                continue

            avg_price = cost / qty
            t_data = ticker_map.get(symbol, {})
            current_price = t_data.get('price') or avg_price # Fallback to cost price if no current price
            change_24h = t_data.get('change_24h', Decimal('0'))
            
            # Custom Logic per User Request:
            # Current Value = (Qty * Price) - Total Fees
            # PnL = Current Value - Cost Basis
            
            gross_value = qty * current_price
            current_value = gross_value - data['fees']
            
            pnl = current_value - cost
            pnl_percent = (pnl / cost) * 100 if cost > 0 else Decimal(0)
            
            portfolio_total_cost += cost
            portfolio_current_value += current_value
            
            holdings_list.append({
                'symbol': symbol,
                'quantity': qty,
                'cost_basis': cost,
                'avg_price': avg_price,
                'current_price': current_price,
                'change_24h': change_24h,
                'current_value': current_value,
                'pnl': pnl,
                'pnl_percent': pnl_percent,
                'fees': data['fees']
            })

        total_pnl = portfolio_current_value - portfolio_total_cost
        total_pnl_percent = (total_pnl / portfolio_total_cost) * 100 if portfolio_total_cost > 0 else Decimal(0)
        
        return {
            'total_cost_basis': portfolio_total_cost,
            'total_current_value': portfolio_current_value,
            'total_pnl': total_pnl,
            'total_pnl_percent': total_pnl_percent,
            'holdings': holdings_list
        }
