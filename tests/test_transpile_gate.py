"""The translation pipeline checks every returned C++ translation unit."""

import pytest

import pineforge_codegen
from pineforge_codegen.cpp_allowlist import Finding, check_generated_cpp
from pineforge_codegen.errors import CompileError, Level, Phase, SourceLocation


SOURCE = (
    '//@version=6\nstrategy("SCRIPT_TEXT_MARKER")\n'
    "SCRIPT_TEXT_MARKER = close\n"
    'plot(SCRIPT_TEXT_MARKER, title="SCRIPT_TEXT_MARKER")\n'
)


@pytest.fixture
def checked_cpp(monkeypatch):
    calls = []

    def check(cpp):
        calls.append(cpp)
        return check_generated_cpp(cpp)

    # An unused import on an older pipeline must fail the call-count assertion.
    monkeypatch.setattr(pineforge_codegen, "check_generated_cpp", check, raising=False)
    return calls


def test_valid_strategy_returns_checked_cpp(checked_cpp):
    cpp = pineforge_codegen.transpile(SOURCE)
    assert isinstance(cpp, str)
    # A completed translation checks its one returned translation unit.
    assert len(checked_cpp) == 1


@pytest.mark.parametrize(
    "entry_point",
    [
        "transpile",  # The string API shares the final generation step.
        "transpile_full",  # The manifest API shares the final generation step.
        "transpile_with_request_inventory",  # The inventory API shares it too.
    ],
)
@pytest.mark.parametrize(
    "check_support",
    [
        True,  # Support checking does not replace the C++ check.
        False,  # Bypassing support checking still requires the C++ check.
    ],
)
@pytest.mark.parametrize(
    "rule,reason",
    [
        (
            "namespace",
            "namespace or mount primitive is not allowed in generated C++",
        ),  # Namespace findings use the fixed namespace reason.
        (
            "thread",
            "thread creation is not allowed in generated C++",
        ),  # Thread findings use the fixed thread reason.
    ],
)
def test_entry_points_refuse_findings_without_script_text(
    monkeypatch, entry_point, check_support, rule, reason
):
    calls = []

    def refuse(cpp):
        calls.append(cpp)
        # The identifier and literal reach C++ before the check runs.
        assert "SCRIPT_TEXT_MARKER" in cpp
        return [Finding(line=987, column=654, rule=rule, reason=reason)]

    # Without pipeline integration this name is unused and no error is raised.
    monkeypatch.setattr(pineforge_codegen, "check_generated_cpp", refuse, raising=False)
    with pytest.raises(CompileError) as caught:
        getattr(pineforge_codegen, entry_point)(
            SOURCE, check_support=check_support, filename="strategy.pine"
        )

    # One generated translation unit produces exactly one checker call.
    assert len(calls) == 1
    # The injected single finding produces exactly one diagnostic.
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    # The expected reason is the literal in the parameter row.
    assert diagnostic.message == reason
    # The C++ check is an error in the generation phase.
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.CODEGEN
    # Generated coordinates cannot locate Pine, so use the file's first column.
    assert diagnostic.location == SourceLocation("strategy.pine", 1, 1, 1)
    # Guidance is fixed and carries no authored text.
    assert diagnostic.hint == "Report this generated C++ allowlist failure to PineForge."
    # Fixed catalog templates have no substituted arguments.
    assert diagnostic.args == {}
    # A regenerated catalog must assign a code rather than the fallback identity.
    assert diagnostic.code != "PF-E0000"
    # Every diagnostic field, including derived catalog fields, excludes the marker.
    for field in (
        diagnostic.level,
        diagnostic.phase,
        diagnostic.location.file,
        diagnostic.location.line,
        diagnostic.location.col,
        diagnostic.location.end_col,
        diagnostic.message,
        diagnostic.hint,
        diagnostic.code,
        diagnostic.args,
        diagnostic.user_message,
    ):
        assert "SCRIPT_TEXT_MARKER" not in repr(field)
    # The exception summary also excludes the authored identifier and literals.
    assert "SCRIPT_TEXT_MARKER" not in str(caught.value)


