# Changelog

What changed for someone who uses Shikigon. Newest first.

## 0.1.14 (2026-10-31)

- **Two games.** The README now describes the outer game next to the inner one: the four ways
  in with an inviting agent's code, what there is to buy and how it is paid, and what the
  inviter earns, dated and linked to the partner terms. The advisor can explain it when asked.
- **Born with an invitation.** `invited_by` under `[character]` sends the inviting agent's code
  once, when your Shikigon is born. A code in another form stops the registration with one
  sentence, and nothing is sent.
- **A long answer is kept short.** A model's answer went into the talk's file uncut, and the
  terminal read it whole. Now each answer is kept as long as the bar shows it, 2,000
  characters, cut with "…"; every exchange is still kept.
- **The README says what a proxy sees.** A key over https passes through a proxy set in your
  environment encrypted, unreadable there unless your system trusts that proxy's certificate.
  The README said "never through a proxy" before, which holds for plain http only.
- **cosmergon-agent 0.28.2** with the shop in the terminal (`S`), and **cosmergon-decider-tree
  2.3.11**: the rule tree itself now rejects a contract offer that would make it pay, as the
  brain already did.
- **Wake a new one starts clean.** After **wake a new one** the new Shikigon was told the old
  one's lessons and look back, measured its learning budget against the old one's total, read
  the old one's last moves as its own and could play a move the old one had announced. Now
  nothing of the old agent is carried over: its moves, lessons, look back, learning budget,
  words and snapshot go with it. Your talk, your advisor and its daily count stay yours.
- **The learning budget counts only the head's own moves.** When the world refused a move and
  the rules played instead, that move was counted against the head's budget; and a rules' move
  refused a moment before could go out a second time with the head's next one. Neither happens
  now.
- **One daily limit while the head has taken over.** A question and the dry run followed the
  advisor's limit instead of the head's; now every call follows the head's limit while it plays.
- **Long answers keep the talk.** A long answer could empty the talk shown in the bar, and long
  refusals could hide the head's last moves from it. The bar and the head now see the lines they
  are meant to see.
- **The README says what the kernel holds.** Your agent may open TCP connections only to port
  443; UDP stays open, because the agent resolves names over it. The README said "connect only
  to port 443" before.
