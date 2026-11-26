# HNIC Toy Runtime

The first runnable piece of HNIC (Human-Negotiated Intelligence Control).
A toy, not a product — a few hundred lines of Python that prove the
language runs. The book about it is *The Exact Frame*, by J.B. Bey.

## The idea in one sentence

The agent is only the script of statements you hand to the runtime — it
never touches files directly; the runtime owns access to the folder and
carries out the allowed work itself.

## Run the demo

From this folder, one command:

```
python3 hnic.py --policy examples/policy.hnic --script examples/demo.hnic --root test-folder --auto-approve
```

The demo is the ten-minute flow from Chapter 3: search one folder, share
one file with approval, a blocked copy, a blocked delete — plus one blocked
read, to show the folder-scoped DENY rule working. `--auto-approve`
answers the approval prompt for you; leave it off and the runtime stops
and asks you in the terminal.

## What each file does

- `hnic.py` — the runtime. Reads a policy file and a script, checks every
  statement against the policy *before* acting, and writes the audit trail.
- `examples/policy.hnic` — the policy, in HNIC: search is allowed in
  `/clients`, sharing needs approval, delete and copy are denied, reading
  `/finance` is denied.
- `examples/demo.hnic` — the demo script: one statement per line.
- `test-folder/` — the folder the statements act on: a client file, a
  contract, a finance folder with a salary sheet, a tax return.
- `audit.log` — created when you run it. One append-only line per
  statement: when it ran, the statement, and the decision (allowed /
  denied / approved-by-human / needs-model).
- `DEMO-TRANSCRIPT.md` — the recorded output of a real demo run.

## How the decision works

For every statement, in this order:

1. A matching DENY rule refuses it.
2. A matching REQUIRE rule stops and asks a human.
3. A matching ALLOW rule lets it run.
4. No matching rule refuses it. Silence is not permission.

## Honest limits

- SUMMARIZE and EXTRACT are stubs. They print that they need a language
  model behind them and do not pretend to work.
- SHARE is recorded, not sent — there are no Dropbox or Drive adapters yet.
- Approval is a terminal prompt, not a signed approval flow.
- The policy language is small on purpose: ALLOW, DENY, and REQUIRE
  approval, with an optional IN folder. That is the whole toy.
