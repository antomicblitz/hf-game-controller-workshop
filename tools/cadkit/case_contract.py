"""Trust boundary for candidate case sources produced by the coding agent.

Submitting a candidate case.py lets generated code execute inside the CAD
sandbox. Before that ever happens, this module proves statically — via AST,
never by executing the candidate — that the candidate kept the authoritative
geometry contract: canonical ASSEMBLY_SPEC, PLACEMENTS, and build_case wiring,
the single unparameterized snap_fit_pair call, broad-face-down print
orientation, the shared USB opening, the literal EXTERIOR_DESIGN binding, and
no raw build123d imports or dynamic-binding escape hatches. Each rule returns a
human-readable rejection string instead of raising, so the editor can surface
the first violation to the student.

The entry point is :func:`source_contract_error`. Editor server routes call it;
cadkit.case_source performs the complementary source-preserving rewrites.
"""

from __future__ import annotations

import ast
from pathlib import Path

__all__ = ["source_contract_error"]


def _direct_named_call(value: ast.AST | None, name: str) -> ast.Call | None:
    if not isinstance(value, ast.Call):
        return None
    return value if isinstance(value.func, ast.Name) and value.func.id == name else None


def _module_assignment(tree: ast.Module, name: str) -> ast.expr | None:
    value: ast.expr | None = None
    for statement in tree.body:
        if isinstance(statement, ast.AnnAssign):
            if isinstance(statement.target, ast.Name) and statement.target.id == name:
                value = statement.value
        elif isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in statement.targets
        ):
            value = statement.value
    return value


def _module_direct_call(tree: ast.Module, name: str, call_name: str) -> ast.Call | None:
    return _direct_named_call(_module_assignment(tree, name), call_name)


def _has_unique_named_keyword(call: ast.Call, keyword: str, value: str) -> bool:
    matches = [
        item
        for item in call.keywords
        if item.arg == keyword and isinstance(item.value, ast.Name) and item.value.id == value
    ]
    return len(matches) == 1


def _has_raw_build123d_import(tree: ast.Module) -> bool:
    allowed = {"BuildPart", "Location", "Locations", "Mode", "Part", "add"}
    module_statements = {id(node) for node in tree.body}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("build123d")
            and (
                id(node) not in module_statements
                or node.module != "build123d"
                or any(
                    alias.name not in allowed or alias.asname is not None for alias in node.names
                )
            )
        ):
            return True
        if isinstance(node, ast.Import) and any(
            alias.name == "build123d" or alias.name.startswith("build123d.") for alias in node.names
        ):
            return True
    return False


def _imported_binding(alias: ast.alias, *, from_import: bool) -> str:
    return alias.asname or (alias.name if from_import else alias.name.split(".", 1)[0])


def _is_canonical_cadkit_import(
    node: ast.ImportFrom,
    alias: ast.alias,
    name: str,
    module_statements: set[int],
) -> bool:
    return (
        id(node) in module_statements
        and node.module == "cadkit"
        and alias.name == name
        and alias.asname is None
    )


def _from_import_binding_count(
    node: ast.ImportFrom,
    name: str,
    module_statements: set[int],
) -> int | None:
    if any(alias.name == "*" for alias in node.names):
        return None
    matching = [alias for alias in node.names if _imported_binding(alias, from_import=True) == name]
    if any(
        not _is_canonical_cadkit_import(node, alias, name, module_statements) for alias in matching
    ):
        return None
    return len(matching)


def _cadkit_import_count_or_conflict(tree: ast.Module, name: str) -> int | None:
    count = 0
    module_statements = {id(statement) for statement in tree.body}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            binding_count = _from_import_binding_count(node, name, module_statements)
            if binding_count is None:
                return None
            count += binding_count
        elif isinstance(node, ast.Import) and any(
            _imported_binding(alias, from_import=False) == name for alias in node.names
        ):
            return None
    return count


def _attribute_root_name(node: ast.AST) -> str | None:
    current = node
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _source_shadows_name(tree: ast.Module, name: str) -> bool:
    return any(
        (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == name
        )
        or (
            isinstance(node, ast.Name)
            and node.id == name
            and isinstance(node.ctx, (ast.Store, ast.Del))
        )
        or (isinstance(node, ast.arg) and node.arg == name)
        or (isinstance(node, ast.ExceptHandler) and node.name == name)
        or (isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == name)
        or (isinstance(node, ast.Attribute) and _attribute_root_name(node) == name)
        for node in ast.walk(tree)
    )


def _has_unshadowed_cadkit_binding(tree: ast.Module, name: str) -> bool:
    return _cadkit_import_count_or_conflict(tree, name) == 1 and not _source_shadows_name(
        tree, name
    )


