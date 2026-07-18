"""Drawing-objects-as-data dispatch for the codegen.

Spec: ``docs/drawing-objects-as-data.md`` §4.3 (the 4-form call dispatch),
§L (handle lifecycle) and §U (UDT × drawing). Everything here only ever
runs for a strategy whose ``self._uses_drawing`` is True (the dispatch is
reached solely when a ``line``/``box``/``label``/``linefill``/``chart.point``
call or a drawing-typed receiver is present), so non-drawing strategies emit
byte-identical C++.

The geometry of a drawing object becomes REAL C++ state (per-type arena in
``pineforge/drawing.hpp``); every VISUAL kwarg/method (color / style / width /
bgcolor / text_* / extend-as-visual / force_overlay) is accepted-and-DROPPED.

``DrawingVisitor`` is mixed into ``CodeGen``; it owns no state of its own and
reads only attributes established by ``CodeGen.__init__`` (``_uses_drawing``,
``_udt_var_types``, ``_udt_param_udt``) and sibling mixins
(``_visit_expr``, ``_type_spec_from_expr``).
"""

from __future__ import annotations

from ..ast_nodes import FuncCall, Identifier, MemberAccess
from .tables import DRAWING_TYPE_TO_CPP, DRAWING_ARENA, ARRAY_VOID_METHODS as _ARRAY_VOID_METHODS

_MAP_VOID_METHODS = frozenset({"clear", "put_all"})

# ---------------------------------------------------------------------------
# Canonical Pine v6 constructor param-name lists (positional order).
# Only the GEOMETRY names are consumed downstream; every other (visual) name is
# merged-and-dropped. Two overloads each: scalar coords vs chart.point.
# ---------------------------------------------------------------------------
_LINE_CTOR = ["x1", "y1", "x2", "y2", "xloc", "extend", "color", "style",
              "width", "force_overlay"]
_LINE_CTOR_PTS = ["first_point", "second_point", "xloc", "color", "style",
                  "width", "extend", "force_overlay"]
_BOX_CTOR = ["left", "top", "right", "bottom", "border_color", "border_width",
             "border_style", "extend", "xloc", "bgcolor", "text", "text_size",
             "text_color", "text_halign", "text_valign", "text_wrap",
             "text_font_family", "force_overlay"]
_BOX_CTOR_PTS = ["top_left", "bottom_right", "border_color", "border_width",
                 "border_style", "extend", "xloc", "bgcolor", "text",
                 "text_size", "text_color", "text_halign", "text_valign",
                 "text_wrap", "text_font_family", "force_overlay"]
_LABEL_CTOR = ["x", "y", "text", "xloc", "yloc", "color", "style", "textcolor",
               "size", "textalign", "tooltip", "text_font_family",
               "force_overlay"]
_LABEL_CTOR_PTS = ["point", "text", "xloc", "yloc", "color", "style",
                   "textcolor", "size", "textalign", "tooltip",
                   "text_font_family", "force_overlay"]


# ---------------------------------------------------------------------------
# Method classification per type. GEOMETRY methods emit real arena calls;
# *_NOOP visual setters emit pf_noop (args evaluated + discarded).
# ---------------------------------------------------------------------------
LINE_METHODS = frozenset({
    "get_x1", "get_x2", "get_y1", "get_y2", "get_price",
    "set_x1", "set_x2", "set_y1", "set_y2", "set_xy1", "set_xy2",
    "set_first_point", "set_second_point", "set_xloc", "copy", "delete",
})
LINE_NOOP = frozenset({"set_color", "set_style", "set_width", "set_extend"})

BOX_METHODS = frozenset({
    "get_left", "get_right", "get_top", "get_bottom",
    "set_left", "set_right", "set_top", "set_bottom",
    "set_lefttop", "set_rightbottom",
    "set_top_left_point", "set_bottom_right_point", "set_xloc",
    "copy", "delete",
})
BOX_NOOP = frozenset({
    "set_border_color", "set_border_width", "set_border_style", "set_bgcolor",
    "set_extend", "set_text", "set_text_color", "set_text_size",
    "set_text_halign", "set_text_valign", "set_text_font_family",
    "set_text_wrap", "set_text_formatting",
})

