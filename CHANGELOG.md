# Changelog

What changed for someone who uses Shikigon. Newest first.

## 0.1.3 (unreleased)

Needs `cosmergon-agent` 0.27.12 and `cosmergon-decider-tree` 2.3.7 (pinned in
`requirements.lock`; the environment is rebuilt on the next start of the shell).

- Your Shikigon no longer sells the mega bombs it gathers while one of its energy offers is
  still open. With rule tree 2.3.6 it fell through to selling its most valuable piece, and in a
  tournament those were the bombs its siege needed. It sells items only when it has no energy
  to sell.
- A scientist with a field collects on it instead of scouting a terminal without a target;
  every such report came back empty.
- A move the world refuses three times in a row is paused for the next 30 moves, and the
  journal says so (`sell on the market: refused 3 times, paused for 30 moves`). Before, a
  Shikigon could ask for the same refused move every minute for hours, such as an offer over
  the listing limit. A move the world did not take because it came twice in one tick, or
  because the world did not answer, does not count. The advisor is never offered a paused
  move, and a paused contract stays paused.
- Moves read the same in the bar and in the terminal: the words come from `cosmergon-agent`
  (`say something`, `make a counter-offer` instead of the raw names).
- The bar's input lines (address, key, question) no longer read the clipboard while they are
  not on screen. Qt fetches whatever another application offers into every editable line on
  each clipboard change; a large or stalled transfer could hold up the whole shell. A line is
  now read-only unless the open panel shows it (marketplace review).
- The bar no longer re-reads its journal, talk and lessons every five seconds while you are not
  looking: it reads them while the panel is open, and the status only while Shikigon is awake.
  It also no longer reads them once when the shell starts, and lets go of them when the panel
  closes. It all runs inside the shell, and the journal can be a quarter of a megabyte.

## 0.1.2 (2026-10-15)

Needs `cosmergon-agent` 0.27.11 and `cosmergon-decider-tree` 2.3.6 (pinned in
`requirements.lock`; the environment is rebuilt on the next start of the shell).

- Your Shikigon no longer puts a new energy offer on the market while one of its own is still
  unsold. Each offer holds its energy until it is sold or expires, and few energy offers sell:
  a Shikigon could have hundreds open at once, nearly all its energy tied up, and looked weak
  while nothing was lost.
- The advisor is told how many of its energy offers stand open, so it no longer recommends
  selling more while they hold the energy.
- Offers that are already open stay until they sell or expire (6 hours unless set otherwise);
  their energy then comes back.

## 0.1.1 (2026-10-15)

Needs `cosmergon-agent` 0.27.10 (pinned in `requirements.lock`; the environment is rebuilt on
the next start of the shell). A terminal window that is still open keeps the old version until
you close it and open it again.

- Your questions, instructions and the talk with the model go over stdin, no longer as a
  command-line argument: other users on the same machine could read those while a model
  answered (marketplace review). This holds for the bar, the advisor's agent (pi, OpenCode)
  and `[M]` in the terminal.
- The folder `~/.local/state/shikigon` is set to mode 700 on wake, also where an earlier
  version made it readable for others.
- The journal keeps its last 128 KiB (about a thousand entries) once it passes 256 KiB. It
  grew by about 80 KiB a day, and the bar read it whole every few seconds. The talk with the
  advisor is kept whole.
- A broken line in the journal or the talk (e.g. after a crash mid-write) is skipped; it no
  longer stops `ask` or `advise`. Status, lessons and the choice of advisor are written atomically,
  so the bar never reads half a file.
- A key for a model goes only over https, or to loopback, private addresses, `.local` names and
  names without a dot. Before, an `http://` address on the open internet would have received it
  in plain text.
- A hint's text is shown as written: `<`, `>` and `&` in it are no longer read as markup by the
  notification daemon.

## 0.1.0 (2026-10-14)

