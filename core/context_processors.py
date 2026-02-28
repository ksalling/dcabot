from core.models import AppSettings

def global_settings(request):
    try:
        app_settings = AppSettings.load()
    except Exception:
        app_settings = None
        
    return {
        'app_settings': app_settings
    }
