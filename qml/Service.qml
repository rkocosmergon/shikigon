import QtQuick
import Quickshell
import Quickshell.Io
import "ShikigonModel.js" as Model

// Shikigon's service: keeps the brain running and answers shortcuts.
//
// The brain is a Python process (brain/shikigon_brain.py) in its own venv. It
// writes status.json and journal.jsonl; the bar widget reads those files and
// never talks to the network itself. The API key stays in the brain's private
// file and is never read into the shell.
//
// Waking goes through brain/shikigon-wake: it sets up the brain (or follows a plugin update)
// and registers an agent if none lives here. The brain runs only while that script does
// not: one of them at a time.
//
// Shortcuts (Hyprland keybinds, see README):
//   omarchy-shell shikigon tick
//   omarchy-shell shikigon instruct grow      (a compass word only, see Model.compassWord)
//   omarchy-shell shikigon terminal | wake | status
// The IPC target takes no free text: whatever stands in a command is readable by every local
// user (marketplace review #11550). Words and questions go through the panel or the terminal,
// on stdin.
//
// No pause here: pausing an agent is an explicit, confirmed step in the terminal
// client (cos20 #468), not a button in the bar.
Item {
  id: root

  property var shell: null

  // A word for the compass is done and none waits after it: the bar reads the journal again and
  // lets go of the compass it showed since the click (BarWidget, pendingCompass).
  signal instructed()

  readonly property string home: Quickshell.env("HOME")
  readonly property string stateHome: Model.stateHome(Quickshell.env("XDG_STATE_HOME"), home)
  readonly property string stateDir: stateHome + "/shikigon"
  readonly property string python: home + "/.local/share/shikigon/venv/bin/python"
  readonly property string brain: decodeURIComponent(
    String(Qt.resolvedUrl("../brain/shikigon_brain.py")).replace("file://", ""))
  readonly property string terminalScript: decodeURIComponent(
    String(Qt.resolvedUrl("../brain/shikigon-terminal")).replace("file://", ""))

  readonly property string wakeScript: decodeURIComponent(
    String(Qt.resolvedUrl("../brain/shikigon-wake")).replace("file://", ""))

  // awake: a Shikigon lives here (its key file exists). Never created without a click.
  property bool awake: false
  property string lastError: ""
  // What the wake script last wrote (setup.json: stage, said) and whether it is at work.
  property var setup: ({})
  // The wake script and the brain's commands the bar starts never run at once: it may move or
  // rebuild the environment they run from. A command waits behind a wake ("busy"), a wake behind
  // the commands still running (`wakeWaiting`); the game loop stops for it (`wantRunning`) and the
  // wake waits for its end (`runner` in `brainBusy`). Until 0.1.16 only the game loop stopped, and
  // a `tick` shortcut during a rebuild started Python in a half-filled environment (cos20 #492,
  // Betreuer-Durchgang 57b426a, K1); "wake a new one" ran beside a game loop that was still
  // ending: it could book into the files `register --new` had cleared, and when it ended after the
  // wake, nothing started it again (Betreuer-Durchgang 1d7ff4d, B2). The terminal, its `M` and a
  // sign-in start outside the bar and are not held here (README: close it before an update).
  property bool wakeWaiting: false
  readonly property bool settingUp: waker.running || wakeWaiting
  readonly property bool brainBusy: runner.running || ticker.running || advisorLister.running
    || modelLister.running || prober.running || asker.running || instructor.running
    || keyKeeper.running || adviser.running
  // The brain starts only after the wake script has looked at it once since the shell
  // started: after a plugin update that is where its environment is rebuilt.
  property bool brainChecked: false
  property bool greeted: false
  readonly property bool wantRunning: awake && brainChecked && !settingUp
  // Stops in a row of a brain that should run (Model.stopsAfter), and since when it ran.
  property int stops: 0
  property real startedAt: 0
  property bool exitSeen: false
  // Why it keeps stopping, for the panel. One stop says nothing: that is how a new system Python
  // gets repaired, and the brain is back within a minute.
  readonly property string stuck: awake && stops >= 2 && !runner.running && !settingUp
    ? Model.stuckSaid(Model.setupSaid(setup), lastError) : ""

  function brainCommand(args) { return [python, "-I", brain].concat(args) }

  function sync() {
    if (wantRunning && !runner.running) runner.running = true
    else if (!wantRunning && runner.running) { stoppedHere = true; runner.running = false }
  }

  function brainStopped(code) {
    stops = Model.stopsAfter(stops, Date.now() - startedAt, code)
    if (code === 0) return
    restartLater.interval = Model.restartWait(stops)
    restartLater.restart()
  }

  // One move now, a finite helper like the others (`ticker`): until 0.1.16 it went out detached,
  // with no end and no word when it could not start (marketplace review #14438).
  function tickNow() {
    if (!awake) return "asleep"
    if (ticker.running || settingUp) return "busy"
    ticker.running = true
    return "ok"
  }

  // The player's words go over stdin, never as an argument: arguments stand in
  // /proc/<pid>/cmdline for every local user (marketplace review #11550). One line each.
  function oneLine(text) { return String(text || "").replace(/\s+/g, " ").trim() }

  function instruct(text) {
    if (!awake) return "asleep"
    var said = oneLine(text)
    if (said === "") return "empty"
    if (settingUp) return "busy"
    // A click while the last word is still on its way is kept, the latest one wins, and goes
    // when that one is done: until 07.11.2026 it was dropped without a word (cos20 #492).
    if (instructor.running) { instructor.next = said; return "ok" }
    instructor.said = said
    instructor.running = true
    return "ok"
  }

  // The game itself lives in the terminal client (cosmergon-dashboard); the bar only shows.
  // Started by omarchy-launch-tui, which hands the path on as one argument (uwsm quotes it): no
  // `eval` of it, whatever the folder is called, and no window chosen by its title. Until
  // af58853 Omarchy's launch-or-focus starter brought any window titled like
  // org.omarchy.cosmergon to the front instead, a web page's too (cos20 #492,
  // Betreuer-Durchgang af58853, B4).
  function openTerminal() {
    Quickshell.execDetached(Model.outside(["omarchy-launch-tui",
                                           "--app-id=org.omarchy.cosmergon", terminalScript]))
    return "ok"
  }

  // A question for its advisor; the brain writes question and answer to chat.jsonl.
  function ask(text) {
    if (!awake) return "asleep"
    var asked = oneLine(text)
    if (asked === "") return "empty"
    // the bar waits for one question at a time anyway; none while its brain is set up
    if (asker.running || settingUp) return "busy"
    asker.said = asked
    asker.running = true
    return "ok"
  }

  // "try again" with a Shikigon: its wait is over. The timer looks after the brain, as ever.
  function tryAgain() {
    if (!restartLater.running) return "awake"
    restartLater.interval = 1
    restartLater.restart()
    return "ok"
  }

  // Creates a new agent on the Cosmergon server: only on an explicit request.
  // `fresh` replaces the one that lives here (after its key ended).
  function wake(fresh) {
    if (settingUp) return "busy"
    if (awake && !fresh) return tryAgain()
    startWaker(fresh, false)
    return "ok"
  }

  // The one way the wake script starts: at once, or once the brain's commands still running are
  // done (`brainBusy`, K1).
  function startWaker(fresh, look) {
    waker.fresh = !!fresh
    waker.look = !!look
    if (brainBusy) { wakeWaiting = true; return }
    waker.running = true
  }
  onBrainBusyChanged: {
    if (brainBusy || !wakeWaiting) return
    waker.running = true  // first: `settingUp` stays true, the game loop does not start between
    wakeWaiting = false
  }

  // Looks for the key file again (a register in a terminal works too). Called when the
  // panel opens; nothing polls while it sleeps.
  function recheck() { if (!credentialCheck.running) credentialCheck.running = true }

  onWantRunningChanged: sync()
  Component.onCompleted: recheck()

  // ---------- Finite helpers (marketplace review #14438) ----------
  // A helper that is meant to end says so exactly once, through `settled`, whichever way it
  // ended: it ran to its end, it could not start, or its deadline passed (Model.bounded:
  // `timeout` ends its whole process group and returns once it is reaped, so whatever waits
  // behind a helper starts only after that). `why` is "" when it ran to its end.
  // Quickshell (src/io/process.cpp): a finished process ends its output stream first, then sends
  // `exited`, then `runningChanged`; one that could not start sends `runningChanged` alone, so a
  // handler on `exited` or on the stream never ran for it. Until 0.1.16 only the brain's runner
  // knew that. The game loop (runner) and the terminal are meant to go on and stay outside.
  component Helper: Process {
    id: helper
    property var argv: []
    property int seconds: Model.HELPER_QUICK_S
    property bool exitSeen: false
    // What it printed, read through the bar's door (K47); null when nothing readable came.
    property var answer: null
    // A command of the brain: it always prints one JSON line, so none is a brain that broke off
    // (Model.helperWhy, Betreuer-Durchgang e859803, B4).
    property bool answers: false
    signal settled(int exitCode, string why, var answer)
    command: Model.bounded(seconds, argv)
    // `exitSeen` is let go of only by the `runningChanged` that follows every `exited`: once
    // per run, either way.
    function settle(code) {
      var said = answer
      answer = null
      settled(code, Model.helperWhy(code, answers ? said !== null : undefined), said)
    }
    onRunningChanged: {
      if (running) { exitSeen = false; answer = null }
      else if (exitSeen) exitSeen = false
      else settle(null)
    }
    // A process ended by a signal comes as CrashExit with the signal's number (measured on the
    // device: `timeout`'s KILL after the deadline reaches `timeout` itself, code 9): counted as
    // the shell counts it, 128 + the signal.
    onExited: (code, status) => {
      exitSeen = true
      settle(status === 0 ? code : 128 + code)
    }
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try { helper.answer = Model.readJson(text) } catch (e) { helper.answer = null }
      }
    }
  }

  // A helper that ended without its result says so on the desktop, once, in fixed words.
  function failed(what, why) {
    if (why === "" || notifier.running) return
    notifier.body = Model.helperNotice(what, why)
    notifier.running = true
  }
  Helper {
    id: notifier
    property string body: ""
    argv: Model.shell('notify-send -a Shikigon -- "Shikigon" "$1"', [body])
  }

  Helper {
    id: ticker
    answers: true
    seconds: Model.HELPER_ASKS_S
    argv: root.brainCommand(["tick"])
    onSettled: (exitCode, why) => root.failed("Moving now", why)
  }

  Helper {
    id: waker
    property bool fresh: false
    // Started here without a click: it looks after the brain and registers nothing (cos20 #630).
    property bool look: false
    seconds: Model.HELPER_SETUP_S
    argv: [root.wakeScript].concat(fresh ? ["--new"] : (look ? ["--look"] : []))
    onSettled: (exitCode, why) => {
      root.brainChecked = true
      setupFile.reload()
      root.recheck()
      root.failed("Waking", why)
    }
  }

  FileView {
    id: setupFile
    path: root.stateDir + "/setup.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try { root.setup = Model.readJson(text()) } catch (e) { root.setup = ({}) }
    }
    onLoadFailed: root.setup = ({})
  }

  // setup.json may not exist when the script starts, and a file that does not exist cannot
  // be watched: while the script runs, look twice a second.
  Timer {
    interval: 500
    running: waker.running
    repeat: true
    onTriggered: setupFile.reload()
  }

  // Said once, to someone who has just installed it and sees two closed eyes.
  // umask 077: what is made here, the state folder among it, is the player's alone (K2).
  Helper {
    id: greeter
    argv: Model.shell(
      'umask 077; test -e "$1/greeted" && exit 0; mkdir -p "$1" && : > "$1/greeted" && '
      + 'notify-send -a Shikigon -- "Shikigon moved into your bar" "Click its eyes to wake it."',
      [root.stateDir])
  }

  // ---------- First-time hints (cos20 #451): which one, ShikigonModel.nextTip ----------
  // tips.json keeps what was shown and when; status.json and advisor.json are the facts.
  // A file that does not exist cannot be watched, so while a Shikigon lives here they are
  // read again every 30 s, like the panel does.
  property var tips: ({ seen: [], at: 0 })
  property bool tipsRead: false
  property bool advisorPicked: false

  function maybeTip(status) {
    if (!root.tipsRead || tipper.running) return
    var now = Math.floor(Date.now() / 1000)
    var tip = Model.nextTip(status, root.tips.seen, root.tips.at, now, root.advisorPicked)
    if (!tip) return
    root.tips = { seen: (root.tips.seen || []).concat(tip.marks),
                  at: tip.id === "first_field" ? root.tips.at : now }
    tipper.title = tip.title
    tipper.body = tip.body  // fixed words only (Model.nextTip)
    tipper.saved = JSON.stringify(root.tips)
    tipper.running = true
  }

  FileView {
    id: tipsFile
    path: root.stateDir + "/tips.json"
    printErrors: false
    onLoaded: {
      try { root.tips = Model.readJson(text()) } catch (e) { root.tips = { seen: [], at: 0 } }
      root.tipsRead = true
    }
    onLoadFailed: root.tipsRead = true
  }

  FileView {
    id: advisorFile
    path: root.stateDir + "/advisor.json"
    printErrors: false
    onLoaded: root.advisorPicked = true
    onLoadFailed: root.advisorPicked = false
  }

  FileView {
    id: statusFile
    path: root.stateDir + "/status.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try { root.maybeTip(Model.readJson(text())) } catch (e) {}
    }
  }

  Timer {
    interval: 30000
    running: root.awake
    repeat: true
    onTriggered: { advisorFile.reload(); statusFile.reload() }
  }

  // Written before it is shown: a hint is never shown twice, even if the shell restarts.
  Helper {
    id: tipper
    property string title: ""
    property string body: ""
    property string saved: ""
    argv: Model.shell(
      'umask 077; mkdir -p "$4" && printf %s "$3" > "$4/tips.json.tmp" '
      + '&& mv "$4/tips.json.tmp" "$4/tips.json" && '
      + 'notify-send -a Shikigon -- "$1" "$2"',  // fixed words only, title and body (Model.nextTip)
      [title, body, saved, root.stateDir])
  }

  Process {
    id: runner
    // Tied to the shell's life (Model.tied): a shell that ended without tearing it down left it
    // playing on, and the next shell started a second one beside it (Betreuer-Durchgang 57b426a,
    // V1).
    command: Model.tied(root.brainCommand(["run"]))
    // A program that cannot be started says this and no `exited` (no environment left after a
    // failed install): nothing brought it back, and the panel said nothing (cos20 #492, B1).
    onRunningChanged: {
      if (running) {
        root.startedAt = Date.now()
        root.lastError = ""
        root.exitSeen = false
      } else if (root.exitSeen) {
        root.exitSeen = false
      } else {
        root.startedAt = Date.now()
        root.brainStopped(-1)
      }
    }
    stderr: SplitParser {
      onRead: data => root.lastError = Model.readLine(data).slice(0, 200)
    }
    onExited: code => {
      // Stopped here (the wake script runs): not a crash, or the timer woke it again every 30 s
      // and stopped it again (cos20 #628, F6). A SIGTERM from outside still brings it back.
      var mine = root.stoppedHere
      root.stoppedHere = false
      root.exitSeen = true
      if (root.restartWanted) { root.restartWanted = false; root.sync() }
      else if (!mine) root.brainStopped(code)
    }
  }

  // ---------- Who answers: the brain looks, tries and keeps; the service passes it on ----------
  // advisors: { now: { picked, label, via, advise, calls, limit }, found: [rows] }
  property var advisors: ({ now: { picked: false, label: "", via: "http", advise: 0,
                                   calls: 0, limit: 0 }, found: [] })
  property var models: ({ where: "", ok: false, models: [], why: "" })
  property bool advisorsRead: false   // read once, on the first ask (not when the panel opens)
  property var tried: null            // what the last probe said; null while it runs
  property bool restartWanted: false
  property bool stoppedHere: false

  function refreshAdvisors() {
    if (awake && !advisorLister.running && !settingUp) advisorLister.running = true
  }
  function listModels(where) {
    if (settingUp) {  // its brain is set up (K1): said where the models would stand
      models = ({ where: where, ok: false, models: [], why: "its brain is being set up" })
      return
    }
    models = ({ where: where, ok: false, models: [], why: "", pending: true })
    // One asks at a time; the answer of the old address never stands under the new (#628, F11).
    if (modelLister.running) { modelLister.next = where; return }
    modelLister.where = where
    modelLister.running = true
  }
  // One real question to a connection; the brain keeps it only if an answer comes.
  function tryConnection(connection, model) {
    if (prober.running || settingUp) return "busy"
    tried = null
    prober.connection = connection
    prober.model = model || ""
    prober.running = true
    return "ok"
  }
  // One key at a time: a second Enter while the first is written replaced it unwritten, and
  // the field was empty already (Fehlersuche Stück 24, K22). "busy" keeps it in the field.
  function keepKey(key) {
    if (keyKeeper.running || settingUp) return "busy"
    keyKeeper.secret = key
    keyKeeper.running = true
    return "ok"
  }
  // 0 = off, "head" = its agent takes over; the brain reads it at its start, so it is
  // started again.
  function setAdvise(every) {
    if (settingUp) return "busy"
    adviser.value = every === "head" ? "head" : (every > 0 ? String(every) : "off")
    adviser.running = true
    return "ok"
  }
  function restartBrain() {
    if (!runner.running) { sync(); return }
    restartWanted = true
    runner.running = false
  }
  // Omarchy's own chooser: it installs the agent and starts it; signing in is the agent's.
  function openAgentSetup() {
    Quickshell.execDetached(Model.outside(["omarchy-menu", "summon", "setup.default.agent"]))
  }
  // The agent's own program, for its sign-in, started by the brain in that terminal: the brain
  // checks the path it starts, so Omarchy's placeholder never runs from here (until 0.1.14 the
  // bar started the agent by its name, cos20 #492, thirteenth review). `agent` is a name from the
  // brain's own list.
  function openSignIn(agent) {
    Quickshell.execDetached(Model.outside(["omarchy-launch-tui", "--app-id=org.omarchy.agent"]
                                          .concat(brainCommand(["sign-in", String(agent)]))))
  }

  Helper {
    id: advisorLister
    answers: true
    seconds: Model.HELPER_ASKS_S
    argv: root.brainCommand(["advisor", "list"])
    onSettled: (exitCode, why, answer) => {
      if (answer) root.advisors = answer
      root.advisorsRead = true
      root.failed("Looking for advisors", why)
    }
  }
  // An address and a model go over stdin too: an address can carry a name and a password
  // (https://me:pw@…), and arguments stand in every local user's process list (cos20 #492 F3).
  Helper {
    id: modelLister
    answers: true
    property string where: ""
    property string next: ""
    seconds: Model.HELPER_ASKS_S
    argv: root.brainCommand(["advisor", "models"])
    stdinEnabled: true
    onStarted: write(JSON.stringify({ where: where }) + "\n")
    onSettled: (exitCode, why, answer) => {
      if (next) {  // the answer of the old address never stands under the new (#628, F11)
        where = next
        next = ""
        running = true
        return
      }
      var models = why ? { ok: false, models: [], why: why }
                       : (answer || { ok: false, models: [], why: "no answer" })
      models.where = where
      root.models = models
    }
  }
  Helper {
    id: prober
    answers: true
    property string connection: ""
    property string model: ""
    seconds: Model.HELPER_ASKS_S
    argv: root.brainCommand(["advisor", "try"])
    stdinEnabled: true
    onStarted: write(JSON.stringify({ connection: connection, model: model }) + "\n")
    onSettled: (exitCode, why, answer) => {
      root.tried = why ? { ok: false, why: why } : (answer || { ok: false, why: "no answer" })
      root.refreshAdvisors()
    }
  }
  // A question and an instruction: over stdin like the key below (see oneLine).
  Helper {
    id: asker
    answers: true
    property string said: ""
    seconds: Model.HELPER_ASKS_S
    argv: root.brainCommand(["ask"])
    stdinEnabled: true
    onStarted: {
      write(said + "\n")
      said = ""
    }
    onSettled: (exitCode, why) => root.failed("Asking", why)
  }
  Helper {
    id: instructor
    answers: true
    property string said: ""
    property string next: ""
    seconds: Model.HELPER_ASKS_S
    argv: root.brainCommand(["instruct"])
    stdinEnabled: true
    onStarted: {
      write(said + "\n")
      said = ""
    }
    onSettled: (exitCode, why) => {
      root.failed("Passing on the word", why)
      if (next === "") { root.instructed(); return }
      said = next
      next = ""
      running = true
    }
  }
  // A key an address asked for goes over stdin, never as an argument, with the address it is
  // for: the brain sends it there and nowhere else (cos20 #492 F4).
  Helper {
    id: keyKeeper
    answers: true
    property string secret: ""
    argv: root.brainCommand(["advisor", "key"])
    stdinEnabled: true
    onStarted: {
      write(JSON.stringify({ key: secret, where: root.models.where }) + "\n")
      secret = ""
    }
    onSettled: (exitCode, why) => {
      root.failed("Keeping the key", why)
      root.listModels(root.models.where)
    }
  }
  Helper {
    id: adviser
    answers: true
    property string value: "off"
    argv: root.brainCommand(["advisor", "advise", value])
    onSettled: (exitCode, why) => {
      root.failed("Changing the advice", why)
      root.refreshAdvisors()
      root.restartBrain()
    }
  }

  // A crashed brain comes back, later each time (brainStopped), and through the wake script: it
  // looks whether the brain can still load its packages and rebuilds it if not. That is what an
  // update of the system's Python does to it, without a restart of the shell (14.10.2026).
  Timer {
    id: restartLater
    interval: 30000
    onTriggered: {
      if (runner.running) return // came back meanwhile: waking stops it for nothing (#628, F6)
      if (!root.awake || root.settingUp) { root.sync(); return }
      root.startWaker(false, true)
    }
  }

  // A start that failed counts as no key file: it sleeps and greets, as before a first wake.
  Helper {
    id: credentialCheck
    argv: ["test", "-f", root.stateDir + "/credentials.json"]
    onSettled: (exitCode, why) => {
      root.awake = (why === "" && exitCode === 0)
      if (root.awake && !root.brainChecked && !root.settingUp) {
        root.startWaker(false, true)
      } else if (!root.awake && !root.greeted) {
        root.greeted = true
        greeter.running = true
      }
    }
  }

  IpcHandler {
    target: "shikigon"

    function tick(): string { return root.tickNow() }
    // A compass word only (grow, defend …): free text would stand in the caller's argv.
    function instruct(word: string): string {
      var preset = Model.compassWord(word)
      return preset === "" ? "words and questions go through the panel or the terminal"
                           : root.instruct(preset)
    }
    function terminal(): string { return root.openTerminal() }
    function wake(): string { return root.wake(false) }
    function status(): string {
      return JSON.stringify({ awake: root.awake, running: runner.running,
                              settingUp: root.settingUp, setup: root.setup,
                              lastError: root.lastError })
    }
  }
}
