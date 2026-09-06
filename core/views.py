import ccxt
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, TemplateView, RedirectView
from django.urls import reverse_lazy, reverse
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from .models import AutobuyJob, ExchangeAccount, JobLog, Trade, JobToken, SupportedExchange, AppSettings
from .forms import AutobuyJobForm, ExchangeAccountForm, UserProfileForm, ExchangeAccountEditForm, AppSettingsForm, EmailUserCreationForm, JobTokenForm
from django.forms import inlineformset_factory
from django.contrib.auth.views import PasswordChangeView

class ProfileView(LoginRequiredMixin, UpdateView):
    model = User
    form_class = UserProfileForm
    template_name = 'core/profile.html'
    success_url = reverse_lazy('profile')

    def get_object(self):
        return self.request.user

    def form_valid(self, form):
        messages.success(self.request, "Profile updated successfully.")
        return super().form_valid(form)

class CustomPasswordChangeView(LoginRequiredMixin, PasswordChangeView):
    template_name = 'core/password_change_form.html'
    success_url = reverse_lazy('profile')
    
    def form_valid(self, form):
        messages.success(self.request, "Password changed successfully.")
        return super().form_valid(form)

def get_dashboard_context(user):
    from .services.portfolio import PortfolioService
    
    # Portfolio Calculation
    portfolio_service = PortfolioService(user)
    portfolio_data = portfolio_service.get_portfolio_summary()

    # Data Fetching
    jobs = AutobuyJob.objects.filter(user=user).order_by('-created_at')
    accounts = ExchangeAccount.objects.filter(user=user)
    recent_trades_qs = Trade.objects.filter(user=user).order_by('-timestamp')[:10] # Show 10
    
    price_map = {h['symbol']: h['current_price'] for h in portfolio_data.get('holdings', [])}
    
    recent_trades = []
    for trade in recent_trades_qs:
        quote_currency = trade.symbol.split('/')[1] if '/' in trade.symbol else 'USD'
        current_price = price_map.get(trade.symbol, trade.purchase_price)
        current_value = (trade.amount_received * current_price) if current_price else trade.amount_spent
        pnl = current_value - trade.amount_spent - trade.fee_incurred
        pnl_percent = (pnl / trade.amount_spent) * 100 if trade.amount_spent > 0 else 0
        
        recent_trades.append({
            'id': trade.id,
            'timestamp': trade.timestamp,
            'symbol': trade.symbol,
            'quote_currency': quote_currency,
            'purchase_price': trade.purchase_price,
            'amount_received': trade.amount_received,
            'amount_spent': trade.amount_spent,
            'current_price': current_price,
            'current_value': current_value,
            'pnl': pnl,
            'pnl_percent': pnl_percent,
        })
    
    return {
        'jobs': jobs,
        'accounts': accounts,
        'recent_trades': recent_trades,
        'portfolio': portfolio_data
    }

class DashboardView(LoginRequiredMixin, View):
    def get(self, request):
        context = get_dashboard_context(request.user)
        if request.headers.get('HX-Request'):
            return render(request, 'core/partials/dashboard_live_content.html', context)
        return render(request, 'core/dashboard.html', context)

