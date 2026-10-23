"""The license notice travels with the framework, and the license text stays canonical (G052).

`governance/NOTICE` goes with every copy of the framework folder, an upgrade and an extension copied on its own, so it
must carry the Required Notice line and the license's name and URL. When the root LICENSE is the PolyForm Noncommercial
text, as in the template, its text beneath the notice lines must be the canonical one byte for byte (pinned by its
SHA-256, so the repository keeps no second copy), and its Required Notice lines must equal the framework notice's. A
project that keeps a LICENSE of its own for its own work is not checked against the pin."""
import hashlib
import unittest

import repo_paths as rp

NOTICE = 'governance/NOTICE'
LICENSE = 'LICENSE'
PREFIX = 'Required Notice: '
NAME = 'PolyForm Noncommercial License 1.0.0'
URL = 'https://polyformproject.org/licenses/noncommercial/1.0.0'
HEADING = '# ' + NAME
# SHA-256 of the canonical text (LF line ends) as the PolyForm Project publishes it, read on 2026-10-20.
CANONICAL_SHA256 = 'c0ea4a896d2c8c394b29f9427589996db826cd501c512279ff0ed3ef48fabbe5'


def text(relative):
    return (rp.ROOT / relative).read_bytes().decode('utf-8').replace('\r\n', '\n')


def notice_lines(content):
    return [line for line in content.split('\n') if line.startswith(PREFIX)]


def split_license(content):
    """(the Required Notice lines above the text, the license text from its heading on)."""
    head, found, rest = content.partition(HEADING)
    return notice_lines(head), (found + rest) if found else None


class LicenseNoticeTest(unittest.TestCase):
    def test_the_framework_notice_names_the_license_and_carries_a_required_notice(self):
        content = text(NOTICE)
        self.assertTrue(content.startswith(PREFIX), 'the first line of %s is the Required Notice line' % NOTICE)
        self.assertGreater(len(notice_lines(content)[0]), len(PREFIX))
        self.assertIn(NAME, content)
        self.assertIn(URL, content)

    def test_a_polyform_noncommercial_license_is_canonical_and_carries_the_same_notice(self):
        if not (rp.ROOT / LICENSE).is_file():
            self.skipTest('no root LICENSE')
        notices, body = split_license(text(LICENSE))
        if body is None:
            self.skipTest('the root LICENSE is not the PolyForm Noncommercial text')
        self.assertEqual(hashlib.sha256(body.encode('utf-8')).hexdigest(), CANONICAL_SHA256,
                         'the license text beneath the Required Notice line differs from the canonical text')
        self.assertEqual(notices, notice_lines(text(NOTICE)),
                         'the Required Notice lines of LICENSE and %s differ' % NOTICE)

    def test_the_split_finds_the_notice_and_refuses_another_text(self):
        notices, body = split_license('Required Notice: Copyright (c) Someone\n\n' + HEADING + '\n\nterms\n')
        self.assertEqual(notices, ['Required Notice: Copyright (c) Someone'])
        self.assertTrue(body.startswith(HEADING))
        self.assertEqual(split_license('MIT License\n\nterms\n'), ([], None))


if __name__ == '__main__':
    unittest.main()
