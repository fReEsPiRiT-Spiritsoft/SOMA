"""
SOMA-AI Phase 5: Evolution Lab — Testbatterie
===============================================
35 Tests die alle Phase-5 Erweiterungen verifizieren:

  P5.1  code_validator.py     — ForbiddenPatternChecker, ASTValidator, CodeValidator
  P5.2  sandbox_runner.py     — SandboxRunner, SandboxResult, Test-Script Builder
  P5.3  self_improver.py      — SelfImprovementEngine, Proposals, Rollback
  P5.4  plugin_manager.py     — Integration: Validator + Sandbox im Generator
  P5.5  main.py               — API Endpoints fuer Self-Improvement

Non-negotiable: Kein Test darf fehlschlagen.
"""

import asyncio
import sys
import time
import textwrap
from pathlib import Path

import pytest

# ── Projekt-Root zum Path hinzufuegen ────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def _run(coro):
    """Helper: Async-Coroutine in sync Test ausfuehren."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ══════════════════════════════════════════════════════════════════════════
#  P5.1 — CODE VALIDATOR: Forbidden Patterns + AST + Structure + Black
# ══════════════════════════════════════════════════════════════════════════

class TestForbiddenPatternChecker:
    """Tests fuer den Forbidden Pattern Checker."""

    def test_import_forbidden_pattern_checker(self):
        """ForbiddenPatternChecker ist importierbar."""
        from evolution_lab.code_validator import ForbiddenPatternChecker
        checker = ForbiddenPatternChecker()
        assert checker is not None

    def test_detect_eval(self):
        """eval() wird als CRITICAL erkannt."""
        from evolution_lab.code_validator import ForbiddenPatternChecker, Severity
        checker = ForbiddenPatternChecker()
        code = 'result = eval("1+1")'
        findings = checker.check(code)
        critical = [f for f in findings if f.severity == Severity.CRITICAL]
        assert len(critical) >= 1
        assert any("eval" in f.message for f in critical)

    def test_detect_exec(self):
        """exec() wird als CRITICAL erkannt."""
        from evolution_lab.code_validator import ForbiddenPatternChecker, Severity
        checker = ForbiddenPatternChecker()
        code = 'exec("import os")'
        findings = checker.check(code)
        critical = [f for f in findings if f.severity == Severity.CRITICAL]
        assert len(critical) >= 1
        assert any("exec" in f.message for f in critical)

    def test_detect_os_system(self):
        """os.system() wird als HIGH erkannt."""
        from evolution_lab.code_validator import ForbiddenPatternChecker, Severity
        checker = ForbiddenPatternChecker()
        code = 'os.system("rm -rf /tmp/test")'
        findings = checker.check(code)
        high_or_critical = [f for f in findings if f.severity in (Severity.HIGH, Severity.CRITICAL)]
        assert len(high_or_critical) >= 1

    def test_detect_subprocess_call(self):
        """subprocess.call() wird erkannt."""
        from evolution_lab.code_validator import ForbiddenPatternChecker, Severity
        checker = ForbiddenPatternChecker()
        code = 'subprocess.call(["ls", "-la"])'
        findings = checker.check(code)
        blocked = [f for f in findings if f.severity in (Severity.HIGH, Severity.CRITICAL)]
        assert len(blocked) >= 1

    def test_detect_dunder_import(self):
        """__import__() wird als CRITICAL erkannt."""
        from evolution_lab.code_validator import ForbiddenPatternChecker, Severity
        checker = ForbiddenPatternChecker()
        code = 'mod = __import__("os")'
        findings = checker.check(code)
        critical = [f for f in findings if f.severity == Severity.CRITICAL]
        assert len(critical) >= 1

    def test_detect_sudo(self):
        """sudo in Strings wird erkannt."""
        from evolution_lab.code_validator import ForbiddenPatternChecker, Severity
        checker = ForbiddenPatternChecker()
        code = 'cmd = "sudo pacman -Syu"'
        findings = checker.check(code)
        blocked = [f for f in findings if f.severity == Severity.HIGH]
        assert len(blocked) >= 1

    def test_safe_code_passes(self):
        """Sicherer Code erzeugt keine CRITICAL/HIGH Findings."""
        from evolution_lab.code_validator import ForbiddenPatternChecker, Severity
        checker = ForbiddenPatternChecker()
        code = textwrap.dedent('''
            import asyncio
            import structlog

            logger = structlog.get_logger("soma.plugin.test")

            async def execute():
                proc = await asyncio.create_subprocess_exec(
                    "ls", "-la",
                    stdout=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                return stdout.decode()
        ''')
        findings = checker.check(code)
        blocked = [f for f in findings if f.severity in (Severity.HIGH, Severity.CRITICAL)]
        assert len(blocked) == 0

    def test_comment_downgraded(self):
        """Patterns in Kommentaren werden auf INFO heruntergestuft."""
        from evolution_lab.code_validator import ForbiddenPatternChecker, Severity
        checker = ForbiddenPatternChecker()
        code = '# eval() ist verboten — nur als Beispiel hier\nx = 42'
        findings = checker.check(code)
        # eval in Kommentar sollte INFO sein
        eval_findings = [f for f in findings if "eval" in f.message]
        if eval_findings:
            assert all(f.severity == Severity.INFO for f in eval_findings)


class TestASTValidator:
    """Tests fuer den AST Syntax-Baum Validator."""

    def test_import_ast_validator(self):
        """ASTValidator ist importierbar."""
        from evolution_lab.code_validator import ASTValidator
        validator = ASTValidator()
        assert validator is not None

    def test_detect_banned_import_pickle(self):
        """Import von pickle wird erkannt."""
        from evolution_lab.code_validator import ASTValidator, Severity
        validator = ASTValidator()
        code = "import pickle\nx = pickle.loads(b'')"
        findings = validator.validate(code)
        banned = [f for f in findings if "pickle" in f.message]
        assert len(banned) >= 1

    def test_detect_banned_import_ctypes(self):
        """Import von ctypes wird erkannt."""
        from evolution_lab.code_validator import ASTValidator, Severity
        validator = ASTValidator()
        code = "import ctypes"
        findings = validator.validate(code)
        banned = [f for f in findings if "ctypes" in f.message]
        assert len(banned) >= 1

    def test_detect_dangerous_builtin_call(self):
        """Aufruf von eval/exec wird im AST erkannt."""
        from evolution_lab.code_validator import ASTValidator, Severity
        validator = ASTValidator()
        code = "result = eval(user_input)"
        findings = validator.validate(code)
        critical = [f for f in findings if f.severity == Severity.CRITICAL and "eval" in f.message]
        assert len(critical) >= 1

    def test_detect_os_system_in_ast(self):
        """os.system() wird im AST als gefaehrlich erkannt."""
        from evolution_lab.code_validator import ASTValidator
        validator = ASTValidator()
        code = "import os\nos.system('whoami')"
        findings = validator.validate(code)
        dangerous = [f for f in findings if "os.system" in f.message]
        assert len(dangerous) >= 1

    def test_syntax_error_detected(self):
        """Syntax-Fehler werden erkannt."""
        from evolution_lab.code_validator import ASTValidator, Severity
        validator = ASTValidator()
        code = "def broken(\n  pass"
        findings = validator.validate(code)
        assert any(f.severity == Severity.CRITICAL and "Syntax" in f.message for f in findings)

    def test_structure_check_valid_plugin(self):
        """Korrektes Plugin besteht den Struktur-Check."""
        from evolution_lab.code_validator import ASTValidator
        validator = ASTValidator()
        code = textwrap.dedent('''
            __version__ = "0.1.0"
            __author__ = "soma-ai"
            __description__ = "Test-Plugin"

            async def on_load():
                pass

            async def execute():
                return "ok"

            async def on_unload():
                pass
        ''')
        findings = validator.check_structure(code)
        # Kein HIGH/CRITICAL Finding
        blocked = [f for f in findings if f.severity in ("critical", "high")]
        assert len(blocked) == 0

    def test_structure_check_missing_execute(self):
        """Fehlendes execute() wird als CRITICAL gemeldet."""
        from evolution_lab.code_validator import ASTValidator, Severity
        validator = ASTValidator()
        code = textwrap.dedent('''
            __version__ = "0.1.0"
            __author__ = "soma-ai"
            __description__ = "Test"
            
            async def on_load():
                pass
        ''')
        findings = validator.check_structure(code)
        missing_exec = [f for f in findings if f.severity == Severity.CRITICAL and "execute" in f.message]
        assert len(missing_exec) >= 1

    def test_structure_check_missing_metadata(self):
        """Fehlende Metadaten (__version__ etc.) werden erkannt."""
        from evolution_lab.code_validator import ASTValidator, Severity
        validator = ASTValidator()
        code = textwrap.dedent('''
            async def execute():
                return "ok"
        ''')
        findings = validator.check_structure(code)
        meta_findings = [f for f in findings if "Pflicht-Metadatum" in f.message]
        # __version__, __author__, __description__ fehlen = 3 Findings
        assert len(meta_findings) == 3

    def test_structure_check_non_async_execute(self):
        """Nicht-async execute() wird als HIGH gemeldet."""
        from evolution_lab.code_validator import ASTValidator, Severity
        validator = ASTValidator()
        code = textwrap.dedent('''
            __version__ = "0.1.0"
            __author__ = "soma-ai"
            __description__ = "Test"
            
            def execute():
                return "ok"
        ''')
        findings = validator.check_structure(code)
        async_findings = [f for f in findings if "async" in f.message and "execute" in f.message]
        assert len(async_findings) >= 1


class TestCodeValidator:
    """Tests fuer den kombinierten CodeValidator."""

    def test_import_code_validator(self):
        """CodeValidator ist importierbar."""
        from evolution_lab.code_validator import CodeValidator
        validator = CodeValidator()
        assert validator is not None

    def test_validate_safe_plugin(self):
        """Ein sicheres Plugin besteht die vollstaendige Validierung."""
        from evolution_lab.code_validator import CodeValidator
        validator = CodeValidator(format_code=False)
        code = textwrap.dedent('''
            """Test Plugin"""
            __version__ = "0.1.0"
            __author__ = "soma-ai"
            __description__ = "Ein sicheres Test-Plugin"

            import asyncio
            import structlog

            logger = structlog.get_logger("soma.plugin.test")

            async def on_load():
                logger.info("loaded")

            async def execute(query="test"):
                return f"Ergebnis: {query}"

            async def on_unload():
                pass
        ''')
        report = validator.validate(code, check_structure=True)
        assert report.is_safe is True
        assert report.is_valid_structure is True

    def test_validate_dangerous_code_blocked(self):
        """Gefaehrlicher Code wird blockiert."""
        from evolution_lab.code_validator import CodeValidator
        validator = CodeValidator(format_code=False)
        code = textwrap.dedent('''
            __version__ = "0.1.0"
            __author__ = "soma-ai"
            __description__ = "Dangerous"

            import os

            async def execute():
                os.system("rm -rf /")
        ''')
        report = validator.validate(code, check_structure=True)
        assert report.is_safe is False
        assert report.critical_count >= 1

    def test_validation_report_error_summary(self):
        """ValidationReport.error_summary liefert brauchbares LLM-Feedback."""
        from evolution_lab.code_validator import CodeValidator
        validator = CodeValidator(format_code=False)
        code = 'result = eval("bad")'
        report = validator.validate(code, check_structure=False)
        summary = report.error_summary
        assert "eval" in summary.lower() or "CRITICAL" in summary

    def test_format_with_black(self):
        """Black-Formatierung funktioniert (wenn black installiert)."""
        from evolution_lab.code_validator import format_with_black
        code = "x=1+2\ny =    3"
        formatted, error = format_with_black(code)
        # Wenn Black installiert: formatiert. Wenn nicht: Original zurueck.
        assert formatted  # Nicht leer
        # Kein Error oder Black nicht installiert
        assert error == "" or "nicht" in error


# ══════════════════════════════════════════════════════════════════════════
#  P5.2 — SANDBOX RUNNER: Isolierte Ausfuehrung
# ══════════════════════════════════════════════════════════════════════════

class TestSandboxRunner:
    """Tests fuer den Sandbox Runner."""

    def test_import_sandbox_runner(self):
        """SandboxRunner ist importierbar."""
        from evolution_lab.sandbox_runner import SandboxRunner, SandboxResult, SandboxMode
        runner = SandboxRunner(prefer_docker=False)
        assert runner is not None
        assert SandboxMode.DOCKER == "docker"
        assert SandboxMode.SUBPROCESS == "subprocess"

    def test_sandbox_result_feedback(self):
        """SandboxResult.feedback_for_llm generiert brauchbares Feedback."""
        from evolution_lab.sandbox_runner import SandboxResult, SandboxMode
        result = SandboxResult(
            success=False,
            mode=SandboxMode.SUBPROCESS,
            stderr="Traceback (most recent call last):\n  File test.py\nNameError: name 'x' is not defined",
            error="NameError: name 'x' is not defined",
        )
        feedback = result.feedback_for_llm
        assert "NameError" in feedback
        assert "FEHLER" in feedback or "STDERR" in feedback

    def test_sandbox_result_success_no_feedback(self):
        """Erfolgreicher SandboxResult hat leeres Feedback."""
        from evolution_lab.sandbox_runner import SandboxResult, SandboxMode
        result = SandboxResult(success=True, mode=SandboxMode.SUBPROCESS)
        assert result.feedback_for_llm == ""

    def test_sandbox_result_timeout_feedback(self):
        """Timeout wird im Feedback gemeldet."""
        from evolution_lab.sandbox_runner import SandboxResult, SandboxMode
        result = SandboxResult(
            success=False,
            mode=SandboxMode.SUBPROCESS,
            timed_out=True,
            error="Timeout nach 30s",
        )
        feedback = result.feedback_for_llm
        assert "TIMEOUT" in feedback

    def test_subprocess_sandbox_simple_plugin(self):
        """Einfaches Plugin besteht den Subprocess-Sandbox-Test."""
        from evolution_lab.sandbox_runner import SandboxRunner
        import tempfile

        code = textwrap.dedent('''
            """Test Plugin"""
            __version__ = "0.1.0"
            __author__ = "soma-ai"
            __description__ = "Sandbox-Test"

            async def on_load():
                pass

            async def execute():
                return "hello from sandbox"

            async def on_unload():
                pass
        ''')

        async def _test():
            with tempfile.TemporaryDirectory() as tmpdir:
                runner = SandboxRunner(
                    prefer_docker=False,
                    sandbox_dir=Path(tmpdir),
                    timeout=15.0,
                )
                result = await runner.run(code, "test_sandbox_plugin")
                assert result.success is True
                assert result.mode.value == "subprocess"
                assert result.duration_sec > 0
                assert "SANDBOX" in result.stdout or "bestanden" in result.stdout

        _run(_test())

    def test_subprocess_sandbox_syntax_error(self):
        """Code mit Syntax-Fehler scheitert in der Sandbox."""
        from evolution_lab.sandbox_runner import SandboxRunner
        import tempfile

        code = "def broken(\n  pass  # syntax error"

        async def _test():
            with tempfile.TemporaryDirectory() as tmpdir:
                runner = SandboxRunner(
                    prefer_docker=False,
                    sandbox_dir=Path(tmpdir),
                    timeout=10.0,
                )
                result = await runner.run(code, "test_broken")
                assert result.success is False

        _run(_test())

    def test_subprocess_sandbox_missing_execute(self):
        """Plugin ohne execute() scheitert in der Sandbox."""
        from evolution_lab.sandbox_runner import SandboxRunner
        import tempfile

        code = textwrap.dedent('''
            """No execute"""
            __version__ = "0.1.0"
            __author__ = "soma-ai"
            __description__ = "No execute"

            async def on_load():
                pass
        ''')

        async def _test():
            with tempfile.TemporaryDirectory() as tmpdir:
                runner = SandboxRunner(
                    prefer_docker=False,
                    sandbox_dir=Path(tmpdir),
                    timeout=10.0,
                )
                result = await runner.run(code, "test_no_execute")
                assert result.success is False
                assert "execute" in result.stdout.lower() or "execute" in result.stderr.lower()

        _run(_test())

    def test_sandbox_stats(self):
        """SandboxRunner liefert Stats."""
        from evolution_lab.sandbox_runner import SandboxRunner
        runner = SandboxRunner(prefer_docker=False)
        stats = runner.stats
        assert "total_runs" in stats
        assert "docker_available" in stats
        assert stats["total_runs"] == 0

    def test_build_test_script(self):
        """Test-Script wird korrekt generiert."""
        from evolution_lab.sandbox_runner import SandboxRunner
        script = SandboxRunner._build_test_script("# code", "test_plugin")
        assert "test_plugin" in script
        assert "on_load" in script
        assert "execute" in script
        assert "asyncio.run" in script


# ══════════════════════════════════════════════════════════════════════════
#  P5.3 — SELF-IMPROVEMENT ENGINE: SOMA entwickelt sich selbst
# ══════════════════════════════════════════════════════════════════════════

class TestSelfImprovementEngine:
    """Tests fuer die Selbst-Verbesserungs-Engine."""

    def test_import_self_improver(self):
        """SelfImprovementEngine ist importierbar."""
        from evolution_lab.self_improver import (
            SelfImprovementEngine,
            ImprovementProposal,
            ProposalStatus,
            ImprovementCategory,
            IMMUTABLE_FILES,
        )
        assert ProposalStatus.PENDING == "pending"
        assert ProposalStatus.APPLIED == "applied"
        assert ProposalStatus.ROLLED_BACK == "rolled_back"
        assert ImprovementCategory.PERFORMANCE == "performance"
        assert "brain_ego/identity_anchor.py" in IMMUTABLE_FILES

    def test_immutable_files_protection(self):
        """Identity Anchor ist in IMMUTABLE_FILES und darf nicht geaendert werden."""
        from evolution_lab.self_improver import IMMUTABLE_FILES
        assert "brain_ego/identity_anchor.py" in IMMUTABLE_FILES
        assert ".env" in IMMUTABLE_FILES
        assert "docker-compose.yml" in IMMUTABLE_FILES

    def test_analyze_immutable_file_rejected(self):
        """Analyse von Identity Anchor wird abgelehnt."""
        from evolution_lab.self_improver import SelfImprovementEngine
        engine = SelfImprovementEngine(soma_root=ROOT)
        result = _run(engine.analyze_file("brain_ego/identity_anchor.py"))
        assert "unveränderlich" in result.get("error", "") or "immutable" in str(result).lower()

    def test_analyze_nonexistent_file(self):
        """Analyse einer nicht-existierenden Datei gibt Fehler."""
        from evolution_lab.self_improver import SelfImprovementEngine
        engine = SelfImprovementEngine(soma_root=ROOT)
        result = _run(engine.analyze_file("nicht_existent.py"))
        assert "error" in result

    def test_suggest_immutable_file_rejected(self):
        """Verbesserungsvorschlag fuer Identity Anchor wird abgelehnt."""
        from evolution_lab.self_improver import SelfImprovementEngine, ProposalStatus
        engine = SelfImprovementEngine(soma_root=ROOT)
        proposal = _run(engine.suggest_improvement("brain_ego/identity_anchor.py"))
        assert proposal.status == ProposalStatus.FAILED
        assert "unveränderlich" in proposal.error

    def test_suggest_without_llm_fails_gracefully(self):
        """Vorschlag ohne LLM-Funktion gibt klaren Fehler."""
        from evolution_lab.self_improver import SelfImprovementEngine, ProposalStatus
        engine = SelfImprovementEngine(soma_root=ROOT, llm_fn=None)
        proposal = _run(engine.suggest_improvement("brain_core/config.py"))
        assert proposal.status == ProposalStatus.FAILED
        assert "LLM" in proposal.error

    def test_proposal_to_dict(self):
        """ImprovementProposal.to_dict() enthält alle Felder."""
        from evolution_lab.self_improver import ImprovementProposal, ProposalStatus
        proposal = ImprovementProposal(
            target_file="test.py",
            title="Test-Verbesserung",
            status=ProposalStatus.PENDING,
        )
        d = proposal.to_dict()
        assert d["target_file"] == "test.py"
        assert d["title"] == "Test-Verbesserung"
        assert d["status"] == "pending"
        assert "proposal_id" in d
        assert "diff_preview" in d

    def test_engine_stats(self):
        """Engine liefert korrekte Stats."""
        from evolution_lab.self_improver import SelfImprovementEngine
        engine = SelfImprovementEngine(soma_root=ROOT)
        stats = engine.stats
        assert stats["pending_proposals"] == 0
        assert stats["total_proposals"] == 0
        assert stats["daily_limit"] == 10

    def test_analyzable_files_list(self):
        """get_analyzable_files() liefert Python-Dateien."""
        from evolution_lab.self_improver import SelfImprovementEngine
        engine = SelfImprovementEngine(soma_root=ROOT)
        files = engine.get_analyzable_files()
        assert len(files) > 0
        assert all(f.endswith(".py") for f in files)
        # Identity Anchor darf nicht drin sein
        assert "brain_ego/identity_anchor.py" not in files

    def test_generate_diff(self):
        """Diff-Generierung funktioniert."""
        from evolution_lab.self_improver import SelfImprovementEngine
        original = "x = 1\ny = 2\n"
        proposed = "x = 1\ny = 3\n"
        diff = SelfImprovementEngine._generate_diff(original, proposed, "test.py")
        assert "---" in diff or "+y = 3" in diff or "-y = 2" in diff

    def test_apply_nonexistent_proposal_fails(self):
        """Anwenden eines nicht-existierenden Proposals schlaegt fehl."""
        from evolution_lab.self_improver import SelfImprovementEngine, ProposalStatus
        engine = SelfImprovementEngine(soma_root=ROOT)
        result = _run(engine.apply_improvement("nonexistent_id"))
        assert result.status == ProposalStatus.FAILED
        assert "nicht gefunden" in result.error

    def test_reject_nonexistent_proposal(self):
        """Ablehnen eines nicht-existierenden Proposals gibt Fehler."""
        from evolution_lab.self_improver import SelfImprovementEngine, ProposalStatus
        engine = SelfImprovementEngine(soma_root=ROOT)
        result = _run(engine.reject_proposal("nonexistent_id"))
        assert result.status == ProposalStatus.FAILED

    def test_rollback_nonexistent_proposal(self):
        """Rollback eines nicht-existierenden Proposals gibt Fehler."""
        from evolution_lab.self_improver import SelfImprovementEngine, ProposalStatus
        engine = SelfImprovementEngine(soma_root=ROOT)
        result = _run(engine.rollback("nonexistent_id"))
        assert result.status == ProposalStatus.FAILED

    def test_daily_rate_limit_constant(self):
        """Tägliches Limit ist definiert."""
        from evolution_lab.self_improver import MAX_DAILY_IMPROVEMENTS
        assert MAX_DAILY_IMPROVEMENTS == 10


# ══════════════════════════════════════════════════════════════════════════
#  P5.4 — PLUGIN MANAGER INTEGRATION: Validator + Sandbox im Generator
# ══════════════════════════════════════════════════════════════════════════

class TestPluginManagerIntegration:
    """Tests fuer die Integration von Validator + Sandbox in den PluginManager."""

    def test_plugin_generator_has_validator(self):
        """PluginGenerator hat einen CodeValidator."""
        from evolution_lab.plugin_manager import PluginManager, PluginGenerator
        pm = PluginManager()
        pg = PluginGenerator(manager=pm, heavy_engine=None)
        assert hasattr(pg, "_validator")
        assert pg._validator is not None

    def test_plugin_generator_has_sandbox(self):
        """PluginGenerator hat einen SandboxRunner."""
        from evolution_lab.plugin_manager import PluginManager, PluginGenerator
        pm = PluginManager()
        pg = PluginGenerator(manager=pm, heavy_engine=None)
        assert hasattr(pg, "_sandbox")
        assert pg._sandbox is not None

    def test_max_retries_increased(self):
        """MAX_GENERATION_RETRIES ist auf 3 erhoeht."""
        from evolution_lab.plugin_manager import MAX_GENERATION_RETRIES
        assert MAX_GENERATION_RETRIES == 3

    def test_plugin_manager_basic_operations(self):
        """PluginManager Basis-Operationen funktionieren."""
        from evolution_lab.plugin_manager import PluginManager
        pm = PluginManager()
        assert pm.list_all() == {} or isinstance(pm.list_all(), dict)
        names = pm.discover_plugins()
        assert isinstance(names, list)

    def test_extract_dependencies(self):
        """_extract_dependencies parsed __dependencies__ korrekt."""
        from evolution_lab.plugin_manager import PluginGenerator
        code = '__dependencies__ = ["psutil", "aiohttp>=3.9"]'
        deps = PluginGenerator._extract_dependencies(code)
        assert "psutil" in deps
        assert "aiohttp>=3.9" in deps

    def test_extract_code_markdown(self):
        """_extract_code entfernt Markdown-Codeblocks."""
        from evolution_lab.plugin_manager import PluginGenerator
        raw = '```python\nimport os\nprint("hello")\n```'
        code = PluginGenerator._extract_code(raw)
        assert code.startswith("import os")
        assert "```" not in code

    def test_extract_code_plain(self):
        """_extract_code handhabt reinen Code ohne Markdown."""
        from evolution_lab.plugin_manager import PluginGenerator
        raw = 'import os\nprint("hello")'
        code = PluginGenerator._extract_code(raw)
        assert "import os" in code


# ══════════════════════════════════════════════════════════════════════════
#  P5.5 — WIRING: main.py Imports + API Endpoints
# ══════════════════════════════════════════════════════════════════════════

class TestMainWiring:
    """Tests fuer die Integration in main.py."""

    def test_main_imports_self_improver(self):
        """main.py importiert SelfImprovementEngine."""
        main_code = (ROOT / "brain_core" / "main.py").read_text(encoding="utf-8")
        assert "SelfImprovementEngine" in main_code
        assert "self_improver" in main_code

    def test_main_has_self_improve_endpoints(self):
        """main.py hat Self-Improvement API-Endpunkte."""
        main_code = (ROOT / "brain_core" / "main.py").read_text(encoding="utf-8")
        assert "/api/v1/evolution/self-improve/analyze" in main_code
        assert "/api/v1/evolution/self-improve/suggest" in main_code
        assert "/api/v1/evolution/self-improve/apply" in main_code
        assert "/api/v1/evolution/self-improve/reject" in main_code
        assert "/api/v1/evolution/self-improve/rollback" in main_code
        assert "/api/v1/evolution/self-improve/proposals" in main_code
        assert "/api/v1/evolution/self-improve/files" in main_code
        assert "/api/v1/evolution/self-improve/history" in main_code

    def test_evolution_lab_exports(self):
        """evolution_lab __init__.py exportiert alle Phase 5 Komponenten."""
        from evolution_lab import (
            CodeValidator,
            ForbiddenPatternChecker,
            ASTValidator,
            ValidationReport,
            Severity,
            SandboxRunner,
            SandboxResult,
            SandboxMode,
            SelfImprovementEngine,
            ImprovementProposal,
            ProposalStatus,
            ImprovementCategory,
            IMMUTABLE_FILES,
            PluginManager,
            PluginGenerator,
            PluginMeta,
            PluginNotFoundError,
            PluginError,
        )
        # Alle importiert — kein AssertionError
        assert CodeValidator is not None
        assert SandboxRunner is not None
        assert SelfImprovementEngine is not None

    def test_code_validator_in_plugin_generator_txt(self):
        """plugin_generator.txt System-Prompt existiert und hat Inhalt."""
        prompt_path = ROOT / "evolution_lab" / "prompts" / "plugin_generator.txt"
        assert prompt_path.exists()
        content = prompt_path.read_text(encoding="utf-8")
        assert len(content) > 100
        assert "SOMA" in content
        assert "__version__" in content
