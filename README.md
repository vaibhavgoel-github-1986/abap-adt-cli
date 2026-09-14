# abap-adt-cli

Fast ABAP development from the command line, built directly on the SAP **ADT REST API** —
the same API Eclipse/ADT uses.

```bash
adt pull ZMY_PACKAGE          # parallel download into a local folder
vim src/zcl_thing.clas.abap   # edit with whatever you like
adt status                    # what changed, offline
adt status --remote           # ...and what changed on the server since your pull
adt push --transport DHAK900123
```

## Why

Four things, in order of importance.

**Correct transport entries.** Editing one method records exactly one
`LIMU METH` entry, the same as Eclipse. The class is not locked as a whole, so a
colleague can work on a different method in a different transport, and importing
your transport will not revert theirs.

```
DHAK907236   LIMU   METH   ZCL_TSTMP_UTILITIES           GET_TZ_OFFSET
```

**No silent overwrites.** Push refuses to clobber work that appeared on the
server after your pull, and refuses to auto-generate a transport behind your
back. See [Guard rails](#guard-rails).

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

Works on macOS, Linux and Windows. Credentials go to the native store on each —
Keychain, Secret Service, or Windows Credential Manager.

## Commands

| Command | Purpose |
| --- | --- |
| `adt init` / `login` / `logout` / `systems` | connection profiles and credentials |
| `adt ping` | check the endpoint is reachable |
| `adt pull [PACKAGE]` | download a package, in parallel |
| `adt status` | locally modified objects, offline |
| `adt status --remote` | also report what changed on the server |
| `adt push` | upload changed objects |

Run `adt pull` with no arguments inside an existing workspace to refresh it. Pull
refuses to discard local edits unless you pass `--force`.

Layout is flat `src/` by default; `--se80` mirrors the SE80 object tree.

## Guard rails

**A transport is required.** Pushing to a transportable package without
`--transport` is refused, rather than letting SAP quietly generate a
`Generated Request for Change Recording` you never asked for. Local `$` packages
(`$TMP` and friends) are never transported, so they push without one.

```console
$ adt push
error package ZGET_SUBS_API_V2 is transportable - pass --transport <TR>
      (only local $ packages can push without one)
```

**Concurrent edits are detected.** The manifest records the hash of exactly what
the server handed you at pull time, so re-fetching and re-hashing shows whether
anyone touched the object since — SE80, Eclipse, or another push. Status marks
three cases:

| Marker | Meaning |
| --- | --- |
| `M` | you changed it |
| `R` | somebody else changed it on the server, you did not |
| `C` | conflict — changed in both places |

```console
$ adt status --remote
  C  src/zcl_tstmp_utilities.clas.abap  (also changed on server)

1 modified, 0 deleted, 1 changed on server
1 conflict(s) - push will refuse these until you re-pull
```

Push performs the same check and stops before writing anything:

```console
$ adt push --transport DHAK900123
  C  src/zcl_tstmp_utilities.clas.abap also changed on dha-110 since your pull
error 1 object(s) changed on the server - pushing would overwrite that work.
      Re-pull to inspect, or use --force to overwrite.
```

Resolve it by re-pulling and reapplying your edit, or override with `--force`
if you know your copy should win. `--dry-run` shows what would be sent without
sending it.

This costs one read round trip per modified object before the write; `--force`
skips the check.

## How it works

```
adt pull   →  POST /repository/informationsystem/virtualfolders/contents   (1 request)
           →  GET  <object>/source/main                                    (parallel)

adt push   →  GET  <object>/source/main                 (drift check, parallel)
           →  POST <object>?_action=LOCK                (stateful, serialised)
           →  PUT  <object>/source/main?lockHandle=...&corrNr=...
           →  POST <object>?_action=UNLOCK
```

Reads are stateless so they parallelise. Locks are session-bound, so writes are
serialised and the session always returns to stateless afterwards, releasing any
server-side enqueue.

Transport granularity is not something this CLI implements — it falls out of
using the same API Eclipse does. SAP decides which includes actually changed and
records those.

ADT serves CRLF; files are stored with LF locally so a pull/push round trip does
not make every object look modified.

## Object types

Source-based objects are pulled as text: classes, interfaces, programs, includes,
function modules, CDS data definitions, metadata extensions, access controls,
behavior definitions, service definitions.

Non-source objects — service bindings, message classes, packages — are pulled as
their ADT metadata XML instead of source text, and can't be pushed back. Adding
a type is one line in `objects.py`.

## Status

Early. Pull, status and push are working and verified against a real system,
including method-level transport entries, the transport guard and conflict
detection.

Not yet implemented: activation after push, where-used, syntax check, unit test
runs, transport creation, object creation and deletion. Pushed objects stay
inactive until you activate them elsewhere.

## Licence

MIT
