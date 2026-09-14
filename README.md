# abap-adt-cli

Fast ABAP development from the command line, built directly on the SAP **ADT REST API** —
the same API Eclipse/ADT uses.

```bash
adt pull ZMY_PACKAGE          # parallel download into a local folder
vim src/zcl_thing.clas.abap   # edit with whatever you like
adt status                    # what changed, offline
adt push --transport DHAK900123
```

## Why

Three things, in order of importance.

**Correct transport entries.** Editing one method records exactly one
`LIMU METH` entry, the same as Eclipse. The class is not locked as a whole, so a
colleague can work on a different method in a different transport, and importing
your transport will not revert theirs.

```
DHAK907236   LIMU   METH   ZCL_TSTMP_UTILITIES           GET_TZ_OFFSET
```

**No source through an LLM.** You edit files on disk; the CLI moves bytes over
HTTPS. Nothing is pasted into a model context, so nothing is spent on tokens.

**Speed.** Enumeration is a single request; every source fetch runs in parallel.
A 30-object package lands in about 2.3 seconds.

## Install

```bash
pipx install "git+https://github.com/vaibhavgoel-github-1986/abap-adt-cli"
adt init          # host, user, client
adt login         # password goes to the OS keychain, never to disk
adt ping
```

Requires Python 3.10+ and a SAP user with `S_DEVELOP`. Nothing is installed in
the SAP system — ADT is already there.

## Commands

| Command | Purpose |
| --- | --- |
| `adt init` / `login` / `systems` | connection profiles and credentials |
| `adt ping` | check the endpoint is reachable |
| `adt pull [PACKAGE]` | download a package, in parallel |
| `adt status` | locally modified objects, offline |
| `adt push` | upload changed objects |

Run `adt pull` with no arguments inside an existing workspace to refresh it. Pull
refuses to discard local edits unless you pass `--force`.

Layout is flat `src/` by default; `--se80` mirrors the SE80 object tree.

## How it works

```
adt pull   →  POST /repository/informationsystem/virtualfolders/contents   (1 request)
           →  GET  <object>/source/main                                    (parallel)

adt push   →  POST <object>?_action=LOCK        (stateful, serialised)
           →  PUT  <object>/source/main?lockHandle=...&corrNr=...
           →  POST <object>?_action=UNLOCK
```

Reads are stateless so they parallelise. Locks are session-bound, so writes are
serialised and the session always returns to stateless afterwards, releasing any
server-side enqueue.

Transport granularity is not something this CLI implements — it falls out of
using the same API Eclipse does. SAP decides which includes actually changed and
records those.

## Object types

Source-based objects are pulled as text: classes, interfaces, programs, includes,
function modules, CDS data definitions, metadata extensions, access controls,
behavior definitions, service definitions.

Non-source objects — service bindings, message classes, packages — are pulled as
their ADT metadata XML instead of source text, and can't be pushed back. Adding
a type is one line in `objects.py`.

## Status

Early. Pull, status and push are working and verified against a real system.
Not yet implemented: activation after push, where-used, syntax check, unit test
runs, transport creation, object creation and deletion.

## Licence

MIT
