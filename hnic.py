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
                    --root test-folder --auto-approve --execute

    python3 hnic.py --read-log audit.log

By default the runtime runs DRY: it checks the policy, prints the
decision and what it would do, and logs it as dry-run, but it changes
nothing. Add --execute to actually perform the allowed actions. The
examples in the book run read-only unless the reader says otherwise,
and this is how the toy keeps that promise. SEARCH and READ still run
in a dry run, because they change nothing; COPY, MOVE, DELETE, RENAME
and SHARE are only described, never done.

--read-log takes only a log path: it reads a JSON-lines audit log
and prints it back in plain words, one sentence per line, so a
non-engineer can read what happened without reading JSON. It
changes nothing.

Paths are resolved ONCE, with realpath, against the real root folder,
and that same resolved path is used for both the policy check and the
action. A path that escapes the root — through ".." or through a
symlink — is denied, never acted on.

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
    2. Every folder the statement touches must match an ALLOW
       rule. Silence is not permission — a statement with no
       ALLOW rule is refused here, before anyone is asked, so a
       human is never asked to approve what the policy would
       refuse anyway.
    3. A matching REQUIRE rule stops and asks a human.
    4. Approved, or allowed outright, when every pair passed.

COPY and MOVE are checked twice: once for the folder the file comes
from, once for the folder it goes to. RENAME is checked for the folder
the file sits in and the folder the new name lands in.

The audit trail is one JSON object per line. The first line for a
statement is the decision, written BEFORE the action runs, so a
crash still leaves the record of what was decided. After an
allowed or approved action is attempted, a second line records
the outcome: "done", "recorded-not-sent" for a SHARE (there is
no adapter yet, so a share is recorded and nothing leaves the
machine), "failed: <error>" when the action raised, or
"not-done-no-model" for SUMMARIZE and EXTRACT (the model is not
here). Denied, dry-run, and needs-approval decisions get no
outcome line — nothing was attempted. Each line names the verb,
the resolved path actually touched (and the destination for
COPY, MOVE, and RENAME), the recipient for SHARE; the decision
line also names the decision and the approver. JSON escaping is
what keeps the record honest: a path containing " | " or quotes
is just string content, never a new column. An approval is
recorded with the name of the person who approved it
(--approver, or the login name when not given); --auto-approve
records "auto" in the approver field, because no human approved
anything.

COPY, MOVE, and RENAME refuse when the destination already exists:
overwriting is a backdoor delete, and this toy never overwrites.
Policy matching ignores case, so a rule written for /finance also
catches /FINANCE. Paths may be quoted when they contain spaces.

