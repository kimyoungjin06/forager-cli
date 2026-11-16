#!/usr/bin/env python3
"""Check the assembled static website without requesting external resources."""
import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.ids, self.links = set(), []
        self.feed(text)

    def handle_starttag(self, tag, pairs):
        attrs = dict(pairs)
        if attrs.get('id'):
            self.ids.add(attrs['id'])
        if tag == 'a' and attrs.get('name'):
            self.ids.add(attrs['name'])
        key = 'src' if tag in {'img', 'script', 'iframe', 'source'} else 'href' if tag in {'a', 'link'} else None
        if key and attrs.get(key):
            self.links.append(attrs[key])


def audit_site(directory):
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ValueError('Build the combined site before checking links.')
    pages = {path.resolve(): Page(path.read_text()) for path in root.rglob('*.html')}
    if not pages:
        raise ValueError('The output directory contains no HTML pages.')
    errors, checked = [], 0
    for page, parsed in pages.items():
        for href in parsed.links:
            url = urlsplit(href)
            if url.scheme and url.scheme not in {'http', 'https'}:
                continue
            if url.netloc and url.netloc != 'kimyoungjin06.github.io':
                continue
            path = unquote(url.path)
            if path.startswith('/forager-cli/'):
                target = root / path[len('/forager-cli/'):]
            elif path.startswith('/'):
                errors.append({'page': str(page.relative_to(root)), 'href': href, 'reason': 'outside_site_base'})
                continue
            else:
                target = page.parent / path if path else page
            target = target.resolve()
            if not target.is_relative_to(root):
                errors.append({'page': str(page.relative_to(root)), 'href': href, 'reason': 'outside_output'})
                continue
            if target.is_dir():
                target /= 'index.html'
            checked += 1
            if not target.is_file():
                errors.append({'page': str(page.relative_to(root)), 'href': href, 'reason': 'missing_file'})
            elif url.fragment and target.suffix == '.html' and unquote(url.fragment) not in pages[target].ids:
                errors.append({'page': str(page.relative_to(root)), 'href': href, 'reason': 'missing_anchor'})
    return {'html_pages': len(pages), 'local_references_checked': checked, 'errors': errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', nargs='?', default='dist', type=Path)
    args = parser.parse_args()
    try:
        report = audit_site(args.directory)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2))
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
