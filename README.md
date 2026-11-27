# HNIC Toy Runtime

The first runnable piece of HNIC (Human-Negotiated Intelligence Control).
A toy, not a product — about 1,000 lines of Python that prove the
language runs. The book about it is *The Exact Frame*, by J.B. Bey.

## The idea in one sentence

The agent is only the script of statements you hand to the runtime — it
never touches files directly; the runtime owns access to the folder and
carries out the allowed work itself.

## Run the demo

From this folder, one command:

```
python3 hnic.py --policy examples/policy.hnic --script examples/demo.hnic --root test-folder --auto-approve --execute
```

The demo is the ten-minute flow from Chapter 3: search one folder, share
one file with approval, a blocked copy, a blocked delete — plus one blocked
read, to show the folder-scoped DENY rule working. `--auto-approve`
answers the approval prompt for you; leave it off and the runtime stops
and asks you in the terminal.

## Dry run is the default

The book promises the examples run read-only unless the reader says
otherwise, and the toy keeps that promise: without `--execute`, the
runtime checks the policy, prints the decision and what it would do,
and logs it as `dry-run`, but it changes nothing. SEARCH and READ still
run in a dry run, because they change nothing. COPY, MOVE, DELETE,
RENAME, and SHARE are only described. Add `--execute` to actually
perform the allowed actions.

## What each file does

- `hnic.py` — the runtime. Reads a policy file and a script, checks every
  statement against the policy *before* acting, and writes the audit trail.
- `test_hnic.py` — the tests (stdlib unittest only). Run
  `python3 test_hnic.py` or `python3 -m unittest test_hnic -v`.
- `examples/policy.hnic` — the policy, in HNIC: search is allowed
  in `/clients`, sharing needs approval and is allowed in
  `/clients`, delete and copy are denied, reading `/finance` is
  denied.
- `examples/demo.hnic` — the demo script: one statement per line.
- `test-folder/` — the folder the statements act on: a client file, a
  contract, a finance folder with a salary sheet, a tax return.
- `audit.log` — created when you run it. One JSON object per line.
  The first line for a statement is the decision, written *before*
  the action runs, so a crash still leaves the record of what was
  decided. After an allowed or approved action is attempted, a
  second line records the outcome: `done`, `recorded-not-sent` for
  a SHARE (no adapter yet, so nothing leaves the machine), `failed:
  <error>` when the action raised, or `not-done-no-model` for
  SUMMARIZE and EXTRACT. Denied, dry-run, and needs-approval
  decisions get no outcome line — nothing was attempted. Each line
  names the verb, the resolved path actually touched (and the
  destination for COPY, MOVE, and RENAME), the recipient for
  SHARE, the decision, and who approved it (`-` when nobody did,
  `auto` when `--auto-approve` answered). A READ and a SHARE no
  longer look the same in the log: the SHARE line carries `"verb":
  "SHARE"` and `"recipient": "Ada Lovelace"`, while a READ line
  carries `"verb": "READ"` and no recipient field. Example lines:
  `{"timestamp": "2026-11-27T17:12:03", "statement": "SHARE
  /clients/contract-acme.txt WITH Ada Lovelace", "verb": "SHARE",
  "decision": "auto-approved", "approver": "auto", "path":
  "/clients/contract-acme.txt", "recipient": "Ada Lovelace"}` and
  `{"timestamp": "2026-11-27T17:12:03", "statement": "SHARE
  /clients/contract-acme.txt WITH Ada Lovelace", "verb": "SHARE",
  "outcome": "recorded-not-sent", "path":
  "/clients/contract-acme.txt", "recipient": "Ada Lovelace"}`.
  Read it back in plain words with `python3 hnic.py --read-log
  audit.log` — one plain sentence per line, so a non-engineer can
  read what happened without reading JSON.
- `DEMO-TRANSCRIPT.md` — the recorded output of a real demo run.

## How the decision works

For every statement, in this order:

1. A matching DENY rule refuses it.
2. Every folder the statement touches must match an ALLOW rule.
   Silence is not permission — a statement with no ALLOW rule is
   refused here, before anyone is asked, so a human is never
   asked to approve what the policy would refuse anyway.
3. A matching REQUIRE rule stops and asks a human.
4. Approved, or allowed outright, when every pair passed.

An approval satisfies the REQUIRE rule, not the ALLOW rule — but
the ALLOW check already ran, so nobody is asked to approve what
the policy refuses anyway.

COPY and MOVE are checked twice: the folder the file comes from and the
folder it goes to. RENAME is checked for both folders too. Every path is
resolved once, with realpath, against the real root folder, and that same
resolved path is used for the policy check and the action — so a `..` or
a symlink cannot slip a file past the rules. A path that escapes the
root folder is denied, never acted on. Policy matching ignores case, so
a rule written for `/finance` also catches `/FINANCE`. Paths may be
quoted when they contain spaces: `READ "/clients/my file.txt"`.

COPY, MOVE, and RENAME refuse when the destination already exists.
Overwriting is a backdoor delete, and this toy never overwrites — not
even renaming a file to its own name.

## Read the log back in plain words

`python3 hnic.py --read-log audit.log` reads a JSON-lines audit log
and prints one plain sentence per line: a decision line says what
was decided, an outcome line says what happened after. A SHARE
decision line becomes a sentence naming the file, the recipient,
and who approved it; its outcome line becomes a sentence saying
it was recorded and nothing was sent. A malformed line is skipped
with a plain note, never a crash. The mode takes only the log path
and changes nothing.

Approvals are recorded with a name on them: `--approver NAME` names the
approver (the login name when not given). `--auto-approve` records
`auto-approved`, never `approved-by-human`, and `auto` in the approver
field, because no human approved anything. The audit line records the
resolved path actually touched, not the path as written — and because
each line is one JSON object, a path holding ` | ` or quotes is just
string content: it cannot forge a new column. A file named
`x | auto-approved | auto.txt` logs as one object whose `path` field
holds the whole name, and nothing else changes.

A malformed policy line fails closed with a clear one-line error, not a
traceback: the policy is the safety core, so a half-read policy must
not run.

## Honest limits

- SUMMARIZE and EXTRACT are stubs. They print that they need a language
  model behind them and do not pretend to work.
- SHARE is recorded, not sent — there are no Dropbox or Drive adapters yet.
- Approval is a terminal prompt, not a signed approval flow.
- The policy language is small on purpose: ALLOW, DENY, and REQUIRE
  approval, with an optional IN folder. That is the whole toy.