SUMMARIZE and EXTRACT are stubs: they print that they need a language
model behind them and do not pretend to work.
"""

import argparse
import getpass
import json
import os
import shlex
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

# Verbs that change something. In a dry run they are only described.
MUTATING = {"SHARE", "COPY", "MOVE", "DELETE", "RENAME"}


def clean_path(text):
    """Turn a written path like /clients/x.txt into a plain relative path."""
    return text.strip().strip("/")


def inside(child, parent):
    """True when folder `child` is `parent` itself or under it.

    Matching ignores case, so a rule written for /finance also
    catches /FINANCE on a case-insensitive filesystem. The action
    itself still uses the real resolved path from the disk."""
    parent = (parent.rstrip("/") or "/").lower()
    child = (child.rstrip("/") or "/").lower()
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
    """Read a policy file into a list of Rules.

    A malformed line fails closed with a clear one-line error, never
    a traceback: the policy is the safety core, so a half-read
    policy must not run. Paths may be quoted when they contain
    spaces; unquoted lines work exactly as before.
    """
    rules = []
    with open(path) as f:
        for number, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                words = shlex.split(line)
            except ValueError:
                die(f"{path}:{number}: unbalanced quote")
            if not words:
                die(f"{path}:{number}: empty rule")
            head = words[0].upper()
            if head == "ALLOW" or head == "DENY":
                # ALLOW SEARCH files IN /clients  |  DENY DELETE
                if len(words) < 2:
                    die(f"{path}:{number}: expected "
                        f"'{head} <verb> [IN <folder>]'")
                verb = words[1].upper()
                folder = None
                if len(words) > 2 and words[2].upper() == "IN":
                    if len(words) < 4:
                        die(f"{path}:{number}: expected "
                            f"'{head} {verb} IN <folder>'")
                    folder = "/" + clean_path(words[3])
                elif len(words) > 2 and words[2].upper() == "FILES":
                    if len(words) > 3 and words[3].upper() == "IN":
                        if len(words) < 5:
                            die(f"{path}:{number}: expected "
                                f"'{head} {verb} files IN <folder>'")
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
                    if len(words) < 6:
                        die(f"{path}:{number}: expected "
                            f"'REQUIRE approval BEFORE {verb} "
                            f"IN <folder>'")
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

    def __init__(self, raw, verb, args, error=None):
        self.raw = raw    # the line as written, for the audit trail
        self.verb = verb
        self.args = args  # the remaining words, split
        self.error = error  # set when the line could not be parsed

    def target_folder(self):
        """The folder a SEARCH statement names, as written."""
        if "IN" in [w.upper() for w in self.args]:
            idx = [w.upper() for w in self.args].index("IN")
            return "/" + clean_path(self.args[idx + 1])
        return "/"


def parse_line(line):
    """Parse one script line into a Statement, or None for a blank
    or comment line. Paths may be quoted: READ "/clients/my file.txt"
    works, and unquoted lines work exactly as before. A line with an
    unbalanced quote becomes a Statement carrying an error — it is
    denied when run, never a crash."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    try:
        words = shlex.split(stripped)
    except ValueError:
        return Statement(stripped, "", [], error="unbalanced quote")
    if not words:
        return None
    verb = words[0].upper()
    if verb == "REQUIRE":
        # A bare "REQUIRE approval" line is a checkpoint:
        # stop and ask a human before going on.
        return Statement(stripped, "REQUIRE", [])
    return Statement(stripped, verb, words[1:])


def parse_script(path):
    """Read a script file into a list of Statements.

    A line the language does not know is kept, not rejected: the run
    denies it as an unknown statement and carries on.
    """
    statements = []
    with open(path) as f:
        for raw in f:
            statement = parse_line(raw)
            if statement is not None:
                statements.append(statement)
    return statements


def die(message):
    """Stop the whole run. Only for setup errors: a missing or
    unreadable policy or script file, a malformed policy line, or a
    missing root folder. The policy is the safety core — a half-read
    policy must not run. A bad path or a bad statement in the script
    is denied and the run carries on; that is a decision, not a
    setup error."""
    print(f"hnic: {message}", file=sys.stderr)
    sys.exit(2)


def find_keyword(args, word):
    """Index of `word` in args, matching case-insensitively, or -1."""
    upper = [w.upper() for w in args]
    return upper.index(word) if word in upper else -1


def validate(statement):
    """Return an error string when the statement is incomplete or
    unknown, else None. A bad statement is denied, never a crash."""
    if statement.error is not None:
        return statement.error
    verb, args = statement.verb, statement.args
    if verb == "REQUIRE":
        return None
    if verb not in VERBS:
        return f"unknown statement {verb!r}"
    if verb == "SEARCH":
        idx = find_keyword(args, "IN")
        if idx == len(args) - 1:
            return "SEARCH names IN but no folder follows it"
        return None
    if verb in ("READ", "DELETE", "SUMMARIZE", "EXTRACT"):
        if not args:
            return f"{verb} names no file"
        return None
    if verb == "SHARE":
        if not args:
            return "SHARE names no file"
        idx = find_keyword(args, "WITH")
        if idx < 0 or idx == len(args) - 1:
            return "SHARE needs WITH <person>"
        return None
    if verb in ("COPY", "MOVE", "RENAME"):
        if not args:
            return f"{verb} names no file"
        idx = find_keyword(args, "TO")
        if idx <= 0 or idx == len(args) - 1:
            return f"{verb} needs TO <destination>"
        return None
    return None


