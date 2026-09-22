"""Sort the explicit export declaration without changing names or executable bodies."""
import ast

path = 'uquant/application/__init__.py'
source = (ROOT / path).read_text()
node = next(n for n in ast.parse(source).body if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == '__all__' for t in n.targets))
values = ast.literal_eval(node.value)
assert len(values) == len(set(values)) and all(isinstance(v, str) for v in values)
ordered = tuple(sorted(values, key=lambda v: (0 if v.isupper() else 1 if v[0].isupper() else 2, v)))
lines = source.splitlines(keepends=True)
replacement = '__all__ = (\n' + ''.join(f'    "{v}",\n' for v in ordered) + ')\n'
write(path, ''.join(lines[:node.lineno-1]) + replacement + ''.join(lines[node.end_lineno:]))
