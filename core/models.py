from django.db import models
from django.conf import settings
from .fields import EncryptedCharField
from django.utils.translation import gettext_lazy as _

class SupportedExchange(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    is_enabled = models.BooleanField(default=True)

    def __str__(self):
        return self.name

class ExchangeAccount(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='exchange_accounts')
    exchange = models.ForeignKey(SupportedExchange, on_delete=models.PROTECT)
    nickname = models.CharField(max_length=100, blank=True)
    
    # Encrypted fields
    api_key = EncryptedCharField(max_length=255)
    api_secret = EncryptedCharField(max_length=255)
    api_passphrase = EncryptedCharField(max_length=255, blank=True, null=True)
    
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.nickname or self.exchange.name} ({self.user.username})"

class AutobuyJob(models.Model):
    INTERVAL_CHOICES = [
        ('hourly', _('Hourly')),
        ('daily', _('Daily')),
        ('weekly', _('Weekly')),
        ('monthly', _('Monthly')),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='autobuy_jobs')
    account = models.ForeignKey(ExchangeAccount, on_delete=models.CASCADE, related_name='jobs')
    
    name = models.CharField(max_length=100, default="Moondrip Job")
    total_amount = models.DecimalField(max_digits=20, decimal_places=2, help_text=_("Total amount to spend per run (in quote currency, e.g. USDT)"))
    
    QUOTE_CURRENCIES = [
        ('USDT', 'USDT'),
        ('USDC', 'USDC'),
        ('USD', 'USD'),
    ]
    quote_currency = models.CharField(max_length=10, choices=QUOTE_CURRENCIES, default="USDT")
    
    interval = models.CharField(max_length=20, choices=INTERVAL_CHOICES, default='daily')
    
    is_active = models.BooleanField(default=False)
    
    start_time = models.DateTimeField()
    end_date = models.DateTimeField(null=True, blank=True, help_text=_("Optional end date to stop the job"))
    last_run = models.DateTimeField(null=True, blank=True)
    last_run = models.DateTimeField(null=True, blank=True)
    next_run = models.DateTimeField(null=True, blank=True)
    
    STATUS_CHOICES = [
        ('success', 'Success'),
        ('warning', 'Warning'),
        ('failure', 'Failure'),
    ]
    last_status = models.CharField(max_length=20, choices=STATUS_CHOICES, blank=True, null=True)
    last_error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} - {self.get_interval_display()}"

class JobToken(models.Model):
    job = models.ForeignKey(AutobuyJob, on_delete=models.CASCADE, related_name='tokens')
    token_symbol = models.CharField(max_length=20, help_text=_("e.g. BTC"))
    percentage = models.DecimalField(max_digits=5, decimal_places=2, help_text=_("Percentage of total amount (0-100)"))

    def __str__(self):
        return f"{self.percentage}% allocation to {self.token_symbol}"

class Trade(models.Model):
    job = models.ForeignKey(AutobuyJob, on_delete=models.SET_NULL, null=True, related_name='trades')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='trades')
    
    exchange_name = models.CharField(max_length=100)
    symbol = models.CharField(max_length=20) # e.g. BTC/USDT
    job_name = models.CharField(max_length=100, default="Unknown Job", help_text="Snapshot of job name at time of trade")
    order_type = models.CharField(max_length=20, default='market')
    
    amount_spent = models.DecimalField(max_digits=20, decimal_places=8)
    amount_received = models.DecimalField(max_digits=20, decimal_places=8)
    purchase_price = models.DecimalField(max_digits=20, decimal_places=8)
    fee_incurred = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    
    order_id = models.CharField(max_length=100, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    status = models.CharField(max_length=20, default='completed')

    def __str__(self):
        return f"Bought {self.amount_received} {self.symbol} on {self.timestamp}"

class JobLog(models.Model):
    LEVEL_CHOICES = [
        ('INFO', 'Info'),
        ('WARNING', 'Warning'),
        ('ERROR', 'Error'),
    ]
    
    job = models.ForeignKey(AutobuyJob, on_delete=models.CASCADE, related_name='logs', null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='job_logs', null=True, blank=True)
    
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default='INFO')
    message = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"[{self.level}] {self.timestamp}: {self.message[:50]}"

class AppSettings(models.Model):
    """
    Singleton model to store application runtime settings, specifically email config.
    """
    smtp_host = models.CharField(max_length=255, default='smtp.sendgrid.net')
    smtp_port = models.IntegerField(default=587)
    smtp_user = models.CharField(max_length=255, blank=True)
    smtp_password = EncryptedCharField(max_length=255, blank=True)
    use_tls = models.BooleanField(default=True)
    default_from_email = models.CharField(max_length=255, default='noreply@example.com')
    
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1 # Force singleton
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Application Settings"

    class Meta:
        verbose_name = "Application Settings"
        verbose_name_plural = "Application Settings"

class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='userprofile')
    
    # Polar.sh fields
    polar_customer_id = models.CharField(max_length=255, blank=True)
    polar_subscription_id = models.CharField(max_length=255, blank=True)
    
    # Subscription Status
    # 'active', 'inactive', 'canceled', 'past_due'
    subscription_status = models.CharField(max_length=50, default='inactive')
    
    # When the current period ends (for showing next billing date)
    current_period_end = models.DateTimeField(null=True, blank=True)
    
    # Admin Override to grant access without subscription
    manual_access_granted = models.BooleanField(default=False, help_text=_("Grant full access without subscription"))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile for {self.user.username}"

    @property
    def has_access(self):
        """
        Check if user has access to premium features (Jobs).
        """
        # Admin override or Active Subscription
        if self.manual_access_granted:
            return True
            
        return self.subscription_status == 'active'

# Signals to create/update UserProfile automatically
from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)

@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def save_user_profile(sender, instance, **kwargs):
    # Ensure profile exists incase it was deleted or user created before signals
    if not hasattr(instance, 'userprofile'):
        UserProfile.objects.create(user=instance)
    else:
        instance.userprofile.save()
