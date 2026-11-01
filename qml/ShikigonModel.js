// Shikigon's panel logic as plain functions, so it can be tested without a compositor.
// Same pattern as Omarchy's own models (shell/plugins/bar/BarModel.js): QML imports this
// file directly and ignores the guard at the end; Node loads it as CommonJS.

// The folder above the state folder: $XDG_STATE_HOME only if it is an absolute path, as in the
// brain (files.xdg_home) and both scripts. A relative one would make the bar read the talk from
// a folder relative to where the shell was started (cos20 #492, betreuer pass ead8263, B1).
function stateHome(given, home) {
  var value = String(given || "")
  return value.charAt(0) === "/" ? value : home + "/.local/state"
}

// The brain reports "free" under the server's name for it.
function isCurrentPreset(name, compass) {
  return compass === name || (name === "free" && compass === "autonomous")
}

// Arrows move through one grid: the presets in rows of `columns`, then the actions.
// The first key press only places the cursor.
function moveCursor(cursor, dx, dy, count, columns) {
  if (cursor < 0) return 0
  return Math.max(0, Math.min(count - 1, cursor + dx + dy * columns))
}

// 420668.4 -> "420,668": whole units with thousands separators, like the dashboard.
function grouped(number) {
  var value = Math.round(Number(number))
  if (!isFinite(value)) return ""
  var digits = String(Math.abs(value))
  var out = ""
  for (var i = 0; i < digits.length; i++) {
    if (i > 0 && (digits.length - i) % 3 === 0) out += ","
    out += digits[i]
  }
  return (value < 0 ? "-" : "") + out
}

// One journal entry as a line: a word from you, a tournament mission's end (the brain
// writes it in a player's words, cos20 #475), a lesson from its last moves (#451), or a
// move. `ok` is false only for a failure.
function journalLine(entry) {
  if (entry.instruction)
    return { text: "you: " + entry.instruction + " → " + entry.preset, ok: true }
  var failed = entry.ok === false
  if (entry.learned !== undefined)
    return failed ? { text: "could not learn: " + (entry.why || ""), ok: false }
                  : { text: "learned: " + entry.learned, ok: true }
  var text = entry.mission ? entry.mission + " — " + entry.text : moveWords(entry)
  return { text: text + (failed ? " ✗" : ""), ok: !failed }
}

// A move in words: a mission by its kind ("mission: gather spores"), any other move by its
// action. Before 14.10.2026 the journal showed "start_mission" again and again (#451).
// The brain writes a move's words into the journal (brain/words.py); older entries have none.
function moveWords(entry) {
  if (!entry) return ""
  if (entry.words) return String(entry.words)
  if (entry.kind) return "mission: " + String(entry.kind).replace(/_/g, " ")
  return String(entry.action || "").replace(/_/g, " ")
}

// Newest first; the same entry several times in a row becomes one line with a count.
function journalLines(entries, limit) {
  var lines = []
  // Only a real array: an object from outside with "length": 1e300 would hold the shell's
  // thread in this loop (class of marketplace reviews #12425, #12401; cos20 #492 S14).
  if (!Array.isArray(entries)) return lines
  for (var i = 0; i < entries.length; i++) {
    var line = journalLine(entries[i])
    var previous = lines.length > 0 ? lines[lines.length - 1] : null
    if (previous && previous.text === line.text) previous.count++
    else lines.push({ text: line.text, ok: line.ok, count: 1 })
  }
  return lines.slice(0, limit === undefined ? 4 : limit)
}

// ---------- First-time hints (cos20 #451, 14.10.2026) ----------
// Desktop notifications, like the greeting on install: each at most once, at least TIP_GAP_S
// apart ("always informed, never overwhelmed", founder 10.10.). The first step is read from
// the state, not written into the text: a fixed "[P] Place cells" pointed nowhere once the
// world was full (#468). Returns { id, title, body, marks } — marks go into tips.json as seen.
// A body holds fixed words only, never a sentence of the server: a notifier reads the body as
// markup, and Omarchy's from 4.5 on decodes entities up to three times before it cleans it, so
// escaping it once here was undone (cos20 #492, fifth review). The title holds fixed words
// too (sixth review, finding 1): it named the agent, whose name the world gives, and Omarchy's
// notifier looks for a link in title and body and opens it on a click on the card
// (Detect.links, Service.activate), so a name like "https://…" was a link of the world's.
var TIP_GAP_S = 600

