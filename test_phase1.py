#!/usr/bin/env python3
"""Phase 1 Integration Test — Memory as the Single Brain (SSOT)."""

import asyncio
import sys

def test_imports():
    """Test all new imports."""
    from brain_core.memory.salience_filter import SalienceFilter, SalienceScore
    from brain_core.memory.diary_writer import DiaryWriter
    from brain_core.memory.integration import (
        init_memory_system, set_consolidation_llm, set_diary_llm,
        after_response, store_system_event, get_orchestrator,
    )
    from brain_core.memory import SalienceFilter as SF2, DiaryWriter as DW2
    assert SF2 is SalienceFilter
    assert DW2 is DiaryWriter
    print("✅ All imports OK")


def test_salience_filter():
    """Test SalienceFilter logic."""
    from brain_core.memory.salience_filter import SalienceFilter

    sf = SalienceFilter()

    # Important event with explicit marker → salient
    s = sf.evaluate(
        user_text="Merk dir: Meine Tochter hat heute Geburtstag!",
        soma_text="Oh wie schön! Alles Gute zum Geburtstag! Ich habe mir das gemerkt!",
        emotion="happy",
        arousal=0.8,
        valence=0.9,
    )
    print(f"  Birthday: score={s.total:.2f} salient={s.is_salient} highly={s.is_highly_salient}")
    assert s.is_salient, f"Birthday with 'merk dir' should be salient, got {s.total}"

    # Trivial → NOT salient
    s2 = sf.evaluate("ja", "OK", "neutral", 0.1, 0.0, "")
    print(f"  Trivial:  score={s2.total:.2f} salient={s2.is_salient}")
    assert not s2.is_salient, f"'ja/OK' should NOT be salient, got {s2.total}"

    # Force-salient (phone)
    s3 = sf.force_salient("phone_call")
    print(f"  Forced:   score={s3.total:.2f} salient={s3.is_salient}")
    assert s3.is_salient

    print("✅ SalienceFilter logic OK")


async def test_memory_init():
    """Test memory system init + orchestrator has new components."""
    from brain_core.memory.integration import init_memory_system, get_orchestrator

    orch = await init_memory_system()
    assert orch is not None
    assert hasattr(orch, 'salience')
    assert hasattr(orch, 'diary')
    print(f"✅ MemoryOrchestrator online with Salience + Diary")

    stats = await orch.get_memory_stats()
    print(f"  Stats keys: {list(stats.keys())}")
    assert "diary_entries" in stats
    print("✅ Stats contain diary metrics")


async def test_after_response():
    """Test the updated after_response with arousal/valence/stress."""
    from brain_core.memory.integration import init_memory_system, after_response

    await init_memory_system()

    # Should NOT crash with the new signature
    await after_response(
        user_text="Wie ist das Wetter?",
        soma_text="Draußen scheint die Sonne, 22 Grad.",
        emotion="neutral",
        arousal=0.2,
        valence=0.3,
        stress=0.1,
        topic="wetter",
    )
    print("✅ after_response with new params OK")


async def test_store_system_event():
    """Test store_system_event for phone/plugin events."""
    from brain_core.memory.integration import init_memory_system, store_system_event

    await init_memory_system()

    await store_system_event(
        event_type="phone_call",
        description="Testanruf von Patrick",
    )
    print("✅ store_system_event OK")


def main():
    print("═" * 50)
    print(" SOMA Phase 1 — Integration Test")
    print("═" * 50)

    test_imports()
    test_salience_filter()
    asyncio.run(_run_async_tests())

    print()
    print("═" * 50)
    print(" ✅ ALL PHASE 1 TESTS PASSED")
    print("═" * 50)


async def _run_async_tests():
    """All async tests in one event loop to share orchestrator state."""
    await test_memory_init()
    await test_after_response()
    await test_store_system_event()

    print()
    print("═" * 50)
    print(" ✅ ALL PHASE 1 TESTS PASSED")
    print("═" * 50)


if __name__ == "__main__":
    main()