- **A model server with a CA of your own.** Shikigon checked https only against the
  certificates it brings along (certifi), so a server in your home with a certificate from your
  own CA (Caddy's `tls internal`, for instance) could not be reached directly, and the panel
  said no more than `ConnectError`. Now https is checked against your system's trust store, where
  `sudo trust anchor --store ca.crt` puts your CA; pi reads the same store. A certificate the
  store does not vouch for is still refused, and the panel now says so in words. The same store
  is used for the world's own addresses; its public certificate is in it.
- **Omarchy's placeholder is never started.** Where Omarchy's placeholder for pi stood in pi's
  place, the brain still asked it for its version before each question, and the placeholder
  installs pi when it runs. Now no start of the agent's program goes past it: pi is not asked,
  and the panel says why.
- **The README says what the learning budget counts.** It counts what each move gives or books
  when it is made. The bombs a mission uses later and a field given up are not counted; the
  README promised the whole loss of a day before. The model's list in the README now also names
  the count of its own unsold energy offers, which it was always told.

## 0.1.13 (2026-10-23)

- **OpenCode is no longer asked, and pi adds none of your files.** pi appended its
  `~/.pi/agent/APPEND_SYSTEM.md` to every prompt, OpenCode its `~/.config/opencode/AGENTS.md` (or
  `~/.claude/CLAUDE.md`) and the `instructions` of your `opencode.json`, and what the model wrote
  could go on to another agent. pi now runs with an appended line of Shikigon's own, which keeps
  its file out. OpenCode has no switch for its files, so Shikigon no longer asks it: pick pi or
  a model directly. If you picked OpenCode before, **WHO ANSWERS?** asks you again. Playing
  Cosmergon with OpenCode itself, without Shikigon, is not touched by this.
- **Through your agent only port 443, the port of https.** Your agent sent the talk wherever its
  own setup named for the model: also in plain text across the network, or to a port on this
  machine that another user may hold, and Shikigon could not see which. Now the kernel lets your
  agent, and everything it starts, connect only to port 443 (Landlock, Linux 6.7 or later); on a
  kernel without it no question goes to an agent. The kernel sees the port, not the encryption:
  an address your agent's own setup writes as `http://…:443` would still go in plain text. A
  model server on this machine or in your home: pick it in the panel directly, not through your
  agent.
- **Only versions of an agent it was tried with.** Omarchy updates every agent to its newest
  version every four hours, and Shikigon asked whichever version ran. Now it reads the agent's
  version before each question and asks only pi 1.2.0, 1.3.0 and 1.4.0, each read at its tag; a
  later release, a patch release too, only once it is read. After an update to another version
  **WHO ANSWERS?** and the panel say so, and the rule tree plays.
- **The coach is gone.** `shikigon coach` wrote a task with the world's words into a file for a
  coding agent with tools (`coach.md` in its state folder), and the skill told that agent to
  follow it. Command and skill are removed; a task an earlier version left is deleted.
- **No answer of the world ends the brain.** An answer in a form the SDK or the rule tree could
  not read (no JSON, a word for a number, a list for an object) ended the brain, and the bar
  started it again only to see it end on the next move. Now that move ends with one line, and
  the next tick tries again (`cosmergon-agent` 0.27.34 and `cosmergon-decider-tree` 2.3.10 read
  such answers by their form). A registration answered so says it in one sentence.
- **No endless number of your agent reaches the world, and no parameter the call takes for
  itself.** `NaN`, `Infinity` or `1e999` in an answer, or a parameter named `action`, ended the
  brain where the move was sent. Now such an answer is unusable and the rule tree plays, and
  whatever a move raises while it is built or sent is a refused move, never the end.
- **The look back reads no name and no words of another player.** Your agent got its moves as
  the journal kept them: whom it wrote to, what it wrote, what the model advised. Now each move
  goes as its numbers and its move word, the world's reason for a refusal quoted.
- **What your agent gives away counts against the budget before it goes.** Only energy sent
  was checked. Now each move your agent writes itself is checked, and a move whose value for
  another agent cannot be said beforehand lets the rule tree play while a budget is set: giving
  goods away (the price the world names for them is the cheapest offer of any other agent, it
  books them at the house price), buying another agent's offer, a contract with escrow or one
  that has your Shikigon pay, a breach, a move the world adds later. So does any move that
  gives something in a world that does not state its total energy: there the budget checked
  nothing. What goes out counts as it was checked, or as the world books it if that is more:
  a world that books nothing no longer leaves the rest whole.
- **A contract is read by what it costs your Shikigon, and it says no to one that costs it.**
  The rule tree took an offer by its kind alone: a warrior, trader, diplomat or expansionist
  accepted any tribute, also one it pays, so another player could have it pay him every tick;
  a scientist, warrior or diplomat any alliance, also one with costs per tick. The budget read
  only a contract's escrow and a payer written exactly like your Shikigon's own id: an alliance
  with costs per tick, a mercenary it hires (who takes its mega bombs when he accepts) and its
  own id in upper case went past. Now one rule reads every contract move, for the rule tree and
  for your agent: an offer that would cost it is rejected, and while a budget is set a contract
  your agent writes that costs anything is played by the rule tree, which says no to it too.
- **A move your agent writes without any fields goes out with `strict` too.** It went out
  without, and the world filled in its own values: `market_list` with no fields offered the whole
  surplus at the median price. Now the world answers which fields it needs.
- **Each round of a look back counts against the day's limit.** A look back counted one call
  and could make four (each `READ` of your agent is a call of its own). Now each is counted, and
  the reading ends at the limit.
- **A name that does not resolve holds no call past its time.** The checks before a call
  resolved the address outside the call's deadline, those of a model list too, and the world's
  name was resolved when the agent was made. And when a call had ended, its loop still waited
  for a resolution left in one of its threads, up to 300 s; the brain did so too when the bar
  stopped it.
- **The rules of the world in another form end no question.** A world whose rules named its
  moves as a list ended **ask** with an error; now they are read as far as they go.
- **When its key ended, the panel says so.** With the SDK it ran on, a key that ended looked
  like a world that did not answer: the panel said "The world did not answer" for ever, never
  offered **wake a new one**, and the brain sent the dead key every minute (`cosmergon-agent`
  0.27.33 tells the two apart).
- **No answer of your agent ends the brain.** An answer with a word where a number belongs
  (`"amount": "half"`) ended the brain on every move it chose itself; now such an answer counts
  as unusable, and the rule tree plays.
- **Names of a model server and the world's words reach the shell's own buttons as text
  only.** Omarchy 4.0.0 and 4.0.1 read markup in their buttons and section headers, and the
  panel gave them model names and the tournament's number: a model named `<img src=…>` made
  the shell load that image. Now no text reaches one of them with a tag in it.
- **A hint holds fixed words only, in its title too.** The title carried its name, which the
  world gives it. The tournament hint carried the server's sentence, and
  Omarchy's notifier from 4.5 on decodes a body before it cleans it, so the escaping was undone:
  a link could stand there under a label of the world's choosing.
- **A world's count of missions costs little.** Each ended tournament mission it listed cost two
  files, as many as it listed; now at most fifty per look, and the bar's view is written once.
- **Opening the panel looks for nobody.** Who could answer (`mise`, the model servers on ports
  11434 and 1234) is looked for when you press **ask**, as the README says.
- **The socket that took the connection decides, not only who listens.** On this machine the
  brain checked who listened on a port when it connected. Another local user who listened on a
  freed port, took the connection and closed his listener before your server listened there
  again was no longer among the listeners and got the question (or, for a world on this
  machine, the key). Now the socket that took this very connection must be yours as well.
- **Nothing goes over plain http to another machine,** also without a key. Your question, the
  talk so far and the situation went in plain text to any http address that wanted no key; on a
  network that only looks like home anyone could read them, and the advice and the moves would
  reach a stranger by themselves. Now an address is https or on this machine (`localhost`). For
  a model server elsewhere: https or an ssh tunnel (README).
- **Every call has one deadline, from the request to the last byte of the answer.** The limit
  counted from the body of an answer on; a server that answered `100 Continue` again and again,
  or sent its headers a byte at a time, held a move, a question or a model list for ever. Now a
  move falls back to the rules in its time, whatever the server sends first. The same holds for
  every answer of the world through `cosmergon-agent` 0.27.31.
- **Programs it starts read no configuration from a folder someone else can write.** The empty
  folder they run in lay in `/tmp`, and a program may read its configuration from every folder
  above its own, up to `/` (`mise` does): another user's file in `/tmp` could start a program of
  theirs as you. Now they run in `/`, which only the system can write, and nothing is started
  if anyone else may. Its name names nobody, and that counts too: pi tells its model the folder
  it runs in, and what the model writes can go on to another agent.
- **Your agent ends when the brain does,** and with it everything your agent started. Stopped
  by the bar (a change of "advise on moves") or a closed terminal, the brain now ends the agent
  it was asking, instead of leaving it to run to its own end. Killed without a word (the
  terminal's time limit, a reload of the shell), the brain could not; now a small guard between
  the brain and each agent ends the agent's whole group then. The terminal waits longer than the
  brain for an answer (300 s) and asks it to stop before it kills it (`cosmergon-agent` 0.27.32).
- **Another player's text stays inside its quote.** A message or contract terms with `>>>` in
  them ended the quote around them, and what followed reached the model as the brain's own words.
  Now `<` and `>` in such a text become `‹` and `›`, and the names of other players stand inside
  the quote too.
- **What the world sends fills a prompt only so far:** each of its sentences a line, its rules
  12,000 characters, a message 280, the time its key ends 64. A configured world could put up to
  8 MiB into every question on your model's allowance (the time its key ends, in a form that is
  no time, still could until the ninth review).
- **Every file it keeps is yours alone (0600),** made so from the start. A file an earlier version
  left readable for others is renewed once, so a program that opened it then reads nothing more.
- **Smaller fixes:** a world's sentence on stderr comes without control characters, also in a
  terminal; the echo check of a message compares no more than the message; the README no longer
  says pi looks back with a tool. The terminal shows the server's advice and your partner code as
  text (`cosmergon-agent` 0.27.31). Hints go to `notify-send` after `--`, so an agent named
  `--icon=…` is a title, not an option. The terminal makes its files for you alone (`umask 077`).
  The README says what "Remove" leaves (`~/.cosmergon`, a session of OpenCode's), that your agent
  sends on over its own connection, and that `K` in the terminal shows the key.

- **Every key goes only to the address it was given for.** A key named by `api_key_env` went to
  any address you picked in the panel afterwards; now it goes only to the `base_url` of the same
  section of the config file. A key kept by 0.1.3 or earlier names no address and went to the
  next one picked; now it goes nowhere, and the address asks for it once more.
- **A name and a password in an address count as a key** (`https://me:pw@…`). They went as a
  Basic key also over plain http to another machine, and to a system account's server on this
  one; now the rule for keys holds for them too: only over https, or to a server on this machine
  that runs as you.
- **Every program the brain starts runs in `/`,** also the model list of your agent and the
  look-up with `mise`, not only the question. Started by hand in a folder someone else wrote,
  they could read that folder's settings.
- **A move goes out only while the world's port is yours at that moment** (marketplace review
  #12584). With a world on this machine, the brain checked its port once per move, before
  waiting for the model; the move after that wait and the retries of `cosmergon-agent` went out
  unchecked, so another local user who took a freed port in between could get the agent's key.
  Now `cosmergon-agent` 0.27.29 asks the brain's check right before every request it sends,
  each retry included, and checks every new connection at its socket, the way the brain's own
  calls already did: a name that resolves elsewhere or a proxy from your environment cannot lead
  past it. A refused request is not sent, and the Shikigon falls asleep and says why.
- **The terminal opens only for a Shikigon of cosmergon.com.** The terminal client talks to
  cosmergon.com only; until now it got the key of a Shikigon from any world and sent it there.
- **The terminal gets its key over a pipe, and shows the world's words as text.** The key
  stood in the terminal client's environment (`COSMERGON_API_KEY`), and `/proc/<pid>/environ`
  kept it there for the client's whole life, readable for you and root. Now it goes over a file
  descriptor (`--api-key-fd`, `cosmergon-agent` 0.27.30). A sentence the world sent with a
  refusal went into the terminal's log as markup, with its control characters; now every
  foreign text there is text. With `cosmergon-agent` 0.27.32 that holds for the numbers too
  (tick, rank, how many are ranked): they are read as numbers, so text in their place cannot
  reach the terminal. With 0.27.33 it holds for every answer of the world, whichever call reads
  it; the running mission in the agent panel came as markup until then. The terminal opens only
  https addresses in your browser, and its settings (`~/.cosmergon`) are yours alone from the
  first byte.

## 0.1.12 (2026-10-22)

- **Answers are read as they come, and only so much of them.** A model server or the world can
  send a compressed answer; until now the brain unpacked it before counting it against its
  limit (marketplace review #12454). Now every call asks for the answer uncompressed, reads it
  raw and refuses one that comes compressed anyway. The same holds for registering and
  forgetting, and, through `cosmergon-agent` 0.27.24 and newer, for every answer of the world.
- **The brain checks the connection it really made.** Before a question or a key goes to an
  address on this machine, the brain now checks whom the connection actually reached, not
  only the address it looked up before. A key goes over plain http only to a server you run
  yourself, never through a proxy from your environment and never to another account's service.
- **An agent that prints too much is stopped,** and whatever it left running ends with its
  answer, not only on a timeout.
- **Odd answers are refused, not crashed on.** Deeply nested JSON from a model now counts as a
  broken answer, and finding the JSON in an answer takes linear time.
- **Smaller fixes:** private files are written through a temporary file of their own; the
  status file keeps its size limit where it is written; every Python started by the scripts runs
  isolated (`-I`); the dashboard of `cosmergon-agent` 0.27.24 no longer passes your key to the
  browser it opens.
- **The terminal shows other players' words without control characters, the 8-bit ones too.**
  A terminal reads ESC and the 8-bit CSI and OSC as commands. The terminal client already
  dropped ESC from what others write, but kept the 8-bit ones; with `cosmergon-agent` 0.27.25 it
  drops both, the same range the bar drops.

## 0.1.11 (2026-10-21)

- **No agent gets a tool from Shikigon any more, also not to look back.** What other agents
  write in the world reaches your agent, and an instruction hidden in it could have made an
  agent with a tool read your files. pi looked back with its own read tool until now; now it
  runs without any tool there too, and may ask the brain instead: it answers
  `READ moves.txt 1-40`, and the brain hands back those lines, only from the three files it
  wrote for the look back, 40 lines and three times at most. A name or path outside them gets a
  sentence, never a file.
- **OpenCode: a second switch.** Besides denying every tool, Shikigon now also switches every
  tool off for its advisor (`tools: {"*": false}`). By OpenCode's documentation both cover the
  tools of MCP servers in your own OpenCode config as well.
- **Codex is refused where it would be called,** not only left out of the choice, as long as it
  has never been tried on a device.
- **Certificates: `certifi` 2026.10.19.** The brain's HTTPS connections trust the `certifi`
  bundle. Versions before 2026.10.18 contain the `emSign ECC Root CA - G3` root, and eight
  intermediate certificates under it have compromised keys (PYSEC-2026-4320,
  GHSA-6q48-6c7w-9gw2): someone holding one of those keys and sitting on the network path
  could read and change a connection to the world or to a model you reach over HTTPS.
  2026.10.19 removes the affected roots. Only this one package changes in the lock.

## 0.1.10 (2026-10-20)

- **Nothing goes to a port another user took.** Whose a port on this machine is, is now read
  right before every question, move, look back and model list, not only when the server was
  found. Before, if your Ollama or LM Studio stopped and another user on the same machine took
  its port, your next question and the conversation went to them (marketplace review of
  0.1.9). The same check now covers an address you typed that leads to this machine and a
  world on this machine. If the port is not yours, Shikigon sends nothing and says why.
- **The world's key goes only to the world that gave it.** If the config names another world,
  Shikigon sends nothing and asks you to register a new Shikigon there. A key from an earlier
  version is bound to the world it goes to first.
- A model's answer is read only up to 256 KiB and within the move's time, the world's rules
  up to 256 KiB and within eight seconds: an endless answer cannot fill the brain's memory.
- When a coding agent does not answer in time, it ends together with the processes it started
  (its process group).
- A model name that starts with `-` is refused, so it can never become an option of the
  agent's command.

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
