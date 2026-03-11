"""Dashboard URL Configuration."""
from django.urls import path
from dashboard import api

urlpatterns = [
    # Dashboard Views
    path("", api.dashboard_view, name="dashboard"),
    path("thinking/", api.thinking_stream_view, name="thinking-stream"),
    
    # API Endpoints – Hardware
    path("hardware/", api.hardware_overview, name="hardware-overview"),
    path("hardware/type/<str:node_type>/", api.nodes_by_type, name="nodes-by-type"),
    path("hardware/room/<slug:slug>/", api.room_detail, name="room-detail"),

    # API Endpoints – Plugin Management
    path("plugins/", api.plugins_list, name="plugins-list"),
    path("plugins/<str:name>/toggle/", api.plugin_toggle, name="plugin-toggle"),
    path("plugins/<str:name>/delete/", api.plugin_delete, name="plugin-delete"),
]
