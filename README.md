# abap cli

Pull an ABAP package onto your laptop, edit it with whatever you like, push it
back under the right transport. Built directly on the SAP **ADT REST API** — the
same interface Eclipse/ADT uses, so nothing has to be installed in the SAP
system.

```bash
abap pull ZMY_PACKAGE --dest ~/Documents/my-package
abap status --remote
abap push --transport DHAK900123
```

## Why this exists

ABAP source lives behind SE80 and Eclipse. Getting it in front of modern tooling
— your editor, `grep`, `git diff`, an AI assistant — usually means copying it out
by hand and pasting it back.

Copilot with an SAP MCP server understands a system well, but it is a poor way to
*move* source. Every object read lands in the chat transcript, so a thirty-object
package burns context before you change a line, and the model becomes the
transport for your code.

Writing back is harder still. A correct write needs an ADT lock, a transport
number, and proof that nobody touched the object since you read it. Miss one and
you get a `Generated Request for Change Recording` nobody asked for, a
whole-class lock that blocks a colleague, or a silent overwrite.

`abap cli` does the transfer over HTTPS — no tokens, no paraphrasing in transit —
and leaves the thinking to you and your tools.

## Developer Workflow

```
┌──────────────┐
│     SAP      │
└──────┬───────┘
       │
       │  abap pull
       ▼
┌──────────────────────┐
│  Local ABAP Files    │
└─────────┬────────────┘
          │
          ▼
┌──────────────────────┐
│       VS Code        │
│                      │
│  Copilot / AI can:   │
│  • inspect code      │
│  • make changes      │
│  • refactor          │
│  • review changes    │
└─────────┬────────────┘
          │
          ▼
┌──────────────────────┐
│   Developer Review   │
│                      │
│  git diff            │
│  abap status         │
│  approve changes     │
└─────────┬────────────┘
          │
          ▼
     Push to SAP
          │
     ┌────┴───────────────┐
     │                    │
     ▼                    ▼
┌──────────────┐   ┌────────────────────┐
│   abap cli   │   │ SAP MCP / ADT /    │
│              │   │ Existing SAP Tools │
│ Source code  │   │                    │
│ pull / push  │   │ Operations not yet │
│ status       │   │ supported by CLI   │
│ drift check  │   │                    │
└──────┬───────┘   └─────────┬──────────┘
       │                     │
       └──────────┬──────────┘
                  │
                  ▼
             ┌─────────┐
             │   SAP   │
             └─────────┘
```

### Why hybrid push?

For normal source-code changes:

```bash
abap status
abap status --remote
abap push --transport DHAK900123
```

But some SAP operations may still require **MCP, ADT/Eclipse, SAP GUI, or other
SAP tooling**, for example:

- create a transport
- create/delete certain SAP objects
- maintain TVARVC entries
- perform SCC1N
- work with object types not yet supported by `abap cli`

So the intended model is:

```
Pull      → abap cli
Develop   → Local files + VS Code + AI
Review    → Developer
Push      → abap cli where supported
            +
            SAP MCP / SAP tools where required
```

> **`abap cli` is not trying to replace SAP MCP or ADT.**
> It removes the slow, token-heavy source transfer from the AI workflow and gives
> developers fast local ABAP files to work with.

## What you get

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

**Speed.** Enumeration is a single request; every source fetch runs in parallel.
A 30-object package lands in about 2.3 seconds.

**Nothing installed in SAP.** ADT is already there.

## Requirements

| | |
| --- | --- |
| Python | 3.10 or newer |
| SAP | ADT enabled — standard on NetWeaver and S/4 — and a user with `S_DEVELOP` |
| OS | macOS, Linux, Windows |
| Optional | VS Code's `code` command on `PATH`, so `--dest` can add the folder to your workspace |

The Python dependencies — `httpx`, `typer`, `rich`, `keyring` — are installed
for you.

## Install

```bash
pipx install "git+https://github.com/vaibhavgoel-github-1986/abap-adt-cli"
```

`pipx` keeps the CLI in its own virtualenv and puts `abap` on your `PATH`. Plain
`pip install` works too.

Then point it at a system and store the password once:

```bash
abap init          # name, host, user, client - prompts for anything you omit
abap login         # password goes to the OS keychain, never to disk
abap ping          # confirm ADT answers
```

