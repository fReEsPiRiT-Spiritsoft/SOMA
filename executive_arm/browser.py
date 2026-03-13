"""
SOMA-AI Browser — Headless Web-Zugriff via Playwright
========================================================
SOMA kann Webseiten lesen, Screenshots machen, Informationen extrahieren.

Aber: SOMA leakt KEINE Daten.
  - Navigieren: JA
  - Lesen: JA
  - Screenshots: JA
  - Formulare ausfuellen: NUR mit PolicyEngine-Check
  - Login irgendwo: SOFT_BLOCK (User muss freigeben)
  - Daten hochladen: HARD_BLOCK (D2 Privacy)

Technik:
  - Playwright async (Chromium headless)
  - Lazy-Init: Browser wird erst beim ersten Aufruf gestartet
  - Auto-Close nach 5 Min Inaktivitaet (RAM sparen)
  - Max 3 gleichzeitige Tabs
  - Jede Navigation durch PolicyEngine

Non-Negotiable:
  - Kein Login auf externen Diensten ohne User-Approval
  - Kein Daten-Upload (Identity Anchor D2)
  - Kein JavaScript-Injection auf fremden Seiten
  - Alle URLs werden geloggt (Audit)
"""

from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

from executive_arm.policy_engine import (
    PolicyEngine,
    ActionRequest,
    ActionType,
)

logger = structlog.get_logger("soma.executive.browser")


# ── Constants ────────────────────────────────────────────────────────────

IDLE_CLOSE_SEC: float = 300.0      # 5 Min → Browser schliessen
MAX_CONCURRENT_TABS: int = 3
DEFAULT_TIMEOUT_MS: int = 15000    # 15s pro Navigation
SCREENSHOT_DIR: str = "data/screenshots"
MAX_CONTENT_LENGTH: int = 30000    # Max 30K Zeichen Text-Extraktion


# ── Browser Result ───────────────────────────────────────────────────────

@dataclass
class BrowserResult:
    """Ergebnis einer Browser-Aktion."""
    url: str = ""
    title: str = ""
    text_content: str = ""         # Extrahierter Text (lesbar)
    screenshot_path: str = ""      # Pfad zum Screenshot
    status_code: int = 0
    duration_ms: float = 0.0
    was_allowed: bool = True
    policy_message: str = ""
    error: str = ""
    timestamp: float = field(default_factory=time.time)


# ══════════════════════════════════════════════════════════════════════════
#  HEADLESS BROWSER — SOMAs Augen im Web
# ══════════════════════════════════════════════════════════════════════════

