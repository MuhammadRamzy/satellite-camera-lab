import unittest
from foundations.control import (SpacecraftAuthority, SC_STANDBY, SC_OPERATIONAL,
                                 SC_SAFE, SC_COMMUNICATIONS, READY, STANDBY, SAFE, FAULT)


class AuthorityTests(unittest.TestCase):
    def test_boot_inhibited_and_validated_update(self):
        authority = SpacecraftAuthority()
        self.assertFalse(authority.may_image)
        with self.assertRaises(ValueError):
            authority.guard_payload(READY)
        for mode in (SC_STANDBY, SC_SAFE, SC_COMMUNICATIONS, 99):
            with self.assertRaises(ValueError):
                authority.update(mode, True)
        self.assertEqual(authority.mode, SC_STANDBY)

    def test_safe_cannot_be_overridden_by_local_recovery(self):
        authority = SpacecraftAuthority()
        authority.update(SC_SAFE, False)
        for target in (STANDBY, READY):
            with self.assertRaises(ValueError):
                authority.guard_payload(target)
        authority.guard_payload(SAFE)
        authority.update(SC_OPERATIONAL, True)
        authority.guard_payload(STANDBY)
        authority.guard_payload(READY)

    def test_permission_revocation_and_internal_modes(self):
        authority = SpacecraftAuthority()
        authority.update(SC_OPERATIONAL, True)
        self.assertTrue(authority.may_image)
        authority.update(SC_OPERATIONAL, False)
        self.assertFalse(authority.may_image)
        with self.assertRaises(ValueError):
            authority.guard_payload(READY)
        with self.assertRaises(ValueError):
            authority.guard_payload(FAULT)
