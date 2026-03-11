"""
Plugin: Window Listening
========================
v1.0.0 — SOMA kann sehen was auf deinen Monitoren läuft.

Arch Linux / Wayland (Hyprland/KDE/GNOME) + X11 Support.
Nutzt Screenshots via grim (Wayland) oder scrot (X11) +
OCR via tesseract um Text zu extrahieren.
Aktives Fenster via hyprctl / xdotool.

✅ Wayland (grim + slurp)
✅ X11 Fallback (scrot + xdotool)  
✅ OCR Text-Extraktion (tesseract)
✅ Aktives Fenster + Titel erkennen
✅ Screenshot auf Anfrage oder automatisch
✅ Ergebnis ins Memory (SOMA "erinnert sich" was sie gesehen hat)

Beispiele:
  - "Soma, was siehst du auf meinem Bildschirm?"
  - "Soma, schau mal auf Monitor 2"
  - "Soma, was steht da gerade?"
  - "Soma, beobachte meinen Bildschirm"
"""
__version__ = "1.0.0"
__author__ = "SOMA Evolution Lab"
__description__ = "SOMA kann Monitore sehen — Screenshot + OCR + aktives Fenster"
__dependencies__ = [
    "pytesseract",
    "Pillow",
    "aiofiles",
]

import asyncio
import os
import tempfile
import time
from pathlib import Path
from typing import Optional
import structlog

logger = structlog.get_logger("soma.plugin.window_listening")

# ── Systemtools Detection ─────────────────────────────────────────────────
_wayland = os.environ.get("WAYLAND_DISPLAY") is not None
_hyprland = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") is not None

# ── State ─────────────────────────────────────────────────────────────────
_observe_task: Optional[asyncio.Task] = None
_observe_active = False
_last_screenshot: Optional[str] = None  # Pfad zum letzten Screenshot
_speak_callback = None


# ── Plugin Lifecycle ──────────────────────────────────────────────────────

async def on_load():
    logger.info(
        "window_listening_loaded",
        wayland=_wayland,
        hyprland=_hyprland,
    )
    # System-Dependencies prüfen
    missing = await _check_system_deps()
    if missing:
        logger.warning("missing_system_tools", tools=missing,
                       hint="Install via: sudo pacman -S " + " ".join(missing))


async def on_unload():
    global _observe_task, _observe_active
    _observe_active = False
    if _observe_task and not _observe_task.done():
        _observe_task.cancel()
    logger.info("window_listening_unloaded")


def set_speak_callback(cb):
    global _speak_callback
    _speak_callback = cb


# ── System Dependency Check ───────────────────────────────────────────────