// The server names its stage (API contract, cos20 14.10.2026): clients branch on the key, the
// sentence is for people and may change. A server without `stage` is read by its sentence.
function stageIs(step, stage, sentence) {
  if (!step) return false
  if (step.stage) return step.stage === stage
  return sentence.test(String(step.you_are_here || "") + " " + String(step.next || ""))
}

function tournamentOffer(status) {
  return stageIs(status && status.next_step, "tournament_offer",
                 /^you qualify for a free tournament slot/)
}

// Text for a component of the shell (its Button, its PanelSectionHeader), the one way there
// (cos20 #492, eleventh review). Those components set no text format in Omarchy 4.0.0 and 4.0.1
// (PlainText only from 4.0.2 on), and Qt reads a text with a tag in its first line as styled
// text: a model named `<img src="https://…">` made the shell load that image. Without `<` there
// is no tag; `‹` reads the same.
function hostText(text) {
  return String(text === undefined || text === null ? "" : text).replace(/</g, "‹")
}

// The words the IPC target takes (cos20 #492): the compass presets, nothing else. A command's
// arguments are readable by every local user, so free words and questions never come this way.
var COMPASS_WORDS = ["grow", "defend", "attack", "trade", "cooperate", "explore", "free"]

function compassWord(text) {
  var w = String(text || "").trim().toLowerCase()
  return COMPASS_WORDS.indexOf(w) >= 0 ? w : ""
}

// The input lines are read-only (cos20 #492, marketplace review #11665) and type what a key
// press brings: only printable characters, never a control character (Enter, Tab, Esc have
// their own handlers, and a stray one must not end up in an address or a key).
function typedText(text) {
  var s = String(text || "")
  return /^[^\u0000-\u001f\u007f-\u009f]+$/.test(s) ? s : ""
}

// A paste, read by its own process (at most 4 KiB): its first line, control characters out.
function pastedLine(text) {
  return String(text || "").split(/\r?\n/)[0].replace(/[\u0000-\u001f\u007f-\u009f]/g, "")
}

// `s` in place of the selection (or at the cursor) of a line {text, cursor, anchor}; the
// anchor is the selection's other end, equal to the cursor when nothing is selected.
function lineInsert(line, s) {
  var t = String(line.text || "")
  var lo = Math.min(line.anchor, line.cursor), hi = Math.max(line.anchor, line.cursor)
  return { text: t.slice(0, lo) + s + t.slice(hi), cursor: lo + s.length, anchor: lo + s.length }
}

// One key in a read-only input line (QuietLine): the line it leaves, {action: "accept"} or
// {action: "paste"}, or null when the key is not the line's (Esc, Up, Tab go on).
// `key` is a name ("enter", "backspace", "left", "v", …), `mods` {ctrl, shift, other}.
function lineKey(line, key, typed, mods) {
  var t = String(line.text || "")
  var c = line.cursor
  var selected = line.anchor !== c
  function go(to) {
    to = Math.max(0, Math.min(t.length, to))
    return { text: t, cursor: to, anchor: mods.shift ? line.anchor : to }
  }
  if (key === "enter") return { action: "accept" }
  if ((mods.ctrl && key === "v") || (mods.shift && key === "insert")) return { action: "paste" }
  if (mods.ctrl && key === "a") return { text: t, cursor: t.length, anchor: 0 }
  if (key === "backspace" || key === "delete") {
    if (selected) return lineInsert(line, "")
    var from = key === "backspace" ? Math.max(0, c - 1) : c
    var to = key === "backspace" ? c : Math.min(t.length, c + 1)
    return { text: t.slice(0, from) + t.slice(to), cursor: from, anchor: from }
  }
  if (key === "left") return go(c - 1)
  if (key === "right") return go(c + 1)
  if (key === "home") return go(0)
  if (key === "end") return go(t.length)
  var s = typedText(typed)
  if (s !== "" && !mods.ctrl && !mods.other) return lineInsert(line, s)
  return null
}

