"""Run all admission checks with disposable state on every supported OS."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plugin', type=Path, required=True)
    parser.add_argument('--hermes', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    args = parser.parse_args()
    plugin, hermes, workspace = (
        path.resolve() for path in (args.plugin, args.hermes, args.workspace)
    )
    temp_root = Path(os.environ.get('RUNNER_TEMP') or tempfile.gettempdir()).resolve()
    temp_root.mkdir(parents=True, exist_ok=True)
    # These paths must not inherit a checkout as their temporary-file root.
    for parent in (temp_root, *temp_root.parents):
        if (parent / '.git').exists():
            parser.error(f'temporary root is inside a git checkout: {temp_root}')
    results: list[tuple[str, int]] = []
    with tempfile.TemporaryDirectory(prefix='vibe-relay-ci-', dir=temp_root) as state:
        home = Path(state) / 'hermes-home'
        temp = Path(state) / 'tmp'
        home.mkdir()
        temp.mkdir()
        env = os.environ.copy()
        env.update(HERMES_HOME=str(home), TMPDIR=str(temp), TEMP=str(temp), TMP=str(temp))
        env.pop('HERMES_DELEGATED_CHILD_CONTEXT', None)
        env['PYTHONPATH'] = os.pathsep.join(
            [str(hermes)] + ([env['PYTHONPATH']] if env.get('PYTHONPATH') else [])
        )

        def run(name: str, command: list[str]) -> None:
            print(f'\n=== {name} ===', flush=True)
            started = time.monotonic()
            try:
                code = subprocess.run(command, cwd=plugin, env=env, check=False).returncode
            except OSError as exc:
                print(f'{name}: could not launch: {exc}', flush=True)
                code = 1
            results.append((name, code))
            print(f'=== {name}: exit={code}, {time.monotonic() - started:.2f}s ===', flush=True)

        run('tests', [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'])
        run('validate', ['hermes', 'plugins', 'validate', '.', '--install-deps'])
        try:
            scanner = workspace / 'scripts' / 'check-windows-footguns.py'
            scanner.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(hermes / 'scripts' / scanner.name, scanner)
            paths = sorted(plugin.glob('*.py')) + sorted((plugin / 'scripts').glob('*.py'))
            paths += [plugin / 'vendor' / 'dev_framework' / '__init__.py']
            lean = plugin / 'vendor' / 'lean_orchestration'
            paths += sorted(lean.glob('*.py')) + [lean / 'lo_core', lean / 'lo_exec']
            print(f'Scanner production targets: {len(paths)}', flush=True)
            run('scanner', [sys.executable, str(scanner), *(str(path) for path in paths)])
        except OSError as exc:
            print(f'scanner: setup failed: {exc}', flush=True)
            results.append(('scanner', 1))
    print('\nSummary: ' + ', '.join(f'{name}=exit {code}' for name, code in results), flush=True)
    return int(any(code != 0 for _, code in results))


if __name__ == '__main__':
    raise SystemExit(main())