def resolve_path(root, path):
    """Resolve a written path once, against the real root folder.

    realpath settles ".." and follows symlinks, so the resolved path
    is the file that would actually be touched. Returns the resolved
    absolute path, or None when it escapes the root.
    """
    joined = os.path.realpath(os.path.join(root, clean_path(path)))
    base = os.path.realpath(root)
    if os.path.commonpath([joined, base]) != base:
        return None
    return joined


def policy_folder(root, resolved, is_folder=False):
    """The folder a resolved path belongs to, for policy matching."""
    rel = os.path.relpath(resolved, root)
    if is_folder:
        return "/" + rel if rel != "." else "/"
    parent = os.path.dirname(rel)
    return "/" + parent if parent else "/"


def resolve_statement(statement, root):
    """Resolve every path a statement touches, once.

    Returns (paths, error): paths is {"target": resolved path,
    "dest": resolved destination or None}; error is a string when a
    path escapes the root, else None. The same resolved paths are
    used for the policy check and for the action.
    """
    verb, args = statement.verb, statement.args
    paths = {"target": None, "dest": None}
    if verb == "SEARCH":
        folder = statement.target_folder()
        resolved = resolve_path(root, folder)
        if resolved is None:
            return None, f"folder escapes the folder: {folder!r}"
        paths["target"] = resolved
        return paths, None
    target = resolve_path(root, args[0])
    if target is None:
        return None, f"path escapes the folder: {args[0]!r}"
    paths["target"] = target
    if verb in ("COPY", "MOVE"):
        dest = resolve_path(root, args[find_keyword(args, "TO") + 1])
        if dest is None:
            return None, ("destination escapes the folder: "
                          f"{args[find_keyword(args, 'TO') + 1]!r}")
        paths["dest"] = dest
    elif verb == "RENAME":
        new_name = args[find_keyword(args, "TO") + 1]
        dest = resolve_path(
            root, os.path.join(os.path.dirname(args[0]), new_name))
        if dest is None:
            return None, f"new name escapes the folder: {new_name!r}"
        paths["dest"] = dest
    return paths, None


def destination_file(statement, paths):
    """The full path a COPY, MOVE, or RENAME would write to, or None."""
    if statement.verb in ("COPY", "MOVE"):
        return os.path.join(paths["dest"],
                            os.path.basename(paths["target"]))
    if statement.verb == "RENAME":
        return paths["dest"]
    return None


def rel_display(resolved, root):
    """A resolved path as the audit line shows it: /clients/x.txt."""
    rel = os.path.relpath(resolved, root)
    return "/" + rel if rel != "." else "/"


def audit_record(statement, paths, root, decision, who):
    """One audit line, as a dict to be written as JSON.

    Fields: timestamp, statement (the line as written), verb, path
    (the resolved path actually touched), destination (COPY, MOVE,
    and RENAME only), recipient (SHARE only), decision, approver.
    JSON escaping is what keeps the record honest: a path holding
    " | " or quotes is string content, never a new column. When
    nothing resolved — a bad statement, a path that escapes the
    folder, a checkpoint — there is no path to record, and the
    statement field is all there is.
    """
    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "statement": statement.raw,
        "verb": statement.verb,
        "decision": decision,
        "approver": who or "-",
    }
    if paths is not None:
        record["path"] = rel_display(paths["target"], root)
        if statement.verb in ("COPY", "MOVE", "RENAME"):
            dest = destination_file(statement, paths)
            if dest is not None:
                record["destination"] = rel_display(dest, root)
    if statement.verb == "SHARE":
        idx = find_keyword(statement.args, "WITH")
        if 0 <= idx < len(statement.args) - 1:
            record["recipient"] = " ".join(statement.args[idx + 1:])
    return record


