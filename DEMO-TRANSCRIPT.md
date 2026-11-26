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
Policy: examples/policy.hnic (5 rules)
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
  `approved-by-human`).
- READ of the salary sheet was refused by the folder-scoped rule.
- COPY and DELETE were refused by the policy.
- Nothing was copied, moved, or deleted: `test-folder/` is unchanged
  after the run, and no `/shared` folder was created.

## The audit trail

`audit.log` after the demo run, one line per statement. The line is
written before the action runs, and an approval carries the name of
the person who approved it:

```
2026-11-26T20:45:38 | SEARCH files IN /clients | allowed | -
2026-11-26T20:45:38 | SHARE /clients/contract-acme.txt WITH Ada Lovelace | auto-approved | root
2026-11-26T20:45:38 | READ /finance/salaries.csv | denied | -
2026-11-26T20:45:38 | COPY /clients/contract-acme.txt TO /shared | denied | -
2026-11-26T20:45:38 | DELETE /clients/acme-contact.txt | denied | -
```

## The dry run

The same command without `--execute` changes nothing. SEARCH and READ
still run, because they change nothing; the rest are only described:

```
Mode: DRY RUN — nothing will change. Add --execute to actually do it.

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
(`python3 test_hnic.py` — 8 tests).

**A — the approval prompt really asks.** Same demo, no
`--auto-approve`, with `y` piped into the prompt: the SHARE was
recorded `approved-by-human`. With `n` piped in: the SHARE was
`denied` (human said no) and nothing was shared.

**B — the stubs and the checkpoint.** A policy allowing SUMMARIZE,
EXTRACT, and READ, run against a script with one of each plus a bare
`REQUIRE approval` line: SUMMARIZE and EXTRACT both came back
`needs-model` ("Needs a language model behind it — this toy has none,
so nothing was done"), the checkpoint stopped and asked the human, and
the READ printed the file.

**C — allowed actions really act.** A policy allowing COPY, RENAME,
MOVE, DELETE, and SEARCH, run on a scratch copy of the test folder:
COPY created the copy in `/archive`, RENAME renamed the contact file,
MOVE moved the contract into `/archive`, DELETE removed the renamed
contact file, and a final SEARCH listed exactly what was left
(`/archive/contract-acme.txt` in `/archive`, nothing in `/clients`).

**D — the `..` bypass and the symlink escape are both denied.** A
policy allowing READ in `/clients` and denying it in `/finance`, run
against `READ /clients/../finance/salaries.csv`: denied, and the
salary file was never printed. A symlink inside the root pointing
outside it, read as `/clients/link/secret.txt`: denied as a path
escaping the folder. A `SEARCH files IN /clients/../finance`: denied,
because no rule allows searching `/finance`.

**E — the destination is checked too.** A policy allowing COPY and
MOVE in `/clients` and denying them in `/shared`: a COPY into
`/shared` was denied and nothing was copied; a MOVE into `/shared`
was denied and the file stayed put. A COPY to a destination outside
the root was denied as well.
