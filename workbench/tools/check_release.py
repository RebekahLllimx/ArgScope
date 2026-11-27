"""Validate the deliverable itself: files, links, pinned KV and native versions."""
import argparse
import hashlib
import json
import plistlib
from pathlib import Path
import re
import runpy
from urllib.parse import unquote

RUNTIME_PARTS = {'.venv', 'node_modules', '__pycache__', '.pytest_cache', 'outputs', 'var'}
SECRET_PATTERN = re.compile(rb'(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')
HOME_PATTERN = re.compile(b'/' + b'Users/' + rb'[^/\s"\x27]+/')
CJK_PATTERN = re.compile('[\u3400-\u9fff]')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def slug(value):
    return re.sub(r'\s', '-', re.sub(r'[^\w\s-]', '', re.sub(r'[`*]', '', value).lower()).strip())


def validate(root, manifest=True):
    root = Path(root).resolve()
    errors = []
    files = {}
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        if any(part in RUNTIME_PARTS for part in path.relative_to(root).parts):
            continue
        if path.is_symlink():
            errors.append('symlink: ' + name)
        elif path.is_file() and name != 'manifest.json':
            files[name] = path
            if path.suffix in {'.aof', '.sqlite', '.sqlite3', '.db', '.env', '.pdf', '.zip'} or path.name.startswith('.env'):
                errors.append('private/runtime file: ' + name)
            body = path.read_bytes()
            if SECRET_PATTERN.search(body) or HOME_PATTERN.search(body):
                errors.append('credential/private path pattern: ' + name)
            if path.suffix in {'.py', '.md', '.txt'} and CJK_PATTERN.search(body.decode('utf-8')):
                errors.append('non-English public source/document: ' + name)
    if manifest:
        metadata = json.loads((root / 'manifest.json').read_text())
        expected = metadata['members']
        if set(expected) != set(files):
            errors.append('manifest file set mismatch')
        for name, wanted in expected.items():
            path = files.get(name)
            if path is None or digest(path) != wanted:
                errors.append('manifest digest mismatch: ' + name)
    links = 0
    for name, path in files.items():
        if path.suffix != '.md':
            continue
        plain = re.sub(r'```[\s\S]*?```', '', path.read_text())
        for target in re.findall(r'!?\[[^\]]*\]\(([^\n)]+)\)', plain):
            target = target.strip().strip('<>')
            if re.match(r'^[a-z]+:', target):
                continue
            part, _, anchor = target.partition('#')
            destination = (path.parent / unquote(part)).resolve() if part else path
            links += 1
            if not destination.is_relative_to(root) or not destination.exists():
                errors.append(name + ': missing/outside link ' + target)
            elif destination.is_file() and destination.relative_to(root).as_posix() not in files:
                errors.append(name + ': excluded link ' + target)
            elif anchor and destination.suffix == '.md':
                headings = [slug(h) for h in re.findall(r'^#{1,6}\s+(.+?)\s*$', destination.read_text(), re.M)]
                if unquote(anchor) not in headings:
                    errors.append(name + ': missing anchor ' + target)
    for ui in ['workbench/workbench/review_web/index.html', 'sourcelens/web/dist/index.html']:
        page = root / ui
        if not page.is_file():
            errors.append('missing frontend: ' + ui)
            continue
        for target in re.findall(r'(?:src|href)="([^"]+)"', page.read_text()):
            if target.startswith(('http:', 'https:', 'data:', '#')):
                continue
            if not (page.parent / target.lstrip('/')).is_file():
                errors.append('missing frontend asset: ' + target)
        for css in page.parent.rglob('*.css'):
            for target in re.findall(r'url\(\s*[\x22\x27]?([^\s\x22\x27)]+)', css.read_text()):
                if target.startswith('data:'):
                    continue
                if target.startswith(('http:', 'https:', '//')):
                    errors.append('external frontend CSS asset: ' + target)
                    continue
                asset = (page.parent / target.lstrip('/') if target.startswith('/') else css.parent / target).resolve()
                if not asset.is_relative_to(page.parent) or not asset.is_file():
                    errors.append('missing/outside frontend CSS asset: ' + target)
    kv = root / 'vendor/kv_server'
    pinned = json.loads((kv / 'MANIFEST.json').read_text())
    if pinned.get('source_repository') != 'https://github.com/RebekahLllimx/kv-server':
        errors.append('pinned KV source repository mismatch')
    source_commit = pinned.get('source_commit')
    if not isinstance(source_commit, str) or not re.fullmatch(r'[0-9a-f]{40}', source_commit):
        errors.append('pinned KV source commit must be an immutable full SHA')
    runtime_names = {path.name for path in kv.glob('*.py') if path.is_file()}
    if set(pinned['files']) != runtime_names:
        errors.append('pinned KV file set mismatch')
    for name, wanted in pinned['files'].items():
        if not (kv / name).is_file() or digest(kv / name) != wanted:
            errors.append('pinned KV mismatch: ' + name)
    version = runpy.run_path(str(root / 'workbench/workbench/version.py'))['__version__']
    if manifest and metadata['version'] != version:
        errors.append('release version mismatch')
    for path in (root / 'workbench').glob('*.app/Contents/Info.plist'):
        data = plistlib.loads(path.read_bytes())
        if data['CFBundleShortVersionString'] != version or data['CFBundleVersion'] != version:
            errors.append('native version mismatch: ' + path.parent.parent.name)
    result = {'status': 'passed' if not errors else 'failed', 'files': len(files),
              'links': links, 'version': version, 'kv_files': len(pinned['files']),
              'kv_source_commit': source_commit, 'errors': errors}
    if errors:
        raise ValueError(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', required=True, type=Path)
    a = p.parse_args()
    print(json.dumps(validate(a.root), indent=2))