def outcome_record(statement, paths, root, outcome):
    """One outcome line, as a dict to be written as JSON.

    Written after an allowed or approved action is attempted, so
    the log says what happened, not just what was decided. Fields:
    timestamp, statement (the line as written), verb, outcome,
    path (the resolved path actually touched), destination (COPY,
    MOVE, and RENAME only), recipient (SHARE only). The path,
    destination, and recipient match the decision line for the
    same statement, so the two lines pair up. Outcome values:
    "done", "recorded-not-sent" (SHARE — no adapter yet),
    "failed: <error>", "not-done-no-model" (SUMMARIZE, EXTRACT).
    """
    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "statement": statement.raw,
        "verb": statement.verb,
        "outcome": outcome,
    }
    if paths is not None:
        record["path"] = rel_display(paths["target"], root)
        if statement.verb in ("COPY", "MOVE", "RENAME"):
            dest = destination_file(statement, paths)
            if dest is not None:
                record["destination"] = rel_display(dest, root)
    if statement.verb == "SHARE":
        idx = find_keyword(statement.args, "WITH")
        if 0 <= idx < len(statement.args) - 1:
            record["recipient"] = " ".join(statement.args[idx + 1:])
    return record


def check_pairs(statement, paths, root):
    """The (verb, folder) pairs this statement must pass.

    COPY and MOVE are checked for the folder the file comes from and
    the folder it goes to. RENAME is checked for the folder the file
    sits in and the folder the new name lands in.
    """
    verb = statement.verb
    if verb == "SEARCH":
        return [(verb, policy_folder(root, paths["target"],
                                     is_folder=True))]
    pairs = [(verb, policy_folder(root, paths["target"]))]
    if paths["dest"] is not None:
        if verb in ("COPY", "MOVE"):
            pairs.append((verb, policy_folder(root, paths["dest"],
                                              is_folder=True)))
        elif verb == "RENAME":
            pairs.append((verb, policy_folder(root, paths["dest"])))
    return pairs


def pair_has_allow(rules, verb, folder):
    """True when some ALLOW rule matches this (verb, folder)."""
    for rule in rules:
        if rule.kind == "ALLOW" and rule.matches(verb, folder):
            return True
    return False


def check_pair(rules, verb, folder):
    """One (verb, folder) check, without asking anyone.

    Returns (outcome, reason): outcome is "deny", "require",
    "allow", or "none" (no rule speaks to it).
    """
    for rule in rules:
        if rule.kind == "DENY" and rule.matches(verb, folder):
            return "deny", f"policy says {rule}"
    for rule in rules:
        if rule.kind == "REQUIRE" and rule.matches(verb, folder):
            return "require", f"policy says {rule}"
    for rule in rules:
        if rule.kind == "ALLOW" and rule.matches(verb, folder):
            return "allow", f"policy says {rule}"
    return "none", "no rule allows it"


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


def decide(rules, pairs, statement, auto_approve, approver, dry_run):
    """Check the pairs against the policy.

    Returns (decision, reason, approver): decision is "allowed",
    "denied", "approved-by-human", "auto-approved", or, in a dry
    run, "needs-approval" when a human would have been asked.
    Approver is "auto" for an auto-approval, the approver's name
    for a human approval, else None.

    The ALLOW check runs before anyone is asked: an approval
    satisfies the REQUIRE rule, not the ALLOW rule, so a statement
    with no ALLOW rule is refused here, and no human is asked to
    approve what the policy would refuse anyway.
    """
    outcomes = [check_pair(rules, verb, folder)
                for verb, folder in pairs]

    # 1. A matching DENY rule refuses it.
    for outcome, reason in outcomes:
        if outcome == "deny":
            return "denied", reason, None

    # 2. Silence is not permission: every pair needs an ALLOW.
    #    Refused here, before anyone is asked.
    for verb, folder in pairs:
        if not pair_has_allow(rules, verb, folder):
            return "denied", "no rule allows it", None

    # 3. A matching REQUIRE rule stops and asks a human. In a dry
    #    run nothing will happen, so nobody is asked.
    required = [reason for outcome, reason in outcomes
                if outcome == "require"]
    approved = False
    if required:
        if dry_run:
            return "needs-approval", \
                f"would stop and ask a human ({required[0]})", None
        if ask_human(statement, auto_approve):
            approved = True
        else:
            return "denied", "human said no", None

    # 4. Every pair is allowed — and approved, when approval was
    #    required.
    if approved:
        if auto_approve:
            return "auto-approved", \
                f"auto-approved for the demo ({required[0]})", "auto"
        return "approved-by-human", \
            f"human approved ({required[0]})", approver
    return "allowed", outcomes[0][1], None


