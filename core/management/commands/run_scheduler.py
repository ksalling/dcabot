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
