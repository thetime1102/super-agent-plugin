#!/usr/bin/env python3
"""
replace_code_symbol.py — Safe Code Surgery with Tree-sitter AST
===============================================================
Cắt ghép code an toàn dựa trên tọa độ AST (byte-level).

Không regex, không string manipulation — dùng Tree-sitter parse
chuẩn CST (Concrete Syntax Tree) để tìm chính xác vị trí symbol.

Usage:
  python replace_code_symbol.py <file> <symbol> <new_file>
  python replace_code_symbol.py <file> <symbol> --code "new code here"
  python replace_code_symbol.py <file> <symbol> --dry-run

Examples:
  # Replace function 'greet' with new implementation
  python replace_code_symbol.py src/service.ts greet new_impl.ts

  # Dry-run: show what would be replaced
  python replace_code_symbol.py src/service.ts hello --dry-run

  # Inline code
  python replace_code_symbol.py src/service.ts hello --code "export function hello() { return 'hi'; }"
"""

import argparse
import difflib
import os
import sys
import shutil
import tempfile
from typing import Optional

# ─── Tree-sitter setup ────────────────────────────────────────────────────

_LANGUAGE_MAP = {}

def _get_language(ext: str):
    """Get tree-sitter Language for file extension."""
    if ext in _LANGUAGE_MAP:
        return _LANGUAGE_MAP[ext]

    try:
        import tree_sitter as ts
        caps = None
        if ext in ('.ts', '.tsx'):
            from tree_sitter_typescript import language_tsx, language_typescript
            caps = language_tsx() if ext == '.tsx' else language_typescript()
        elif ext == '.js':
            from tree_sitter_javascript import language_javascript
            caps = language_javascript()
        elif ext == '.py':
            from tree_sitter_python import language_python
            caps = language_python()
        elif ext == '.json':
            from tree_sitter_json import language_json
            caps = language_json()
        elif ext == '.ps1':
            from tree_sitter_powershell import language_powershell
            caps = language_powershell()
        else:
            return None

        if caps is not None:
            lang = ts.Language(caps)
            _LANGUAGE_MAP[ext] = lang
            return lang
        return None
    except Exception as e:
        print(f"!! Lang init error [{ext}]: {e}")
        return None


# ─── AST Symbol Finder ─────────────────────────────────────────────────────

def find_symbol_node(root, symbol_name: str):
    """
    DFS tìm node khớp tên symbol (function/class/method/variable/type).

    Returns: (node, kind) or (None, None)
    """
    # Map node types to their name field
    type_to_name_field = {
        # TypeScript / JavaScript
        'function_declaration': 'name',
        'method_definition': 'name',
        'class_declaration': 'name',
        'arrow_function': None,  # Arrow funcs need parent assignment
        'variable_declarator': 'name',
        'interface_declaration': 'name',
        'type_alias_declaration': 'name',
        'enum_declaration': 'name',
        'export_statement': None,  # Named child
        'lexical_declaration': None,  # Contains variable_declarator

        # Python
        'function_definition': 'name',
        'class_definition': 'name',
        'assignment': None,

        # JSON
        'pair': 'key',

        # PowerShell
        'function_definition': 'name',
    }

    # Walk tree
    cursor = root.walk()
    reached_root = False
    nodes = []

    while True:
        node = cursor.node
        ntype = node.type
        name = None

        # Get name from named child
        if ntype in type_to_name_field:
            field_name = type_to_name_field[ntype]
            if field_name:
                child = node.child_by_field_name(field_name)
                if child:
                    name = child.text.decode('utf-8', errors='replace').strip()

        # Handle export_statement: check first named child
        if ntype == 'export_statement':
            for i in range(node.child_count):
                child = node.child(i)
                if child.type in type_to_name_field:
                    fn = type_to_name_field.get(child.type)
                    if fn:
                        c2 = child.child_by_field_name(fn)
                        if c2:
                            name = c2.text.decode('utf-8', errors='replace').strip()
                            if name == symbol_name:
                                nodes.append((child, 'export'))

        # Handle lexical_declaration (const/let/var)
        if ntype == 'lexical_declaration':
            for i in range(node.child_count):
                child = node.child(i)
                if child.type == 'variable_declarator':
                    nc = child.child_by_field_name('name')
                    if nc:
                        n = nc.text.decode('utf-8', errors='replace').strip()
                        if n == symbol_name:
                            nodes.append((child, 'const'))

        if name and name == symbol_name:
            nodes.append((node, ntype))

        if not cursor.goto_first_child():
            while not cursor.goto_next_sibling():
                if not cursor.goto_parent():
                    reached_root = True
                    break
        if reached_root:
            break

    return nodes if nodes else []


# ─── Replace Operation ────────────────────────────────────────────────────

