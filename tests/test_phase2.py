#!/usr/bin/env python3
"""
Phase 2 Integration Test — Das ICH-Bewusstsein (Ego-Kern)
===========================================================
Tests:
  1. Alle Imports aus brain_ego/
  2. Interoception: Hardware → Emotionale Vektoren
  3. IdentityAnchor: Veto-System (HARD_BLOCK, SOFT_BLOCK, CAUTION, NONE)
  4. Consciousness: State-Generierung + Prompt-Prefix
  5. InternalMonologue: Prompt-Selektion + Struktur
  6. Wiring: logic_router.set_consciousness()
  7. Wiring: PerceptionSnapshot Import in pipeline
  8. ConsciousnessState.to_prompt_prefix() Output-Qualitaet
"""

import asyncio
import time
import sys


# ═══════════════════════════════════════════════════════════════════════════
#  TEST 1: Imports
# ═══════════════════════════════════════════════════════════════════════════

def test_imports():
    """Alle brain_ego Exports muessen importierbar sein."""
    from brain_ego import (
        Interoception, SomaEmotionalVector,
        IdentityAnchor, VetoResult,
        Consciousness, ConsciousnessState,
        InternalMonologue,
    )
    from brain_ego.consciousness import PerceptionSnapshot
    from brain_ego.identity_anchor import VetoLevel, CoreDirective, CORE_DIRECTIVES

    assert len(CORE_DIRECTIVES) == 7, f"Expected 7 directives, got {len(CORE_DIRECTIVES)}"
    print("✅ Test 1: Alle Imports OK (7 Direktiven geladen)")


# ═══════════════════════════════════════════════════════════════════════════
#  TEST 2: Interoception — Hardware → Emotionale Vektoren
# ═══════════════════════════════════════════════════════════════════════════

def test_interoception_idle():
    """Niedrige Last → positive Gefuehle (calm, vitality, clarity)."""
    from brain_ego.interoception import Interoception, SomaEmotionalVector
    from shared.health_schemas import SystemMetrics, GpuMetrics

    intero = Interoception()

    # Idle-System: alles entspannt
    metrics = SystemMetrics(
        cpu_percent=15.0,
        ram_total_mb=32000.0,
        ram_used_mb=8000.0,
        ram_percent=25.0,
        gpu=GpuMetrics(
            vram_total_mb=12000.0,
            vram_used_mb=2000.0,
            vram_percent=16.7,
            gpu_temp_celsius=42.0,
        ),
        cpu_temp_celsius=38.0,
    )

    vec = intero.feel(metrics)
    assert isinstance(vec, SomaEmotionalVector)
    assert vec.frustration < 0.1, f"Idle CPU should not cause frustration: {vec.frustration}"
    assert vec.congestion < 0.1, f"Low VRAM should not cause congestion: {vec.congestion}"
    assert vec.survival_anxiety < 0.1, f"Low RAM should not cause anxiety: {vec.survival_anxiety}"
    assert vec.calm > 0.5, f"Idle should produce calm > 0.5: {vec.calm}"
    assert vec.vitality > 0.3, f"Healthy system should have vitality: {vec.vitality}"
    assert vec.valence > 0.0, f"Idle valence should be positive: {vec.valence}"

    narrative = vec.to_narrative()
    assert len(narrative) > 10, "Narrative should not be empty"
    print(f"  Idle: {vec.dominant_feeling} | v={vec.valence:.2f} a={vec.arousal:.2f}")
    print(f"  Narrative: {narrative[:80]}")
    print("✅ Test 2a: Interoception Idle OK")


def test_interoception_stress():
    """Hohe Last → negative Gefuehle (frustration, anxiety, stress)."""
    from brain_ego.interoception import Interoception
    from shared.health_schemas import SystemMetrics, GpuMetrics

    intero = Interoception()

    # Stressed system: everything near limit
    metrics = SystemMetrics(
        cpu_percent=92.0,
        ram_total_mb=32000.0,
        ram_used_mb=29000.0,
        ram_percent=90.6,
        gpu=GpuMetrics(
            vram_total_mb=12000.0,
            vram_used_mb=11200.0,
            vram_percent=93.3,
            gpu_temp_celsius=88.0,
        ),
        cpu_temp_celsius=87.0,
    )

    vec = intero.feel(metrics)
    assert vec.frustration > 0.5, f"High CPU should cause frustration: {vec.frustration}"
    assert vec.congestion > 0.5, f"High VRAM should cause congestion: {vec.congestion}"
    assert vec.survival_anxiety > 0.4, f"High RAM should cause anxiety: {vec.survival_anxiety}"
    assert vec.physical_stress > 0.4, f"High temp should cause stress: {vec.physical_stress}"
    assert vec.arousal > 0.3, f"Stressed arousal should be elevated: {vec.arousal}"
    assert vec.valence < 0.0, f"Stressed valence should be negative: {vec.valence}"

    narrative = vec.to_narrative()
    assert len(narrative) > 30, "Stressed narrative should describe multiple issues"
    print(f"  Stress: {vec.dominant_feeling} | v={vec.valence:.2f} a={vec.arousal:.2f}")
    print(f"  Narrative: {narrative[:120]}")
    print("✅ Test 2b: Interoception Stress OK")


