"""Build a self-contained source-layout release with pinned KV and both UIs.

Only curated paths are copied. Private state is never a packaging input.
"""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
import zipfile
import runpy
from check_release import validate, digest

ROOT = Path(__file__).resolve().parents[2]
VERSION = runpy.run_path(str(ROOT / 'workbench/workbench/version.py'))['__version__']
TREES = (
    'argscope/argscope', 'argscope/docs', 'argscope/tests', 'argscope/examples', 'argscope/research',
    'falsifylab/falsifylab', 'falsifylab/docs', 'falsifylab/tests', 'falsifylab/tools', 'falsifylab/configs',
    'sourcelens/sourcelens', 'sourcelens/docs', 'sourcelens/tests', 'sourcelens/examples',
    'sourcelens/evaluation', 'sourcelens/web',
    'workbench/workbench', 'workbench/docs', 'workbench/tests', 'workbench/tools',
    'workbench/examples', 'workbench/studies', 'workbench/review_web',
    'vendor/kv_server', '.github/workflows',
)
SKIP = {'node_modules', '__pycache__', '.pytest_cache', '.DS_Store'}
PRIVATE_DOCS = {
    'workbench/docs/PROJECT_WALKTHROUGH.zh-CN.md',
    'workbench/docs/DESIGN_DISCUSSION.zh-CN.md',
    'workbench/docs/REQUIREMENTS_RATIONALE.md',
    'workbench/docs/PAPER_VALIDATION_WALKTHROUGH.md',
}
PUBLIC_DOCS = {
    'workbench/docs/README.md',
    'workbench/docs/ENGINEERING_DESIGN.md',
    'workbench/docs/FINAL_REPORT.md',
    'workbench/docs/MVP_GUIDE.md',
    'workbench/docs/API_REFERENCE.md',
    'workbench/docs/FIRST_ORDER_GUIDE.md',
}
PUBLIC_DOC_FILES = {
    'workbench/docs/examples/request.json',
    'workbench/docs/examples/draft.json',
    'workbench/docs/assets/review.png',
    'workbench/docs/assets/start.png',
    'workbench/docs/assets/library.png',
    'workbench/docs/assets/versions.png',
    *(f'workbench/docs/evidence/{name}' for name in (
        'reference_checks.json', 'codex-pilot-20261124.json',
        'bailian-quine-feedback-20261124.json', 'kv-lifecycle-20261124.json',
        'kv-aof-rewrite-20261124.json', 'scoped-review-20261124.json',
    )),
}
EXTENSIONS = {'.py', '.md', '.json', '.toml', '.txt', '.html', '.jsx', '.js', '.cjs', '.mjs', '.css', '.png', '.svg', '.yml', '.yaml'}
BOOTSTRAP = '''"""Start the packaged application using only this release's source and KV."""
import os
from pathlib import Path
import sys
root = Path(__file__).resolve().parent
paths = [str(root / name) for name in ("workbench", "argscope", "falsifylab", "sourcelens")]
sys.path[:0] = paths
os.environ["PYTHONPATH"] = os.pathsep.join(paths)
os.environ["PYTHONNOUSERSITE"] = "1"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
from workbench.review_app import main
args = sys.argv[1:]
if not args or args[0] != "verify-report":
    def supplied(option):
        return any(a == option or a.startswith(option + "=") for a in args)
    if not supplied("--kv-root") and not supplied("--kv-port"):
        args = ["--kv-root", str(root / "vendor/kv_server"), *args]
    if not supplied("--data-dir"):
        args = ["--data-dir", str(root / "workbench/outputs/package-data"), *args]
raise SystemExit(main(args))
'''
LAUNCH = '''#!/bin/zsh
set -e
cd -- "${0:A:h}"
exec "$PWD/workbench/Launch MVP.command" "$@"
'''


def source_files(root):
    root = Path(root)
    found = set()
    for tree in TREES:
        directory = root / tree
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError('Missing or symlinked release tree: ' + tree)
        for path in directory.rglob('*'):
            name = path.relative_to(root).as_posix()
            parts = path.relative_to(directory).parts
            if any(part in SKIP or part.endswith('.egg-info') for part in parts):
                continue
            if path.is_symlink():
                raise ValueError('Symlink in release input: ' + str(path.relative_to(root)))
            if path.is_file():
                if path.suffix not in EXTENSIONS:
                    raise ValueError('Unexpected curated input: ' + str(path.relative_to(root)))
                if name.startswith('workbench/docs/') and name not in PUBLIC_DOCS | PUBLIC_DOC_FILES:
                    continue
                found.add(path)
    for name in ('README.md', '.gitignore', 'ArgScope.code-workspace'):
        found.add(root / name)
    if (root / 'LICENSE').is_file():
        found.add(root / 'LICENSE')
    for component in ('argscope', 'falsifylab', 'sourcelens', 'workbench'):
        for pattern in ('README.md', 'pyproject.toml', 'requirements*.txt', '.gitignore'):
            found.update((root / component).glob(pattern))
    found.update((root / 'workbench').glob('*.command'))
    for path in (root / 'workbench').glob('*.app'):
        found.update(p for p in path.rglob('*') if p.is_file())
    for path in sorted(found):
        if path.is_symlink() or not path.is_file():
            raise ValueError('Invalid release input: ' + str(path.relative_to(root)))
        yield path


def build(output, root=ROOT):
    root, output = Path(root).resolve(), Path(output).resolve()
    archive = output.with_name(output.name + '.zip')
    if output.exists() or archive.exists():
        raise ValueError('Choose a new delivery path')
    # Fail before publishing a partial release; both builds are required.
    for ui in ['workbench/workbench/review_web/index.html', 'sourcelens/web/dist/index.html']:
        if not (root / ui).is_file():
            raise ValueError('Build both frontends first: missing ' + ui)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.argscope-build-', dir=output.parent) as temporary:
        stage = Path(temporary) / output.name
        stage.mkdir()
        for source in source_files(root):
            destination = stage / source.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        (stage / 'launch.py').write_text(BOOTSTRAP)
        (stage / 'Launch.command').write_text(LAUNCH)
        (stage / 'Launch.command').chmod(0o755)
        members = {p.relative_to(stage).as_posix(): digest(p) for p in sorted(stage.rglob('*')) if p.is_file()}
        manifest = {'schema': 'argscope-release-v2', 'version': runpy.run_path(str(stage / 'workbench/workbench/version.py'))['__version__'],
                    'layout': 'workspace-relative source, docs, tests, both built UIs and pinned KV',
                    'requires': 'Python 3.10/3.11; workbench/requirements-review.txt; optional Codex CLI/login',
                    'members': members}
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        validation = validate(stage)
        staged_zip = Path(temporary) / archive.name
        with zipfile.ZipFile(staged_zip, 'x', zipfile.ZIP_DEFLATED) as zipped:
            for source in sorted(stage.rglob('*')):
                if source.is_file():
                    info = zipfile.ZipInfo(output.name + '/' + source.relative_to(stage).as_posix(), (2026, 1, 1, 0, 0, 0))
                    info.create_system = 3
                    mode = 0o755 if source.stat().st_mode & 0o111 else 0o644
                    info.external_attr = (0o100000 | mode) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    zipped.writestr(info, source.read_bytes())
        stage.rename(output)
        staged_zip.rename(archive)
    return {'directory': str(output), 'zip': str(archive), 'zip_sha256': digest(archive),
            'members': len(members) + 1, 'validation': validation}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(build(parser.parse_args().output), indent=2))
