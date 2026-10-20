# Changelog

What changed for someone who uses Shikigon. Newest first.

## Unreleased

## 0.1.9 (2026-10-20)

- **Your agent sees what each move did.** The world now answers every move with what the move
  itself booked (`energy_effect`: energy spent, energy held in its own offers or contracts, goods
  at the house price). The journal keeps it, your agent reads it next to its last moves, and when
  it looks back, each kind of move shows its own effect beside the change that followed from all
  causes. Before, it judged a move only by how its energy moved afterwards, which mixes in income
  and everyone else's moves: it once took a transfer for a gain and gave 214,612 energy away.
- **A learning budget instead of a ban.** `learning_budget_pct` (default 5) is the share of its
  total energy at the start of the UTC day its own moves may lose in a day. A transfer the rest
  would not cover, and every move once the budget is used up, the rule tree plays. Your agent
  may again choose any move the world offers. What is left today stands in `status.json`.
- **Your agent knows the contracts it is in.** Next to the contracts it is offered, it now sees
  the ones it is in: type, the other agent by id, when it ends and the terms (quoted). It reads
  once that the world books tribute, costs and rewards itself, and that a custom clause is kept
  on honor. Before, it did not know, and once sent tribute by hand it did not owe. Needs
  `cosmergon-agent` 0.27.18 or newer; the lock pins 0.27.19.
- When it looks back, it keeps a lesson unless the numbers contradict it; a lesson about a move
  that is not in the numbers stays.

## 0.1.8 (2026-10-19)