class JobCreateView(LoginRequiredMixin, CreateView):
    model = AutobuyJob
    form_class = AutobuyJobForm
    template_name = 'core/job_form.html'
    success_url = reverse_lazy('dashboard')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user # Pass user to form __init__
        return kwargs

    def get_context_data(self, **kwargs):
        data = super().get_context_data(**kwargs)
        JobTokenFormSet = inlineformset_factory(AutobuyJob, JobToken, form=JobTokenForm, fields=('token_symbol', 'percentage'), extra=1, can_delete=False)
        if self.request.POST:
            data['tokens'] = JobTokenFormSet(self.request.POST)
        else:
            data['tokens'] = JobTokenFormSet()
        return data

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
            
        # Check Subscription
        if not hasattr(request.user, 'userprofile'):
            from .models import UserProfile
            UserProfile.objects.create(user=request.user)
            
        if not request.user.userprofile.has_access:
            messages.warning(request, "You must have an active subscription to create new jobs.")
            return redirect('subscription')
            
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        context = self.get_context_data()
        tokens = context['tokens']
        if form.is_valid() and tokens.is_valid():
            validation_errors = []

            # 1. Validate Total Percentage = 100%
            total_percentage = 0
            for token_form in tokens:
                if token_form.cleaned_data and not token_form.cleaned_data.get('DELETE', False):
                    total_percentage += token_form.cleaned_data.get('percentage', 0)
            
            if total_percentage != 100:
                validation_errors.append(f"Total percentage must be 100%. Current total: {total_percentage}%.")

            # 2. Exchange Validation
            self.object = form.save(commit=False)
            self.object.user = self.request.user
            self.object.is_active = True # Active by default on create
            
            from .services.exchange_service import ExchangeService
            service = ExchangeService(self.object.account)
            
            # Check Funds (Soft Validation)
            has_funds, msg = service.validate_job_funds(self.object.total_amount, self.object.quote_currency)
            if not has_funds:
                self.object.last_status = 'warning'
                self.object.last_error_message = msg
                messages.warning(self.request, f"Warning: {msg} The job has been created but may fail if funds are not added.")
            else:
                self.object.last_status = 'success'
                self.object.last_error_message = ''
            
            # Check All Pairs & Minimum Order Sizes (Hard Validation)
            for token_form in tokens:
                if token_form.cleaned_data and not token_form.cleaned_data.get('DELETE', False):
                    raw_symbol = token_form.cleaned_data.get('token_symbol')
                    if not raw_symbol:
                        validation_errors.append("Token symbol cannot be empty.")
                        continue
                        
                    is_valid, std_symbol, pair_msg = service.validate_pair(raw_symbol, self.object.quote_currency)
                    if not is_valid:
                        validation_errors.append(pair_msg)
                    else:
                        token_form.instance.token_symbol = std_symbol
                        # Validate Minimum Order Size
                        percentage = float(token_form.cleaned_data.get('percentage', 0))
                        allocation = (percentage / 100.0) * float(self.object.total_amount)
                        is_valid_size, size_err = service.validate_order_size(std_symbol, allocation, self.object.quote_currency)
                        if not is_valid_size:
                            validation_errors.append(size_err)

            if validation_errors:
                for err in validation_errors:
                    messages.error(self.request, err)
                return self.render_to_response(self.get_context_data(form=form))

            self.object = form.save(commit=False)
            self.object.is_active = True
            self.object.next_run = self.object.calculate_next_run()
            self.object.save()
            tokens.instance = self.object
            tokens.save()
            messages.success(self.request, "Job created successfully!")
            return redirect(self.success_url)
        else:
            return self.render_to_response(self.get_context_data(form=form))

class AccountCreateView(LoginRequiredMixin, CreateView):
    model = ExchangeAccount
    form_class = ExchangeAccountForm
    template_name = 'core/account_form.html'
    success_url = reverse_lazy('dashboard')

    def form_valid(self, form):
        form.instance.user = self.request.user
        messages.success(self.request, "Account connected!")
        return super().form_valid(form)

class AccountUpdateView(LoginRequiredMixin, UpdateView):
    model = ExchangeAccount
    form_class = ExchangeAccountEditForm
    template_name = 'core/account_edit.html'
    context_object_name = 'account'
    success_url = reverse_lazy('dashboard')
    
    def get_queryset(self):
        # Ensure user can only edit their own accounts
        return ExchangeAccount.objects.filter(user=self.request.user)

    def form_valid(self, form):
        messages.success(self.request, "Account updated successfully.")
        return super().form_valid(form)

class AccountDeleteView(LoginRequiredMixin, DeleteView):
    model = ExchangeAccount
    success_url = reverse_lazy('dashboard')
    
    def get_queryset(self):
        return ExchangeAccount.objects.filter(user=self.request.user)
    
    def form_valid(self, form):
        messages.success(self.request, "Account disconnected successfully.")
        return super().form_valid(form)

