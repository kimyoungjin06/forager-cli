"""Publication checks catch broken routes, assets and anchors in static output."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from check_site_links import audit_site


class SiteLinks(unittest.TestCase):
    def setUp(self):
        owned = tempfile.TemporaryDirectory()
        self.addCleanup(owned.cleanup)
        self.root = Path(owned.name)

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_combined_book_routes_assets_and_anchors(self):
        self.put('index.html', '<a href="/forager-cli/docs/">Docs</a>')
        self.put('docs/index.html', '<h1 id="start">Start</h1><a href="#start">Start</a><img src="../assets/logo.svg">')
        self.put('assets/logo.svg', '<svg/>')
        report = audit_site(self.root)
        self.assertEqual(report['html_pages'], 2)
        self.assertEqual(report['local_references_checked'], 3)
        self.assertEqual(report['errors'], [])

    def test_missing_page_and_anchor_are_failures(self):
        self.put('index.html', '<a href="missing.html">Missing</a><a href="#absent">Anchor</a>')
        self.assertEqual({row['reason'] for row in audit_site(self.root)['errors']}, {'missing_file', 'missing_anchor'})

    def test_encoded_paths_query_and_same_site_absolute_url(self):
        self.put('index.html', '<a href="https://kimyoungjin06.github.io/forager-cli/docs/a%20b.html?q=1#hello%20world">Read</a>')
        self.put('docs/a b.html', '<h1 id="hello world">Hello</h1>')
        self.assertEqual(audit_site(self.root)['errors'], [])

    def test_external_resources_are_not_contacted_or_certified(self):
        self.put('index.html', '<a href="https://example.invalid/missing">External</a><a href="mailto:example@example.invalid">Mail</a>')
        self.assertEqual(audit_site(self.root)['local_references_checked'], 0)
        self.assertEqual(audit_site(self.root)['errors'], [])

    def test_bad_site_base_and_output_escape_are_failures(self):
        self.put('index.html', '<a href="/docs/">Wrong base</a><a href="../private.txt">Outside</a>')
        self.assertEqual({row['reason'] for row in audit_site(self.root)['errors']}, {'outside_site_base', 'outside_output'})

    def test_missing_or_empty_build_is_not_success(self):
        for directory in [self.root, self.root / 'absent']:
            with self.assertRaises(ValueError):
                audit_site(directory)


if __name__ == '__main__':
    unittest.main()