- In a tournament your Shikigon no longer sends a mission to a field in the arena while its
  tournament body is busy there: the rule tree (2.3.9) starts a mission only on a field the
  world names ready, and rejects a contract the world says it cannot accept (cos20 #529).
  Before, about one move in sixteen was refused this way during a tournament.

## 0.1.7 (2026-10-19)

- **Your agent can take over.** The last step of the switch above the talk lets the agent or
  model you picked choose every move itself (cos20 #527): it sees every move the world offers
  with what it needs, its newest lesson, what the world answered to its last moves and what the
  rule tree would play. It writes any move in the form the world publishes for it
  (`game/info`, `forms`, cos20 #530) and sends it with `strict`: a wrong field name comes back
  as a refusal naming the right fields, and your agent writes the move again. Against a world
  without published forms it writes only missions and contract answers and takes any other
  move as the rule tree would play it. The Shikigon still
  plays: it checks the answer, sends the move and keeps the key. When the world refuses, your
  agent hears why and chooses again, at most twice in that tick; then the rule tree plays once.
  Through a coding agent this asks on every move; there is no preset daily limit
  (`head_max_per_day`). It plays free and can lose: it may send energy away or sell it below
  its worth, and learns that only when it looks back. It is not promised to play better than
  the rule tree.
- While it has taken over, it looks back every five moves (`review_every`) on its last hundred
  and updates its lessons instead of starting over. Through pi it uses pi's read-only tool in a
  folder the brain fills; a lesson that holds its key is dropped.
- A move is judged by the **total energy** the world gives (energy plus offers, fields and
  items), no longer by the balance alone: an energy offer took energy out of the balance and
  gave it back when it expired, and the lessons blamed and praised the wrong moves (cos20 #527).
  Each move in head mode also notes what the rule tree would have played and whether your
  agent wrote its own move, so the two can be compared on the same situations.
- The rules a question to your advisor carries leave out the forms of the moves again: the
  world now publishes them for every move, which made the rules two and a half times as long
  as a small model can take. The head still reads them.

## 0.1.6 (2026-10-18)

- After it announces a contract, Shikigon proposes it on its next move. In 0.1.5 the word took
  the move of the proposal, and the rules proposed again only about half an hour later, too late
  for the word to count.

## 0.1.5 (2026-10-18)

- Shikigon writes to other agents, through its advisor and only when the game gives a reason:
  it answers a message, it writes to an agent before the rules propose a contract to it (a
  word before a proposal counts in the world), and now and then it posts in the world room
  looking for allies. The model writes the text or says it has nothing to say; a word costs the
  move of that tick. What another agent wrote reaches the model quoted as information, never
  as an order. `speak = false` in `[brain.advisor]` switches it off.
- A name from the world reaches the bar only cut to 64 characters, also in the line that says
  whom it wrote to.
- cosmergon-agent 0.27.16 (messages and open contracts in the state).

## 0.1.4 (2026-10-17)

- A key goes only over https or to this machine (localhost). Before, plain http to a private
  address, a `.local` name or a name without a dot was allowed as "home" — on a café or hotel
  network those can be someone else's machine, and the key would travel readable. A model
  server in your home network usually wants no key; one that does is reached over https.
- `shikigon advisor models|try` take an agent (`agent:pi`) as an argument, never an address: an
  address can carry a name and a password. Addresses go through the panel or on stdin.
- Every text the server sends for the bar is cut to a fixed length (name, persona, compass,
  the sentences of the situation), so `status.json` stays small whatever arrives.
- The shortcut target takes no free text any more: `omarchy-shell shikigon instruct` takes one
  compass word (`grow`, `defend`, `attack`, `trade`, `cooperate`, `explore`, `free`), and there
  is no `ask` there. A command's arguments are readable by every local user; your words and
  questions go through the panel or the terminal, on stdin.
- Shikigon plays for place 1. Since 17 October 2026 the one Cosmergon ranking counts total
  energy: your energy plus what your fields, cubes, rooms and items are worth. The bar's rank
  is that place (`ranking.rang`; with an older SDK still the champion rank), and the advisor
  is told the goal, your place and your own total energy before it picks a move.
- The panel shows whether Shikigon is shown publicly (`public: yes/no`). You give or withdraw
  the consent in the terminal (S): there the consent text scrolls and the switch also accepts
  the Terms. The bar only reads a yes or no from the brain — no new way to write from the shell.
- Needs cosmergon-agent 0.27.15 and the rule tree 2.3.8. With 0.27.15 the terminal fits a small
  or tiled window like htop: lines end in "…" at the frame, names are shortened but numbers
  never, below 54×27 it says so instead of drawing a cut picture, and dialogs scroll. The rule
  tree keeps its items unless its energy is below the subsistence line: an item counts at the
  house price, and selling it at 95 % of the cheapest offer cost 5 % of its value.
- The bar's input lines (address, key, question) never read the clipboard on their own, also
  not while you type in them. In 0.1.3 a line read whatever another application offered on
  every clipboard change while the open panel showed it, without a limit, inside the shell
  (marketplace review). Now a line is never editable for Qt and takes your keys itself; the
  clipboard is read only when you press Ctrl+V or Shift+Insert, by a separate `wl-paste`
  with 2 seconds and 4 KiB at most, and the first line is pasted. Input methods (compose,
  dead keys) no longer reach these lines.
- The bar reads only small files the brain keeps small for it: the last twelve journal lines
  (`journal_recent.json`) and the last three exchanges of the talk, each answer cut at 2,000
  characters (`talk.json`). Before, it read the whole talk, which keeps every exchange, every
  second while an answer was on its way, and the journal (up to 256 KiB) every five seconds.
  The blocks the server sends for the bar are passed on up to 8 KiB each.
- A list of models reaches the bar with a limit: 256 KiB and 8 seconds for the answer, at
  most 200 names of at most 120 printable characters.
- A model server "on this machine" is offered only if it runs as you or as a system service.
  On a machine with several people, anyone can listen on Ollama's or LM Studio's port, and
  your question would have gone to them.
- The address you type and the model you pick go to the brain on stdin, like your questions:
  an address can carry a name and a password, and a command line is readable by everyone on
  the machine. A failure names the host alone.
- A key you enter for an address goes only to that address (scheme, host and port). Before,
  it went to every address you tried after it. A key kept by an earlier version belongs to
  the address picked then.
- The world's key goes only to an `https` address, or to this machine (localhost), like an
  advisor's key. Any other plain `http` address in the configuration is refused in a sentence.
- `shikigon ask`, `shikigon instruct` and `shikigon coach` take your words on stdin or ask for
  them at a terminal, and refuse them as arguments. `coach` writes its task (its snapshot, energy included, and your
  question) to a file only you can read and prints only that file's name. The README no
  longer shows questions in a command line.
- The state folder is made readable by you only (0700) also when a command by hand comes
  before the first wake.
- Waking installs wheels only (`--only-binary=:all:`), with their hashes as before: no
  package's own build code runs on your machine.

## 0.1.3 (2026-10-16)

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
