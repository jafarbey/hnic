# Demo Transcript

Recorded from real runs on November 27, 2026. The demo command first,
then the dry run, then the extra checks that prove the rest of the
runtime.

## The demo

Command (from `~/workspace/hnic-toy-runtime/`):

```
python3 hnic.py --policy examples/policy.hnic --script examples/demo.hnic --root test-folder --auto-approve --execute
```

Output:

```
Policy: examples/policy.hnic (6 rules)
Script: examples/demo.hnic (5 statements)
Folder: test-folder
Mode: EXECUTE — allowed actions will actually happen.
Approver: root

SEARCH files IN /clients
  ALLOWED — policy says ALLOW SEARCH IN /clients
  /clients/acme-contact.txt
  /clients/contract-acme.txt

SHARE /clients/contract-acme.txt WITH Ada Lovelace
  (auto-approved for the demo)
  AUTO-APPROVED — auto-approved for the demo (policy says REQUIRE approval BEFORE SHARE)
  Shared /clients/contract-acme.txt with Ada Lovelace (recorded — no adapter yet, nothing was sent).

READ /finance/salaries.csv
  DENIED — policy says DENY READ IN /finance

COPY /clients/contract-acme.txt TO /shared
  DENIED — policy says DENY COPY

DELETE /clients/acme-contact.txt
  DENIED — policy says DENY DELETE

Audit trail: audit.log
```

What worked, what was refused:

- SEARCH returned both client files.
- SHARE stopped for approval (auto-approved in this run with
  `--auto-approve`, and recorded as `auto-approved`, not
  `approved-by-human`), and the policy now also allows sharing in
  `/clients` — an approval satisfies the REQUIRE rule, not the ALLOW
  rule.
- READ of the salary sheet was refused by the folder-scoped rule.
- COPY and DELETE were refused by the policy.
- Nothing was copied, moved, or deleted: `test-folder/` is unchanged
  after the run, and no `/shared` folder was created.

## The audit trail

`audit.log` after the demo run, one JSON object per line. The first
line for a statement is the decision, written before the action
runs, and it names the verb, the resolved path actually touched
(and the destination for COPY, MOVE, and RENAME), the recipient
for SHARE, the decision, and the approver (`auto` when
`--auto-approve` answered, a name when a human did). After an
allowed or approved action is attempted, a second line records the
outcome — `done`, or `recorded-not-sent` for a SHARE — so the log
says what happened, not just what was decided. Denied statements
get no outcome line: nothing was attempted. Because each line is
JSON, a path holding ` | ` or quotes is just string content — it
cannot forge a new column:

```
{"timestamp": "2026-11-27T17:12:03", "statement": "SEARCH files IN /clients", "verb": "SEARCH", "decision": "allowed", "approver": "-", "path": "/clients"}
{"timestamp": "2026-11-27T17:12:03", "statement": "SEARCH files IN /clients", "verb": "SEARCH", "outcome": "done", "path": "/clients"}
{"timestamp": "2026-11-27T17:12:03", "statement": "SHARE /clients/contract-acme.txt WITH Ada Lovelace", "verb": "SHARE", "decision": "auto-approved", "approver": "auto", "path": "/clients/contract-acme.txt", "recipient": "Ada Lovelace"}
{"timestamp": "2026-11-27T17:12:03", "statement": "SHARE /clients/contract-acme.txt WITH Ada Lovelace", "verb": "SHARE", "outcome": "recorded-not-sent", "path": "/clients/contract-acme.txt", "recipient": "Ada Lovelace"}
{"timestamp": "2026-11-27T17:12:03", "statement": "READ /finance/salaries.csv", "verb": "READ", "decision": "denied", "approver": "-", "path": "/finance/salaries.csv"}
{"timestamp": "2026-11-27T17:12:03", "statement": "COPY /clients/contract-acme.txt TO /shared", "verb": "COPY", "decision": "denied", "approver": "-", "path": "/clients/contract-acme.txt", "destination": "/shared/contract-acme.txt"}
{"timestamp": "2026-11-27T17:12:03", "statement": "DELETE /clients/acme-contact.txt", "verb": "DELETE", "decision": "denied", "approver": "-", "path": "/clients/acme-contact.txt"}
```