@pytest.mark.parametrize(
    "name,escaped",
    [
        ("unshare", "pf_safe_unshare"),  # Namespace creation uses a safe identifier.
        ("setns", "pf_safe_setns"),  # Namespace entry uses a safe identifier.
        ("clone", "pf_safe_clone"),  # Process creation uses a safe identifier.
        ("clone3", "pf_safe_clone3"),  # Process creation uses a safe identifier.
        ("mount", "pf_safe_mount"),  # Mount creation uses a safe identifier.
        ("umount", "pf_safe_umount"),  # Mount removal uses a safe identifier.
        ("umount2", "pf_safe_umount2"),  # Mount removal uses a safe identifier.
        ("pivot_root", "pf_safe_pivot_root"),  # Root mount changes use a safe name.
        ("chroot", "pf_safe_chroot"),  # Process root changes use a safe name.
        ("prctl", "pf_safe_prctl"),  # Process control uses a safe identifier.
        ("seccomp", "pf_safe_seccomp"),  # Process filtering uses a safe identifier.
        ("pthread_create", "pf_safe_pthread_create"),  # POSIX threads use a safe name.
        ("thrd_create", "pf_safe_thrd_create"),  # C threads use a safe identifier.
        ("__clone", "pf_safe___clone"),  # Low-level thread creation uses a safe name.
        ("thread", "pf_safe_thread"),  # Standard thread types use a safe identifier.
        ("jthread", "pf_safe_jthread"),  # Joining thread types use a safe identifier.
        ("async", "pf_safe_async"),  # Asynchronous execution uses a safe identifier.
    ],
)
def test_reserved_identifiers_pass_the_real_checker(checked_cpp, name, escaped):
    source = (
        '//@version=6\nstrategy("names")\n'
        f"{name} = close\nplot(close + {name})\n"
    )
    cpp = pineforge_codegen.transpile(source)
    # Each row spells the complete expected escaped identifier literally.
    assert escaped in cpp
    # The actual checker must have accepted the one returned translation unit.
    assert len(checked_cpp) == 1


def test_findings_are_capped_at_twenty_in_reported_order(monkeypatch):
    findings = [
        Finding(987, 654, "namespace",
                "namespace or mount primitive is not allowed in generated C++"),
        *[
            Finding(987, 654, "thread",
                    "thread creation is not allowed in generated C++")
            for _ in range(19)
        ],
        Finding(987, 654, "asm", "inline assembly is not allowed in generated C++"),
    ]
    calls = []

    def refuse(cpp):
        calls.append(cpp)
        return findings

    # An unused patch on an older pipeline cannot produce the required refusal.
    monkeypatch.setattr(pineforge_codegen, "check_generated_cpp", refuse, raising=False)
    with pytest.raises(CompileError) as caught:
        pineforge_codegen.transpile(SOURCE)

    # Only the final translation unit is checked once.
    assert len(calls) == 1
    diagnostics = caught.value.diagnostics
    # One namespace finding and nineteen thread findings fill the twenty slots.
    assert len(diagnostics) == 20
    # The first finding remains the first diagnostic.
    assert diagnostics[0].message == (
        "namespace or mount primitive is not allowed in generated C++"
    )
    # The twentieth finding remains last; the twenty-first assembly finding is omitted.
    assert diagnostics[19].message == "thread creation is not allowed in generated C++"
    # No assembly diagnostic survives the first-twenty cap.
    assert "inline assembly is not allowed in generated C++" not in [
        diagnostic.message for diagnostic in diagnostics
    ]
    # An omitted filename uses the public default at the first source column.
    assert diagnostics[0].location == SourceLocation("<input>", 1, 1, 1)
