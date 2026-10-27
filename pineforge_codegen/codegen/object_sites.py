"""Immutable bound on the script-owned objects one execution of the script body creates.

A host reserves scratch for the objects a script owns (arrays, the string
elements they hold, maps, matrices, user-type instances and drawing records)
from this bound. Every branch that lowers a construct creating such objects
records its site (``record_object_site``) with what one run of it costs: one
for each object it makes, plus one for each string it stores, or
``UNBOUNDED`` when that number is only known at run time (the copy of an
array of strings, a string array of computed size). The count follows the
program with the id-sites algebra: a site counts once per static call of the
user function that holds it (transitively), once per iteration of a ``for``
loop whose bounds are known before the script runs, and in both arms of an
``if``; a ``while`` or ``for ... in`` loop, a ``for`` loop with a computed
bound or a recursion around a creating site has no static bound, and neither
has a creating site a ``request.security`` expression evaluates, which runs
once per requested bar. The bound is per execution of the script body, not
per bar: ``calc_on_order_fills`` runs the body again.
"""

from dataclasses import dataclass
import json

from ..ast_nodes import FuncCall, Identifier, MemberAccess, StringLiteral, TypeDecl
from ..limits import syntax_children
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
    kind: str
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


def udt_field_specs(emitter, type_name):
    """``(field, spec)`` for every field of the user type ``type_name``."""
    specs = emitter._udt_field_type_specs.get(type_name, {})
    return [(field, specs.get(field.name) or emitter._type_spec_from_hint_name(field.type_name))
            for field in emitter._udt_defs.get(type_name, ())]


def udt_copy_weight(emitter, type_name):
    """``Type.copy``: a record, a copy of each string, and of each array the record holds by value."""
    weight = 1
    for _field, spec in udt_field_specs(emitter, type_name):
        if spec is None:
            weight = _plus(weight, 1)
        elif spec.kind == "primitive" and spec.name == "string":
            weight = _plus(weight, 1)
        elif spec.kind == "array" and emitter._udt_array_field_cpp(spec, type_name) is None:
            weight = _plus(weight, copy_weight(spec.element))
    return weight


def request_payload_weight(emitter, expression):
    """``request.security_lower_tf``: one array per requested value; strings make it unbounded."""
    values = getattr(expression, "elements", None) or [expression]
    weight = 0
    for value in values:
        cpp_type = emitter._infer_type(value) if value is not None else "double"
        weight = _plus(weight, UNBOUNDED if cpp_type == "std::string" else 1)
    return weight


def record_object_site(emitter, node, kind, weight, per_element=0, counts=(), defaults=()) -> None:
    """Called only by a branch that lowers a construct creating script-owned objects."""
    if not weight and not per_element and not defaults:
        return
    sites = getattr(emitter, "_object_sites", None)
    if sites is None:
        sites = emitter._object_sites = {}
    # A site lowered without its node has no position to count from: the
    # bound it leaves is ``UNBOUNDED`` (``_Executions.total``).
    key = id(node) if node is not None else None
    site = ObjectSite(node, kind, weight, per_element, tuple(counts), tuple(defaults))
    previous = sites.get(key)
    if previous is not None:
        # The same node lowered again (another instance of its function, typed
        # from another call): keep the larger cost of the two.
        site = ObjectSite(node, kind, max(previous.weight, weight),
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
    constructor that omits the field. A creating site under a request runs
    once per requested bar, which no static bound holds.
    """

    def __init__(self, ast, index, counted, facts, defaults):
        self.defaults = defaults
        super().__init__(ast, index, counted, facts)

    def _cost(self, node):
        if isinstance(node, TypeDecl):
            return 0
        if _is_request(node):
            payload = 0
            for child in syntax_children(node):
                payload = _plus(payload, self._cost(child))
            return _plus(self.counted.get(id(node), 0), UNBOUNDED if payload else 0)
        total = super()._cost(node)
        for default in self.defaults.get(id(node), ()):
            total = _plus(total, self._cost(default))
        return total


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
    index = _Index(ast, keys)
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
    return _ObjectExecutions(ast, index, counted, facts, defaults).total()


def objects_document(emitter) -> str:
    count = objects_per_execution(emitter)
    objects = {"perExecutionStatus": "unbounded"} if count == UNBOUNDED else {"perExecution": int(count)}
    return json.dumps({"strategy": {"objects": objects}, "version": 1},
                      sort_keys=True, separators=(",", ":"), allow_nan=False)
