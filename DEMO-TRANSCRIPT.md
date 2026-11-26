# Demo Transcript

Recorded from real runs on November 26, 2026. The demo command first,
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

`audit.log` after the demo run, one line per statement. The line is
written before the action runs, and it records the resolved path
actually touched — not the path as written — plus the approver
(`auto` when `--auto-approve` answered, a name when a human did):

```
2026-11-26T21:03:23 | /clients | allowed | -
2026-11-26T21:03:23 | /clients/contract-acme.txt | auto-approved | auto
2026-11-26T21:03:23 | /finance/salaries.csv | denied | -
2026-11-26T21:03:23 | /clients/contract-acme.txt -> /shared/contract-acme.txt | denied | -
2026-11-26T21:03:23 | /clients/acme-contact.txt | denied | -
```

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
15 tests).

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

Audit lines from the two runs:

```
2026-11-26T21:03:42 | /clients/contract-acme.txt | approved-by-human | root
2026-11-26T21:03:42 | /clients/contract-acme.txt | denied | -
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
contract
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
```

**F — the second review's fixes, shown live.** Overwriting is
refused: with a file already at the destination, COPY, MOVE, and
RENAME are all denied — even renaming a file to its own name — and
the existing file is untouched.

```
COPY /clients/a.txt TO /shared
  DENIED — destination already exists — refusing to overwrite

MOVE /clients/a.txt TO /shared
  DENIED — destination already exists — refusing to overwrite

RENAME /clients/a.txt TO b.txt
  DENIED — destination already exists — refusing to overwrite

RENAME /clients/a.txt TO a.txt
  DENIED — destination already exists — refusing to overwrite
```

An approval still needs an ALLOW: a policy with REQUIRE approval
BEFORE SHARE and no ALLOW SHARE rule, run with `--auto-approve` —
the human approves, and the statement is still denied, because
approval satisfies the REQUIRE rule, not the ALLOW rule.

```
SHARE /clients/contract.txt WITH Ada Lovelace
  (auto-approved for the demo)
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
hnic: /tmp/regen/scratch/f6b-policy.hnic:1: expected 'ALLOW <verb> [IN <folder>]'
```

exit: 2