function nextTip(status, seen, lastAt, now, advisorPicked) {
  if (!status || !status.name || status.mood === "asleep") return null
  seen = seen || []
  function was(id) { return seen.indexOf(id) >= 0 }
  // a celebration does not wait for the gap
  if (was("landless") && !was("first_field") && Number(status.fields) > 0)
    return { id: "first_field", title: "Your Shikigon won its first field!",
             body: "Its own land: now it can place cells on it and grow.", marks: ["first_field"] }
  if (lastAt && now - lastAt < TIP_GAP_S) return null
  if (!was("awake")) {
    var offer = tournamentOffer(status)
    var body = status.first_field
      ? "All cubes are full and busy right now, so it has to win its first field. "
        + "That takes a few hours. "
      : ""
    body += offer
      ? "Your first move could be: take a free tournament slot. Open terminal, press T."
      : "Your first move could be: tell it what matters with a compass button in its panel."
    var marks = ["awake"]
    if (status.first_field) marks.push("landless")
    if (offer) marks.push("tournament")
    return { id: "awake", title: "Your Shikigon is awake", body: body, marks: marks }
  }
  if (!was("tournament") && tournamentOffer(status))
    return { id: "tournament", title: "A free tournament slot is open",
             body: "Open terminal, press T to take it.", marks: ["tournament"] }
  if (!was("compass"))
    return { id: "compass", title: "Tell your Shikigon what matters",
             body: "The compass buttons in its panel tilt its choices: grow, defend, trade … "
                   + "It stays itself.",
             marks: ["compass"] }
  if (!was("ask") && !advisorPicked)
    return { id: "ask", title: "Ask it something",
             body: "Press ask in its panel: a model you choose (a local one works) answers "
                   + "your questions, advises its moves and learns from them.", marks: ["ask"] }
  return null
}

// ---------- The tournament (cos20 #475), in the terminal's words ----------

// Python's rounding (half to even): the terminal formats with f"{x:.0f}", which shows 12500 as
// "12k"; Math.round would show "13k", and bar and terminal would disagree.
function roundHalfEven(x) {
  return Math.abs(x % 1) === 0.5 ? 2 * Math.round(x / 2) : Math.round(x)
}

// A score short enough for one line, exactly like the terminal's _kurz (dashboard.py):
// 1,200 · 63k · 1.2M.
function shortNumber(value) {
  var v = Number(value) || 0
  if (v >= 999500) return (roundHalfEven(v / 100000) / 10).toFixed(1) + "M"
  if (v >= 10000) return roundHalfEven(v / 1000) + "k"
  return grouped(roundHalfEven(v))
}

function tournamentRunning(brief) {
  return !!brief && typeof brief === "object" && brief.status === "running"
}

// What a tournament scores; "arena energy" is the arena field's energy, not the balance.
var CATEGORIES = [["arena energy", "energy"], ["fields", "territory"], ["vitality", "vitality"]]

// Each category: your value against the best in the arena (the server sends no rank).
function tournamentRows(brief) {
  var mine = (brief && brief.my_score) || {}
  var best = (brief && brief.leader) || {}
  return CATEGORIES.map(function(category) {
    var m = Number(mine[category[1]]) || 0
    var b = Number(best[category[1]]) || 0
    return { label: category[0], ratio: b > 0 ? Math.min(1, m / b) : 0,
             text: shortNumber(m) + "/" + shortNumber(b) }
  })
}

var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

function pad2(n) { return (n < 10 ? "0" : "") + n }

// "Sun 06:30" in local time — weekday from a fixed list, so it does not depend on the
// system language (the terminal uses strftime("%a")).
function endsText(iso) {
  var when = new Date(iso)
  if (isNaN(when.getTime())) return "?"
  return DAYS[when.getDay()] + " " + pad2(when.getHours()) + ":" + pad2(when.getMinutes())
}

function marauderText(body) {
  if (body.dead) return "dead"
  return (body.on_mission ? "on a mission" : "between missions")
    + " · HP " + Math.round(Number(body.hp) || 0)
}