LABEL_METHODS = frozenset({
    "get_x", "get_y", "get_text",
    "set_x", "set_y", "set_xy", "set_point", "set_xloc", "set_yloc",
    "set_text", "copy", "delete",
})
LABEL_NOOP = frozenset({
    "set_color", "set_style", "set_textcolor", "set_size", "set_textalign",
    "set_tooltip", "set_text_font_family", "set_text_formatting",
})

LINEFILL_METHODS = frozenset({"get_line1", "get_line2", "delete"})
LINEFILL_NOOP = frozenset({"set_color"})

# Scalar getter -> C++ return type. (linefill.get_line1/2 return a Line handle,
# typed via _type_spec_from_expr -> udt, not here.) x-coords are int64_t,
# y-coords/prices double, label text std::string.
_DRAWING_GETTER_RET = {
    ("line", "get_x1"): "int64_t", ("line", "get_x2"): "int64_t",
    ("line", "get_y1"): "double", ("line", "get_y2"): "double",
    ("line", "get_price"): "double",
    ("box", "get_left"): "int64_t", ("box", "get_right"): "int64_t",
    ("box", "get_top"): "double", ("box", "get_bottom"): "double",
    ("label", "get_x"): "int64_t", ("label", "get_y"): "double",
    ("label", "get_text"): "std::string",
}

# Per-type lookup (geometry, noop) used by the dispatcher and support_checker.
DRAWING_METHODS_BY_TYPE = {
    "line": (LINE_METHODS, LINE_NOOP),
    "box": (BOX_METHODS, BOX_NOOP),
    "label": (LABEL_METHODS, LABEL_NOOP),
    "linefill": (LINEFILL_METHODS, LINEFILL_NOOP),
}

# All method names that the §4.3 receiver dispatch recognises. ``new`` is
# DELIBERATELY excluded (it is namespace-functional only and would otherwise
# shadow ``Type.new(...)`` UDT constructors). Gating the receiver branch on
# membership here enforces the L.1 precedence rule: a user method (egoigor's
# ``slope``) is not in this set and therefore routes to user-method dispatch.
ALL_DRAWING_METHODS = (
    LINE_METHODS | LINE_NOOP | BOX_METHODS | BOX_NOOP
    | LABEL_METHODS | LABEL_NOOP | LINEFILL_METHODS | LINEFILL_NOOP
)


