import pyotp
import qrcode
import base64
from io import BytesIO
from django.shortcuts import render, redirect, resolve_url
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.contrib.auth.views import LoginView
from django.contrib.auth import login
from django.contrib.auth.models import User
from core.models import AppSettings
from authlib.integrations.django_client import OAuth
from django.conf import settings
from django.urls import reverse

oauth = OAuth()
oauth.register(
    name='google',
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile'
    }
)

class TwoFactorSetupView(LoginRequiredMixin, View):
    def get(self, request):
        profile = request.user.userprofile
        
        # If they already have 2FA enabled, the GET view becomes a status/disable page
        if profile.totp_enabled:
            return render(request, 'core/2fa_setup.html', {'is_enabled': True})
            
        # Generate a new secret if one doesn't exist, store it in session until verification
        secret = request.session.get('pending_totp_secret')
        if not secret:
            secret = pyotp.random_base32()
            request.session['pending_totp_secret'] = secret
            
        # Build the provisioning URI for the Google Authenticator app
        totp = pyotp.TOTP(secret)
        provisioning_uri = totp.provisioning_uri(
            name=request.user.email or request.user.username,
            issuer_name="Moondrip"
        )
        
        # Generate PNG QR code
        img = qrcode.make(provisioning_uri)
        stream = BytesIO()
        img.save(stream, format="PNG")
        encoded = base64.b64encode(stream.getvalue()).decode("utf-8")
        qr_code_data_uri = f"data:image/png;base64,{encoded}"
        
        return render(request, 'core/2fa_setup.html', {
            'is_enabled': False,
            'qr_code_data_uri': qr_code_data_uri,
            'secret_key': secret
        })

    def post(self, request):
        profile = request.user.userprofile
        
        if profile.totp_enabled:
            # Handle disable request
            if request.POST.get('action') == 'disable':
                profile.totp_enabled = False
                profile.totp_secret = None
                profile.save()
                messages.success(request, "Two-Factor Authentication has been disabled.")
            return redirect('profile')
            
        # Handle verification of the first code
        code = request.POST.get('code', '').strip()
        secret = request.session.get('pending_totp_secret')
        
        if not secret:
            messages.error(request, "Setup session expired. Please try again.")
            return redirect('2fa_setup')
            
        totp = pyotp.TOTP(secret)
        if totp.verify(code):
            # Code is correct, burn the secret into the DB and enable
            profile.totp_secret = secret
            profile.totp_enabled = True
            profile.save()
            
            # Wipe session
            del request.session['pending_totp_secret']
            
            messages.success(request, "Two-Factor Authentication successfully enabled!")
            return redirect('profile')
        else:
            messages.error(request, "Invalid authentication code. Please check your app and try again.")
            return redirect('2fa_setup')

class CustomLoginView(LoginView):
    def form_valid(self, form):
        user = form.get_user()
        profile = getattr(user, 'userprofile', None)
        settings = AppSettings.load()
        
        # Determine if 2FA applies
        requires_2fa = profile and profile.totp_enabled
        must_setup_2fa = settings.require_2fa_globally and (not profile or not profile.totp_enabled)
        
        if requires_2fa:
            # Store user ID in session, do NOT log them in yet
            self.request.session['pre_2fa_user_id'] = user.id
            return redirect('2fa_verify')
        elif must_setup_2fa:
            # Log them in, but redirect to setup immediately
            login(self.request, user)
            messages.warning(self.request, "Administrators have required all users to enable 2FA.")
            return redirect('2fa_setup')
        else:
            # Standard login
            login(self.request, user)
            return redirect(self.get_success_url())

class TwoFactorVerifyView(View):
    def get(self, request):
        if 'pre_2fa_user_id' not in request.session:
            return redirect('login')
        return render(request, 'core/2fa_verify.html')

    def post(self, request):
        user_id = request.session.get('pre_2fa_user_id')
        if not user_id:
            return redirect('login')
            
        try:
            user = User.objects.get(id=user_id)
            profile = user.userprofile
            
            code = request.POST.get('code', '').strip()
            totp = pyotp.TOTP(profile.totp_secret)
            
            if totp.verify(code):
                # Success!
                login(request, user)
                del request.session['pre_2fa_user_id']
                
                next_url = request.session.get('next', request.GET.get('next', 'dashboard'))
                return redirect(next_url)
            else:
                messages.error(request, "Invalid authentication code.")
                return redirect('2fa_verify')
        except User.DoesNotExist:
            del request.session['pre_2fa_user_id']
            return redirect('login')

def google_login(request):
    app_settings = AppSettings.load()
    if not app_settings.allow_google_oauth:
        messages.error(request, "Google login is currently disabled.")
        return redirect('login')
        
    redirect_uri = request.build_absolute_uri(reverse('google_callback'))
    return oauth.google.authorize_redirect(request, redirect_uri)

def google_callback(request):
    app_settings = AppSettings.load()
    if not app_settings.allow_google_oauth:
        messages.error(request, "Google login is currently disabled.")
        return redirect('login')
        
    try:
        token = oauth.google.authorize_access_token(request)
        user_info = token.get('userinfo')
        if not user_info:
            messages.error(request, "Failed to retrieve user information from Google.")
            return redirect('login')
            
        email = user_info.get('email')
        if not email:
            messages.error(request, "Google did not provide an email address.")
            return redirect('login')
            
        # Check if user exists, or create
        user, created = User.objects.get_or_create(email=email, defaults={
            'username': email[:150],
            'first_name': user_info.get('given_name', ''),
            'last_name': user_info.get('family_name', '')
        })
        
        # Determine 2FA validation
        profile = getattr(user, 'userprofile', None)
        requires_2fa = profile and profile.totp_enabled
        must_setup_2fa = app_settings.require_2fa_globally and (not profile or not profile.totp_enabled)
        
        if requires_2fa:
            request.session['pre_2fa_user_id'] = user.id
            return redirect('2fa_verify')
        elif must_setup_2fa:
            login(request, user)
            messages.warning(request, "Administrators have required all users to enable 2FA.")
            return redirect('2fa_setup')
        else:
            login(request, user)
            return redirect('dashboard')
            
    except Exception as e:
        messages.error(request, f"Google OAuth failed: {str(e)}")
        return redirect('login')