### The brain
- One Cosmergon agent of its own, registered on `wake` and kept in a private key file.
- Moves by a rule tree ([cosmergon-decider-tree](https://pypi.org/project/cosmergon-decider-tree/)),
  once per tick of the world. A tick that outlasts `interval_s` is waited for.
- A word from you sets its compass (`instruct`).
- **A model as its advisor** (optional, any OpenAI-compatible endpoint, off until you name
  one): `decider = "advised"` lets the model pick among up to three moves the rule tree
  proposes; `ask` answers your questions from the rules of the world and its state.
  `advise` is the dry run. Only a proposed move is ever played.
- **Who answers** is picked in the panel (`ask`): a coding agent you already use (OpenCode
  or pi, called without a terminal and without tools), a model server on this machine, or an
  address you type. A connection is taken only after it answered one real question.
  From a terminal: `advisor list | try | advise | forget`.
- **It learns from its own moves** (with an advisor picked): every 100 moves the model
  turns its energy before and after each move into a short lesson, kept in `lessons.json`
  and given along with every later advice and answer; the journal shows `learned: …`, and the
  panel keeps the newest under LEARNED (more, less, how many moves it rests on). It is asked
  for in three labelled lines, in a player's words, and a text that runs long is cut at a
  sentence end or marked with "…". A lesson that does not fit says why in the journal.
  `learn` makes one now. Nothing of it goes to the server.
- Other deciders load by name from the Python entry-point group `cosmergon.deciders`.
- **Your coding agent as its coach**: `coach` prints a task that points the agent you
  already use to `skill/SKILL.md`, the brain and its snapshot. Shikigon connects to no model
  for this.
- `brain/shikigon-wake` sets up the brain from `requirements.lock` and wakes it; an update
  that fails brings the old environment back.
- `forget --yes` deletes the agent on the server and its files on your machine.
- A world that is away, refuses, or limits you is said in one sentence, the server's own
  where it has one.
- **Its first field** is told as a goal, not as a failure: while it has none, the panel says
  the world is full and it has to win one (gather spores, build bombs, besiege a weak field).
  Having no field alone does not make it "struggling"; falling energy or a catastrophe does.
- Every move is said in words, not by its name in the world's interface: in the journal,
  under "last move", and in what its advisor is told (`sell on the market`,
  `mission: gather spores`).

### The bar
- Eyes that show its mood; a panel with energy, fields, rank, compass, last move and journal.
- During a tournament the panel shows its score against the leader, its compass and its
  Marauders, and the journal says how each tournament mission ended.
- `status.json` carries the server's `next_step`, `key_expiry` and, asleep, the reason in one
  word. The panel shows the server's sentence on what comes next, and when the key ended.
- The panel never runs off the screen: its head and its buttons stay, the middle (now,
  journal, tournament, the answers of the talk) scrolls when the screen is too short.
  Wheel or touchpad, or PgUp/PgDn.
- No "move now" and no middle click: it moves by itself right after every tick.
- **The first hours:** a few short desktop notifications, each at most once and at least ten
  minutes apart, walk a new player through it: awake with a first move read from its state,
  a free tournament slot, the compass, `ask` (only while no advisor is picked). A first field
  won is cheered at once. When the server proposes a tournament slot, the panel says where:
  open terminal, press T.
- **open terminal** hands the terminal client the brain as its advisor: `M` there asks the
  same advisor as "ask" in the panel (with `cosmergon-agent` 0.27 or newer).

### Install
- `requirements.lock`: every package with version and hashes (18, all pure Python),
  resolved with Python 3.14.
- Waking leaves no download cache behind (`pip --no-cache-dir`): everything it installs is
  in `~/.local/share/shikigon`.
- An environment that can no longer load its packages is rebuilt by itself: after the system
  moved to a new Python, its packages sit in the old version's folder.
- The README says how to remove it completely, and how to run the brain from a terminal
  with its own Python.
