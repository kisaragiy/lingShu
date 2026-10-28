"""Check release metadata without importing the application."""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
metadata = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
version = metadata['project']['version']
source = (ROOT / 'src/agent_harness/__init__.py').read_text(encoding='utf-8')
match = re.search(r'__version__\s*=\s*[\"\x27]([^\"\x27]+)', source)
if match is None or match.group(1) != version:
    raise SystemExit('Package and project versions disagree')
if f'v{version}' not in (ROOT / 'AGENTS.md').read_text(encoding='utf-8'):
    raise SystemExit('AGENTS.md does not contain the current release version')
print(f'Release metadata consistent: {version}')
