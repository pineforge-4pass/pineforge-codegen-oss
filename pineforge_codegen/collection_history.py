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
(or the next declaration of the same name, in a sibling block) closes it with
a copy of the value. ``a[1]`` used to lower to the current array's element 1
and ``m[1]`` to a ``Series<double>``; neither compiled where the script reads
an array or a matrix.

``CollectionHistoryChecker.decide`` gives every read one of three outcomes:

* ``SUPPORTED``: a variable of the script's top level or of one of its blocks,
  read where a built-in reads or changes it, by ``na()``, as a ``for...in``
  iterable, or bound to a variable or given to a function that only reads it.
  The analyzer annotates the read (``COLLECTION_HISTORY_ANNOTATION``).
* ``LEGACY``: every other read that TradingView accepts and whose earlier
  lowering compiled (the element ``a[k]`` of the current array, a parameter's
  current collection) keeps that lowering, and its warning: ``na()`` and
  ``str.tostring()`` in a function, a request payload or a loop, a
  parameter's history, a dead function's body.
* ``REFUSED``: what TradingView refuses (CE10123, CE10173, CE10101, CE10122,
  CE10009, CE10013), and the TradingView-valid uses whose earlier lowering
  could not compile (it read an element where the script needs the
  collection, or the current collection where it needs a value): a change
  through a variable or a slice bound to the history, a matrix's history in
  a function, a selection's or a function's result that needs the collection.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ast_nodes import (
    Assignment, BinOp, ExprStmt, ForInStmt, ForStmt, FuncCall, FuncDef,
    Identifier, IfStmt, MemberAccess, MethodDef, NumberLiteral, Program,
    Subscript, SwitchStmt, Ternary, TupleAssign, TupleLiteral, UnaryOp,
    VarDecl, WhileStmt,
)
from .limits import iter_ast_nodes, syntax_children


# Set on a ``Subscript`` the checker supports: a dict with the variable
# (``var``), its history member (``member``), its kind (``array`` /
# ``matrix``) and TypeSpec (``spec``), and how the value is used (``use``):
# ``read`` (a built-in reads it), ``change`` (a built-in changes it: the run
# stops), ``copy`` (bound to a variable or given to a user function, which
# read it), ``loop`` (a for...in iterable: the variable's current array, as
# TradingView iterates) or ``na`` (``na()``).
COLLECTION_HISTORY_ANNOTATION = "pf_collection_history"

SUPPORTED = "supported"
LEGACY = "legacy"
REFUSED = "refused"

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

# The parameters (namespace positions) of the array and matrix built-ins that
# take a collection besides the first: every other one takes a value, where
# TradingView refuses an array (CE10123: fixtures/array_history_tv README).
_COLLECTION_SLOTS = {
    "array": {"concat": {1}, "covariance": {1}},
    "matrix": {"concat": {1}, "sum": {1}, "diff": {1}, "mult": {1},
               "kron": {1}, "add_row": {2}, "add_col": {2}},
}

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

# Built-ins that take numbers, strings or conditions only: TradingView refuses
# an array or a matrix for any of their arguments (CE10123; math.abs, nz,
# plot, a strategy quantity, ta.sma, label.new's text and log.info's message
# were checked: fixtures/array_history_tv README).
_SCALAR_FUNCTIONS = frozenset({
    "nz", "fixnan", "plot", "plotshape", "plotchar", "plotarrow", "plotbar",
    "plotcandle", "bgcolor", "barcolor", "fill", "hline", "alert",
    "alertcondition", "int", "float", "bool", "string", "color", "max_bars_back",
    "timestamp", "time", "time_close", "year", "month", "weekofyear",
    "dayofmonth", "dayofweek", "hour", "minute", "second",
})
_SCALAR_NAMESPACES = frozenset({
    "math", "ta", "strategy", "input", "color", "timeframe", "syminfo",
    "ticker", "label", "line", "box", "table", "linefill", "runtime",
})

# The uses (``Use.tag``; an entry also covers its ``:``-extensions) the
# earlier lowering of a read the checker does not support could not compile,
# from a probe of every use and every array function in every scope on the
# build before this one; a use not listed keeps that lowering. A variable's
# history read an element of the current array -- a number, a bool, a color
# or a string -- and a matrix variable's a ``Series<double>`` that never
# compiled; a parameter's read the parameter's current collection.
#
# A number's element took no array function; ``array.copy`` built a vector
# of the element's size, which compiled where a namespace call or a loop's
# local took it, not where a method or a member did.
_NUMBER_ELEMENT_FAILING_FUNCTIONS = frozenset({
    "abs", "avg", "binary_search", "binary_search_leftmost",
    "binary_search_rightmost", "concat", "covariance", "every", "first", "get",
    "includes", "indexof", "join", "last", "lastindexof", "max", "median",
    "min", "mode", "percentile_linear_interpolation",
    "percentile_nearest_rank", "percentrank", "range", "size", "some",
    "sort_indices", "standardize", "stdev", "sum", "variance", "add_row",
    "add_col",
})
# The array functions returning a new array: whether the earlier lowering
# compiled depends on how that array is used (``_array_result_consumer``):
# called a method on, returned by a function, rendered or held by a member,
# it did not compile.
_ARRAY_RESULT_FUNCTIONS = frozenset({
    "abs", "copy", "slice", "sort_indices", "standardize", "concat",
})
_FAILING_RESULT_CONSUMERS = ("receiver", "result", "render", "member")
_ELEMENT_LOWERING_FAILS = frozenset({
    "read:receiver", "change:receiver", "slice:receiver", "slice:argument",
    "change:argument", "copy:typed", "loop", "read:history", "other:collection",
}) | frozenset(f"read:argument:{f}" for f in _NUMBER_ELEMENT_FAILING_FUNCTIONS) | frozenset(
    f"read:argument:copy:{c}" for c in _FAILING_RESULT_CONSUMERS)
# A ``std::string`` element has ``size()``, ``[]``, ``clear()``, iterators and
# more, so many array functions compiled on it; it has no na and no number
# rendering.
_STRING_ELEMENT_FAILING_FUNCTIONS = frozenset({
    "binary_search", "binary_search_leftmost", "binary_search_rightmost",
    "concat", "copy", "covariance", "includes", "indexof", "lastindexof",
    "add_row", "add_col",
})
_STRING_ELEMENT_LOWERING_FAILS = frozenset({
    "read:receiver", "change:receiver", "slice:receiver", "copy:typed", "na",
    "render:tostring", "read:history", "other:collection",
    "change:argument:push", "change:argument:unshift", "change:argument:insert",
    "change:argument:set", "change:argument:fill",
    # A function returning what these read (typed a number).
    "change:argument:pop:result", "change:argument:shift:result",
    "change:argument:remove:result", "read:argument:get:result",
    "read:argument:first:result", "read:argument:last:result",
    "read:argument:join:result",
}) | frozenset(f"read:argument:{f}" for f in _STRING_ELEMENT_FAILING_FUNCTIONS) | frozenset(
    f"{how}:argument:{f}:{c}"
    for how, f in (("read", "abs"), ("read", "sort_indices"), ("read", "standardize"),
                   ("change", "concat"), ("slice", "slice"))
    for c in _FAILING_RESULT_CONSUMERS)
