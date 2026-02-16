"""
SOMA-AI Dashboard API
======================
REST-Endpunkte für das Thinking Stream UI und das Tablet-Face.
"""

from django.http import JsonResponse
from django.views.decorators.http import require_GET
from hardware.models import Room, HardwareNode


@require_GET
def hardware_overview(request):
    """Alle Räume mit ihren Nodes für brain_core."""
    rooms = Room.objects.filter(is_active=True).prefetch_related("hardware_nodes")
    data = []
    for room in rooms:
        nodes = room.hardware_nodes.filter(is_active=True)
        data.append({
            "room_id": room.slug,
            "room_name": room.name,
            "floor": room.floor,
            "is_kids_room": room.is_kids_room,
            "nodes": [node.to_api_dict() for node in nodes],
        })
    return JsonResponse({"rooms": data})


@require_GET
def nodes_by_type(request, node_type: str):
    """Alle Nodes eines Typs (z.B. mic, spk, tab)."""
    nodes = HardwareNode.objects.filter(
        node_type=node_type,
        is_active=True,
    ).select_related("room")
    return JsonResponse({
        "nodes": [n.to_api_dict() for n in nodes],
    })


@require_GET
def room_detail(request, slug: str):
    """Detail-Info zu einem Raum."""
    try:
        room = Room.objects.get(slug=slug, is_active=True)
    except Room.DoesNotExist:
        return JsonResponse({"error": "Room not found"}, status=404)

    nodes = room.hardware_nodes.filter(is_active=True)
    return JsonResponse({
        "room_id": room.slug,
        "room_name": room.name,
        "floor": room.floor,
        "is_kids_room": room.is_kids_room,
        "nodes": [n.to_api_dict() for n in nodes],
    })