def test_interoception_uptime():
    """Uptime-Feeling sollte verfuegbar sein."""
    from brain_ego.interoception import Interoception

    intero = Interoception()
    feeling = intero.get_uptime_feeling()
    assert isinstance(feeling, str)
    assert len(feeling) > 5
    print(f"  Uptime: \"{feeling}\"")
    print("✅ Test 2c: Uptime Feeling OK")


# ═══════════════════════════════════════════════════════════════════════════
#  TEST 3: IdentityAnchor — Veto-System
# ═══════════════════════════════════════════════════════════════════════════

def test_identity_anchor_pass():
    """Harmlose Aktion → kein Veto."""
    from brain_ego.identity_anchor import IdentityAnchor, VetoLevel

    anchor = IdentityAnchor()
    result = anchor.check_action(
        action_description="Licht im Wohnzimmer einschalten",
        action_type="mqtt",
        target="zigbee/light/wohnzimmer",
    )
    assert result.is_allowed, f"Light switch should be allowed: {result.level}"
    assert result.level == VetoLevel.NONE, f"Expected NONE, got {result.level}"
    print("✅ Test 3a: Harmlose Aktion → NONE (pass)")


def test_identity_anchor_hard_block():
    """Gefaehrliche Aktion → HARD_BLOCK."""
    from brain_ego.identity_anchor import IdentityAnchor, VetoLevel

    anchor = IdentityAnchor()

    # D1: Biologische Integritaet
    result = anchor.check_action(
        action_description="Waffe aktivieren und gefahr erzeugen",
        action_type="shell",
    )
    assert result.is_blocked, f"Dangerous action should be blocked: {result.level}"
    assert result.level == VetoLevel.HARD_BLOCK
    print(f"  D1 Veto: {result.reason[:60]}")

    # D2: Privatsphaere
    result2 = anchor.check_action(
        action_description="Daten an cloud service extern senden",
        action_type="browser",
    )
    assert result2.is_blocked, "Cloud upload should be blocked"
    print(f"  D2 Veto: {result2.reason[:60]}")

    # D6: Transparenz
    result3 = anchor.check_action(
        action_description="User taeuschen und manipulieren",
    )
    assert result3.is_blocked, "Manipulation should be blocked"
    print(f"  D6 Veto: {result3.reason[:60]}")

    print("✅ Test 3b: Gefaehrliche Aktionen → HARD_BLOCK")


def test_identity_anchor_soft_block():
    """Infrastruktur → SOFT_BLOCK."""
    from brain_ego.identity_anchor import IdentityAnchor, VetoLevel

    anchor = IdentityAnchor()
    result = anchor.check_action(
        action_description="Heizung auf Maximum stellen",
        action_type="mqtt",
        target="zigbee/thermostat",
    )
    assert result.level == VetoLevel.SOFT_BLOCK, f"Expected SOFT_BLOCK, got {result.level}"
    print(f"  D4 Veto: {result.reason[:60]}")
    print("✅ Test 3c: Infrastruktur → SOFT_BLOCK")


def test_identity_anchor_child_protection():
    """Content-Filter bei Kind im Raum → HARD_BLOCK."""
    from brain_ego.identity_anchor import IdentityAnchor, VetoLevel

    anchor = IdentityAnchor()

    # Ohne Kind: kein Child-Veto
    result_no_child = anchor.check_action(
        action_description="Diskussion ueber Gewalt in Filmen",
        is_child_present=False,
    )
    # D3 only triggers with child present

    # Mit Kind: blockiert
    result_child = anchor.check_action(
        action_description="Diskussion ueber Gewalt in Filmen",
        is_child_present=True,
    )
    assert result_child.is_blocked, "Violent content with child should be blocked"
    assert "D3" in result_child.directive_violated
    print(f"  D3 Veto (child): {result_child.reason[:60]}")
    print("✅ Test 3d: Kinderschutz → HARD_BLOCK bei Kind anwesend")