def decide_checkpoint(statement, auto_approve, approver, dry_run):
    """A bare REQUIRE approval line: stop and ask, or note it."""
    if dry_run:
        return "needs-approval", "would stop and ask a human", None
    if ask_human(statement, auto_approve):
        if auto_approve:
            return "auto-approved", "auto-approved for the demo", \
                "auto"
        return "approved-by-human", "human approved the checkpoint", \
            approver
    return "denied", "human said no", None


def would_do(statement):
    """Plain words for what a mutating statement would do."""
    verb, args = statement.verb, statement.args
    if verb == "SHARE":
        idx = find_keyword(args, "WITH")
        person = " ".join(args[idx + 1:]) \
            if 0 <= idx < len(args) - 1 else "someone"
        return f"share {args[0]} with {person}"
    if verb in ("COPY", "MOVE"):
        idx = find_keyword(args, "TO")
        dest = args[idx + 1] if 0 <= idx < len(args) - 1 else "?"
        action = "copy" if verb == "COPY" else "move"
        return f"{action} {args[0]} to {dest}"
    if verb == "DELETE":
        return f"delete {args[0]}"
    if verb == "RENAME":
        idx = find_keyword(args, "TO")
        new = args[idx + 1] if 0 <= idx < len(args) - 1 else "?"
        return f"rename {args[0]} to {new}"
    return statement.raw.lower()


def run_statement(statement, root, paths):
    """Carry out an allowed statement. Paths are already resolved."""
    verb = statement.verb
    args = statement.args

    if verb == "SEARCH":
        full = paths["target"]
        if not os.path.isdir(full):
            print(f"  Folder {statement.target_folder()} not found.")
            return
        found = []
        for dirpath, _dirs, files in os.walk(full):
            for name in sorted(files):
                found.append("/" + os.path.relpath(
                    os.path.join(dirpath, name), root))
        if not found:
            print(f"  Nothing in {statement.target_folder()}.")
        for path in found:
            print(f"  {path}")

    elif verb == "READ":
        full = paths["target"]
        if not os.path.isfile(full):
            print(f"  File {args[0]} not found.")
            return
        with open(full) as f:
            print(f.read().rstrip())

    elif verb == "SHARE":
        # No adapters yet: sharing is recorded, nothing is actually sent.
        idx = find_keyword(args, "WITH")
        person = " ".join(args[idx + 1:]) if idx >= 0 else "someone"
        print(f"  Shared {args[0]} with {person} "
              f"(recorded — no adapter yet, nothing was sent).")

    elif verb == "COPY":
        src = paths["target"]
        dest_folder = paths["dest"]
        if not os.path.isfile(src):
            print(f"  File {args[0]} not found.")
            return
        os.makedirs(dest_folder, exist_ok=True)
        shutil.copy2(src, os.path.join(dest_folder,
                                       os.path.basename(src)))
        print(f"  Copied {args[0]} to "
              f"{args[find_keyword(args, 'TO') + 1]}.")

    elif verb == "MOVE":
        src = paths["target"]
        dest_folder = paths["dest"]
        if not os.path.isfile(src):
            print(f"  File {args[0]} not found.")
            return
        os.makedirs(dest_folder, exist_ok=True)
        shutil.move(src, os.path.join(dest_folder,
                                      os.path.basename(src)))
        print(f"  Moved {args[0]} to "
              f"{args[find_keyword(args, 'TO') + 1]}.")

    elif verb == "DELETE":
        full = paths["target"]
        if not os.path.isfile(full):
            print(f"  File {args[0]} not found.")
            return
        os.remove(full)
        print(f"  Deleted {args[0]}.")

    elif verb == "RENAME":
        src = paths["target"]
        new_name = args[find_keyword(args, "TO") + 1]
        if not os.path.isfile(src):
            print(f"  File {args[0]} not found.")
            return
        os.rename(src, paths["dest"])
        print(f"  Renamed {args[0]} to {new_name}.")