# A parameter's current array took most array functions; one returning a new
# array, a method on it, na() and rendering did not compile, nor did a
# string array's element-typed reads (typed a number).
_PARAMETER_LOWERING_FAILS = frozenset({
    "read:receiver", "change:receiver", "slice:receiver", "na", "render",
    "copy:untyped",
}) | frozenset(
    f"{how}:argument:{f}:{c}"
    for how, f in (("read", "abs"), ("read", "copy"), ("read", "sort_indices"),
                   ("read", "standardize"), ("slice", "slice"), ("change", "concat"))
    for c in _FAILING_RESULT_CONSUMERS)
# A string parameter's element read where a variable or a function's result
# typed a number took it.
_STRING_PARAMETER_LOWERING_FAILS = _PARAMETER_LOWERING_FAILS | frozenset(
    f"{how}:argument:{f}:{c}"
    for how, names in (
        ("read", ("avg", "covariance", "every", "first", "get", "join", "last",
                  "max", "median", "min", "mode", "percentile_linear_interpolation",
                  "percentile_nearest_rank", "percentrank", "range", "some",
                  "stdev", "sum", "variance")),
        ("change", ("pop", "remove", "shift")))
    for f in names for c in ("result", "bound"))
_MATRIX_RECEIVER_LOWERING_FAILS = frozenset({
    "read:receiver", "change:receiver", "render", "copy:untyped",
    "read:argument:det", "read:argument:copy", "change:argument",
    "other:collection",  # matrix.sum (_matrix_sum_use)
})


def _failing_tag(tags, table) -> str | None:
    """The first of ``tags`` the table lists (an entry covers its
    ``:``-extensions), else None."""
    for tag in sorted(tags):
        if any(tag == entry or tag.startswith(entry + ":") for entry in table):
            return tag
    return None


def _element_name(spec) -> str | None:
    """``float`` / ``int`` / ``bool`` / ``string`` / ``color`` for a
    collection of primitives, ``udt`` for one of objects or drawings."""
    element = getattr(spec, "element", None)
    if element is None:
        return None
    return element.name if element.kind == "primitive" else "udt"

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


def collection_hint(hint) -> str | None:
    """``array`` / ``matrix`` / ``map`` for a collection type hint
    (``array<float>``, ``float[]``, ``matrix<int>``), else None."""
    if not isinstance(hint, str):
        return None
    if hint.endswith("[]") or hint.startswith("array<") or hint == "array":
        return "array"
    if hint.startswith("matrix<") or hint == "matrix":
        return "matrix"
    if hint.startswith("map<") or hint == "map":
        return "map"
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
    """A declaration whose history the script reads, for the codegen."""
    name: str
    kind: str
    spec: object
    is_var: bool
    capacity: int | None           # None: the Series default (dynamic offsets)
    decl_node_id: int | None
    ordinal: int = 0               # its rank among its name's declarations
    # The other declarations writing the same class member (a sibling
    # block's ``x``): each closes this history before it writes.
    closed_by: set[int] = field(default_factory=set)


@dataclass
class Decision:
    outcome: str                   # SUPPORTED | LEGACY | REFUSED
    use: str | None = None         # SUPPORTED: read / change / copy / loop / na
    node: object = None            # REFUSED: where
    message: str | None = None
    hint: str | None = None


@dataclass
class Use:
    """How a value of an array or a matrix is used.

    ``how``: ``read`` / ``change`` (a built-in reads or changes it), ``loop``
    (a for...in iterable), ``na``, ``render`` (``str.tostring``,
    ``str.format``: an element renders in the earlier lowering), ``copy``
    (bound to a name or given to a parameter, whose uses ``names`` sums up),
    ``slice`` (a slice of it, whose uses ``names`` sums up), ``flow`` (a
    selection's arm, a function's result, a request's payload: ``names``
    sums up its consumer), ``discard`` (a value nothing reads), ``reject``
    (TradingView refuses it) and ``other``. ``needs_collection`` marks an
    ``other`` or a ``copy`` that only a collection fits (an array-typed
    parameter or field, a history of the history)."""
    how: str
    node: object = None
    message: str | None = None
    hint: str | None = None
    names: "NameUses | None" = None
    needs_collection: bool = False
    form: str | None = None        # read / change / slice: receiver or argument;
                                   # render: tostring or format

    def tag(self) -> str:
        """The use's class, by which ``_ELEMENT_LOWERING_FAILS`` and its
        siblings tell whether the earlier lowering compiled it."""
        if self.how in ("read", "change", "slice"):
            return f"{self.how}:{self.form or 'argument'}"
        if self.how == "render":
            return f"render:{self.form or 'tostring'}"
        if self.how == "copy":
            return "copy:typed" if self.needs_collection else "copy:untyped"
        if self.how == "other" and self.needs_collection:
            return "other:collection"
        return self.how


@dataclass
class NameUses:
    """The uses a value reaches, summed up: the first that needs the
    collection (``collection``), that an element fits (``element``: ``na``,
    ``str.tostring``), that changes it (``change``), that TradingView
    refuses (``reject``) and any other (``other``)."""
    collection: Use | None = None
    element: Use | None = None
    change: Use | None = None
    reject: Use | None = None
    other: Use | None = None
    tags: dict[str, Use] = field(default_factory=dict)   # every use's class

    def merge(self, other: "NameUses | None") -> None:
        if other is None:
            return
        for key in ("collection", "element", "change", "reject", "other"):
            if getattr(self, key) is None and getattr(other, key) is not None:
                setattr(self, key, getattr(other, key))
        for tag, use in other.tags.items():
            self.tags.setdefault(tag, use)

    def add(self, use: Use) -> None:
        """Fold one use in."""
        self.tags.setdefault(use.tag(), use)
        how = use.how
        if how in ("read", "loop"):
            self.merge(NameUses(collection=use))
        elif how == "change":
            self.merge(NameUses(collection=use, change=use))
        elif how in ("na", "render"):
            self.merge(NameUses(element=use))
        elif how == "reject":
            self.merge(NameUses(reject=use))
        elif how == "slice":
            # The slice's own uses are an array's: only what it changes or
            # TradingView refuses reaches the history.
            self.merge(NameUses(collection=use))
            if use.names is not None:
                self.merge(NameUses(change=use.names.change,
                                    reject=use.names.reject))
                if use.names.element is not None or use.names.other is not None:
                    self.merge(NameUses(other=use.names.element or use.names.other))
        elif how in ("copy", "flow"):
            self.merge(use.names)
            if use.needs_collection:
                self.merge(NameUses(collection=use))
        elif how == "other":
            self.merge(NameUses(other=use))
            if use.needs_collection:
                self.merge(NameUses(collection=use))

    def needs_collection(self) -> bool:
        return self.collection is not None or self.change is not None


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


def _body_lists(node) -> list[list]:
    """The statement lists directly under ``node``."""
    if isinstance(node, (Program, FuncDef, MethodDef, ForStmt, ForInStmt, WhileStmt)):
        return [node.body]
    if isinstance(node, IfStmt):
        return [node.body, node.else_body]
    if isinstance(node, SwitchStmt):
        return [body for _cond, body in node.cases] + [node.default_body]
    return []


def _declares(stmt, name: str) -> bool:
    if isinstance(stmt, VarDecl):
        return stmt.name == name
    if isinstance(stmt, TupleAssign):
        return name in stmt.names
    return False


