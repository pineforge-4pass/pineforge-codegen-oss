"""Immutable bound on the script-owned objects one execution of the script body creates.

A host reserves scratch for the objects a script owns (arrays, the string
elements they hold, maps, matrices, user-type instances and drawing records)
from this bound. Every branch that lowers a construct creating such objects
records its site (``record_object_site``) with what one run of it costs: one
for each object it makes, plus one for each string it stores, or
``UNBOUNDED`` when that number is only known at run time (the copy of an
array of strings, a string array of computed size). A store is such a site
too: a string a user-type field takes, and the copy of an array a variable,
a parameter or a field already holds when another variable, a field or a
function's value takes it. The count follows the program with the id-sites
algebra: a site counts once per static call of the user function that holds
it (transitively), once per iteration of a ``for`` loop whose bounds are
known before the script runs unless its body assigns the loop's counter or
declares a variable of the same name, and in both arms of an ``if``; a
``while`` or ``for ... in`` loop, any other ``for`` loop or a recursion
around a creating site has no static bound, and neither has a creating site
a ``request.security`` expression can reach (in its own syntax, in a function
it calls, or in the value of a global that either of them reads, which the
evaluator computes again), since it runs once per requested bar. The bound
is per execution of the script body, not per bar: ``calc_on_order_fills``
runs the body again.
"""

from dataclasses import dataclass
import json

from ..ast_nodes import (
    Assignment, ExprStmt, FuncCall, Identifier, IfStmt, MemberAccess, NaLiteral,
    StringLiteral, SwitchStmt, Ternary, TupleLiteral, TypeDecl, VarDecl,
)
from ..limits import iter_ast_nodes, syntax_children
from .id_sites import UNBOUNDED, _bounded, _Executions, _Index, _plus, _static_int
from .order_shapes import _site_facts


# The requests whose expression the engine evaluates once per requested bar.
REQUEST_CALLS = frozenset(("security", "security_lower_tf"))
# Array operations, by what one call creates beyond the receiver.
ARRAY_COPIES = frozenset(("copy", "slice"))
ARRAY_NUMERIC_RESULTS = frozenset(("abs", "standardize", "sort_indices"))
ARRAY_BULK_WRITES = frozenset(("concat", "fill"))
ARRAY_ELEMENT_WRITES = frozenset(("push", "unshift", "insert", "set"))
# Matrix operations returning a new matrix or array of the receiver's elements.
MATRIX_COPIES = frozenset((
    "copy", "submatrix", "transpose", "diff", "mult", "pow", "inv", "pinv",
    "eigenvectors", "kron", "row", "col",
))
MATRIX_BULK_WRITES = frozenset(("fill", "add_row", "add_col", "concat"))
# The C++ spelling of an array (``_type_spec_to_cpp``): ``std::vector<element>``.
ARRAY_CPP_PREFIX = "std::vector<"


@dataclass(eq=False)
class ObjectSite:
    """What one run of a lowered creating site costs.

    ``weight`` is a whole number or ``UNBOUNDED``. ``per_element`` more are
    added for each element whose count is the product of the ``counts``
    expressions, known only once their values are (a literal size, a
    never-reassigned top-level name). ``defaults`` are the field defaults a
    constructor evaluates for the fields it omits.
    """

    node: object
    weight: object
    per_element: object = 0
    counts: tuple = ()
    defaults: tuple = ()


def element_weight(spec):
    """The objects one stored element of type ``spec`` owns.

    A string is one object, a nested collection holds a number only known at
    run time, and a number, bool, color or handle owns none. An element type
    the lowering could not resolve counts as a string.
    """
    if spec is None:
        return 1
    if spec.kind == "primitive":
        return 1 if spec.name == "string" else 0
    if spec.kind == "udt":
        return 0
    return UNBOUNDED


def copy_weight(spec):
    """A new collection holding a run-time number of elements of type ``spec``."""
    return 1 if element_weight(spec) == 0 else UNBOUNDED


def is_empty_string(node) -> bool:
    return isinstance(node, StringLiteral) and node.value == ""