def run_one(statement, rules, root, log, execute, auto_approve,
            approver):
    """Run one statement: validate, resolve, decide, log, then act.

    The audit line is written BEFORE the action, so a crash still
    leaves the record. Returns the decision as logged.
    """
    dry_run = not execute
    print(statement.raw)
    verb = statement.verb
    paths = None

    # A bare REQUIRE approval line is a checkpoint, not an action.
    if verb == "REQUIRE":
        decision, reason, who = decide_checkpoint(
            statement, auto_approve, approver, dry_run)
    else:
        error = validate(statement)
        if error is not None:
            decision, reason, who = "denied", \
                f"incomplete: {error}", None
        else:
            paths, error = resolve_statement(statement, root)
            if error is not None:
                decision, reason, who = "denied", error, None
            elif verb in ("COPY", "MOVE", "RENAME") and \
                    os.path.lexists(destination_file(statement, paths)):
                # Overwriting is a backdoor delete: refuse it, no
                # exceptions, before any policy question is asked.
                decision, reason, who = "denied", \
                    "destination already exists — refusing " \
                    "to overwrite", None
            else:
                pairs = check_pairs(statement, paths, root)
                decision, reason, who = decide(
                    rules, pairs, statement, auto_approve, approver,
                    dry_run)
                if decision in ("allowed", "approved-by-human",
                                "auto-approved") \
                        and verb in NEEDS_MODEL:
                    # The model is not here. Say so, do not fake it.
                    decision = "needs-model"
                    reason = "needs a language model behind it"

    logged = decision if execute else f"dry-run:{decision}"

    # The decision goes down before anything happens, as one JSON
    # object per line. It names the verb, the resolved path actually
    # touched, the recipient for SHARE, the decision, and who
    # approved it — a path holding " | " or quotes cannot forge a
    # column, because JSON escaping keeps it string content.
    log.write(json.dumps(audit_record(statement, paths, root,
                                     logged, who)) + "\n")
    log.flush()
    print(f"  {logged.upper()} — {reason}")

    # The outcome goes down after the action is attempted, as a
    # second JSON line, so the log says what happened, not just
    # what was decided. Denied, dry-run, and needs-approval
    # decisions get no outcome line — nothing was attempted.
    outcome = None
    if decision == "needs-model":
        # The model is not here. Say so, do not fake it.
        if execute:
            outcome = "not-done-no-model"
    elif decision in ("allowed", "approved-by-human", "auto-approved") \
            and verb != "REQUIRE":
        # A checkpoint has no action; everything else runs below.
        if dry_run and verb in MUTATING:
            print(f"  Dry run — would {would_do(statement)}; "
                  f"nothing was changed.")
            print("  (Add --execute to actually do it.)")
        else:
            try:
                run_statement(statement, root, paths)
            except OSError as error:
                # The decision line already said allowed. Say what
                # failed and carry on with the next statement.
                print(f"  Failed: {error}")
                if execute:
                    outcome = f"failed: {error}"
            else:
                if execute:
                    outcome = ("recorded-not-sent"
                               if verb == "SHARE" else "done")
    elif dry_run and decision == "needs-approval" \
            and verb in MUTATING:
        # A dry run never asks, but it still says what would happen.
        print(f"  Dry run — would {would_do(statement)} once a "
              f"human approves it; nothing was changed.")
    if outcome is not None:
        log.write(json.dumps(outcome_record(statement, paths, root,
                                           outcome)) + "\n")
        log.flush()

    return logged