class JobToggleView(LoginRequiredMixin, View):
    def post(self, request, pk):
        job = get_object_or_404(AutobuyJob, pk=pk, user=request.user)
        
        # If we are activating the job (currently inactive)
        if not job.is_active:
            # Check Subscription before activating
            if not request.user.userprofile.has_access:
                messages.warning(request, "You must have an active subscription to activate jobs.")
                 # If HTMX, return error message or redirect? 
                 # For simplicity, if HTMX, maybe just return button as disabled or alert?
                 # But we are in a POST.
                if request.headers.get('HX-Request'):
                    response = render(request, 'core/partials/job_card.html', {'job': job})
                    # We can use hx-trigger to show toast?
                    response['HX-Trigger'] = '{"showMessage": "Subscription Required"}' 
                    # Assuming we have toast logic, if not just return job as is
                    return response
                return redirect('subscription')

            # 1. Clear previous alerts
            job.last_status = None
            job.last_error_message = ""
            
            # 2. Re-validate funds
            from .services.exchange_service import ExchangeService
            service = ExchangeService(job.account)
            has_funds, msg = service.validate_job_funds(job.total_amount, job.quote_currency)
            
            if not has_funds:
                job.last_status = 'warning'
                job.last_error_message = msg
                # Do NOT activate. Keep is_active = False.
                # Force save to store the warning
                job.save(update_fields=['last_status', 'last_error_message'])
            else:
                job.last_status = 'success'
                # Only activate if funds are okay
                job.is_active = True
                
                from django.utils import timezone
                if not job.next_run or job.next_run <= timezone.now():
                    job.next_run = job.calculate_next_run()
                    
                job.save()
        else:
             # Deactivating
             job.is_active = False
             job.save()

        if request.headers.get('HX-Request'):
             # Return the *updated* job card partial to swap outerHTML
             return render(request, 'core/partials/job_card.html', {'job': job})
             
        return redirect('dashboard')

class AccountToggleView(LoginRequiredMixin, View):
    def post(self, request, pk):
        account = get_object_or_404(ExchangeAccount, pk=pk, user=request.user)
        
        # Prevent enabling if exchange is disabled by admin
        if not account.is_active and not account.exchange.is_enabled:
             messages.error(request, f"Cannot enable account. {account.exchange.name} is currently disabled by the administrator.")
             # If HTMX, we might want to return a specific partial or just the button with error? 
             # For now, let's just return the button state (still disabled) + OOB message if possible?
             # Or just render the button as is.
             if request.headers.get('HX-Request'):
                 return render(request, 'core/partials/account_toggle_button.html', {'account': account})
             return redirect('dashboard')

        account.is_active = not account.is_active
        account.save()
        
        if request.headers.get('HX-Request'):
             return render(request, 'core/partials/account_toggle_button.html', {'account': account})
        return redirect('dashboard')

class JobRunNowView(LoginRequiredMixin, View):
    def post(self, request, pk):
        job = get_object_or_404(AutobuyJob, pk=pk, user=request.user)
        from .services.trade_executor import TradeExecutor
        executor = TradeExecutor()
        executor.execute_job(job.pk)
        messages.success(request, f"Job {job.name} triggered manually.")
        if request.headers.get('HX-Request'):
            context = get_dashboard_context(request.user)
            return render(request, 'core/partials/dashboard_live_content.html', context)
        return redirect('dashboard')

class ManageExchangesView(LoginRequiredMixin, View):
    def get(self, request):
        if not request.user.is_staff:
            messages.error(request, "You do not have permission to access this page.")
            return redirect('dashboard')
            
        all_exchanges = ccxt.exchanges
        supported_exchanges = SupportedExchange.objects.all()
        supported_map = {e.slug: e for e in supported_exchanges}
        
        exchange_list = []
        for slug in all_exchanges:
            is_enabled = False
            if slug in supported_map and supported_map[slug].is_enabled:
                is_enabled = True
            
            exchange_list.append({
                'slug': slug,
                'name': slug.capitalize(),
                'is_enabled': is_enabled
            })
            
        context = {
            'exchanges': exchange_list
        }
        return render(request, 'core/manage_exchanges.html', context)

    def post(self, request):
        if not request.user.is_staff:
            return HttpResponse(status=403)
            
        slug = request.POST.get('slug')
        action = request.POST.get('action')
        
        if not slug or slug not in ccxt.exchanges:
            messages.error(request, "Invalid exchange.")
            return redirect('manage_exchanges')
            
        if action == 'enable':
            obj, created = SupportedExchange.objects.get_or_create(slug=slug, defaults={'name': slug.capitalize()})
            obj.is_enabled = True
            obj.save()
            messages.success(request, f"{slug.capitalize()} enabled. NOTE: Users must manually re-enable their accounts and jobs.")
        elif action == 'disable':
            try:
                obj = SupportedExchange.objects.get(slug=slug)
                obj.is_enabled = False
                obj.save()
                
                # Cascade disable accounts
                affected_accounts = ExchangeAccount.objects.filter(exchange=obj, is_active=True)
                count_accounts = affected_accounts.update(is_active=False)
                
                # Cascade disable jobs (connected to this exchange)
                # Jobs are linked to Account. We can find jobs linked to this exchange via Account.
                # Note: We just disabled the accounts, so we can't filter by is_active=True on account anymore if we want to catch them all?
                # Actually, filtering by exchange is safer.
                affected_jobs = AutobuyJob.objects.filter(account__exchange=obj, is_active=True)
                count_jobs = affected_jobs.update(is_active=False)
                
                messages.success(request, f"{slug.capitalize()} disabled. {count_accounts} accounts and {count_jobs} jobs were automatically disabled.")
            except SupportedExchange.DoesNotExist:
                pass
        
        return redirect('manage_exchanges')