def owned_elements(spec, nodes):
    """The objects the elements ``nodes`` own once stored as elements of type ``spec``."""
    per = element_weight(spec)
    total = 0
    for node in nodes:
        if not is_empty_string(node):
            total = _plus(total, per)
    return total


def array_method_weight(method, element):
    """Objects one call of array method ``method`` creates on elements of type ``element``."""
    per = element_weight(element)
    if method in ARRAY_COPIES:
        return copy_weight(element)
    if method in ARRAY_NUMERIC_RESULTS:
        return 1
    if method in ARRAY_BULK_WRITES:
        return 0 if per == 0 else UNBOUNDED
    if method in ARRAY_ELEMENT_WRITES:
        return per
    return 0


def matrix_method_weight(method, element, has_arguments):
    """Objects one call of matrix method ``method`` creates on elements of type ``element``."""
    if method in MATRIX_COPIES:
        return copy_weight(element)
    if method == "sum":
        # Two matrices (or a matrix and a number) sum into a new matrix; the
        # receiver alone is an aggregate.
        return 1 if has_arguments else 0
    if method == "eigenvalues":
        return 1
    if method == "set":
        return element_weight(element)
    if method in MATRIX_BULK_WRITES:
        return 0 if element_weight(element) == 0 else UNBOUNDED
    return 0


def map_method_weight(method, spec):
    """Objects one call of map method ``method`` creates on a map of type ``spec``."""
    key = spec.key if spec is not None and spec.kind == "map" else None
    value = spec.value if spec is not None and spec.kind == "map" else None
    if method in ("copy", "put_all"):
        return 1 if element_weight(key) == 0 and element_weight(value) == 0 else UNBOUNDED
    if method == "keys":
        return copy_weight(key)
    if method == "values":
        return copy_weight(value)
    if method == "put":
        return _plus(1, _plus(element_weight(key), element_weight(value)))
    return 0


def collection_method_weight(spec, method, has_arguments):
    """Objects one call of ``method`` creates on a collection of type ``spec``,
    whichever lowering spells the call; nothing on any other receiver."""
    kind = spec.kind if spec is not None else None
    if kind == "array":
        return array_method_weight(method, spec.element)
    if kind == "matrix":
        return matrix_method_weight(method, spec.element, has_arguments)
    if kind == "map":
        return map_method_weight(method, spec)
    return 0


def udt_field_specs(emitter, type_name):
    """``(field, spec)`` for every field of the user type ``type_name``."""
    specs = emitter._udt_field_type_specs.get(type_name, {})
    return [(field, specs.get(field.name) or emitter._type_spec_from_hint_name(field.type_name))
            for field in emitter._udt_defs.get(type_name, ())]


def udt_field_spec(emitter, type_name, name):
    """The type of the field ``name`` of the user type ``type_name``; None when unknown."""
    for field, spec in udt_field_specs(emitter, type_name):
        if field.name == name:
            return spec
    return None


def field_store_weight(emitter, spec, value, compound=False):
    """Objects a user-type field of type ``spec`` takes when ``value`` is stored
    into it, by ``Type.new`` or by an assignment.

    A non-empty string is one object, and so is the string a compound
    assignment builds. An array that is not a new one is copied in, and an
    omitted or ``na`` one is a new empty array. A number, bool, color or
    handle owns none. A field whose type is not resolved counts as a string.
    """
    if spec is None or spec.kind == "primitive" and spec.name == "string":
        return 1 if compound or value is not None and not is_empty_string(value) else 0
    if spec.kind == "array" and not compound:
        if value is None or isinstance(value, NaLiteral):
            return 1
        return 0 if emitter._array_arg_kind(value) == "fresh" else copy_weight(spec.element)
    return 0


def element_store_weight(element, value, compound=False):
    """``xs[k] := v``: what an array element of type ``element`` takes, as
    ``array.set`` stores it. A non-empty string is one object, and so is the
    string a compound assignment builds; a number, bool, color or handle owns
    none. An element whose type is not resolved counts as a string."""
    per = element_weight(element)
    if per == 1 and not compound and is_empty_string(value):
        return 0
    return per


