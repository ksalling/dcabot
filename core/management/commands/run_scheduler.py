from django.conf import settings
from django.core.management.base import BaseCommand
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from django_apscheduler.jobstores import DjangoJobStore
from django_apscheduler.models import DjangoJobExecution
from django_apscheduler import util
from core.models import AutobuyJob
from core.services.trade_executor import TradeExecutor
from django.utils import timezone
import logging

from core.services.notification_service import NotificationService
from core.models import JobLog

logger = logging.getLogger(__name__)

def check_and_run_jobs():
    """
    Check for jobs that are due and run them.
    Also checks for paused jobs that were due and records a skip notice & alert.
    """
    now = timezone.now()
    
    # 1. Grab jobs that are active and due
    active_jobs = AutobuyJob.objects.filter(is_active=True, next_run__lte=now)
    for job in active_jobs:
        logger.info(f"Triggering active job {job.id} ({job.name})")
        executor = TradeExecutor()
        try:
            executor.execute_job(job.id)
        except Exception as e:
            logger.error(f"Error executing job {job.id}: {e}")

    # 2. Check for paused/inactive jobs that were scheduled and due
    paused_due_jobs = AutobuyJob.objects.filter(is_active=False, next_run__isnull=False, next_run__lte=now)
    for job in paused_due_jobs:
        logger.info(f"Job {job.id} ({job.name}) is due but paused. Skipping run and rolling next_run forward.")
        skip_msg = f"Scheduled trade was skipped because job '{job.name}' is currently paused."
        job.last_status = 'warning'
        job.last_error_message = skip_msg
        job.next_run = job.calculate_next_run(after_time=now)
        job.save(update_fields=['last_status', 'last_error_message', 'next_run'])
        
        JobLog.objects.create(
            job=job,
            user=job.user,
            level='WARNING',
            message=skip_msg
        )
        
        NotificationService.send_trade_skipped_paused_email(job)

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
