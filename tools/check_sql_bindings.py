"""Static check of literal SQL placeholders and literal positional arguments.

Dynamic statements and executemany are outside this deliberately narrow check.
This does not replace actual database or payment acceptance.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    checked, errors = 0, []
    for path in sorted((ROOT / 'web').glob('*.py')):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != 'execute':continue
            if len(node.args) < 2:continue
            query, args = node.args[:2]
            if not isinstance(query, ast.Constant) or not isinstance(query.value, str):continue
            if not isinstance(args, (ast.Tuple, ast.List)) or any(isinstance(value, ast.Starred) for value in args.elts):continue
            checked += 1
            if query.value.count('?') != len(args.elts):
                errors.append(f'{path.relative_to(ROOT)}:{node.lineno}: {query.value.count("?")} placeholders / {len(args.elts)} arguments')
    print(f'Checked {checked} literal SQL calls; dynamic queries not included')
    for error in errors:print(error, file=sys.stderr)
    return bool(errors)


if __name__ == '__main__':raise SystemExit(main())
