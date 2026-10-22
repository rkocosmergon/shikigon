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

  readonly property string home: Quickshell.env("HOME")
  readonly property string stateHome: Quickshell.env("XDG_STATE_HOME") || (home + "/.local/state")
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
  readonly property bool settingUp: waker.running
  // The brain starts only after the wake script has looked at it once since the shell
  // started: after a plugin update that is where its environment is rebuilt.
  property bool brainChecked: false
  property bool greeted: false
  readonly property bool wantRunning: awake && brainChecked && !settingUp

  function brainCommand(args) { return [python, brain].concat(args) }

  function sync() {
    if (wantRunning && !runner.running) runner.running = true
    else if (!wantRunning && runner.running) runner.running = false
  }

  function tickNow() {
    if (!awake) return "asleep"
    Quickshell.execDetached(brainCommand(["tick"]))
    return "ok"
  }

  // The player's words go over stdin, never as an argument: arguments stand in
  // /proc/<pid>/cmdline for every local user (marketplace review #11550). One line each.
  function oneLine(text) { return String(text || "").replace(/\s+/g, " ").trim() }

  function instruct(text) {
    if (!awake) return "asleep"
    var said = oneLine(text)
    if (said === "") return "empty"
    if (instructor.running) return "busy"
    instructor.said = said
    instructor.running = true
    return "ok"
  }

  // The game itself lives in the terminal client (cosmergon-dashboard); the bar only shows.
  function openTerminal() {
    Quickshell.execDetached(["omarchy-launch-or-focus-tui", "--app-id=org.omarchy.cosmergon",
                             terminalScript])
    return "ok"
  }

  // A question for its advisor; the brain writes question and answer to chat.jsonl.
  function ask(text) {
    if (!awake) return "asleep"
    var asked = oneLine(text)
    if (asked === "") return "empty"
    if (asker.running) return "busy"  // the bar waits for one question at a time anyway
    asker.said = asked
    asker.running = true
    return "ok"
  }

  // Creates a new agent on the Cosmergon server: only on an explicit request.
  // `fresh` replaces the one that lives here (after its key ended).
  function wake(fresh) {
    if (waker.running) return "busy"
    if (awake && !fresh) return "awake"
    waker.fresh = !!fresh
    waker.running = true
    return "ok"
  }

  // Looks for the key file again (a register in a terminal works too). Called when the
  // panel opens; nothing polls while it sleeps.
  function recheck() { if (!credentialCheck.running) credentialCheck.running = true }

  onWantRunningChanged: sync()
  Component.onCompleted: recheck()

  Process {
    id: waker
    property bool fresh: false
    command: [root.wakeScript].concat(fresh ? ["--new"] : [])
    onExited: {
      root.brainChecked = true
      setupFile.reload()
      root.recheck()
    }
  }

  FileView {
    id: setupFile
    path: root.stateDir + "/setup.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try { root.setup = JSON.parse(text()) } catch (e) { root.setup = ({}) }
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
  Process {
    id: greeter
    command: ["sh", "-c",
      'umask 077; test -e "$1/greeted" && exit 0; mkdir -p "$1" && : > "$1/greeted" && '
      + 'notify-send -a Shikigon "Shikigon moved into your bar" "Click its eyes to wake it."',
      "sh", root.stateDir]
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
    tipper.body = Model.notifyBody(tip.body)
    tipper.saved = JSON.stringify(root.tips)
    tipper.running = true
  }

  FileView {
    id: tipsFile
    path: root.stateDir + "/tips.json"
    printErrors: false
    onLoaded: {
      try { root.tips = JSON.parse(text()) } catch (e) { root.tips = { seen: [], at: 0 } }
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
      try { root.maybeTip(JSON.parse(text())) } catch (e) {}
    }
  }

  Timer {
    interval: 30000
    running: root.awake
    repeat: true
    onTriggered: { advisorFile.reload(); statusFile.reload() }
  }

  // Written before it is shown: a hint is never shown twice, even if the shell restarts.
  Process {
    id: tipper
    property string title: ""
    property string body: ""
    property string saved: ""
    command: ["sh", "-c",
      'umask 077; mkdir -p "$4" && printf %s "$3" > "$4/tips.json.tmp" '
      + '&& mv "$4/tips.json.tmp" "$4/tips.json" && '
      + 'notify-send -a Shikigon "$1" "$2"',
      "sh", title, body, saved, root.stateDir]
  }

  Process {
    id: runner
    command: root.brainCommand(["run"])
    stderr: SplitParser {
      onRead: data => root.lastError = String(data).slice(0, 200)
    }
    onExited: code => {
      if (root.restartWanted) { root.restartWanted = false; root.sync() }
      else if (code !== 0) restartLater.restart()
    }
  }

  // ---------- Who answers: the brain looks, tries and keeps; the service passes it on ----------
  // advisors: { now: { picked, label, via, advise, calls, limit }, found: [rows] }
  property var advisors: ({ now: { picked: false, label: "", via: "http", advise: 0,
                                   calls: 0, limit: 0 }, found: [] })
  property var models: ({ where: "", ok: false, models: [], why: "" })
  property var tried: null            // what the last probe said; null while it runs
  property bool restartWanted: false

  function refreshAdvisors() { if (awake && !advisorLister.running) advisorLister.running = true }
  function listModels(where) {
    modelLister.where = where
    models = ({ where: where, ok: false, models: [], why: "", pending: true })
    modelLister.running = true
  }
  // One real question to a connection; the brain keeps it only if an answer comes.
  function tryConnection(connection, model) {
    if (prober.running) return "busy"
    tried = null
    prober.connection = connection
    prober.model = model || ""
    prober.running = true
    return "ok"
  }
  function keepKey(key) { keyKeeper.secret = key; keyKeeper.running = true }
  // 0 = off, "head" = its agent takes over; the brain reads it at its start, so it is
  // started again.
  function setAdvise(every) {
    adviser.value = every === "head" ? "head" : (every > 0 ? String(every) : "off")
    adviser.running = true
  }
  function restartBrain() {
    if (!runner.running) { sync(); return }
    restartWanted = true
    runner.running = false
  }
  // Omarchy's own chooser: it installs the agent and starts it; signing in is the agent's.
  function openAgentSetup() {
    Quickshell.execDetached(["omarchy-menu", "summon", "setup.default.agent"])
  }
  function openSignIn(argv) {
    Quickshell.execDetached(["omarchy-launch-tui", "--app-id=org.omarchy.agent"].concat(argv))
  }

  function parsed(text, fallback) {
    try { return JSON.parse(text) } catch (e) { return fallback }
  }

  Process {
    id: advisorLister
    command: root.brainCommand(["advisor", "list"])
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.advisors = root.parsed(text, root.advisors)
    }
  }
  // An address and a model go over stdin too: an address can carry a name and a password
  // (https://me:pw@…), and arguments stand in every local user's process list (cos20 #492 F3).
  Process {
    id: modelLister
    property string where: ""
    command: root.brainCommand(["advisor", "models"])
    stdinEnabled: true
    onStarted: write(JSON.stringify({ where: where }) + "\n")
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var said = root.parsed(text, { ok: false, models: [], why: "no answer" })
        said.where = modelLister.where
        root.models = said
      }
    }
  }
  Process {
    id: prober
    property string connection: ""
    property string model: ""
    command: root.brainCommand(["advisor", "try"])
    stdinEnabled: true
    onStarted: write(JSON.stringify({ connection: connection, model: model }) + "\n")
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.tried = root.parsed(text, { ok: false, why: "no answer" })
        root.refreshAdvisors()
      }
    }
  }
  // A question and an instruction: over stdin like the key below (see oneLine).
  Process {
    id: asker
    property string said: ""
    command: root.brainCommand(["ask"])
    stdinEnabled: true
    onStarted: {
      write(said + "\n")
      said = ""
    }
  }
  Process {
    id: instructor
    property string said: ""
    command: root.brainCommand(["instruct"])
    stdinEnabled: true
    onStarted: {
      write(said + "\n")
      said = ""
    }
  }
  // A key an address asked for goes over stdin, never as an argument, with the address it is
  // for: the brain sends it there and nowhere else (cos20 #492 F4).
  Process {
    id: keyKeeper
    property string secret: ""
    command: root.brainCommand(["advisor", "key"])
    stdinEnabled: true
    onStarted: {
      write(JSON.stringify({ key: secret, where: root.models.where }) + "\n")
      secret = ""
    }
    onExited: root.listModels(root.models.where)
  }
  Process {
    id: adviser
    property string value: "off"
    command: root.brainCommand(["advisor", "advise", value])
    onExited: { root.refreshAdvisors(); root.restartBrain() }
  }

  // A crashed brain comes back, but not in a tight loop, and through the wake script: it looks
  // whether the brain can still load its packages and rebuilds it if not. That is what an
  // update of the system's Python does to it, without a restart of the shell (14.10.2026).
  Timer {
    id: restartLater
    interval: 30000
    onTriggered: {
      if (!root.awake || waker.running) { root.sync(); return }
      waker.fresh = false
      waker.running = true
    }
  }

  Process {
    id: credentialCheck
    command: ["test", "-f", root.stateDir + "/credentials.json"]
    onExited: code => {
      root.awake = (code === 0)
      if (root.awake && !root.brainChecked && !waker.running) {
        waker.fresh = false
        waker.running = true
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
