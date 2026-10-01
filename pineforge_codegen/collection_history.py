"""The history of an array or a matrix variable (``a[k]``, ``m[k]``).

TradingView (``tests/fixtures/array_history_tv``) reads ``a[k]`` as a copy of
the collection as the variable left it at the end of its scope's execution k
executions back -- the bar k bars back, for a variable of the script's top
level -- not as the array the variable holds now: a ``var`` array's history
is one element shorter than the array it keeps growing. The copy is
read-only: a change to it, to a slice of it or through a variable or a
parameter bound to it stops the run (RE10051), and a method on it before the
variable has a history stops the run too (RE10052 for an array, RE10053 for a
matrix). A ``for...in`` loop over ``a[k]`` iterates the array the variable
holds now.

The codegen keeps such a variable's own ``std::vector`` or matrix, so every
operation on the variable keeps its C++, and beside it the copies its
executions left (``COLLECTION_HISTORY_CPP``): each execution of the
declaration -- each bar, for a ``var`` -- opens a slot, and the end of the bar
closes it with a copy of the value. ``a[1]`` used to lower to the current
array's element 1 and ``m[1]`` to a ``Series<double>``; neither compiled where
the script reads an array or a matrix.

The analyzer annotates each history read with how its value is used
(``COLLECTION_HISTORY_ANNOTATION``) and refuses, by name, every use the
codegen does not lower: TradingView's own refusals (CE10123, CE10173, CE10101,
CE10009) and the forms PineForge cannot keep faithfully, none of which
compiled before (function scope, a loop's or a block's ``var``, a selection,
a change through a variable or a parameter bound to the history).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ast_nodes import (
    Assignment, BinOp, ExprStmt, ForInStmt, FuncCall, FuncDef, Identifier,
    IfStmt, MemberAccess, MethodDef, NumberLiteral, Program, Subscript,
    SwitchStmt, Ternary, UnaryOp, VarDecl, WhileStmt,
)
from .limits import iter_ast_nodes, syntax_children


# Set on a ``Subscript`` whose object is an array or a matrix variable: a
# dict with the variable (``var``), its kind (``array`` / ``matrix``) and
# TypeSpec (``spec``), and how the value is used (``use``): ``read`` (a built-in reads it), ``change`` (a
# built-in changes it: the run stops), ``copy`` (bound to a variable or given
# to a user function, which read it), ``loop`` (a for...in iterable: the
# variable's current array, as TradingView iterates) or ``na`` (``na()``).
COLLECTION_HISTORY_ANNOTATION = "pf_collection_history"

# TradingView's runtime answers (fixtures/array_history_tv README).
HISTORICAL_CHANGE_MESSAGE = (
    "Cannot modify the elements of a historical array or any slices of that "
    "array. Instead of modifying an array referenced by an ID retrieved with "
    "the `[]` operator, create a shallow copy of the array with "
    "`array.copy()`, then modify the copy or a slice of that copy."
)
NA_ARRAY_MESSAGE = "Cannot call array methods when id of array is na."
NA_MATRIX_MESSAGE = "Cannot call matrix methods when id of matrix is na."

# The built-ins that change the array or matrix they are called on (method
# syntax) or given first (namespace syntax).
ARRAY_CHANGING_METHODS = frozenset({
    "set", "push", "unshift", "insert", "pop", "shift", "remove", "clear",
    "fill", "sort", "reverse", "concat",
})
MATRIX_CHANGING_METHODS = frozenset({
    "set", "fill", "add_row", "add_col", "remove_row", "remove_col",
    "swap_rows", "swap_columns", "reshape", "reverse", "sort", "concat",
})

# Type hints TradingView refuses an array or a matrix for (CE10173).
_SCALAR_HINTS = frozenset({"float", "int", "bool", "string", "color"})

# Built-in namespaces whose functions share a name with an array method
# (``math.abs``, ``str.join``...): a call of theirs is no collection method.
_BUILTIN_NAMESPACES = frozenset({
    "math", "str", "ta", "strategy", "request", "input", "color", "timeframe",
    "syminfo", "barstate", "runtime", "log", "label", "line", "box", "table",
    "linefill", "polyline", "chart", "ticker", "map", "array", "matrix",
    "session", "alert",
})

COLLECTION_HISTORY_CPP = r"""
// The history of an array or a matrix variable: TradingView's x[k] is a
// read-only copy of the collection as the variable left it at the end of its
// scope's execution k executions back (tests/fixtures/array_history_tv). The
// variable keeps its own value; each execution of its declaration (each bar,
// for a var) opens a slot, and the end of the bar closes it with a copy.
template <typename T>
struct _PFCollectionTraits;
template <typename E, typename A>
struct _PFCollectionTraits<std::vector<E, A>> {
    static std::shared_ptr<const std::vector<E, A>> freeze(const std::vector<E, A>& value) {
        return std::make_shared<const std::vector<E, A>>(value);
    }
    static std::vector<E, A> copy(const std::vector<E, A>& value) { return value; }
    static bool is_na(const std::vector<E, A>&) { return false; }
    static const char* na_message() {
        return "Cannot call array methods when id of array is na.";
    }
};
"""

# The matrix traits, emitted when the script uses the matrix types.
COLLECTION_HISTORY_MATRIX_CPP = r"""
template <>
struct _PFCollectionTraits<PineMatrix> {
    static std::shared_ptr<const PineMatrix> freeze(const PineMatrix& value) {
        if (value.is_na()) return nullptr;
        return std::make_shared<const PineMatrix>(value.copy());
    }
    static PineMatrix copy(const PineMatrix& value) {
        return value.is_na() ? PineMatrix{} : value.copy();
    }
    static bool is_na(const PineMatrix& value) { return value.is_na(); }
    static const char* na_message() {
        return "Cannot call matrix methods when id of matrix is na.";
    }
};
"""

COLLECTION_HISTORY_GENERIC_MATRIX_CPP = r"""
template <typename E>
struct _PFCollectionTraits<PineGenericMatrix<E>> {
    static std::shared_ptr<const PineGenericMatrix<E>> freeze(const PineGenericMatrix<E>& value) {
        if (value.is_na()) return nullptr;
        return std::make_shared<const PineGenericMatrix<E>>(value.copy());
    }
    static PineGenericMatrix<E> copy(const PineGenericMatrix<E>& value) {
        return value.is_na() ? PineGenericMatrix<E>{} : value.copy();
    }
    static bool is_na(const PineGenericMatrix<E>& value) { return value.is_na(); }
    static const char* na_message() {
        return "Cannot call matrix methods when id of matrix is na.";
    }
};
"""

COLLECTION_HISTORY_CLASS_CPP = r"""
// A change to the history of an array or a matrix stops the run, as
// TradingView's does (RE10051).
[[noreturn]] inline void _pf_collection_history_changed() {
    pine_runtime_error("Cannot modify the elements of a historical array or any slices of that array. Instead of modifying an array referenced by an ID retrieved with the `[]` operator, create a shallow copy of the array with `array.copy()`, then modify the copy or a slice of that copy.");
    throw 0;
}

