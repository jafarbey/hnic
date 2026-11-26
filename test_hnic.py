#!/usr/bin/env python3
"""Tests for the HNIC toy runtime. Stdlib unittest only.

Run: python3 test_hnic.py

Each test builds a fresh folder with a clients/ folder, a finance/
folder, and a shared/ folder, then runs statements through the same
run_one() the real runtime uses, with the output captured.
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hnic


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class ToyTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(
            os.path.join(self.tmp.name, "root"))
        os.makedirs(os.path.join(self.root, "clients"))
        os.makedirs(os.path.join(self.root, "finance"))
        os.makedirs(os.path.join(self.root, "shared"))
        write(os.path.join(self.root, "clients", "contract.txt"),
              "contract\n")
        write(os.path.join(self.root, "clients", "contact.txt"),
              "contact\n")
        write(os.path.join(self.root, "finance", "salaries.csv"),
              "secret salary\n")
        self.log = io.StringIO()

    def tearDown(self):
        self.tmp.cleanup()

    def rules(self, text):
        path = os.path.join(self.tmp.name, "policy.hnic")
        write(path, text)
        return hnic.parse_policy(path)

    def run_line(self, line, rules, execute=True, auto_approve=False,
            approver="tester"):
        """Run one statement, capturing the printed output."""
        words = line.split()
        statement = hnic.Statement(line, words[0].upper(), words[1:])
        out = io.StringIO()
        with redirect_stdout(out):
            decision = hnic.run_one(statement, rules, self.root,
                                    self.log, execute, auto_approve,
                                    approver)
        return decision, out.getvalue()

    def test_dotdot_bypass_denied(self):
        # Problem 1: READ /clients/../finance/salaries.csv must be
        # checked against /finance, not /clients, and denied.
        rules = self.rules("ALLOW READ IN /clients\n"
                           "DENY READ IN /finance\n")
        decision, out = self.run_line(
            "READ /clients/../finance/salaries.csv", rules)
        self.assertEqual(decision, "denied")
        self.assertNotIn("secret salary", out)
        self.assertIn("denied", self.log.getvalue())

        # The same trick on a SEARCH folder is denied too.
        decision, _ = self.run_line(
            "SEARCH files IN /clients/../finance", rules)
        self.assertEqual(decision, "denied")

    def test_symlink_escape_denied(self):
        # Problem 2: a symlink inside the root that points outside
        # it must not let a read through.
        outside = os.path.join(self.tmp.name, "outside")
        os.makedirs(outside)
        write(os.path.join(outside, "secret.txt"), "outside secret\n")
        os.symlink(outside,
                   os.path.join(self.root, "clients", "link"))
        os.symlink(os.path.join(outside, "secret.txt"),
                   os.path.join(self.root, "clients", "evil.txt"))
        rules = self.rules("ALLOW READ IN /clients\n")
        decision, out = self.run_line(
            "READ /clients/link/secret.txt", rules)
        self.assertEqual(decision, "denied")
        self.assertNotIn("outside secret", out)
        decision, out = self.run_line("READ /clients/evil.txt", rules)
        self.assertEqual(decision, "denied")
        self.assertNotIn("outside secret", out)

    def test_copy_and_move_destination_checked(self):
        # Problem 3: COPY and MOVE are checked for the destination
        # folder as well as the source folder.
        rules = self.rules("ALLOW COPY IN /clients\n"
                           "DENY COPY IN /shared\n"
                           "ALLOW MOVE IN /clients\n"
                           "DENY MOVE IN /shared\n")
        decision, _ = self.run_line(
            "COPY /clients/contract.txt TO /shared", rules)
        self.assertEqual(decision, "denied")
        self.assertFalse(os.path.exists(
            os.path.join(self.root, "shared", "contract.txt")))
        decision, _ = self.run_line(
            "MOVE /clients/contact.txt TO /shared", rules)
        self.assertEqual(decision, "denied")
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "clients", "contact.txt")))

        # A destination outside the root is denied too.
        decision, _ = self.run_line(
            "COPY /clients/contract.txt TO /../outside", rules)
        self.assertEqual(decision, "denied")

    def test_auto_approve_logs_name_and_survives_failure(self):
        # Problem 4: --auto-approve records "auto-approved", never
        # "approved-by-human", and the approver's name is on the line.
        rules = self.rules("REQUIRE approval BEFORE SHARE\n")
        decision, _ = self.run_line(
            "SHARE /clients/contract.txt WITH Ada Lovelace",
            rules, auto_approve=True, approver="Ada")
        self.assertEqual(decision, "auto-approved")
        log = self.log.getvalue()
        self.assertIn("auto-approved", log)
        self.assertNotIn("approved-by-human", log)
        self.assertIn("Ada", log)

        # The log line is written BEFORE the action: even when the
        # action then fails, the record is already there.
        rules = self.rules("ALLOW COPY IN /clients\n"
                           "ALLOW COPY IN /shared\n")
        before = self.log.tell()
        with mock.patch("shutil.copy2",
                        side_effect=OSError("disk full")):
            decision, out = self.run_line(
                "COPY /clients/contract.txt TO /shared", rules)
        self.assertEqual(decision, "allowed")
        self.assertIn("Failed: disk full", out)
        logged = self.log.getvalue()[before:]
        self.assertIn("COPY /clients/contract.txt TO /shared", logged)
        self.assertIn("allowed", logged)
        self.assertFalse(os.path.exists(
            os.path.join(self.root, "shared", "contract.txt")))

    def test_interactive_approval_records_approver(self):
        # A human who says yes is recorded by name; a no is a denial.
        rules = self.rules("REQUIRE approval BEFORE SHARE\n")
        with mock.patch("builtins.input", return_value="y"):
            decision, _ = self.run_line(
                "SHARE /clients/contract.txt WITH Ada Lovelace",
                rules, approver="Ada")
        self.assertEqual(decision, "approved-by-human")
        self.assertIn("Ada", self.log.getvalue())
        with mock.patch("builtins.input", return_value="n"):
            decision, _ = self.run_line(
                "SHARE /clients/contract.txt WITH Ada Lovelace",
                rules, approver="Ada")
        self.assertEqual(decision, "denied")

    def test_lowercase_keywords_and_missing_args(self):
        # Problem 5: lowercase "with" and "to" work, and a statement
        # with missing arguments is denied, never a crash.
        rules = self.rules("REQUIRE approval BEFORE SHARE\n"
                           "ALLOW COPY IN /clients\n"
                           "ALLOW COPY IN /shared\n")
        decision, _ = self.run_line(
            "SHARE /clients/contract.txt with Ada Lovelace",
            rules, auto_approve=True, approver="Ada")
        self.assertEqual(decision, "auto-approved")
        decision, _ = self.run_line(
            "COPY /clients/contract.txt to /shared", rules)
        self.assertEqual(decision, "allowed")
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "shared", "contract.txt")))

        for line in ("SHARE /clients/contract.txt",
                     "COPY /clients/contract.txt",
                     "READ",
                     "FROBNICATE /clients/contract.txt"):
            decision, _ = self.run_line(line, rules)
            self.assertEqual(decision, "denied", line)

    def test_dry_run_changes_nothing(self):
        # Problem 6: by default the runtime changes nothing; --execute
        # is what actually performs the allowed actions.
        rules = self.rules("ALLOW COPY IN /clients\n"
                           "ALLOW COPY IN /shared\n"
                           "ALLOW DELETE IN /clients\n")
        decision, out = self.run_line(
            "COPY /clients/contract.txt TO /shared", rules,
            execute=False)
        self.assertEqual(decision, "dry-run:allowed")
        self.assertFalse(os.path.exists(
            os.path.join(self.root, "shared", "contract.txt")))
        self.assertIn("dry-run", self.log.getvalue())
        decision, _ = self.run_line(
            "DELETE /clients/contact.txt", rules, execute=False)
        self.assertEqual(decision, "dry-run:allowed")
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "clients", "contact.txt")))

        decision, _ = self.run_line(
            "COPY /clients/contract.txt TO /shared", rules,
            execute=True)
        self.assertEqual(decision, "allowed")
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "shared", "contract.txt")))
        decision, _ = self.run_line(
            "DELETE /clients/contact.txt", rules, execute=True)
        self.assertEqual(decision, "allowed")
        self.assertFalse(os.path.exists(
            os.path.join(self.root, "clients", "contact.txt")))

    def test_bad_path_denied_and_run_continues(self):
        # A bad path is denied and the run carries on to the next
        # statement, instead of aborting the whole run.
        rules = self.rules("ALLOW SEARCH IN /clients\n")
        decision, _ = self.run_line("READ /../secret.txt", rules)
        self.assertEqual(decision, "denied")
        decision, out = self.run_line("SEARCH files IN /clients", rules)
        self.assertEqual(decision, "allowed")
        self.assertIn("contract.txt", out)


if __name__ == "__main__":
    unittest.main()