A READ and a SHARE no longer look the same in the log: the SHARE
line carries `"verb": "SHARE"` and `"recipient": "Ada Lovelace"`,
while the READ line carries `"verb": "READ"` and no recipient field
at all. And the SHARE now has two lines: the decision says it was
auto-approved, and the outcome line says it was recorded and
nothing was sent.

## The dry run

The same command without `--execute` changes nothing. SEARCH and READ
still run, because they change nothing; the rest are only described:

```
Policy: examples/policy.hnic (6 rules)
Script: examples/demo.hnic (5 statements)
Folder: test-folder
Mode: DRY RUN — nothing will change. Add --execute to actually do it.
Approver: root

SEARCH files IN /clients
  DRY-RUN:ALLOWED — policy says ALLOW SEARCH IN /clients
  /clients/acme-contact.txt
  /clients/contract-acme.txt

SHARE /clients/contract-acme.txt WITH Ada Lovelace
  DRY-RUN:NEEDS-APPROVAL — would stop and ask a human (policy says REQUIRE approval BEFORE SHARE)
  Dry run — would share /clients/contract-acme.txt with Ada Lovelace once a human approves it; nothing was changed.

READ /finance/salaries.csv
  DRY-RUN:DENIED — policy says DENY READ IN /finance

COPY /clients/contract-acme.txt TO /shared
  DRY-RUN:DENIED — policy says DENY COPY

DELETE /clients/acme-contact.txt
  DRY-RUN:DENIED — policy says DENY DELETE
```

## Extra checks

These were run the same day against temporary policies and a scratch
copy of the test folder, so the demo folder stayed untouched. The
tests in `test_hnic.py` cover the same ground and all pass
(`python3 test_hnic.py` and `python3 -m unittest test_hnic -v` —
24 tests).

**A — the approval prompt really asks.** Same demo, no
`--auto-approve`, with `y` piped into the prompt: the SHARE was
recorded `approved-by-human`, with the approver's name on the line.
With `n` piped in: the SHARE was `denied` (human said no) and
nothing was shared.

```
SHARE /clients/contract-acme.txt WITH Ada Lovelace
  Approval needed before: SHARE /clients/contract-acme.txt WITH Ada Lovelace
  Approve? [y/n]   APPROVED-BY-HUMAN — human approved (policy says REQUIRE approval BEFORE SHARE)
```

Audit lines from the two runs — the `y` run names the approver, the
`n` run records the denial, and both name the verb and the recipient.
The `y` run has two SHARE lines: the decision, then the outcome.

```
{"timestamp": "2026-11-27T17:12:18", "statement": "SHARE /clients/contract-acme.txt WITH Ada Lovelace", "verb": "SHARE", "decision": "approved-by-human", "approver": "root", "path": "/clients/contract-acme.txt", "recipient": "Ada Lovelace"}
{"timestamp": "2026-11-27T17:12:18", "statement": "SHARE /clients/contract-acme.txt WITH Ada Lovelace", "verb": "SHARE", "outcome": "recorded-not-sent", "path": "/clients/contract-acme.txt", "recipient": "Ada Lovelace"}
{"timestamp": "2026-11-27T17:12:18", "statement": "SHARE /clients/contract-acme.txt WITH Ada Lovelace", "verb": "SHARE", "decision": "denied", "approver": "-", "path": "/clients/contract-acme.txt", "recipient": "Ada Lovelace"}
```

