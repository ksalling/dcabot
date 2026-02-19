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
                
                # Create Trade record
                Trade.objects.create(
                    job=job,
                    user=job.user,
                    exchange_name=job.account.exchange.name,
                    symbol=symbol,
                    amount_spent=allocation, # Approximation based on what we wanted to spend
                    amount_received=Decimal(order.get('amount', 0)), # Actual filled amount
                    purchase_price=Decimal(order.get('price', 0) or 0), # Average price
                    fee_incurred=Decimal(order.get('fee', {}).get('cost', 0) or 0),
                    order_id=str(order.get('id', '')),
                    status='completed'
                )
            except Exception as e:
                exchange_service.log(f"Failed to buy {symbol}: {e}", level='ERROR', job=job)

        # Update job timing
        job.last_run = timezone.now()
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
        job.save()
        
        exchange_service.log(f"Job finished. Next run at {job.next_run}", job=job)