def _main_assignment_call(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    targets: tuple[str, str],
    call_name: str,
) -> ast.Call | None:
    for statement in function.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        if not isinstance(target, (ast.Tuple, ast.List)) or [
            item.id if isinstance(item, ast.Name) else None for item in target.elts
        ] != list(targets):
            continue
        return _direct_named_call(statement.value, call_name)
    return None


def _has_dynamic_geometry_binding(tree: ast.Module) -> bool:
    dynamic_names = {
        "__import__",
        "delattr",
        "eval",
        "exec",
        "globals",
        "locals",
        "setattr",
        "vars",
    }
    return any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id in dynamic_names)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module")
        )
        for node in ast.walk(tree)
    )


def _protected_geometry_binding_error(tree: ast.Module) -> str | None:
    if _has_raw_build123d_import(tree):
        return "candidate source may not recreate snap geometry with raw build123d primitives"
    if _has_dynamic_geometry_binding(tree):
        return "candidate source may not dynamically replace protected geometry bindings"
    if not _has_unshadowed_cadkit_binding(tree, "gamepad_body"):
        return "candidate source must retain the unshadowed cadkit gamepad_body import"
    if not _has_unshadowed_cadkit_binding(tree, "snap_fit_pair"):
        return "candidate source must retain the unshadowed cadkit snap_fit_pair import"
    if not _has_unshadowed_cadkit_binding(tree, "orient_case_halves_for_print"):
        return "candidate source must retain authoritative print orientation"
    return None


def _authoritative_main(tree: ast.Module) -> ast.FunctionDef | None:
    functions = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"
    ]
    return functions[0] if len(functions) == 1 else None


def _snap_call_contract_error(tree: ast.Module, main_function: ast.FunctionDef) -> str | None:
    snap_calls = [
        call
        for node in ast.walk(tree)
        if (call := _direct_named_call(node, "snap_fit_pair")) is not None
    ]
    if len(snap_calls) != 1:
        return "candidate source must retain exactly one authoritative snap_fit_pair call"
    snap_call = _main_assignment_call(
        main_function,
        targets=("assembly_top", "assembly_bottom"),
        call_name="snap_fit_pair",
    )
    if snap_call is None:
        return "candidate source must apply snap_fit_pair to the exported shell halves"
    if (
        len(snap_call.args) != 1
        or not isinstance(snap_call.args[0], ast.Name)
        or snap_call.args[0].id != "case_for_main"
        or bool(snap_call.keywords)
    ):
        return "candidate source may not parameterize authoritative snap-fit geometry"
    return None


def _print_orientation_contract_error(
    tree: ast.Module, main_function: ast.FunctionDef
) -> str | None:
    orientation_calls = [
        call
        for node in ast.walk(tree)
        if (call := _direct_named_call(node, "orient_case_halves_for_print")) is not None
    ]
    orientation_call = _main_assignment_call(
        main_function,
        targets=("top", "bottom"),
        call_name="orient_case_halves_for_print",
    )
    actual_arguments = (
        [
            argument.id if isinstance(argument, ast.Name) else None
            for argument in orientation_call.args
        ]
        if orientation_call is not None
        else []
    )
    if (
        len(orientation_calls) != 1
        or orientation_call is None
        or actual_arguments != ["assembly_top", "assembly_bottom"]
        or bool(orientation_call.keywords)
    ):
        return "candidate source must retain authoritative broad-face-down print orientation"
    return None


def _authoritative_geometry_contract_error(
    tree: ast.Module, body_calls: list[ast.Call]
) -> str | None:
    if len(body_calls) != 1:
        return "candidate build_case must retain exactly one direct gamepad_body call"
    body_call = body_calls[0]
    if not _has_unique_named_keyword(body_call, "fillet_radius_mm", "FILLET_RADIUS_MM"):
        return "candidate build_case must pass FILLET_RADIUS_MM as gamepad_body fillet_radius_mm"
    required_body_keywords = (
        ("length_mm", "CASE_LENGTH_MM"),
        ("width_mm", "CASE_WIDTH_MM"),
        ("thickness_mm", "CASE_THICKNESS_MM"),
        ("feather_variant", "FEATHER_VARIANT"),
        ("exterior_design", "EXTERIOR_DESIGN"),
    )
    if any(
        not _has_unique_named_keyword(body_call, name, value)
        for name, value in required_body_keywords
    ):
        return (
            "candidate gamepad_body must retain canonical dimensions, fillet, Feather, "
            "and EXTERIOR_DESIGN bindings"
        )
    if any(keyword.arg == "wall_thickness_mm" for call in body_calls for keyword in call.keywords):
        return "candidate source may not override the authoritative shell wall thickness"
    if binding_error := _protected_geometry_binding_error(tree):
        return binding_error
    main_function = _authoritative_main(tree)
    if main_function is None:
        return "candidate source must retain exactly one main function"
    return _snap_call_contract_error(tree, main_function) or _print_orientation_contract_error(
        tree, main_function
    )