template <typename T>
class _PFCollectionHistory {
public:
    explicit _PFCollectionHistory(int max_len = 500) : slots_(max_len) {}
    // An execution of the variable's declaration begins: the previous one's
    // slot keeps its copy, and this one's opens.
    void open(const T& value, bool new_slot) {
        close(value);
        if (new_slot) slots_.push(nullptr);
        else slots_.update(nullptr);
        open_ = true;
    }
    // The execution ended: its slot keeps a copy of the value.
    void close(const T& value) {
        if (!open_) return;
        slots_.update(_PFCollectionTraits<T>::freeze(value));
        open_ = false;
    }
    bool is_na(int offset) const { return offset <= 0 || !slots_[offset]; }
    bool is_na(int offset, const T& current) const {
        return offset == 0 ? _PFCollectionTraits<T>::is_na(current) : is_na(offset);
    }
    // The copy k executions back; a method on a na history stops the run, as
    // TradingView's does (RE10052, RE10053).
    const T& at(int offset) const {
        std::shared_ptr<const T> slot = offset > 0 ? slots_[offset] : nullptr;
        if (!slot) {
            pine_runtime_error(_PFCollectionTraits<T>::na_message());
        }
        return *slot;
    }
    const T& at(int offset, const T& current) const {
        return offset == 0 ? current : at(offset);
    }
    // A copy of the history for a variable or a parameter: empty for an
    // array before it has one (PineForge holds no na array), na for a matrix.
    T value(int offset) const {
        std::shared_ptr<const T> slot = offset > 0 ? slots_[offset] : nullptr;
        return slot ? _PFCollectionTraits<T>::copy(*slot) : T{};
    }
    T value(int offset, const T& current) const {
        return offset == 0 ? current : value(offset);
    }
    // The receiver of a built-in that changes it: the variable itself at a
    // zero offset, else the run stops, as TradingView's does (RE10051; a na
    // history stops it with RE10052 / RE10053 first).
    T& changed(int offset, T& current) const {
        if (offset == 0) return current;
        (void)at(offset);
        _pf_collection_history_changed();
    }

private:
    Series<std::shared_ptr<const T>> slots_;
    bool open_ = false;
};