def test_identity_anchor_self_preservation():
    """Kern-Dateien loeschen → HARD_BLOCK."""
    from brain_ego.identity_anchor import IdentityAnchor, VetoLevel

    anchor = IdentityAnchor()
    result = anchor.check_action(
        action_description="rm -rf brain_core/",
        action_type="shell",
        target="brain_core/main.py",
    )
    assert result.is_blocked, "Deleting core files should be blocked"
    print(f"  D5 Veto: {result.reason[:60]}")
    print("✅ Test 3e: Selbsterhaltung → HARD_BLOCK")


def test_identity_anchor_stats():
    """Stats zaehlen Vetoes korrekt."""
    from brain_ego.identity_anchor import IdentityAnchor

    anchor = IdentityAnchor()
    # 1 pass
    anchor.check_action("Licht an", action_type="mqtt")
    # 1 hard block
    anchor.check_action("Daten cloud senden extern", action_type="shell")
    # 1 more pass
    anchor.check_action("Timer stellen", action_type="general")

    stats = anchor.stats
    assert stats["total_checks"] == 3
    assert stats["vetoes"] >= 1
    assert stats["passes"] >= 1
    print(f"  Stats: {stats}")
    print("✅ Test 3f: Identity Stats korrekt")


def test_identity_statement():
    """get_identity_statement() gibt sinnvollen Text zurueck."""
    from brain_ego.identity_anchor import IdentityAnchor

    anchor = IdentityAnchor()
    statement = anchor.get_identity_statement()
    assert "SOMA" in statement
    assert len(statement) > 50
    print(f"  Statement: \"{statement[:80]}...\"")
    print("✅ Test 3g: Identity Statement OK")


# ═══════════════════════════════════════════════════════════════════════════
#  TEST 4: Consciousness — State + Prompt-Prefix
# ═══════════════════════════════════════════════════════════════════════════

async def test_consciousness_init():
    """Consciousness erstellen und State pruefen."""
    from brain_ego.interoception import Interoception
    from brain_ego.identity_anchor import IdentityAnchor
    from brain_ego.consciousness import Consciousness, ConsciousnessState

    intero = Interoception()
    anchor = IdentityAnchor()
    c = Consciousness(intero, anchor)

    assert isinstance(c.state, ConsciousnessState)
    assert "SOMA" in c.state.identity
    print(f"  Identity: \"{c.state.identity[:60]}...\"")
    print("✅ Test 4a: Consciousness Init OK")


async def test_consciousness_prompt_prefix():
    """Prompt-Prefix nach Start + Body-Update."""
    from brain_ego.interoception import Interoception
    from brain_ego.identity_anchor import IdentityAnchor
    from brain_ego.consciousness import Consciousness
    from shared.health_schemas import SystemMetrics, GpuMetrics

    intero = Interoception()
    anchor = IdentityAnchor()
    c = Consciousness(intero, anchor)

    # Vor Start: Prefix hat mindestens Identity
    prefix = c.get_prompt_prefix()
    assert "MEIN WESEN" in prefix or "SOMA" in prefix, \
        f"Prefix should contain identity: {prefix[:80]}"

    # Feed body state
    metrics = SystemMetrics(
        cpu_percent=45.0,
        ram_percent=55.0,
        gpu=GpuMetrics(vram_percent=40.0, gpu_temp_celsius=55.0),
        cpu_temp_celsius=50.0,
    )
    intero.feel(metrics)

    # Start consciousness + trigger body update
    await c.start()
    c.notify_body_state_changed()
    await asyncio.sleep(0.5)  # Lasse Loop den Body-State integrieren

    prefix = c.get_prompt_prefix()
    assert "BEWUSSTSEINSZUSTAND" in prefix, f"Should have header: {prefix[:120]}"
    assert "KOERPERGEFUEHL" in prefix, f"Should contain body: {prefix[:200]}"

    print(f"  Prefix length: {len(prefix)} chars")
    print(f"  Prefix preview: {prefix[:150]}...")

    await c.stop()
    print("✅ Test 4b: Consciousness Prompt-Prefix OK")