def _assembly_contract_error(tree: ast.Module) -> str | None:
    assembly_call = _module_direct_call(tree, "ASSEMBLY_SPEC", "build_demo_assembly")
    if assembly_call is None:
        return "candidate ASSEMBLY_SPEC must retain the direct build_demo_assembly call"
    dimensions = assembly_call.args[0] if len(assembly_call.args) == 1 else None
    canonical_dimensions = isinstance(dimensions, ast.Tuple) and [
        item.id for item in dimensions.elts if isinstance(item, ast.Name)
    ] == ["CASE_LENGTH_MM", "CASE_WIDTH_MM", "CASE_THICKNESS_MM"]
    if not canonical_dimensions:
        return "candidate ASSEMBLY_SPEC must retain canonical case dimensions"
    if not _has_unique_named_keyword(assembly_call, "case_fillet_radius_mm", "FILLET_RADIUS_MM"):
        return "candidate ASSEMBLY_SPEC must pass FILLET_RADIUS_MM as case_fillet_radius_mm"
    if not _has_unique_named_keyword(assembly_call, "buttons", "CONTROLS"):
        return (
            "candidate ASSEMBLY_SPEC must retain canonical CONTROLS and FILLET_RADIUS_MM bindings"
        )
    if {item.arg for item in assembly_call.keywords} != {
        "buttons",
        "case_fillet_radius_mm",
    }:
        return "candidate ASSEMBLY_SPEC may not parameterize protected assembly geometry"
    return None


def _usb_contract_error(build_case: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    calls = [
        call
        for node in ast.walk(build_case)
        if (call := _direct_named_call(node, "shared_usb_opening")) is not None
    ]
    if len(calls) != 1:
        return "candidate build_case must retain exactly one shared USB opening call"
    call = calls[0]
    argument = call.args[0] if len(call.args) == 1 else None
    canonical_argument = isinstance(argument, ast.Tuple) and [
        item.attr
        for item in argument.elts
        if isinstance(item, ast.Attribute)
        and isinstance(item.value, ast.Name)
        and item.value.id == "opening_dimensions"
    ] == ["x", "y", "z"]
    if not canonical_argument:
        return "candidate build_case must retain the canonical shared USB opening dimensions"
    if call.keywords:
        return "candidate build_case may not parameterize the shared USB opening"
    return None


def _build_case_contract_error(tree: ast.Module) -> str | None:
    build_cases = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "build_case"
    ]
    if len(build_cases) != 1:
        return "candidate source must retain exactly one build_case function"
    build_case = build_cases[0]
    if usb_error := _usb_contract_error(build_case):
        return usb_error
    body_calls = [
        call
        for node in ast.walk(build_case)
        if (call := _direct_named_call(node, "gamepad_body")) is not None
    ]
    placements_call = _module_direct_call(tree, "PLACEMENTS", "placements_from_assembly")
    if placements_call is None or len(placements_call.args) != 1 or placements_call.keywords:
        return "candidate PLACEMENTS must be derived from ASSEMBLY_SPEC"
    if (
        not isinstance(placements_call.args[0], ast.Name)
        or placements_call.args[0].id != "ASSEMBLY_SPEC"
    ):
        return "candidate PLACEMENTS must be derived exactly from ASSEMBLY_SPEC"
    case_call = _module_direct_call(tree, "case", "build_case")
    if case_call is None or case_call.args or case_call.keywords:
        return "candidate case must be the direct build_case result"
    return _authoritative_geometry_contract_error(tree, body_calls)


def source_contract_error(path: Path) -> str | None:
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except (OSError, SyntaxError) as exc:
        return f"candidate source is not valid Python: {exc}"
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    missing = sorted(
        {"CONTROLS", "EXTERIOR_DESIGN", "ASSEMBLY_SPEC", "PLACEMENTS", "build_case", "case"} - names
    )
    if missing:
        return f"candidate source is missing required names: {', '.join(missing)}"
    try:
        from .case_source import inspect_exterior_design_source

        inspect_exterior_design_source(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"candidate source must retain a literal EXTERIOR_DESIGN binding: {exc}"
    if assembly_error := _assembly_contract_error(tree):
        return assembly_error
    return _build_case_contract_error(tree)
