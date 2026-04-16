import os
import json
import sys
from tree_sitter import Language, Parser
import tree_sitter_cpp
import tree_sitter_python
def setup_parsers():
    cpp_lang = Language(tree_sitter_cpp.language())
    py_lang = Language(tree_sitter_python.language())
    cpp_parser = Parser()
    cpp_parser.language = cpp_lang
    py_parser = Parser()
    py_parser.language = py_lang
    return cpp_parser, py_parser
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
    cpp_parser, py_parser = setup_parsers()
    files_payload = []
    failed_files = []
    files_scanned = 0
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d != 'venv']
        for file in files:
            filepath = os.path.join(root, file)
            ext = os.path.splitext(file)[1].lower()
            ast_tree = None
            language_tag = ""
            if ext in ['.cpp', '.hpp', '.h', '.c', '.cc']:
                if file in ['compare.py', 'extract.py']:
                    continue
                ast_tree = extract_nested_ast(filepath, cpp_parser)
                language_tag = "cpp"
            elif ext == '.py':
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
        print("Usage: python paranoia.py <path_to_project_folder>")
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