def tuple_array_copy_weight(cpp_types, stored=False):
    """The arrays among a tuple's elements, of C++ types ``cpp_types``, each
    copied once: an array of strings copies every string it holds, which has
    no static bound; any other array is one new array, or nothing when a
    variable takes it (``stored``, as ``held_array_store_weight`` prices a
    store); a value that is not an array owns none."""
    weight = 0
    for cpp_type in cpp_types:
        if not (cpp_type.startswith(ARRAY_CPP_PREFIX) and cpp_type.endswith(">")):
            continue
        if cpp_type[len(ARRAY_CPP_PREFIX):-1] == "std::string":
            return UNBOUNDED
        if not stored:
            weight = _plus(weight, 1)
    return weight


def udt_copy_weight(emitter, type_name):
    """``Type.copy``: a record and a copy of each string it holds. The copy of
    a type with an array field is refused before it is priced."""
    weight = 1
    for _field, spec in udt_field_specs(emitter, type_name):
        if spec is None:
            weight = _plus(weight, 1)
        elif spec.kind == "primitive" and spec.name == "string":
            weight = _plus(weight, 1)
    return weight


def _last_value(body):
    """What an if or a switch arm leaves: its last expression, else its last statement."""
    if not body:
        return None
    last = body[-1]
    return last.expr if isinstance(last, ExprStmt) else last


def stored_values(value):
    """The values ``value`` can hand over to the variable, field or caller that
    takes it: either value of a ternary, the last value of each arm of an if
    or a switch, each element of a tuple, else ``value`` itself."""
    pending = [value]
    while pending:
        node = pending.pop()
        if isinstance(node, Ternary):
            pending.extend((node.true_val, node.false_val))
        elif isinstance(node, TupleLiteral):
            pending.extend(node.elements)
        elif isinstance(node, IfStmt):
            pending.extend((_last_value(node.body), _last_value(node.else_body)))
        elif isinstance(node, SwitchStmt):
            pending.extend(_last_value(body) for _case, body in node.cases)
            pending.append(_last_value(node.default_body))
        elif node is not None:
            yield node


def _held_arrays(emitter, value):
    """``(node, spec)`` for each variable, parameter or field holding an array
    that ``value`` can hand over as it is."""
    for node in stored_values(value):
        if isinstance(node, (Identifier, MemberAccess)):
            spec = emitter._type_spec_from_expr(node)
            if spec is not None and spec.kind == "array":
                yield node, spec


def held_array_store_weight(emitter, value):
    """``a := b`` or ``c = b``: an array a variable or a field already holds,
    stored into another variable, is copied there with every element it
    holds. Elements that own objects (strings, nested collections) have no
    static bound; numbers, bools, colors and handles add nothing."""
    for _node, spec in _held_arrays(emitter, value):
        if element_weight(spec.element):
            return UNBOUNDED
    return 0


def _moved_local(emitter, definition, name) -> bool:
    """Whether ``name`` is a local the function body declares once from a new
    array, not ``var`` and never reassigned: C++ moves it out of the call."""
    params = {param if isinstance(param, str) else param[0]
              for param in getattr(definition, "params", None) or ()}
    body = list(getattr(definition, "body", None) or ())
    declarations = [stmt for stmt in body if isinstance(stmt, VarDecl) and stmt.name == name]
    if name in params or len(declarations) != 1:
        return False
    declaration = declarations[0]
    if declaration.is_var or declaration.is_varip:
        return False
    for stmt in body:
        for node, _depth in iter_ast_nodes(stmt):
            if (isinstance(node, Assignment) and isinstance(node.target, Identifier)
                    and node.target.name == name):
                return False
    return emitter._array_arg_kind(declaration.value) == "fresh"


def record_returned_arrays(emitter, definition, value) -> None:
    """A function returns ``value`` by copy: each array a variable, a parameter
    or a field holds that ``value`` can be is copied once per call, with
    every element it holds. A local the body declares from a new array, and
    returns as it is, is moved out instead."""
    if isinstance(value, Identifier) and _moved_local(emitter, definition, value.name):
        return
    for node, spec in _held_arrays(emitter, value):
        record_object_site(emitter, node, copy_weight(spec.element))