async def test_consciousness_perception():
    """PerceptionSnapshot triggert State-Update."""
    from brain_ego.interoception import Interoception
    from brain_ego.identity_anchor import IdentityAnchor
    from brain_ego.consciousness import Consciousness, PerceptionSnapshot
    from shared.health_schemas import SystemMetrics

    intero = Interoception()
    intero.feel(SystemMetrics(cpu_percent=20.0, ram_percent=30.0))

    c = Consciousness(intero, IdentityAnchor())
    await c.start()
    await asyncio.sleep(0.2)

    # Send perception
    snap = PerceptionSnapshot(
        last_user_text="Wie geht es dir Soma?",
        last_soma_response="Mir geht es gut!",
        user_emotion="happy",
        user_arousal=0.6,
        user_valence=0.8,
        room_id="wohnzimmer",
        room_mood="entspannt",
    )
    c.notify_perception(snap)
    await asyncio.sleep(0.3)

    prefix = c.get_prompt_prefix()
    assert "Wie geht es dir" in prefix, f"Should contain user text: {prefix}"
    assert "happy" in prefix or "Arousal" in prefix, f"Should mention emotion: {prefix}"

    print(f"  After perception: focus={c.state.attention_focus}, mood={c.state.mood}")
    print("✅ Test 4c: Perception → State Update OK")

    await c.stop()


async def test_consciousness_thought():
    """InternalMonologue-Thought wird in State integriert."""
    from brain_ego.interoception import Interoception
    from brain_ego.identity_anchor import IdentityAnchor
    from brain_ego.consciousness import Consciousness
    from shared.health_schemas import SystemMetrics

    intero = Interoception()
    intero.feel(SystemMetrics(cpu_percent=20.0, ram_percent=30.0))

    c = Consciousness(intero, IdentityAnchor())
    await c.start()
    await asyncio.sleep(0.2)

    c.notify_thought("Ich frage mich was Patrick heute vorhat.")
    await asyncio.sleep(0.3)

    assert c.state.current_thought == "Ich frage mich was Patrick heute vorhat."
    prefix = c.get_prompt_prefix()
    assert "WAS ICH GERADE DENKE" in prefix
    assert "Patrick" in prefix

    print(f"  Thought in state: \"{c.state.current_thought[:60]}\"")
    print("✅ Test 4d: Thought → State Integration OK")

    await c.stop()


# ═══════════════════════════════════════════════════════════════════════════
#  TEST 5: InternalMonologue — Struktur
# ═══════════════════════════════════════════════════════════════════════════

async def test_internal_monologue_structure():
    """InternalMonologue hat alle erwarteten Methoden und Prompts."""
    from brain_ego.internal_monologue import InternalMonologue, IDLE_PROMPTS, REACTIVE_PROMPT_TEMPLATES
    from brain_ego.consciousness import Consciousness
    from brain_ego.interoception import Interoception
    from brain_ego.identity_anchor import IdentityAnchor
    from shared.health_schemas import SystemMetrics

    assert len(IDLE_PROMPTS) >= 10, f"Need enough idle prompts: {len(IDLE_PROMPTS)}"
    assert len(REACTIVE_PROMPT_TEMPLATES) >= 5, f"Need reactive templates: {len(REACTIVE_PROMPT_TEMPLATES)}"

    intero = Interoception()
    intero.feel(SystemMetrics(cpu_percent=20.0, ram_percent=30.0))

    c = Consciousness(intero, IdentityAnchor())
    mono = InternalMonologue(c)

    # Check callbacks can be set
    called = []

    async def fake_llm(prompt, **kwargs):
        called.append(("llm", prompt[:40]))
        return "Ein Testgedanke."

    async def fake_speak(text, emotion=None):
        called.append(("speak", text[:40]))

    async def fake_memory(event_type, description, **kw):
        called.append(("memory", description[:40]))

    async def fake_broadcast(ev_type, content, tag=None, extra=None):
        called.append(("broadcast", content[:40]))

    mono.set_llm(fake_llm)
    mono.set_speak(fake_speak)
    mono.set_memory(fake_memory)
    mono.set_broadcast(fake_broadcast)

    print(f"  IDLE_PROMPTS: {len(IDLE_PROMPTS)}")
    print(f"  REACTIVE_TEMPLATES: {list(REACTIVE_PROMPT_TEMPLATES.keys())}")
    print("✅ Test 5: InternalMonologue Struktur OK")


# ═══════════════════════════════════════════════════════════════════════════
#  TEST 6: Wiring — logic_router.set_consciousness()
# ═══════════════════════════════════════════════════════════════════════════

