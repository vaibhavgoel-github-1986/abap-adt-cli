# abap cli

Pull an ABAP package onto your laptop, edit it with whatever you like, push it
back under the right transport. Built directly on the SAP **ADT REST API** — the
same interface Eclipse/ADT uses, so nothing has to be installed in the SAP
system.

```bash
abap pull ZMY_PACKAGE --dest ~/Documents/my-package
abap status --remote
abap push --transport DEVK900123
```

## Why this exists

ABAP source lives behind SE80 and Eclipse. Getting a whole package in front of
modern tooling — your editor, `grep`, `git diff`, an AI assistant — usually means
copying it out one object at a time.

`abap cli` moves it in bulk, over HTTPS:

- **Pull a package in one command.** Thousands of objects fetched in parallel
  and written as ordinary files, including the parts SE80 hides — a class
  arrives with its local classes and test classes as separate, editable files.
- **See what changed before you ship.** `abap status` says which objects you
  touched; `abap diff` says what changed, line by line, and whether it was you
  or somebody working in SAP.
- **Push and activate in bulk.** Every edited object goes in one run and they
  activate together, so you never work out the right order for a CDS view, its
  behavior definition and the class behind it.
- **Correct transport entries.** Editing one method records one `LIMU METH`
  entry, not a lock on the whole class, so a colleague can work on the next
  method in their own transport.
- **Your source never passes through a language model.** The transfer is plain
  HTTPS between your machine and SAP. An AI assistant reads the files from
  disk, so a large package costs no context window and nothing is paraphrased
  in transit.

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
`pip install` works too. To pin a published version rather than the tip of the
default branch, name the tag:

```bash
pipx install "git+https://github.com/vaibhavgoel-github-1986/abap-adt-cli@v1.5.2"
```