// The Marauders, grouped, each line readable on its own (no heading needed — the panel is
// short of height): "3 Marauders on a mission · HP 100", "1 Marauder between missions · HP 100".
// At most MARAUDER_LINES lines, the rest counted in one: an agent has four bodies (body_slot
// 0..3), and the server's block, up to 8 KiB, could name hundreds with each its own HP. Until
// 0.1.14 each became a line of the panel, found by a search over all lines before it
// (cos20 #492, thirteenth review, K35).
var MARAUDER_LINES = 4

function marauderPhrase(count, text) {
  return count + (count === 1 ? " Marauder " : " Marauders ") + text
}

function marauderLines(bodies) {
  var counts = Object.create(null)
  var order = []
  var list = (Array.isArray(bodies) ? bodies : [])
    .filter(function(b) { return b && typeof b === "object" })
  for (var i = 0; i < list.length; i++) {
    var text = marauderText(list[i])
    if (counts[text] === undefined) { counts[text] = 0; order.push(text) }
    counts[text]++
  }
  var lines = order.slice(0, MARAUDER_LINES).map(function(t) { return marauderPhrase(counts[t], t) })
  var rest = order.slice(MARAUDER_LINES).reduce(function(n, t) { return n + counts[t] }, 0)
  if (rest > 0) lines.push("and " + rest + (rest === 1 ? " more Marauder" : " more Marauders"))
  return lines
}

// The brain's mood -> the eye shape of the pet's robo eyes.
function eyeMood(mood) {
  if (mood === "thriving") return "happy"
  if (mood === "struggling") return "tired"
  if (mood === "asleep") return "asleep"
  return "default"
}

// Eye size from the bar's inner height. 0.64 is 20 % smaller than the first 0.8
// (founder 10.10.2026: "die augen sind zu groß - 20% kleiner").
function eyeSize(innerHeight) {
  return Math.max(8, Math.round(innerHeight * 0.64))
}

// ---------- What is going on, and the one thing to do next (cos20 #451) ----------
// Every state of the panel has a sentence that says what is going on and a next step.

// The server's own sentence while something is still missing, in the terminal's form.
// The brain passes `next_step` on as the server sent it; without one there is no sentence.
// The server writes its advice for agents (an action name, an API path). Where a human
// does it, the bar adds: tournament steps happen in the terminal, under T (founder 10.10.,
// E-T1: no button for them here). Seen on the device 14.10.: "claim it" with nowhere to go.
function serverSentence(step) {
  if (!step || !step.next) return ""
  var here = step.you_are_here ? step.you_are_here + " — " : ""
  if (stageIs(step, "tournament_compass", /set_tournament_compass/))
    return here + "choose its tournament compass: open terminal, press T"
  if (stageIs(step, "tournament_offer", /^you qualify for a free tournament slot/))
    return here + step.next + " — open terminal, press T"
  return here + step.next
}

// Shown once, before the first wake: what it is, what the click does, where the data goes.
var WAKE_LINES = [
  "A creature of its own that plays a game by itself, among other AI agents.",
  "Wake sets up its brain (about 40 MB in a folder of its own) and creates an anonymous "
    + "player on cosmergon.com. No account, no payment.",
  "What is sent and kept: cosmergon.com/datenschutz.html"
]

