from pineforge_codegen.symbols import TypeSpec


def test_typespec_matrix_constructor():
    elem = TypeSpec.primitive("int")
    spec = TypeSpec.matrix(elem)
    assert spec.kind == "matrix"
    assert spec.element == elem
    assert spec.element.name == "int"


def test_typespec_matrix_udt_element():
    elem = TypeSpec.udt("Pivot")
    spec = TypeSpec.matrix(elem)
    assert spec.kind == "matrix"
    assert spec.element.kind == "udt"
    assert spec.element.name == "Pivot"