Afterwards `abap update` keeps it current — see
[Versions and updating](#versions-and-updating).

## First-time setup

Three commands, once per system. `init` writes a connection profile, `login`
puts the password in your OS keychain, `ping` proves both are right.

**1. Describe the system.** `init` prompts for anything you leave out:

```console
$ abap init
System name (e.g. dev-100): dev-100
Host URL (https://host:port): https://sap.example.com:44300
SAP user: DEVELOPER
SAP client: 100
saved dev-100 to /Users/you/.abap-adt/config.json
next: abap login --system dev-100
```

| Prompt | What it wants | Where to find it |
| --- | --- | --- |
| System name | any label you choose, later used as `--system dev-100` | your own convention; `<sid>-<client>` reads well |
| Host URL | the ADT endpoint, scheme and port included | the same host and port your Eclipse ADT project uses |
| SAP user | your SAP user name | SU3, or your logon screen |
| SAP client | three digits | the client you log on to |

Add `--insecure` if the system serves a self-signed certificate, which is common
on internal dev boxes — without it the connection fails at the TLS handshake.
Every `init` makes that system the default, and re-running it with the same name
edits the profile in place.

**2. Store the password.** It goes to the OS keychain, never to a file:

```bash
abap login
```

**3. Check it answers**, before waiting on a long pull:

```console
$ abap ping
ADT alive on dev-100 client 100 as DEVELOPER  (24,118 bytes)
```

A failure here tells you which of the three things is wrong — host unreachable,
credentials rejected, or ADT not enabled for your user. A host that is only
reachable over VPN fails in about five seconds rather than hanging.

**4. Pull a package.**

```bash
abap pull ZEXAMPLE_PKG --dest ~/Documents/example-pkg
```

That is the whole setup. `abap systems` lists what you have configured and marks
the default with `*`. Anything beyond one system — several systems, CI without a
keychain, changing a password — is in
[Systems and credentials](#systems-and-credentials).

## Quick start

Pull a package into a folder you choose. With `--dest` that folder is also added
to your active VS Code workspace:

```bash
abap pull ZEXAMPLE_PKG --dest ~/Documents/example-pkg
cd ~/Documents/example-pkg
```

```console
connected to dev-100, listing ZEXAMPLE_PKG and its sub-packages...
pulling 30 objects ━━━━━━━━━━━━━━━━━━━━ 74/74 0:00:02
pulled ZEXAMPLE_PKG from dev-100 - 30 objects in 38 files, 204,345 bytes in 2.3s
```

That brings down the named package **and every package underneath it**, because
a package in SAP is usually a hierarchy. Pass `--no-subpackages` for the named
package alone. See [Sub-packages](#sub-packages).

The bar counts *requests*, not objects, because one object can be several
editable texts — a class is up to five. You now have ordinary files, laid out the
way SE80 shows them, with every file of an object in one folder:

```
Class Library/Classes/ZCL_EXAMPLE_READER/
  zcl_example_reader.clas.abap
  zcl_example_reader.clas.testclasses.abap
Core Data Services/Data Definitions/ZCDS_I_EXAMPLE/
  zcds_i_example.ddls.asddls
Business Services/Service Definitions/ZSD_EXAMPLE_API/
  zsd_example_api.srvd.srvdsrv
.adt/manifest.json        # what was pulled, from where, and its hashes
```

Pass `--flat` if you would rather have every file in one `src/` folder. See
[Layout](#layout).

Edit them however you like, then see what you touched — offline and instant:

```console
$ abap status
ZEXAMPLE_PKG from dev-100, pulled 2026-09-14T17:37:49
  M  Core Data Services/Data Definitions/ZCDS_I_EXAMPLE/zcds_i_example.ddls.asddls

1 modified, 0 deleted
```

Check nobody else moved, confirm the blast radius, then send it:

```bash
abap status --remote              # did anyone change these on the server?
abap push --dry-run               # what would be sent - makes no ADT calls
abap push --transport DEVK900456
```

```console
  M  Core Data Services/Data Definitions/ZCDS_I_EXAMPLE/zcds_i_example.ddls.asddls
  pushed Core Data Services/Data Definitions/ZCDS_I_EXAMPLE/zcds_i_example.ddls.asddls

1 object(s) pushed
```

Pushed objects stay **inactive** until activated. Add `--activate` and push
activates exactly what it just wrote:

```bash
abap push --transport DEVK900456 --activate
```

Three options are worth knowing from the start:

| Option | |
| --- | --- |
| `--system` / `-s` | which system, when you have more than one |
| `--trace` | print every ADT request and response, secrets redacted |
| `--help` | per-command help. It must come *after* the command: `abap pull --help` |

`--trace` is accepted on either side of the command:

```bash
abap push --transport DEVK900123 --trace
abap --trace push --transport DEVK900123
```

## Commands

| Command | Purpose |
| --- | --- |
| [`abap init` / `login` / `logout` / `systems`](#abap-init-login-logout-systems-ping) | connection profiles and credentials |
| [`abap ping`](#abap-init-login-logout-systems-ping) | check the endpoint is reachable |
| [`abap types`](#abap-types) | object types that can be pulled and pushed |
| [`abap pull [PACKAGE]`](#abap-pull) | download a package, in parallel |
| [`abap pull [PACKAGE] --match / --type / --user`](#abap-pull) | download only part of one |
| [`abap status`](#abap-status) | locally modified objects, offline |
| [`abap status --remote`](#abap-status) | also report what changed on the server |
| [`abap diff`](#abap-diff) | line-level differences against the server copy |
| [`abap push`](#abap-push) | upload changed objects |
| [`abap push --activate`](#abap-push) | ...and activate just those objects afterwards |
| [`abap delete NAME...`](#abap-delete) | delete objects from SAP and the workspace |
| [`abap transports`](#abap-transports) | every request you own, filtered by status and category |
| [`abap transports new "DESC"`](#abap-transports) | create a request |
| [`abap transports attr TR NAME=VALUE`](#abap-transports) | show or set CTS attributes |
| [`abap transports delete TR`](#abap-transports) | delete a request or task |
| [`abap transport TR list`](#abap-transport) | what a request contains |
| [`abap transport TR add NAME...`](#abap-transport) | add objects, whole or method-level |
| [`abap transport TR remove NAME...`](#abap-transport) | remove objects from a request |
| [`abap version`](#abap-version-abap-update) | which version is installed, and how |
| [`abap update`](#abap-version-abap-update) | install the newest published release |

Every command that talks to SAP also takes `--system` / `-s` and `--trace`.

Every command also carries its own reference: `abap <command> --help` prints what
it does, the guard rails it applies and worked examples.

### What each command is for

#### `abap init`, `login`, `logout`, `systems`, `ping`

Connection profiles and credentials. Passwords go to the OS keychain, never to a
file. `ping` checks the endpoint answers before you wait on a long pull.
Full detail: [Systems and credentials](#systems-and-credentials).

#### `abap types`

The object types the installed build can pull and push, read straight from the
registry the code uses. `--all` also lists what is skipped and why. Start here if
you are unsure whether a package is fully covered.
Full detail: [Object types](#object-types).

#### `abap pull`

Downloads a package and the packages below it into a folder, and writes a
manifest describing what was fetched. Run it with no arguments inside an
existing workspace to refresh in place, keeping the same system, layout, breadth
and any `--match` / `--type` / `--user` narrowing. Full detail:
[Pulling](#pulling).

#### `abap status`

Which objects you changed, compared against the hashes taken at pull time.
Offline and instant. `--remote` also reports what changed *in SAP* since your
pull, which is exactly what push will refuse.
Full detail: [Reviewing before you push](#reviewing-before-you-push).

#### `abap diff`

What changed, line by line, against the copy in SAP, and whether the change came
from you, from the server, or both. The third case is a conflict.
Full detail: [Reviewing before you push](#reviewing-before-you-push).

#### `abap push`

Sends every changed file back, each to its own ADT endpoint, so a one-method edit
stays a one-method transport entry. Add `--activate` to activate the whole batch
in a single run. Always safe to rehearse with `--dry-run`.
Full detail: [Pushing](#pushing) and [Activating](#activating).

#### `abap delete`

Removes objects from SAP and from the workspace. The one irreversible command, so
it confirms first and only accepts objects the workspace already tracks.
Full detail: [Deleting](#deleting).

#### `abap transports`

Every request you own, split into workbench and customizing, modifiable and
released, with a filter for each. `new` creates one from a description,
`attr` shows or sets its CTS attributes, and `delete` removes it.
Full detail: [Transports](#transports).

#### `abap transport`

Inspect a request, or move objects in and out of it. `list` prints the exact
`PGMID:TYPE:NAME` form that `add` and `remove` accept.
Full detail: [Transports](#transports).

#### `abap version`, `abap update`

Which build is installed and how it was installed, and upgrading to the newest
published release. Full detail: [Versions and updating](#versions-and-updating).

## Pulling

A *workspace* is any folder containing `.adt/manifest.json`. The manifest records
the package, the system, the layout, and one entry per file — the object's name,
ADT type and ADT URI, which editable text of that object the file holds, and the
SHA-256 of exactly what the server sent. Everything `status` and `push` do later
is derived from it, which is why no git repository is required.

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
| `--match` / `-m` | object name pattern, e.g. `'ZCL_EX*'` |
| `--type` / `-t` | comma-separated ADT types, e.g. `'CLAS,DDLS'` |
| `--user` / `-u` | object owner; defaults to you for `$` packages, `'*'` means everyone |
| `--se80` / `--flat` | SE80 object tree (default), or one flat `src/` folder; a refresh keeps whatever the manifest recorded |
| `--subpackages` / `--no-subpackages` | descend the package hierarchy (default), or take the named package alone |
| `--jobs` / `-j` | parallel requests, default 30 |
| `--force` / `-f` | discard local modifications and overwrite |
| `--system` / `-s` | pull from a system other than the default, or the one recorded in the manifest |

Objects that fail to download are reported individually and do not abort the
pull; everything else still lands.

### Sub-packages

SAP applications are package *hierarchies*, and `abap pull` brings the whole
hierarchy down by default:

```console
$ abap pull ZEXAMPLE_SUITE
connected to dev-100, listing ZEXAMPLE_SUITE and its sub-packages...
5154 objects in 9 packages
  pulling 4812 objects ━━━━━━━━━━━━━━━━━━━━ 6701/6701 0:02:04
pulled ZEXAMPLE_SUITE from dev-100 - 4790 objects in 4881 files across 9 packages, ...
```

The listing endpoint does most of that on its own: SAP's `package` facet is
*already* hierarchical, so asking for one package returns the objects of every
package beneath it too. Walking the tree is therefore not what finds the
children — it is what tells parent and child apart.

That is why `--no-subpackages` works by subtraction. It lists the sub-packages
as well, then drops everything they account for, leaving only what sits directly
in the package you named:

```console
$ abap pull ZEXAMPLE_SUITE --no-subpackages
5046 objects in 1 package
```

An object listed by two packages is fetched once, and a cycle in the hierarchy
terminates because each package is visited only once. `--match`, `--type`,
`--user` and the chosen layout apply to the whole tree, and every object lands in
the same workspace regardless of which package it came from — the manifest tracks
objects, not packages.

The choice is recorded in the manifest, so a later bare `abap pull` refresh
reaches exactly as far as the pull it repeats and can never silently widen.

A workspace pulled **before 1.1.0** has no such record, and is refreshed as the
single package it originally was. Pass `--subpackages` once to widen it
deliberately:

```bash
abap pull ZMY_PACKAGE --subpackages --force
```

### Layout

The default is an SE80-style tree. Each object gets a folder inside its type
folder, and everything belonging to that object lives in it:

```
Class Library/Classes/ZCL_EXAMPLE_UTILS/
  zcl_example_utils.clas.abap
  zcl_example_utils.clas.locals_imp.abap
  zcl_example_utils.clas.testclasses.abap
Function Groups/ZFG_EXAMPLE/
  zfg_example.fugr.abap              # the group's main program
  lzfg_exampletop.fugr.abap          # its includes
  z_example_read.fugr.abap           # and its function modules
```

`--flat` puts every file in a single `src/` folder instead, which is easier to
`grep` and keeps paths short. Both layouts hold the same files, and the choice is
recorded in the manifest — a later bare `abap pull` refresh keeps whatever the
workspace was pulled with, so an existing workspace never reshuffles underneath
you.

Switching layout on an existing workspace by passing `--se80` or `--flat`
explicitly rewrites the manifest and writes the files at their new paths, but
does **not** remove the files at the old ones. Delete the folder and pull again
for a clean switch.

### One object, several files

An object is not always one file. A class keeps its local definitions, local
implementations, macros and test classes in separate ADT includes, and SAP edits,
locks and transports each of them on its own. Pull brings down all five:

| File | ADT include |
| --- | --- |
| `zcl_x.clas.abap` | the class itself |
| `zcl_x.clas.locals_def.abap` | local type and class definitions |
| `zcl_x.clas.locals_imp.abap` | local class implementations |
| `zcl_x.clas.macros.abap` | macros |
| `zcl_x.clas.testclasses.abap` | ABAP Unit test classes |

Includes the class never used are skipped rather than written as empty files:
SAP answers 404 for one that was never created, and returns nothing but a
commented banner for one that exists and is empty. Neither becomes a file, so a
class with no local helpers still arrives as a single `.clas.abap`.

Push works the same way in reverse. Editing only `zcl_x.clas.testclasses.abap`
writes only that include, so the transport records the test include and nothing
else. A class edited across three files is still activated once.

Function groups are expanded on pull. A package listing only names the group, so
the CLI resolves its includes and function modules through the same repository
node structure Eclipse uses — without that step a function module could never be
pulled or pushed at all. They are filed under the group, the way SE80 shows them.

### Local objects: `$TMP`

Local packages work like any other, with one wrinkle: `$TMP` is a single package
shared by *every* developer on the system. On a busy system that is tens of
thousands of objects, most of them somebody else's.

So a `$` package defaults to **your** objects — the user the session is
authenticated as:

```console
$ abap pull '$TMP' --dest ~/Documents/my-local
connected to dev-100, listing $TMP owned by DEVELOPER...
pulled $TMP from dev-100 - 69 objects in 84 files, 273,395 bytes in 5.8s
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

The default SE80 tree is the same grouping Eclipse shows under a user's `$TMP`,
which makes a folder per developer easy to browse side by side:

```bash
abap pull '$TMP' --dest ~/Documents/DEVELOPER
abap pull '$TMP' --user ANOTHER_DEV --dest ~/Documents/ANOTHER_DEV
```

```
DEVELOPER/
  Business Services/{Service Bindings,Service Definitions}   4
  Class Library/{Classes,Interfaces}                        22
  Core Data Services/{Data Definitions,Behavior,...}        15
  Dictionary/{Database Tables,Views}                         4
  Enhancements/                                              1
  Function Groups/                                           1
  Message Classes/                                           1
  Programs/                                                 21
```

Counts are objects; each is a folder of one or more files.

Pushing back to `$TMP` needs no transport — local packages are never
transported. See [Guard rails](#guard-rails).

### Pulling part of a package

`--match` filters by object name on the server, `--type` by ADT object type.
They combine, and either one alone is enough:

```bash
abap pull ZEXAMPLE_PKG --type CLAS,INTF        # just the ABAP OO objects
abap pull ZEXAMPLE_PKG --match 'ZCDS_I_*'      # just those CDS views
abap pull ZEXAMPLE_PKG --match 'ZCL_EX*' --type CLAS
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
6. **Drift check** — one parallel read per modified file, comparing the server
   copy of that exact include against the baseline. Any conflict aborts before a
   single byte is written.
7. **Write loop** — for each file, serially: `LOCK` the object, `PUT` that
   include with `corrNr`, `UNLOCK`. The local baseline is re-hashed and the
   manifest saved after each file, so a failure halfway through does not make
   already-pushed work look unpushed. Re-running pushes only what is left.

| Variation | What it does |
| --- | --- |
| `abap push` | refused on a transportable package — a transport is never invented for you |
| `abap push --transport DEVK900123` | the normal case: checks drift, then pushes every locally modified object under that TR |
| `abap push --dry-run` | lists exactly what would be sent and stops. Fully offline — it makes no ADT calls at all |
| `abap push --force` | skips the drift check and overwrites whatever is on the server. Use only when you know your copy should win |
| `abap push --dest <path>` | pushes the workspace at that path instead of the current directory |
| `abap push --activate` | activates the objects it just pushed, in one run afterwards |
| `abap push --system <name>` | only accepted when `<name>` is the system the workspace was pulled from; anything else is refused |
| `abap push --trace` | prints every lock, PUT and unlock with full payloads |

Pushed objects stay **inactive** until something activates them. Add
`--activate` to do it in the same run — see [Activating](#activating).

### New objects

A file that the manifest has never seen is a new object. Drop it anywhere in the
workspace with the right name and extension — the folder it sits in does not
matter, only the file name does — and push creates it in SAP first, then writes
the source into it, the same two steps Eclipse performs when you add a class:

```console
$ abap status
  A  src/zce_new_view.ddls.asddls  (new, not in SAP yet)
  A  src/zcl_new_thing.clas.abap  (new, not in SAP yet)

0 modified, 2 new, 0 deleted

$ abap push --transport DEVK900201 --activate
  A  src/zce_new_view.ddls.asddls  (new)
  A  src/zcl_new_thing.clas.abap  (new)
  created ZCE_NEW_VIEW
  created ZCL_NEW_THING

activating 2 object(s)...

2 object(s) pushed
activated 2 object(s) on dev-100
```

The object name comes from the filename and the type from the extension, so
`zcl_thing.clas.abap` becomes class `ZCL_THING`. Part suffixes are understood
too: `zcl_thing.clas.testclasses.abap` is the test include of `ZCL_THING`, not an
object called `ZCL_THING_CLAS_TESTCLASSES`. The object lands in the workspace's
package, under the transport push resolved. Descriptions are taken from
`@EndUserText.label` where the source has one, and default to the object name
otherwise.

Creatable types are classes, interfaces, programs, CDS data definitions,
metadata extensions, access controls and service definitions. A new file of any
other type is refused by name rather than ignored — create it in Eclipse/ADT
once, then re-pull.

New **function group** objects are the exception worth knowing: four ADT types
share the `.fugr.abap` extension, so a new file with that extension is read as a
function group. A new function module cannot be created by dropping a file —
create it in Eclipse/ADT, then re-pull and edit it here.

Files in dot-directories are never considered, so `.git` and `.adt` stay out of
it. Deleting a file still does nothing: push never deletes objects in SAP.

## Activating

A push leaves inactive versions behind, exactly as editing in Eclipse does.
`--activate` finishes the job in the same run:

```console
$ abap push --activate
  M  src/zcl_example_utils.clas.abap
using DEVK900201 (DEVELOPER, 'Example work') - it already holds these objects
  recording under your task DEVK900202
  pushed ZCL_EXAMPLE_UTILS

activating 1 object(s)...

1 object(s) pushed
activated 1 object(s) on dev-100
```

It activates **only the objects that were just pushed**, never the whole
workspace — activating a package you did not touch is a good way to turn a
colleague's half-finished object into an active one. There is deliberately no
`abap activate` command for that reason.

Everything pushed goes into a single activation run, which is what you want after
a mass change: SAP resolves the order itself, so a CDS view and the class that
selects from it activate together rather than failing on sequence. Activation
only starts once every write has completed and unlocked, because SAP refuses to
activate a locked object.

Errors are reported per object, with the line number where SAP has one, and the
command exits non-zero so a script stops:

```console
$ abap push --activate
  pushed ZCL_EXAMPLE_UTILS

activating 1 object(s)...
  !  CLAS ZCL_EXAMPLE_UTILS line 42: Field "LV_MISSING" is unknown.
error 1 activation error(s) - the objects stay inactive until they are fixed
```

The source is still in SAP and still in the transport at that point; only the
active version is unchanged. Fix the file locally and push again. Warnings are
printed but do not fail the run.

## Deleting

Deletion is the one irreversible thing here, so it is deliberately awkward: it
takes object *names* rather than file paths, only accepts objects the workspace
already tracks, and asks before it acts.

```console
$ abap delete ZCL_EXAMPLE_DEMO
  D  ZCL_EXAMPLE_DEMO  (src/zcl_example_demo.clas.abap)

This deletes 1 object(s) from dev-100. Deleted objects cannot be restored by this CLI.
Continue? [y/N]: y
using DEVK900201 (DEVELOPER, 'Example work') - it already holds these objects
  recording under your task DEVK900202
  deleted ZCL_EXAMPLE_DEMO

1 object(s) deleted
```

`--dry-run` lists and stops; `--yes` skips the prompt for scripts. The same
transport rules as push apply, because a deletion has to be recorded somewhere
before it can reach the next system — see [Guard rails](#guard-rails). On
success the local file and its manifest entry go too, so `status` stays clean.

A name the workspace does not track is refused rather than guessed at:

```console
$ abap delete ZCL_NOT_HERE
  !  ZCL_NOT_HERE is not in this workspace
error only objects tracked by the workspace can be deleted - re-pull first
```

## Transports

`abap transports` is SE09 without the GUI, and `abap transport` works on a
single request.

### Listing what you own

```console
$ abap transports --unreleased
workbench / modifiable
  DEVK900101  ABC-1234 example read buffer
      task DEVK900102  DEVELOPER

customizing / modifiable
  DEVK900105  ABC-5678 example configuration
      task DEVK900106  DEVELOPER

2 request(s)
```

Filters come in pairs, and passing neither of a pair — or both — means no
narrowing at all:

| Option | Effect |
| --- | --- |
| `--released` / `--unreleased` | only released, or only what you can still change |
| `--workbench` / `--customizing` | only that category |
| `--user` / `-u` | somebody else's requests; `'*'` is everybody |
| `--objects` | also list the objects inside each request |
| `--system` / `-s` | a system other than the workspace's |

Status is filtered by SAP, category by the CLI. That split is not arbitrary:
the tree endpoint defaults `requestStatus` to *released*, so asking for
everything is genuinely two queries, while the section a request is filed under
is what the tree is authoritative about — a request whose `tm:type` disagrees
with its folder is still in that folder.

### Creating one

```console
$ abap transports new 'ABC-1234 example read buffer'
created DEVK900101  ABC-1234 example read buffer
  task DEVK900102  DEVELOPER
abap push --transport DEVK900101
```

`--customizing` makes a type `W` request instead of type `K`. The transport
target is left to SAP unless `--target` says otherwise, so the transport layer
of the package decides where the request goes rather than a guess made here.

The task inside it is classified as *Development/Correction* (or *Customizing*)
as a second step, because SAP creates every task through this API as
*Unclassified* whatever the create payload asks for, and an unclassified task
refuses every object with `Changes to objects are only allowed in
correction/repair`.

A request created this way is a *transportable* one. Objects in a **local**
package can only live in a **local** request — SAP says `Only edit objects from
package X in local requests` — and those are the ones SAP generates for you.
There is no way to ask for a local request here yet, so for a local package,
reuse the generated request instead of making a new one.

Descriptions are checked against SAP's 60-character limit before anything is
sent, because a request created with a truncated description has to be deleted
and remade.

### Attributes

CTS attributes are the Jira keys and release markers your system defines on a
request. `attr` shows them, and sets them:

```console
$ abap transports attr DEVK900103 Z_TICKET=ABC-1234
  +  Z_TICKET (Ticket reference) = ABC-1234

  Z_FEATURE (Feature reference) = ABC-5678
  Z_TICKET (Ticket reference) = ABC-1234

2 attribute(s) on DEVK900103
```

`--attr` / `-a` sets them at creation time, repeat it per attribute:

```console
$ abap transports new 'ABC-1234 fix' -a Z_TICKET=ABC-1234 -a Z_FEATURE=ABC-5678
created DEVK900103  ABC-1234 fix
  task DEVK900104  DEVELOPER
  +  Z_TICKET (Ticket reference) = ABC-1234
  +  Z_FEATURE (Feature reference) = ABC-5678
```

That is create-then-set, not one call: SAP has no way to carry attributes on the
request that creates it, and sets them one at a time.

`NAME=VALUE` splits on the *first* `=`, so a value may contain more of them.
Setting a name the request already carries replaces its value rather than
adding a second copy — SAP has two different actions for those cases
(`addattribute` and `modifyattribute`), and the second addresses the attribute
by *position*, not by name. Positions shift as attributes come and go, so the
current list is read immediately before every write.

`abap transports attr --names` lists what your system defines:

```console
$ abap transports attr --names
  Z_DEPLOYMENT  Deployment reference
  Z_FEATURE  Feature reference
  Z_TICKET  Ticket reference

46 attribute(s) defined on this system
```

That list is SAP's own value help, which is what the Eclipse dialog uses, so it
is the set you are *allowed* to set. The underlying table `WBOATTR` holds a few
more — `SAPCORR`, `SAPIMG`, `TAKT*` and friends — that SAP maintains itself and
hides from the dialog. Anything the value help omits is refused by SAP rather
than silently ignored:

```console
$ abap transports attr DEVK900103 Z_NOT_A_THING=x
error Z_NOT_A_THING is not a valid attribute; enter a valid attribute
```

### Deleting a request

```console
$ abap transports delete DEVK900103
DEVK900103
  task DEVK900104  DEVELOPER  Modifiable
  D  R3TR CLAS ZCL_THING

This deletes DEVK900103 and the 1 object(s) in it. A deleted request cannot be
restored by this CLI.
Continue? [y/N]:
```

The contents are read and shown *before* the prompt, so you can see what goes
with it. `--yes` / `-y` skips the confirmation for scripts.

SAP does the refusing here rather than the CLI: a released request cannot be
deleted, and neither can one still holding object locks. The transport number is
checked for shape before anything connects, so a typo costs nothing.

### Working on one request

`abap transport` inspects a request, and adds or removes objects in it:

```console
$ abap transport DEVK900201 list
task DEVK900202  DEVELOPER  Modifiable
  locked  R3TR CLAS ZCL_EXAMPLE_READER
  locked  R3TR DDLS ZCE_EXAMPLE

2 object(s) in DEVK900201

$ abap transport DEVK900301 add ZCL_EXAMPLE_UTILS
  +  R3TR CLAS ZCL_EXAMPLE_UTILS

1 object(s) in DEVK900301
```

Objects live in *tasks*, not requests, so a request number is resolved to the
task you own before anything is written.

### Naming what to add

Three forms, because not every entry is a whole object:

| Form | Records | Example |
| --- | --- | --- |
| `NAME` | `R3TR` + the type from the workspace manifest | `ZCL_EXAMPLE_UTILS` |
| `CLASS=>METHOD` | `LIMU METH` — one method on its own | `ZCL_EXAMPLE_UTILS=>GET_OFFSET` |
| `PGMID:TYPE:NAME` | exactly what you type | `R3TR:TABL:ZMY_TABLE` |

The method form is the one that matters for shared classes, and produces the
same entry SAP writes when you edit a single method in Eclipse:

```console
$ abap transport DEVK900302 add 'ZCL_EXAMPLE_UTILS=>GET_OFFSET'
  +  LIMU METH ZCL_EXAMPLE_UTILS             GET_OFFSET

1 object(s) in DEVK900302
```

That spacing is not cosmetic: SAP stores a `LIMU METH` key as the class name
padded to 30 characters followed by the method, so the CLI builds it that way.
Interface implementations work through the same form —
`ZCL_THING=>ZIF_THING~DO_IT`.

Quote the argument. `=>` means nothing to zsh or bash, but shells differ and a
stray `>` would redirect to a file.

The plain `NAME` form needs the object in the workspace, since that is where its
type comes from. `PGMID:TYPE:NAME` is the escape hatch when it is not, or when
you want an entry the CLI would never infer. Types for `remove` come from the
request itself, so an entry can still be named after its object is gone.

**Removing works, and is addressed by position.** SAP identifies an entry by its
position in the request, not by name, so the current contents are read
immediately before the write — positions shift as entries go:

```console
$ abap transport DEVK900201 remove ZCL_NEW_THING ZCE_NEW_VIEW
  -  R3TR CLAS ZCL_NEW_THING removed
  -  R3TR DDLS ZCE_NEW_VIEW removed

2 object(s) removed from DEVK900201
```

The result is read back afterwards rather than trusting the `200`: omit the
position and SAP answers `200` and does nothing at all, so anything still in the
request is reported as a failure instead of a success.

Removing an object's only entry releases its lock, which is how you hand a class
back without releasing the whole request.

## Object types

`abap` pulls exactly what it can push back. A type earns its place by having
something ADT will accept a write for; anything else is skipped rather than
downloaded as read-only metadata you would only discover was read-only when the
push refused it.

To see the current list on your own install:

```console
abap types          # types that are pulled and pushed
abap types --all    # plus the ones deliberately skipped
```

```console
Class Library/Classes
  CLAS/OC   .clas.abap, .clas.locals_def.abap, .clas.locals_imp.abap,
            .clas.macros.abap, .clas.testclasses.abap

Dictionary/Data Elements
  DTEL/DE   .dtel.xml  xml

Function Groups
  FUGR/F    .fugr.abap
  FUGR/FF   .fugr.abap
  FUGR/I    .fugr.abap
```

Two shapes are covered.

**Source objects** are plain text at a source endpoint: classes (with their
local definitions, local implementations, macros and test classes), interfaces,
programs, includes, function groups, function modules and their includes, type
groups, transformations, CDS data definitions, metadata extensions, annotation
definitions, access controls, behavior definitions, service definitions,
database tables and structures.

**Dictionary, text and service objects** have no source endpoint at all — they
are edited as the object's own XML document: data elements, domains, table
types, lock objects, message classes and service bindings. A message class
carries every message number and text, and a service binding carries its
contract and published state, so those are editable too. SAP stamps `changedAt`
when it stores one, so push re-reads the object afterwards and takes the
server's copy as the new baseline; without that every pushed object would look
changed for ever after.

Skipped, because ADT answers with a SAP GUI shim rather than a real resource:
DDIC views and BSP applications. Enhancement implementations are skipped as
well — they do have a resource, but SAP fails to serialise a good share of them
with a 500. So are packages and authorization defaults, types the registry has
never heard of — SEGW projects, SICF nodes, transactions — and objects another
SAP service owns, such as enterprise service proxies. All of them are counted in
one line rather than producing a 404 each:

```console
pulled ZEXAMPLE_SUITE from dev-100 - 3894 objects in 3974 files in 97.6s
1664 object(s) have no editable content in ADT and were skipped
```

Objects that fail for any other reason are reported one per line, and the rest of
the pull still completes.

Adding a type is one line in `objects.py` — a `Part` names the file suffix and
the URI segment that serves it. An empty segment means the object resource
itself, which is how the XML-backed types are written.

## Guard rails

**A transport is required.** Pushing to a transportable package without
`--transport` is refused, rather than letting SAP quietly generate a
`Generated Request for Change Recording` you never asked for. Local `$` packages
(`$TMP` and friends) are never transported, so they push without one.

```console
$ abap push
error package ZEXAMPLE_PKG is transportable - pass --transport <TR>
      (only local $ packages can push without one)
```

**Push goes back where it came from.** A workspace is bound to the system it was
pulled from. The baseline hashes and object URIs in the manifest describe that
system and no other, so a diff against anything else is meaningless. `--system`
may name that system, but it cannot redirect the push:

```console
$ abap push --system qa-300 --transport DEVK900123
error workspace was pulled from dev-100, refusing to push to qa-300 -
      pull the package from qa-300 into its own folder if that is the real target
```

Moving code between systems is what the transport route is for, not a re-pointed
push.

**The transport is checked before anything is written.** SAP locks an object in
one *request*; tasks are just per-developer slots inside it. So before the first
write, push asks SAP which request already holds each object.

When exactly one request holds them, there is nothing to choose — SAP would
reject any other — so push adopts it and says so:

```console
$ abap push
  M  src/zcl_example_utils.clas.abap
using DEVK900456 (DEVELOPER, 'Example work') - it already holds these objects
  recording under your task DEVK900457
  pushed ZCL_EXAMPLE_UTILS

1 object(s) pushed
```

If the request is open but you have no task in it — developer A holds the object
under their task — SAP opens one for you, and push reports that instead:

```
using DEVK900456 (ANOTHER_DEV, 'Feature work') - it already holds these objects
  SAP will open a task for DEVELOPER in it
```

`--transport` is still honoured, and still checked. Pass one that does not lead
to the holding request and push stops before writing:

```console
$ abap push --transport DEVK900123
  !  src/zcl_example_utils.clas.abap is locked in DEVK900456 (DEVELOPER, 'Example work')
error 1 object(s) already locked in DEVK900456, not DEVK900123 - SAP locks an
      object in one request only, so re-run with --transport DEVK900456
```

The request number and any task number inside it are all accepted, since they
all record into the same request.

**When a transport really is needed**, you get the plain error — no request holds
these objects yet, so there is nothing to infer and nothing is invented for you:

```console
$ abap push
error package ZEXAMPLE_PKG is transportable - pass --transport <TR>
      (only local $ packages can push without one)
```

Mixed pushes are handled the obvious way. If two of three objects sit in
`DEVK900456` and the third is in no request at all, the third joins the same
request and is called out by name rather than slipped in quietly:

```console
  +  src/zcl_new_helper.clas.abap is in no request yet, it will be added to DEVK900456
```

Objects spanning *several* requests are refused, with the request numbers listed,
because one push cannot legally record into both. Local `$` packages skip the
check entirely: they are never transported.

**Locks are taken and released for you.** There is no separate lock step, and no
unlock to remember. Each object is locked immediately before its write and
unlocked immediately after, which is where the transport entry comes from. If
somebody else is holding the object, SAP refuses the lock and push stops with the
server's own message, having written nothing to that object:

```console
$ abap push --transport DEVK900123
error User ANOTHER_DEV is currently editing ZCL_EXAMPLE_UTILS
```

Locks are session-bound, so they cannot leak: the unlock runs even when the write
fails, and the session is returned to stateless afterwards, which releases any
enqueue SAP still holds. A push interrupted halfway leaves earlier objects
written and re-baselined, so re-running sends only what is left.

**Concurrent edits are detected.** The manifest records the hash of exactly what
the server handed you at pull time, so re-fetching and re-hashing shows whether
anyone touched the object since — SE80, Eclipse, or another push. Status marks
three cases:

| Marker | Meaning |
| --- | --- |
| `M` | you changed it |
| `A` | a new file, not in SAP yet |
| `R` | somebody else changed it on the server, you did not |
| `C` | conflict — changed in both places |

```console
$ abap status --remote
  C  src/zcl_example_utils.clas.abap  (also changed on server)

1 modified, 0 deleted, 1 changed on server
1 conflict(s) - push will refuse these until you re-pull
```

Push performs the same check and stops before writing anything:

```console
$ abap push --transport DEVK900123
  C  src/zcl_example_utils.clas.abap also changed on dev-100 since your pull
error 1 object(s) changed on the server - pushing would overwrite that work.
      Re-pull to inspect, or use --force to overwrite.
```

Resolve it by re-pulling and reapplying your edit, or override with `--force`
if you know your copy should win. `--dry-run` shows what would be sent without
sending it.

This costs one read round trip per modified object before the write; `--force`
skips the check.

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
│  abap diff           │
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
│ status/diff  │   │ supported by CLI   │
│ activate     │   │                    │
└──────┬───────┘   └─────────┬──────────┘
       │                     │
       └──────────┬──────────┘
                  │
                  ▼
             ┌─────────┐
             │   SAP   │
             └─────────┘
```

### Reviewing before you push

Two commands, two different questions.

`abap status` tells you **which** objects you changed. It hashes every file
against the manifest baseline recorded at pull time, so it needs no network and
no git repository.

`abap diff` shows **what** changed, line by line, against the copy currently in
SAP — and, from the manifest baseline, **who** changed it:

> Console samples from here on use short `src/…` paths, the [`--flat`
> layout](#layout), so lines fit. The default SE80 tree prints the same files at
> longer paths.

```console
$ abap diff
'-' is dev-100 as it stands now, '+' is your local copy - what push would make it
trailing spaces shown as ·, tabs as →

  R  src/zcds_i_example.ddls.asddls  (changed on dev-100, not by you)
  line 8
   as select from zdt_example

 {
-  key object_id,  //changes
+  key object_id,
   key number_int,
  line 18
       cycle_start_date,
-      cycle_end_date··
+      cycle_end_date
 }

1 object(s) differ from dev-100
1 of them changed on dev-100 since your pull - pushing would overwrite that work, re-pull instead
```

Two readability details, because an ABAP diff is often whitespace: changed lines
show trailing spaces as `·` and tabs as `→`, so an invisible edit is visible, and
a file whose only difference is spacing is flagged `whitespace only`. Hunk
positions are printed as plain `line 18` rather than `@@ -18,5 +18,5 @@`.

The marker is the important part, because a diff on its own cannot tell you which
side moved:

| Marker | Meaning |
| --- | --- |
| `M` | you changed it; `+` is your edit, ready to push |
| `R` | somebody changed it in SAP and you did not; `-` is their work, and pushing would wipe it |
| `C` | both sides changed it |

In the example above someone edited the view in SAP GUI. The `-` line is *their*
change and the `+` line is your untouched copy, so pushing would silently revert
them — hence the warning. Re-pull instead.

The manifest stores hashes, not text, so `diff` fetches the server copy. That is
the same request `status --remote` makes, kept rather than discarded, and costs
one GET per tracked object.

Git is still worth adding on top if you want local history and revert:

```bash
cd ~/Documents/example-pkg
git init && git add -A && git commit -m "baseline: pulled from dev-100"
```

That buys `git diff` offline, plus `git checkout -- <file>` to throw a change
away. Entirely optional — `abap diff`, `abap status` and `abap push --dry-run`
cover review on their own.

### What still needs SAP tooling

`abap` covers the develop-review-ship loop for the object types in
[Object types](#object-types). A few things deliberately live elsewhere, because
they are one-off setup steps rather than part of the edit cycle:

| Task | Where |
| --- | --- |
| release a transport | SE09/SE10 |
| object types outside the registry | Eclipse/ADT or SAP GUI |
| SEGW projects, SICF nodes, DDIC views | SAP GUI — no ADT editor exists |
| TVARVC entries, client copies | SAP GUI or an MCP server |

Everything else — pulling, editing, diffing, pushing, activating, adding to and
removing from a transport, deleting objects — is `abap`.

Run `abap types` to see exactly which object types the installed build handles;
it reads the same registry the pull and push use, so it cannot drift from the
code.

## What you get

**Correct transport entries.** Editing one method records exactly one
`LIMU METH` entry, the same as Eclipse. The class is not locked as a whole, so a
colleague can work on a different method in a different transport, and importing
your transport will not revert theirs.

```
DEVK900303   LIMU   METH   ZCL_EXAMPLE_UTILS             GET_OFFSET
```

**The whole object, not just its main source.** A class comes down as its own
source plus its local definitions, local implementations, macros and test
classes, each of them a file you can edit and push on its own. Function groups
are expanded into their includes and function modules. See
[One object, several files](#one-object-several-files).

**No silent overwrites.** Push refuses to clobber work that appeared on the
server after your pull, and refuses to auto-generate a transport behind your
back. See [Guard rails](#guard-rails).

**Speed.** Enumeration is a single request; every source fetch runs in parallel.
A 30-object package lands in about 2.3 seconds.

**Nothing installed in SAP.** ADT is already there.

## Systems and credentials

A *system* is a named connection profile: host, SAP user, client. Add one
interactively — `init` prompts for anything you leave out:

```console
$ abap init
System name (e.g. dev-100): dev-100
Host URL (https://host:port): https://sap.example.com:44300
SAP user: DEVELOPER
SAP client: 100
saved dev-100 to /Users/you/.abap-adt/config.json
next: abap login --system dev-100
```

Or non-interactively, which is what you want in a script:

```bash
abap init --name dev-100 \
          --host https://sap.example.com:44300 \
          --user DEVELOPER \
          --client 100 \
          --description "Dev, client 100"
```

Add `--insecure` to skip TLS verification for systems with a self-signed
certificate. Every `init` makes that system the default.

Then store the password once. It goes to the OS keychain under service
`abap-adt-cli`, account `<system>:<user>` — never to a file, never to the config:

```bash
abap login --system dev-100     # prompts, input hidden
abap ping   --system dev-100    # confirm it works
abap systems                    # list them all, '*' marks the default
abap logout --system dev-100    # remove the stored password
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
abap login --system dev-100     # after a SAP password change
abap ping  --system dev-100     # confirm the new one works
```

This is also the fix when a push suddenly fails with `authentication failed`
after a periodic SAP password expiry. `abap logout --system dev-100` deletes the
entry outright, and the next command falls back to prompting.

### Editing or removing a system

Re-running `init` with an existing name overwrites that profile and makes it the
default again — that is how you move a system to a new host or client:

```bash
abap init --name dev-100 --host https://newhost:44300 --user DEVELOPER --client 100
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
  "default_system": "dev-100",
  "systems": {
    "dev-100": {
      "host": "https://sap.example.com:44300",
      "user": "DEVELOPER",
      "client": "100",
      "insecure": false,
      "description": "Dev, client 100"
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
export ADT_CLIENT=100
export ABAP_PASSWORD=...        # from your CI secret store
abap pull ZEXAMPLE_PKG
```

## How it works

```
abap pull   →  POST /repository/informationsystem/virtualfolders/contents   (1 per package)
            →  POST /repository/nodestructure                   (sub-packages, function groups)
            →  GET  <object><part>                              (parallel, one per part)

abap diff   →  GET  <object><part>                       (parallel, text kept)

abap push   →  GET  <object><part>                       (drift check, parallel)
            →  POST /cts/transportchecks                 (which request owns it)
            →  POST <object>?_action=LOCK                (stateful, serialised)
            →  PUT  <object><part>?lockHandle=...&corrNr=...
            →  POST <object>?_action=UNLOCK
            →  POST /activation                          (--activate, one batch)
```

`<part>` is `/source/main` for most objects, and one of `/includes/definitions`,
`/includes/implementations`, `/includes/macros` or `/includes/testclasses` for
the other texts a class owns. The lock is always taken on the object; only the
`PUT` targets an individual include.

Reads are stateless so they parallelise. Locks are session-bound, so writes are
serialised and the session always returns to stateless afterwards, releasing any
server-side enqueue. Activation comes last and in a single call, because SAP
refuses to activate a locked object and resolves dependency order itself — and an
object edited across several of its includes is activated once, not once per
file.

Transport granularity is not something this CLI implements — it falls out of
using the same API Eclipse does. SAP decides which includes actually changed and
records those.

ADT serves CRLF; files are stored with LF locally so a pull/push round trip does
not make every object look modified.

## Versions and updating

`abap version` reports what is actually running, and how it got there:

```console
$ abap version
abap-adt-cli 1.5.0 (pipx, /Users/you/Library/Application Support/pipx/venvs/abap-adt-cli)

$ abap version --check
abap-adt-cli 1.5.0 (pipx, ...)
1.5.2 is available - run 'abap update'
```

The version is read from the *installed distribution*, not from the source tree,
so a checkout sitting next to an older installed copy cannot make it lie.

```bash
abap update              # install the newest release, if it is newer
abap update --check      # print the command it would run, change nothing
abap update --version v1.5.2
abap update --force      # reinstall even when nothing is newer
```

It works out how the CLI was installed and upgrades it the matching way —
`pipx upgrade` for a pipx install, `pip install --upgrade` for a venv. A source
checkout is refused, because `git pull` is the right answer there:

```console
$ abap update
error this looks like a source checkout, not an installed copy -
      update it with 'git pull' instead
```

A `--version` argument is matched against a version pattern before it reaches
the install URL. Anything else is refused rather than interpolated.

### Cutting a release

There is one version number, in `src/adt_cli/__init__.py`. `pyproject.toml`
reads it from there through `[tool.hatch.version]`, so the two cannot drift.

```bash
# bump __version__ in src/adt_cli/__init__.py, then
git commit -am "release 1.5.2"
git tag v1.5.2
git push && git push --tags
```

`abap update` finds the newest GitHub release, and falls back to the newest tag
when no release has been published for it.

## Project status

Every command has been exercised against a live S/4HANA system, not only
unit-tested — pulling a package hierarchy of several thousand objects, creating
and deleting objects, method-level transport entries, the transport guard,
conflict detection, creating and deleting transport requests with their CTS
attributes, and a single activation run resolving a cyclic CDS dependency that
would fail if the objects were activated one at a time.

Not yet implemented: where-used, syntax check, unit test runs, releasing a
transport, and creating a *local* request for a local package. For those, reach
for ADT/Eclipse or an MCP server. See
[What still needs SAP tooling](#what-still-needs-sap-tooling).

### Known rough edges

- Switching an existing workspace between `--se80` and `--flat` leaves the files
  at the old paths on disk. Delete the folder and re-pull. See
  [Layout](#layout).
- A *new* `.fugr.abap` file is created as a function group, because four ADT
  types share that extension. Create new function modules in Eclipse/ADT, then
  re-pull. See [New objects](#new-objects).
- `abap transports new` always produces a *transportable* request, so it cannot
  be used for objects in a local package. See [Creating one](#creating-one).

## Licence

MIT