**B — the stubs and the checkpoint.** A policy allowing SUMMARIZE,
EXTRACT, and READ, run against a script with one of each plus a bare
`REQUIRE approval` line: SUMMARIZE and EXTRACT both came back
`needs-model` ("Needs a language model behind it — this toy has none,
so nothing was done"), the checkpoint stopped and asked the human, and
the READ printed the file.

```
SUMMARIZE /clients/contract-acme.txt
  NEEDS-MODEL — needs a language model behind it

EXTRACT /clients/contract-acme.txt
  NEEDS-MODEL — needs a language model behind it

REQUIRE approval
  Approval needed before: REQUIRE approval
  Approve? [y/n]   APPROVED-BY-HUMAN — human approved the checkpoint

READ /clients/contract-acme.txt
  ALLOWED — policy says ALLOW READ IN /clients
ACME CORP — SERVICE AGREEMENT
Acme Corp agrees to pay for consulting services at the rate set out
in Schedule A. This agreement runs for twelve months from signing.
Either side may end it with thirty days of written notice.
```

**C — allowed actions really act.** A policy allowing COPY, RENAME,
MOVE, DELETE, and SEARCH, run on a scratch copy of the test folder:
COPY created the copy in `/archive`, RENAME renamed the contact file,
MOVE moved the renamed contact into `/archive`, DELETE removed the
contract from `/clients`, and a final SEARCH listed exactly what was
left — nothing in `/clients`, both files in `/archive`.

```
COPY /clients/contract-acme.txt TO /archive
  ALLOWED — policy says ALLOW COPY IN /clients
  Copied /clients/contract-acme.txt to /archive.

RENAME /clients/acme-contact.txt TO acme-contact-v2.txt
  ALLOWED — policy says ALLOW RENAME IN /clients
  Renamed /clients/acme-contact.txt to acme-contact-v2.txt.

MOVE /clients/acme-contact-v2.txt TO /archive
  ALLOWED — policy says ALLOW MOVE IN /clients
  Moved /clients/acme-contact-v2.txt to /archive.

DELETE /clients/contract-acme.txt
  ALLOWED — policy says ALLOW DELETE IN /clients
  Deleted /clients/contract-acme.txt.

SEARCH files IN /clients
  ALLOWED — policy says ALLOW SEARCH IN /clients
  Nothing in /clients.

SEARCH files IN /archive
  ALLOWED — policy says ALLOW SEARCH IN /archive
  /archive/acme-contact-v2.txt
  /archive/contract-acme.txt
```

**D — the `..` bypass and the symlink escape are both denied.** A
policy allowing READ in `/clients` and denying it in `/finance`, run
against `READ /clients/../finance/salaries.csv`: denied, and the
salary file was never printed. A symlink inside the root pointing
outside it, read as `/clients/link/secret.txt`: denied as a path
escaping the folder. A `SEARCH files IN /clients/../finance`: denied,
because no rule allows searching `/finance`.

```
READ /clients/../finance/salaries.csv
  DENIED — policy says DENY READ IN /finance

READ /clients/link/secret.txt
  DENIED — path escapes the folder: '/clients/link/secret.txt'

READ /clients/evil.txt
  DENIED — path escapes the folder: '/clients/evil.txt'

SEARCH files IN /clients/../finance
  DENIED — no rule allows it
```

**E — the destination is checked too.** A policy allowing COPY and
MOVE in `/clients` and denying them in `/shared`: a COPY into
`/shared` was denied and nothing was copied; a MOVE into `/shared`
was denied and the file stayed put. A COPY to a destination outside
the root was denied as well.

```
COPY /clients/contract.txt TO /shared
  DENIED — policy says DENY COPY IN /shared

MOVE /clients/contact.txt TO /shared
  DENIED — policy says DENY MOVE IN /shared

COPY /clients/contract.txt TO /../outside
  DENIED — destination escapes the folder: '/../outside'
```

**F — the second review's fixes, shown live.** Overwriting is
refused: with a file already at the destination, COPY, MOVE, and
RENAME are all denied — even renaming a file to its own name — and
the existing file is untouched.

```
COPY /clients/contract-acme.txt TO /shared
  DENIED — destination already exists — refusing to overwrite

MOVE /clients/contract-acme.txt TO /shared
  DENIED — destination already exists — refusing to overwrite

RENAME /clients/acme-contact.txt TO contract-acme.txt
  DENIED — destination already exists — refusing to overwrite

RENAME /clients/acme-contact.txt TO acme-contact.txt
  DENIED — destination already exists — refusing to overwrite
```

An approval still needs an ALLOW — and now the runtime does not
even ask. A policy with REQUIRE approval BEFORE SHARE and no ALLOW
SHARE rule, run with `--auto-approve`: the statement is refused
before anyone is asked, because the ALLOW check runs before the
REQUIRE prompt. The decision is the same as before — denied, "no
rule allows it" — but no human is asked to approve what the policy
would refuse anyway. The audit log holds exactly one line for it,
the decision; nothing was attempted, so there is no outcome line.

```
SHARE /clients/contract.txt WITH Ada Lovelace
  DENIED — no rule allows it
```

Case-insensitive matching: `READ /FINANCE/salaries.csv` is denied
by the rule written for `/finance`.

```
READ /FINANCE/salaries.csv
  DENIED — policy says DENY READ IN /finance
```

Quoted paths with spaces work:

```
READ "/clients/my file.txt"
  ALLOWED — policy says ALLOW READ IN /clients
spaced
```

And a malformed policy line fails closed with a clear one-line
error, not a traceback:

```
hnic: /tmp/hnic-regen2/pbad.hnic:1: expected 'ALLOW <verb> [IN <folder>]'
```

exit: 2

**G — a forged path cannot forge a column.** A file named
`x | auto-approved | auto.txt`, read with a policy allowing READ in
`/clients`: the read works, and the audit line is one JSON object
whose `path` field holds the whole name as string content. The
forged ` | auto-approved | auto` adds no column and no field:

```
READ "/clients/x | auto-approved | auto.txt"
  ALLOWED — policy says ALLOW READ IN /clients
forged
```

```
{"timestamp": "2026-11-27T17:12:38", "statement": "READ \"/clients/x | auto-approved | auto.txt\"", "verb": "READ", "decision": "allowed", "approver": "-", "path": "/clients/x | auto-approved | auto.txt"}
{"timestamp": "2026-11-27T17:12:38", "statement": "READ \"/clients/x | auto-approved | auto.txt\"", "verb": "READ", "outcome": "done", "path": "/clients/x | auto-approved | auto.txt"}
```

Parsed back, the decision line is one object with exactly six
fields — `timestamp`, `statement`, `verb`, `decision`, `approver`,
`path` — and the decision is the real one, `allowed`. The outcome
line carries the same path and says `done`. The forged words
changed nothing in either line, because they were never a column.
They were a file name, inside a field, where they belong.

**H — the outcome lines, and reading the log back.** The demo run
above left two outcome lines in `audit.log`, one per action that
was attempted: the SEARCH says `done`, the SHARE says
`recorded-not-sent`. The three denied statements have no outcome
line at all.

```
{"timestamp": "2026-11-27T17:12:03", "statement": "SEARCH files IN /clients", "verb": "SEARCH", "outcome": "done", "path": "/clients"}
{"timestamp": "2026-11-27T17:12:03", "statement": "SHARE /clients/contract-acme.txt WITH Ada Lovelace", "verb": "SHARE", "outcome": "recorded-not-sent", "path": "/clients/contract-acme.txt", "recipient": "Ada Lovelace"}
```

`--read-log` prints the same log in plain words, one sentence per
line — a decision line says what was decided, an outcome line says
what happened after:

```
$ python3 hnic.py --read-log audit.log
The runtime allowed this: search /clients.
It happened: the runtime searched /clients.
Auto-approved for the demo, no human involved: share /clients/contract-acme.txt with Ada Lovelace.
Recorded, not sent: the runtime recorded share /clients/contract-acme.txt with Ada Lovelace, and nothing was sent.
The runtime refused this: read /finance/salaries.csv.
The runtime refused this: copy /clients/contract-acme.txt to /shared/contract-acme.txt.
The runtime refused this: delete /clients/acme-contact.txt.
```

A failure gets an outcome line too. With a file named `/archive`
in the way of the destination folder, an allowed COPY fails, and
the log holds both halves: the decision already said allowed, so
the outcome line must say it failed.

```
COPY /clients/contract.txt TO /archive
  ALLOWED — policy says ALLOW COPY IN /clients
  Failed: [Errno 17] File exists: '/tmp/hnic-regen2/htest/archive'

{"timestamp": "2026-11-27T17:12:47", "statement": "COPY /clients/contract.txt TO /archive", "verb": "COPY", "decision": "allowed", "approver": "-", "path": "/clients/contract.txt", "destination": "/archive/contract.txt"}
{"timestamp": "2026-11-27T17:12:47", "statement": "COPY /clients/contract.txt TO /archive", "verb": "COPY", "outcome": "failed: [Errno 17] File exists: '/tmp/hnic-regen2/htest/archive'", "path": "/clients/contract.txt", "destination": "/archive/contract.txt"}
```
