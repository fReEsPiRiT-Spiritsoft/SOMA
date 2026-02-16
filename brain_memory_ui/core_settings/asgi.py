"""ASGI config for SOMA-AI Brain Memory UI."""
import os
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core_settings.settings")
application = get_asgi_application()
