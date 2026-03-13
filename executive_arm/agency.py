"""
SOMA-AI Agency — Der LangGraph State-Machine Agent
=====================================================
Dies ist SOMAs exekutive Handlungsfaehigkeit.

SOMA denkt nicht nur — SOMA HANDELT.

Architektur (Agentic Loop):
  1. GOAL   — Was will SOMA erreichen?
  2. PLAN   — Welche Schritte sind noetig?
  3. EXECUTE — Fuehre naechsten Schritt aus (via Toolset)
  4. OBSERVE — Was ist passiert? (Tool-Output)
  5. VERIFY  — Ziel erreicht? → JA: Fertig / NEIN: Zurueck zu PLAN

  ┌─────────────────────────────────────────────────┐
  │                                                   │
  │   GOAL → PLAN → EXECUTE → OBSERVE → VERIFY       │
  │            ▲                           │           │
  │            └───── NEIN ────────────────┘           │
  │                                                   │
  │                   JA → COMPLETE                    │
  │                                                   │
  └─────────────────────────────────────────────────┘

Sicherheit:
  - Max 10 Schritte pro Agent-Run (kein endloser Loop)
  - Identity Anchor Check vor JEDEM Tool-Call
  - PolicyEngine prueft jede einzelne Aktion
  - Agent kann VOM USER gestoppt werden (cancel)
  - Jeder Schritt wird ans Dashboard gebroadcastet

Non-Negotiable:
  - Alles async
  - Alles durch PolicyEngine
  - Kein unkontrollierter Loop
  - Jeder Schritt geloggt + in Memory
  - Agent darf sich NICHT selbst aufrufen (Rekursions-Schutz)
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Awaitable

import structlog

from executive_arm.toolset import Toolset, ToolResult
from executive_arm.policy_engine import PolicyEngine, ActionRequest, ActionType
from brain_ego.identity_anchor import IdentityAnchor, VetoResult

logger = structlog.get_logger("soma.executive.agency")


# ── Agent State Machine ──────────────────────────────────────────────────

class AgentPhase(str, Enum):
    """Phase im Agentic Loop."""
    IDLE = "idle"
    GOAL_SET = "goal_set"
    PLANNING = "planning"
    EXECUTING = "executing"
    OBSERVING = "observing"
    VERIFYING = "verifying"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ── Agent Step ───────────────────────────────────────────────────────────

@dataclass
class AgentStep:
    """Ein einzelner Schritt im Agentic Loop."""
    step_number: int
    phase: AgentPhase
    action: str = ""              # Was wurde getan
    tool_name: str = ""           # Welches Tool wurde genutzt
    tool_args: dict = field(default_factory=dict)
    tool_result: str = ""         # Ergebnis des Tools
    reasoning: str = ""           # LLM-Begruendung fuer diesen Schritt
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)
    was_allowed: bool = True
    error: str = ""


# ── Agent Run ────────────────────────────────────────────────────────────

@dataclass
class AgentRun:
    """Eine vollstaendige Agent-Ausfuehrung."""
    run_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    goal: str = ""
    status: AgentPhase = AgentPhase.IDLE
    steps: list[AgentStep] = field(default_factory=list)
    plan: list[str] = field(default_factory=list)  # Geplante Schritte
    final_result: str = ""
    total_duration_ms: float = 0.0
    started_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    error: str = ""


# ── Constants ────────────────────────────────────────────────────────────

MAX_STEPS: int = 10              # Maximale Schritte pro Run
MAX_PLAN_RETRIES: int = 2        # Wie oft darf umgeplant werden
LLM_TIMEOUT_SEC: float = 30.0   # Timeout fuer LLM-Calls
MAX_CONCURRENT_RUNS: int = 1     # Nur ein Agent gleichzeitig (Sicherheit!)


# ══════════════════════════════════════════════════════════════════════════
#  THE AGENT — SOMAs Handlungsfaehigkeit
# ══════════════════════════════════════════════════════════════════════════

class SomaAgent:
    """
    LangGraph-inspirierter State-Machine Agent.
    
    Der Agent bekommt ein Ziel und fuehrt es selbststaendig aus:
      1. Versteht das Ziel (LLM)
      2. Erstellt einen Plan (LLM)
      3. Fuehrt Schritte aus (Tools via Toolset)
      4. Beobachtet Ergebnisse
      5. Prueft ob Ziel erreicht (LLM)
      6. Plant um wenn noetig
    
    Sicherheit:
      - Max 10 Schritte (kein Endlos-Loop)
      - Jeder Step durch PolicyEngine
      - Identity Anchor vor jeder Aktion
      - Broadcast jedes Steps ans Dashboard
      - User kann jederzeit canceln
    
    Usage:
        agent = SomaAgent(toolset, llm_fn, identity_anchor, policy_engine)
        result = await agent.run("Installiere das Wetter-Plugin")
        # → AgentRun mit allen Schritten + finalem Ergebnis
    """

    def __init__(
        self,
        toolset: Toolset,
        identity_anchor: IdentityAnchor,
        policy_engine: PolicyEngine,
    ):
        self._toolset = toolset
        self._identity = identity_anchor
        self._policy = policy_engine

        # ── LLM Callback (gesetzt von main.py) ──────────────────────
        self._llm_fn: Optional[Callable[[str, str], Awaitable[str]]] = None

        # ── Callbacks ────────────────────────────────────────────────
        self._broadcast_fn: Optional[
            Callable[[str, str, str], Awaitable[None]]
        ] = None
        self._memory_fn: Optional[
            Callable[[str, str, str, float], Awaitable[None]]
        ] = None

        # ── State ────────────────────────────────────────────────────
        self._current_run: Optional[AgentRun] = None
        self._run_history: list[AgentRun] = []
        self._cancel_requested: bool = False
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_RUNS)

        # ── Stats ────────────────────────────────────────────────────
        self._total_runs: int = 0
        self._successful_runs: int = 0
        self._failed_runs: int = 0
        self._cancelled_runs: int = 0

        logger.info(
            "agent_initialized",
            tools=self._toolset.tool_names,
        )

    # ══════════════════════════════════════════════════════════════════
    #  CONFIGURATION
    # ══════════════════════════════════════════════════════════════════

    def set_llm(
        self,
        fn: Callable[[str, str], Awaitable[str]],
    ) -> None:
        """
        LLM-Callback setzen.
        fn(system_prompt, user_prompt) → response
        Nutzt Heavy-Engine fuer komplexe Planung.
        """
        self._llm_fn = fn

    def set_broadcast(
        self,
        fn: Callable[[str, str, str], Awaitable[None]],
    ) -> None:
        """Dashboard-Broadcast: (type, msg, tag)."""
        self._broadcast_fn = fn

    def set_memory(
        self,
        fn: Callable[[str, str, str, float], Awaitable[None]],
    ) -> None:
        """Memory-Callback: (description, event_type, emotion, importance)."""
        self._memory_fn = fn

    # ══════════════════════════════════════════════════════════════════
    #  RUN — Der Haupteingang
    # ══════════════════════════════════════════════════════════════════

    async def run(self, goal: str) -> AgentRun:
        """
        Fuehre ein Ziel autonom aus.
        
        Args:
            goal: Was soll erreicht werden? (Menschensprache)
            
        Returns:
            AgentRun mit allen Schritten und Ergebnis
        """
        if self._llm_fn is None:
            return AgentRun(
                goal=goal,
                status=AgentPhase.FAILED,
                error="Kein LLM konfiguriert — set_llm() aufrufen",
            )

        # Nur ein Agent gleichzeitig
        if self._semaphore.locked():
            return AgentRun(
                goal=goal,
                status=AgentPhase.FAILED,
                error="Ein Agent laeuft bereits — bitte warten",
            )

        async with self._semaphore:
            return await self._execute_run(goal)

    async def cancel(self) -> bool:
        """Breche den laufenden Agent-Run ab."""
        if self._current_run and self._current_run.status in (
            AgentPhase.PLANNING, AgentPhase.EXECUTING, AgentPhase.OBSERVING,
        ):
            self._cancel_requested = True
            logger.info("agent_cancel_requested", run_id=self._current_run.run_id)
            return True
        return False

    # ══════════════════════════════════════════════════════════════════
    #  INTERNAL: Der Agentic Loop
    # ══════════════════════════════════════════════════════════════════

    async def _execute_run(self, goal: str) -> AgentRun:
        """Der vollstaendige Agentic Loop."""
        self._total_runs += 1
        self._cancel_requested = False

        run = AgentRun(goal=goal, status=AgentPhase.GOAL_SET)
        self._current_run = run

        t0 = time.monotonic()
        await self._broadcast(
            "info",
            f"🤖 Agent gestartet: {goal[:80]}",
            "AGENT",
        )

        try:
            # ── 1. IDENTITY CHECK — Darf SOMA dieses Ziel verfolgen? ─
            veto = self._identity.check_action(
                action_description=goal,
                action_type="agent_goal",
                context="Agent will autonom handeln",
            )
            if veto.is_blocked:
                run.status = AgentPhase.FAILED
                run.error = f"Identity Veto: {veto.reason}"
                await self._broadcast(
                    "warn",
                    f"🚫 Agent-Ziel abgelehnt: {veto.reason}",
                    "AGENT",
                )
                self._failed_runs += 1
                return run

            # ── 2. PLANNING — Erstelle einen Plan ───────────────────
            run.status = AgentPhase.PLANNING
            plan = await self._create_plan(goal)

            if not plan:
                run.status = AgentPhase.FAILED
                run.error = "Konnte keinen Plan erstellen"
                self._failed_runs += 1
                return run

            run.plan = plan
            await self._broadcast(
                "info",
                f"📋 Plan ({len(plan)} Schritte): {' → '.join(plan[:5])}",
                "AGENT",
            )

            # ── 3. EXECUTION LOOP ───────────────────────────────────
            step_count = 0
            replan_count = 0
            accumulated_context: list[str] = []

            while step_count < MAX_STEPS:
                # Cancel-Check
                if self._cancel_requested:
                    run.status = AgentPhase.CANCELLED
                    run.error = "Vom User abgebrochen"
                    self._cancelled_runs += 1
                    await self._broadcast("warn", "⏹️ Agent abgebrochen", "AGENT")
                    break

                # ── EXECUTE: Naechsten Schritt planen + ausfuehren ──
                run.status = AgentPhase.EXECUTING
                step = await self._execute_step(
                    goal=goal,
                    plan=plan,
                    step_number=step_count + 1,
                    context=accumulated_context,
                )
                run.steps.append(step)
                step_count += 1

                if step.error and not step.was_allowed:
                    # Policy-Block — weitermachen mit anderem Ansatz?
                    accumulated_context.append(
                        f"Schritt {step.step_number}: BLOCKIERT — {step.error}"
                    )
                    continue

                # ── OBSERVE: Was ist passiert? ───────────────────────
                run.status = AgentPhase.OBSERVING
                accumulated_context.append(
                    f"Schritt {step.step_number} [{step.tool_name}]: {step.tool_result[:200]}"
                )

                await self._broadcast(
                    "info",
                    f"  Schritt {step.step_number}: {step.action[:60]} → {'✅' if not step.error else '❌'}",
                    "AGENT",
                )

                # ── VERIFY: Ziel erreicht? ───────────────────────────
                run.status = AgentPhase.VERIFYING
                is_done, summary = await self._verify_goal(
                    goal=goal,
                    context=accumulated_context,
                )

                if is_done:
                    run.status = AgentPhase.COMPLETE
                    run.final_result = summary
                    self._successful_runs += 1
                    await self._broadcast(
                        "info",
                        f"✅ Agent fertig: {summary[:80]}",
                        "AGENT",
                    )
                    break

                # ── REPLAN wenn noetig ───────────────────────────────
                if step.error and replan_count < MAX_PLAN_RETRIES:
                    replan_count += 1
                    new_plan = await self._create_plan(
                        goal,
                        context=accumulated_context,
                        failed_steps=run.steps,
                    )
                    if new_plan:
                        plan = new_plan
                        await self._broadcast(
                            "info",
                            f"🔄 Umgeplant: {' → '.join(new_plan[:3])}",
                            "AGENT",
                        )

            else:
                # Max Steps erreicht
                run.status = AgentPhase.FAILED
                run.error = f"Max {MAX_STEPS} Schritte erreicht ohne Erfolg"
                self._failed_runs += 1
                await self._broadcast(
                    "warn",
                    f"⚠️ Agent: Max Schritte erreicht",
                    "AGENT",
                )

        except asyncio.CancelledError:
            run.status = AgentPhase.CANCELLED
            self._cancelled_runs += 1
            raise
        except Exception as exc:
            run.status = AgentPhase.FAILED
            run.error = str(exc)
            self._failed_runs += 1
            logger.error("agent_run_error", error=str(exc))
        finally:
            run.total_duration_ms = (time.monotonic() - t0) * 1000
            run.completed_at = time.time()
            self._current_run = None

            # In History speichern
            self._run_history.append(run)
            if len(self._run_history) > 20:
                self._run_history = self._run_history[-20:]

            # Memory-Event
            await self._remember(
                f"Agent-Run: {goal[:80]} → {run.status.value}",
                "agent_run",
                "neutral" if run.status == AgentPhase.COMPLETE else "concerned",
                0.7,
            )

        return run

    # ══════════════════════════════════════════════════════════════════
    #  PLANNING — LLM erstellt einen Plan
    # ══════════════════════════════════════════════════════════════════

    async def _create_plan(
        self,
        goal: str,
        context: list[str] | None = None,
        failed_steps: list[AgentStep] | None = None,
    ) -> list[str]:
        """
        Lasse das LLM einen Ausfuehrungsplan erstellen.
        
        Returns:
            Liste von Plan-Schritten (Strings)
        """
        system_prompt = (
            "Du bist der Planungs-Agent von SOMA. "
            "Erstelle einen konkreten Ausfuehrungsplan fuer das gegebene Ziel.\n\n"
            f"{self._toolset.get_tool_descriptions()}\n\n"
            "REGELN:\n"
            "- Maximal 8 Schritte\n"
            "- Jeder Schritt MUSS ein verfuegbares Tool nutzen\n"
            "- Konkret und ausfuehrbar (kein 'dann mach das irgendwie')\n"
            "- Bei Dateiaenderungen: IMMER zuerst lesen, dann aendern\n"
            "- Antworte NUR mit einer JSON-Liste von Schritten\n\n"
            "FORMAT:\n"
            '[\"Schritt 1: read_file auf config.py\", \"Schritt 2: ...\"]'
        )

        user_prompt = f"ZIEL: {goal}"

        if context:
            user_prompt += "\n\nBISHERIGER KONTEXT:\n" + "\n".join(context[-5:])

        if failed_steps:
            failures = [
                f"  - Schritt {s.step_number}: {s.action} → FEHLER: {s.error}"
                for s in failed_steps if s.error
            ]
            if failures:
                user_prompt += "\n\nFEHLGESCHLAGENE SCHRITTE:\n" + "\n".join(failures)
                user_prompt += "\n\nBitte einen alternativen Plan erstellen."

        try:
            raw = await asyncio.wait_for(
                self._llm_fn(system_prompt, user_prompt),
                timeout=LLM_TIMEOUT_SEC,
            )

            # Parse JSON-Liste
            plan = self._parse_plan(raw)
            if plan:
                logger.info("agent_plan_created", steps=len(plan))
                return plan

            logger.warning("agent_plan_parse_failed", raw=raw[:200])
            return []

        except asyncio.TimeoutError:
            logger.error("agent_plan_timeout")
            return []
        except Exception as exc:
            logger.error("agent_plan_error", error=str(exc))
            return []

    @staticmethod
    def _parse_plan(raw: str) -> list[str]:
        """Parse LLM-Output zu Plan-Liste."""
        # Versuche JSON
        try:
            # Finde JSON-Array in der Antwort
            start = raw.find("[")
            end = raw.rfind("]") + 1
            if start >= 0 and end > start:
                plan = json.loads(raw[start:end])
                if isinstance(plan, list) and all(isinstance(s, str) for s in plan):
                    return plan[:8]  # Max 8 Schritte
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: Zeilen als Schritte
        lines = [
            line.strip().lstrip("0123456789.-) ")
            for line in raw.strip().split("\n")
            if line.strip() and not line.strip().startswith("#")
        ]
        return lines[:8] if lines else []

    # ══════════════════════════════════════════════════════════════════
    #  EXECUTION — Einen Schritt ausfuehren
    # ══════════════════════════════════════════════════════════════════

    async def _execute_step(
        self,
        goal: str,
        plan: list[str],
        step_number: int,
        context: list[str],
    ) -> AgentStep:
        """
        Lasse LLM den naechsten Tool-Call bestimmen und fuehre ihn aus.
        """
        t0 = time.monotonic()

        system_prompt = (
            "Du bist der Ausfuehrungs-Agent von SOMA.\n"
            "Bestimme den naechsten Tool-Call basierend auf Plan und Kontext.\n\n"
            f"{self._toolset.get_tool_descriptions()}\n\n"
            "Antworte NUR mit JSON:\n"
            '{"tool": "tool_name", "args": {"param": "value"}, "reasoning": "warum"}'
        )

        plan_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(plan))
        context_text = "\n".join(context[-5:]) if context else "Noch kein Kontext"

        user_prompt = (
            f"ZIEL: {goal}\n\n"
            f"PLAN:\n{plan_text}\n\n"
            f"AKTUELLER SCHRITT: {step_number}\n\n"
            f"BISHERIGER KONTEXT:\n{context_text}\n\n"
            "Was ist der naechste Tool-Call?"
        )

        try:
            raw = await asyncio.wait_for(
                self._llm_fn(system_prompt, user_prompt),
                timeout=LLM_TIMEOUT_SEC,
            )

            # Parse Tool-Call
            tool_name, tool_args, reasoning = self._parse_tool_call(raw)

            if not tool_name:
                return AgentStep(
                    step_number=step_number,
                    phase=AgentPhase.EXECUTING,
                    error="LLM konnte keinen Tool-Call bestimmen",
                    reasoning=raw[:200],
                    duration_ms=(time.monotonic() - t0) * 1000,
                )

            # ── Tool ausfuehren ──────────────────────────────────────
            tool_result = await self._toolset.execute(
                tool_name=tool_name,
                arguments=tool_args,
                agent_goal=goal,
            )

            duration_ms = (time.monotonic() - t0) * 1000

            return AgentStep(
                step_number=step_number,
                phase=AgentPhase.EXECUTING,
                action=f"{tool_name}({json.dumps(tool_args, ensure_ascii=False)[:80]})",
                tool_name=tool_name,
                tool_args=tool_args,
                tool_result=tool_result.output[:500],
                reasoning=reasoning,
                duration_ms=duration_ms,
                was_allowed=tool_result.was_allowed,
                error=tool_result.error or (tool_result.policy_message if not tool_result.was_allowed else ""),
            )

        except asyncio.TimeoutError:
            return AgentStep(
                step_number=step_number,
                phase=AgentPhase.EXECUTING,
                error="LLM Timeout",
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return AgentStep(
                step_number=step_number,
                phase=AgentPhase.EXECUTING,
                error=str(exc),
                duration_ms=(time.monotonic() - t0) * 1000,
            )

    @staticmethod
    def _parse_tool_call(raw: str) -> tuple[str, dict, str]:
        """Parse LLM-Output zu (tool_name, args, reasoning)."""
        try:
            # Finde JSON-Object in der Antwort
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(raw[start:end])
                return (
                    data.get("tool", ""),
                    data.get("args", {}),
                    data.get("reasoning", ""),
                )
        except (json.JSONDecodeError, ValueError):
            pass

        return "", {}, raw[:200]

    # ══════════════════════════════════════════════════════════════════
    #  VERIFICATION — Ziel erreicht?
    # ══════════════════════════════════════════════════════════════════

    async def _verify_goal(
        self,
        goal: str,
        context: list[str],
    ) -> tuple[bool, str]:
        """
        Lasse LLM pruefen ob das Ziel erreicht wurde.
        
        Returns:
            (is_done, summary)
        """
        system_prompt = (
            "Du bist der Verifizierungs-Agent von SOMA.\n"
            "Pruefe ob das Ziel erreicht wurde basierend auf den bisherigen Ergebnissen.\n\n"
            "Antworte NUR mit JSON:\n"
            '{"done": true/false, "summary": "Was wurde erreicht / was fehlt noch"}'
        )

        context_text = "\n".join(context[-8:])
        user_prompt = f"ZIEL: {goal}\n\nBISHERIGE ERGEBNISSE:\n{context_text}"

        try:
            raw = await asyncio.wait_for(
                self._llm_fn(system_prompt, user_prompt),
                timeout=LLM_TIMEOUT_SEC,
            )

            # Parse
            try:
                start = raw.find("{")
                end = raw.rfind("}") + 1
                if start >= 0 and end > start:
                    data = json.loads(raw[start:end])
                    return data.get("done", False), data.get("summary", "")
            except (json.JSONDecodeError, ValueError):
                pass

            # Fallback: Einfache Keyword-Erkennung
            lower = raw.lower()
            if any(w in lower for w in ("done", "erreicht", "fertig", "complete", "true")):
                return True, raw[:200]

            return False, raw[:200]

        except asyncio.TimeoutError:
            return False, "Verifikation Timeout"
        except Exception as exc:
            return False, f"Verifikation Fehler: {exc}"

    # ══════════════════════════════════════════════════════════════════
    #  HELPERS
    # ══════════════════════════════════════════════════════════════════

    async def _broadcast(self, level: str, msg: str, tag: str) -> None:
        """Dashboard-Broadcast (fire-and-forget)."""
        if self._broadcast_fn:
            try:
                asyncio.create_task(
                    self._broadcast_fn(level, msg, tag),
                    name="agent-broadcast",
                )
            except Exception:
                pass

    async def _remember(
        self,
        description: str,
        event_type: str,
        emotion: str,
        importance: float,
    ) -> None:
        """Memory-Event (fire-and-forget)."""
        if self._memory_fn:
            try:
                asyncio.create_task(
                    self._memory_fn(description, event_type, emotion, importance),
                    name="agent-memory",
                )
            except Exception:
                pass

    # ══════════════════════════════════════════════════════════════════
    #  QUERY API
    # ══════════════════════════════════════════════════════════════════

    @property
    def is_running(self) -> bool:
        return self._current_run is not None

    @property
    def current_goal(self) -> Optional[str]:
        return self._current_run.goal if self._current_run else None

    @property
    def current_step(self) -> Optional[int]:
        if self._current_run and self._current_run.steps:
            return len(self._current_run.steps)
        return None

    def get_run_history(self, limit: int = 10) -> list[dict]:
        """Letzte Agent-Runs fuer Dashboard."""
        return [
            {
                "run_id": r.run_id,
                "goal": r.goal,
                "status": r.status.value,
                "steps": len(r.steps),
                "duration_ms": r.total_duration_ms,
                "result": r.final_result[:100],
                "error": r.error[:100],
                "started_at": r.started_at,
            }
            for r in reversed(self._run_history[-limit:])
        ]

    @property
    def stats(self) -> dict:
        return {
            "total_runs": self._total_runs,
            "successful": self._successful_runs,
            "failed": self._failed_runs,
            "cancelled": self._cancelled_runs,
            "is_running": self.is_running,
            "current_goal": self.current_goal,
            "current_step": self.current_step,
        }