That is enough to start. Multi-system, CI and keychain details are in
[Systems and credentials](#systems-and-credentials).

## Quick start

Pull a package into a folder you choose. With `--dest` that folder is also added
to your active VS Code workspace:

```bash
abap pull ZGET_SUBS_API_V2 --dest ~/Documents/ZGET_SUBS_API_V2
cd ~/Documents/ZGET_SUBS_API_V2
```

```console
connected to dha-110, listing ZGET_SUBS_API_V2...
pulling 30 objects ━━━━━━━━━━━━━━━━━━━━ 30/30 0:00:02
pulled ZGET_SUBS_API_V2 from dha-110 - 30 objects, 204,345 bytes in 2.3s
```

You now have ordinary files:

```
src/zcl_subs_query_provider.clas.abap
src/zcds_i_cont_billsch.ddls.asddls
src/zsd_get_subs_v2.srvd.srvdsrv
.adt/manifest.json        # what was pulled, from where, and its hashes
```

Edit them however you like, then see what you touched — offline and instant:

```console
$ abap status
ZGET_SUBS_API_V2 from dha-110, pulled 2026-09-14T17:37:49
  M  src/zcds_i_cont_billsch.ddls.asddls

1 modified, 0 deleted
```

Check nobody else moved, confirm the blast radius, then send it:

```bash
abap status --remote              # did anyone change these on the server?
abap push --dry-run               # what would be sent - makes no ADT calls
abap push --transport DHAK907258
```

```console
  M  src/zcds_i_cont_billsch.ddls.asddls
  pushed ZCDS_I_CONT_BILLSCH

1 object(s) pushed
```

Pushed objects stay **inactive** — activate them in Eclipse/ADT or SE80.

Three options are worth knowing from the start:

| Option | |
| --- | --- |
| `--system` / `-s` | which system, when you have more than one |
| `--trace` | print every ADT request and response, secrets redacted |
| `--help` | per-command help. It must come *after* the command: `abap pull --help` |

`--trace` is accepted on either side of the command:

```bash
abap push --transport DHAK900123 --trace
abap --trace push --transport DHAK900123
```

## Commands

| Command | Purpose |
| --- | --- |
| `abap init` / `login` / `logout` / `systems` | connection profiles and credentials |
| `abap ping` | check the endpoint is reachable |
| `abap pull [PACKAGE]` | download a package, in parallel |
| `abap pull [PACKAGE] --match / --type / --user` | download only part of one |
| `abap status` | locally modified objects, offline |
| `abap status --remote` | also report what changed on the server |
| `abap push` | upload changed objects |
| `abap version` | show the CLI version |

Every command that talks to SAP also takes `--system` / `-s` and `--trace`.

## Pulling

A *workspace* is any folder containing `.adt/manifest.json`. The manifest records
the package, the system, the layout, and one entry per object — its name, ADT
type, ADT URI and the SHA-256 of exactly what the server sent. Everything
`status` and `push` do later is derived from it, which is why no git repository
is required.

Where the files land depends on whether you pass `--dest` and whether you are
already standing in a workspace:

| Command | Run from | Result |
| --- | --- | --- |
| `abap pull ZMY_PACKAGE` | a plain folder | creates `./ZMY_PACKAGE/` (name upper-cased) |
| `abap pull ZMY_PACKAGE --dest ~/Documents/proj` | anywhere | uses `~/Documents/proj` exactly, then runs `code --add` on it |
| `abap pull` | a workspace | refreshes that workspace in place |
| `abap pull ZMY_PACKAGE` | that same package's workspace | refreshes in place, never nests a copy |
| `abap pull ZOTHER` | a different package's workspace | creates `./ZOTHER/` beside it |

So the two forms differ in three ways:

**`abap pull ZMY_PACKAGE`** derives the folder name from the package, always
underneath the current directory, and does not touch VS Code. Where you happen
to be standing decides where the code lands.

**`abap pull ZMY_PACKAGE --dest <path>`** puts it at exactly that path — any
name, anywhere on disk, absolute or relative — and after a successful pull runs
`code --add <path>` to add the folder to the active VS Code workspace. Install
VS Code's `code` command on your `PATH` to enable this; a missing command prints
a note and does not affect the pull.

Re-running `abap pull` inside a workspace is a refresh, and reuses the package,
system and layout from the manifest. It is deliberately conservative: if any
tracked file is locally modified or deleted, it lists them and stops rather than
overwriting your work. `--force` discards local changes and re-downloads.

| Option | Effect |
| --- | --- |
| `--dest` / `-d` | exact target folder, plus `code --add` |
| `--match` / `-m` | object name pattern, e.g. `'ZCL_SUBS*'` |
| `--type` / `-t` | comma-separated ADT types, e.g. `'CLAS,DDLS'` |
| `--user` / `-u` | object owner; defaults to you for `$` packages, `'*'` means everyone |
| `--se80` / `--flat` | SE80 object tree, or flat `src/` (default); a refresh keeps whatever the manifest recorded |
| `--jobs` / `-j` | parallel requests, default 16 |
| `--force` / `-f` | discard local modifications and overwrite |
| `--system` / `-s` | pull from a system other than the default, or the one recorded in the manifest |

Objects that fail to download are reported individually and do not abort the
pull; everything else still lands.

### Local objects: `$TMP`

Local packages work like any other, with one wrinkle: `$TMP` is a single package
shared by *every* developer on the system. On a busy system that is tens of
thousands of objects, most of them somebody else's.

So a `$` package defaults to **your** objects — the user the session is
authenticated as:

```console
$ abap pull '$TMP' --dest ~/Documents/my-local
connected to dha-110, listing $TMP owned by VAIBHAGO...
pulled $TMP from dha-110 - 69 objects, 273,395 bytes in 5.8s
```

Quote it. `$TMP` is a variable reference to your shell, and unquoted it expands
to nothing before the CLI ever sees it:

```bash
abap pull '$TMP'      # correct
abap pull "\$TMP"     # also fine
abap pull $TMP        # wrong - zsh/bash expand this to an empty string
```

To read somebody else's local objects, or everyone's:

```bash
abap pull '$TMP' --user ANOTHER_DEV --dest ~/Documents/ANOTHER_DEV
abap pull '$TMP' --user '*' --match 'ZCL_A*'     # everyone, narrowed by name
```

Add `--se80` to get the same grouped tree Eclipse shows under a user's `$TMP`,
which makes a folder per developer easy to browse side by side:

```bash
abap pull '$TMP' --se80 --dest ~/Documents/VAIBHAGO
abap pull '$TMP' --se80 --user ANOTHER_DEV --dest ~/Documents/ANOTHER_DEV
```

```
VAIBHAGO/
  Business Services/{Service Bindings,Service Definitions}   4
  Class Library/{Classes,Interfaces}                        22
  Core Data Services/{Data Definitions,Behavior,...}        15
  Dictionary/{Database Tables,Views}                         4
  Enhancements/                                              1
  Function Groups/Function Modules                           1
  Message Classes/                                           1
  Programs/                                                 21
```

Pushing back to `$TMP` needs no transport — local packages are never
transported. See [Guard rails](#guard-rails).

### Pulling part of a package

`--match` filters by object name on the server, `--type` by ADT object type.
They combine, and either one alone is enough:

```bash
abap pull ZGET_SUBS_API_V2 --type CLAS,INTF        # just the ABAP OO objects
abap pull ZGET_SUBS_API_V2 --match 'ZCDS_I_*'      # just those CDS views
abap pull ZGET_SUBS_API_V2 --match 'ZCL_SUBS*' --type CLAS
```

`--type` accepts either the short form (`CLAS`, `DDLS`) or the full ADT code
(`CLAS/OC`, `DDLS/DF`). If nothing matches, the pull stops with
`nothing in <PACKAGE> matched` rather than creating an empty workspace.

Filters are recorded in the manifest, so a later bare `abap pull` refreshes the
*same* narrow set instead of silently widening to the whole package. Override
them by passing the options again — `--match '*'` and `--user '*'` widen back
out.

## Pushing

Push only ever sends files whose hash differs from the manifest baseline, so an
edit to one method sends one object. It runs a series of checks in order, and
the first five happen before any network call:

1. **Manifest must exist** — otherwise there is no baseline to diff against.
2. **Deleted files are ignored**, with a note. Push never deletes objects in SAP.
3. **Nothing modified** — it exits saying so.
4. **Non-source objects are refused.** A modified `.msag.xml` or `.srvb.xml`
   aborts the whole push rather than pushing the rest silently.
5. **A transport is required** for transportable packages. See
   [Guard rails](#guard-rails).
6. **Drift check** — one parallel read per modified object, comparing the server
   copy against the baseline. Any conflict aborts before a single byte is
   written.
7. **Write loop** — for each object, serially: `LOCK`, `PUT` the source with
   `corrNr`, `UNLOCK`. The local baseline is re-hashed and the manifest saved
   after each object, so a failure halfway through does not make already-pushed
   objects look unpushed. Re-running pushes only what is left.

| Variation | What it does |
| --- | --- |
| `abap push` | refused on a transportable package — a transport is never invented for you |
| `abap push --transport DHAK900123` | the normal case: checks drift, then pushes every locally modified object under that TR |
| `abap push --dry-run` | lists exactly what would be sent and stops. Fully offline — it makes no ADT calls at all |
| `abap push --force` | skips the drift check and overwrites whatever is on the server. Use only when you know your copy should win |
| `abap push --dest <path>` | pushes the workspace at that path instead of the current directory |
| `abap push --system <name>` | only accepted when `<name>` is the system the workspace was pulled from; anything else is refused |
| `abap push --trace` | prints every lock, PUT and unlock with full payloads |

Pushed objects stay **inactive**. This CLI has no activation command yet, so
activate in Eclipse/ADT or SE80 afterwards.

## Guard rails

**A transport is required.** Pushing to a transportable package without
`--transport` is refused, rather than letting SAP quietly generate a
`Generated Request for Change Recording` you never asked for. Local `$` packages
(`$TMP` and friends) are never transported, so they push without one.

```console
$ abap push
error package ZGET_SUBS_API_V2 is transportable - pass --transport <TR>
      (only local $ packages can push without one)
```

**Push goes back where it came from.** A workspace is bound to the system it was
pulled from. The baseline hashes and object URIs in the manifest describe that
system and no other, so a diff against anything else is meaningless. `--system`
may name that system, but it cannot redirect the push:

```console
$ abap push --system qha-300 --transport DHAK900123
error workspace was pulled from dha-110, refusing to push to qha-300 -
      pull the package from qha-300 into its own folder if that is the real target
```

Moving code between systems is what the transport route is for, not a re-pointed
push.

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
$ abap status --remote
  C  src/zcl_tstmp_utilities.clas.abap  (also changed on server)

1 modified, 0 deleted, 1 changed on server
1 conflict(s) - push will refuse these until you re-pull
```

Push performs the same check and stops before writing anything:

```console
$ abap push --transport DHAK900123
  C  src/zcl_tstmp_utilities.clas.abap also changed on dha-110 since your pull
error 1 object(s) changed on the server - pushing would overwrite that work.
      Re-pull to inspect, or use --force to overwrite.
```

Resolve it by re-pulling and reapplying your edit, or override with `--force`
if you know your copy should win. `--dry-run` shows what would be sent without
sending it.

This costs one read round trip per modified object before the write; `--force`
skips the check.

## Systems and credentials

A *system* is a named connection profile: host, SAP user, client. Add one
interactively — `init` prompts for anything you leave out:

```console
$ abap init
System name (e.g. dev-100): dha-110
Host URL (https://host:port): https://sap.example.com:44300
SAP user: DEVELOPER
SAP client: 110
saved dha-110 to /Users/you/.abap-adt/config.json
next: abap login --system dha-110
```

Or non-interactively, which is what you want in a script:

```bash
abap init --name dha-110 \
          --host https://sap.example.com:44300 \
          --user DEVELOPER \
          --client 110 \
          --description "Dev, client 110"
```

Add `--insecure` to skip TLS verification for systems with a self-signed
certificate. Every `init` makes that system the default.

Then store the password once. It goes to the OS keychain under service
`abap-adt-cli`, account `<system>:<user>` — never to a file, never to the config:

```bash
abap login --system dha-110     # prompts, input hidden
abap ping   --system dha-110    # confirm it works
abap systems                    # list them all, '*' marks the default
abap logout --system dha-110    # remove the stored password
```

Omit `--system` on any of these when you only have one system, or when the one
you want is already the default.

### Where everything is stored

Two separate places, on every platform: the profile is a plain file, the password
never is.

| | Connection profiles | Password |
| --- | --- | --- |
| macOS | `~/.abap-adt/config.json` | Keychain (see it in Keychain Access) |
| Linux | `~/.abap-adt/config.json` | Secret Service — GNOME Keyring, KWallet |
| Windows | `%USERPROFILE%\.abap-adt\config.json` | Credential Manager, under Windows Credentials |

The keychain entry is stored under service `abap-adt-cli` with account
`<system>:<user>`, so several systems and several users coexist without
clashing. Nothing readable is written to disk by the CLI.

On a headless Linux box with no Secret Service running, `abap login` reports
`no usable keychain backend on this machine`. That is expected — use
`$ABAP_PASSWORD` instead, as below.

### Changing a password

Run `login` again. It overwrites whatever was stored for that system:

```bash
abap login --system dha-110     # after a SAP password change
abap ping  --system dha-110     # confirm the new one works
```

This is also the fix when a push suddenly fails with `authentication failed`
after a periodic SAP password expiry. `abap logout --system dha-110` deletes the
entry outright, and the next command falls back to prompting.

### Editing or removing a system

Re-running `init` with an existing name overwrites that profile and makes it the
default again — that is how you move a system to a new host or client:

```bash
abap init --name dha-110 --host https://newhost:44300 --user DEVELOPER --client 110
```

There is no `remove` command yet. Delete the entry from `config.json` by hand,
and fix `default_system` if it pointed at what you removed. Same for switching
the default without re-running `init`.

If you change the **user** of an existing system, the old password stays behind
in the keychain under the previous `<system>:<user>` account. Run
`abap logout --system <name>` *before* the re-init to clean it up.

### The config file

Profiles live in `~/.abap-adt/config.json`, created with `0600` permissions.
It is plain JSON and safe to edit or check into a dotfiles repo — it holds no
secrets:

```json
{
  "default_system": "dha-110",
  "systems": {
    "dha-110": {
      "host": "https://sap.example.com:44300",
      "user": "DEVELOPER",
      "client": "110",
      "insecure": false,
      "description": "Dev, client 110"
    }
  }
}
```

### Which system a command uses

First match wins:

1. `--system` / `-s` on the command
2. for `status`, `push` and a refreshing `pull`, the system recorded in the
   workspace manifest at pull time
3. `$ADT_SYSTEM`
4. `default_system` in the config file
5. the only configured system, if there is exactly one

With several systems and no default, commands stop and list the names rather
than guessing.

`push` is the exception: it always goes back to the system in the manifest, and
`--system` can only confirm that name, never redirect. See
[Guard rails](#guard-rails).

### Passwords and CI

Password lookup is `$ABAP_PASSWORD`, then the OS keychain, then an interactive
prompt. On a build agent with no keychain, set the environment variable and skip
`abap login` entirely. `$ADT_HOST`, `$ADT_USER` and `$ADT_CLIENT` likewise
override the profile, so a system need not be configured at all:

```bash
export ADT_HOST=https://sap.example.com:44300
export ADT_USER=DEVELOPER
export ADT_CLIENT=110
export ABAP_PASSWORD=...        # from your CI secret store
abap pull ZGET_SUBS_API_V2
```

## How it works

```
abap pull   →  POST /repository/informationsystem/virtualfolders/contents   (1 request)
            →  GET  <object>/source/main                                    (parallel)

abap push   →  GET  <object>/source/main                 (drift check, parallel)
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

Non-source objects — service bindings, message classes, DDIC views, enhancement
implementations, packages — are pulled as their ADT metadata XML instead of
source text, and can't be pushed back. Adding a type is one line in
`objects.py`.

Anything still unmapped lands in `Other/` as `.txt`. A handful of generated or
obsolete objects have no readable ADT endpoint at all; those are reported one
per line and skipped, and the rest of the pull still completes.

## Project status

Early. Pull, status and push are working and verified against a real system,
including method-level transport entries, the transport guard and conflict
detection.

Not yet implemented: activation after push, where-used, syntax check, unit test
runs, transport creation, object creation and deletion. Pushed objects stay
inactive until you activate them elsewhere.

## Licence

MIT
