import logging
from django.core.mail import send_mail
from django.conf import settings

logger = logging.getLogger(__name__)

class NotificationService:
    @staticmethod
    def is_notification_enabled(user, notification_type):
        """
        Check if user has opted into this specific email notification type.
        """
        if not user or not user.email:
            return False
            
        profile = getattr(user, 'userprofile', None)
        if not profile:
            return True
            
        return getattr(profile, notification_type, True)

    @classmethod
    def send_trade_success_email(cls, job, trades):
        """
        Send an email notification when a trade executes successfully.
        """
        try:
            if not cls.is_notification_enabled(job.user, 'notify_trade_success'):
                logger.info(f"Skipping success email for {job.user.username} (notification disabled)")
                return

            purchases_lines = []
            total_spent = sum(t.amount_spent for t in trades)
            for t in trades:
                purchases_lines.append(
                    f"  • {t.amount_received:.8f} {t.symbol} at ${t.purchase_price:.2f} "
                    f"(Spent: ${t.amount_spent:.2f}, Fee: ${t.fee_incurred:.4f})"
                )
            
            purchases_text = "\n".join(purchases_lines)
            next_run_str = job.next_run.strftime('%Y-%m-%d %H:%M UTC') if job.next_run else 'Not scheduled'
            
            subject = f"Moondrip - Trade Executed: {job.name}"
            body = (
                f"Hello {job.user.first_name or job.user.username},\n\n"
                f"Your DCA job \"{job.name}\" executed successfully on {job.account.exchange.name}.\n\n"
                f"Purchases Made:\n"
                f"{purchases_text}\n\n"
                f"Total Amount Spent: ${total_spent:.2f} {job.quote_currency}\n"
                f"Next Scheduled Run: {next_run_str}\n\n"
                f"You can view your active portfolio and recent trade history in your dashboard.\n\n"
                f"— The Moondrip Team"
            )

            send_mail(
                subject=subject,
                message=body,
                from_email=None,
                recipient_list=[job.user.email],
                fail_silently=False
            )
            logger.info(f"Sent trade success email to {job.user.email} for job {job.name}")
        except Exception as e:
            logger.error(f"Failed to send trade success email for job {job.id}: {e}")

    @classmethod
    def send_trade_failed_email(cls, job, error_message, symbol=None):
        """
        Send an email notification when a trade fails to execute.
        """
        try:
            if not cls.is_notification_enabled(job.user, 'notify_trade_failed'):
                logger.info(f"Skipping failure email for {job.user.username} (notification disabled)")
                return

            subject = f"Moondrip Alert - Trade Failed: {job.name}"
            symbol_str = f" for {symbol}" if symbol else ""
            
            body = (
                f"Hello {job.user.first_name or job.user.username},\n\n"
                f"Your DCA job \"{job.name}\" failed to execute{symbol_str} on {job.account.exchange.name}.\n\n"
                f"Reason for Failure:\n"
                f"  {error_message}\n\n"
                f"To protect your account, this job has been temporarily paused.\n\n"
                f"Troubleshooting Steps:\n"
                f"1. Check that your {job.account.exchange.name} account has sufficient {job.quote_currency} balance.\n"
                f"2. Verify that your API keys have trading permissions enabled and have not expired.\n"
                f"3. Ensure the allocation amount meets the exchange's minimum order size requirements.\n\n"
                f"Once resolved, you can resume the job directly from your Moondrip dashboard.\n\n"
                f"— The Moondrip Team"
            )

            send_mail(
                subject=subject,
                message=body,
                from_email=None,
                recipient_list=[job.user.email],
                fail_silently=False
            )
            logger.info(f"Sent trade failure email to {job.user.email} for job {job.name}")
        except Exception as e:
            logger.error(f"Failed to send trade failure email for job {job.id}: {e}")

    @classmethod
    def send_trade_skipped_paused_email(cls, job):
        """
        Send an email notification when a scheduled trade is skipped because the job is paused.
        """
        try:
            if not cls.is_notification_enabled(job.user, 'notify_trade_skipped_paused'):
                logger.info(f"Skipping paused job skip email for {job.user.username} (notification disabled)")
                return

            subject = f"Moondrip Notice - Scheduled Trade Skipped: {job.name}"
            next_run_str = job.next_run.strftime('%Y-%m-%d %H:%M UTC') if job.next_run else 'Not scheduled'
            
            body = (
                f"Hello {job.user.first_name or job.user.username},\n\n"
                f"A scheduled trade for your DCA job \"{job.name}\" was skipped because the job is currently paused.\n\n"
                f"If you wish to continue automated trading with this job, simply log in to your Moondrip dashboard and click 'Resume Job'.\n\n"
                f"Next Projected Schedule: {next_run_str}\n\n"
                f"— The Moondrip Team"
            )

            send_mail(
                subject=subject,
                message=body,
                from_email=None,
                recipient_list=[job.user.email],
                fail_silently=False
            )
            logger.info(f"Sent trade skipped (paused) email to {job.user.email} for job {job.name}")
        except Exception as e:
            logger.error(f"Failed to send trade skipped email for job {job.id}: {e}")
