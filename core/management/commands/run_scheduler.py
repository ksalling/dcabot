from django.conf import settings
from django.core.management.base import BaseCommand
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from django_apscheduler.jobstores import DjangoJobStore
from django_apscheduler.models import DjangoJobExecution
from django_apscheduler import util
from core.models import AutobuyJob, ExchangeAccount, UserProfile, AppSettings
from django.core.mail import send_mail
from core.services.trade_executor import TradeExecutor
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)

def check_and_run_jobs():
    """
    Check for jobs that are due and run them.
    This simple implementation just checks DB for due jobs.
    In a more complex setup, we might schedule each job individually in APScheduler.
    But for dynamic user jobs, polling DB every minute is often simpler and robust.
    """
    now = timezone.now()
    # Find active jobs where next_run <= now OR next_run is null (first run)
    # But usually creating a job sets next_run.
    
    # We grab jobs that are active and due
    jobs = AutobuyJob.objects.filter(is_active=True, next_run__lte=now)
    
    for job in jobs:
        logger.info(f"Triggering job {job.id}")
        executor = TradeExecutor()
        # We might want to run this async/threaded if many jobs?
        # For now, blocking in this loop is okay if few jobs.
        try:
            executor.execute_job(job.id)
        except Exception as e:
            logger.error(f"Error executing job {job.id}: {e}")

def enforce_subscription_limits():
    """
    Check for users whose subscriptions have expired or are inactive.
    Disable their active accounts and jobs and notify them via email.
    """
    profiles = UserProfile.objects.all()
    app_settings = AppSettings.load()
    default_from = app_settings.default_from_email or 'noreply@example.com'
    
    for profile in profiles:
        if not profile.has_access:
            user = profile.user
            # Find active items
            active_jobs = AutobuyJob.objects.filter(user=user, is_active=True)
            active_accounts = ExchangeAccount.objects.filter(user=user, is_active=True)
            
            jobs_count = active_jobs.count()
            accounts_count = active_accounts.count()
            
            if jobs_count > 0 or accounts_count > 0:
                # Deactivate
                active_jobs.update(is_active=False, last_status='warning', last_error_message='Subscription inactive')
                active_accounts.update(is_active=False)
                
                logger.info(f"Disabled {jobs_count} jobs and {accounts_count} accounts for user {user.username} due to inactive subscription.")
                
                # Send email notification
                if user.email:
                    subject = "Action Required: Moondrip Pro Features Disabled"
                    message = (
                        f"Hello {user.first_name or user.username},\n\n"
                        "Your Moondrip Pro subscription has expired or is inactive. "
                        f"As a result, we have automatically disabled {jobs_count} active job(s) and {accounts_count} active exchange connection(s).\n\n"
                        "To resume your automated trading workflows and re-enable your accounts, please log in and upgrade to Pro or link a valid referral account.\n\n"
                        "Best,\nThe Moondrip Team"
                    )
                    try:
                        send_mail(
                            subject,
                            message,
                            default_from,
                            [user.email],
                            fail_silently=True,
                        )
                    except Exception as e:
                        logger.error(f"Failed to send deactivation email to {user.email}: {e}")

@util.close_old_connections
def delete_old_job_executions(max_age=604_800):
    """
    This job deletes APScheduler job execution entries older than `max_age` from the database.
    It helps to prevent the database from filling up with old historical records that are no
    longer useful.
    
    :param max_age: The maximum length of time to retain historical job execution records.
                    Defaults to 7 days.
    """
    DjangoJobExecution.objects.delete_old_job_executions(max_age)

class Command(BaseCommand):
    help = "Runs APScheduler."

    def handle(self, *args, **options):
        scheduler = BlockingScheduler(timezone=settings.TIME_ZONE)
        scheduler.add_jobstore(DjangoJobStore(), "default")

        # Schedule the "check_and_run_jobs" to run every minute
        scheduler.add_job(
            check_and_run_jobs,
            trigger=CronTrigger(second="0"),  # Every minute at :00
            id="check_and_run_jobs",
            max_instances=1,
            replace_existing=True,
        )
        logger.info("Added job 'check_and_run_jobs'.")

        # Schedule the subscription enforcer to run every 5 minutes
        scheduler.add_job(
            enforce_subscription_limits,
            trigger=CronTrigger(minute="*/5"),
            id="enforce_subscription_limits",
            max_instances=1,
            replace_existing=True,
        )
        logger.info("Added job 'enforce_subscription_limits'.")

        scheduler.add_job(
            delete_old_job_executions,
            trigger=CronTrigger(
                day_of_week="mon", hour="00", minute="00"
            ),  # Midnight on Monday, before start of the next work week.
            id="delete_old_job_executions",
            max_instances=1,
            replace_existing=True,
        )
        logger.info(
            "Added weekly job: 'delete_old_job_executions'."
        )

        try:
            logger.info("Starting scheduler...")
            scheduler.start()
        except KeyboardInterrupt:
            logger.info("Stopping scheduler...")
            scheduler.shutdown()
            logger.info("Scheduler shut down successfully!")
