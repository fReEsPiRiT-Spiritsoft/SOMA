"""WSGI config for SOMA-AI Brain Memory UI."""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core_settings.settings")
application = get_wsgi_application()
