#!/usr/bin/env python3
"""Tests for the HNIC toy runtime. Stdlib unittest only.

Run either way:

    python3 test_hnic.py
    python3 -m unittest test_hnic -v

Each test builds a fresh folder with a clients/ folder, a finance/
folder, and a shared/ folder, then runs statements through the same
run_one() the real runtime uses, with the output captured.
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hnic

HNIC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "hnic.py")


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
        """Run one statement, capturing the printed output. The line
        goes through the real script parser, so quoted paths and
        malformed lines are exercised exactly as the runtime sees
        them."""
        path = os.path.join(self.tmp.name, "script.hnic")
        write(path, line + "\n")
        statements = hnic.parse_script(path)
        self.assertEqual(len(statements), 1, line)
        out = io.StringIO()
        with redirect_stdout(out):
            decision = hnic.run_one(statements[0], rules, self.root,
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

    def test_auto_approve_logs_auto_and_survives_failure(self):
        # Problem 4: --auto-approve records "auto-approved", never
        # "approved-by-human", and the approver column says "auto" —
        # no person's name is attached to an approval nobody gave.
        rules = self.rules("REQUIRE approval BEFORE SHARE\n"
                           "ALLOW SHARE IN /clients\n")
        decision, _ = self.run_line(
            "SHARE /clients/contract.txt WITH Ada Lovelace",
            rules, auto_approve=True, approver="Ada")
        self.assertEqual(decision, "auto-approved")
        log = self.log.getvalue()
        self.assertIn("auto-approved", log)
        self.assertNotIn("approved-by-human", log)
        self.assertIn("| auto", log)
        self.assertNotIn("Ada", log)

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
        self.assertIn(
            "/clients/contract.txt -> /shared/contract.txt", logged)
        self.assertIn("allowed", logged)
        self.assertFalse(os.path.exists(
            os.path.join(self.root, "shared", "contract.txt")))

    def test_interactive_approval_records_approver(self):
        # A human who says yes is recorded by name; a no is a denial.
        rules = self.rules("REQUIRE approval BEFORE SHARE\n"
                           "ALLOW SHARE IN /clients\n")
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
                           "ALLOW SHARE IN /clients\n"
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


    def test_overwrite_refused(self):
        # Overwriting is a backdoor delete: COPY, MOVE, and RENAME
        # onto an existing file are refused, even with DENY DELETE
        # in the policy — and even renaming a file to its own name.
        write(os.path.join(self.root, "shared", "contract.txt"),
              "existing\n")
        os.makedirs(os.path.join(self.root, "archive"))
        rules = self.rules("ALLOW COPY IN /clients\n"
                           "ALLOW COPY IN /shared\n"
                           "ALLOW COPY IN /archive\n"
                           "ALLOW MOVE IN /clients\n"
                           "ALLOW MOVE IN /shared\n"
                           "ALLOW RENAME IN /clients\n"
                           "DENY DELETE\n")
        decision, out = self.run_line(
            "COPY /clients/contract.txt TO /shared", rules)
        self.assertEqual(decision, "denied")
        self.assertIn("already exists", out)
        with open(os.path.join(self.root, "shared",
                               "contract.txt")) as f:
            self.assertEqual(f.read(), "existing\n")
        decision, out = self.run_line(
            "MOVE /clients/contract.txt TO /shared", rules)
        self.assertEqual(decision, "denied")
        self.assertIn("already exists", out)
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "clients", "contract.txt")))
        decision, out = self.run_line(
            "RENAME /clients/contract.txt TO contact.txt", rules)
        self.assertEqual(decision, "denied")
        self.assertIn("already exists", out)
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "clients", "contract.txt")))
        # Renaming a file to its own name is refused too.
        decision, _ = self.run_line(
            "RENAME /clients/contract.txt TO contract.txt", rules)
        self.assertEqual(decision, "denied")
        # When the destination is free, the action goes ahead.
        decision, _ = self.run_line(
            "COPY /clients/contract.txt TO /archive", rules)
        self.assertEqual(decision, "allowed")
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "archive", "contract.txt")))
        decision, _ = self.run_line(
            "RENAME /clients/contact.txt TO contact-v2.txt", rules)
        self.assertEqual(decision, "allowed")
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "clients", "contact-v2.txt")))
        self.assertFalse(os.path.exists(
            os.path.join(self.root, "clients", "contact.txt")))

    def test_audit_logs_resolved_path(self):
        # The audit line records the file actually touched, not the
        # path as written.
        rules = self.rules("ALLOW READ IN /clients\n"
                           "DENY READ IN /finance\n")
        decision, _ = self.run_line(
            "READ /clients/../finance/salaries.csv", rules)
        self.assertEqual(decision, "denied")
        log = self.log.getvalue()
        self.assertIn("/finance/salaries.csv", log)
        self.assertNotIn("/clients/../finance", log)

        # A COPY with ".." in the source logs the resolved source
        # and destination, joined with an arrow.
        rules = self.rules("ALLOW COPY IN /clients\n"
                           "ALLOW COPY IN /shared\n")
        decision, _ = self.run_line(
            "COPY /clients/../clients/contract.txt TO /shared", rules)
        self.assertEqual(decision, "allowed")
        log = self.log.getvalue()
        self.assertIn(
            "/clients/contract.txt -> /shared/contract.txt", log)
        self.assertNotIn("/clients/../clients", log)

    def test_auto_approve_records_auto(self):
        # An auto-approval records "auto" in the approver column —
        # never a person's name, because no human approved anything.
        # A human approval still records the name.
        rules = self.rules("REQUIRE approval BEFORE SHARE\n"
                           "ALLOW SHARE IN /clients\n")
        decision, _ = self.run_line(
            "SHARE /clients/contract.txt WITH Ada Lovelace",
            rules, auto_approve=True, approver="Ada")
        self.assertEqual(decision, "auto-approved")
        log = self.log.getvalue()
        self.assertIn("auto-approved", log)
        self.assertNotIn("approved-by-human", log)
        self.assertIn("| auto", log)
        self.assertNotIn("Ada", log)
        with mock.patch("builtins.input", return_value="y"):
            decision, _ = self.run_line(
                "SHARE /clients/contract.txt WITH Ada Lovelace",
                rules, approver="Ada")
        self.assertEqual(decision, "approved-by-human")
        self.assertIn("| Ada", self.log.getvalue())

    def test_approval_still_needs_allow(self):
        # An approval satisfies the REQUIRE rule, not the ALLOW
        # rule: a SHARE the human approves is still denied when no
        # ALLOW rule covers it.
        rules = self.rules("REQUIRE approval BEFORE SHARE\n")
        decision, out = self.run_line(
            "SHARE /clients/contract.txt WITH Ada Lovelace",
            rules, auto_approve=True, approver="Ada")
        self.assertEqual(decision, "denied")
        self.assertIn("no rule allows it", out)
        # With an ALLOW SHARE rule, the same approval goes through.
        rules = self.rules("REQUIRE approval BEFORE SHARE\n"
                           "ALLOW SHARE IN /clients\n")
        decision, _ = self.run_line(
            "SHARE /clients/contract.txt WITH Ada Lovelace",
            rules, auto_approve=True, approver="Ada")
        self.assertEqual(decision, "auto-approved")

    def test_case_insensitive_deny_matching(self):
        # Policy matching ignores case, so DENY READ IN /finance
        # catches /FINANCE on a case-insensitive filesystem too.
        # (True macOS/Windows filesystem behaviour could not be
        # tested on this Linux machine — this tests the matching.)
        rules = self.rules("ALLOW READ IN /clients\n"
                           "DENY READ IN /finance\n")
        for path in ("/FINANCE/salaries.csv",
                     "/Finance/salaries.csv",
                     "/fInAnCe/salaries.csv"):
            decision, out = self.run_line(f"READ {path}", rules)
            self.assertEqual(decision, "denied", path)
            self.assertNotIn("secret salary", out)
        # A lowercase read of an allowed folder still works.
        decision, out = self.run_line(
            "READ /clients/contract.txt", rules)
        self.assertEqual(decision, "allowed")
        self.assertIn("contract", out)

    def test_quoted_path_with_spaces(self):
        # Paths with spaces work when quoted, and unquoted lines
        # work exactly as before.
        write(os.path.join(self.root, "clients", "my file.txt"),
              "spaced\n")
        rules = self.rules("ALLOW READ IN /clients\n")
        decision, out = self.run_line(
            'READ "/clients/my file.txt"', rules)
        self.assertEqual(decision, "allowed")
        self.assertIn("spaced", out)
        # An unbalanced quote is a malformed line: denied, never a
        # crash — and the run carries on to the next statement.
        decision, out = self.run_line(
            'READ "/clients/my file.txt', rules)
        self.assertEqual(decision, "denied")
        self.assertIn("unbalanced quote", out)
        decision, out = self.run_line(
            "READ /clients/contract.txt", rules)
        self.assertEqual(decision, "allowed")

    def test_bare_allow_policy_fails_cleanly(self):
        # A malformed policy line like a bare ALLOW fails closed
        # with a clear one-line error, not a Python traceback.
        bad = os.path.join(self.tmp.name, "bad-policy.hnic")
        script = os.path.join(self.tmp.name, "empty.hnic")
        write(script, "SEARCH files IN /clients\n")
        audit = os.path.join(self.tmp.name, "audit.log")
        for policy_text in ("ALLOW\n", "ALLOW READ IN\n",
                            "REQUIRE approval BEFORE\n"):
            write(bad, policy_text)
            result = subprocess.run(
                [sys.executable, HNIC, "--policy", bad,
                 "--script", script, "--root", self.root,
                 "--audit", audit],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, policy_text)
            self.assertIn("hnic:", result.stderr, policy_text)
            self.assertNotIn("Traceback", result.stderr, policy_text)


if __name__ == "__main__":
    unittest.main()
