"""
SOMA-AI URL Configuration
"""

from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse


def api_root(request):
    return JsonResponse({
        "service": "SOMA-AI Brain Memory",
        "version": "1.0.0-genesis",
        "endpoints": {
            "admin": "/admin/",
            "dashboard": "/dashboard/",
            "hardware_api": "/api/dashboard/hardware/",
        },
    })


urlpatterns = [
    path("", api_root),
    path("admin/", admin.site.urls),
    # Dashboard UI (HTML Views)
    path("dashboard/", include("dashboard.urls")),
    # API Endpoints  
    path("api/dashboard/", include("dashboard.urls")),
]
