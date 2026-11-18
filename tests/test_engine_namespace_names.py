"""Scope correctness and strict paired-header refresh for the name inventory."""

from pathlib import Path

from scripts import gen_engine_names as gen
from tests import _compile as compile_env


def test_only_global_imported_inline_and_explicitly_used_names_are_visible():
    tree = {"kind": "TranslationUnitDecl", "inner": [
        {"kind": "LinkageSpecDecl", "inner": [
            {"kind": "TypedefDecl", "name": "public_handle"},
            {"kind": "EnumDecl", "name": "PublicMode", "inner": [
                {"kind": "EnumConstantDecl", "name": "PUBLIC_MODE"},
            ]},
        ]},
        {"kind": "NamespaceDecl", "id": "p", "name": "pineforge", "inner": [
            {"kind": "CXXRecordDecl", "name": "Visible", "inner": [
                {"kind": "CXXRecordDecl", "name": "HiddenMember"},
            ]},
            {"kind": "NamespaceDecl", "id": "s", "name": "source", "inner": [
                {"kind": "CXXRecordDecl", "name": "HiddenSource"},
            ]},
            {"kind": "NamespaceDecl", "id": "v", "name": "version",
             "isInline": True, "inner": [
                 {"kind": "CXXRecordDecl", "name": "InlineVisible"},
                 {"kind": "EnumDecl", "name": "ScopedMode",
                  "scopedEnumTag": "class", "inner": [
                      {"kind": "EnumConstantDecl", "name": "HiddenEnumerator"},
                  ]},
             ]},
            {"kind": "NamespaceDecl", "id": "u", "name": "utilities", "inner": [
                {"kind": "FunctionDecl", "name": "explicitly_used"},
            ]},
            {"kind": "UsingDirectiveDecl", "nominatedNamespace": {"id": "u"}},
        ]},
    ]}
    names = gen.visible_names(tree)
    assert names["types"] == {
        "public_handle", "PublicMode", "Visible", "InlineVisible", "ScopedMode",
    }
    assert names["values"] == {"PUBLIC_MODE", "explicitly_used"}
    assert names["namespaces"] == {"pineforge", "source", "version", "utilities"}


def test_namespace_reopenings_and_using_declarations_keep_their_target_kind():
    tree = {"kind": "TranslationUnitDecl", "inner": [
        {"kind": "NamespaceDecl", "id": "p1", "name": "pineforge", "inner": [
            {"kind": "NamespaceDecl", "id": "n1", "name": "nested", "inner": [
                {"kind": "TypedefDecl", "name": "First"},
            ]},
        ]},
        {"kind": "NamespaceDecl", "id": "p2", "name": "pineforge", "inner": [
            {"kind": "NamespaceDecl", "id": "n2", "name": "nested", "inner": [
                {"kind": "TypeAliasDecl", "name": "Second"},
            ]},
            {"kind": "UsingDirectiveDecl", "nominatedNamespace": {"id": "n1"}},
            {"kind": "UsingDecl", "name": "external::Alias"},
            {"kind": "UsingShadowDecl", "target": {
                "kind": "TypeAliasDecl", "name": "Alias",
            }},
        ]},
    ]}
    assert gen.visible_names(tree)["types"] == {"First", "Second", "Alias"}


def test_committed_engine_names_match_the_complete_paired_header_inventory():
    compile_env.skip_if_no_compile_env()
    cxx = gen.find_clang(compile_env._COMPILER)
    assert cxx is not None, "the native inventory check requires clang"
    includes = [
        str(path) for path in (
            compile_env._ENGINE_INC, compile_env._EIGEN_INC,
            compile_env._GENERATED_INC,
        ) if path is not None
    ]
    repo = Path(__file__).resolve().parents[1]
    actual = gen.render(gen.derive(repo, cxx, includes))
    assert (repo / "pineforge_codegen/codegen/engine_names.py").read_text(
        encoding="utf-8",
    ) == actual, "refresh engine_names.py with scripts/gen_engine_names.py"
