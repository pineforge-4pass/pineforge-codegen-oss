"""Typed handoff example only; W4 inventory precedes a production sidecar.

These types describe the accepted facts a consumer must retain. They neither
extract TA sites nor infer startup lengths, convergence, or run adequacy.
"""
from typing import Literal, TypedDict


class SourceRange(TypedDict):
    line: int
    col: int
    end_line: int
    end_col: int


class Length(TypedDict):
    kind: Literal["constant", "input", "unknown"]
    value: int | None
    input_name: str | None
    expression: str | None


class Site(TypedDict):
    call_site_id: str
    source_range: SourceRange
    code: Literal["PF-W1505"]
    length: Length
    context: Literal["chart", "same_symbol_request", "other_symbol_request"]
    timeframe: str | None
    seeding: Literal["first_value", "sma", "unknown"]
    startup_unit: Literal["bars_of_evaluation_context_timeframe"]
    required_startup_bars: int | None
    recursive_adequacy: Literal["unknown"]
    has_unbounded_state: bool


class CompileSidecar(TypedDict):
    version: Literal[1]
    source_sha256: str
    generated_sha256: str
    sites: list[Site]


class ResolvedLength(TypedDict):
    call_site_id: str
    source_range: SourceRange
    scope: Literal["run", "trial", "study"]
    submitted_value: int | None
    searched_range: list[int] | None


def same_site(diagnostic: dict, site: Site) -> bool:
    """Text is deliberately absent from this fixture's join predicate."""
    return (diagnostic.get("call_site_id") == site["call_site_id"]
            and diagnostic.get("source_range") == site["source_range"])