def default_approver():
    """Who is approving, when nobody says: the login name."""
    try:
        return getpass.getuser()
    except Exception:
        return "reader"


def verb_phrase(record):
    """Plain words for what a log line is about, present tense."""
    verb = record.get("verb", "")
    path = record.get("path", "")
    dest = record.get("destination", "")
    who = record.get("recipient", "")
    if verb == "SEARCH":
        return f"search {path}"
    if verb == "READ":
        return f"read {path}"
    if verb == "SHARE":
        return f"share {path} with {who}" if who else f"share {path}"
    if verb == "COPY":
        return f"copy {path} to {dest}"
    if verb == "MOVE":
        return f"move {path} to {dest}"
    if verb == "DELETE":
        return f"delete {path}"
    if verb == "RENAME":
        return f"rename {path} to {dest}"
    if verb == "SUMMARIZE":
        return f"summarize {path}"
    if verb == "EXTRACT":
        return f"extract {path}"
    if verb == "REQUIRE":
        return "the checkpoint"
    return record.get("statement", "").lower() or "do something"


def verb_phrase_past(record):
    """Plain words for what a log line is about, past tense."""
    verb = record.get("verb", "")
    path = record.get("path", "")
    dest = record.get("destination", "")
    who = record.get("recipient", "")
    if verb == "SEARCH":
        return f"searched {path}"
    if verb == "READ":
        return f"read {path}"
    if verb == "SHARE":
        return f"shared {path} with {who}" \
            if who else f"shared {path}"
    if verb == "COPY":
        return f"copied {path} to {dest}"
    if verb == "MOVE":
        return f"moved {path} to {dest}"
    if verb == "DELETE":
        return f"deleted {path}"
    if verb == "RENAME":
        return f"renamed {path} to {dest}"
    if verb == "SUMMARIZE":
        return f"summarized {path}"
    if verb == "EXTRACT":
        return f"extracted {path}"
    return record.get("statement", "").lower() or "did something"


def read_log_line(record):
    """One plain sentence for one audit line, or None to skip it.

    A decision line says what was decided; an outcome line says
    what happened after. Both read in plain words, so a
    non-engineer can read the log without reading JSON.
    """
    if "outcome" in record:
        outcome = record["outcome"]
        if outcome == "done":
            return (f"It happened: the runtime "
                    f"{verb_phrase_past(record)}.")
        if outcome == "recorded-not-sent":
            return (f"Recorded, not sent: the runtime recorded "
                    f"{verb_phrase(record)}, and nothing was sent.")
        if outcome == "not-done-no-model":
            return (f"Not done: the runtime needs a language model "
                    f"to {verb_phrase(record)}, and this toy has "
                    f"none.")
        if outcome.startswith("failed:"):
            error = outcome[len("failed:"):].strip()
            return (f"It failed: the runtime tried to "
                    f"{verb_phrase(record)} and it failed "
                    f"({error}).")
        return None
    if "decision" in record:
        decision = record["decision"]
        who = record.get("approver", "-")
        if record.get("verb") == "REQUIRE":
            if decision == "approved-by-human":
                name = who if who not in ("-", "auto") else "a human"
                return f"{name} approved the checkpoint."
            if decision == "auto-approved":
                return ("Auto-approved for the demo, no human "
                        "involved: the checkpoint.")
            if decision == "denied":
                return "The runtime refused the checkpoint: a human said no."
            if decision == "needs-approval":
                return ("The runtime would stop and ask a human at "
                        "the checkpoint.")
            if decision.startswith("dry-run:"):
                return ("Dry run: the runtime would stop and ask a "
                        "human at the checkpoint, but nothing was "
                        "changed.")
            return None
        if decision == "allowed":
            return (f"The runtime allowed this: "
                    f"{verb_phrase(record)}.")
        if decision == "denied":
            return (f"The runtime refused this: "
                    f"{verb_phrase(record)}.")
        if decision == "approved-by-human":
            name = who if who not in ("-", "auto") else "a human"
            return f"{name} approved this: {verb_phrase(record)}."
        if decision == "auto-approved":
            return (f"Auto-approved for the demo, no human involved: "
                    f"{verb_phrase(record)}.")
        if decision == "needs-model":
            return (f"Not done: the runtime needs a language model "
                    f"to {verb_phrase(record)}, and this toy has "
                    f"none.")
        if decision == "needs-approval":
            return (f"The runtime would stop and ask a human before "
                    f"{verb_phrase(record)}.")
        if decision.startswith("dry-run:"):
            inner = decision[len("dry-run:"):]
            if inner == "needs-approval":
                return (f"Dry run: the runtime would stop and ask a "
                        f"human before {verb_phrase(record)}.")
            return (f"Dry run: the runtime would "
                    f"{verb_phrase(record)}, but nothing was changed.")
        return None
    return None


