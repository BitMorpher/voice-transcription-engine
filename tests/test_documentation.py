"""Keep local Markdown links and documented CLI flags aligned with source."""

import ast
from pathlib import Path
import re
import shlex

from src.batch.cli import parser

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / 'README.md', *sorted((ROOT / 'docs').glob('*.md'))]


def test_local_markdown_links_exist():
    for path in DOCS:
        for target in re.findall(r'\]\(([^)]+)\)', path.read_text()):
            if '://' in target or target.startswith('#'):
                continue
            file = target.split('#')[0]
            assert (path.parent / file).exists(), f'Broken documentation link in {path.name}'


def test_documented_cli_options_are_declared():
    tree = ast.parse((ROOT / 'src/cli.py').read_text())
    options = {'--help'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'add_argument':
            options.update(arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str))
    checked = {'voice-transcribe': 0, 'voice-batch': 0}
    for path in DOCS:
        for block in re.findall(r'```bash\n(.*?)```', path.read_text(), re.S):
            for line in block.replace('\\\n', ' ').splitlines():
                words = shlex.split(line, comments=True)
                for command in checked:
                    if command not in words:
                        continue
                    args = words[words.index(command) + 1:]
                    if command == 'voice-batch':
                        if '--help' not in args:
                            parser().parse_args(args)
                    else:
                        assert all(word.split('=', 1)[0] in options for word in args if word.startswith('--')), path.name
                    checked[command] += 1
    assert checked['voice-batch'] >= 15 and checked['voice-transcribe'] >= 20