def request_payload_weight(emitter, expression):
    """``request.security_lower_tf``: one array per requested value; strings make it unbounded."""
    values = getattr(expression, "elements", None) or [expression]
    weight = 0
    for value in values:
        cpp_type = emitter._infer_type(value) if value is not None else "double"
        weight = _plus(weight, UNBOUNDED if cpp_type == "std::string" else 1)
    return weight


def record_object_site(emitter, node, weight, per_element=0, counts=(), defaults=()) -> None:
    """Called only by a branch that lowers a construct creating script-owned objects."""
    if not weight and not per_element and not defaults:
        return
    if (getattr(emitter, "_object_sites_in_request", 0)
            or getattr(emitter, "_security_payload_depth", 0)):
        # Lowered for a request's evaluator, which runs it once per requested
        # bar: no static bound holds.
        weight = UNBOUNDED
    sites = getattr(emitter, "_object_sites", None)
    if sites is None:
        sites = emitter._object_sites = {}
    # A site lowered without its node has no position to count from: the
    # bound it leaves is ``UNBOUNDED`` (``_Executions.total``).
    key = id(node) if node is not None else None
    site = ObjectSite(node, weight, per_element, tuple(counts), tuple(defaults))
    previous = sites.get(key)
    if previous is not None:
        # The same node lowered again (another instance of its function, typed
        # from another call): keep the larger cost of the two.
        site = ObjectSite(node, max(previous.weight, weight),
                          max(previous.per_element, per_element),
                          counts or previous.counts, defaults or previous.defaults)
    sites[key] = site


def _is_request(node) -> bool:
    callee = node.callee if isinstance(node, FuncCall) else None
    return (isinstance(callee, MemberAccess) and callee.member in REQUEST_CALLS
            and isinstance(callee.object, Identifier) and callee.object.name == "request")


def _element_count(counts, bindings):
    """The product of ``counts`` when each is known before the script runs, else ``UNBOUNDED``."""
    values = [_static_int(count, bindings) for count in counts]
    if any(value is not None and value <= 0 for value in values):
        return 0
    if any(value is None for value in values):
        return UNBOUNDED
    total = 1
    for value in values:
        total = _bounded(total * value)
    return total


def _site_weight(site, bindings):
    if not site.per_element:
        return site.weight
    elements = _element_count(site.counts, bindings)
    if elements == 0:
        return site.weight
    if UNBOUNDED in (elements, site.per_element):
        return UNBOUNDED
    return _plus(site.weight, _bounded(elements * site.per_element))