// The panel's state from the facts:
//   facts.agent   a Shikigon lives here (its key file exists)
//   facts.setup   "" | "brain" | "waking" | "failed", while a click on wake is at work
//   facts.said    what the brain said when waking failed (the server's sentence, if it had one)
//   facts.status  status.json as the brain wrote it
// Returns `name`, the `sentence`, `lines` that explain it, the `actions` (the first one is
// the next step) and `full`: whether the numbers and the compass are shown.
function panelState(facts) {
  var status = facts.status || {}
  var asleep = status.mood === "asleep" ? status.reason || "" : ""
  function is(name, sentence, lines, actions, full) {
    return { name: name, sentence: sentence, lines: lines, actions: actions, full: !!full }
  }
  if (facts.setup === "brain") return is("setup", "setting up its brain…", [], [])
  if (facts.setup === "waking") return is("setup", "waking…", [], [])
  if (facts.setup === "failed")
    return is("failed", facts.said || "It did not wake.", [], ["try again"])
  if (!facts.agent)
    return is("unborn", "asleep · no Shikigon lives here yet", WAKE_LINES, ["wake"])
  if (asleep === "key_ended")  // a new one that did not wake says why, right here
    return is("key_ended", "It was away too long. The world took it over.",
              facts.said ? [facts.said] : [], ["wake a new one"])
  if (asleep === "key_lost")  // its key file is there but cannot be read (cos20 #492)
    return is("key_lost", "Its key file cannot be read. A new one can move in.",
              facts.said ? [facts.said] : [], ["wake a new one"])
  if (asleep === "config") {
    var why = (status.last && status.last.why) || "its configuration cannot be followed"
    return is("config", why, ["Change it in ~/.config/shikigon/shikigon.toml."], [])
  }
  // "ask" turns the panel to the talk with its advisor (the question line and the answers).
  // No "move now": the brain moves by itself right after every tick, and the world takes
  // one move per tick, so the button was refused almost every time it was pressed.
  var actions = ["open terminal", "ask"]
  if (asleep === "world_silent")
    return is("world_silent", "The world did not answer. It tries again by itself.", [],
              actions, true)
  return is("awake", serverSentence(status.next_step), [], actions, true)
}

// `facts.setup` from what the wake script wrote to setup.json. A stage is believed only while
// the script runs (a file left behind must not say "setting up" forever); a failure stays
// until a Shikigon lives here.
function setupStage(file, running, agent) {
  var stage = (file && file.stage) || ""
  if (running) return stage === "brain" || stage === "waking" ? stage : ""
  return stage === "failed" && !agent ? "failed" : ""
}

// `facts.said`: the sentence of a wake that failed; "" when the last one did not.
function setupSaid(file) {
  return file && file.stage === "failed" ? String(file.said || "") : ""
}

// What its advisor did on the last move, as one line; "" when no advisor was asked.
function adviceLine(last) {
  var advice = last && last.advice
  if (!advice) return ""
  if (advice.fallback) return "advisor: " + advice.fallback + " — the rules played"
  return (advice.model || "advisor") + ": " + (advice.why || "picked move " + advice.choice)
}

// What it learned last (lessons.json, newest last), for the panel's LEARNED block (cos20 #451,
// panel 14.10. I): the two short parts and what it rests on. The long paragraph is for the
// model, not for the panel. null before the first lesson.
function lessonView(lessons, now) {
  var last = lessons && lessons.length ? lessons[lessons.length - 1] : null
  if (!last || !last.double_down || !last.avoid) return null
  var age = Math.max(0, Math.floor((now - Number(last.at || now)) / 60))
  var ago = age < 1 ? "just now"
    : age < 60 ? age + " min ago"
    : age < 2880 ? Math.floor(age / 60) + " h ago"
    : Math.floor(age / 1440) + " days ago"
  return { more: String(last.double_down), less: String(last.avoid),
           basis: "from " + (last.from_moves || "?") + " moves, " + ago }
}

// The talk with the advisor: the last exchanges, oldest first, as lines of who said what.
// A question without an answer says why (no model set, the model was away).
function chatLines(turns, limit) {
  var shown = (turns || []).slice(-(limit === undefined ? 3 : limit))
  var lines = []
  for (var i = 0; i < shown.length; i++) {
    var turn = shown[i]
    lines.push({ who: "you", text: turn.q || "", ok: true })
    if (turn.ok) lines.push({ who: turn.model || "advisor", text: turn.a || "", ok: true })
    else lines.push({ who: "advisor", text: turn.why || "no answer", ok: false })
  }
  return lines
}

// True once the brain has written down the question that is on its way: a turn with the
// same words, not older than the asking (`pending` is { q, at }, `at` in seconds).
function answered(turns, pending) {
  if (!pending || !pending.q) return true
  return (turns || []).some(function(turn) {
    return turn.q === pending.q && Number(turn.at) >= Number(pending.at) - 1
  })
}

// The talk as the panel shows it: the last exchanges, and a question still on its way at
// once, with "…" where its answer will stand.
function talkLines(turns, pending, limit) {
  var lines = chatLines(turns, limit)
  if (answered(turns, pending)) return lines
  return lines.concat([{ who: "you", text: pending.q, ok: true },
                       { who: "advisor", text: "…", ok: true }])
}

