import json
import plistlib
import tempfile
import unittest
from pathlib import Path

from backend.wallet_catalog import build_catalog, decode_archive

A = 'A' * 27 + '='
B = 'B' * 27 + '='
C = 'C' * 27 + '='


def device(ids, model='iPhone16,1'):
    return {'modelIdentifier': model, 'remotePaymentInstruments': [
        {'passID': i, 'displayName': 'Same bank', 'primaryAccountIdentifier': 'must-not-export',
         'primaryPaymentApplication': {'applicationIdentifier': 'A0000000000000000' + str(n)}}
        for n, i in enumerate(ids)
    ]}


def write_archive(root, devices):
    data = {'$objects': ['$null', {'NS.objects': [plistlib.UID(i + 2) for i in range(len(devices))]}] + devices,
            '$top': {'root': plistlib.UID(1)}}
    (root / 'RemoteDevices.archive').write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_BINARY))


class WalletCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'Cards').mkdir()

    def test_exact_id_matches_device_and_deduplicates_without_merging_names(self):
        write_archive(self.root, [device([A, A, B]), device([C])])
        result = build_catalog(self.root, [A], 'iPhone16,1')
        self.assertEqual(result['paymentStatus'], 'matched')
        self.assertEqual([c['id'] for c in result['payments']], [A, B])
        self.assertEqual([c['name'] for c in result['payments']], ['Same bank', 'Same bank'])
        self.assertEqual(result['payments'][0]['activationID'], 'A00000000000000000')
        self.assertNotIn('must-not-export', json.dumps(result))

    def test_unique_matching_model_can_bootstrap_activation_mapping(self):
        write_archive(self.root, [device([A])])
        result = build_catalog(self.root, [], 'iPhone16,1')
        self.assertEqual(result['paymentStatus'], 'matched')
        self.assertEqual([c['id'] for c in result['payments']], [A])

    def test_multiple_matching_models_without_overlap_are_ambiguous(self):
        write_archive(self.root, [device([A]), device([B])])
        result = build_catalog(self.root, [], 'iPhone16,1')
        self.assertEqual(result['paymentStatus'], 'ambiguous')
        self.assertEqual(result['payments'], [])

    def test_wrong_device_model_does_not_match(self):
        write_archive(self.root, [device([A], 'iPhone17,1')])
        self.assertEqual(build_catalog(self.root, [A], 'iPhone16,1')['payments'], [])

    def test_shared_card_on_two_cached_devices_is_ambiguous(self):
        write_archive(self.root, [device([A, B]), device([A, C])])
        result = build_catalog(self.root, [A], 'iPhone16,1')
        self.assertEqual(result['paymentStatus'], 'ambiguous')
        self.assertEqual(result['payments'], [])

    def test_membership_is_a_separate_mac_candidate(self):
        folder = self.root / 'Cards' / (C + '.pkpass')
        folder.mkdir()
        (folder / 'pass.json').write_text(json.dumps({'generic': {}, 'organizationName': 'Example Air', 'description': 'Membership', 'authenticationToken': 'secret'}))
        result = build_catalog(self.root, [A], 'iPhone16,1')
        self.assertEqual(result['memberships'], [{'id': C, 'name': 'Example Air · Membership', 'source': 'membership'}])
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(result['payments'], [])
        self.assertTrue(result['warnings'])

    def test_corrupt_cache_keeps_diagnostics_available(self):
        (self.root / 'RemoteDevices.archive').write_bytes(b'bad plist')
        result = build_catalog(self.root, [A], 'iPhone16,1')
        self.assertEqual(result['paymentStatus'], 'unavailable')
        self.assertTrue(result['warnings'])

    def test_membership_symlink_is_not_followed(self):
        outside = self.root / 'private.json'
        outside.write_text(json.dumps({'generic': {}, 'description': 'Not a pass file'}))
        folder = self.root / 'Cards' / (C + '.pkpass')
        folder.mkdir()
        (folder / 'pass.json').symlink_to(outside)
        self.assertEqual(build_catalog(self.root, [], '')['memberships'], [])

    def test_archive_cycles_and_invalid_references_fail(self):
        for ref in [1, 999]:
            data = {'$objects': ['$null', {'NS.objects': [plistlib.UID(ref)]}], '$top': {'root': plistlib.UID(1)}}
            with self.assertRaises(ValueError):
                decode_archive(plistlib.dumps(data, fmt=plistlib.FMT_BINARY))

    def test_missing_cache_is_not_reported_as_zero_phone_cards(self):
        result = build_catalog(self.root, [], 'iPhone16,1')
        self.assertEqual(result['paymentStatus'], 'unavailable')
        self.assertNotIn('phoneTotal', result)
        self.assertTrue(result['warnings'])


if __name__ == '__main__':
    unittest.main()
