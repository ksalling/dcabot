from django.utils import timezone
from core.models import AutobuyJob, Trade
from .exchange_service import ExchangeService
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
        
        for token in job.tokens.all():
            allocation = (token.percentage / 100) * total_amount
            symbol = token.token_symbol # e.g. BTC/USDT
            
            # Simple check if symbol ends with quote currency? 
            # Or just trust user input. 
            # Assuming symbol is correct pair for now.
            
            try:
                order = exchange_service.place_market_buy_order(symbol, allocation, job=job)
                
                # Check if order result is minimal (e.g. missing status or cost/fee)
                # Some exchanges only return ID on creation.
                if order and (not order.get('status') or order.get('status') == 'open' or not order.get('cost')):
                     try:
                         # Wait briefly for propagation
                         import time
                         time.sleep(1) 
                         # Fetch full order details
                         fetched_order = exchange_service.exchange.fetch_order(order['id'], symbol)
                         if fetched_order:
                             order = fetched_order
                             exchange_service.log(f"Fetched full order details: {order.get('id')}", level='INFO', job=job)
                     except Exception as fetch_err:
                         exchange_service.log(f"Could not fetch full order details: {fetch_err}", level='WARNING', job=job)

                # Log full order details for debugging
                exchange_service.log(f"Order Details: {order}", level='INFO', job=job)
                
                # Create Trade record
                Trade.objects.create(
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
                # If we get here, at least one trade worked.
                # However, if we loop through multiple tokens, one might fail.
                # For now, if any token processing fails, we catch it below.
                # If all succeed, we set success.
                
            except Exception as e:
                exchange_service.log(f"Failed to buy {symbol}: {e}", level='ERROR', job=job)
                job.last_status = 'failure'
                job.last_error_message = f"Failed to buy {symbol}: {str(e)}"
                job.is_active = False # Deactivate on error
                job.save(update_fields=['last_status', 'last_error_message', 'is_active'])
                return # Stop execution immediately on error

        # Update job timing
        job.last_run = timezone.now()
        
        # Determine status if not already set to failure by the loop
        if job.last_status != 'failure':
             job.last_status = 'success'
             job.last_error_message = ''
        
        # Calculate next run based on interval
        from datetime import timedelta
        if job.interval == 'hourly':
            delta = timedelta(hours=1)
        elif job.interval == 'daily':
            delta = timedelta(days=1)
        elif job.interval == 'weekly':
            delta = timedelta(weeks=1)
        elif job.interval == 'monthly':
            delta = timedelta(days=30) # Approximation
        else:
            delta = timedelta(days=1)
            
        job.next_run = job.last_run + delta
        
        # Check for expiration
        if job.end_date and job.next_run > job.end_date:
             job.is_active = False
             job.next_run = None
             job.last_status = 'warning' 
             job.last_error_message = "Job finished: End date reached."
             exchange_service.log(f"Job reached end date {job.end_date}. Deactivating.", level='WARNING', job=job)

        job.save()
        
        exchange_service.log(f"Job finished. Next run at {job.next_run}", job=job)