class JobUpdateView(LoginRequiredMixin, UpdateView):
    model = AutobuyJob
    form_class = AutobuyJobForm
    template_name = 'core/job_form.html'
    success_url = reverse_lazy('dashboard')
    context_object_name = 'job'

    def get_queryset(self):
        return AutobuyJob.objects.filter(user=self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        data = super().get_context_data(**kwargs)
        JobTokenFormSet = inlineformset_factory(AutobuyJob, JobToken, form=JobTokenForm, fields=('token_symbol', 'percentage'), extra=0, can_delete=True)
        if self.request.POST:
            data['tokens'] = JobTokenFormSet(self.request.POST, instance=self.object)
        else:
            data['tokens'] = JobTokenFormSet(instance=self.object)
        return data

    def form_valid(self, form):
        context = self.get_context_data()
        tokens = context['tokens']
        
        if form.is_valid() and tokens.is_valid():
            validation_errors = []

            # 1. Validate Total Percentage = 100%
            total_percentage = 0
            for token_form in tokens:
                if token_form.cleaned_data and not token_form.cleaned_data.get('DELETE', False):
                    total_percentage += token_form.cleaned_data.get('percentage', 0)
            
            if total_percentage != 100:
                validation_errors.append(f"Total percentage must be 100%. Current total: {total_percentage}%.")

            # 2. Exchange Validation
            job = form.save(commit=False)
            from .services.exchange_service import ExchangeService
            service = ExchangeService(job.account)
            
            # Check Funds (Soft Validation)
            has_funds, msg = service.validate_job_funds(job.total_amount, job.quote_currency)
            if not has_funds:
                job.last_status = 'warning'
                job.last_error_message = msg
                messages.warning(self.request, f"Warning: {msg} The job has been created but may fail if funds are not added.")
            else:
                job.last_status = 'success'
                job.last_error_message = ''
            
            # Check All Pairs & Minimum Order Sizes (Hard Validation)
            for token_form in tokens:
                if token_form.cleaned_data and not token_form.cleaned_data.get('DELETE', False):
                    raw_symbol = token_form.cleaned_data.get('token_symbol')
                    if not raw_symbol:
                        validation_errors.append("Token symbol cannot be empty.")
                        continue

                    is_valid, std_symbol, pair_msg = service.validate_pair(raw_symbol, job.quote_currency)
                    if not is_valid:
                        validation_errors.append(pair_msg)
                    else:
                        token_form.instance.token_symbol = std_symbol
                        # Validate Minimum Order Size
                        percentage = float(token_form.cleaned_data.get('percentage', 0))
                        allocation = (percentage / 100.0) * float(job.total_amount)
                        is_valid_size, size_err = service.validate_order_size(std_symbol, allocation, job.quote_currency)
                        if not is_valid_size:
                            validation_errors.append(size_err)

            if validation_errors:
                for err in validation_errors:
                    messages.error(self.request, err)
                return self.render_to_response(self.get_context_data(form=form))

            self.object = form.save(commit=False)
            
            # Check action_active from paused modal
            action_active = self.request.POST.get('action_active')
            if action_active == 'enable':
                self.object.is_active = True
            elif action_active == 'keep_paused':
                self.object.is_active = False
            
            # Recalculate next_run based on start_time and interval so it always points to the next scheduled future occurrence
            self.object.next_run = self.object.calculate_next_run()
            self.object.save()
            tokens.instance = self.object
            tokens.save()
            
            status_note = " and resumed" if action_active == 'enable' else (" (remains paused)" if action_active == 'keep_paused' else "")
            messages.success(self.request, f"Job updated successfully{status_note}!")
            return redirect(self.success_url)
        else:
            return self.render_to_response(self.get_context_data(form=form))

class JobDeleteView(LoginRequiredMixin, DeleteView):
    model = AutobuyJob
    success_url = reverse_lazy('dashboard')
    
    def get_queryset(self):
        return AutobuyJob.objects.filter(user=self.request.user)
    
    def form_valid(self, form):
        messages.success(self.request, "Job deleted successfully.")
        return super().form_valid(form)

class JobClearAlertView(LoginRequiredMixin, View):
    def post(self, request, pk):
        job = get_object_or_404(AutobuyJob, pk=pk, user=request.user)
        job.last_status = None
        job.last_error_message = ""
        job.save(update_fields=['last_status', 'last_error_message'])
        if request.headers.get('HX-Request'):
             # Return the clean job card to reset borders/alerts
             return render(request, 'core/partials/job_card.html', {'job': job})
        return redirect('dashboard')

class JobHistoryView(LoginRequiredMixin, DetailView):
    model = AutobuyJob
    template_name = 'core/job_history.html'
    context_object_name = 'job'

    def get_queryset(self):
        return AutobuyJob.objects.filter(user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['logs'] = self.object.logs.all().order_by('-timestamp')[:50] # Last 50 logs
        context['trades'] = self.object.trades.all().order_by('-timestamp')[:50] # Last 50 trades
        return context

class JobRunwayView(LoginRequiredMixin, View):
    def get(self, request, pk):
        job = get_object_or_404(AutobuyJob, pk=pk, user=request.user)
        
        # Calculate Runway
        balance = 0
        runs_remaining = 0
        currency = job.quote_currency
        
        from .services.exchange_service import ExchangeService
        try:
            service = ExchangeService(job.account)
            # We need a method to get specific balance, but fetch_balance returns all.
            # Let's use fetch_balance from service (which uses CCXT).
            # Note: service.fetch_balance() is not explicitly defined in ExchangeService wrapper (Wait, it is. I saw it earlier.)
            # Let's check ExchangeService again to be sure. It has 'fetch_balance' method?
            # Yes, line 88: def fetch_balance(self): return self.exchange.fetch_balance()
            
            all_balances = service.fetch_balance()
            # standard ccxt structure: {'USDT': {'free': 100, ...}, ...}
            # OR {'free': {'USDT': 100, ...}} ?
            # CCXT 'total', 'free', 'used' usually.
            # safe way: all_balances.get(currency, {}).get('free', 0)
            
            balance = float(all_balances.get(currency, {}).get('free', 0))
            
            if job.total_amount > 0:
                runs_remaining = int(balance // float(job.total_amount))
            else:
                runs_remaining = 0
                
        except Exception as e:
            # If error (e.g. API fail), just show 0 or error
            balance = 0
            runs_remaining = 0
            # Maybe log it?
            
        context = {
            'job': job,
            'balance': balance,
            'runs_remaining': runs_remaining,
            'currency': currency
        }
        return render(request, 'core/partials/job_runway.html', context)

class TradeListView(LoginRequiredMixin, ListView):
    model = Trade
    template_name = 'core/trade_list.html'
    context_object_name = 'trades'
    paginate_by = 50
    
    def get_queryset(self):
        from django.db.models import Q
        queryset = Trade.objects.filter(user=self.request.user)
        
        # Job Filter
        job_filter = self.request.GET.get('job', '').strip()
        if job_filter:
            queryset = queryset.filter(Q(job_name=job_filter) | Q(job__name=job_filter))

        # Search
        search_query = self.request.GET.get('search', '').strip()
        if search_query:
            queryset = queryset.filter(
                Q(job_name__icontains=search_query) |
                Q(exchange_name__icontains=search_query) |
                Q(symbol__icontains=search_query) |
                Q(order_type__icontains=search_query)
            )
            
        # Sorting
        sort_by = self.request.GET.get('sort', '-timestamp')
        valid_sort_fields = ['timestamp', 'job_name', 'exchange_name', 'symbol', 'order_type', 'amount_received', 'purchase_price', 'amount_spent', 'fee_incurred']
        
        # Check if sort field is valid (allowing for '-' prefix)
        raw_sort_field = sort_by.lstrip('-')
        if raw_sort_field in valid_sort_fields:
            queryset = queryset.order_by(sort_by)
        else:
            queryset = queryset.order_by('-timestamp')
            
        return queryset

    def get_context_data(self, **kwargs):
        import json
        from django.db.models import Sum, Avg
        context = super().get_context_data(**kwargs)
        
        job_filter = self.request.GET.get('job', '').strip()
        search_query = self.request.GET.get('search', '').strip()
        current_sort = self.request.GET.get('sort', '-timestamp')
        
        # Distinct jobs available for filter dropdown
        user_jobs = AutobuyJob.objects.filter(user=self.request.user).values_list('name', flat=True)
        trade_jobs = Trade.objects.filter(user=self.request.user).values_list('job_name', flat=True).distinct()
        available_jobs = sorted(list(set(list(user_jobs) + list(trade_jobs))))

        # Summary Metrics on the filtered queryset (unpaginated)
        filtered_qs = self.get_queryset()
        total_trades = filtered_qs.count()
        stats_agg = filtered_qs.aggregate(
            total_spent=Sum('amount_spent'),
            total_fees=Sum('fee_incurred'),
            avg_spent=Avg('amount_spent')
        )
        total_spent = stats_agg['total_spent'] or 0
        total_fees = stats_agg['total_fees'] or 0
        avg_trade_size = stats_agg['avg_spent'] or 0

        # Portfolio summary for current pricing
        from .services.portfolio import PortfolioService
        price_map = {}
        try:
            portfolio_service = PortfolioService(self.request.user)
            portfolio_data = portfolio_service.get_portfolio_summary()
            price_map = {h['symbol']: h['current_price'] for h in portfolio_data.get('holdings', [])}
        except Exception:
            price_map = {}

        # Timeline Data (Cumulative Investment & Profit Over Time)
        chronological_trades = filtered_qs.order_by('timestamp')
        timeline_labels = []
        timeline_values = []
        profit_values = []
        
        running_spent = 0.0
        running_fees = 0.0
        running_tokens = {}

        for t in chronological_trades:
            running_spent += float(t.amount_spent)
            running_fees += float(t.fee_incurred)
            running_tokens[t.symbol] = running_tokens.get(t.symbol, 0.0) + float(t.amount_received)
            
            # Calculate market valuation of accumulated tokens up to this trade
            cur_val = 0.0
            for sym, qty in running_tokens.items():
                cur_price = float(price_map.get(sym, 0.0))
                if cur_price == 0.0:
                    cur_price = float(t.purchase_price)
                cur_val += (qty * cur_price)
            
            pnl = cur_val - running_spent - running_fees
            
            timeline_labels.append(t.timestamp.strftime('%b %d, %H:%M'))
            timeline_values.append(round(running_spent, 2))
            profit_values.append(round(pnl, 2))

        # Asset Breakdown Data
        breakdown_agg = (
            filtered_qs.values('symbol')
            .annotate(total=Sum('amount_spent'))
            .order_by('-total')
        )
        breakdown_labels = [item['symbol'] for item in breakdown_agg]
        breakdown_values = [round(float(item['total']), 2) for item in breakdown_agg]

        context.update({
            'current_sort': current_sort,
            'search_query': search_query,
            'job_filter': job_filter,
            'available_jobs': available_jobs,
            'total_trades': total_trades,
            'total_spent': total_spent,
            'total_fees': total_fees,
            'avg_trade_size': avg_trade_size,
            'timeline_labels_json': json.dumps(timeline_labels),
            'timeline_values_json': json.dumps(timeline_values),
            'profit_values_json': json.dumps(profit_values),
            'breakdown_labels_json': json.dumps(breakdown_labels),
            'breakdown_values_json': json.dumps(breakdown_values),
            'is_filtered': bool(job_filter or search_query)
        })
        return context

@login_required
def export_trades_csv(request):
    import csv
    from django.http import HttpResponse
    from django.db.models import Q

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="moondrip_trades.csv"'

    writer = csv.writer(response)
    writer.writerow(['Date/Time (UTC)', 'Job Name', 'Exchange', 'Pair', 'Type', 'Amount', 'Price', 'Cost', 'Fees'])

    trades = Trade.objects.filter(user=request.user)
    
    # Filter by job if present
    job_filter = request.GET.get('job', '').strip()
    if job_filter:
        trades = trades.filter(Q(job_name=job_filter) | Q(job__name=job_filter))

    # Filter by search if present
    search_query = request.GET.get('search', '').strip()
    if search_query:
        trades = trades.filter(
            Q(job_name__icontains=search_query) |
            Q(exchange_name__icontains=search_query) |
            Q(symbol__icontains=search_query) |
            Q(order_type__icontains=search_query)
        )

    trades = trades.order_by('-timestamp')

    for trade in trades:
        writer.writerow([
            trade.timestamp.isoformat(),
            trade.job_name,
            trade.exchange_name,
            trade.symbol,
            trade.order_type,
            trade.amount_received,
            trade.purchase_price,
            trade.amount_spent,
            trade.fee_incurred,
        ])

    return response

class TradeBackupExportView(LoginRequiredMixin, View):
    def get(self, request):
        import json
        from django.utils import timezone
        from .services.trade_backup_service import TradeBackupService
        
        backup_data = TradeBackupService.export_trades_json(request.user)
        timestamp_str = timezone.now().strftime('%Y%m%d_%H%M%S')
        filename = f"moondrip_portfolio_backup_{request.user.username}_{timestamp_str}.json"
        
        response = HttpResponse(
            json.dumps(backup_data, indent=2),
            content_type='application/json'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

class TradeImportView(LoginRequiredMixin, View):
    def post(self, request):
        from .services.trade_backup_service import TradeBackupService
        
        uploaded_file = request.FILES.get('backup_file')
        if not uploaded_file:
            messages.error(request, "Please select a JSON backup or CSV file to import.")
            return redirect('trade_list')

        result = TradeBackupService.import_trades(request.user, uploaded_file)
        
        if result['success']:
            if result['imported_count'] == 0 and result['skipped_duplicates'] == 0:
                messages.warning(request, result.get('error_message') or "No trade records found in the uploaded file.")
            else:
                msg = f"Successfully imported {result['imported_count']} trade(s) into your portfolio."
                if result['skipped_duplicates'] > 0:
                    msg += f" ({result['skipped_duplicates']} duplicate(s) skipped)."
                messages.success(request, msg)
        else:
            messages.error(request, f"Import failed: {result.get('error_message', 'Unknown error occurred.')}")

        return redirect('trade_list')

class RegisterView(CreateView):
    form_class = EmailUserCreationForm
    success_url = reverse_lazy('login')
    template_name = 'registration/register.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Account created successfully! Please log in.")
        return response

class SiteSettingsView(UserPassesTestMixin, UpdateView):
    model = AppSettings
    form_class = AppSettingsForm
    template_name = 'core/site_settings.html'
    success_url = reverse_lazy('site_settings')

    def test_func(self):
        return self.request.user.is_superuser

    def get_object(self, queryset=None):
        return AppSettings.load()

    def form_valid(self, form):
        messages.success(self.request, "Site settings updated successfully.")
        return super().form_valid(form)

class TestEmailView(UserPassesTestMixin, View):
    def test_func(self):
        return self.request.user.is_superuser

    def post(self, request, *args, **kwargs):
        from django.core.mail import send_mail
        from django.conf import settings
        
        email = request.POST.get('test_email')
        if not email:
            messages.error(request, "Please provide an email address.")
            return redirect('site_settings')

        try:
            # Force reload of settings in backend if needed
            # But normally each send_mail instantiates a new backend which loads new settings
            
            send_mail(
                subject='Moondrip - Test Email',
                message='This is a test email to verify your SMTP settings are configured correctly.',
                from_email=None, # Uses default from settings/backend
                recipient_list=[email],
                fail_silently=False,
            )
            messages.success(request, f"Test email sent successfully to {email}.")
        except Exception as e:
            messages.error(request, f"Failed to send email: {str(e)}")
            
        return redirect('site_settings')

class AccountPairsView(LoginRequiredMixin, View):
    def get(self, request, pk):
        account = get_object_or_404(ExchangeAccount, pk=pk, user=request.user)
        quote = request.GET.get('quote')
        from .services.exchange_service import ExchangeService
        service = ExchangeService(account)
        try:
            pairs = service.get_available_pairs(quote_currency=quote)
            return JsonResponse({
                'success': True,
                'account_id': account.id,
                'exchange': account.exchange.name,
                'quote': quote,
                'pairs': pairs
            })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': str(e),
                'pairs': []
            })

