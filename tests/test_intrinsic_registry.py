"""Every intrinsic registry participates in keyword-name lookup."""

from pineforge_codegen import signatures
from pineforge_codegen.signatures import IntrinsicFunc


def test_every_intrinsic_registry_record_is_reachable():
    registries = {
        name: registry
        for name, registry in vars(signatures).items()
        if isinstance(registry, dict) and registry
        and all(isinstance(record, IntrinsicFunc) for record in registry.values())
    }
    assert registries, "no intrinsic registries discovered"
    for registry_name, registry in registries.items():
        for key, record in registry.items():
            namespace = None
            if record.name != key:
                suffix = f".{key}"
                assert record.name.endswith(suffix), (registry_name, key, record.name)
                namespace = record.name[:-len(suffix)]
            assert signatures.get_intrinsic(namespace, key) is record, (
                registry_name, key, record.name,
            )


def test_registry_records_carry_no_evidence_fields():
    import dataclasses
    import re

    banned = ("ta" + "pe", "dig" + "est", "sha" + "256")
    for field in dataclasses.fields(IntrinsicFunc):
        assert not any(word in field.name.lower() for word in banned), field.name
    for name, value in vars(signatures).items():
        assert not any(word in name.lower() for word in banned), name
        if isinstance(value, dict):
            for record in value.values():
                if isinstance(record, IntrinsicFunc):
                    for item in dataclasses.astuple(record):
                        assert not re.search(r"\b[0-9a-f]{64}\b", repr(item)), record.name
