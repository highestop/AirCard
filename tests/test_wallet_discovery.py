import unittest

from wallet_discovery import activation_ids, card_ids, unique_records

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

    def test_dedup_preserves_identity_selection_artwork(self):
        rows = unique_records([{"id": B, "confirmed": True, "imagePath": "/skin-b.png", "selected": False},
                               {"id": A, "imagePath": "/skin-a.png"}, {"id": B}, {"id": A, "confirmed": True}])
        self.assertEqual([row["id"] for row in rows], [B, A])
        self.assertFalse(rows[0]["selected"])
        self.assertTrue(rows[1]["confirmed"])
        self.assertEqual(rows[0]["imagePath"], "/skin-b.png")
        self.assertEqual(unique_records([{"id": "../../bad"}]), [])