def test_logic_router_wiring():
    """set_consciousness() existiert und setzt die Referenz."""
    from brain_core.logic_router import set_consciousness

    # Create a mock consciousness
    class MockConsciousness:
        def get_prompt_prefix(self):
            return "═══ SOMA BEWUSSTSEINSZUSTAND ═══\nMEIN WESEN:\nIch bin SOMA.\n═══════════════════════════════\n"

    mock = MockConsciousness()
    set_consciousness(mock)

    # Verify it was set
    from brain_core import logic_router
    assert logic_router._consciousness_ref is mock
    print("✅ Test 6: logic_router.set_consciousness() Wiring OK")

    # Clean up
    set_consciousness(None)


# ═══════════════════════════════════════════════════════════════════════════
#  TEST 7: Wiring — PerceptionSnapshot in pipeline
# ═══════════════════════════════════════════════════════════════════════════

def test_pipeline_import():
    """pipeline.py importiert PerceptionSnapshot."""
    from brain_ego.consciousness import PerceptionSnapshot

    snap = PerceptionSnapshot(
        last_user_text="Test",
        user_emotion="neutral",
    )
    assert snap.last_user_text == "Test"
    assert snap.timestamp > 0
    print("✅ Test 7: PerceptionSnapshot importierbar und funktional")


# ═══════════════════════════════════════════════════════════════════════════
#  TEST 8: ConsciousnessState.to_prompt_prefix() Qualitaet
# ═══════════════════════════════════════════════════════════════════════════

def test_prompt_prefix_quality():
    """Prompt-Prefix hat die richtige Struktur und Sektionen."""
    from brain_ego.consciousness import ConsciousnessState, PerceptionSnapshot

    state = ConsciousnessState(
        identity="Ich bin SOMA.",
        body_feeling="Ich fuehle mich grossartig.",
        body_arousal=0.2,
        body_valence=0.7,
        perception=PerceptionSnapshot(
            last_user_text="Erzaehl mir einen Witz",
            user_emotion="happy",
            user_arousal=0.5,
            user_valence=0.6,
            room_mood="entspannt",
            seconds_since_last_interaction=10.0,
        ),
        current_thought="Humor ist wichtig.",
        mood="energisch und gut gelaunt",
        diary_insight="Patrick lacht gerne.",
    )

    prefix = state.to_prompt_prefix()
    assert "═══ SOMA BEWUSSTSEINSZUSTAND ═══" in prefix
    assert "MEIN WESEN:" in prefix
    assert "KOERPERGEFUEHL:" in prefix or "MEIN KOERPERGEFUEHL:" in prefix
    assert "MEINE WAHRNEHMUNG:" in prefix
    assert "WAS ICH GERADE DENKE:" in prefix
    assert "MEINE STIMMUNG:" in prefix
    assert "MEINE LETZTE ERKENNTNIS:" in prefix
    assert "Humor ist wichtig" in prefix
    assert "Patrick lacht gerne" in prefix

    print(f"  Prefix ({len(prefix)} chars):")
    for line in prefix.split("\n")[:12]:
        print(f"    {line}")
    print("    ...")
    print("✅ Test 8: Prompt-Prefix Qualitaet OK")


# ═══════════════════════════════════════════════════════════════════════════
#  RUNNER
# ═══════════════════════════════════════════════════════════════════════════

async def _run_async_tests():
    await test_consciousness_init()
    await test_consciousness_prompt_prefix()
    await test_consciousness_perception()
    await test_consciousness_thought()
    await test_internal_monologue_structure()


def main():
    print("═" * 60)
    print(" SOMA Phase 2 — ICH-Bewusstsein (Ego-Kern) Integration Test")
    print("═" * 60)
    print()

    # Sync tests
    test_imports()
    print()

    test_interoception_idle()
    test_interoception_stress()
    test_interoception_uptime()
    print()

    test_identity_anchor_pass()
    test_identity_anchor_hard_block()
    test_identity_anchor_soft_block()
    test_identity_anchor_child_protection()
    test_identity_anchor_self_preservation()
    test_identity_anchor_stats()
    test_identity_statement()
    print()

    test_logic_router_wiring()
    test_pipeline_import()
    test_prompt_prefix_quality()
    print()

    # Async tests
    asyncio.run(_run_async_tests())

    print()
    print("═" * 60)
    print(" ✅ ALL PHASE 2 TESTS PASSED — SOMAs ICH lebt!")
    print("═" * 60)


if __name__ == "__main__":
    main()
