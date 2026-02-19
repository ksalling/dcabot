import ccxt
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy, reverse
from django.http import HttpResponse
from django.contrib import messages
from django.contrib.auth.models import User
from .models import AutobuyJob, ExchangeAccount, JobLog, Trade, JobToken, SupportedExchange
from .forms import AutobuyJobForm, ExchangeAccountForm, UserProfileForm, ExchangeAccountEditForm
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

class DashboardView(LoginRequiredMixin, View):
    def get(self, request):
        from .services.portfolio import PortfolioService
        
        # Data Fetching
        jobs = AutobuyJob.objects.filter(user=request.user).order_by('-created_at')
        accounts = ExchangeAccount.objects.filter(user=request.user)
        recent_trades = Trade.objects.filter(user=request.user).order_by('-timestamp')[:10] # Show 10
        
        # Portfolio Calculation
        portfolio_service = PortfolioService(request.user)
        portfolio_data = portfolio_service.get_portfolio_summary()
        
        context = {
            'jobs': jobs,
            'accounts': accounts,
            'recent_trades': recent_trades,
            'portfolio': portfolio_data
        }
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
        JobTokenFormSet = inlineformset_factory(AutobuyJob, JobToken, fields=('token_symbol', 'percentage'), extra=1, can_delete=False)
        if self.request.POST:
            data['tokens'] = JobTokenFormSet(self.request.POST)
        else:
            data['tokens'] = JobTokenFormSet()
        return data

    def form_valid(self, form):
        context = self.get_context_data()
        tokens = context['tokens']
        if form.is_valid() and tokens.is_valid():
            # 1. Validate Total Percentage = 100%
            total_percentage = 0
            for token_form in tokens:
                if token_form.cleaned_data and not token_form.cleaned_data.get('DELETE', False):
                    total_percentage += token_form.cleaned_data.get('percentage', 0)
            
            if total_percentage != 100:
                messages.error(self.request, f"Total percentage must be 100%. Current total: {total_percentage}%")
                return self.render_to_response(self.get_context_data(form=form))

            # 2. Exchange Validation
            self.object = form.save(commit=False)
            self.object.user = self.request.user
            self.object.is_active = True # Active by default on create
            # We need to save object first? OR use instance?
            # ModelForm save(commit=False) gives us an instance.
            
            from .services.exchange_service import ExchangeService
            service = ExchangeService(self.object.account)
            
            # Check Funds (Soft Validation)
            has_funds, msg = service.validate_job_funds(self.object.total_amount, self.object.quote_currency)
            if not has_funds:
                self.object.last_status = 'warning'
                self.object.last_error_message = msg
                messages.warning(self.request, f"Warning: {msg} The job has been created but may fail if funds are not added.")
            else:
                self.object.last_status = 'success' # Or clear it?
                self.object.last_error_message = ''
            
            # Check Pairs (Hard Validation)
            for token_form in tokens:
                 if token_form.cleaned_data and not token_form.cleaned_data.get('DELETE', False):
                     symbol = token_form.cleaned_data.get('token_symbol')
                     if '/' in symbol:
                         base, quote = symbol.split('/')
                         if quote != self.object.quote_currency:
                              messages.error(self.request, f"Token {symbol} quote currency ({quote}) does not match job quote currency ({self.object.quote_currency}).")
                              return self.render_to_response(self.get_context_data(form=form))
                     else:
                         base = symbol
                     
                     is_valid, pair_msg = service.validate_pair(base, self.object.quote_currency)
                     if not is_valid:
                         messages.error(self.request, pair_msg)
                         return self.render_to_response(self.get_context_data(form=form))

            self.object = form.save(commit=False)
            self.object.is_active = True
            
            # Initialize next_run to start_time so scheduler picks it up
            if not self.object.next_run:
                self.object.next_run = self.object.start_time
                
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
                
                # If next_run is missing (e.g. from legacy or error), set it to now to resume immediately
                # or start_time if it's in the future? 
                # Simplest is: if no next_run, set to now.
                if not job.next_run:
                    from django.utils import timezone
                    job.next_run = timezone.now()
                    
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
        JobTokenFormSet = inlineformset_factory(AutobuyJob, JobToken, fields=('token_symbol', 'percentage'), extra=0, can_delete=True)
        if self.request.POST:
            data['tokens'] = JobTokenFormSet(self.request.POST, instance=self.object)
        else:
            data['tokens'] = JobTokenFormSet(instance=self.object)
        return data

    def form_valid(self, form):
        context = self.get_context_data()
        tokens = context['tokens']
        
        if form.is_valid() and tokens.is_valid():
            # 1. Validate Total Percentage = 100%
            total_percentage = 0
            for token_form in tokens:
                if token_form.cleaned_data and not token_form.cleaned_data.get('DELETE', False):
                    total_percentage += token_form.cleaned_data.get('percentage', 0)
            
            if total_percentage != 100:
                messages.error(self.request, f"Total percentage must be 100%. Current total: {total_percentage}%")
                return self.render_to_response(self.get_context_data(form=form))

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
            
            # Check Pairs (Hard Validation)
            for token_form in tokens:
                 if token_form.cleaned_data and not token_form.cleaned_data.get('DELETE', False):
                     symbol = token_form.cleaned_data.get('token_symbol')
                     if '/' in symbol:
                         base, quote = symbol.split('/')
                         if quote != job.quote_currency:
                              messages.error(self.request, f"Token {symbol} quote currency ({quote}) does not match job quote currency ({job.quote_currency}).")
                              return self.render_to_response(self.get_context_data(form=form))
                     else:
                         base = symbol
                     
                     is_valid, pair_msg = service.validate_pair(base, job.quote_currency)
                     if not is_valid:
                         messages.error(self.request, pair_msg)
                         return self.render_to_response(self.get_context_data(form=form))

            self.object = form.save(commit=False)
            
            # If start_time was changed, reset the schedule
            if 'start_time' in form.changed_data:
                 self.object.next_run = self.object.start_time
            # Ensure next_run is set if missing (legacy)
            elif not self.object.next_run:
                 self.object.next_run = self.object.start_time
            self.object.is_active = True
            self.object.save()
            tokens.instance = self.object
            tokens.save()
            
            # Only show success message if no higher priority message (like error) - but we return early on error.
            # Warning persists.
            messages.success(self.request, "Job updated successfully!")
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
        return Trade.objects.filter(user=self.request.user).order_by('-timestamp')

