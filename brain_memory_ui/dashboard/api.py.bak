"""
SOMA-AI Dashboard API
======================
REST-Endpunkte für das Thinking Stream UI und das Tablet-Face.
"""

import json
from pathlib import Path

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from hardware.models import Room, HardwareNode

# Pfad zu den Plugins – relativ zu dieser Datei
PLUGINS_DIR = Path(__file__).resolve().parent.parent.parent / "evolution_lab" / "generated_plugins"


# ── Plugin Helpers ────────────────────────────────────────────────────────────

def _read_plugin_meta(path: Path) -> dict:
    """Liest __version__, __author__, __description__ aus einem Plugin-File."""
    meta = {"version": "—", "author": "soma-ai", "description": "—"}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("__version__"):
                meta["version"] = line.split("=", 1)[1].strip().strip("'\"")
            elif line.startswith("__author__"):
                meta["author"] = line.split("=", 1)[1].strip().strip("'\"")
            elif line.startswith("__description__"):
                meta["description"] = line.split("=", 1)[1].strip().strip("'\"")
    except Exception:
        pass
    return meta


def _collect_plugins() -> list[dict]:
    """Gibt alle Plugins (aktiv + deaktiviert) als Dicts zurück."""
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    plugins = []

    for f in sorted(PLUGINS_DIR.glob("*.py")):
        if f.name.startswith("_"):
            continue
        meta = _read_plugin_meta(f)
        plugins.append({
            "name": f.stem,
            "enabled": True,
            "version": meta["version"],
            "author": meta["author"],
            "description": meta["description"],
        })

    for f in sorted(PLUGINS_DIR.glob("*.disabled")):
        name = f.name[: -len(".disabled")]   # entfernt ".disabled" am Ende
        if name.endswith(".py"):
            name = name[:-3]
        meta = _read_plugin_meta(f)
        plugins.append({
            "name": name,
            "enabled": False,
            "version": meta["version"],
            "author": meta["author"],
            "description": meta["description"],
        })

    return plugins


@require_GET
def dashboard_view(request):
    """Haupt-Dashboard Template."""
    return render(request, 'dashboard.html')


@require_GET
def thinking_stream_view(request):
    """Legacy Thinking Stream Template."""
    return render(request, 'thinking_stream.html')


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


# ── Plugin Management API ─────────────────────────────────────────────────────

@require_GET
def plugins_list(request):
    """Alle Plugins mit Status (aktiv/deaktiviert) auflisten."""
    return JsonResponse({"plugins": _collect_plugins()})


@csrf_exempt
@require_POST
def plugin_toggle(request, name: str):
    """Plugin aktivieren oder deaktivieren (toggle)."""
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    py_path = PLUGINS_DIR / f"{name}.py"
    disabled_path = PLUGINS_DIR / f"{name}.disabled"

    if py_path.exists():
        py_path.rename(disabled_path)
        return JsonResponse({"status": "disabled", "name": name})
    elif disabled_path.exists():
        disabled_path.rename(py_path)
        return JsonResponse({"status": "enabled", "name": name})
    else:
        return JsonResponse({"error": f"Plugin '{name}' nicht gefunden"}, status=404)


@csrf_exempt
@require_POST
def plugin_delete(request, name: str):
    """Plugin dauerhaft löschen."""
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    py_path = PLUGINS_DIR / f"{name}.py"
    disabled_path = PLUGINS_DIR / f"{name}.disabled"

    if py_path.exists():
        py_path.unlink()
        return JsonResponse({"status": "deleted", "name": name})
    elif disabled_path.exists():
        disabled_path.unlink()
        return JsonResponse({"status": "deleted", "name": name})
    else:
        return JsonResponse({"error": f"Plugin '{name}' nicht gefunden"}, status=404)
