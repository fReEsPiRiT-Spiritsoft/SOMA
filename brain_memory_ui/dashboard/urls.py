"""Dashboard URL Configuration."""
from django.urls import path
from dashboard import api

urlpatterns = [
    path("hardware/", api.hardware_overview, name="hardware-overview"),
    path("hardware/type/<str:node_type>/", api.nodes_by_type, name="nodes-by-type"),
    path("hardware/room/<slug:slug>/", api.room_detail, name="room-detail"),
]