async def _check_system_deps() -> list[str]:
    """Prüft ob alle nötigen Tools installiert sind."""
    missing = []
    tools = ["tesseract"]

    if _wayland:
        tools.append("grim")
    else:
        tools.extend(["scrot", "xdotool"])

    if _hyprland:
        tools.append("hyprctl")

    for tool in tools:
        proc = await asyncio.create_subprocess_exec(
            "which", tool,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        if proc.returncode != 0:
            missing.append(tool)

    return missing


# ── Screenshot ────────────────────────────────────────────────────────────

async def take_screenshot(monitor: int = 0) -> Optional[str]:
    """
    Macht einen Screenshot des angegebenen Monitors.
    Returns: Pfad zur temporären PNG-Datei oder None bei Fehler.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False, prefix="soma_screen_")
    tmp.close()
    path = tmp.name

    try:
        if _wayland:
            # Wayland: grim (alle Outputs auflisten, dann gezielt screenshotten)
            if monitor == 0:
                # Gesamter Desktop
                proc = await asyncio.create_subprocess_exec(
                    "grim", path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            else:
                # Spezifischer Monitor via Output-Name
                output_name = await _get_wayland_output(monitor)
                if output_name:
                    proc = await asyncio.create_subprocess_exec(
                        "grim", "-o", output_name, path,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                else:
                    proc = await asyncio.create_subprocess_exec(
                        "grim", path,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
        else:
            # X11: scrot
            proc = await asyncio.create_subprocess_exec(
                "scrot", f"--screen={monitor}", path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        if proc.returncode != 0:
            logger.error("screenshot_failed", stderr=stderr.decode()[:200])
            return None

        return path

    except asyncio.TimeoutError:
        logger.error("screenshot_timeout")
        return None
    except Exception as e:
        logger.error("screenshot_exception", error=str(e))
        return None


async def _get_wayland_output(monitor_index: int) -> Optional[str]:
    """Gibt den Wayland Output-Namen für einen Monitor-Index zurück."""
    try:
        if _hyprland:
            proc = await asyncio.create_subprocess_exec(
                "hyprctl", "monitors", "-j",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            import json
            monitors = json.loads(stdout.decode())
            if monitor_index < len(monitors):
                return monitors[monitor_index].get("name")
        else:
            # wlr-randr oder swaymsg als Fallback
            proc = await asyncio.create_subprocess_exec(
                "wlr-randr",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            lines = stdout.decode().splitlines()
            outputs = [l.split()[0] for l in lines if l and not l.startswith(" ")]
            if monitor_index < len(outputs):
                return outputs[monitor_index]
    except Exception as e:
        logger.warning("wayland_output_detection_failed", error=str(e))
    return None


# ── Aktives Fenster ───────────────────────────────────────────────────────

async def get_active_window() -> dict:
    """
    Gibt Titel + App-Name des aktiven Fensters zurück.
    Hyprland > Wayland-generic > X11
    """
    result = {"title": "Unbekannt", "app": "Unbekannt", "workspace": ""}

    try:
        if _hyprland:
            proc = await asyncio.create_subprocess_exec(
                "hyprctl", "activewindow", "-j",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            import json
            data = json.loads(stdout.decode())
            result["title"] = data.get("title", "Unbekannt")
            result["app"] = data.get("class", "Unbekannt")
            result["workspace"] = str(data.get("workspace", {}).get("name", ""))

        elif not _wayland:
            # X11: xdotool
            proc = await asyncio.create_subprocess_exec(
                "xdotool", "getactivewindow", "getwindowname",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            result["title"] = stdout.decode().strip()

    except Exception as e:
        logger.warning("active_window_failed", error=str(e))

    return result


# ── OCR Text-Extraktion ───────────────────────────────────────────────────

async def extract_text_from_screenshot(image_path: str) -> str:
    """
    Extrahiert Text aus einem Screenshot via tesseract OCR.
    Läuft in ThreadPool um den Event-Loop nicht zu blockieren.
    """
    try:
        import pytesseract
        from PIL import Image

        loop = asyncio.get_event_loop()

        def _ocr():
            img = Image.open(image_path)
            # Deutsch + Englisch, PSM 3 = automatische Seiten-Segmentierung
            text = pytesseract.image_to_string(img, lang="deu+eng", config="--psm 3")
            return text.strip()

        text = await loop.run_in_executor(None, _ocr)
        return text

    except ImportError:
        logger.error("pytesseract_not_installed",
                     hint="pip install pytesseract Pillow && sudo pacman -S tesseract tesseract-data-deu")
        return ""
    except Exception as e:
        logger.error("ocr_failed", error=str(e))
        return ""


# ── Haupt-Funktion: Bildschirm analysieren ────────────────────────────────

async def analyze_screen(monitor: int = 0) -> str:
    """
    Komplette Analyse: Screenshot + OCR + aktives Fenster.
    Returns: Beschreibung was SOMA sieht.
    """
    global _last_screenshot

    # Parallel: Screenshot + aktives Fenster
    screenshot_task = asyncio.create_task(take_screenshot(monitor))
    window_task = asyncio.create_task(get_active_window())

    screenshot_path, window_info = await asyncio.gather(screenshot_task, window_task)

    if not screenshot_path:
        return "Ich konnte keinen Screenshot machen. Ist grim/scrot installiert?"

    _last_screenshot = screenshot_path

    # OCR Text extrahieren
    ocr_text = await extract_text_from_screenshot(screenshot_path)

    # Temporäre Datei aufräumen
    try:
        os.unlink(screenshot_path)
    except Exception:
        pass

    # Ergebnis zusammenbauen
    parts = []

    if window_info["title"] != "Unbekannt":
        parts.append(
            f"Aktives Fenster: '{window_info['title']}' "
            f"(App: {window_info['app']}"
            + (f", Workspace: {window_info['workspace']}" if window_info["workspace"] else "")
            + ")"
        )

    if ocr_text:
        # Text kürzen wenn zu lang
        if len(ocr_text) > 1500:
            ocr_text = ocr_text[:1500] + "... [gekürzt]"
        parts.append(f"Sichtbarer Text auf dem Bildschirm:\n{ocr_text}")
    else:
        parts.append("Kein Text auf dem Bildschirm erkannt (evtl. nur Grafiken/Videos).")

    result = "\n\n".join(parts)

    # Ins Memory schreiben (SOMA erinnert sich was sie gesehen hat)
    try:
        from brain_core.memory.integration import get_orchestrator
        orch = get_orchestrator()
        asyncio.create_task(orch.store_interaction(
            user_text=f"SOMA hat Monitor {monitor} analysiert",
            soma_text=result[:500],
            emotion="neutral",
            intent="screen_analysis",
            topic="window_listening",
        ))
    except Exception:
        pass

    return result


# ── Beobachtungs-Modus ────────────────────────────────────────────────────

async def start_observing(interval_sec: int = 30, monitor: int = 0):
    """
    Startet kontinuierliche Bildschirm-Beobachtung.
    SOMA meldet sich wenn sich etwas Wichtiges ändert.
    """
    global _observe_task, _observe_active

    if _observe_active:
        return "Ich beobachte deinen Bildschirm bereits."

    _observe_active = True
    _observe_task = asyncio.create_task(
        _observe_loop(interval_sec, monitor)
    )
    return f"Ich beobachte jetzt deinen Bildschirm alle {interval_sec} Sekunden."


async def stop_observing() -> str:
    global _observe_active, _observe_task
    _observe_active = False
    if _observe_task and not _observe_task.done():
        _observe_task.cancel()
    return "Ich schaue nicht mehr auf deinen Bildschirm."


async def _observe_loop(interval_sec: int, monitor: int):
    """Beobachtungs-Loop — meldet Änderungen proaktiv."""
    last_window = ""

    while _observe_active:
        try:
            window = await get_active_window()
            current = window["title"]

            # Nur melden wenn sich das Fenster geändert hat
            if current != last_window and current != "Unbekannt":
                logger.debug("window_changed", from_=last_window, to=current)
                last_window = current

                # Ins Memory schreiben
                try:
                    from brain_core.memory.integration import get_orchestrator
                    orch = get_orchestrator()
                    asyncio.create_task(orch.store_interaction(
                        user_text=f"Fenster gewechselt zu: {current}",
                        soma_text="window_change_observed",
                        emotion="neutral",
                        intent="window_change",
                        topic="window_listening",
                    ))
                except Exception:
                    pass

            await asyncio.sleep(interval_sec)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("observe_loop_error", error=str(e))
            await asyncio.sleep(interval_sec)