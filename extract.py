import os
import json
import sys
import importlib
from tree_sitter import Language, Parser
LIB_PATH = "build/my-languages.so"
LANGUAGES = [
    "tree-sitter-cpp",
    "tree-sitter-python",
    "tree-sitter-ocaml/ocaml",
    "tree-sitter-ocaml/ocaml_interface"
]

def ensure_language_lib():
    os.makedirs(os.path.dirname(LIB_PATH), exist_ok=True)
    if not os.path.exists(LIB_PATH):
        try:
            if hasattr(Language, "build_library"):
                Language.build_library(
                    LIB_PATH,
                    [
                        os.path.join(os.path.dirname(__file__), lang)
                        for lang in LANGUAGES
                    ]
                )
            else:
                raise RuntimeError("build_library is not available in this tree_sitter version")
        except Exception as e:
            print("Error: Could not build the shared parser library for legacy tree_sitter API.")
            print(f"Reason: {e}")
            print("Tip: Install the language wheels listed in README and use a recent tree_sitter package.")
            sys.exit(1)


def language_from_module(module_name, attr_candidates):
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None

    for attr_name in attr_candidates:
        if not hasattr(module, attr_name):
            continue
        value = getattr(module, attr_name)
        raw = value() if callable(value) else value

        if isinstance(raw, Language):
            return raw

        try:
            return Language(raw)
        except TypeError:
            continue

    return None


def set_parser_language(parser, language):
    if hasattr(parser, "set_language"):
        parser.set_language(language)
    else:
        parser.language = language


def setup_parsers():
    # Prefer modern prebuilt language wheels first.
    cpp_lang = language_from_module("tree_sitter_cpp", ["language", "LANGUAGE"])
    py_lang = language_from_module("tree_sitter_python", ["language", "LANGUAGE"])
    ocaml_lang = language_from_module("tree_sitter_ocaml", ["language_ocaml", "language", "LANGUAGE"])
    ocaml_interface_lang = language_from_module(
        "tree_sitter_ocaml", ["language_ocaml_interface", "language_interface", "LANGUAGE_INTERFACE"]
    )

    if not all([cpp_lang, py_lang, ocaml_lang, ocaml_interface_lang]):
        ensure_language_lib()
        cpp_lang = Language(LIB_PATH, "cpp")
        py_lang = Language(LIB_PATH, "python")
        ocaml_lang = Language(LIB_PATH, "ocaml")
        ocaml_interface_lang = Language(LIB_PATH, "ocaml_interface")

    cpp_parser = Parser()
    set_parser_language(cpp_parser, cpp_lang)
    py_parser = Parser()
    set_parser_language(py_parser, py_lang)
    ocaml_parser = Parser()
    set_parser_language(ocaml_parser, ocaml_lang)
    ocaml_intf_parser = Parser()
    set_parser_language(ocaml_intf_parser, ocaml_interface_lang)
    return cpp_parser, py_parser, ocaml_parser, ocaml_intf_parser
def extract_nested_ast(filepath, parser):
    try:
        with open(filepath, 'rb') as f:
            source_code = f.read()
    except Exception as e:
        print(f"Skipping file {filepath}: {e}")
        return None
    tree = parser.parse(source_code)
    def traverse(node):
        if not node.is_named:
            return None
        node_data = {"type": node.type}
        children = []
        for child in node.children:
            child_data = traverse(child)
            if child_data:
                children.append(child_data)
        if children:
            node_data["children"] = children
        return node_data
    return traverse(tree.root_node)
def scan_project(directory):
    cpp_parser, py_parser, ocaml_parser, ocaml_intf_parser = setup_parsers()
    files_payload = []
    failed_files = []
    files_scanned = 0
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if (d != 'venv' and d != '.git' and d != 'build' and d != '__pycache__')]
        for file in files:
            filepath = os.path.join(root, file)
            ext = os.path.splitext(file)[1].lower()
            ast_tree = None
            language_tag = ""
            if ext in ['.cpp', '.hpp', '.h', '.c', '.cc']:
                if filepath in ['compare.py', 'extract.py']:
                    continue
                ast_tree = extract_nested_ast(filepath, cpp_parser)
                language_tag = "cpp"
            elif ext == '.ml':
                ast_tree = extract_nested_ast(filepath, ocaml_parser)
                language_tag = "ocaml"
            elif ext == '.mli':
                ast_tree = extract_nested_ast(filepath, ocaml_intf_parser)
                language_tag = "ocaml_interface"
            elif ext == '.py':
                if filepath in ['./compare.py', './extract.py']:
                    continue
                ast_tree = extract_nested_ast(filepath, py_parser)
                language_tag = "python"
            if ast_tree:
                files_payload.append({
                    "language": language_tag,
                    "ast": ast_tree
                })
                files_scanned += 1
            else:
                failed_files.append(filepath)
    return files_payload, files_scanned, failed_files
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extract.py <path_to_project_folder>")
        sys.exit(1)
    target_directory = sys.argv[1]
    output_file = "paranoia_fingerprint.json"
    if not os.path.isdir(target_directory):
        print(f"Error: '{target_directory}' is not a valid directory.")
        sys.exit(1)
    project_asts, file_count, failed_files = scan_project(target_directory)
    if file_count == 0:
        print("No valid C++ or Python files found.")
        sys.exit(0)
    payload = {
        "tool": "PARANOIA_NESTED",
        "scanned_files": file_count,
        "failed_files": failed_files,
        "files": project_asts
    }
    print(f"Scanned {file_count} files. Failed to parse {len(failed_files)} files.")
    try:
        with open(output_file, 'w') as f:
            json.dump(payload, f, separators=(',', ':'))
    except Exception as e:
        print(f"Error saving fingerprint: {e}")
