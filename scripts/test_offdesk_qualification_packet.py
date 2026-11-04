"""Bounded source packet integrity and inline-delivery regressions."""
import hashlib
import json
import os
import copy
from pathlib import Path
import tempfile
import shutil
import unittest

import offdesk_qualification_packet as packets


class PacketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='forager-packet-',
            dir=os.environ.get('FORAGER_TEST_TEMP_ROOT', '/var/tmp'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'scripts').mkdir()
        (self.root / '.runtime').mkdir()
        self.source = 'scripts/source.py'
        self.contract = '.runtime/contract.json'
        self.checker = '.runtime/check.py'
        (self.root / self.source).write_text('def original():\n    return 1\n')
        (self.root / self.contract).write_text(json.dumps({
            'write_paths': [self.source], 'operator_implementation_patch_supplied': False,
            'requirements': ['Keep bytes and privacy unchanged.']}))
        (self.root / self.checker).write_text('class Cases:\n' + ''.join(
            f"    def test_{i:02d}(self): self.expected('fixture-{i}', b'', {i % 3}, {bool(i % 2)})\n"
            for i in range(12)))

    def build(self, ranges=None):
        return packets.build_packet(self.root, self.source, self.contract, self.checker,
                                    ranges if ranges is not None else [(1, 2)])

    def store(self, packet):
        raw = packets.packet_bytes(packet)
        (self.root / '.runtime/packet.json').write_bytes(raw)
        return {'path': '.runtime/packet.json', 'sha256': hashlib.sha256(raw).hexdigest()}

    def test_source_and_case_expectations_are_original_and_deterministic(self):
        packet = self.build()
        self.assertEqual(packets.packet_bytes(packet), packets.packet_bytes(self.build()))
        self.assertEqual(packet['excerpts'][0]['text'], (self.root / self.source).read_text())
        self.assertEqual(packet['cases'][11]['frames'], 2)
        self.assertTrue(packet['cases'][11]['terminal'])
        actual, text = packets.load_packet(self.root, self.store(packet))
        self.assertEqual(actual, packet)
        self.assertIn('def original():', text)
        self.assertIn('Case test_11:', text)
        for case in packet['cases']:
            self.assertIn(case['fixture'].lstrip(), text)
        self.assertFalse(packet['implementation_patch_supplied'])
        self.assertFalse(packet['execution_authority'])

    def test_stale_source_contract_or_checker_refuses_delivery(self):
        for relative in (self.source, self.contract, self.checker):
            with self.subTest(relative=relative):
                selected = self.store(self.build())
                path = self.root / relative
                original = path.read_bytes()
                path.write_bytes(original + b'\n')
                with self.assertRaisesRegex(ValueError, 'differs'):
                    packets.load_packet(self.root, selected)
                path.write_bytes(original)

    def test_modified_packet_rejected_even_with_recomputed_packet_hash(self):
        for field in ('requirements', 'excerpts', 'cases', 'bindings'):
            with self.subTest(field=field):
                packet = self.build()
                if field == 'requirements': packet[field][0] = 'forged requirement'
                if field == 'excerpts': packet[field][0]['text'] = 'operator decoder patch'
                if field == 'cases': packet[field][0]['frames'] = 999
                if field == 'bindings': packet[field][self.source] = 'forged'
                with self.assertRaisesRegex(ValueError, 'differs'):
                    packets.load_packet(self.root, self.store(packet))

    def test_changed_packet_hash_rejected_before_delivery(self):
        selected = self.store(self.build())
        (self.root / selected['path']).write_text('{}')
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            packets.load_packet(self.root, selected)

    def test_path_escape_symlink_directory_and_hardlink_refused(self):
        for relative in ('../outside', str(self.root / self.source), '.runtime', None):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                packets.selected_file(self.root, relative)
        (self.root / 'linked').symlink_to(self.root / 'scripts', target_is_directory=True)
        with self.assertRaises(ValueError): packets.selected_file(self.root, 'linked/source.py')
        os.link(self.root / self.source, self.root / 'extra-link')
        with self.assertRaises(ValueError): self.build()

    def test_ranges_cannot_omit_requested_lines_silently(self):
        for ranges in ([], [(0, 1)], [(1, 3)], [(True, 2)], [(1, 2)] * 5):
            with self.subTest(ranges=ranges), self.assertRaises(ValueError):
                self.build(ranges)
        (self.root / self.source).write_text('pass\n' * 61)
        with self.assertRaises(ValueError): self.build([(1, 61)])

    def test_packet_and_inline_limits_fail_without_truncating(self):
        (self.root / self.source).write_text('x' * 20000 + '\n')
        with self.assertRaisesRegex(ValueError, 'byte bound'): self.build([(1, 1)])
        (self.root / self.source).write_text('pass\n')
        packet = self.build([(1, 1)])
        packet['requirements'] = ['z' * packets.MAX_INLINE_BYTES]
        with self.assertRaisesRegex(ValueError, 'prompt byte bound'): packets.render_packet(packet)

    def test_missing_case_or_nonliteral_expectation_refused(self):
        path = self.root / self.checker
        original = path.read_text()
        path.write_text(original.replace('def test_11', 'def disabled_11'))
        with self.assertRaisesRegex(ValueError, 'twelve'): self.build()
        path.write_text(original.replace("self.expected('fixture-0', b'', 0, False)",
                                         "self.expected('fixture-0', b'', unknown, False)"))
        with self.assertRaises(ValueError): self.build()


class HelperPacketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='forager-helper-packet-',
            dir=os.environ.get('FORAGER_TEST_TEMP_ROOT', '/var/tmp'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'scripts').mkdir()
        (self.root / '.runtime').mkdir()
        fixtures = Path(__file__).resolve().parent.parent / 'tests/fixtures/stream_helper'
        for name in ('contract.json', 'HELPER_VECTORS.json', 'check_stream_helper.py'):
            shutil.copyfile(fixtures / name, self.root / '.runtime' / name)
        self.contract = '.runtime/contract.json'
        self.checker = '.runtime/check_stream_helper.py'
        self.vectors = '.runtime/HELPER_VECTORS.json'

    def build(self):
        return packets.build_helper_packet(self.root, self.contract, self.checker, self.vectors)

    def store(self, packet):
        raw = packets.packet_bytes(packet)
        (self.root / '.runtime/packet.json').write_bytes(raw)
        return {'path': '.runtime/packet.json', 'sha256': hashlib.sha256(raw).hexdigest()}

    def test_all_literal_vectors_expand_exactly_with_no_truncation_or_decoder(self):
        packet = self.build()
        original = json.loads((self.root / self.vectors).read_text())

        def expand(parts):
            return b''.join(bytes.fromhex(part) if isinstance(part, str)
                            else bytes.fromhex(part[1]) * part[0] for part in parts)

        for row, case in zip(packet['cases'], original['cases'], strict=True):
            self.assertEqual(expand(row[3]), bytes.fromhex(case['payload_hex']))
            for index, field in ((4, 'expected_frames_hex'), (5, 'expected_before_finish_hex')):
                self.assertEqual([expand(parts) for parts in row[index]], [bytes.fromhex(x) for x in case[field]])
        actual, inline = packets.load_packet(self.root, self.store(packet))
        self.assertEqual(actual, packet)
        self.assertEqual(packet, self.build())
        self.assertLessEqual(len(inline.encode()), packets.MAX_INLINE_BYTES)
        for row in packet['cases']:
            self.assertIn(json.dumps(row, separators=(',', ':')), inline)
        self.assertFalse((self.root / packets.HELPER_TARGET).exists())
        self.assertIn('helper pass stays pending review', inline)
        self.assertIn('all twelve HTTP cases', inline)
        self.assertFalse(packet['implementation_patch_supplied'])
        self.assertFalse(packet['execution_authority'])

    def test_existing_target_and_dangling_symlink_are_not_absent(self):
        target = self.root / packets.HELPER_TARGET
        target.write_text('pass\n')
        with self.assertRaisesRegex(ValueError, 'absent target'): self.build()
        target.unlink()
        target.symlink_to(self.root / 'missing')
        with self.assertRaisesRegex(ValueError, 'absent target'): self.build()

    def test_changed_input_is_not_trusted_after_packet_hash_is_recomputed(self):
        for name in (self.contract, self.checker, self.vectors):
            with self.subTest(name=name):
                selection = self.store(self.build())
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b'\n')
                with self.assertRaisesRegex(ValueError, 'differs'):
                    packets.load_packet(self.root, selection)
                path.write_bytes(original)

    def test_forged_cases_api_absence_and_scope_refused(self):
        for field in ('cases', 'api', 'absent_paths', 'write_paths', 'implementation_stage'):
            with self.subTest(field=field):
                packet = self.build()
                packet[field] = [] if field != 'implementation_stage' else 'stream_relay.v1'
                with self.assertRaisesRegex(ValueError, 'differs'):
                    packets.load_packet(self.root, self.store(packet))

    def test_scope_authority_or_host_report_cannot_be_changed_implicitly(self):
        path = self.root / self.contract
        original = json.loads(path.read_text())
        for field, value in [('write_paths', [packets.HELPER_TARGET, 'scripts/offdesk_request_telemetry.py']),
                             ('launch_authorized', True), ('qualification_complete', True),
                             ('scope', []), ('api', None), ('schema', 'unknown')]:
            with self.subTest(field=field):
                modified = copy.deepcopy(original)
                modified[field] = value
                path.write_text(json.dumps(modified))
                with self.assertRaises(ValueError): self.build()
        modified = copy.deepcopy(original)
        modified['checks']['host_report_path'] = '.night/helper-model-check.json'
        path.write_text(json.dumps(modified))
        with self.assertRaisesRegex(ValueError, 'separate host'): self.build()

    def test_incomplete_duplicate_or_malformed_vector_sets_refused(self):
        original = json.loads((self.root / self.vectors).read_text())
        variants = []
        changed = copy.deepcopy(original); changed['cases'].pop(); variants.append(changed)
        changed = copy.deepcopy(original); changed['cases'][1]['id'] = changed['cases'][0]['id']; variants.append(changed)
        changed = copy.deepcopy(original); changed['cases'][0]['payload_hex'] = 'AA'; variants.append(changed)
        changed = copy.deepcopy(original); changed['cases'][0]['max_bytes'] = True; variants.append(changed)
        changed = copy.deepcopy(original); changed['cases'][0]['expected_drops'] = -1; variants.append(changed)
        changed = copy.deepcopy(original); changed['cases'][0]['expected_before_finish_hex'] = ['ffff']; variants.append(changed)
        changed = copy.deepcopy(original); changed['cases'][0] = None; variants.append(changed)
        changed = copy.deepcopy(original); changed['expected_tests'] = 0; variants.append(changed)
        for variant in variants:
            with self.subTest(variant=variants.index(variant)), self.assertRaises(ValueError):
                packets.helper_cases(variant)

    def test_checker_default_vectors_must_be_exact_sibling(self):
        for checker, vectors in [(self.checker, '.runtime/elsewhere.json'),
                                  ('.runtime/other.py', self.vectors)]:
            shutil.copyfile(self.root / self.checker, self.root / '.runtime/other.py')
            shutil.copyfile(self.root / self.vectors, self.root / '.runtime/elsewhere.json')
            with self.assertRaisesRegex(ValueError, 'selected together'):
                packets.build_helper_packet(self.root, self.contract, checker, vectors)

    def test_symlinked_or_hardlinked_immutable_inputs_refused(self):
        os.link(self.root / self.vectors, self.root / '.runtime/extra-link.json')
        with self.assertRaisesRegex(ValueError, 'regular-file'): self.build()
        (self.root / '.runtime/extra-link.json').unlink()
        path = self.root / self.checker
        raw = path.read_bytes()
        path.unlink()
        path.symlink_to(self.root / 'elsewhere')
        with self.assertRaisesRegex(ValueError, 'regular relative'): self.build()
        path.unlink()
        path.write_bytes(raw)


if __name__ == '__main__':
    unittest.main(verbosity=2)
