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
import json
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

    def log_records(self):
        """The audit lines so far, each parsed as one JSON object.
        A line that is not valid JSON fails the test right here —
        the audit trail is one JSON object per line, always."""
        lines = [line for line in self.log.getvalue().splitlines()
                 if line.strip()]
        return [json.loads(line) for line in lines]

    def decision_records(self):
        """Only the decision lines: the first line per statement,
        naming the decision. An outcome line has no "decision"."""
        return [r for r in self.log_records() if "decision" in r]

    def outcome_records(self):
        """Only the outcome lines: the second line per attempted
        action, naming the outcome. A decision line has no
        "outcome"."""
        return [r for r in self.log_records() if "outcome" in r]

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

    def run_script(self, text, rules, execute=True, auto_approve=False,
                   approver="tester"):
        """Run a whole script, capturing the printed output. Returns
        the list of decisions, in order."""
        path = os.path.join(self.tmp.name, "script.hnic")
        write(path, text)
        statements = hnic.parse_script(path)
        decisions = []
        out = io.StringIO()
        with redirect_stdout(out):
            for statement in statements:
                decisions.append(hnic.run_one(
                    statement, rules, self.root, self.log, execute,
                    auto_approve, approver))
        return decisions, out.getvalue()

    def test_no_approval_prompt_without_allow(self):
        # The new order: DENY, then ALLOW, then REQUIRE. A SHARE
        # with a REQUIRE rule and no ALLOW rule is refused WITHOUT
        # asking anyone — the human is never asked to approve what
        # the policy would refuse anyway.
        rules = self.rules("REQUIRE approval BEFORE SHARE\n")
        with mock.patch("builtins.input",
                        return_value="y") as prompt:
            decision, out = self.run_line(
                "SHARE /clients/contract.txt WITH Ada Lovelace",
                rules, auto_approve=False, approver="Grace")
        self.assertEqual(decision, "denied")
        prompt.assert_not_called()
        self.assertNotIn("Approve?", out)
        self.assertIn("no rule allows it", out)
        # One audit line only: the decision. Nothing was attempted,
        # so there is no outcome line.
        self.assertEqual(len(self.log_records()), 1)
        self.assertEqual(self.log_records()[0]["decision"], "denied")

    def test_outcome_lines_for_done_actions(self):
        # Every allowed action that runs gets a second audit line
        # recording the outcome "done".
        os.makedirs(os.path.join(self.root, "archive"))
        rules = self.rules("ALLOW SEARCH IN /clients\n"
                           "ALLOW SEARCH IN /archive\n"
                           "ALLOW READ IN /clients\n"
                           "ALLOW COPY IN /clients\n"
                           "ALLOW COPY IN /archive\n"
                           "ALLOW MOVE IN /clients\n"
                           "ALLOW MOVE IN /archive\n"
                           "ALLOW DELETE IN /clients\n"
                           "ALLOW RENAME IN /clients\n")
        decisions, _ = self.run_script(
            "SEARCH files IN /clients\n"
            "READ /clients/contract.txt\n"
            "COPY /clients/contract.txt TO /archive\n"
            "MOVE /clients/contact.txt TO /archive\n"
            "RENAME /clients/contract.txt TO contract-v2.txt\n"
            "DELETE /clients/contract-v2.txt\n", rules)
        self.assertEqual(decisions, ["allowed"] * 6)
        outcomes = self.outcome_records()
        self.assertEqual(len(outcomes), 6)
        self.assertEqual([r["outcome"] for r in outcomes],
                         ["done"] * 6)
        self.assertEqual([r["verb"] for r in outcomes],
                         ["SEARCH", "READ", "COPY", "MOVE", "RENAME",
                          "DELETE"])
        # The outcome line carries the same path and destination as
        # its decision line.
        copy_outcome = [r for r in outcomes
                        if r["verb"] == "COPY"][0]
        self.assertEqual(copy_outcome["path"],
                         "/clients/contract.txt")
        self.assertEqual(copy_outcome["destination"],
                         "/archive/contract.txt")
        decision_copy = [r for r in self.decision_records()
                         if r["verb"] == "COPY"][0]
        self.assertEqual(decision_copy["path"],
                         copy_outcome["path"])
        self.assertEqual(decision_copy["destination"],
                         copy_outcome["destination"])

    def test_outcome_line_share_recorded_not_sent(self):
        # A SHARE is recorded, not sent: the outcome line says so.
        rules = self.rules("REQUIRE approval BEFORE SHARE\n"
                           "ALLOW SHARE IN /clients\n")
        decision, _ = self.run_line(
            "SHARE /clients/contract.txt WITH Ada Lovelace",
            rules, auto_approve=True, approver="Grace")
        self.assertEqual(decision, "auto-approved")
        outcomes = self.outcome_records()
        self.assertEqual(len(outcomes), 1)
        outcome = outcomes[0]
        self.assertEqual(outcome["outcome"], "recorded-not-sent")
        self.assertEqual(outcome["verb"], "SHARE")
        self.assertEqual(outcome["path"], "/clients/contract.txt")
        self.assertEqual(outcome["recipient"], "Ada Lovelace")
        self.assertEqual(outcome["statement"],
                         "SHARE /clients/contract.txt WITH Ada Lovelace")
        # The decision line names the same path and recipient.
        decision_record = self.decision_records()[-1]
        self.assertEqual(decision_record["path"], outcome["path"])
        self.assertEqual(decision_record["recipient"],
                         outcome["recipient"])

    def test_outcome_line_failed(self):
        # The decision line already said allowed, so the outcome
        # line must say it failed.
        rules = self.rules("ALLOW COPY IN /clients\n"
                           "ALLOW COPY IN /shared\n")
        with mock.patch("shutil.copy2",
                        side_effect=OSError("disk full")):
            decision, out = self.run_line(
                "COPY /clients/contract.txt TO /shared", rules)
        self.assertEqual(decision, "allowed")
        self.assertIn("Failed: disk full", out)
        outcomes = self.outcome_records()
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["outcome"], "failed: disk full")
        self.assertEqual(outcomes[0]["path"], "/clients/contract.txt")
        self.assertEqual(outcomes[0]["destination"],
                         "/shared/contract.txt")

    def test_outcome_line_not_done_no_model(self):
        # SUMMARIZE and EXTRACT pass the policy check, then stop:
        # the model is not here, so the outcome is not-done-no-model.
        rules = self.rules("ALLOW SUMMARIZE IN /clients\n"
                           "ALLOW EXTRACT IN /clients\n")
        for line in ("SUMMARIZE /clients/contract.txt",
                     "EXTRACT /clients/contract.txt"):
            decision, _ = self.run_line(line, rules)
            self.assertEqual(decision, "needs-model", line)
        outcomes = self.outcome_records()
        self.assertEqual(len(outcomes), 2)
        self.assertEqual([r["outcome"] for r in outcomes],
                         ["not-done-no-model"] * 2)
        self.assertEqual([r["verb"] for r in outcomes],
                         ["SUMMARIZE", "EXTRACT"])

    def test_no_outcome_line_when_nothing_was_attempted(self):
        # Denied, dry-run, and needs-approval decisions get no
        # outcome line — nothing was attempted.
        rules = self.rules("ALLOW COPY IN /clients\n"
                           "ALLOW COPY IN /shared\n"
                           "REQUIRE approval BEFORE SHARE\n"
                           "ALLOW SHARE IN /clients\n"
                           "DENY DELETE\n")
        decision, _ = self.run_line(
            "DELETE /clients/contract.txt", rules)
        self.assertEqual(decision, "denied")
        decision, _ = self.run_line(
            "COPY /clients/contract.txt TO /shared", rules,
            execute=False)
        self.assertEqual(decision, "dry-run:allowed")
        decision, _ = self.run_line(
            "SHARE /clients/contract.txt WITH Ada Lovelace",
            rules, execute=False)
        self.assertEqual(decision, "dry-run:needs-approval")
        self.assertEqual(self.outcome_records(), [])
        self.assertEqual(len(self.decision_records()), 3)

    def test_read_log_mode(self):
        # --read-log prints the log in plain words, one sentence
        # per line, and skips a malformed line with a plain note
        # instead of crashing.
        log_path = os.path.join(self.tmp.name, "sample.log")
        write(log_path,
              '{"timestamp": "2026-11-27T12:00:00", "statement": '
              '"SHARE /clients/contract.txt WITH Ada Lovelace", '
              '"verb": "SHARE", "decision": "auto-approved", '
              '"approver": "auto", "path": "/clients/contract.txt", '
              '"recipient": "Ada Lovelace"}\n'
              '{"timestamp": "2026-11-27T12:00:01", "statement": '
              '"SHARE /clients/contract.txt WITH Ada Lovelace", '
              '"verb": "SHARE", "outcome": "recorded-not-sent", '
              '"path": "/clients/contract.txt", '
              '"recipient": "Ada Lovelace"}\n'
              'this is not json\n'
              '{"timestamp": "2026-11-27T12:00:02", "statement": '
              '"READ /finance/salaries.csv", "verb": "READ", '
              '"decision": "denied", "approver": "-", '
              '"path": "/finance/salaries.csv"}\n')
        result = subprocess.run(
            [sys.executable, HNIC, "--read-log", log_path],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        out = result.stdout
        self.assertIn("Auto-approved for the demo, no human involved: "
                      "share /clients/contract.txt with Ada Lovelace.",
                      out)
        self.assertIn("Recorded, not sent: the runtime recorded share "
                      "/clients/contract.txt with Ada Lovelace, and "
                      "nothing was sent.", out)
        self.assertIn("Skipped: line 3 is not a readable audit line.",
                      out)
        self.assertIn("The runtime refused this: "
                      "read /finance/salaries.csv.", out)
        self.assertNotIn("Traceback", result.stderr)

        # A missing log file fails cleanly, not with a traceback.
        missing = subprocess.run(
            [sys.executable, HNIC, "--read-log",
             os.path.join(self.tmp.name, "nope.log")],
            capture_output=True, text=True)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("hnic: cannot read log file", missing.stderr)
        self.assertNotIn("Traceback", missing.stderr)

    def test_dotdot_bypass_denied(self):
        # Problem 1: READ /clients/../finance/salaries.csv must be
        # checked against /finance, not /clients, and denied.
        rules = self.rules("ALLOW READ IN /clients\n"
                           "DENY READ IN /finance\n")
        decision, out = self.run_line(
            "READ /clients/../finance/salaries.csv", rules)
        self.assertEqual(decision, "denied")
        self.assertNotIn("secret salary", out)
        self.assertEqual(self.log_records()[-1]["decision"], "denied")

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
        # "approved-by-human", and the approver field says "auto" —
        # no person's name is attached to an approval nobody gave.
        # The approver here is "Grace", not "Ada": the statement
        # shares WITH Ada Lovelace, and an approver named Ada would
        # make the "no name in the log" check meaningless.
        rules = self.rules("REQUIRE approval BEFORE SHARE\n"
                           "ALLOW SHARE IN /clients\n")
        decision, _ = self.run_line(
            "SHARE /clients/contract.txt WITH Ada Lovelace",
            rules, auto_approve=True, approver="Grace")
        self.assertEqual(decision, "auto-approved")
        records = self.decision_records()
        self.assertEqual(records[-1]["decision"], "auto-approved")
        self.assertEqual(records[-1]["approver"], "auto")
        self.assertNotIn("Grace", self.log.getvalue())

        # The decision line is written BEFORE the action: even when
        # the action then fails, the record is already there — and
        # a second line now records the outcome, so the log says
        # what happened, not just what was decided.
        rules = self.rules("ALLOW COPY IN /clients\n"
                           "ALLOW COPY IN /shared\n")
        before = len(self.log_records())
        with mock.patch("shutil.copy2",
                        side_effect=OSError("disk full")):
            decision, out = self.run_line(
                "COPY /clients/contract.txt TO /shared", rules)
        self.assertEqual(decision, "allowed")
        self.assertIn("Failed: disk full", out)
        records = self.log_records()[before:]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["path"],
                         "/clients/contract.txt")
        self.assertEqual(records[0]["destination"],
                         "/shared/contract.txt")
        self.assertEqual(records[0]["decision"], "allowed")
        self.assertEqual(records[1]["outcome"], "failed: disk full")
        self.assertEqual(records[1]["path"],
                         "/clients/contract.txt")
        self.assertEqual(records[1]["destination"],
                         "/shared/contract.txt")
        self.assertFalse(os.path.exists(
            os.path.join(self.root, "shared", "contract.txt")))

    def test_interactive_approval_records_approver(self):
        # A human who says yes is recorded by name; a no is a denial.
        # The approver is "Grace", not "Ada": the statement shares
        # WITH Ada Lovelace, so a name check against "Ada" would
        # pass on the recipient alone.
        rules = self.rules("REQUIRE approval BEFORE SHARE\n"
                           "ALLOW SHARE IN /clients\n")
        with mock.patch("builtins.input", return_value="y"):
            decision, _ = self.run_line(
                "SHARE /clients/contract.txt WITH Ada Lovelace",
                rules, approver="Grace")
        self.assertEqual(decision, "approved-by-human")
        self.assertEqual(self.decision_records()[-1]["approver"],
                         "Grace")
        with mock.patch("builtins.input", return_value="n"):
            decision, _ = self.run_line(
                "SHARE /clients/contract.txt WITH Ada Lovelace",
                rules, approver="Grace")
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
            rules, auto_approve=True, approver="Grace")
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
        self.assertEqual(self.log_records()[-1]["decision"],
                         "dry-run:allowed")
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
        # path as written. The statement field still carries the line
        # as written — it is the path field that must be resolved.
        rules = self.rules("ALLOW READ IN /clients\n"
                           "DENY READ IN /finance\n")
        decision, _ = self.run_line(
            "READ /clients/../finance/salaries.csv", rules)
        self.assertEqual(decision, "denied")
        record = self.log_records()[-1]
        self.assertEqual(record["path"], "/finance/salaries.csv")
        self.assertEqual(record["statement"],
                         "READ /clients/../finance/salaries.csv")

        # A COPY with ".." in the source logs the resolved source
        # and destination as separate fields.
        rules = self.rules("ALLOW COPY IN /clients\n"
                           "ALLOW COPY IN /shared\n")
        decision, _ = self.run_line(
            "COPY /clients/../clients/contract.txt TO /shared", rules)
        self.assertEqual(decision, "allowed")
        record = self.decision_records()[-1]
        self.assertEqual(record["path"], "/clients/contract.txt")
        self.assertEqual(record["destination"],
                         "/shared/contract.txt")

    def test_auto_approve_records_auto(self):
        # An auto-approval records "auto" in the approver field —
        # never a person's name, because no human approved anything.
        # A human approval still records the name. The approver is
        # "Grace", not "Ada": the statement shares WITH Ada
        # Lovelace, so "Ada" in the log would prove nothing.
        rules = self.rules("REQUIRE approval BEFORE SHARE\n"
                           "ALLOW SHARE IN /clients\n")
        decision, _ = self.run_line(
            "SHARE /clients/contract.txt WITH Ada Lovelace",
            rules, auto_approve=True, approver="Grace")
        self.assertEqual(decision, "auto-approved")
        record = self.decision_records()[-1]
        self.assertEqual(record["decision"], "auto-approved")
        self.assertEqual(record["approver"], "auto")
        self.assertNotIn("Grace", self.log.getvalue())
        with mock.patch("builtins.input", return_value="y"):
            decision, _ = self.run_line(
                "SHARE /clients/contract.txt WITH Ada Lovelace",
                rules, approver="Grace")
        self.assertEqual(decision, "approved-by-human")
        self.assertEqual(self.decision_records()[-1]["approver"],
                         "Grace")

    def test_approval_still_needs_allow(self):
        # An approval satisfies the REQUIRE rule, not the ALLOW
        # rule: a SHARE the human approves is still denied when no
        # ALLOW rule covers it.
        rules = self.rules("REQUIRE approval BEFORE SHARE\n")
        decision, out = self.run_line(
            "SHARE /clients/contract.txt WITH Ada Lovelace",
            rules, auto_approve=True, approver="Grace")
        self.assertEqual(decision, "denied")
        self.assertIn("no rule allows it", out)
        # With an ALLOW SHARE rule, the same approval goes through.
        rules = self.rules("REQUIRE approval BEFORE SHARE\n"
                           "ALLOW SHARE IN /clients\n")
        decision, _ = self.run_line(
            "SHARE /clients/contract.txt WITH Ada Lovelace",
            rules, auto_approve=True, approver="Grace")
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


    def test_audit_line_names_the_verb_and_recipient(self):
        # Gap 1: the audit line names the verb and, for SHARE, the
        # recipient — a READ and a SHARE no longer look the same in
        # the log.
        rules = self.rules("ALLOW READ IN /clients\n"
                           "REQUIRE approval BEFORE SHARE\n"
                           "ALLOW SHARE IN /clients\n")
        decision, _ = self.run_line(
            "SHARE /clients/contract.txt WITH Ada Lovelace",
            rules, auto_approve=True, approver="Grace")
        self.assertEqual(decision, "auto-approved")
        share = self.decision_records()[-1]
        self.assertEqual(share["verb"], "SHARE")
        self.assertEqual(share["path"], "/clients/contract.txt")
        self.assertEqual(share["recipient"], "Ada Lovelace")
        self.assertEqual(share["decision"], "auto-approved")
        self.assertEqual(share["approver"], "auto")

        decision, _ = self.run_line(
            "READ /clients/contract.txt", rules)
        self.assertEqual(decision, "allowed")
        read = self.decision_records()[-1]
        self.assertEqual(read["verb"], "READ")
        self.assertEqual(read["path"], "/clients/contract.txt")
        self.assertNotIn("recipient", read)

        # The two lines are distinguishable by verb alone.
        self.assertNotEqual(share["verb"], read["verb"])

    def test_audit_line_cannot_be_forged_with_pipes(self):
        # Gap 2: a path holding " | " and quotes is just string
        # content in the JSON line — it cannot inject fake columns.
        name = 'weird "name" | auto-approved | auto.txt'
        write(os.path.join(self.root, "clients", name), "weird\n")
        rules = self.rules("ALLOW READ IN /clients\n")
        line = ('READ "/clients/weird \\"name\\" | auto-approved | '
                'auto.txt"')
        decision, out = self.run_line(line, rules)
        self.assertEqual(decision, "allowed")
        self.assertIn("weird", out)
        lines = [line for line in self.log.getvalue().splitlines()
                 if line.strip()]
        # Two audit lines for this statement: the decision, then
        # the outcome. Both parse as exactly one JSON object.
        self.assertEqual(len(lines), 2)
        record = json.loads(lines[0])
        self.assertEqual(record["verb"], "READ")
        self.assertEqual(record["path"], "/clients/" + name)
        self.assertEqual(record["decision"], "allowed")
        self.assertEqual(record["approver"], "-")
        # No extra fields: the forged text added nothing.
        self.assertEqual(set(record),
                         {"timestamp", "statement", "verb", "path",
                          "decision", "approver"})
        self.assertEqual(record["statement"], line)
        # The outcome line says the read happened, and carries the
        # same path — the forged text is string content there too.
        outcome = json.loads(lines[1])
        self.assertEqual(outcome["outcome"], "done")
        self.assertEqual(outcome["verb"], "READ")
        self.assertEqual(outcome["path"], "/clients/" + name)
        self.assertEqual(outcome["statement"], line)
        self.assertNotIn("decision", outcome)


if __name__ == "__main__":
    unittest.main()
