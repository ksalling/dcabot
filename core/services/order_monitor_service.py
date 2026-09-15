import logging
from decimal import Decimal
from django.utils import timezone
from core.models import Trade, JobLog
from .exchange_service import ExchangeService
from .notification_service import NotificationService

logger = logging.getLogger(__name__)

def safe_decimal(val, fallback=0):
    if val is None or val == '' or str(val).strip().lower() in ['none', 'null', 'nan']:
        return Decimal(str(fallback))
    try:
        return Decimal(str(val))
    except Exception:
        return Decimal(str(fallback))

class OrderMonitorService:
    @staticmethod
    def get_exchange_service_for_trade(trade: Trade):
        if trade.job and trade.job.account:
            return ExchangeService(trade.job.account)
        elif trade.user and trade.user.exchange_accounts.filter(is_active=True).exists():
            # Fallback to user's matching active exchange account
            acc = trade.user.exchange_accounts.filter(
                exchange__name__iexact=trade.exchange_name,
                is_active=True
            ).first() or trade.user.exchange_accounts.filter(is_active=True).first()
            if acc:
                return ExchangeService(acc)
        return None

    @classmethod
    def sync_open_orders_for_user(cls, user):
        """
        Synchronously checks and updates any open limit orders for a specific user against the exchange.
        Called on dashboard loads / live polls to guarantee instant real-time reflection of order fills.
        """
        if not user or not user.is_authenticated:
            return

        open_trades = Trade.objects.filter(user=user, order_type='limit', status='open')
        if not open_trades.exists():
            return

        for trade in open_trades:
            try:
                exchange_service = cls.get_exchange_service_for_trade(trade)
                if not exchange_service or not trade.order_id:
                    continue

                order_data = exchange_service.fetch_order_status(trade.order_id, trade.symbol, job=trade.job)
                if not order_data:
                    continue

                order_status = order_data.get('status')

                # 1. Order is fully filled
                if order_status == 'closed':
                    trade.amount_received = safe_decimal(order_data.get('filled') or order_data.get('amount'), 0)
                    trade.amount_spent = safe_decimal(order_data.get('cost') or trade.amount_spent, trade.amount_spent)
                    trade.purchase_price = safe_decimal(order_data.get('average') or order_data.get('price') or trade.purchase_price, trade.purchase_price)
                    trade.fee_incurred = safe_decimal((order_data.get('fee') or {}).get('cost'), 0)
                    trade.status = 'completed'
                    trade.save()

                    exchange_service.log(
                        f"Maker limit order {trade.order_id} filled: Bought {trade.amount_received} {trade.symbol} for ${trade.amount_spent}.",
                        level='INFO',
                        job=trade.job
                    )
                    if trade.job:
                        NotificationService.send_trade_success_email(trade.job, [trade])
                    continue

                # 2. Order was canceled/rejected externally
                if order_status in ['canceled', 'rejected', 'expired']:
                    filled = safe_decimal(order_data.get('filled'), 0)
                    filled_cost = safe_decimal(order_data.get('cost'), 0)
                    initial_allocation = trade.amount_spent

                    if filled > 0:
                        trade.amount_received = filled
                        trade.amount_spent = filled_cost
                        trade.purchase_price = safe_decimal(order_data.get('average') or order_data.get('price'), trade.purchase_price)
                        trade.fee_incurred = safe_decimal((order_data.get('fee') or {}).get('cost'), 0)
                        trade.status = 'completed'
                        remaining_quote = initial_allocation - filled_cost
                    else:
                        trade.status = 'canceled'
                        trade.amount_spent = 0
                        remaining_quote = initial_allocation

                    trade.save()
                    if remaining_quote > Decimal('0.01') and trade.job and trade.job.is_active:
                        cls.cancel_and_replace_limit_order(trade, exchange_service=exchange_service)
                    continue

                # 3. Timeout check
                timeout_minutes = trade.job.limit_order_timeout_minutes if (trade.job and trade.job.limit_order_timeout_minutes) else 15
                order_age_seconds = (timezone.now() - trade.timestamp).total_seconds()
                if order_age_seconds >= (timeout_minutes * 60):
                    cls.cancel_and_replace_limit_order(trade, exchange_service=exchange_service)

            except Exception as e:
                logger.error(f"Error syncing open limit trade {trade.id} for user {user.id}: {e}")

    @classmethod
    def check_open_limit_orders(cls):
        """
        Periodically checks all open limit orders across the platform.
        - Fills: Updates trade status to completed and sends success email.
        - Stale/Timed-out: Cancels on exchange and replaces with a fresh limit order at the new bid price.
        """
        open_trades = Trade.objects.filter(order_type='limit', status='open')
        if not open_trades.exists():
            return

        logger.info(f"Checking {open_trades.count()} open limit orders...")

        for trade in open_trades:
            try:
                exchange_service = cls.get_exchange_service_for_trade(trade)
                if not exchange_service:
                    logger.warning(f"No exchange service available for open trade {trade.id} ({trade.symbol})")
                    continue

                if not trade.order_id:
                    continue

                order_data = exchange_service.fetch_order_status(trade.order_id, trade.symbol, job=trade.job)
                if not order_data:
                    continue

                order_status = order_data.get('status')

                # 1. Order is fully filled
                if order_status == 'closed':
                    trade.amount_received = safe_decimal(order_data.get('filled') or order_data.get('amount'), 0)
                    trade.amount_spent = safe_decimal(order_data.get('cost') or trade.amount_spent, trade.amount_spent)
                    trade.purchase_price = safe_decimal(order_data.get('average') or order_data.get('price') or trade.purchase_price, trade.purchase_price)
                    trade.fee_incurred = safe_decimal((order_data.get('fee') or {}).get('cost'), 0)
                    trade.status = 'completed'
                    trade.save()

                    exchange_service.log(
                        f"Maker limit order {trade.order_id} filled: Bought {trade.amount_received} {trade.symbol} for ${trade.amount_spent}.",
                        level='INFO',
                        job=trade.job
                    )
                    if trade.job:
                        NotificationService.send_trade_success_email(trade.job, [trade])
                    continue

                # 2. Order was canceled or rejected externally -> Auto-replace at new bid
                if order_status in ['canceled', 'rejected', 'expired']:
                    filled = safe_decimal(order_data.get('filled'), 0)
                    filled_cost = safe_decimal(order_data.get('cost'), 0)
                    initial_allocation = trade.amount_spent

                    if filled > 0:
                        trade.amount_received = filled
                        trade.amount_spent = filled_cost
                        trade.purchase_price = safe_decimal(order_data.get('average') or order_data.get('price'), trade.purchase_price)
                        trade.fee_incurred = safe_decimal((order_data.get('fee') or {}).get('cost'), 0)
                        trade.status = 'completed'
                        remaining_quote = initial_allocation - filled_cost
                    else:
                        trade.status = 'canceled'
                        trade.amount_spent = 0
                        remaining_quote = initial_allocation

                    trade.save()
                    exchange_service.log(
                        f"Limit order {trade.order_id} for {trade.symbol} was {order_status}. Automatically replacing at new bid price...",
                        level='WARNING',
                        job=trade.job
                    )

                    # Auto-replace order at fresh bid if job is active and remaining allocation > 0
                    if remaining_quote > Decimal('0.01') and trade.job and trade.job.is_active:
                        cls.cancel_and_replace_limit_order(trade, exchange_service=exchange_service)
                    continue

                # 3. Order is still open -> Check Timeout
                timeout_minutes = 15
                if trade.job and trade.job.limit_order_timeout_minutes:
                    timeout_minutes = trade.job.limit_order_timeout_minutes

                order_age_seconds = (timezone.now() - trade.timestamp).total_seconds()
                if order_age_seconds >= (timeout_minutes * 60):
                    exchange_service.log(
                        f"Limit order {trade.order_id} ({trade.symbol}) timed out after {int(order_age_seconds // 60)} minutes. Canceling and replacing at current bid...",
                        level='INFO',
                        job=trade.job
                    )
                    cls.cancel_and_replace_limit_order(trade, exchange_service=exchange_service)

            except Exception as e:
                logger.error(f"Error checking open limit trade {trade.id}: {e}")

    @classmethod
    def cancel_and_replace_limit_order(cls, trade: Trade, exchange_service=None):
        """
        Cancels an open limit order, records any partial fills, and creates a replacement maker
        limit order at the latest bid price for any remaining allocation.
        """
        if not exchange_service:
            exchange_service = cls.get_exchange_service_for_trade(trade)
            if not exchange_service:
                raise ValueError("Could not resolve exchange account for trade.")

        # 1. Cancel on exchange
        if trade.order_id:
            exchange_service.cancel_order_safe(trade.order_id, trade.symbol, job=trade.job)

        # 2. Fetch final order status to check for partial fills
        final_order = exchange_service.fetch_order_status(trade.order_id, trade.symbol, job=trade.job) or {}
        filled_amount = safe_decimal(final_order.get('filled'), 0)
        filled_cost = safe_decimal(final_order.get('cost'), 0)

        initial_allocation = trade.amount_spent

        if filled_amount > 0:
            trade.amount_received = filled_amount
            trade.amount_spent = filled_cost
            trade.purchase_price = safe_decimal(final_order.get('average') or final_order.get('price'), trade.purchase_price)
            trade.fee_incurred = safe_decimal((final_order.get('fee') or {}).get('cost'), 0)
            trade.status = 'completed'
            remaining_quote = initial_allocation - filled_cost
        else:
            trade.status = 'canceled'
            remaining_quote = initial_allocation

        trade.save()

        # 3. If remaining quote is positive, place a replacement maker limit order
        if remaining_quote > Decimal('0.01'):
            quote_curr = trade.job.quote_currency if trade.job else (trade.symbol.split('/')[1] if '/' in trade.symbol else 'USD')
            is_valid_size, _ = exchange_service.validate_order_size(trade.symbol, float(remaining_quote), quote_curr)
            
            if is_valid_size:
                try:
                    new_order = exchange_service.place_maker_limit_buy_order(
                        trade.symbol,
                        float(remaining_quote),
                        job=trade.job,
                        quote_currency=quote_curr
                    )
                    new_status = 'completed' if new_order.get('status') == 'closed' else 'open'
                    new_trade = Trade.objects.create(
                        job=trade.job,
                        user=trade.user,
                        exchange_name=trade.exchange_name,
                        symbol=trade.symbol,
                        job_name=trade.job_name,
                        order_type='limit',
                        amount_spent=safe_decimal(new_order.get('cost') or remaining_quote, remaining_quote),
                        amount_received=safe_decimal(new_order.get('filled') or (new_order.get('amount') if new_status == 'completed' else 0), 0),
                        purchase_price=safe_decimal(new_order.get('average') or new_order.get('price'), 0),
                        fee_incurred=safe_decimal((new_order.get('fee') or {}).get('cost'), 0),
                        order_id=str(new_order.get('id', '')),
                        status=new_status
                    )
                    exchange_service.log(
                        f"Replaced limit order {trade.order_id} with new order {new_trade.order_id} ({new_status}) at bid {new_trade.purchase_price}",
                        level='INFO',
                        job=trade.job
                    )
                    return new_trade
                except Exception as e:
                    exchange_service.log(f"Failed to place replacement limit order for {trade.symbol}: {e}", level='ERROR', job=trade.job)
        return None

    @classmethod
    def manual_cancel_order(cls, trade: Trade, user):
        """
        Manually cancel an open order at the user's request.
        """
        if trade.user != user:
            return False, "Unauthorized access to trade."
        if trade.status != 'open':
            return False, "Order is not currently open."

        exchange_service = cls.get_exchange_service_for_trade(trade)
        if not exchange_service:
            return False, "Exchange account not available."

        if trade.order_id:
            exchange_service.cancel_order_safe(trade.order_id, trade.symbol, job=trade.job)

        final_order = exchange_service.fetch_order_status(trade.order_id, trade.symbol, job=trade.job) or {}
        filled_amount = safe_decimal(final_order.get('filled'), 0)
        filled_cost = safe_decimal(final_order.get('cost'), 0)

        if filled_amount > 0:
            trade.amount_received = filled_amount
            trade.amount_spent = filled_cost
            trade.purchase_price = safe_decimal(final_order.get('average') or final_order.get('price'), trade.purchase_price)
            trade.fee_incurred = safe_decimal((final_order.get('fee') or {}).get('cost'), 0)
            trade.status = 'completed'
            msg = f"Order was partially filled ({trade.amount_received} {trade.symbol}) before cancellation."
        else:
            trade.status = 'canceled'
            msg = f"Order for {trade.symbol} canceled successfully."

        trade.save()
        exchange_service.log(f"Manual cancel executed for order {trade.order_id} ({trade.symbol}).", job=trade.job)
        return True, msg

    @classmethod
    def manual_replace_order(cls, trade: Trade, user):
        """
        Manually trigger the bot to check the latest bid price and replace the limit order.
        """
        if trade.user != user:
            return False, "Unauthorized access to trade."
        if trade.status != 'open':
            return False, "Order is not currently open."

        exchange_service = cls.get_exchange_service_for_trade(trade)
        if not exchange_service:
            return False, "Exchange account not available."

        new_trade = cls.cancel_and_replace_limit_order(trade, exchange_service=exchange_service)
        if new_trade:
            return True, f"Order successfully replaced at current bid (New Order ID: {new_trade.order_id})."
        return True, "Previous order canceled. (Remaining allocation was below exchange minimum)."

    @classmethod
    def manual_market_fallback(cls, trade: Trade, user):
        """
        Manually fallback and execute the remaining open allocation as an immediate market buy order.
        """
        if trade.user != user:
            return False, "Unauthorized access to trade."
        if trade.status != 'open':
            return False, "Order is not currently open."

        exchange_service = cls.get_exchange_service_for_trade(trade)
        if not exchange_service:
            return False, "Exchange account not available."

        # 1. Cancel existing limit order
        if trade.order_id:
            exchange_service.cancel_order_safe(trade.order_id, trade.symbol, job=trade.job)

        final_order = exchange_service.fetch_order_status(trade.order_id, trade.symbol, job=trade.job) or {}
        filled_amount = safe_decimal(final_order.get('filled'), 0)
        filled_cost = safe_decimal(final_order.get('cost'), 0)
        initial_allocation = trade.amount_spent

        if filled_amount > 0:
            trade.amount_received = filled_amount
            trade.amount_spent = filled_cost
            trade.purchase_price = safe_decimal(final_order.get('average') or final_order.get('price'), trade.purchase_price)
            trade.fee_incurred = safe_decimal((final_order.get('fee') or {}).get('cost'), 0)
            trade.status = 'completed'
            remaining_quote = initial_allocation - filled_cost
        else:
            trade.status = 'canceled'
            remaining_quote = initial_allocation

        trade.save()

        # 2. Execute market order for remaining allocation
        if remaining_quote > Decimal('0.01'):
            quote_curr = trade.job.quote_currency if trade.job else (trade.symbol.split('/')[1] if '/' in trade.symbol else 'USD')
            try:
                market_order = exchange_service.place_market_buy_order(
                    trade.symbol,
                    float(remaining_quote),
                    job=trade.job,
                    quote_currency=quote_curr
                )
                m_trade = Trade.objects.create(
                    job=trade.job,
                    user=trade.user,
                    exchange_name=trade.exchange_name,
                    symbol=trade.symbol,
                    job_name=trade.job_name,
                    order_type='market',
                    amount_spent=safe_decimal(market_order.get('cost') or remaining_quote, remaining_quote),
                    amount_received=safe_decimal(market_order.get('amount') or market_order.get('filled'), 0),
                    purchase_price=safe_decimal(market_order.get('price') or market_order.get('average'), 0),
                    fee_incurred=safe_decimal((market_order.get('fee') or {}).get('cost'), 0),
                    order_id=str(market_order.get('id', '')),
                    status='completed'
                )
                exchange_service.log(f"Market fallback executed: Bought {m_trade.amount_received} {m_trade.symbol} for ${m_trade.amount_spent}", job=trade.job)
                return True, f"Market order executed successfully ({m_trade.amount_received} {m_trade.symbol})."
            except Exception as e:
                exchange_service.log(f"Market fallback order failed: {e}", level='ERROR', job=trade.job)
                return False, f"Market order failed: {str(e)}"

        return True, "Limit order canceled."