class HeadlessBrowser:
    """
    Async Playwright-basierter headless Browser.
    
    Features:
      - Lazy-Init (Browser startet erst bei Bedarf)
      - Auto-Shutdown nach Inaktivitaet
      - Policy-Check vor jeder Aktion
      - Text-Extraktion von Webseiten
      - Screenshots (gespeichert in data/screenshots/)
      - Kein Cookie/Session-Persistence (Privacy)
    
    Usage:
        browser = HeadlessBrowser(policy_engine=pe)
        result = await browser.navigate("https://example.com")
        result = await browser.screenshot("https://example.com")
        await browser.close()
    """

    def __init__(
        self,
        policy_engine: PolicyEngine,
        screenshot_dir: str = SCREENSHOT_DIR,
    ):
        self._policy = policy_engine
        self._screenshot_dir = Path(screenshot_dir)
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

        # ── Playwright State (lazy) ──────────────────────────────────
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._initialized = False

        # ── Lifecycle ────────────────────────────────────────────────
        self._last_activity: float = 0.0
        self._idle_task: Optional[asyncio.Task] = None
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_TABS)

        # ── Stats ────────────────────────────────────────────────────
        self._total_navigations: int = 0
        self._total_screenshots: int = 0
        self._denied_count: int = 0

        logger.info("headless_browser_initialized")

    # ══════════════════════════════════════════════════════════════════
    #  LAZY INIT — Browser startet erst wenn noetig
    # ══════════════════════════════════════════════════════════════════

    async def _ensure_browser(self) -> None:
        """Stelle sicher dass der Browser laeuft."""
        if self._initialized and self._browser:
            return

        try:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--disable-extensions",
                    "--disable-background-networking",
                    # Privacy: Keine Telemetrie, kein Tracking
                    "--disable-client-side-phishing-detection",
                    "--disable-component-update",
                    "--disable-default-apps",
                    "--disable-translate",
                ],
            )

            # Context OHNE Cookies/Storage (jede Session frisch)
            self._context = await self._browser.new_context(
                locale="de-DE",
                timezone_id="Europe/Berlin",
                user_agent=(
                    "SOMA-AI/1.0 (Local Ambient OS; +https://soma.local) "
                    "Chromium Headless"
                ),
                viewport={"width": 1280, "height": 720},
                # Kein persistent storage
                storage_state=None,
            )

            self._page = await self._context.new_page()
            self._initialized = True
            self._last_activity = time.monotonic()

            # Idle-Shutdown Timer starten
            if self._idle_task is None:
                self._idle_task = asyncio.create_task(
                    self._idle_shutdown_loop(),
                    name="browser-idle-shutdown",
                )

            logger.info("browser_started", headless=True)

        except ImportError:
            logger.error(
                "playwright_not_installed",
                msg="pip install playwright && playwright install chromium",
            )
            raise
        except Exception as exc:
            logger.error("browser_start_failed", error=str(exc))
            raise

    async def close(self) -> None:
        """Browser sauber herunterfahren."""
        if self._idle_task:
            self._idle_task.cancel()
            try:
                await self._idle_task
            except asyncio.CancelledError:
                pass
            self._idle_task = None

        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

        self._initialized = False
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None

        logger.info("browser_closed")

    async def _idle_shutdown_loop(self) -> None:
        """Schliesse Browser nach Inaktivitaet (RAM sparen)."""
        while True:
            await asyncio.sleep(60.0)
            if (
                self._initialized
                and time.monotonic() - self._last_activity > IDLE_CLOSE_SEC
            ):
                logger.info("browser_idle_shutdown", idle_sec=IDLE_CLOSE_SEC)
                await self.close()
                return

    # ══════════════════════════════════════════════════════════════════
    #  NAVIGATE — Eine URL oeffnen und Text extrahieren
    # ══════════════════════════════════════════════════════════════════

    async def navigate(
        self,
        url: str,
        reason: str = "",
        agent_goal: str = "",
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> BrowserResult:
        """
        Navigiere zu einer URL und extrahiere den Text-Inhalt.
        
        Policy-Check: BROWSER_NAVIGATE (RiskLevel.LOW)
        """
        self._total_navigations += 1

        # ── Policy-Check ─────────────────────────────────────────────
        policy_request = ActionRequest(
            action_type=ActionType.BROWSER_NAVIGATE,
            description=f"Browser: Navigiere zu {url}",
            target=url,
            reason=reason,
            agent_goal=agent_goal,
        )
        policy_result = await self._policy.check(policy_request)

        if not policy_result.allowed:
            self._denied_count += 1
            return BrowserResult(
                url=url,
                was_allowed=False,
                policy_message=policy_result.message,
            )

        # ── Browser starten (lazy) ──────────────────────────────────
        try:
            await self._ensure_browser()
        except Exception as exc:
            return BrowserResult(
                url=url,
                error=f"Browser init failed: {exc}",
            )

        # ── Navigation ──────────────────────────────────────────────
        t0 = time.monotonic()
        async with self._semaphore:
            try:
                response = await self._page.goto(
                    url,
                    timeout=timeout_ms,
                    wait_until="domcontentloaded",
                )

                # Warte kurz fuer dynamischen Content
                await self._page.wait_for_load_state("networkidle", timeout=5000)

                title = await self._page.title()

                # Text extrahieren (body-Text, ohne Tags)
                text = await self._page.evaluate("""
                    () => {
                        // Entferne Script/Style Tags
                        const scripts = document.querySelectorAll('script, style, nav, footer, header');
                        scripts.forEach(el => el.remove());
                        
                        // Hauptinhalt extrahieren
                        const main = document.querySelector('main, article, [role="main"], .content, #content');
                        const target = main || document.body;
                        return target ? target.innerText.trim() : '';
                    }
                """)

                # Truncate
                if len(text) > MAX_CONTENT_LENGTH:
                    text = text[:MAX_CONTENT_LENGTH] + "\n... [truncated]"

                duration_ms = (time.monotonic() - t0) * 1000
                self._last_activity = time.monotonic()

                status = response.status if response else 0

                logger.info(
                    "browser_navigated",
                    url=url[:80],
                    status=status,
                    title=title[:40],
                    text_len=len(text),
                    ms=f"{duration_ms:.0f}",
                )

                return BrowserResult(
                    url=url,
                    title=title,
                    text_content=text,
                    status_code=status,
                    duration_ms=duration_ms,
                )

            except Exception as exc:
                duration_ms = (time.monotonic() - t0) * 1000
                logger.error(
                    "browser_navigate_failed",
                    url=url[:80],
                    error=str(exc),
                )
                return BrowserResult(
                    url=url,
                    error=str(exc),
                    duration_ms=duration_ms,
                )

    # ══════════════════════════════════════════════════════════════════
    #  SCREENSHOT — Visueller Snapshot einer Seite
    # ══════════════════════════════════════════════════════════════════

    async def screenshot(
        self,
        url: str,
        reason: str = "",
        agent_goal: str = "",
        full_page: bool = False,
    ) -> BrowserResult:
        """
        Navigiere zu URL und mache einen Screenshot.
        
        Screenshot wird in data/screenshots/ gespeichert.
        """
        self._total_screenshots += 1

        # ── Policy-Check ─────────────────────────────────────────────
        policy_request = ActionRequest(
            action_type=ActionType.BROWSER_SCREENSHOT,
            description=f"Browser: Screenshot von {url}",
            target=url,
            reason=reason,
            agent_goal=agent_goal,
        )
        policy_result = await self._policy.check(policy_request)

        if not policy_result.allowed:
            self._denied_count += 1
            return BrowserResult(
                url=url,
                was_allowed=False,
                policy_message=policy_result.message,
            )

        # ── Navigieren ──────────────────────────────────────────────
        nav_result = await self.navigate(url, reason, agent_goal)
        if nav_result.error:
            return nav_result

        # ── Screenshot ──────────────────────────────────────────────
        try:
            ts = time.strftime("%Y%m%d_%H%M%S")
            safe_name = "".join(c if c.isalnum() else "_" for c in url[:50])
            filename = f"screenshot_{safe_name}_{ts}.png"
            filepath = self._screenshot_dir / filename

            await self._page.screenshot(
                path=str(filepath),
                full_page=full_page,
            )

            self._last_activity = time.monotonic()

            logger.info("browser_screenshot", url=url[:80], path=str(filepath))

            return BrowserResult(
                url=nav_result.url,
                title=nav_result.title,
                text_content=nav_result.text_content,
                screenshot_path=str(filepath),
                status_code=nav_result.status_code,
                duration_ms=nav_result.duration_ms,
            )

        except Exception as exc:
            logger.error("browser_screenshot_failed", error=str(exc))
            return BrowserResult(
                url=url,
                error=f"Screenshot failed: {exc}",
            )

    # ══════════════════════════════════════════════════════════════════
    #  INTERACT — Formular-Interaktion (EINGESCHRAENKT)
    # ══════════════════════════════════════════════════════════════════

    async def click(
        self,
        selector: str,
        reason: str = "",
        agent_goal: str = "",
    ) -> BrowserResult:
        """
        Klicke auf ein Element (nach Policy-Check).
        
        Policy: BROWSER_INTERACT (RiskLevel.MEDIUM)
        """
        if not self._initialized or not self._page:
            return BrowserResult(error="Browser not initialized")

        policy_request = ActionRequest(
            action_type=ActionType.BROWSER_INTERACT,
            description=f"Browser: Klick auf '{selector}'",
            target=self._page.url,
            parameters={"selector": selector, "action": "click"},
            reason=reason,
            agent_goal=agent_goal,
        )
        policy_result = await self._policy.check(policy_request)

        if not policy_result.allowed:
            self._denied_count += 1
            return BrowserResult(
                url=self._page.url if self._page else "",
                was_allowed=False,
                policy_message=policy_result.message,
            )

        try:
            await self._page.click(selector, timeout=5000)
            await self._page.wait_for_load_state("networkidle", timeout=5000)
            self._last_activity = time.monotonic()

            title = await self._page.title()
            return BrowserResult(
                url=self._page.url,
                title=title,
            )
        except Exception as exc:
            return BrowserResult(error=f"Click failed: {exc}")

    async def extract_links(self) -> list[dict]:
        """Extrahiere alle Links der aktuellen Seite."""
        if not self._initialized or not self._page:
            return []

        try:
            links = await self._page.evaluate("""
                () => {
                    const anchors = document.querySelectorAll('a[href]');
                    return Array.from(anchors).slice(0, 50).map(a => ({
                        text: a.innerText.trim().substring(0, 100),
                        href: a.href,
                    }));
                }
            """)
            return links
        except Exception:
            return []

    # ══════════════════════════════════════════════════════════════════
    #  STATS
    # ══════════════════════════════════════════════════════════════════

    @property
    def is_active(self) -> bool:
        return self._initialized and self._browser is not None

    @property
    def stats(self) -> dict:
        return {
            "is_active": self.is_active,
            "total_navigations": self._total_navigations,
            "total_screenshots": self._total_screenshots,
            "denied": self._denied_count,
            "idle_since_sec": (
                time.monotonic() - self._last_activity
                if self._last_activity > 0
                else 0
            ),
        }
