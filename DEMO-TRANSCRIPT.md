# Demo Transcript

Recorded from real runs on November 26, 2026. The demo command first,
then the extra checks that prove the rest of the runtime.

## The demo

Command (from `~/workspace/hnic-toy-runtime/`):

```
python3 hnic.py --policy examples/policy.hnic --script examples/demo.hnic --root test-folder --auto-approve
```

Output:

```
Policy: examples/policy.hnic (5 rules)
Script: examples/demo.hnic (5 statements)
Folder: test-folder

SEARCH files IN /clients
  ALLOWED — policy says ALLOW SEARCH IN /clients
  /clients/acme-contact.txt
  /clients/contract-acme.txt

SHARE /clients/contract-acme.txt WITH Ada Lovelace
  (auto-approved for the demo)
  APPROVED-BY-HUMAN — human approved (REQUIRE approval BEFORE SHARE)
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
  `--auto-approve`).
- READ of the salary sheet was refused by the folder-scoped rule.
- COPY and DELETE were refused by the policy.
- Nothing was copied, moved, or deleted: `test-folder/` is unchanged
  after the run, and no `/shared` folder was created.

## The audit trail

`audit.log` after the demo run, one line per statement:

```
2026-11-26T20:05:32 | SEARCH files IN /clients | allowed
2026-11-26T20:05:32 | SHARE /clients/contract-acme.txt WITH Ada Lovelace | approved-by-human
2026-11-26T20:05:32 | READ /finance/salaries.csv | denied
2026-11-26T20:05:32 | COPY /clients/contract-acme.txt TO /shared | denied
2026-11-26T20:05:32 | DELETE /clients/acme-contact.txt | denied
```

## Extra checks

These were run the same day against temporary policies and a scratch
copy of the test folder, so the demo folder stayed untouched.

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
MOVE moved it into `/archive`, DELETE removed it, and a final SEARCH
listed exactly what was left (`/archive/contract-acme.txt`).
