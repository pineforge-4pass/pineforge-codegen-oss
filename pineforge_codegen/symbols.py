from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from .errors import SourceLocation


class PineType(Enum):
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    STRING = "string"
    COLOR = "color"
    VOID = "void"
    NA = "na"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TypeSpec:
    """Structured Pine type metadata for collections and UDTs.

    ``PineType`` intentionally stays small because much of the older analyzer
    expects primitive enum values.  TypeSpec carries the extra shape needed by
    codegen and diagnostics without forcing a broad type-system rewrite.
    """

    kind: str
    name: str | None = None
    element: TypeSpec | None = None
    key: TypeSpec | None = None
    value: TypeSpec | None = None

    @classmethod
    def primitive(cls, name: str) -> TypeSpec:
        return cls(kind="primitive", name=name)

    @classmethod
    def udt(cls, name: str) -> TypeSpec:
        return cls(kind="udt", name=name)

    @classmethod
    def array(cls, element: TypeSpec) -> TypeSpec:
        return cls(kind="array", element=element)

    @classmethod
    def map(cls, key: TypeSpec, value: TypeSpec) -> TypeSpec:
        return cls(kind="map", key=key, value=value)

    @classmethod
    def matrix(cls, element: TypeSpec) -> TypeSpec:
        return cls(kind="matrix", element=element)

    def __str__(self) -> str:
        if self.kind == "array" and self.element is not None:
            return f"array<{self.element}>"
        if self.kind == "map" and self.key is not None and self.value is not None:
            return f"map<{self.key},{self.value}>"
        if self.kind == "matrix" and self.element is not None:
            return f"matrix<{self.element}>"
        return self.name or self.kind


def method_receiver_type_name(spec: TypeSpec | None) -> str | None:
    """Canonical Pine type name used to key a user method receiver.

    ``MethodDef.type_name`` is the parser-normalized spelling (for example
    ``array<int>``).  Calls are resolved from structured ``TypeSpec`` values,
    so both sides need one lossless spelling across primitive, UDT, and
    collection receivers.  Incomplete collection specs stay unresolved rather
    than accidentally colliding under a coarse ``array``/``map``/``matrix``
    key.
    """

    if spec is None:
        return None
    if spec.kind in {"primitive", "udt"}:
        return spec.name
    if spec.kind == "array" and spec.element is not None:
        element = method_receiver_type_name(spec.element)
        return f"array<{element}>" if element is not None else None
    if spec.kind == "map" and spec.key is not None and spec.value is not None:
        key = method_receiver_type_name(spec.key)
        value = method_receiver_type_name(spec.value)
        if key is not None and value is not None:
            return f"map<{key},{value}>"
        return None
    if spec.kind == "matrix" and spec.element is not None:
        element = method_receiver_type_name(spec.element)
        return f"matrix<{element}>" if element is not None else None
    return None


def method_receiver_cpp_token(
    spec: TypeSpec | None,
    fallback_name: str | None = None,
) -> str:
    """Return a stable C++ identifier fragment for a method receiver type.

    Existing primitive, UDT, and drawing receiver spellings remain unchanged.
    Generic Pine types cannot be embedded directly in an identifier, so their
    structure is encoded recursively (``array<int>`` -> ``array_int``).  The
    fallback only supports older/synthetic ``FuncInfo`` records that lack the
    structured receiver spec.
    """

    if spec is not None:
        if spec.kind in {"primitive", "udt"} and spec.name:
            return spec.name
        if spec.kind == "array" and spec.element is not None:
            return f"array_{method_receiver_cpp_token(spec.element)}"
        if spec.kind == "map" and spec.key is not None and spec.value is not None:
            return (
                f"map_{method_receiver_cpp_token(spec.key)}_"
                f"{method_receiver_cpp_token(spec.value)}"
            )
        if spec.kind == "matrix" and spec.element is not None:
            return f"matrix_{method_receiver_cpp_token(spec.element)}"

    raw = fallback_name or "receiver"
    token = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw)
    if token and token[0].isdigit():
        token = f"type_{token}"
    return token or "receiver"


@dataclass
class Symbol:
    name: str
    pine_type: PineType
    is_series: bool
    is_var: bool
    is_const: bool
    const_value: Any
    scope: str
    loc: SourceLocation
    # User-defined enum: Pine `enum` is a derived type (not a primitive). We still
    # use PineType.INT for variables holding the selected member’s ordinal in C++.
    # enum_type_name links to the enum’s name so str.tostring / field payloads work.
    enum_type_name: str | None = None
    # User-defined type: variable holds an instance of this UDT (from TypeName.new(...)).
    udt_type_name: str | None = None
    # Structured collection/UDT type, when a primitive PineType is insufficient.
    type_spec: TypeSpec | None = None


class Scope:
    def __init__(self, name: str, parent: Scope | None):
        self.name = name
        self.parent = parent
        self.symbols: dict[str, Symbol] = {}

    def define(self, symbol: Symbol) -> None:
        self.symbols[symbol.name] = symbol

    def resolve(self, name: str) -> Symbol | None:
        if name in self.symbols:
            return self.symbols[name]
        if self.parent is not None:
            return self.parent.resolve(name)
        return None


class SymbolTable:
    def __init__(self):
        self.global_scope = Scope(name="global", parent=None)
        self.current_scope = self.global_scope
        self.all_scopes: list[Scope] = [self.global_scope]

    def enter_scope(self, name: str) -> None:
        new_scope = Scope(name=name, parent=self.current_scope)
        self.all_scopes.append(new_scope)
        self.current_scope = new_scope

    def exit_scope(self) -> None:
        if self.current_scope.parent is not None:
            self.current_scope = self.current_scope.parent

    def define(self, symbol: Symbol) -> None:
        self.current_scope.define(symbol)

    def resolve(self, name: str) -> Symbol | None:
        return self.current_scope.resolve(name)