def read_log_file(path):
    """Print an audit log in plain words, one sentence per line.

    Malformed lines are skipped with a plain note, never a crash.
    This mode changes nothing.
    """
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except OSError as error:
        die(f"cannot read log file: {error}")
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            print(f"Skipped: line {number} is not a readable audit line.")
            continue
        if not isinstance(record, dict) or "verb" not in record \
                or ("decision" not in record
                    and "outcome" not in record):
            print(f"Skipped: line {number} is not a readable audit line.")
            continue
        sentence = read_log_line(record)
        if sentence is None:
            print(f"Skipped: line {number} is not a readable audit line.")
            continue
        print(sentence)


def main():
    parser = argparse.ArgumentParser(
        description="HNIC toy runtime: run a script of statements "
                    "against a policy, in a real folder.")
    parser.add_argument("--read-log", default=None, metavar="LOG",
                        help="read an audit log and print it in plain "
                             "words; takes only the log path")
    parser.add_argument("--policy", default=None,
                        help="policy file, written in HNIC")
    parser.add_argument("--script", default=None,
                        help="script file: one statement per line")
    parser.add_argument("--root", default=None,
                        help="folder the statements act on")
    parser.add_argument("--audit", default="audit.log",
                        help="audit trail file (default: audit.log)")
    parser.add_argument("--auto-approve", action="store_true",
                        help="approve every REQUIRE prompt automatically "
                             "(demo runs only)")
    parser.add_argument("--approver", default=None,
                        help="name recorded on approvals "
                             "(default: the login name)")
    parser.add_argument("--execute", action="store_true",
                        help="actually perform the allowed actions; "
                             "without it the run is dry and changes "
                             "nothing")
    args = parser.parse_args()

    if args.read_log is not None:
        read_log_file(args.read_log)
        return
    if not args.policy or not args.script or not args.root:
        parser.error("--policy, --script, and --root are required "
                     "unless --read-log is given")

    root = os.path.realpath(args.root)
    if not os.path.isdir(root):
        die(f"no such folder: {args.root}")
    try:
        rules = parse_policy(args.policy)
    except OSError as error:
        die(f"cannot read policy file: {error}")
    try:
        statements = parse_script(args.script)
    except OSError as error:
        die(f"cannot read script file: {error}")

    approver = args.approver or default_approver()

    print(f"Policy: {args.policy} ({len(rules)} rules)")
    print(f"Script: {args.script} ({len(statements)} statements)")
    print(f"Folder: {args.root}")
    if args.execute:
        print("Mode: EXECUTE — allowed actions will actually happen.")
    else:
        print("Mode: DRY RUN — nothing will change. "
              "Add --execute to actually do it.")
    print(f"Approver: {approver}")
    print()

    with open(args.audit, "a") as log:
        for statement in statements:
            run_one(statement, rules, root, log, args.execute,
                    args.auto_approve, approver)
            print()

    print(f"Audit trail: {args.audit}")


if __name__ == "__main__":
    main()