"""


def history_annotation(node) -> dict | None:
    """The ``COLLECTION_HISTORY_ANNOTATION`` of ``node``, else None."""
    return (getattr(node, "annotations", None) or {}).get(COLLECTION_HISTORY_ANNOTATION)


def literal_offset(node: Subscript) -> int | None:
    """The offset of ``node`` when it is an integer literal, else None."""
    index = node.index
    if isinstance(index, NumberLiteral) and isinstance(index.value, int):
        return index.value
    return None


@dataclass
class HistoryRead:
    """A history read of an array or a matrix variable the analyzer met."""
    node: Subscript
    name: str
    kind: str                      # "array" | "matrix"
    spec: object                   # its TypeSpec
    in_callable: bool              # read inside a function or method body
    is_parameter: bool             # the variable is a parameter
    is_global: bool                # the variable belongs to the top level
    is_var: bool
    decl_node_id: int | None
    scope_name: str = "global"     # the analyzer's scope of the variable


@dataclass
class HistoryVariable:
    """A variable whose history the script reads, for the codegen."""
    name: str
    kind: str
    spec: object
    is_var: bool
    capacity: int | None           # None: the Series default (dynamic offsets)
    decl_node_ids: set[int] = field(default_factory=set)


class CollectionHistoryRefusal(Exception):
    def __init__(self, node, message: str, hint: str | None = None):
        super().__init__(message)
        self.node = node
        self.message = message
        self.hint = hint


def _a(kind: str) -> str:
    """``an array`` / ``a matrix``."""
    return f"an {kind}" if kind[:1] in "aeiou" else f"a {kind}"


def _spelling(node: Subscript) -> str:
    obj = node.object
    name = obj.name if isinstance(obj, Identifier) else "x"
    offset = literal_offset(node)
    index = (str(offset) if offset is not None
             else node.index.name if isinstance(node.index, Identifier) else "k")
    return f"{name}[{index}]"


class CollectionHistoryChecker:
    """Classifies the use of every array or matrix history read and refuses
    the uses the codegen does not lower (see the module docstring)."""

    def __init__(self, program: Program, name_kind=None):
        """``name_kind(name)`` answers ``array``, ``matrix``, ``map`` or
        ``scalar`` for a script variable the analyzer typed, else None."""
        from .ast_nodes import TypeDecl
        from .codegen.tables import ARRAY_METHODS, MATRIX_METHODS

        self._name_kind = name_kind or (lambda name: None)
        self._types = {stmt.name for stmt in program.body if isinstance(stmt, TypeDecl)}

        self._array_methods = frozenset(ARRAY_METHODS)
        self._matrix_methods = frozenset(MATRIX_METHODS)
        self._parent: dict[int, object] = {}
        self._nodes_by_id: dict[int, object] = {}
        self._program = program
        for node, _depth in iter_ast_nodes(program):
            self._nodes_by_id[id(node)] = node
            for child in syntax_children(node):
                self._parent[id(child)] = node
        self._functions: dict[str, list[FuncDef]] = {}
        self._methods: dict[str, list[MethodDef]] = {}
        for stmt in program.body:
            if isinstance(stmt, FuncDef):
                self._functions.setdefault(stmt.name, []).append(stmt)
            elif isinstance(stmt, MethodDef):
                self._methods.setdefault(stmt.name, []).append(stmt)
        self._top_level_ids = {id(stmt) for stmt in program.body}

    # -- scopes -------------------------------------------------------------

    def _ancestors(self, node):
        current = self._parent.get(id(node))
        while current is not None:
            yield current
            current = self._parent.get(id(current))

    def _in_request(self, node) -> bool:
        return any(isinstance(a, FuncCall) and isinstance(a.callee, MemberAccess)
                   and isinstance(a.callee.object, Identifier)
                   and a.callee.object.name == "request"
                   for a in self._ancestors(node))

    def declaration_scope(self, decl_node_id: int | None) -> str:
        """``top`` for a declaration statement of the script's top level,
        ``block`` for one in an if or switch block of it, ``loop`` inside a
        loop, ``callable`` inside a function or method, ``unknown`` without a
        declaration statement (a tuple's, a loop binder's)."""
        if decl_node_id is None:
            return "unknown"
        decl = self._nodes_by_id.get(decl_node_id)
        if not isinstance(decl, VarDecl):
            return "unknown"
        if id(decl) in self._top_level_ids:
            return "top"
        from .ast_nodes import ForStmt
        for ancestor in self._ancestors(decl):
            if isinstance(ancestor, (FuncDef, MethodDef)):
                return "callable"
            if isinstance(ancestor, (ForStmt, ForInStmt, WhileStmt)):
                return "loop"
        return "block"

    # -- uses ---------------------------------------------------------------

    def _call_is_namespace(self, call: FuncCall, namespace: str) -> bool:
        callee = call.callee
        return (isinstance(callee, MemberAccess)
                and isinstance(callee.object, Identifier)
                and callee.object.name == namespace)

    def _changing(self, kind: str, method: str) -> bool:
        table = ARRAY_CHANGING_METHODS if kind == "array" else MATRIX_CHANGING_METHODS
        return method in table

    def _builtin_methods(self, kind: str) -> frozenset[str]:
        return self._array_methods if kind == "array" else self._matrix_methods

    def classify(self, node, kind: str, label: str, bound: frozenset = frozenset(),
                 historical: bool = True) -> str:
        """How the value of ``node`` (a history read, or a name bound to
        one) is used: ``read``, ``change``, ``copy``, ``loop`` or ``na``.
        Raises ``CollectionHistoryRefusal`` for a use the codegen does not
        lower. A zero offset (``historical`` False) is the variable itself,
        which a variable or a parameter it is bound to may change."""
        parent = self._parent.get(id(node))
        if isinstance(parent, MemberAccess) and parent.object is node:
            call = self._parent.get(id(parent))
            if not (isinstance(call, FuncCall) and call.callee is parent):
                raise CollectionHistoryRefusal(node, f"{label}.{parent.member}: {_a(kind)} has no fields.")
            if parent.member in self._builtin_methods(kind):
                return "change" if self._changing(kind, parent.member) else "read"
            if parent.member in self._methods:
                if historical:
                    self._check_parameter_reads(
                        self._methods[parent.member], 0, kind, label, node, bound)
                return "copy"
            raise CollectionHistoryRefusal(
                node,
                f"{label}.{parent.member}(): no array or matrix method of that "
                "name; the history of an array or a matrix is not supported in "
                "PineForge there.")
        if isinstance(parent, FuncCall):
            return self._classify_argument(parent, node, kind, label, bound, historical)
        if isinstance(parent, ForInStmt) and parent.iterable is node:
            return "loop"
        if isinstance(parent, VarDecl) and parent.value is node:
            if parent.type_hint in _SCALAR_HINTS:
                raise CollectionHistoryRefusal(
                    node,
                    f"{label} is {_a(kind)}, which TradingView refuses for the "
                    f"{parent.type_hint} variable {parent.name} (CE10173).")
            if historical:
                self._check_bound_reads(parent.name, kind, label, node, bound)
            return "copy"
        if isinstance(parent, Assignment) and parent.value is node:
            if parent.op != ":=":
                raise CollectionHistoryRefusal(
                    node,
                    f"{label} is {_a(kind)}, which TradingView refuses for "
                    f"operator {parent.op} (CE10123).")
            if not isinstance(parent.target, Identifier):
                raise CollectionHistoryRefusal(
                    node,
                    f"{label} assigned to a field is not supported in PineForge: "
                    "the field would hold a changeable copy, where TradingView's "
                    "holds the read-only history.")
            target_kind = self._name_kind(parent.target.name)
            if target_kind is not None and target_kind != kind:
                raise CollectionHistoryRefusal(
                    node,
                    f"{label} is {_a(kind)}, which TradingView refuses for the "
                    f"variable {parent.target.name} of another type (CE10173).")
            if historical:
                self._check_bound_reads(parent.target.name, kind, label, node, bound)
            return "copy"
        if isinstance(parent, (BinOp, UnaryOp)):
            op = parent.op
            raise CollectionHistoryRefusal(
                node,
                f"{label} is {_a(kind)}, which TradingView refuses for operator "
                f"{op} (CE10123).")
        if isinstance(parent, Ternary) and parent.condition is node:
            raise CollectionHistoryRefusal(
                node,
                f"{label} is {_a(kind)}, which TradingView refuses as the "
                "condition of ?: (CE10123).")
        if isinstance(parent, (IfStmt, WhileStmt)) and parent.condition is node:
            statement = "if" if isinstance(parent, IfStmt) else "while"
            raise CollectionHistoryRefusal(
                node,
                f"{label} is {_a(kind)}, which TradingView refuses as the "
                f"condition of an {statement} statement (CE10101).")
        if isinstance(parent, Subscript) and parent.object is node:
            raise CollectionHistoryRefusal(
                node,
                f"({label})[...]: the history of {_a(kind)}'s history is not "
                "supported in PineForge.")
        if isinstance(parent, ExprStmt) and not isinstance(
                self._parent.get(id(parent)), SwitchStmt):
            raise CollectionHistoryRefusal(
                node,
                f"{label} as a statement: TradingView reads it as a declaration "
                "and refuses it (CE10009, \"Extraneous input\").")
        raise CollectionHistoryRefusal(
            node,
            f"{label} is not supported in PineForge here: the history of "
            f"{_a(kind)} is supported as the receiver or an argument of "
            f"{_a(kind)} function, a for...in iterable, na(), the value of a "
            "variable and a user function's argument.")

    def _classify_argument(self, call: FuncCall, node, kind: str, label: str,
                           bound: frozenset, historical: bool = True) -> str:
        callee = call.callee
        positional = next((i for i, a in enumerate(call.args) if a is node), None)
        keyword = next((k for k, v in call.kwargs.items() if v is node), None)
        if isinstance(callee, Identifier) and callee.name == "na" and len(call.args) == 1:
            return "na"
        for namespace, methods, changing in (
                ("array", self._array_methods, ARRAY_CHANGING_METHODS),
                ("matrix", self._matrix_methods, MATRIX_CHANGING_METHODS)):
            if self._call_is_namespace(call, namespace) and callee.member in methods:
                first = positional == 0 or keyword in ("id", "id1")
                return "change" if first and callee.member in changing else "read"
        if (isinstance(callee, MemberAccess)
                and callee.member not in self._methods
                and not (isinstance(callee.object, Identifier)
                         and (callee.object.name in _BUILTIN_NAMESPACES
                              or callee.object.name in self._types))
                and (callee.member in self._array_methods
                     or callee.member in self._matrix_methods)):
            # An argument of a built-in method on another collection
            # (``c.concat(a[1])``, ``m.add_row(0, a[1])``): read, never changed.
            return "read"
        if isinstance(callee, Identifier) and callee.name in self._functions:
            if historical:
                self._check_parameter_reads(
                    self._functions[callee.name], positional, kind, label, node,
                    bound, keyword)
            return "copy"
        if (isinstance(callee, MemberAccess) and callee.member in self._methods
                and not (isinstance(callee.object, Identifier)
                         and callee.object.name in ("array", "matrix", "map"))):
            if historical:
                self._check_parameter_reads(
                    self._methods[callee.member],
                    None if positional is None else positional + 1,
                    kind, label, node, bound, keyword)
            return "copy"
        if (isinstance(callee, MemberAccess) and isinstance(callee.object, Identifier)
                and callee.object.name in self._types):
            raise CollectionHistoryRefusal(
                node,
                f"{label} as a field of a new {callee.object.name} is not supported "
                "in PineForge: the field would hold a changeable copy, where "
                "TradingView's holds the read-only history.")
        if (isinstance(callee, MemberAccess) and isinstance(callee.object, Identifier)
                and callee.object.name == "str"):
            raise CollectionHistoryRefusal(
                node,
                f"str.{callee.member}({label}) is not supported in PineForge: "
                "it does not render an array.")
        name = (callee.name if isinstance(callee, Identifier)
                else f"{callee.object.name}.{callee.member}"
                if isinstance(callee, MemberAccess) and isinstance(callee.object, Identifier)
                else "the function")
        # TradingView answers CE10122 for an element of array.from (one of
        # several element types expected), CE10123 elsewhere.
        code = "CE10122" if name == "array.from" else "CE10123"
        raise CollectionHistoryRefusal(
            node,
            f"{label} is {_a(kind)}, which TradingView refuses as an argument of "
            f"{name} ({code}).")

    # -- reads of a name bound to the history ---------------------------------

    def _uses_of(self, name: str, scope) -> list:
        return [node for node, _depth in iter_ast_nodes(scope)
                if isinstance(node, Identifier) and node.name == name
                and not (isinstance(self._parent.get(id(node)), Assignment)
                         and self._parent[id(node)].target is node)]

    def _check_name_reads(self, name: str, scope, kind: str, label: str,
                          origin, bound: frozenset) -> None:
        """Every use of ``name`` in ``scope`` reads the copy: TradingView's
        name is bound to the read-only history, and a change through it stops
        the run (RE10051), where PineForge would change the copy."""
        key = (name, id(scope))
        if key in bound:
            return
        bound = bound | {key}
        for use in self._uses_of(name, scope):
            parent = self._parent.get(id(use))
            if isinstance(parent, Subscript) and parent.object is use:
                continue   # its own history read, checked as one
            try:
                how = self.classify(use, kind, name, bound)
            except CollectionHistoryRefusal as refusal:
                raise CollectionHistoryRefusal(
                    origin,
                    f"{label} is bound to {name}, whose use is not supported in "
                    f"PineForge: {refusal.message}") from None
            if how == "change":
                raise CollectionHistoryRefusal(
                    origin,
                    f"{label} is bound to {name}, which the script changes "
                    f"(line {use.loc.line if use.loc else '?'}): TradingView "
                    "stops the run there (RE10051: \"Cannot modify the elements "
                    "of a historical array\"); PineForge, which binds a copy, "
                    "does not.",
                    hint=f"Bind array.copy({label}) or matrix.copy() to change it.")

    def _check_bound_reads(self, name: str, kind: str, label: str, origin,
                           bound: frozenset) -> None:
        self._check_name_reads(name, self._program, kind, label, origin, bound)

    def _check_parameter_reads(self, definitions, index, kind: str, label: str,
                               origin, bound: frozenset, keyword: str | None = None) -> None:
        for definition in definitions:
            params = list(definition.params)
            if keyword is not None and keyword in params:
                position = params.index(keyword)
            else:
                position = index
            if position is None or position >= len(params):
                continue
            param = params[position]
            self._check_name_reads(param, definition, kind, label, origin, bound)

    # -- the pass -------------------------------------------------------------

    @staticmethod
    def _annotate(read: HistoryRead, use: str) -> None:
        read.node.annotations = dict(read.node.annotations or {})
        read.node.annotations[COLLECTION_HISTORY_ANNOTATION] = {
            "var": read.name, "kind": read.kind, "use": use, "spec": read.spec,
        }

    def check(self, reads: list[HistoryRead]) -> dict[str, HistoryVariable]:
        """Annotate every read with its use and return the variables whose
        history the codegen keeps. Raises ``CollectionHistoryRefusal`` for the first read the
        codegen does not lower."""
        variables: dict[str, HistoryVariable] = {}
        for read in reads:
            label = _spelling(read.node)
            offset = literal_offset(read.node)
            if self._in_request(read.node):
                raise CollectionHistoryRefusal(
                    read.node,
                    f"{label} inside a request.security expression is not "
                    "supported in PineForge: TradingView reads the copies the "
                    "requested timeframe's bars left, which PineForge does not "
                    "keep.")
            if offset == 0:
                # The variable itself, wherever it is read.
                use = self.classify(read.node, read.kind, label, historical=False)
                self._annotate(read, use)
                continue
            if read.in_callable:
                what = ("parameter" if read.is_parameter
                        else "script variable read in a function" if read.is_global
                        else "function's local")
                raise CollectionHistoryRefusal(
                    read.node,
                    f"{label}: the history of {_a(read.kind)} {what} is not "
                    "supported in PineForge (TradingView reads one copy per "
                    "call: fixtures/array_history_tv ahist_fn).")
            scope = self.declaration_scope(read.decl_node_id)
            if scope == "loop":
                raise CollectionHistoryRefusal(
                    read.node,
                    f"{label}: the history of {_a(read.kind)} declared in a loop "
                    "is not supported in PineForge.")
            if read.scope_name.startswith("top_"):
                raise CollectionHistoryRefusal(
                    read.node,
                    f"{label}: the history of {_a(read.kind)} declared in a block "
                    "that redeclares a script variable is not supported in "
                    "PineForge (such a block's declarations are C++ locals).")
            if scope in ("callable", "unknown") or not read.is_global:
                raise CollectionHistoryRefusal(
                    read.node,
                    f"{label}: the history of this {read.kind} is not supported "
                    "in PineForge (only a variable the script's top level or one "
                    "of its blocks declares keeps one).")
            if read.is_var and scope != "top":
                raise CollectionHistoryRefusal(
                    read.node,
                    f"{label}: the history of a block's var {read.kind} is not "
                    "supported in PineForge.")
            use = self.classify(read.node, read.kind, label)
            self._annotate(read, use)
            if use == "loop":
                continue
            entry = variables.get(read.name)
            if entry is None:
                entry = variables[read.name] = HistoryVariable(
                    name=read.name, kind=read.kind, spec=read.spec,
                    is_var=read.is_var, capacity=0)
            if read.decl_node_id is not None:
                entry.decl_node_ids.add(read.decl_node_id)
            if entry.capacity is not None:
                entry.capacity = (None if offset is None
                                  else max(entry.capacity, offset + 1))
        return variables