def replace_symbol(file_path: str, symbol_name: str, new_code: str, dry_run: bool = False) -> bool:
    """
    Replace symbol in file by Tree-sitter coordinate.
    Returns True if replacement was made.
    """
    if not os.path.isfile(file_path):
        print(f"!! File not found: {file_path}")
        return False

    ext = os.path.splitext(file_path)[1].lower()
    lang = _get_language(ext)
    if lang is None:
        print(f"!! No tree-sitter parser for: {ext}")
        return False

    # Read file
    with open(file_path, 'rb') as f:
        source_bytes = f.read()

    # Parse
    import tree_sitter as ts_parser
    parser = ts_parser.Parser(lang)
    tree = parser.parse(source_bytes)
    root = tree.root_node

    # Find symbol
    nodes = find_symbol_node(root, symbol_name)
    if not nodes:
        print(f"!! Symbol '{symbol_name}' not found in {file_path}")
        return False

    # Use first match (most specific)
    node, kind = nodes[0]
    start_byte = node.start_byte
    end_byte = node.end_byte

    # Build new source
    new_bytes = source_bytes[:start_byte] + new_code.encode('utf-8') + source_bytes[end_byte:]

    # Verify parse
    new_tree = parser.parse(new_bytes)
    if new_tree.root_node.has_error:
        print(f"!! Parse error after replacement — syntax invalid!")
        print(f"   Replacement at bytes [{start_byte}:{end_byte}]")
        return False

    # Show diff
    old_text = source_bytes[start_byte:end_byte].decode('utf-8', errors='replace')
    print(f"** Replace: {symbol_name} ({kind}) in {file_path}")
    print(f"   Location: lines {node.start_point[0]+1}-{node.end_point[0]+1}")
    print(f"   Bytes: [{start_byte}:{end_byte}]")

    # Diff
    diff = difflib.unified_diff(
        old_text.splitlines(True),
        new_code.splitlines(True),
        fromfile=f'{file_path}:{symbol_name} (old)',
        tofile=f'{file_path}:{symbol_name} (new)',
        lineterm='',
    )
    for line in diff:
        print(f"   {line}")

    if dry_run:
        print("** Dry-run — no changes written")
        return True

    # Backup
    backup = file_path + '.bak'
    shutil.copy2(file_path, backup)
    print(f"   Backup: {backup}")

    # Write
    with open(file_path, 'wb') as f:
        f.write(new_bytes)
    print(f"   Written: {file_path}")
    return True


def find_symbol(file_path: str, symbol_name: str):
    """Find and display symbol info without replacing."""
    if not os.path.isfile(file_path):
        print(f"!! File not found: {file_path}")
        return

    ext = os.path.splitext(file_path)[1].lower()
    lang = _get_language(ext)
    if lang is None:
        print(f"!! No tree-sitter parser for: {ext}")
        return

    with open(file_path, 'rb') as f:
        source_bytes = f.read()

    import tree_sitter as ts_parser
    parser = ts_parser.Parser(lang)
    tree = parser.parse(source_bytes)
    root = tree.root_node

    nodes = find_symbol_node(root, symbol_name)
    if not nodes:
        print(f"!! Symbol '{symbol_name}' not found")
        return

    print(f"** Found '{symbol_name}' in {file_path}:")
    for node, kind in nodes:
        text = node.text.decode('utf-8', errors='replace')
        lines = text.split('\n')
        print(f"\n  [{kind}] lines {node.start_point[0]+1}-{node.end_point[0]+1}")
        print(f"  Bytes: [{node.start_byte}:{node.end_byte}]")
        for i, line in enumerate(lines[:8]):
            print(f"    {line}")
        if len(lines) > 8:
            print(f"    ... ({len(lines)-8} more lines)")


# ─── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="replace_code_symbol — Safe Code Surgery with Tree-sitter AST",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('file', help='Target file path')
    parser.add_argument('symbol', help='Symbol name to replace (function/class/variable)')
    parser.add_argument('--code', '-c', help='New code content (inline)')
    parser.add_argument('--file', '-f', dest='code_file', help='New code content (from file)')
    parser.add_argument('--dry-run', '-n', action='store_true', help='Show what would change, no write')
    parser.add_argument('--find', action='store_true', help='Just find and display symbol')

    args = parser.parse_args()

    if args.find:
        find_symbol(args.file, args.symbol)
        return

    # Get new code
    new_code = args.code
    if args.code_file:
        if not os.path.isfile(args.code_file):
            print(f"!! Code file not found: {args.code_file}")
            sys.exit(1)
        with open(args.code_file, 'r', encoding='utf-8') as f:
            new_code = f.read()

    if not new_code:
        print("!! Provide --code or --file with replacement code")
        sys.exit(1)

    success = replace_symbol(args.file, args.symbol, new_code, dry_run=args.dry_run)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
