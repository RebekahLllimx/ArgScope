"""Release-boundary regression tests; no model calls or user data."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
with patch.object(sys, 'path', [str(ROOT / 'workbench/tools'), *sys.path]):
    import build_review_mvp as build_tool
    from check_release import validate
    from verify_release import extract


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.base = Path(cls.temp.name)
        cls.release = cls.base / 'baseline'
        cls.built = build_tool.build(cls.release)

    def copy(self, name):
        return Path(shutil.copytree(self.release, self.base / name))

    def test_archive_is_complete_and_launchers_keep_execute_permission(self):
        extracted = extract(self.built['zip'], self.base / 'extracted')
        self.assertEqual(validate(extracted)['status'], 'passed')
        self.assertTrue((extracted / 'workbench/Launch Codex.command').stat().st_mode & 0o111)
        for name in ['vendor/kv_server/aof_lock.py', 'workbench/docs/MVP_GUIDE.md', 'sourcelens/web/dist/index.html', '.github/workflows/tests.yml']:
            self.assertTrue((extracted / name).is_file())
        self.assertFalse((extracted / '.local').exists())

    def test_operator_notes_are_not_release_members(self):
        self.assertFalse((self.release / 'AGENTS.md').exists())
        for name in build_tool.PRIVATE_DOCS:
            self.assertFalse((self.release / name).exists(), name)
        self.assertEqual(
            {p.relative_to(self.release).as_posix() for p in (self.release / 'workbench/docs').glob('*.md')},
            build_tool.PUBLIC_DOCS,
        )

    def test_modified_or_unlisted_file_is_rejected(self):
        candidate = self.copy('tampered')
        (candidate / 'extra.md').write_text('unexpected')
        with self.assertRaisesRegex(ValueError, 'file set'):
            validate(candidate)
        (candidate / 'extra.md').unlink()
        (candidate / 'vendor/kv_server/store.py').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'KV mismatch'):
            validate(candidate)

    def test_missing_ui_leaves_no_partial_output(self):
        candidate = self.copy('missing-ui')
        (candidate / 'sourcelens/web/dist/index.html').unlink()
        out = self.base / 'failed-build'
        with self.assertRaisesRegex(ValueError, 'both frontends'):
            build_tool.build(out, candidate)
        self.assertFalse(out.exists())
        self.assertFalse(out.with_suffix('.zip').exists())

    def test_unpinned_runtime_module_is_rejected(self):
        candidate = self.copy('unpinned-runtime')
        (candidate / 'vendor/kv_server/unpinned.py').write_text('VALUE = 1\n')
        with self.assertRaisesRegex(ValueError, 'pinned KV file set mismatch'):
            validate(candidate, manifest=False)

    def test_upstream_identity_requires_repository_and_full_commit(self):
        candidate = self.copy('upstream-identity')
        path = candidate / 'vendor/kv_server/MANIFEST.json'
        pinned = json.loads(path.read_text())
        for field, value, error in [
            ('source_repository', None, 'source repository mismatch'),
            ('source_repository', 'https://example.com/other', 'source repository mismatch'),
            ('source_commit', None, 'immutable full SHA'),
            ('source_commit', 'main', 'immutable full SHA'),
            ('source_commit', '415f5a6', 'immutable full SHA'),
        ]:
            with self.subTest(field=field, value=value):
                path.write_text(json.dumps({**pinned, field: value}))
                with self.assertRaisesRegex(ValueError, error):
                    validate(candidate, manifest=False)

    def test_missing_bundled_math_font_is_rejected(self):
        candidate = self.copy('missing-math-font')
        font = next((candidate / 'workbench/workbench/review_web/review-assets').glob('*.woff2'))
        font.unlink()
        with self.assertRaisesRegex(ValueError, 'frontend CSS asset'):
            validate(candidate, manifest=False)

    def test_private_or_symlinked_curated_input_is_rejected(self):
        candidate = self.copy('private-input')
        path = candidate / 'workbench/docs/private.env'
        path.write_text('not a real credential')
        with self.assertRaisesRegex(ValueError, 'Unexpected curated'):
            build_tool.build(self.base / 'reject-private', candidate)
        path.unlink()
        path.symlink_to(candidate / 'README.md')
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            build_tool.build(self.base / 'reject-link', candidate)

    def test_broken_document_link_is_rejected_before_publication(self):
        candidate = self.copy('broken-link')
        p = candidate / 'README.md'
        p.write_text(p.read_text() + '\n[missing](missing.md)\n')
        out = self.base / 'reject-docs'
        with self.assertRaisesRegex(ValueError, 'missing/outside link'):
            build_tool.build(out, candidate)
        self.assertFalse(out.exists())

    def test_build_is_repeatable_and_never_overwrites(self):
        a = build_tool.build(self.base / 'one/ArgScope', self.release)
        b = build_tool.build(self.base / 'two/ArgScope', self.release)
        self.assertEqual(a['zip_sha256'], b['zip_sha256'])
        with self.assertRaisesRegex(ValueError, 'new delivery'):
            build_tool.build(self.base / 'one/ArgScope', self.release)

    def test_public_document_language_is_checked(self):
        candidate = self.copy('document-language')
        p = candidate / 'README.md'
        p.write_text(p.read_text() + '\n\u79c1\u4eba\u8ba8\u8bba\n')
        with self.assertRaisesRegex(ValueError, 'non-English'):
            validate(candidate, manifest=False)

    def test_archive_path_traversal_is_rejected(self):
        archive = self.base / 'bad.zip'
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('../escape', 'invalid')
        with self.assertRaisesRegex(ValueError, 'archive path'):
            extract(archive, self.base / 'bad-extracted')
        self.assertFalse((self.base / 'escape').exists())
