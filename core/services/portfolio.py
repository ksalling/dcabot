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
        trades = Trade.objects.filter(user=self.user)
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

        # 2. Fetch Current Prices
        # We need an exchange instance to fetch prices.
        # Use the first active account found for the user.
        account = ExchangeAccount.objects.filter(user=self.user, is_active=True).first()
        ticker_map = {}
        
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
                # Try fetchTickers first if list is long? 
                # For safety/compatibility, loop fetchTicker or fetchTickers if supported.
                # CCXT fetchTickers usually takes a list of symbols.
                
                symbols_to_fetch = list(holdings_map.keys())
                try:
                    tickers = exchange.fetch_tickers(symbols_to_fetch)
                    # format: {'BTC/USDT': {'last': 50000, ...}}
                    for s, data in tickers.items():
                        ticker_map[s] = Decimal(str(data['last'])) if data.get('last') else Decimal(0)
                except Exception:
                    # Fallback to loop if fetchTickers fails or not supported
                    for s in symbols_to_fetch:
                        try:
                            ticker = exchange.fetch_ticker(s)
                            ticker_map[s] = Decimal(str(ticker['last'])) if ticker.get('last') else Decimal(0)
                        except Exception as e:
                            logger.error(f"Failed to fetch ticker for {s}: {e}")
                            ticker_map[s] = Decimal(0)

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
            current_price = ticker_map.get(symbol, avg_price) # Fallback to cost price if no current price
            
            # If we couldn't fetch price, we can't calculate real PnL.
            # Using avg_price means PnL is 0.
            
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
