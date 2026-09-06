from django.utils import timezone
from core.models import AutobuyJob, Trade
from .exchange_service import ExchangeService
from .notification_service import NotificationService
import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

class TradeExecutor:
    def execute_job(self, job_id):
        try:
            job = AutobuyJob.objects.get(id=job_id)
        except AutobuyJob.DoesNotExist:
            logger.error(f"Job {job_id} not found")
            return

        if not job.is_active:
            logger.info(f"Job {job.id} skipped (inactive)")
            return

        exchange_service = ExchangeService(job.account)
        exchange_service.log(f"Starting job execution: {job.name}", job=job)

        total_amount = job.total_amount
        created_trades = []
        
        for token in job.tokens.all():
            allocation = (token.percentage / 100) * total_amount
            raw_symbol = token.token_symbol.strip().upper()
            
            # Pre-trade validation against exchange markets
            is_valid, symbol, err_msg = exchange_service.validate_pair(raw_symbol, job.quote_currency)
            if not is_valid:
                error_desc = f"Pair validation failed: {err_msg}"
                exchange_service.log(f"Validation failed for pair {raw_symbol}: {err_msg}", level='ERROR', job=job)
                job.last_status = 'failure'
                job.last_error_message = error_desc
                job.is_active = False
                job.save(update_fields=['last_status', 'last_error_message', 'is_active'])
                NotificationService.send_trade_failed_email(job, error_desc, symbol=raw_symbol)
                return

            # Pre-trade minimum order size check
            is_valid_size, size_err = exchange_service.validate_order_size(symbol, allocation, job.quote_currency)
            if not is_valid_size:
                error_desc = f"Order size validation failed: {size_err}"
                exchange_service.log(f"Order size validation failed for {symbol}: {size_err}", level='ERROR', job=job)
                job.last_status = 'failure'
                job.last_error_message = error_desc
                job.is_active = False
                job.save(update_fields=['last_status', 'last_error_message', 'is_active'])
                NotificationService.send_trade_failed_email(job, error_desc, symbol=symbol)
                return
            
            try:
                order = exchange_service.place_market_buy_order(symbol, allocation, job=job, quote_currency=job.quote_currency)
                
                # Check if order result is minimal (e.g. missing status or cost/fee)
                if order and (not order.get('status') or order.get('status') == 'open' or not order.get('cost')):
                     try:
                         import time
                         time.sleep(1) 
                         fetched_order = exchange_service.exchange.fetch_order(order['id'], symbol)
                         if fetched_order:
                             order = fetched_order
                             exchange_service.log(f"Fetched full order details: {order.get('id')}", level='INFO', job=job)
                     except Exception as fetch_err:
                         exchange_service.log(f"Could not fetch full order details: {fetch_err}", level='WARNING', job=job)

                # Log full order details for debugging
                exchange_service.log(f"Order Details: {order}", level='INFO', job=job)
                
                # Create Trade record
                trade = Trade.objects.create(
                    job=job,
                    user=job.user,
                    exchange_name=job.account.exchange.name,
                    symbol=symbol,
                    job_name=job.name,  # Snapshot
                    order_type='market', # Currently always market
                    amount_spent=Decimal(order.get('cost') or allocation), # Use actual cost if available
                    amount_received=Decimal(order.get('amount', 0)), # Actual filled amount
                    purchase_price=Decimal(order.get('price', 0) or 0), # Average price
                    fee_incurred=Decimal((order.get('fee') or {}).get('cost', 0) or 0),
                    order_id=str(order.get('id', '')),
                    status='completed'
                )
                created_trades.append(trade)
                
            except Exception as e:
                error_desc = f"Failed to buy {symbol}: {str(e)}"
                exchange_service.log(error_desc, level='ERROR', job=job)
                job.last_status = 'failure'
                job.last_error_message = error_desc
                job.is_active = False # Deactivate on error
                job.save(update_fields=['last_status', 'last_error_message', 'is_active'])
                NotificationService.send_trade_failed_email(job, error_desc, symbol=symbol)
                return # Stop execution immediately on error

        # Update job timing
        job.last_run = timezone.now()
        
        # Determine status if not already set to failure by the loop
        if job.last_status != 'failure':
             job.last_status = 'success'
             job.last_error_message = ''
        
        # Calculate next scheduled run preserving original start_time time of day
        job.next_run = job.calculate_next_run(after_time=job.last_run)
        
        # Check for expiration
        if job.end_date and (not job.next_run or job.next_run > job.end_date):
             job.is_active = False
             job.next_run = None
             job.last_status = 'warning' 
             job.last_error_message = "Job finished: End date reached."
             exchange_service.log(f"Job reached end date {job.end_date}. Deactivating.", level='WARNING', job=job)

        job.save()
        
        # Send Success Email Notification
        if created_trades:
            NotificationService.send_trade_success_email(job, created_trades)
            
        exchange_service.log(f"Job finished. Next run at {job.next_run}", job=job)

