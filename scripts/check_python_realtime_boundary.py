#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PYTHON_PACKAGE = ROOT / "waterbag_inspection"

FORBIDDEN_IMPORT_ROOTS = {
    "gxipy",
    "harvesters",
    "MvCameraControl_class",
    "opcua",
    "pyads",
    "pycomm3",
    "pylogix",
    "pymodbus",
    "pypylon",
    "snap7",
}

FORBIDDEN_CALLS = {
    "arm_burst",
    "execute_sort_command",
    "grab_frame",
    "read_coils",
    "read_discrete_inputs",
    "read_holding_registers",
    "read_input_registers",
    "read_laser_presence",
    "sort_ng",
    "sort_ok",
    "start_grabbing",
    "start_light_burst",
    "VideoCapture",
    "write_coil",
    "write_register",
    "write_registers",
}

ALLOWED_POST_ROUTES = {
    "/api/demo/upload",
    "/api/results/sync",
}


def _line_for(node: ast.AST) -> int:
    return int(getattr(node, "lineno", 0))


def _import_root(module: str) -> str:
    return module.split(".", maxsplit=1)[0]


def _forbidden_symbol_reason(name: str) -> str | None:
    lowered = name.lower()
    if "plc" in lowered:
        return "PLC control belongs in C++"
    if "modbus" in lowered:
        return "Modbus transport belongs in C++"
    if "sorter" in lowered or "sorting" in lowered:
        return "sorter control belongs in C++"
    if "realtime" in lowered:
        return "realtime pipeline belongs in C++"
    if "replay" in lowered:
        return "replay scheduling belongs in C++"
    if "fault" in lowered and ("inject" in lowered or "injection" in lowered):
        return "fault injection belongs in C++ tests"
    if "burst" in lowered and any(token in lowered for token in ("controller", "driver", "orchestrator", "capture")):
        return "burst orchestration belongs in C++"
    if "camera" in lowered and any(token in lowered for token in ("sdk", "driver", "controller", "capture", "grab", "acquire")):
        return "camera control belongs in C++"
    return None


def _route_path_from_decorator(decorator: ast.AST) -> str | None:
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    if not isinstance(func, ast.Attribute) or func.attr != "route":
        return None
    if not decorator.args:
        return None
    first = decorator.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _route_methods_from_decorator(decorator: ast.AST) -> set[str]:
    if not isinstance(decorator, ast.Call):
        return set()
    for keyword in decorator.keywords:
        if keyword.arg != "methods":
            continue
        value = keyword.value
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            methods = set()
            for item in value.elts:
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    methods.add(item.value.upper())
            return methods
    return {"GET"}


def _node_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def check_file(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    errors: list[str] = []
    relpath = path.relative_to(ROOT)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = _import_root(alias.name)
                if root in FORBIDDEN_IMPORT_ROOTS:
                    errors.append(f"{relpath}:{_line_for(node)} imports {alias.name}; industrial IO control belongs in C++")

        if isinstance(node, ast.ImportFrom) and node.module:
            root = _import_root(node.module)
            if root in FORBIDDEN_IMPORT_ROOTS:
                errors.append(f"{relpath}:{_line_for(node)} imports {node.module}; industrial IO control belongs in C++")

        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            reason = _forbidden_symbol_reason(node.name)
            if reason:
                errors.append(f"{relpath}:{_line_for(node)} defines {node.name}; {reason}")

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    path_text = _route_path_from_decorator(decorator)
                    if not path_text:
                        continue
                    methods = _route_methods_from_decorator(decorator)
                    if "POST" in methods and path_text not in ALLOWED_POST_ROUTES:
                        errors.append(f"{relpath}:{_line_for(node)} defines POST {path_text}; no new Python realtime/control APIs")

        if isinstance(node, ast.Call):
            name = _node_name(node.func)
            if name in FORBIDDEN_CALLS:
                errors.append(f"{relpath}:{_line_for(node)} calls {name}; realtime hardware actions belong in C++")

        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_CALLS:
            errors.append(f"{relpath}:{_line_for(node)} references {node.attr}; realtime hardware actions belong in C++")

    return errors


def main() -> int:
    errors: list[str] = []
    for path in sorted(PYTHON_PACKAGE.rglob("*.py")):
        errors.extend(check_file(path))

    if errors:
        print("Python realtime boundary check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Python realtime boundary check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
