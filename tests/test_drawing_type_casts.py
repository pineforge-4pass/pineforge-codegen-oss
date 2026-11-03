"""A typed missing drawing handle equals the typed declaration's default."""

import pytest

from pineforge_codegen import transpile


@pytest.mark.parametrize("kind,cpp_type", [("line", "Line"), ("box", "Box")])
@pytest.mark.parametrize("name", ["handle", "guide"])
def test_na_drawing_cast_matches_typed_na_declaration(kind, cpp_type, name):
    def source(value):
        return (
            '//@version=6\nstrategy("Drawing casts", overlay=true)\n'
            f'var {kind} {name} = {value}\n'
            f'{kind}.delete({name})\nplot(close)\n'
        )

    cpp = transpile(source(f"{kind}(na)"))
    control = transpile(source("na"))
    initializers = lambda text: [
        line.strip() for line in text.splitlines()
        if f"{name} =" in line or f"{name};" in line
    ]
    assert initializers(cpp) == initializers(control)
    assert f"{name} = {cpp_type}{{}};" in cpp
    assert f"pf_{kind}_delete" in cpp
