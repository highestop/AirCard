import unittest

from backend.wallet_discovery import activation_ids, card_ids, is_wallet_line, unique_records

A = "A" * 27 + "="
B = "B" * 27 + "="
C = "C" * 27 + "="


class WalletDiscoveryTests(unittest.TestCase):
    def test_requires_wallet_path_or_specific_message(self):
        self.assertEqual(card_ids(f"Wallet /Cards/{B}.cache/Preview /Cards/{A}.pkpass/en.lproj /Cards/{B}.pkcache/FrontFace"), [B, A])
        for text in (f"passd identifier {A}", "/Cards/<private>.pkpass", f"nfcd: passIDs[global]: {{(\"{A}\")}}", "/Cards/OM6NYhwXMZrAw0sRUjR62wmF4ZQ=.pkpass"):
            self.assertEqual(card_ids(text), [])
        for text, expected in ((f"Wallet /Passes/Cards/{A}/FrontFace", A), (f"PDCardFileManager: writing card {B}", B),
                               (f"PDPassLibrary: wrote pass {A}", A), (f"VerificationCheck.{C}", C), (f"updated selected pass uniqueID: {B}", B)):
            self.assertEqual(card_ids(text), [expected])

    def test_session_lists_only_preserve_order(self):
        cards = [f"{index:020d}AAAAAAA=" for index in range(12)]
        self.assertEqual(card_ids('nfcd: passIDs[InSession]: {(' + ', '.join(f'"{card}"' for card in cards) + f')}} passIDs[global]: {{("{A}")}}'), cards)

    def test_activation_case_and_multiline(self):
        activation = "A00000000310100100000020"
        self.assertEqual(activation_ids(f"setActivePaymentApplet: x requestedApplet:\n<NFApplet> {{ identifier = {activation.lower()} family=0x0 }}"), [activation])

    def test_dashboard_event_forms_preserve_order_and_wallet_context(self):
        lines = [
            f"Passbook(PassKitUI): Dashboard loading (...): for {A}, pass feature unknown",
            f"Passbook(PassKitUI): Dashboard loading (...): {B} - m:NO, sm:NO",
            f"Wallet: dashboard\tLOADING (...): for {C}",
        ]
        for line, expected in zip(lines, [A, B, C]):
            with self.subTest(line=line):
                self.assertTrue(is_wallet_line(line))
                self.assertEqual(card_ids(line), [expected])
        combined = f"Wallet /Cards/{B}.cache "+lines[0]+f" Dashboard loading: {B} - repeated"
        self.assertEqual(card_ids(combined), [B, A])

    def test_dashboard_rejects_unrelated_events_and_invalid_or_partial_ids(self):
        invalid = [
            f"Dashboard loading (...): for {A}",
            f"Browser: Dashboard loading (...): for {A}",
            f"NotWallet: Dashboard loading (...): for {A}",
            f"Wallet: unrelated event\nBrowser: Dashboard loading (...): for {A}",
            f"Wallet Dashboard loading unrelated\nOther event: for {A}",
            f"Wallet Dashboard loading: for ../{A}",
            f"Wallet Dashboard loading: for {A}/extra",
            "Wallet Dashboard loading: for <private>",
            "Wallet Dashboard loading: for OM6NYhwXMZrAw0sRUjR62wmF4ZQ=",
        ]
        for identifier in ("A" * 19, "A" * 65, "A" * 80, A + "X" * 40):
            invalid.extend([f"Wallet Dashboard loading: for {identifier}",
                            f"Passbook Dashboard loading: {identifier} - m:NO"])
        for line in invalid:
            with self.subTest(line=line):
                self.assertEqual(card_ids(line), [])
        for identifier in ("A" * 20, "A" * 64):
            self.assertEqual(card_ids(f"Wallet Dashboard loading: for {identifier}"), [identifier])

    def test_json_activation_keeps_context_and_normalizes_multiline_hex(self):
        activation = "A00000000310100100000020"
        prefix = "nfcd: setActivePaymentApplet: x requestedApplet: "
        line = prefix + '{"identifier":"' + activation.lower() + '","family":0}'
        self.assertEqual(activation_ids(line), [activation])
        multiline = prefix + '{\n "identifier" : "' + activation + '"\n}'
        self.assertEqual(activation_ids(multiline), [activation])
        self.assertEqual(activation_ids(line + "\n" + multiline), [activation])

    def test_activation_rejects_incomplete_nonhex_overlong_and_unrelated_values(self):
        activation = "A00000000310100100000020"
        prefix = "nfcd: setActivePaymentApplet: requestedApplet: "
        invalid = [
            '{"identifier":"' + activation + '"}',
            'nfcd: requestedApplet: {"identifier":"' + activation + '"}',
            'nfcd: setActivePaymentApplet: otherApplet: {"identifier":"' + activation + '"}',
            prefix + '{"otheridentifier":"' + activation + '"}',
            prefix + '{"identifier":"' + activation,
            prefix + '{"identifier":"' + activation + '-extra"}',
            prefix + '{"identifier":"' + activation + '"suffix}',
            prefix + '{"identifier":"' + 'A' * 65 + '"}',
            prefix + '{"identifier":"' + 'A' * 9 + '"}',
            prefix + '{"identifier":"' + activation + 'Z"}',
            prefix + 'identifier = ' + activation + '-extra',
        ]
        for line in invalid:
            with self.subTest(line=line):
                self.assertEqual(activation_ids(line), [])

    def test_dedup_preserves_identity_selection_artwork(self):
        rows = unique_records([{"id": B, "confirmed": True, "imagePath": "/skin-b.png", "selected": False},
                               {"id": A, "imagePath": "/skin-a.png"}, {"id": B}, {"id": A, "confirmed": True}])
        self.assertEqual([row["id"] for row in rows], [B, A])
        self.assertFalse(rows[0]["selected"])
        self.assertTrue(rows[1]["confirmed"])
        self.assertEqual(rows[0]["imagePath"], "/skin-b.png")
        self.assertEqual(unique_records([{"id": "../../bad"}]), [])
