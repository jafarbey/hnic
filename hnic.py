#!/usr/bin/env python3
"""HNIC toy runtime — the first runnable piece of the language.

HNIC (Human-Negotiated Intelligence Control) is a small human-readable
language for governed file access by AI agents under human oversight.
This file is a toy, not a product. It reads two plain-text inputs — a
policy file written in HNIC, and a script of statements — checks every
statement against the policy BEFORE acting, and keeps an audit trail.

The "agent" here is only the script of statements. It never touches
files directly. The runtime owns access to the folder and carries out
the allowed work itself. That is the credential-brokering shape: the
agent holds no token, the runtime holds it, and the only road to the
files runs through the rules.

Usage:
    python3 hnic.py --policy examples/policy.hnic \
                    --script examples/demo.hnic \
                    --root test-folder [--auto-approve]

Policy lines (one rule per line, plain words):
    ALLOW SEARCH files IN /clients
    ALLOW READ IN /clients
    DENY DELETE
    DENY READ IN /finance
    REQUIRE approval BEFORE SHARE
    REQUIRE approval BEFORE COPY IN /clients

Script lines (one statement per line, plain words):
    SEARCH files IN /clients
    READ /clients/contract-acme.txt
    SHARE /clients/contract-acme.txt WITH Ada Lovelace
    COPY /clients/contract-acme.txt TO /shared
    MOVE /clients/contract-acme.txt TO /archive
    DELETE /clients/contract-acme.txt
    RENAME /clients/contract-acme.txt TO contract-acme-v2.txt
    REQUIRE approval
    SUMMARIZE /clients/contract-acme.txt
    EXTRACT /clients/contract-acme.txt

Decision order for every statement:
    1. A matching DENY rule refuses it.
    2. A matching REQUIRE rule stops and asks a human.
    3. A matching ALLOW rule lets it run.
    4. No matching rule refuses it. Silence is not permission.

SUMMARIZE and EXTRACT are stubs: they print that they need a language
model behind them and do not pretend to work.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime

# Verbs the language knows. SEARCH takes "files" as its object;
# the rest take a file path.
VERBS = {
    "SEARCH", "READ", "SHARE", "COPY", "MOVE",
    "DELETE", "RENAME", "SUMMARIZE", "EXTRACT",
}

# Verbs that need a language model behind them. The toy has none.
NEEDS_MODEL = {"SUMMARIZE", "EXTRACT"}


def clean_path(text):
    """Turn a written path like /clients/x.txt into a plain relative path."""
    return text.strip().strip("/")


def folder_of(path):
    """The folder a file path lives in, or / for the root."""
    parent = os.path.dirname(clean_path(path))
    return "/" + parent if parent else "/"


def inside(child, parent):
    """True when folder `child` is `parent` itself or under it."""
    parent = parent.rstrip("/") or "/"
    child = child.rstrip("/") or "/"
    if parent == "/":
        return True
    return child == parent or child.startswith(parent + "/")


class Rule:
    """One policy line: ALLOW, DENY, or REQUIRE, for a verb, in a folder."""

    def __init__(self, kind, verb, folder):
        self.kind = kind      # "ALLOW", "DENY", or "REQUIRE"
        self.verb = verb      # e.g. "SHARE"
        self.folder = folder  # e.g. "/clients", or None for everywhere

    def matches(self, verb, folder):
        if self.verb != verb:
            return False
        if self.folder is None:
            return True
        return inside(folder, self.folder)

    def __str__(self):
        where = f" IN {self.folder}" if self.folder else ""
        if self.kind == "REQUIRE":
            return f"REQUIRE approval BEFORE {self.verb}{where}"
        return f"{self.kind} {self.verb}{where}"


def parse_policy(path):
    """Read a policy file into a list of Rules."""
    rules = []
    with open(path) as f:
        for number, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            words = line.split()
            head = words[0].upper()
            if head == "ALLOW" or head == "DENY":
                # ALLOW SEARCH files IN /clients  |  DENY DELETE
                verb = words[1].upper()
                folder = None
                if len(words) > 2 and words[2].upper() == "IN":
                    folder = "/" + clean_path(words[3])
                elif len(words) > 2 and words[2].upper() == "FILES":
                    if len(words) > 3 and words[3].upper() == "IN":
                        folder = "/" + clean_path(words[4])
                if verb not in VERBS:
                    die(f"{path}:{number}: unknown verb {verb!r}")
                rules.append(Rule(head, verb, folder))
            elif head == "REQUIRE":
                # REQUIRE approval BEFORE SHARE  |  ... BEFORE COPY IN /clients
                if len(words) < 4 or words[1].upper() != "APPROVAL" \
                        or words[2].upper() != "BEFORE":
                    die(f"{path}:{number}: expected "
                        f"'REQUIRE approval BEFORE <verb>'")
                verb = words[3].upper()
                folder = None
                if len(words) > 4 and words[4].upper() == "IN":
                    folder = "/" + clean_path(words[5])
                if verb not in VERBS:
                    die(f"{path}:{number}: unknown verb {verb!r}")
                rules.append(Rule("REQUIRE", verb, folder))
            else:
                die(f"{path}:{number}: line must start with "
                    f"ALLOW, DENY, or REQUIRE")
    return rules


class Statement:
    """One script line: a verb and the words that follow it."""

    def __init__(self, raw, verb, args):
        self.raw = raw    # the line as written, for the audit trail
        self.verb = verb
        self.args = args  # the remaining words, split

    def target_folder(self):
        """The folder this statement touches, for policy matching."""
        if self.verb == "SEARCH":
            # SEARCH files IN /clients  (or plain SEARCH files)
            if "IN" in [w.upper() for w in self.args]:
                idx = [w.upper() for w in self.args].index("IN")
                return "/" + clean_path(self.args[idx + 1])
            return "/"
        # Every other verb names a file first: its folder governs it.
        return folder_of(self.args[0])

    def target_path(self):
        return self.args[0] if self.args else ""


def parse_script(path):
    """Read a script file into a list of Statements."""
    statements = []
    with open(path) as f:
        for number, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            words = line.split()
            verb = words[0].upper()
            if verb == "REQUIRE":
                # A bare "REQUIRE approval" line is a checkpoint:
                # stop and ask a human before going on.
                statements.append(Statement(line, "REQUIRE", []))
                continue
            if verb not in VERBS:
                die(f"{path}:{number}: unknown statement {verb!r}")
            statements.append(Statement(line, verb, words[1:]))
    return statements


def die(message):
    print(f"hnic: {message}", file=sys.stderr)
    sys.exit(2)


def ask_human(statement, auto_approve):
    """Stop and ask a human. Returns True when the human says yes."""
    if auto_approve:
        print("  (auto-approved for the demo)")
        return True
    print(f"  Approval needed before: {statement.raw}")
    try:
        answer = input("  Approve? [y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes")


def decide(rules, statement, auto_approve):
    """Check one statement against the policy. Returns (decision, reason).

    Decisions: allowed, denied, approved-by-human, needs-model.
    """
    verb = statement.verb
    folder = statement.target_folder()

    # 1. A matching DENY rule refuses it.
    for rule in rules:
        if rule.kind == "DENY" and rule.matches(verb, folder):
            return "denied", f"policy says {rule}"

    # 2. A matching REQUIRE rule stops and asks a human.
    for rule in rules:
        if rule.kind == "REQUIRE" and rule.matches(verb, folder):
            if ask_human(statement, auto_approve):
                return "approved-by-human", f"human approved ({rule})"
            return "denied", "human said no"

    # 3. A matching ALLOW rule lets it run.
    for rule in rules:
        if rule.kind == "ALLOW" and rule.matches(verb, folder):
            return "allowed", f"policy says {rule}"

    # 4. No matching rule refuses it. Silence is not permission.
    return "denied", "no rule allows it"


def safe_join(root, path):
    """Join a written path to the root folder, refusing escapes."""
    joined = os.path.normpath(os.path.join(root, clean_path(path)))
    base = os.path.normpath(root)
    if os.path.commonpath([joined, base]) != base:
        die(f"path escapes the folder: {path!r}")
    return joined


def run_statement(statement, root):
    """Carry out an allowed statement against the real folder."""
    verb = statement.verb
    args = statement.args

    if verb == "SEARCH":
        folder = statement.target_folder()
        full = safe_join(root, folder)
        if not os.path.isdir(full):
            print(f"  Folder {folder} not found.")
            return
        found = []
        for dirpath, _dirs, files in os.walk(full):
            for name in sorted(files):
                found.append(os.path.relpath(
                    os.path.join(dirpath, name), root))
        if not found:
            print(f"  Nothing in {folder}.")
        for path in found:
            print(f"  /{path}")

    elif verb == "READ":
        full = safe_join(root, statement.target_path())
        if not os.path.isfile(full):
            print(f"  File {statement.target_path()} not found.")
            return
        with open(full) as f:
            print(f.read().rstrip())

    elif verb == "SHARE":
        # No adapters yet: sharing is recorded, nothing is actually sent.
        person = " ".join(args[args.index("WITH") + 1:]) \
            if "WITH" in [w.upper() for w in args] else "someone"
        print(f"  Shared {statement.target_path()} with {person} "
              f"(recorded — no adapter yet, nothing was sent).")

    elif verb == "COPY":
        src = safe_join(root, args[0])
        dest_folder = safe_join(root, args[args.index("TO") + 1])
        if not os.path.isfile(src):
            print(f"  File {args[0]} not found.")
            return
        os.makedirs(dest_folder, exist_ok=True)
        shutil.copy2(src, os.path.join(dest_folder,
                                       os.path.basename(src)))
        print(f"  Copied {args[0]} to {args[args.index('TO') + 1]}.")

    elif verb == "MOVE":
        src = safe_join(root, args[0])
        dest_folder = safe_join(root, args[args.index("TO") + 1])
        if not os.path.isfile(src):
            print(f"  File {args[0]} not found.")
            return
        os.makedirs(dest_folder, exist_ok=True)
        shutil.move(src, os.path.join(dest_folder,
                                      os.path.basename(src)))
        print(f"  Moved {args[0]} to {args[args.index('TO') + 1]}.")

    elif verb == "DELETE":
        full = safe_join(root, statement.target_path())
        if not os.path.isfile(full):
            print(f"  File {statement.target_path()} not found.")
            return
        os.remove(full)
        print(f"  Deleted {statement.target_path()}.")

    elif verb == "RENAME":
        src = safe_join(root, args[0])
        new_name = args[args.index("TO") + 1]
        if not os.path.isfile(src):
            print(f"  File {args[0]} not found.")
            return
        os.rename(src, os.path.join(os.path.dirname(src), new_name))
        print(f"  Renamed {args[0]} to {new_name}.")


def main():
    parser = argparse.ArgumentParser(
        description="HNIC toy runtime: run a script of statements "
                    "against a policy, in a real folder.")
    parser.add_argument("--policy", required=True,
                        help="policy file, written in HNIC")
    parser.add_argument("--script", required=True,
                        help="script file: one statement per line")
    parser.add_argument("--root", required=True,
                        help="folder the statements act on")
    parser.add_argument("--audit", default="audit.log",
                        help="audit trail file (default: audit.log)")
    parser.add_argument("--auto-approve", action="store_true",
                        help="approve every REQUIRE prompt automatically "
                             "(demo runs only)")
    args = parser.parse_args()

    if not os.path.isdir(args.root):
        die(f"no such folder: {args.root}")

    rules = parse_policy(args.policy)
    statements = parse_script(args.script)

    print(f"Policy: {args.policy} ({len(rules)} rules)")
    print(f"Script: {args.script} ({len(statements)} statements)")
    print(f"Folder: {args.root}")
    print()

    with open(args.audit, "a") as log:
        for statement in statements:
            print(statement.raw)

            # A bare REQUIRE approval line is a checkpoint, not an action.
            if statement.verb == "REQUIRE":
                if ask_human(statement, args.auto_approve):
                    decision, reason = "approved-by-human", \
                        "human approved the checkpoint"
                else:
                    decision, reason = "denied", "human said no"
                print(f"  {decision.upper()} — {reason}")
            else:
                decision, reason = decide(rules, statement,
                                           args.auto_approve)
                print(f"  {decision.upper()} — {reason}")
                if decision in ("allowed", "approved-by-human"):
                    if statement.verb in NEEDS_MODEL:
                        # The model is not here. Say so, do not fake it.
                        decision = "needs-model"
                        print("  Needs a language model behind it — "
                              "this toy has none, so nothing was done.")
                    else:
                        run_statement(statement, args.root)

            # One append-only line per statement, whatever happened.
            stamp = datetime.now().isoformat(timespec="seconds")
            log.write(f"{stamp} | {statement.raw} | {decision}\n")
            log.flush()
            print()

    print(f"Audit trail: {args.audit}")


if __name__ == "__main__":
    main()