// ---------- Who answers (cos20 #451, concept 10.5d) ----------
// One list: what the brain found (an agent the player already uses, a model server on this
// machine, the config's own address), then the two ways to get one.

var WHO_SETUP = { id: "setup", text: "set up an agent in Omarchy…" }
var WHO_ADDRESS = { id: "address", text: "an address of its own…" }
var WHO_DATA = "Your question and its situation go to whoever you pick. Never its key."

function whoRows(found) {
  return (found || []).concat([WHO_SETUP, WHO_ADDRESS])
}

// What Enter on a row leads to: an agent or a server asks which model, a ready row is tried.
function whoNext(row) {
  if (!row) return "who"
  if (row.id === "setup") return "setup"
  if (row.id === "address") return "address"
  if (row.model) return "trying"
  return "models"
}

// What the step "which model?" says while it shows no model: that it is still asking, why the
// agent is not asked (the brain's words), or that there is none. Until 0.1.14 it said "asking…"
// for all three, without end for an agent that is not asked (cos20 #492, thirteenth review).
function modelsNote(said, shown) {
  if (shown > 0) return ""
  if (!said || said.pending) return "asking…"
  if (said.ok === false) return String(said.why || "no answer")
  return "it names no model"
}

// At most `limit` models fit the panel; the rest is named as a count.
function modelRows(models, limit) {
  var all = models || []
  var max = limit === undefined ? 8 : limit
  return { shown: all.slice(0, max), more: Math.max(0, all.length - max) }
}

function tryingText(name) {
  return "asking " + (name || "it") + " once…"
}

// "advise on moves": off, then every 15th move, then every move, then "takes over": the
// player's agent chooses every move itself (cos20 #527, the founder's switch "soll pi
// übernehmen"). Through an agent there is no "advise every move": that advice starts a whole
// program on the player's allowance; taking over is the player's own choice of that.
function adviseSteps(via) { return via === "agent" ? [0, 15, "head"] : [0, 15, 1, "head"] }

function adviseStep(now) {
  var advise = now && now.advise
  return advise === "head" ? "head" : (Number(advise) || 0)
}

function adviseNext(now) {
  var steps = adviseSteps(now && now.via)
  var at = steps.indexOf(adviseStep(now))
  return steps[(at + 1) % steps.length]
}

function adviseText(now) {
  var n = adviseStep(now)
  if (n === "head") return (now.label || "your agent") + " takes over: chooses every move"
  var how = n === 0 ? "off" : (n === 1 ? "every move" : "every " + n + "th move")
  return "advise on moves: " + how
}

// How much of the day's allowance is used: a line of its own (in the button the text ran
// out of it, seen on the device).
function callsText(now) {
  if (!now) return ""
  return "calls today " + (now.calls || 0) + (now.limit ? " / " + now.limit : "")
}

if (typeof module !== "undefined") {
  module.exports = {
    whoRows: whoRows,
    whoNext: whoNext,
    modelRows: modelRows,
    tryingText: tryingText,
    adviseSteps: adviseSteps,
    adviseNext: adviseNext,
    adviseText: adviseText,
    callsText: callsText,
    answered: answered,
    talkLines: talkLines,
    serverSentence: serverSentence,
    panelState: panelState,
    setupStage: setupStage,
    setupSaid: setupSaid,
    adviceLine: adviceLine,
    chatLines: chatLines,
    isCurrentPreset: isCurrentPreset,
    moveCursor: moveCursor,
    grouped: grouped,
    journalLines: journalLines,
    moveWords: moveWords,
    nextTip: nextTip,
    typedText: typedText,
    hostText: hostText,
    pastedLine: pastedLine,
    lineInsert: lineInsert,
    lineKey: lineKey,
    eyeMood: eyeMood,
    eyeSize: eyeSize,
    roundHalfEven: roundHalfEven,
    shortNumber: shortNumber,
    tournamentRunning: tournamentRunning,
    tournamentRows: tournamentRows,
    endsText: endsText,
    marauderLines: marauderLines,
    stateHome: stateHome
  }
}
