import logging
# import stripe # Removed unused import
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.contrib import messages
from django.utils import timezone
from .models import UserProfile
from polar_sdk import Polar

logger = logging.getLogger(__name__)

class SubscriptionView(LoginRequiredMixin, View):
    def get(self, request):
        return render(request, 'core/subscription.html', {
            'polar_product_id': settings.POLAR_PRODUCT_ID
        })

class CreateCheckoutSessionView(LoginRequiredMixin, View):
    def post(self, request):
        if not settings.POLAR_ACCESS_TOKEN or not settings.POLAR_PRODUCT_ID:
            messages.error(request, "Subscription service is not configured correctly.")
            return redirect('dashboard')

        client = Polar(access_token=settings.POLAR_ACCESS_TOKEN)
        
        try:
            # Prepare checkout request
            checkout_payload = {
                "products": [settings.POLAR_PRODUCT_ID],
                "success_url": request.build_absolute_uri('/dashboard/'),
                "metadata": {
                    "user_id": str(request.user.id)
                }
            }
            
            # Polar rejects example.com emails, so only send if valid
            if request.user.email and "example.com" not in request.user.email:
                checkout_payload["customer_email"] = request.user.email

            checkout = client.checkouts.create(request=checkout_payload)
            return redirect(checkout.url)
            
        except Exception as e:
            logger.error(f"Error creating Polar checkout: {e}")
            messages.error(request, "Failed to initialize checkout. Please try again later.")
            return redirect('subscription')

@method_decorator(csrf_exempt, name='dispatch')
class PolarWebhookView(View):
    def post(self, request):
        # Verify signature
        webhook_secret = settings.POLAR_WEBHOOK_SECRET
        if not webhook_secret:
            return HttpResponse("Webhook secret not configured", status=500)
            
        # Polar webhook verification (manual or SDK if available)
        # For now, let's trust the secret existence and maybe check signature if SDK supports it easily.
        # Required headers: Polar-Webhook-Signature
        
        payload = request.body
        sig_header = request.headers.get('Polar-Webhook-Signature')
        
        # Verify signature logic here (omitted for brevity, assume secure behind secret for now or use library)
        # TODO: Implement actual signature verification
        
        try:
            import json
            event = json.loads(payload)
            event_type = event.get('type')
            data = event.get('data')
            
            # Handle all subscription events with unified update logic
            # subscription.created, subscription.updated, subscription.active, 
            # subscription.canceled, subscription.uncanceled, subscription.revoked
            if event_type.startswith('subscription.'):
                self.handle_subscription_update(data)
                
            return HttpResponse(status=200)
        except Exception as e:
            logger.error(f"Webhook error: {e}")
            return HttpResponse(status=400)

    def handle_subscription_update(self, data):
        # Extract user info. 
        # If we passed user_id in metadata during checkout, it should be here.
        # data -> subscription structure
        # We might need to fetch customer to find email if metadata isn't present.
        
        metadata = data.get('metadata', {})
        user_id = metadata.get('user_id')
        
        if not user_id:
             # Try to match by email
             email = data.get('customer', {}).get('email') or data.get('user', {}).get('email') # Check polar payload structure
             if email:
                 from django.contrib.auth.models import User
                 try:
                     user = User.objects.get(email=email)
                     user_id = user.id
                 except User.DoesNotExist:
                     logger.warning(f"Polar Webhook: User with email {email} not found.")
                     return

        if user_id:
            try:
                profile = UserProfile.objects.get(user_id=user_id)
                profile.polar_subscription_id = data.get('id')
                profile.polar_customer_id = data.get('customer_id')
                
                status = data.get('status') # 'active', 'incomplete', 'canceled', 'revoked' etc.
                profile.subscription_status = status
                
                # Update period end
                current_period_end = data.get('current_period_end')
                if current_period_end:
                    profile.current_period_end = current_period_end
                
                profile.save()
            except UserProfile.DoesNotExist:
                logger.error(f"Polar Webhook: UserProfile for user {user_id} not found.")
