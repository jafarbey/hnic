# HNIC Toy Runtime

The first runnable piece of HNIC (Human-Negotiated Intelligence Control).
A toy, not a product — under 800 lines of Python that prove the
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
- `audit.log` — created when you run it. One append-only line per
  statement: when it ran, the resolved path actually touched, the
  decision, and who approved it (`-` when nobody did, `auto` when
  `--auto-approve` answered). The line is written *before* the action
  runs, so a crash still leaves the record.
- `DEMO-TRANSCRIPT.md` — the recorded output of a real demo run.

## How the decision works

For every statement, in this order:

1. A matching DENY rule refuses it.
2. A matching REQUIRE rule stops and asks a human.
3. An approval satisfies the REQUIRE rule, not the ALLOW rule:
   every folder the statement touches still needs an ALLOW.
4. No matching rule refuses it. Silence is not permission.

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

Approvals are recorded with a name on them: `--approver NAME` names the
approver (the login name when not given). `--auto-approve` records
`auto-approved`, never `approved-by-human`, and `auto` in the approver
column, because no human approved anything. The audit line records the
resolved path actually touched, not the path as written.

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