class CollectionHistoryChecker:
    """Decides every array or matrix history read (see the module
    docstring): ``decide`` gives a read its outcome, memoized by node."""

    def __init__(self, program: Program, name_kind=None):
        """``name_kind(name)`` answers ``array``, ``matrix``, ``map`` or
        ``scalar`` for a script variable the analyzer typed, else None."""
        from .ast_nodes import TypeDecl
        from .codegen.tables import ARRAY_METHODS, MATRIX_METHODS

        self._name_kind = name_kind or (lambda name: None)
        self._types = {stmt.name: stmt for stmt in program.body if isinstance(stmt, TypeDecl)}
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
        # Indexes built once, so a name's reads and a callable's calls cost
        # their own count, not a walk of the script each.
        self._position: dict[int, tuple[object, list, int]] = {}
        self._declaring: dict[tuple[int, str], list[int]] = {}
        self._reads_by_name: dict[str, list[Identifier]] = {}
        self._calls_by_name: dict[tuple[bool, str], list[FuncCall]] = {}
        self._callable_names: dict[int, set[str]] = {}
        for node in self._nodes_by_id.values():
            for body in _body_lists(node):
                for index, stmt in enumerate(body):
                    self._position[id(stmt)] = (node, body, index)
                    for name in ([stmt.name] if isinstance(stmt, VarDecl)
                                 else stmt.names if isinstance(stmt, TupleAssign) else []):
                        self._declaring.setdefault((id(body), name), []).append(index)
            if isinstance(node, Identifier):
                self._reads_by_name.setdefault(node.name, []).append(node)
            elif isinstance(node, FuncCall):
                callee = node.callee
                if isinstance(callee, Identifier):
                    self._calls_by_name.setdefault((False, callee.name), []).append(node)
                elif isinstance(callee, MemberAccess):
                    self._calls_by_name.setdefault((True, callee.member), []).append(node)
        # Every read of a name, under each statement list holding it at any
        # depth: a region's candidates are its own list's, so sibling blocks
        # declaring one name do not scan each other's reads.
        self._reads_under: dict[tuple[int, str], list[Identifier]] = {}
        for name, reads in self._reads_by_name.items():
            for read in reads:
                current = read
                while current is not None:
                    position = self._position.get(id(current))
                    if position is not None:
                        self._reads_under.setdefault((id(position[1]), name), []).append(read)
                    current = self._parent.get(id(current))
        for stmt in program.body:
            if isinstance(stmt, (FuncDef, MethodDef)):
                bound = set(stmt.params)
                for node, _depth in iter_ast_nodes(stmt):
                    if isinstance(node, VarDecl):
                        bound.add(node.name)
                    elif isinstance(node, TupleAssign):
                        bound.update(node.names)
                self._callable_names[id(stmt)] = bound
        self._reachable = self._reachable_callables()
        self._decisions: dict[int, Decision] = {}
        self._uses: dict[tuple, Use] = {}
        self._name_uses: dict[tuple, NameUses] = {}
        self._in_progress: set[tuple] = set()
        # The element type of the collection being decided (``float``,
        # ``string``, ``color``, ``udt``...): an element renders or reads na
        # by its type in the earlier lowering.
        self._element: str | None = None

    # -- the program's structure ---------------------------------------------

    def _ancestors(self, node):
        current = self._parent.get(id(node))
        while current is not None:
            yield current
            current = self._parent.get(id(current))

    def _callable_of(self, node):
        """The function or method whose body holds ``node``, else None."""
        return next((a for a in self._ancestors(node)
                     if isinstance(a, (FuncDef, MethodDef))), None)

    def _calls_in(self, root) -> tuple[set[str], set[str]]:
        functions: set[str] = set()
        methods: set[str] = set()
        for node, _depth in iter_ast_nodes(root):
            if not isinstance(node, FuncCall):
                continue
            callee = node.callee
            if isinstance(callee, Identifier):
                functions.add(callee.name)
            elif isinstance(callee, MemberAccess):
                methods.add(callee.member)
        return functions, methods

    def _reachable_callables(self) -> set[int]:
        """The functions and methods a call from the script's top level
        reaches (methods by name: a receiver's type is not known here). The
        codegen emits no other: anything in their bodies compiled."""
        functions: set[str] = set()
        methods: set[str] = set()
        for stmt in self._program.body:
            if isinstance(stmt, (FuncDef, MethodDef)):
                continue
            f, m = self._calls_in(stmt)
            functions |= f
            methods |= m
        reached: set[int] = set()
        pending = True
        while pending:
            pending = False
            for name, defs in list(self._functions.items()) + list(self._methods.items()):
                called = name in (functions if defs and isinstance(defs[0], FuncDef) else methods)
                if not called:
                    continue
                for definition in defs:
                    if id(definition) in reached:
                        continue
                    reached.add(id(definition))
                    f, m = self._calls_in(definition)
                    functions |= f
                    methods |= m
                    pending = True
        return reached

    def is_dead(self, node) -> bool:
        """Whether ``node`` sits in a function or method nothing calls."""
        owner = self._callable_of(node)
        return owner is not None and id(owner) not in self._reachable

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
        for ancestor in self._ancestors(decl):
            if isinstance(ancestor, (FuncDef, MethodDef)):
                return "callable"
            if isinstance(ancestor, (ForStmt, ForInStmt, WhileStmt)):
                return "loop"
        return "block"

    def _containing_list(self, stmt) -> tuple[object, list] | None:
        position = self._position.get(id(stmt))
        return None if position is None else position[:2]

    # -- the reads of a name ---------------------------------------------------

    def _in_region(self, read, name: str, region: list, start: int,
                   top: bool) -> bool:
        """Whether ``read`` (an identifier spelled ``name``) reads the
        variable a declaration at ``region[start - 1]`` binds -- for a
        parameter, ``start`` 0 of its callable's body: it sits in
        ``region[start:]`` before a later declaration of the name there, and
        no nested statement list or loop between them declares the name
        first. A declaration of the script's top level (``top``) also
        reaches the functions and methods that do not bind the name."""
        current = read
        while True:
            position = self._position.get(id(current))
            if position is not None:
                holder, body, index = position
                declared = self._declaring.get((id(body), name), [])
                if body is region:
                    later = next((i for i in declared if i >= start), None)
                    return index >= start and (later is None or index <= later)
                if isinstance(holder, (FuncDef, MethodDef)):
                    return top and name not in self._callable_names.get(id(holder), ())
                if declared and declared[0] < index:
                    return False
                if body is getattr(holder, "body", None) and (
                        (isinstance(holder, ForStmt) and holder.var == name)
                        or (isinstance(holder, ForInStmt)
                            and (holder.var == name or name in (holder.vars or [])))):
                    return False
            current = self._parent.get(id(current))
            if current is None:
                return False

    def _region_reads(self, name: str, region: list, start: int, top: bool) -> list:
        """The reads of ``name`` in a region (``_in_region``), in source order."""
        reads = [read for read in self._reads_under.get((id(region), name), ())
                 if self._in_region(read, name, region, start, top)]
        reads.sort(key=lambda r: (getattr(r.loc, "line", 0) or 0,
                                  getattr(r.loc, "column", 0) or 0))
        return reads

    def _declaration_reads(self, decl) -> list:
        """The reads a declaration's value reaches through its name: the
        rest of its statement list and, for the top level, the functions and
        methods that read the script variable."""
        position = self._position.get(id(decl))
        if position is None:
            return []
        holder, body, index = position
        return self._region_reads(decl.name, body, index + 1, isinstance(holder, Program))

    def _declaration_of(self, name: str, node):
        """The declaration (a ``VarDecl``, or ``(definition, index)`` for a
        parameter) that ``name`` read at ``node`` resolves to, else None."""
        current = node
        while current is not None:
            parent = self._parent.get(id(current))
            if parent is None:
                return None
            if isinstance(parent, (FuncDef, MethodDef)) and name in parent.params:
                return (parent, parent.params.index(name))
            position = self._position.get(id(current))
            if position is not None:
                _holder, body, index = position
                for earlier in reversed(body[:index]):
                    if isinstance(earlier, VarDecl) and earlier.name == name:
                        return earlier
                    if _declares(earlier, name):
                        return None
            current = parent
        return None

    # -- uses ----------------------------------------------------------------

    def _call_namespace(self, call: FuncCall) -> str | None:
        callee = call.callee
        if isinstance(callee, MemberAccess) and isinstance(callee.object, Identifier):
            return callee.object.name
        return None

    def _builtin_methods(self, kind: str) -> frozenset[str]:
        return self._array_methods if kind == "array" else self._matrix_methods

    def _changing(self, kind: str, method: str) -> bool:
        table = ARRAY_CHANGING_METHODS if kind == "array" else MATRIX_CHANGING_METHODS
        return method in table

    def use(self, node, kind: str, label: str) -> Use:
        """How the value of ``node`` -- an array or a matrix: a history
        read, a name bound to one, a call or a selection holding one -- is
        used. Memoized."""
        key = (id(node), kind, self._element)
        cached = self._uses.get(key)
        if cached is not None:
            return cached
        if key in self._in_progress:
            return Use("discard", node)
        self._in_progress.add(key)
        try:
            result = self._use(node, kind, label)
        finally:
            self._in_progress.discard(key)
        self._uses[key] = result
        return result

    def _reject(self, node, message: str, hint: str | None = None) -> Use:
        return Use("reject", node, message, hint)

    def _use(self, node, kind: str, label: str) -> Use:
        parent = self._parent.get(id(node))
        if isinstance(parent, MemberAccess) and parent.object is node:
            return self._receiver_use(parent, node, kind, label)
        if isinstance(parent, FuncCall) and parent.callee is not node:
            return self._argument_use(parent, node, kind, label)
        if isinstance(parent, ForInStmt) and parent.iterable is node:
            return Use("loop", node)
        if isinstance(parent, VarDecl) and parent.value is node:
            return self._declaration_use(parent, node, kind, label)
        if isinstance(parent, Assignment):
            if parent.target is node:
                # ``a[k] := v``: TradingView refuses it (CE10009, CE10013 for a
                # computed offset); the earlier lowering set the current
                # array's element and compiled, so it keeps it.
                return Use("other", node,
                           f"{label} := ...: an assignment to the history-referencing "
                           "operator is not supported in PineForge.")
            return self._assignment_use(parent, node, kind, label)
        if isinstance(parent, (BinOp, UnaryOp)):
            return self._reject(
                node, f"{label} is {_a(kind)}, which TradingView refuses for "
                f"operator {parent.op} (CE10123).")
        if isinstance(parent, Ternary):
            if parent.condition is node:
                return self._reject(
                    node, f"{label} is {_a(kind)}, which TradingView refuses as "
                    "the condition of ?: (CE10123).")
            return self._flow(parent, kind, label, node)
        if isinstance(parent, (IfStmt, WhileStmt)) and parent.condition is node:
            statement = "if" if isinstance(parent, IfStmt) else "while"
            return self._reject(
                node, f"{label} is {_a(kind)}, which TradingView refuses as the "
                f"condition of an {statement} statement (CE10101).")
        if isinstance(parent, Subscript) and parent.object is node:
            outer = NameUses()
            outer.add(self.use(parent, kind, f"({label})[...]"))
            return Use("other", node,
                       f"({label})[...]: the history of {_a(kind)}'s history "
                       "is not supported in PineForge.",
                       needs_collection=outer.needs_collection())
        if isinstance(parent, ExprStmt):
            return self._statement_use(parent, node, kind, label)
        if isinstance(parent, TupleLiteral):
            return self._tuple_flow(parent, node, kind, label)
        return Use("other", node, f"{label} is not supported in PineForge here.")

    def _receiver_use(self, access: MemberAccess, node, kind: str, label: str) -> Use:
        call = self._parent.get(id(access))
        method = access.member
        if not (isinstance(call, FuncCall) and call.callee is access):
            return Use("other", node, f"{label}.{method}: {_a(kind)} has no fields.",
                       needs_collection=True)
        if method in self._builtin_methods(kind):
            if kind == "matrix" and method == "sum":
                return self._matrix_sum_use(node, label)
            if kind == "array" and method == "slice":
                return self._slice_use(call, node, label, "receiver")
            if self._changing(kind, method):
                return Use("change", node, form="receiver")
            return Use("read", node, form="receiver")
        if method in self._methods:
            definitions = self._definitions_for(self._methods[method], 0, None, kind)
            return Use("copy", node, names=self._parameter_uses(
                definitions, 0, None, kind), needs_collection=True, form="receiver")
        return Use("other", node,
                   f"{label}.{method}(): no array or matrix method of that name.",
                   needs_collection=True)

    def _matrix_sum_use(self, node, label: str) -> Use:
        """``matrix.sum(m[k], m2)``: its matrix result does not compile,
        history or not (the earlier build refused the history)."""
        return Use("other", node,
                   f"matrix.sum of {label} is not supported in PineForge: the "
                   "matrix it returns does not compile.", needs_collection=True)

    def _slice_use(self, call: FuncCall, node, label: str, form: str) -> Use:
        """``(a[k]).slice(...)``: TradingView's slice shares the history's
        elements, so a change to it stops the run (RE10051)."""
        names = NameUses()
        names.add(self.use(call, "array", f"{label}.slice()"))
        return Use("slice", node, names=names, form=form)

    def _argument_use(self, call: FuncCall, node, kind: str, label: str) -> Use:
        callee = call.callee
        positional = next((i for i, a in enumerate(call.args) if a is node), None)
        keyword = next((k for k, v in call.kwargs.items() if v is node), None)
        namespace = self._call_namespace(call)
        if isinstance(callee, Identifier):
            name = callee.name
            if name == "na":
                return Use("na", node)
            if name in self._functions:
                definitions = self._definitions_for(
                    self._functions[name], positional, keyword, kind)
                return Use("copy", node,
                           names=self._parameter_uses(definitions, positional, keyword, kind),
                           needs_collection=self._parameter_is_collection(
                               definitions, positional, keyword))
            if name in _SCALAR_FUNCTIONS:
                return self._reject(
                    node, f"{label} is {_a(kind)}, which TradingView refuses as an "
                    f"argument of {name} (CE10123).")
            return Use("other", node, f"{label} as an argument of {name} is not "
                       "supported in PineForge.")
        if not isinstance(callee, MemberAccess):
            return Use("other", node, f"{label} is not supported in PineForge here.")
        member = callee.member
        if namespace in ("array", "matrix"):
            methods = self._array_methods if namespace == "array" else self._matrix_methods
            if member == "from" and namespace == "array":
                return self._reject(
                    node, f"{label} is {_a(kind)}, which TradingView refuses as an "
                    "element of array.from (CE10122).")
            if namespace == "matrix" and member == "sum" and kind == "matrix":
                return self._matrix_sum_use(node, label)
            if member in methods:
                return self._builtin_slot_use(
                    namespace, member, positional, keyword, node, kind, label)
            return self._reject(
                node, f"{label} is {_a(kind)}, which TradingView refuses as an "
                f"argument of {namespace}.{member} (CE10123).")
        if namespace in self._types or namespace in ("chart",):
            if member == "new":
                return Use("other", node,
                           f"{label} as a field of a new {namespace} is not supported "
                           "in PineForge: the field would hold a changeable copy, "
                           "where TradingView's holds the read-only history.",
                           needs_collection=self._field_is_collection(
                               namespace, positional, keyword))
        if namespace == "str":
            if member == "tostring" and (positional == 0 or keyword == "value"):
                return self._render_use(node, kind, label, "str.tostring")
            if member == "format" and positional not in (None, 0):
                return self._render_use(node, kind, label, "str.format")
            return self._reject(
                node, f"{label} is {_a(kind)}, which TradingView refuses as an "
                f"argument of str.{member} (CE10123).")
        if namespace == "log":
            if positional not in (None, 0):
                return self._render_use(node, kind, label, f"log.{member}")
            return self._reject(
                node, f"{label} is {_a(kind)}, which TradingView refuses as the "
                f"message of log.{member} (CE10123).")
        if namespace == "request":
            return self._flow(call, kind, label, node)
        if namespace in _SCALAR_NAMESPACES:
            return self._reject(
                node, f"{label} is {_a(kind)}, which TradingView refuses as an "
                f"argument of {namespace}.{member} (CE10123).")
        if namespace not in _BUILTIN_NAMESPACES and member in self._methods:
            # A user method called on another receiver: a parameter after it.
            if len(self._methods[member]) > 1:
                # Overloads: the earlier lowering passed the element to the
                # one taking a number, which compiled.
                return Use("other", node, f"{label} as an argument of the "
                           f"overloaded method {member} is not supported in "
                           "PineForge.")
            position = None if positional is None else positional + 1
            definitions = self._definitions_for(
                self._methods[member], position, keyword, kind)
            return Use("copy", node, names=self._parameter_uses(
                definitions, position, keyword, kind),
                needs_collection=self._parameter_is_collection(
                    definitions, position, keyword))
        if (namespace not in _BUILTIN_NAMESPACES and namespace not in self._types
                and (member in self._array_methods or member in self._matrix_methods)):
            # A built-in method on another collection (``c.concat(a[1])``,
            # ``m.add_row(0, a[1])``): its namespace position is one more.
            spaces = [ns for ns, table in (("array", self._array_methods),
                                           ("matrix", self._matrix_methods))
                      if member in table]
            receiver_kind = (self._name_kind(callee.object.name)
                             if isinstance(callee.object, Identifier) else None)
            if receiver_kind in spaces:
                spaces = [receiver_kind]
            position = None if positional is None else positional + 1
            if any(position in _COLLECTION_SLOTS.get(ns, {}).get(member, set())
                   for ns in spaces):
                return Use("read", node, form=f"argument:{member}")
            return self._reject(
                node, f"{label} is {_a(kind)}, which TradingView refuses as a "
                f"value of {member}() (CE10123).")
        return Use("other", node, f"{label} as an argument of "
                   f"{namespace or 'the function'}.{member} is not supported in "
                   "PineForge.")

    def _builtin_slot_use(self, namespace: str, member: str, positional, keyword,
                          node, kind: str, label: str) -> Use:
        first = positional == 0 or keyword in ("id", "id1")
        form = f"argument:{member}"
        if first:
            call = self._parent[id(node)]
            if namespace == "array" and member in _ARRAY_RESULT_FUNCTIONS:
                form += self._array_result_consumer(call)
            if namespace == "array" and member == "slice":
                return self._slice_use(call, node, label, form)
            if namespace == "array" and member not in _ARRAY_RESULT_FUNCTIONS:
                form += self._value_consumer(call)
            if self._changing(namespace, member):
                return Use("change", node, form=form)
            return Use("read", node, form=form)
        slots = _COLLECTION_SLOTS.get(namespace, {}).get(member, set())
        if positional in slots or keyword in ("id2", "array_id"):
            return Use("read", node, form=form)
        return self._reject(
            node, f"{label} is {_a(kind)}, which TradingView refuses as a value "
            f"of {namespace}.{member} (CE10123).")

    def _array_result_consumer(self, call: FuncCall) -> str:
        """How an array function's new array (``array.copy(a[k])``) is used,
        for the earlier lowering's verdict (``_ARRAY_RESULT_FUNCTIONS``):
        ``:receiver`` for a method called on it, ``:result`` for a function
        returning it, ``:render`` for ``str.tostring``, ``:member`` for a
        variable of the script's top level or a block's holding it, else
        none (a namespace call's or a user function's argument, a loop's or
        a function's local, a for...in iterable)."""
        consumer = self.use(call, "array", "array function's result")
        if consumer.form == "receiver":
            return ":receiver"
        if consumer.how == "render":
            return ":render"
        if consumer.how == "flow" and consumer.form == "result":
            return ":result"
        parent = self._parent.get(id(call))
        if isinstance(parent, (VarDecl, Assignment)) and self._callable_of(parent) is None \
                and not any(isinstance(a, (ForStmt, ForInStmt, WhileStmt))
                            for a in self._ancestors(parent)):
            return ":member"
        return ""

    def _value_consumer(self, call: FuncCall) -> str:
        """Where an array function's value goes, for the earlier lowering's
        verdict on a string element or a string array parameter: ``:result``
        when a function or a method returns it, ``:bound`` when a variable
        takes it (typed a number there), else none (a statement, an
        argument, an operand)."""
        parent = self._parent.get(id(call))
        if isinstance(parent, ExprStmt):
            located = self._containing_list(parent)
            if (located is not None and isinstance(located[0], (FuncDef, MethodDef))
                    and located[1][-1] is parent):
                return ":result"
            return ""
        if isinstance(parent, (VarDecl, Assignment)) and parent.value is call:
            return ":bound"
        return ""

    def _render_use(self, node, kind: str, label: str, function: str) -> Use:
        """``str.tostring(a[k])``, ``str.format(..., a[k])``: TradingView
        renders an array of numbers, bools or strings and refuses one of
        colors or objects (CE10123, CE10122 for str.format:
        fixtures/array_history_tv README); the earlier lowering rendered an
        element."""
        if self._element in ("color", "udt"):
            # TradingView answers CE10123 for str.tostring and CE10122 (an
            # argument of int, float, bool or string expected) for
            # str.format.
            what = "colors" if self._element == "color" else "objects"
            code = "CE10123" if function == "str.tostring" else "CE10122"
            return self._reject(
                node, f"{label} is an array of {what}, which TradingView refuses "
                f"for {function} ({code}).")
        return Use("render", node, form="tostring" if function == "str.tostring"
                   else "format")

    def _declaration_use(self, decl: VarDecl, node, kind: str, label: str) -> Use:
        if decl.type_hint in _SCALAR_HINTS:
            return self._reject(
                node, f"{label} is {_a(kind)}, which TradingView refuses for the "
                f"{decl.type_hint} variable {decl.name} (CE10173).")
        return Use("copy", node, names=self._declaration_uses(decl, kind),
                   needs_collection=collection_hint(decl.type_hint) is not None)

    def _assignment_use(self, assignment: Assignment, node, kind: str, label: str) -> Use:
        if assignment.op != ":=":
            return self._reject(
                node, f"{label} is {_a(kind)}, which TradingView refuses for "
                f"operator {assignment.op} (CE10123).")
        target = assignment.target
        if not isinstance(target, Identifier):
            field_type = self._field_type(target, assignment)
            if field_type in _SCALAR_HINTS:
                return self._reject(
                    node, f"{label} is {_a(kind)}, which TradingView refuses for "
                    f"the {field_type} field {target.member} (CE10173).")
            return Use("other", node,
                       f"{label} assigned to a field is not supported in PineForge: "
                       "the field would hold a changeable copy, where TradingView's "
                       "holds the read-only history.",
                       needs_collection=collection_hint(field_type) is not None)
        decl = self._declaration_of(target.name, assignment)
        if isinstance(decl, VarDecl):
            if decl.type_hint in _SCALAR_HINTS:
                return self._reject(
                    node, f"{label} is {_a(kind)}, which TradingView refuses for "
                    f"the {decl.type_hint} variable {target.name} (CE10173).")
            from_history = (isinstance(decl.value, Subscript)
                            and isinstance(decl.value.object, Identifier))
            return Use("copy", node, names=self._declaration_uses(decl, kind),
                       needs_collection=not from_history)
        if isinstance(decl, tuple):
            definition, index = decl
            return Use("copy", node, names=self._parameter_uses(
                [definition], index, None, kind), needs_collection=True)
        target_kind = self._name_kind(target.name)
        if target_kind == "scalar" or (target_kind is not None and target_kind != kind):
            return self._reject(
                node, f"{label} is {_a(kind)}, which TradingView refuses for the "
                f"variable {target.name} of another type (CE10173).")
        return Use("other", node, f"{label} assigned to {target.name} is not "
                   "supported in PineForge here.")

    def _field_type(self, target, at) -> str | None:
        """The declared type of ``obj.field`` when ``obj`` is a variable
        whose declaration names its user-defined type, else None."""
        if not (isinstance(target, MemberAccess) and isinstance(target.object, Identifier)):
            return None
        decl = self._declaration_of(target.object.name, at)
        type_name = None
        if isinstance(decl, VarDecl):
            type_name = decl.type_hint
            value = decl.value
            if (type_name is None and isinstance(value, FuncCall)
                    and isinstance(value.callee, MemberAccess)
                    and isinstance(value.callee.object, Identifier)
                    and value.callee.member in ("new", "copy")):
                type_name = value.callee.object.name
        elif isinstance(decl, tuple):
            definition, index = decl
            hints = (definition.annotations or {}).get("param_type_hints") or []
            type_name = hints[index] if index < len(hints) else None
        type_decl = self._types.get(type_name) if isinstance(type_name, str) else None
        if type_decl is None:
            return None
        return next((f.type_name for f in type_decl.fields if f.name == target.member), None)

    def _statement_use(self, stmt: ExprStmt, node, kind: str, label: str) -> Use:
        located = self._containing_list(stmt)
        holder, body = located if located is not None else (None, [])
        last = bool(body) and body[-1] is stmt
        if isinstance(holder, Program):
            return self._reject(
                node, f"{label} as a statement: TradingView reads it as a "
                "declaration and refuses it (CE10009, \"Extraneous input\").")
        if not last:
            return Use("discard", node)
        if isinstance(holder, (FuncDef, MethodDef)):
            return self._result_use(holder, kind, label, node)
        if isinstance(holder, (IfStmt, SwitchStmt)):
            return self._flow(holder, kind, label, node)
        return Use("discard", node)

    def _flow(self, holder, kind: str, label: str, node) -> Use:
        """A value an expression holding ``node`` produces: a selection's
        arm (``c ? a[1] : b``, an if or switch block's value), a request's
        payload. Its consumer is the holder's."""
        if isinstance(holder, (IfStmt, SwitchStmt)):
            parent = self._parent.get(id(holder))
            located = self._containing_list(holder)
            if located is not None:
                outer, body = located
                if body[-1] is not holder:
                    return Use("discard", node)
                if isinstance(outer, (FuncDef, MethodDef)):
                    return self._result_use(outer, kind, label, node)
                if isinstance(outer, (IfStmt, SwitchStmt)):
                    return self._flow(outer, kind, label, node)
                return Use("discard", node)
            if parent is None:
                return Use("discard", node)
        names = NameUses()
        names.add(self.use(holder, kind, label))
        return Use("flow", node, names=names)

    def _tuple_flow(self, tuple_node: TupleLiteral, node, kind: str, label: str) -> Use:
        """An element of a function's result tuple: each tuple declaration
        of a call binds it to the name at its index (``[p, q] = f()``)."""
        index = next(i for i, e in enumerate(tuple_node.elements) if e is node)
        statement = self._parent.get(id(tuple_node))
        located = (self._containing_list(statement)
                   if isinstance(statement, ExprStmt) else None)
        if located is None or not isinstance(located[0], (FuncDef, MethodDef)) \
                or located[1][-1] is not statement:
            return Use("other", node, f"{label} in a tuple is not supported in PineForge.")
        definition = located[0]
        names = NameUses()
        for call in self._calls_by_name.get(
                (isinstance(definition, MethodDef), definition.name), ()):
            decl = self._parent.get(id(call))
            position = self._position.get(id(decl))
            if (not isinstance(decl, TupleAssign) or decl.value is not call
                    or index >= len(decl.names) or position is None):
                continue
            holder, body, at = position
            name = decl.names[index]
            names.merge(self._uses_of_name(
                ("tuple", id(decl), index, kind), name,
                lambda n=name, b=body, i=at, top=isinstance(holder, Program):
                    self._region_reads(n, b, i + 1, top),
                kind))
        return Use("flow", node, names=names)

    def _result_use(self, definition, kind: str, label: str, node) -> Use:
        """A function's or a method's result: each call's use."""
        names = NameUses()
        method = isinstance(definition, MethodDef)
        for call in self._calls_by_name.get((method, definition.name), ()):
            names.add(self.use(call, kind, f"{definition.name}()"))
        return Use("flow", node, names=names, form="result")

    def _definitions_for(self, definitions, index, keyword, kind: str) -> list:
        """The overloads (or same-named methods of other types) whose
        parameter at ``index`` / ``keyword`` can take a collection of
        ``kind``: typed as one, or untyped. All of them when none can, so a
        refusal still names a use."""
        fitting = []
        for definition in definitions:
            params = list(definition.params)
            position = params.index(keyword) if keyword in params else index
            if position is None or position >= len(params):
                continue
            hints = (definition.annotations or {}).get("param_type_hints") or []
            hint = hints[position] if position < len(hints) else None
            if isinstance(definition, MethodDef) and position == 0:
                hint = hint or definition.type_name
            if hint is None or collection_hint(hint) == kind:
                fitting.append(definition)
        return fitting or list(definitions)

    def _parameter_is_collection(self, definitions, index, keyword) -> bool:
        for definition in definitions:
            params = list(definition.params)
            position = params.index(keyword) if keyword in params else index
            if position is None or position >= len(params):
                continue
            hints = (definition.annotations or {}).get("param_type_hints") or []
            hint = hints[position] if position < len(hints) else None
            if isinstance(definition, MethodDef) and position == 0:
                hint = hint or definition.type_name
            if collection_hint(hint) is not None:
                return True
        return False

    def _field_is_collection(self, type_name: str, index, keyword) -> bool:
        decl = self._types.get(type_name)
        if decl is None:
            return False
        fields_ = list(decl.fields)
        position = next((i for i, f in enumerate(fields_) if f.name == keyword),
                        index)
        if position is None or position >= len(fields_):
            return False
        return collection_hint(fields_[position].type_name) is not None

    def _parameter_uses(self, definitions, index, keyword, kind: str) -> NameUses:
        names = NameUses()
        for definition in definitions:
            params = list(definition.params)
            position = params.index(keyword) if keyword in params else index
            if position is None or position >= len(params):
                continue
            key = ("param", id(definition), position, kind)
            names.merge(self._uses_of_name(
                key, params[position],
                lambda d=definition, p=params[position]: self._region_reads(p, d.body, 0, False),
                kind))
        return names

    def _declaration_uses(self, decl: VarDecl, kind: str) -> NameUses:
        return self._uses_of_name(("decl", id(decl), kind), decl.name,
                                  lambda: self._declaration_reads(decl), kind)

    def _uses_of_name(self, key: tuple, name: str, reads, kind: str) -> NameUses:
        """The uses of a name bound to the value, summed up; memoized, and
        empty for a name already being summed up (a cycle of bindings)."""
        key = key + (self._element,)
        cached = self._name_uses.get(key)
        if cached is not None:
            return cached
        if key in self._in_progress:
            return NameUses()
        self._in_progress.add(key)
        try:
            names = NameUses()
            for read in reads():
                parent = self._parent.get(id(read))
                if isinstance(parent, Assignment) and parent.target is read:
                    continue
                if isinstance(parent, Subscript) and parent.object is read:
                    # The name's own history, a read of its own: the name
                    # must hold a collection where that read needs one.
                    own = NameUses()
                    own.add(self.use(parent, kind, f"{name}[...]"))
                    if own.needs_collection():
                        names.add(Use("read", read, form="history"))
                    continue
                names.add(self.use(read, kind, name))
        finally:
            self._in_progress.discard(key)
        self._name_uses[key] = names
        return names

    # -- decisions -------------------------------------------------------------

    def _scope_refusal(self, read: HistoryRead) -> str | None:
        """Why PineForge keeps no history of the variable read, else None."""
        label = _spelling(read.node)
        if self._in_request(read.node):
            return (f"{label} inside a request.security expression is not "
                    "supported in PineForge: TradingView reads the copies the "
                    "requested timeframe's bars left, which PineForge does not keep.")
        if read.in_callable:
            what = ("parameter" if read.is_parameter
                    else "script variable read in a function" if read.is_global
                    else "function's local")
            return (f"{label}: the history of {_a(read.kind)} {what} is not "
                    "supported in PineForge (TradingView reads one copy per "
                    "call: fixtures/array_history_tv ahist_fn).")
        scope = self.declaration_scope(read.decl_node_id)
        if scope == "loop":
            return (f"{label}: the history of {_a(read.kind)} declared in a loop "
                    "is not supported in PineForge.")
        if read.scope_name.startswith("top_"):
            return (f"{label}: the history of {_a(read.kind)} declared in a block "
                    "that redeclares a script variable is not supported in "
                    "PineForge (such a block's declarations are C++ locals).")
        if scope in ("callable", "unknown") or not read.is_global:
            return (f"{label}: the history of this {read.kind} is not supported "
                    "in PineForge (only a variable the script's top level or one "
                    "of its blocks declares keeps one).")
        if read.is_var and scope != "top":
            return (f"{label}: the history of a block's var {read.kind} is not "
                    "supported in PineForge.")
        return None

    def decide(self, read: HistoryRead) -> Decision:
        """The outcome of a history read (see the module docstring)."""
        cached = self._decisions.get(id(read.node))
        if cached is not None:
            return cached
        decision = self._decide(read)
        self._decisions[id(read.node)] = decision
        return decision

    def decision_for(self, node) -> Decision | None:
        return self._decisions.get(id(node))

    def _decide(self, read: HistoryRead) -> Decision:
        label = _spelling(read.node)
        self._element = _element_name(read.spec)
        use = self.use(read.node, read.kind, label)
        uses = NameUses()
        uses.add(use)
        if self.is_dead(read.node):
            # The codegen emits no function nothing calls; a matrix's read
            # registers nothing (its Series made the variable a number).
            return Decision(LEGACY)
        if uses.reject is not None:
            return Decision(REFUSED, node=uses.reject.node or read.node,
                            message=uses.reject.message, hint=uses.reject.hint)
        why = self._scope_refusal(read)
        if why is None:
            decision = self._decide_supported(read, use, uses, label)
            if decision is not None:
                return decision
        elif read.is_parameter:
            return self._decide_parameter(read, use, why)
        return self._decide_earlier(read, uses, label, why)

    def _decide_supported(self, read: HistoryRead, use: Use, uses: NameUses,
                          label: str) -> Decision | None:
        """A read of a variable whose history the codegen keeps: SUPPORTED
        where it lowers the use, REFUSED where neither it nor the earlier
        lowering can, None for the earlier lowering's call."""
        kind = read.kind
        if use.how in ("read", "change", "loop", "na"):
            return Decision(SUPPORTED, use.how)
        if use.how == "slice":
            if uses.change is not None:
                return self._change_refusal(read, label, uses.change, "a slice of it")
            if uses.other is not None:
                return Decision(REFUSED, node=uses.other.node or read.node,
                                message=uses.other.message or
                                f"{label}.slice() is not supported in PineForge here.")
            return Decision(SUPPORTED, "read")
        if use.how == "copy":
            if uses.change is not None:
                return self._change_refusal(read, label, uses.change, "a name bound to it")
            if uses.needs_collection():
                if uses.element is not None:
                    return Decision(
                        REFUSED, node=read.node,
                        message=(f"{label} is bound where the script reads it both "
                                 f"as {_a(kind)} and through na() or str.tostring(): "
                                 "that is not supported in PineForge, which holds no "
                                 f"na {kind} and does not render one."))
                if uses.other is not None:
                    return Decision(REFUSED, node=uses.other.node or read.node,
                                    message=uses.other.message or
                                    f"{label} is not supported in PineForge here.")
                return Decision(SUPPORTED, "copy")
            return None
        if kind == "matrix" and use.how == "discard":
            return Decision(SUPPORTED, "copy")
        return None

    def _decide_earlier(self, read: HistoryRead, uses: NameUses, label: str,
                        why: str | None) -> Decision:
        """A read the codegen does not lower: the earlier lowering (an
        element of the current array) where it compiled, else REFUSED."""
        if read.kind == "matrix":
            specific = uses.other.message if uses.other is not None else None
            return Decision(REFUSED, node=read.node, message=why or specific or (
                f"{label} is not supported in PineForge here: the history of a "
                "matrix is supported as the receiver or an argument of a matrix "
                "function, na(), and the value of a variable or a function's "
                "argument that reads it."))
        table = (_STRING_ELEMENT_LOWERING_FAILS if self._element == "string"
                 else _ELEMENT_LOWERING_FAILS)
        failing = _failing_tag(uses.tags, table)
        if failing is None:
            return Decision(LEGACY)
        message = uses.tags[failing].message
        return Decision(REFUSED, node=read.node, message=why or message or (
            f"{label} is not supported in PineForge here: the history of an "
            "array is supported as the receiver or an argument of an array "
            "function, a for...in iterable, na(), and the value of a variable "
            "or a function's argument that reads it."))

    def _decide_parameter(self, read: HistoryRead, use: Use, why: str) -> Decision:
        """A parameter's history: the earlier lowering read the parameter's
        current collection, where it compiled (with a warning), else
        REFUSED."""
        owner = self._callable_of(read.node)
        receiver = (isinstance(owner, MethodDef) and bool(owner.params)
                    and owner.params[0] == read.name)
        tags = {use.tag()}
        if read.kind == "matrix":
            if receiver and _failing_tag(tags, _MATRIX_RECEIVER_LOWERING_FAILS) is None:
                return Decision(LEGACY, "series")
            return Decision(REFUSED, node=read.node, message=why)
        table = (_STRING_PARAMETER_LOWERING_FAILS if self._element == "string"
                 else _PARAMETER_LOWERING_FAILS)
        if _failing_tag(tags, table) is None:
            return Decision(LEGACY)
        return Decision(REFUSED, node=read.node, message=why)

    def decide_expression(self, node: Subscript, spec) -> Decision:
        """The history of an expression whose value is a collection (a
        selection's, a call's): the earlier lowering read an element of a
        selection's current array, where it compiled; a call's never
        compiled. Memoized by node."""
        cached = self._decisions.get(id(node))
        if cached is None:
            cached = self._decisions[id(node)] = self._decide_expression(node, spec)
        return cached

    def _decide_expression(self, node: Subscript, spec) -> Decision:
        kind = spec.kind
        what = ("a call's" if isinstance(node.object, FuncCall)
                else "a selection's" if isinstance(node.object, Ternary)
                else "an expression's")
        label = "(...)[k]"
        self._element = _element_name(spec)
        use = self.use(node, kind, label)
        uses = NameUses()
        uses.add(use)
        if self.is_dead(node):
            return Decision(LEGACY)
        if uses.reject is not None:
            return Decision(REFUSED, node=uses.reject.node or node,
                            message=uses.reject.message, hint=uses.reject.hint)
        table = (_STRING_ELEMENT_LOWERING_FAILS if self._element == "string"
                 else _ELEMENT_LOWERING_FAILS)
        if (isinstance(node.object, Ternary) and kind == "array"
                and _failing_tag(uses.tags, table) is None):
            return Decision(LEGACY, "element")
        return Decision(
            REFUSED, node=node,
            message=(f"The history of {what} {kind} is not supported in "
                     "PineForge here: TradingView reads the copy that expression "
                     "produced that many bars back, which PineForge does not "
                     "keep."),
            hint="Bind the expression to a variable and read the variable's history.")

    def decide_parameter_history(self, node: Subscript, spec) -> Decision:
        """``x[k]`` of a parameter an array or a matrix reaches: a typed
        parameter as ``decide`` decides it; an untyped one's earlier lowering
        never compiled."""
        cached = self._decisions.get(id(node))
        if cached is not None:
            return cached
        owner = self._callable_of(node)
        name = node.object.name
        if owner is None or name not in owner.params:
            return Decision(LEGACY)
        index = owner.params.index(name)
        hints = (owner.annotations or {}).get("param_type_hints") or []
        hint = hints[index] if index < len(hints) else None
        if isinstance(owner, MethodDef) and index == 0:
            hint = hint or owner.type_name
        read = HistoryRead(
            node=node, name=name, kind=spec.kind, spec=spec, in_callable=True,
            is_parameter=True, is_global=False, is_var=False, decl_node_id=None,
            scope_name="")
        decision = self.decide(read)
        if (decision.outcome == LEGACY and collection_hint(hint) is None
                and not self.is_dead(node)):
            decision = Decision(REFUSED, node=node, message=self._scope_refusal(read))
            self._decisions[id(node)] = decision
        return decision

    def _change_refusal(self, read: HistoryRead, label: str, change: Use,
                        through: str) -> Decision:
        line = getattr(getattr(change.node, "loc", None), "line", None)
        return Decision(
            REFUSED, node=read.node,
            message=(f"{label} reaches a change through {through} "
                     f"(line {line if line is not None else '?'}): TradingView "
                     "stops the run there (RE10051: \"Cannot modify the elements "
                     "of a historical array\"); PineForge, which holds a copy, "
                     "does not."),
            hint=f"Copy it first (array.copy({label}) or matrix.copy()) to change it.")

    # -- the codegen's history members -------------------------------------------

    def _source_position(self, node_id: int | None) -> tuple:
        loc = getattr(self._nodes_by_id.get(node_id), "loc", None)
        return (getattr(loc, "line", 0) or 0, getattr(loc, "column", 0) or 0)

    def history_variables(self, reads: list[HistoryRead],
                          writers: dict[str, set[int]]) -> dict[str, HistoryVariable]:
        """The declarations whose history the supported reads need, keyed
        ``name`` (``name#<ordinal>`` for a later declaration of the name);
        each read's annotation names its key (``member``)."""
        by_declaration: dict[tuple, HistoryVariable] = {}
        keyed: list[tuple[dict, tuple]] = []
        for read in reads:
            decision = self._decisions.get(id(read.node))
            annotation = history_annotation(read.node)
            if decision is None or decision.outcome != SUPPORTED or annotation is None:
                continue
            offset = literal_offset(read.node)
            if decision.use == "loop" or offset == 0:
                continue
            key = (read.name, read.decl_node_id)
            entry = by_declaration.get(key)
            if entry is None:
                entry = by_declaration[key] = HistoryVariable(
                    name=read.name, kind=read.kind, spec=read.spec,
                    is_var=read.is_var, capacity=0, decl_node_id=read.decl_node_id)
            if entry.capacity is not None:
                entry.capacity = (None if offset is None
                                  else max(entry.capacity, offset + 1))
            keyed.append((annotation, key))
        names: dict[str, list[HistoryVariable]] = {}
        for entry in by_declaration.values():
            names.setdefault(entry.name, []).append(entry)
        variables: dict[str, HistoryVariable] = {}
        keys: dict[tuple, str] = {}
        for name, entries in names.items():
            entries.sort(key=lambda e: self._source_position(e.decl_node_id))
            for ordinal, entry in enumerate(entries):
                entry.ordinal = ordinal
                entry.closed_by = set(writers.get(name, set())) - {entry.decl_node_id}
                member_key = name if ordinal == 0 else f"{name}#{ordinal}"
                variables[member_key] = entry
                keys[(name, entry.decl_node_id)] = member_key
        for annotation, key in keyed:
            annotation["member"] = keys[key]
        return variables
