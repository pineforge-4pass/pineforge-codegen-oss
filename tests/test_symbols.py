from pineforge_codegen.symbols import PineType, Symbol, Scope, SymbolTable
from pineforge_codegen.errors import SourceLocation

def test_scope_define_and_resolve():
    scope = Scope(name="global", parent=None)
    loc = SourceLocation("f", 1, 1, 5)
    sym = Symbol("x", PineType.FLOAT, is_series=False, is_var=False,
                 is_const=False, const_value=None, scope="global", loc=loc)
    scope.define(sym)
    assert scope.resolve("x") is sym
    assert scope.resolve("y") is None

def test_scope_parent_resolution():
    parent = Scope(name="global", parent=None)
    child = Scope(name="function:foo", parent=parent)
    loc = SourceLocation("f", 1, 1, 5)
    sym = Symbol("x", PineType.FLOAT, False, False, False, None, "global", loc)
    parent.define(sym)
    # Child should find parent's symbol
    assert child.resolve("x") is sym

def test_scope_shadow():
    parent = Scope(name="global", parent=None)
    child = Scope(name="if", parent=parent)
    loc = SourceLocation("f", 1, 1, 1)
    parent.define(Symbol("x", PineType.FLOAT, False, False, False, None, "global", loc))
    child.define(Symbol("x", PineType.INT, False, False, False, None, "if", loc))
    # Child's version shadows parent's
    assert child.resolve("x").pine_type == PineType.INT
    assert parent.resolve("x").pine_type == PineType.FLOAT

def test_symbol_table_enter_exit_scope():
    st = SymbolTable()
    loc = SourceLocation("f", 1, 1, 1)
    st.define(Symbol("a", PineType.FLOAT, False, False, False, None, "global", loc))
    st.enter_scope("function:foo")
    st.define(Symbol("b", PineType.INT, False, False, False, None, "function:foo", loc))
    assert st.resolve("b") is not None
    assert st.resolve("a") is not None  # parent scope
    st.exit_scope()
    assert st.resolve("b") is None  # out of scope
    assert st.resolve("a") is not None