class DrawingVisitor:
    """Drawing-objects-as-data emit helpers. Mixed into ``CodeGen``."""

    # ------------------------------------------------------------------
    # Enum / extend lowering helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _lower_xloc(node) -> str:
        if (isinstance(node, MemberAccess) and isinstance(node.object, Identifier)
                and node.object.name == "xloc" and node.member == "bar_time"):
            return "XLoc::bar_time"
        return "XLoc::bar_index"

    @staticmethod
    def _lower_yloc(node) -> str:
        if isinstance(node, MemberAccess) and isinstance(node.object, Identifier) and node.object.name == "yloc":
            if node.member == "abovebar":
                return "YLoc::abovebar"
            if node.member == "belowbar":
                return "YLoc::belowbar"
        return "YLoc::price"

    @staticmethod
    def _lower_extend(node) -> tuple[str, str]:
        """Map ``extend.{none,left,right,both}`` -> (ext_left, ext_right)."""
        if isinstance(node, MemberAccess) and isinstance(node.object, Identifier) and node.object.name == "extend":
            return {
                "both": ("true", "true"),
                "left": ("true", "false"),
                "right": ("false", "true"),
                "none": ("false", "false"),
            }.get(node.member, ("false", "false"))
        return ("false", "false")

    @staticmethod
    def _is_chart_point_callee(callee) -> bool:
        """True for ``chart.point.<member>(...)`` callees."""
        return (
            isinstance(callee, MemberAccess)
            and isinstance(callee.object, MemberAccess)
            and isinstance(callee.object.object, Identifier)
            and callee.object.object.name == "chart"
            and callee.object.member == "point"
        )

    # ------------------------------------------------------------------
    # Constructor (CREATE) lowering
    # ------------------------------------------------------------------
    @staticmethod
    def _merge_drawing_args(node: FuncCall, param_names: list[str]) -> dict:
        """Map positional args + kwargs onto ``param_names`` -> {name: node}."""
        vals: dict = {}
        for i, a in enumerate(node.args):
            if i < len(param_names):
                vals[param_names[i]] = a
        for k, v in node.kwargs.items():
            vals[k] = v
        return vals

    def _drawing_ctor_is_points(self, node: FuncCall) -> bool:
        """Pick the chart.point overload when the first geometry arg is a
        ChartPoint (inferred TypeSpec udt('chart.point')) or a point kwarg is
        supplied."""
        for k in ("first_point", "top_left", "point"):
            if k in node.kwargs:
                return True
        if node.args:
            spec = self._type_spec_from_expr(node.args[0])
            if spec is not None and spec.kind == "udt" and spec.name == "chart.point":
                return True
        return False

    def _emit_drawing_ctor(self, dtype: str, node: FuncCall) -> str:
        arena = DRAWING_ARENA[dtype]
        if dtype == "linefill":
            vals = self._merge_drawing_args(node, ["line1", "line2", "color"])
            l1 = self._visit_expr(vals["line1"]) if vals.get("line1") is not None else "Line{}"
            l2 = self._visit_expr(vals["line2"]) if vals.get("line2") is not None else "Line{}"
            return f"pf_linefill_new({arena}, {l1}, {l2})"

        use_pts = self._drawing_ctor_is_points(node)

        if dtype == "line":
            vals = self._merge_drawing_args(node, _LINE_CTOR_PTS if use_pts else _LINE_CTOR)
            xloc = self._lower_xloc(vals.get("xloc"))
            if use_pts:
                p1 = self._visit_expr(vals.get("first_point"))
                p2 = self._visit_expr(vals.get("second_point"))
                return f"pf_line_new_pts({arena}, {p1}, {p2}, {xloc})"
            el, er = self._lower_extend(vals.get("extend"))
            x1 = self._visit_expr(vals.get("x1"))
            y1 = self._visit_expr(vals.get("y1"))
            x2 = self._visit_expr(vals.get("x2"))
            y2 = self._visit_expr(vals.get("y2"))
            return (f"pf_line_new({arena}, (int64_t)({x1}), (double)({y1}), "
                    f"(int64_t)({x2}), (double)({y2}), {xloc}, {el}, {er})")

        if dtype == "box":
            vals = self._merge_drawing_args(node, _BOX_CTOR_PTS if use_pts else _BOX_CTOR)
            xloc = self._lower_xloc(vals.get("xloc"))
            if use_pts:
                tl = self._visit_expr(vals.get("top_left"))
                br = self._visit_expr(vals.get("bottom_right"))
                return f"pf_box_new_pts({arena}, {tl}, {br}, {xloc})"
            left = self._visit_expr(vals.get("left"))
            top = self._visit_expr(vals.get("top"))
            right = self._visit_expr(vals.get("right"))
            bottom = self._visit_expr(vals.get("bottom"))
            return (f"pf_box_new({arena}, (int64_t)({left}), (double)({top}), "
                    f"(int64_t)({right}), (double)({bottom}), {xloc})")

        if dtype == "label":
            vals = self._merge_drawing_args(node, _LABEL_CTOR_PTS if use_pts else _LABEL_CTOR)
            yloc = self._lower_yloc(vals.get("yloc"))
            text = (self._visit_expr(vals["text"]) if vals.get("text") is not None
                    else 'std::string("")')
            if use_pts:
                pt = self._visit_expr(vals.get("point"))
                return f"pf_label_new_pt({arena}, {pt}, {text}, {yloc})"
            x = self._visit_expr(vals.get("x"))
            y = self._visit_expr(vals.get("y"))
            xloc = self._lower_xloc(vals.get("xloc"))
            return (f"pf_label_new({arena}, (int64_t)({x}), (double)({y}), "
                    f"{text}, {xloc}, {yloc})")

        return "0"  # unreachable

    # ------------------------------------------------------------------
    # chart.point — inline aggregate literals (no arena)
    # ------------------------------------------------------------------
    def _emit_chart_point(self, func_name: str, node: FuncCall) -> str:
        if func_name == "copy":
            inner = self._visit_expr(node.args[0]) if node.args else "ChartPoint{}"
            return f"ChartPoint({inner})"
        if func_name == "now":
            vals = self._merge_drawing_args(node, ["price"])
            price = (self._visit_expr(vals["price"]) if vals.get("price") is not None
                     else "current_bar_.close")
            return (f"ChartPoint{{ .index=(int64_t)(pine_bar_index()), "
                    f".time=(int64_t)current_bar_.timestamp, .price=({price}) }}")
        if func_name == "from_index":
            vals = self._merge_drawing_args(node, ["index", "price"])
            idx = self._visit_expr(vals.get("index"))
            price = self._visit_expr(vals.get("price"))
            return (f"ChartPoint{{ .index=(int64_t)({idx}), .time=na<int64_t>(), "
                    f".price=({price}) }}")
        if func_name == "from_time":
            vals = self._merge_drawing_args(node, ["time", "price"])
            tm = self._visit_expr(vals.get("time"))
            price = self._visit_expr(vals.get("price"))
            return (f"ChartPoint{{ .index=na<int64_t>(), .time=(int64_t)({tm}), "
                    f".price=({price}) }}")
        if func_name == "new":
            vals = self._merge_drawing_args(node, ["time", "index", "price"])
            tm = self._visit_expr(vals.get("time"))
            idx = self._visit_expr(vals.get("index"))
            price = self._visit_expr(vals.get("price"))
            return (f"ChartPoint{{ .index=(int64_t)({idx}), .time=(int64_t)({tm}), "
                    f".price=({price}) }}")
        return "ChartPoint{}"

    # ------------------------------------------------------------------
    # Namespace-functional + method-form dispatch
    # ------------------------------------------------------------------
    def _emit_drawing_namespace_call(self, namespace: str, func_name: str, node: FuncCall) -> str:
        """``line.new(...)`` / ``line.get_y2(ln)`` / ``linefill.new(l1,l2,c)`` …"""
        if func_name == "new":
            return self._emit_drawing_ctor(namespace, node)
        if not node.args:
            return "0"
        return self._emit_drawing_method(namespace, func_name, node.args[0], list(node.args[1:]), node)

    def _emit_drawing_method(self, dtype: str, method: str, recv_node, arg_nodes: list, node: FuncCall) -> str:
        """Lower a drawing method onto its arena (or pf_noop for visual setters).

        ``recv_node`` is the handle receiver; ``arg_nodes`` the remaining args.
        """
        arena = DRAWING_ARENA[dtype]
        recv = self._visit_expr(recv_node)
        geometry, noop = DRAWING_METHODS_BY_TYPE[dtype]
        if method in noop:
            extra = "".join(", " + self._visit_expr(a) for a in arg_nodes)
            return f"pf_noop({recv}{extra})"
        av = [self._visit_expr(a) for a in arg_nodes]
        if dtype == "line":
            return self._emit_line_method(method, arena, recv, av, arg_nodes)
        if dtype == "box":
            return self._emit_box_method(method, arena, recv, av, arg_nodes)
        if dtype == "label":
            return self._emit_label_method(method, arena, recv, av, arg_nodes)
        if dtype == "linefill":
            return self._emit_linefill_method(method, arena, recv, av)
        return "0"

    def _emit_line_method(self, m, a, r, av, raw) -> str:
        if m == "get_x1":
            return f"pf_line_get_x1({a}, {r})"
        if m == "get_x2":
            return f"pf_line_get_x2({a}, {r})"
        if m == "get_y1":
            return f"pf_line_get_y1({a}, {r})"
        if m == "get_y2":
            return f"pf_line_get_y2({a}, {r})"
        if m == "get_price":
            return f"pf_line_get_price({a}, {r}, (int64_t)({av[0]}))"
        if m == "set_x1":
            return f"pf_line_set_x1({a}, {r}, (int64_t)({av[0]}))"
        if m == "set_x2":
            return f"pf_line_set_x2({a}, {r}, (int64_t)({av[0]}))"
        if m == "set_y1":
            return f"pf_line_set_y1({a}, {r}, (double)({av[0]}))"
        if m == "set_y2":
            return f"pf_line_set_y2({a}, {r}, (double)({av[0]}))"
        if m == "set_xy1":
            return f"pf_line_set_xy1({a}, {r}, (int64_t)({av[0]}), (double)({av[1]}))"
        if m == "set_xy2":
            return f"pf_line_set_xy2({a}, {r}, (int64_t)({av[0]}), (double)({av[1]}))"
        if m == "set_first_point":
            return f"pf_line_set_first_point({a}, {r}, {av[0]})"
        if m == "set_second_point":
            return f"pf_line_set_second_point({a}, {r}, {av[0]})"
        if m == "set_xloc":
            return f"pf_line_set_xloc({a}, {r}, (int64_t)({av[0]}), (int64_t)({av[1]}), {self._lower_xloc(raw[2])})"
        if m == "copy":
            return f"pf_line_copy({a}, {r})"
        if m == "delete":
            return f"pf_line_delete({a}, {r})"
        return "0"

    def _emit_box_method(self, m, a, r, av, raw) -> str:
        if m == "get_left":
            return f"pf_box_get_left({a}, {r})"
        if m == "get_right":
            return f"pf_box_get_right({a}, {r})"
        if m == "get_top":
            return f"pf_box_get_top({a}, {r})"
        if m == "get_bottom":
            return f"pf_box_get_bottom({a}, {r})"
        if m == "set_left":
            return f"pf_box_set_left({a}, {r}, (int64_t)({av[0]}))"
        if m == "set_right":
            return f"pf_box_set_right({a}, {r}, (int64_t)({av[0]}))"
        if m == "set_top":
            return f"pf_box_set_top({a}, {r}, (double)({av[0]}))"
        if m == "set_bottom":
            return f"pf_box_set_bottom({a}, {r}, (double)({av[0]}))"
        if m == "set_lefttop":
            return f"pf_box_set_lefttop({a}, {r}, (int64_t)({av[0]}), (double)({av[1]}))"
        if m == "set_rightbottom":
            return f"pf_box_set_rightbottom({a}, {r}, (int64_t)({av[0]}), (double)({av[1]}))"
        if m == "set_top_left_point":
            return f"pf_box_set_top_left_point({a}, {r}, {av[0]})"
        if m == "set_bottom_right_point":
            return f"pf_box_set_bottom_right_point({a}, {r}, {av[0]})"
        if m == "set_xloc":
            return f"pf_box_set_xloc({a}, {r}, (int64_t)({av[0]}), (int64_t)({av[1]}), {self._lower_xloc(raw[2])})"
        if m == "copy":
            return f"pf_box_copy({a}, {r})"
        if m == "delete":
            return f"pf_box_delete({a}, {r})"
        return "0"

    def _emit_label_method(self, m, a, r, av, raw) -> str:
        if m == "get_x":
            return f"pf_label_get_x({a}, {r})"
        if m == "get_y":
            return f"pf_label_get_y({a}, {r})"
        if m == "get_text":
            return f"pf_label_get_text({a}, {r})"
        if m == "set_x":
            return f"pf_label_set_x({a}, {r}, (int64_t)({av[0]}))"
        if m == "set_y":
            return f"pf_label_set_y({a}, {r}, (double)({av[0]}))"
        if m == "set_xy":
            return f"pf_label_set_xy({a}, {r}, (int64_t)({av[0]}), (double)({av[1]}))"
        if m == "set_point":
            return f"pf_label_set_point({a}, {r}, {av[0]})"
        if m == "set_xloc":
            return f"pf_label_set_xloc({a}, {r}, (int64_t)({av[0]}), {self._lower_xloc(raw[1])})"
        if m == "set_yloc":
            return f"pf_label_set_yloc({a}, {r}, {self._lower_yloc(raw[0])})"
        if m == "set_text":
            return f"pf_label_set_text({a}, {r}, {av[0]})"
        if m == "copy":
            return f"pf_label_copy({a}, {r})"
        if m == "delete":
            return f"pf_label_delete({a}, {r})"
        return "0"

    def _emit_linefill_method(self, m, a, r, av) -> str:
        if m == "get_line1":
            return f"pf_linefill_get_line1({a}, {r})"
        if m == "get_line2":
            return f"pf_linefill_get_line2({a}, {r})"
        if m == "delete":
            return f"pf_linefill_delete({a}, {r})"
        return "0"

    def _drawing_call_return_cpp(self, node: FuncCall) -> str | None:
        """C++ scalar return type for a drawing GETTER call, else None.

        Covers both the namespace-functional form (``label.get_text(lb)``) and
        the method form (``lb.get_text()``). Used by ``_infer_type`` /
        ``_type_for_decl`` so the receiving local declares as the right scalar
        (an int64_t x-coord, a double y-coord, or a std::string label text)
        instead of the analyzer's default double.
        """
        callee = node.callee
        if not isinstance(callee, MemberAccess):
            return None
        method = callee.member
        _fn, ns = self._resolve_callee(callee)
        from .tables import DRAWING_NS
        dtype = None
        if ns in DRAWING_NS:
            dtype = ns
        else:
            recv_spec = self._type_spec_from_expr(callee.object)
            if (recv_spec is not None and recv_spec.kind == "udt"
                    and recv_spec.name in DRAWING_TYPE_TO_CPP):
                dtype = recv_spec.name
        if dtype is None:
            return None
        return _DRAWING_GETTER_RET.get((dtype, method))

    def _drawing_call_is_void(self, node) -> bool:
        """True if ``node`` is a drawing call that lowers to a VOID C++
        expression (setters, ``delete``, visual-noop) — i.e. it cannot be used
        as a return value / RHS. Constructors (``new``), ``copy`` and getters
        return a value. Used by UDF last-expression lowering so a function whose
        final statement is e.g. ``label.set_text(lb, ...)`` emits it as a
        statement instead of ``return pf_label_set_text(...);``.
        """
        if not isinstance(node, FuncCall) or not isinstance(node.callee, MemberAccess):
            return False
        method = node.callee.member
        _fn, ns = self._resolve_callee(node.callee)
        from .tables import DRAWING_NS
        dtype = None
        if ns in DRAWING_NS:
            dtype = ns
        else:
            recv_spec = self._type_spec_from_expr(node.callee.object)
            if (recv_spec is not None and recv_spec.kind == "udt"
                    and recv_spec.name in DRAWING_TYPE_TO_CPP):
                dtype = recv_spec.name
        if dtype is None:
            return False
        if method in ("new", "copy"):
            return False                      # constructors / copy return a handle
        if (dtype, method) in _DRAWING_GETTER_RET:
            return False                      # getters return a scalar
        geometry, noop = DRAWING_METHODS_BY_TYPE.get(dtype, (frozenset(), frozenset()))
        # any other recognised drawing method is a setter / delete / visual-noop
        return method in geometry or method in noop

    def _call_is_void(self, node) -> bool:
        """True if ``node`` is a call that lowers to a VOID (or non-scalar) C++
        expression and so cannot be used as a function's return value / RHS.

        Covers drawing setters/delete/visual-noop (delegated to
        ``_drawing_call_is_void``), the Pine array MUTATOR methods whose C++
        lowering is void / an iterator (``array.push/insert/clear/set/fill/
        sort/reverse/concat/unshift``), and terminal map ``clear``/``put_all``
        calls. A function ending in one of these calls must emit it as a
        statement with a default return, never ``return <void-expression>;``.
        """
        if self._drawing_call_is_void(node):
            return True
        if not isinstance(node, FuncCall) or not isinstance(node.callee, MemberAccess):
            return False
        method = node.callee.member
        _fn, ns = self._resolve_callee(node.callee)
        if method in _ARRAY_VOID_METHODS:
            # array.<method>(arr, ...) namespace form OR arr.<method>(...)
            # method form on a std::vector receiver.
            if ns == "array":
                return True
            recv_spec = self._type_spec_from_expr(node.callee.object)
            if recv_spec is not None and recv_spec.kind == "array":
                return True

        if method not in _MAP_VOID_METHODS:
            return False
        # Preserve the unsupported arbitrary-map-receiver boundary. Namespace
        # functional calls are established; method calls must use a named
        # global/local/parameter receiver.
        if ns == "map":
            # The established namespace-functional form has a positional map
            # receiver.  Keyword-only receiver routing remains unsupported
            # and must retain its pre-KI-48g fallback output.
            expected_arity = 1 if method == "clear" else 2
            return len(node.args) == expected_arity and not node.kwargs
        if not isinstance(node.callee.object, Identifier):
            return False
        name = node.callee.object.name
        param_spec = getattr(self, "_current_func_param_specs", {}).get(name)
        if method == "clear":
            if node.args or node.kwargs:
                return False
        elif param_spec is not None and param_spec.kind == "map":
            if not (
                (len(node.args) == 1 and not node.kwargs)
                or (not node.args and set(node.kwargs) == {"id2"})
            ):
                return False
        elif len(node.args) != 1 or node.kwargs:
            return False
        recv_spec = param_spec or self._type_spec_from_expr(node.callee.object)
        return recv_spec is not None and recv_spec.kind == "map"

    # ------------------------------------------------------------------
    # _uses_drawing detection + arena caps
    # ------------------------------------------------------------------
    def _spec_mentions_drawing(self, spec) -> bool:
        if spec is None:
            return False
        if spec.kind == "udt" and spec.name in DRAWING_TYPE_TO_CPP:
            return True
        if spec.kind == "array":
            return self._spec_mentions_drawing(spec.element)
        if spec.kind == "map":
            return self._spec_mentions_drawing(spec.key) or self._spec_mentions_drawing(spec.value)
        if spec.kind == "matrix":
            return self._spec_mentions_drawing(spec.element)
        return False

    def _detect_drawing_usage(self) -> bool:
        """True iff emitted C++ needs pineforge/drawing.hpp + the arenas.

        Mirrors ``_detect_matrix_usage``: any var/field/array of a drawing type
        OR any line/box/label/linefill namespace call OR any chart.point call.
        """
        for t in self._udt_var_types.values():
            if t in DRAWING_TYPE_TO_CPP:
                return True
        for spec in self._collection_types.values():
            if self._spec_mentions_drawing(spec):
                return True
        for specs in self._func_collection_types.values():
            for spec in specs.values():
                if self._spec_mentions_drawing(spec):
                    return True
        for specs in self._block_collection_types.values():
            for spec in specs.values():
                if self._spec_mentions_drawing(spec):
                    return True
        for fields in self._udt_field_type_specs.values():
            for spec in fields.values():
                if self._spec_mentions_drawing(spec):
                    return True
        for _tname, _fields in self._udt_defs.items():
            for f in _fields:
                if self._spec_mentions_drawing(self._type_spec_from_hint_name(f.type_name)):
                    return True
        from .tables import DRAWING_NS
        for node in self._walk_ast(self.ctx.ast):
            if isinstance(node, FuncCall):
                _fn, ns = self._resolve_callee(node.callee)
                if ns in DRAWING_NS:
                    return True
                if self._is_chart_point_callee(node.callee):
                    return True
        return False

    def _compute_drawing_caps(self) -> dict:
        """Per-type arena capacity from the strategy() header (default 50)."""
        from ..ast_nodes import StrategyDecl
        caps = {"line": 50, "box": 50, "label": 50, "linefill": 50}
        header_field = {"line": "max_lines_count", "box": "max_boxes_count",
                        "label": "max_labels_count"}
        for node in self._walk_ast(self.ctx.ast):
            if isinstance(node, StrategyDecl):
                for key, field in header_field.items():
                    v = self._int_literal_value(node.kwargs.get(field))
                    if v is not None and v > 0:
                        caps[key] = v
        return caps
