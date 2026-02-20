from django.contrib import admin
from .models import SupportedExchange, ExchangeAccount, AutobuyJob, JobToken, Trade, JobLog, UserProfile

@admin.register(SupportedExchange)
class SupportedExchangeAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'is_enabled')
    list_editable = ('is_enabled',)

@admin.register(ExchangeAccount)
class ExchangeAccountAdmin(admin.ModelAdmin):
    list_display = ('nickname', 'exchange', 'user', 'is_active')
    list_filter = ('exchange', 'is_active')
    search_fields = ('nickname', 'user__username', 'api_key')

class JobTokenInline(admin.TabularInline):
    model = JobToken
    extra = 1

@admin.register(AutobuyJob)
class AutobuyJobAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'account', 'total_amount', 'interval', 'is_active', 'next_run')
    list_filter = ('interval', 'is_active')
    inlines = [JobTokenInline]

@admin.register(Trade)
class TradeAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'symbol', 'amount_received', 'purchase_price', 'status', 'user')
    list_filter = ('status', 'exchange_name')
    date_hierarchy = 'timestamp'

@admin.register(JobLog)
class JobLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'level', 'job', 'message')
    list_filter = ('level',)
    date_hierarchy = 'timestamp'

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'subscription_status', 'manual_access_granted', 'current_period_end')
    list_filter = ('subscription_status', 'manual_access_granted')
    search_fields = ('user__username', 'user__email', 'polar_customer_id')