class _ObjectExecutions(_Executions):
    """The id-sites execution count with what objects add to it.

    A type declaration runs nothing: a field default is priced at each
    constructor that omits the field. A request's evaluator runs once per
    requested bar, which no static bound holds: a request that can reach a
    creating site is unbounded. What a loop's own header creates (the keys a
    map loop walks) runs once per execution of the loop.
    """

    def __init__(self, ast, index, counted, facts, defaults, reads):
        self.defaults = defaults
        self.reads = reads
        self.creating = None
        super().__init__(ast, index, counted, facts)

    def _cost(self, node):
        if isinstance(node, TypeDecl):
            return 0
        if _is_request(node):
            return _plus(self.counted.get(id(node), 0),
                         UNBOUNDED if self._request_creates(node) else 0)
        total = super()._cost(node)
        for default in self.defaults.get(id(node), ()):
            total = _plus(total, self._cost(default))
        return total

    def _open_loop_cost(self, loop):
        return _plus(self.counted.get(id(loop), 0), super()._open_loop_cost(loop))

    def _request_creates(self, request):
        """Whether the request's evaluator can reach a creating site: in the
        request's own syntax, in a function it calls, or in what the
        evaluator computes again for a global any of these reads (the
        global's value, or a ``var`` or reassigned global's statements). A
        name is followed whatever binds it, which errs high."""
        names, definitions = self._creating_units()
        for child in syntax_children(request):
            for node, _depth in iter_ast_nodes(child):
                if self.counted.get(id(node)):
                    return True
                if isinstance(node, Identifier) and node.name in names:
                    return True
                if isinstance(node, FuncCall) and any(
                        id(target) in definitions for target in self._targets(node)):
                    return True
        return False

    def _creating_units(self):
        """The global names and the definitions that reach a creating site,
        through the functions they call and the globals they read, as
        ``(names, definition ids)``. Computed once: each root a global or a
        definition evaluates (a value, a statement, a body statement) is
        walked once, and ``evaluated_by`` maps what it reaches back to it."""
        if self.creating is not None:
            return self.creating
        roots = {}
        evaluated_by = {}
        for name, values in self.reads.items():
            for root in values:
                roots[id(root)] = root
                evaluated_by.setdefault(("root", id(root)), set()).add(("name", name))
        for definition in self.index.definitions:
            for root in self._roots(definition):
                roots[id(root)] = root
                evaluated_by.setdefault(("root", id(root)), set()).add(("def", id(definition)))
        creating = set()
        for key, root in roots.items():
            unit = ("root", key)
            for node, _depth in iter_ast_nodes(root):
                if self.counted.get(id(node)):
                    creating.add(unit)
                if isinstance(node, Identifier) and node.name in self.reads:
                    evaluated_by.setdefault(("name", node.name), set()).add(unit)
                elif isinstance(node, FuncCall):
                    for target in self._targets(node):
                        evaluated_by.setdefault(("def", id(target)), set()).add(unit)
        pending = list(creating)
        while pending:
            for unit in evaluated_by.get(pending.pop(), ()):
                if unit not in creating:
                    creating.add(unit)
                    pending.append(unit)
        self.creating = ({key for kind, key in creating if kind == "name"},
                         {key for kind, key in creating if kind == "def"})
        return self.creating


def _request_reads(emitter):
    """What a request's evaluator computes again for a global it reads, by name:
    a global's value, a ``var`` or reassigned global's statements."""
    reads = {}
    for name, value in (getattr(emitter.ctx, "global_expr_map", None) or {}).items():
        if value is not None:
            reads.setdefault(name, []).append(value)
    for name, info in (getattr(emitter.ctx, "global_mutable_infos", None) or {}).items():
        reads.setdefault(name, []).extend(
            stmt for stmt in getattr(info, "source_stmts", None) or () if stmt is not None)
    return reads


def objects_per_execution(emitter):
    """Objects one execution of the script body can create; ``UNBOUNDED`` when no static bound holds."""
    sites = dict(getattr(emitter, "_object_sites", None) or {})
    # A variable whose history the script reads leaves a copy of itself each
    # time its declaration runs (collection_history.py): a site at the
    # declaration, of the variable's elements.
    history = [(variable.decl_node_id, copy_weight(getattr(variable.spec, "element", None)))
               for variable in emitter._collection_history_variables()]
    keys = set(sites) | {key for key, _weight in history}
    if not keys:
        return 0
    ast = emitter.ctx.ast
    # Every request is walked, a function's too: what its evaluator reaches counts.
    requests = {id(node) for node, _depth in iter_ast_nodes(ast) if _is_request(node)}
    index = _Index(ast, keys | requests)
    facts = _site_facts(ast, emitter.ctx.global_mutable_infos, keys | index.loops)
    weights = {}
    for key, site in sites.items():
        weights[key] = _site_weight(site, facts.get(key, (0, "", {}))[2])
    for key, weight in history:
        weights[key] = _plus(weights.get(key, 0), weight)
    defaults = {key: site.defaults for key, site in sites.items() if site.defaults}
    counted = {key: weight for key, weight in weights.items() if weight or key in defaults}
    if not counted:
        return 0
    for key in requests:
        counted.setdefault(key, 0)
    return _ObjectExecutions(ast, index, counted, facts, defaults, _request_reads(emitter)).total()


def objects_document(emitter) -> str:
    count = objects_per_execution(emitter)
    objects = {"perExecutionStatus": "unbounded"} if count == UNBOUNDED else {"perExecution": int(count)}
    return json.dumps({"strategy": {"objects": objects}, "version": 1},
                      sort_keys=True, separators=(",", ":"), allow_nan=False)
