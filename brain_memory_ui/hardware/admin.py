"""
SOMA-AI Hardware Admin
=======================
Dashboard zur Verwaltung von Räumen und Hardware-Nodes.
"""

from django.contrib import admin
from hardware.models import Room, HardwareNode, NodeCapability


class NodeCapabilityInline(admin.TabularInline):
    model = NodeCapability
    extra = 1


class HardwareNodeInline(admin.TabularInline):
    model = HardwareNode
    extra = 0
    fields = ["node_id", "name", "node_type", "protocol", "status", "is_active"]
    readonly_fields = ["node_id", "status"]
    show_change_link = True


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = [
        "name", "floor", "is_kids_room", "is_active",
        "node_count", "created_at",
    ]
    list_filter = ["is_kids_room", "is_active", "floor"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    inlines = [HardwareNodeInline]

    def node_count(self, obj):
        return obj.hardware_nodes.count()
    node_count.short_description = "Nodes"


@admin.register(HardwareNode)
class HardwareNodeAdmin(admin.ModelAdmin):
    list_display = [
        "name", "node_id", "node_type", "protocol",
        "room", "status", "is_active", "last_seen",
    ]
    list_filter = ["node_type", "protocol", "status", "is_active", "room"]
    search_fields = ["node_id", "name", "ip_address", "mqtt_topic"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [NodeCapabilityInline]

    fieldsets = (
        ("Identifikation", {
            "fields": ("node_id", "name", "node_type"),
        }),
        ("Verbindung", {
            "fields": ("protocol", "ip_address", "mqtt_topic", "ha_entity_id"),
        }),
        ("Zuordnung", {
            "fields": ("room", "is_active", "status"),
        }),
        ("Details", {
            "fields": ("firmware_version", "discovered_via", "config"),
            "classes": ("collapse",),
        }),
        ("Zeitstempel", {
            "fields": ("last_seen", "created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )


@admin.register(NodeCapability)
class NodeCapabilityAdmin(admin.ModelAdmin):
    list_display = ["node", "capability", "direction"]
    list_filter = ["direction", "capability"]


# Admin Site Customization
admin.site.site_header = "🧠 SOMA-AI Nervensystem"
admin.site.site_title = "SOMA Admin"
admin.site.index_title = "Hardware & System-Verwaltung"
