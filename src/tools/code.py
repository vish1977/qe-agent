"""Code analysis tools — read source, detect test framework, write test files."""

import json
import os


def detect_test_framework(repo_path: str) -> str:
    """Detect the testing framework from config files in the repo."""
    frameworks = []

    indicators = {
        "pytest": ["pytest.ini", "pyproject.toml", "setup.cfg", "conftest.py"],
        "jest": ["jest.config.js", "jest.config.ts"],
        "vitest": ["vitest.config.ts", "vitest.config.js"],
        "mocha": [".mocharc.js", ".mocharc.yml"],
        "go_test": ["*_test.go"],
        "junit": ["pom.xml", "build.gradle"],
    }

    for framework, files in indicators.items():
        for f in files:
            if f.startswith("*"):
                # glob check
                ext = f[1:]
                for root, _, filenames in os.walk(repo_path):
                    if any(fn.endswith(ext) for fn in filenames):
                        frameworks.append(framework)
                        break
            else:
                if os.path.exists(os.path.join(repo_path, f)):
                    frameworks.append(framework)
                    break

    if not frameworks:
        # fallback heuristic based on file extensions
        for root, _, filenames in os.walk(repo_path):
            for fn in filenames:
                if fn.endswith(".py"):
                    frameworks.append("pytest")
                    break
                if fn.endswith(".ts") or fn.endswith(".js"):
                    frameworks.append("jest")
                    break
            if frameworks:
                break

    return json.dumps({
        "detected_frameworks": list(set(frameworks)),
        "primary": frameworks[0] if frameworks else "pytest",
        "repo_path": repo_path,
    })


def read_source_file(file_path: str) -> str:
    """Read a source file from the local filesystem."""
    if not os.path.exists(file_path):
        return json.dumps({"error": f"File not found: {file_path}"})
    try:
        with open(file_path) as f:
            content = f.read()
        return json.dumps({"file_path": file_path, "content": content, "lines": content.count("\n") + 1})
    except Exception as e:
        return json.dumps({"error": str(e)})


def write_test_file(file_path: str, content: str) -> str:
    """Write a generated test file to disk (creates directories as needed)."""
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write(content)
        return json.dumps({"written": True, "file_path": file_path, "bytes": len(content)})
    except Exception as e:
        return json.dumps({"written": False, "error": str(e)})


def find_test_files(directory: str, pattern: str = "test_") -> str:
    """Walk a directory and find files that look like test files."""
    test_files = []
    if not os.path.exists(directory):
        return json.dumps({"test_files": [], "error": f"Directory not found: {directory}"})

    for root, _, filenames in os.walk(directory):
        for fn in filenames:
            if pattern in fn or fn.startswith("test") or fn.endswith("_test.py") or fn.endswith(".spec.ts"):
                test_files.append(os.path.join(root, fn))

    return json.dumps({"test_files": test_files, "count": len(test_files)})


def analyze_code_structure(file_path: str) -> str:
    """Basic static analysis — extract function/class names from a source file."""
    import ast

    if not os.path.exists(file_path):
        return json.dumps({"error": f"File not found: {file_path}"})

    if not file_path.endswith(".py"):
        return json.dumps({
            "file_path": file_path,
            "note": "Static analysis currently supported only for .py files",
        })

    try:
        with open(file_path) as f:
            source = f.read()
        tree = ast.parse(source)
        functions = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
        classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        return json.dumps({
            "file_path": file_path,
            "classes": classes,
            "functions": functions,
            "lines": source.count("\n") + 1,
        })
    except SyntaxError as e:
        return json.dumps({"error": f"Syntax error: {e}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Tool schemas ─────────────────────────────────────────────────────────────

CODE_TOOLS = [
    {
        "name": "detect_test_framework",
        "description": "Detect the primary testing framework used in a local repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "Absolute path to the local repo"},
            },
            "required": ["repo_path"],
        },
    },
    {
        "name": "read_source_file",
        "description": "Read the content of a source file from the local filesystem.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "write_test_file",
        "description": "Write a generated or patched test file to the local filesystem.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "find_test_files",
        "description": "Find all test files in a directory matching a naming pattern.",
        "input_schema": {
            "type": "object",
            "properties": {
                "directory": {"type": "string"},
                "pattern": {"type": "string", "default": "test_"},
            },
            "required": ["directory"],
        },
    },
    {
        "name": "analyze_code_structure",
        "description": "Extract class and function names from a Python source file for test generation context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
            },
            "required": ["file_path"],
        },
    },
]


def execute_code_tool(name: str, inp: dict) -> str:
    dispatch = {
        "detect_test_framework": lambda: detect_test_framework(inp["repo_path"]),
        "read_source_file": lambda: read_source_file(inp["file_path"]),
        "write_test_file": lambda: write_test_file(inp["file_path"], inp["content"]),
        "find_test_files": lambda: find_test_files(inp["directory"], inp.get("pattern", "test_")),
        "analyze_code_structure": lambda: analyze_code_structure(inp["file_path"]),
    }
    fn = dispatch.get(name)
    if fn is None:
        return f"Error: unknown code tool '{name}'"
    try:
        return fn()
    except Exception as e:
        return f"Error executing {name}: {e}"
