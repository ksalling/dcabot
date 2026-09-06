from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from .models import ExchangeAccount, AutobuyJob, JobToken, SupportedExchange, AppSettings
from .services.exchange_service import ExchangeService

class UserProfileForm(forms.ModelForm):
    notify_trade_success = forms.BooleanField(
        required=False,
        label="Successful Trade Alerts",
        help_text="Receive an email summary whenever a scheduled DCA trade executes."
    )
    notify_trade_failed = forms.BooleanField(
        required=False,
        label="Failed Trade Alerts",
        help_text="Receive an immediate email alert if an order fails on the exchange."
    )
    notify_trade_skipped_paused = forms.BooleanField(
        required=False,
        label="Paused Job Alerts",
        help_text="Receive an email notice if a scheduled trade was skipped because the job is paused."
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']
        labels = {
            'first_name': 'First Name',
            'last_name': 'Last Name',
            'email': 'Email Address',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and hasattr(self.instance, 'userprofile'):
            profile = self.instance.userprofile
            self.fields['notify_trade_success'].initial = profile.notify_trade_success
            self.fields['notify_trade_failed'].initial = profile.notify_trade_failed
            self.fields['notify_trade_skipped_paused'].initial = profile.notify_trade_skipped_paused

    def save(self, commit=True):
        user = super().save(commit=commit)
        if hasattr(user, 'userprofile'):
            profile = user.userprofile
            profile.notify_trade_success = self.cleaned_data.get('notify_trade_success', True)
            profile.notify_trade_failed = self.cleaned_data.get('notify_trade_failed', True)
            profile.notify_trade_skipped_paused = self.cleaned_data.get('notify_trade_skipped_paused', True)
            if commit:
                profile.save()
        return user

class ExchangeAccountForm(forms.ModelForm):
    class Meta:
        model = ExchangeAccount
        fields = ['exchange', 'nickname', 'api_key', 'api_secret', 'api_passphrase']
        widgets = {
            'api_key': forms.PasswordInput(render_value=True),
            'api_secret': forms.PasswordInput(render_value=True),
            'api_passphrase': forms.PasswordInput(render_value=True),
        }
        labels = {
            'exchange': 'Exchange Platform',
            'nickname': 'Account Nickname (Optional)',
            'api_key': 'API Key',
            'api_secret': 'API Secret',
            'api_passphrase': 'API Passphrase (if required)',
        }

    def clean(self):
        cleaned_data = super().clean()
        exchange_data = cleaned_data.get('exchange')
        api_key = cleaned_data.get('api_key')
        api_secret = cleaned_data.get('api_secret')
        api_passphrase = cleaned_data.get('api_passphrase')

        if not exchange_data or not api_key or not api_secret:
            return cleaned_data

        # Temporary instance for validation
        temp_account = ExchangeAccount(
            exchange=exchange_data,
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase
        )
        
        # We need to manually encrypt because ExchangeService expects decrypted values
        # BUT ExchangeService uses the model instance which has EncryptedCharField.
        # EncryptedCharField automatically decrypts when accessed on a model instance IF it was loaded from DB.
        # However, here we have a fresh instance. 
        # ExchangeService._get_exchange_instance accesses self.account.api_key.
        # On a fresh unsaved instance, self.account.api_key will be the raw string we passed in (e.g. "my-key").
        # This works fine because ExchangeService just uses that string.
        # EXCEPT `ExchangeService` assumes it might need to decrypt if it was encrypted?
        # Let's check `ExchangeService`. It just reads `self.account.api_key`. 
        # If I pass `api_key="abc"` to `ExchangeAccount(...)`, accessing `.api_key` returns "abc".
        # So we can pass this temp account to ExchangeService.

        service = ExchangeService(temp_account)
        try:
            # We need to verify if the exchange is supported first, which ExchangeService.__init__ does.
            # Then validate connection.
            if not service.validate_connection():
                raise forms.ValidationError("Could not connect to exchange with provided credentials. Please check your API Key, Secret, and Passphrase.")
        except Exception as e:
            # If validate_connection fails or init fails
            raise forms.ValidationError(f"Connection failed: {str(e)}")
            
        return cleaned_data

class ExchangeAccountEditForm(forms.ModelForm):
    class Meta:
        model = ExchangeAccount
        fields = ['nickname']
        labels = {
            'nickname': 'Account Nickname',
        }

class AutobuyJobForm(forms.ModelForm):
    class Meta:
        model = AutobuyJob
        fields = ['name', 'account', 'total_amount', 'quote_currency', 'interval', 'start_time', 'end_date']
        widgets = {
            'start_time': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'end_date': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'total_amount': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'quote_currency': forms.Select(choices=AutobuyJob.QUOTE_CURRENCIES),
        }
        labels = {
            'name': 'Job Name',
            'account': 'Exchange Account',
            'total_amount': 'Total Investment Amount',
            'quote_currency': 'Quote Currency (e.g. USDT)',
            'interval': 'Run Frequency',
            'start_time': 'Start Date & Time',
            'end_date': 'End Date & Time (Optional)',
        }

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['account'].queryset = ExchangeAccount.objects.filter(user=user)

class JobTokenForm(forms.ModelForm):
    class Meta:
        model = JobToken
        fields = ['token_symbol', 'percentage']
        widgets = {
            'token_symbol': forms.TextInput(attrs={
                'list': 'available-pairs-list',
                'placeholder': 'e.g. BTC/USDT or BTC',
                'autocomplete': 'off',
                'class': 'token-symbol-input'
            }),
            'percentage': forms.NumberInput(attrs={
                'step': '0.01',
                'min': '0',
                'max': '100',
                'placeholder': '100'
            }),
        }

class AppSettingsForm(forms.ModelForm):
    class Meta:
        model = AppSettings
        fields = ['smtp_host', 'smtp_port', 'smtp_user', 'smtp_password', 'use_tls', 'default_from_email']
        widgets = {
            'smtp_password': forms.PasswordInput(render_value=True),
        }
        help_texts = {
            'smtp_password': "Stored securely using encryption.",
            'default_from_email': "The email address that automated emails will appear to come from.",
        }

class EmailUserCreationForm(UserCreationForm):
    first_name = forms.CharField(required=True)
    last_name = forms.CharField(required=True)
    email = forms.EmailField(required=True, help_text="Required. Use a valid email address.")

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("A user with that email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        # Set username to email. Ensure it's not too long (User.username max_length=150)
        # We assume email is validated regarding length by EmailField usually, but strict mapping:
        user.username = user.email[:150] 
        if commit:
            user.save()
        return user
