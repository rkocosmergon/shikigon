// Inline components and delegates below read this file's ids (root, presetGrid, actionRow):
// bound to the context they are made in, as Qt recommends for that (qmllint: unqualified).
pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "ShikigonModel.js" as Model

// Shikigon in the bar: two eyes that show its mood, and a panel with its numbers,
// its journal and the compass. Reads only the files the brain writes.
//
// The panel is built from the shell's own kit (KeyboardPanel, PanelKeyCatcher, Button,
// PanelSectionHeader, PanelSeparator, Style.font / Style.space), so corners, borders,
// hover and fonts come from the user's theme. Keys: arrows or h/j/k/l move, Enter
// activates, Esc closes, Tab goes to the neighbouring panel, PgUp/PgDn scroll the middle.
// In the question line the keys write; Enter sends, Esc gives them back to the panel.
BarWidget {
  id: root
  moduleName: "cosmergon.shikigon"

  readonly property var service: bar && bar.shell && bar.shell.serviceFor
    ? bar.shell.serviceFor("cosmergon.shikigon") : null
  readonly property string stateDir: (Quickshell.env("XDG_STATE_HOME")
    || (Quickshell.env("HOME") + "/.local/state")) + "/shikigon"
  // The panel uses the theme's foreground (as omarchy.agents does); the eyes are drawn on
  // the bar itself and use the bar's foreground, which differs on a transparent bar
  // (Bar.qml: barForeground, as ActiveWindow uses it). Palette through Commons.Color:
  // Qt 6.12 shadows an unqualified Color (shell-dev.md).
  readonly property color ink: bar ? bar.foreground : Commons.Color.foreground
  readonly property color eyeInk: bar ? bar.barForeground : Commons.Color.foreground
  readonly property color dim: Qt.darker(ink, 1.4)
  readonly property string font: bar ? bar.fontFamily : "monospace"

  property var status: ({ mood: "asleep" })
  property var journal: []
  property var lessons: []
  property bool popupOpen: false

  readonly property string mood: status && status.mood ? status.mood : "asleep"
  readonly property bool awake: service ? service.awake : false
  readonly property string compass: status && status.compass ? String(status.compass) : ""
  // The tournament it plays in (cos20 #475): the server's block, passed on by the brain.
  // While it runs it is the game; the panel shows it first and the world steps back.
  readonly property var tournament: status && Model.tournamentRunning(status.tournament)
    ? status.tournament : null
  readonly property bool inTournament: tournament !== null

  // What is going on and the one thing to do next (Model.panelState): every state of the
  // panel has a sentence and a next step, also "its key ended" and a wake that failed.
  readonly property var view: Model.panelState({
    agent: awake,
    setup: Model.setupStage(service ? service.setup : null,
                            service ? service.settingUp : false, awake),
    said: Model.setupSaid(service ? service.setup : null),
    status: status
  })

  // The talk with its advisor: what the brain wrote to chat.jsonl, and a question on its way.
  // "ask" turns the panel to the talk (the numbers step back, so it fits a small screen
  // also in a tournament); Esc turns it back.
  property var chat: []
  property var pending: null
  property bool talking: false
  onTalkingChanged: if (!talking) scroll.contentY = 0   // back from the talk: from the top
  readonly property var talk: Model.talkLines(chat, pending, 2)

  // Shape contract for shell summon/hide/toggle (same as first-party panel widgets).
  readonly property bool opened: popupOpen
  function open() {
    popupOpen = true
    readPanelFiles()
    scroll.contentY = 0
    if (service) { service.recheck(); service.refreshAdvisors() }
  }
  function close() { popupOpen = false; cursor = -1; talking = false; step = "talk" }
  function toggle() { if (popupOpen) close(); else open() }

  // ---------- Panel: what the keys and the mouse can reach ----------
  // One grid for the cursor: the compass presets, then the actions.
  readonly property var presets: [
    "grow", "defend", "attack", "trade", "cooperate", "explore", "free"
  ]
  readonly property var actions: view.actions
  readonly property int presetCount: view.full ? presets.length : 0
  readonly property int presetColumns: 4
  property int cursor: -1

  // Three zones, so the panel never runs off the screen: the head and the foot (with the
  // buttons) stay, the middle scrolls when the screen is too short for all of it — as the
  // list in the shell's network panel does. The card asks for the full height;
  // KeyboardPanel caps it at the screen.
  readonly property real zoneGap: Style.space(14)
  readonly property real wantedHeight: Math.ceil(head.implicitHeight
    + (scrolled.implicitHeight > 0 ? zoneGap + scrolled.implicitHeight : 0)
    + (foot.implicitHeight > 0 ? zoneGap + foot.implicitHeight : 0))

  // The logic lives in ShikigonModel.js (tested with qmltestrunner, tests/qml).
  function isCurrentPreset(name) { return Model.isCurrentPreset(name, compass) }

  function moveCursor(dx, dy) {
    if (talking) { moveInTalk(dx, dy); return }
    cursor = Model.moveCursor(cursor, dx, dy, presetCount + actions.length, presetColumns)
  }

  function activate(index) {
    if (talking) { activateInTalk(); return }
    if (!service || index < 0) return
    if (index < presetCount) { service.instruct(presets[index]); return }
    var action = actions[index - presetCount]
    if (action === "open terminal") { service.openTerminal(); close() }
    else if (action === "ask") startTalking()
    else if (action === "wake" || action === "try again") service.wake(false)
    else if (action === "wake a new one") service.wake(true)
  }

  // ---------- The talk, and who answers (Model: whoRows, whoNext, adviseNext) ----------
  // step: talk (the question line) | who (the list) | address | key | models | trying | failed.
  // In a list the arrows move `pick` and Enter takes it; in a line the keys write.
  property string step: "talk"
  property int pick: 0
  property var pickedRow: null      // the row being set up: { id, name, sign_in? }
  property string failure: ""
  readonly property var advisorNow: service && service.advisors ? service.advisors.now : null
  readonly property bool advisorPicked: !!(advisorNow && advisorNow.picked)
  readonly property var whoList: Model.whoRows(
    service && service.advisors ? service.advisors.found : [])
  readonly property var modelList: Model.modelRows(
    step === "models" && pickedRow && pickedRow.models ? pickedRow.models
      : (service && service.models ? service.models.models : []))
  readonly property bool typing:
    askField.activeFocus || addressField.activeFocus || keyField.activeFocus

  function startTalking() {
    talking = true
    if (service) service.refreshAdvisors()
    toStep(advisorPicked ? "talk" : "who")
  }
  function stopTalking() { talking = false; step = "talk"; keyCatcher.forceActiveFocus() }
  // Esc: out of a list or a line back to the talk if someone answers, else out of the view.
  function backInTalk() {
    if (step !== "talk" && step !== "who") toStep("who")
    else if (step === "who" && advisorPicked) toStep("talk")
    else stopTalking()
  }
  function toStep(next) {
    if (next !== "failed") failure = ""
    step = next
    pick = 0
    Qt.callLater(function() {
      if (!root.talking) return
      if (root.step === "talk") askField.forceActiveFocus()
      else if (root.step === "address") addressField.forceActiveFocus()
      else if (root.step === "key") keyField.forceActiveFocus()
      else keyCatcher.forceActiveFocus()
    })
  }
  function listLength() {
    if (step === "who") return whoList.length
    if (step === "models") return modelList.shown.length
    if (step === "failed") return pickedRow && pickedRow.sign_in ? 2 : 1
    if (step === "talk") return 2   // "answers: …" and "advise on moves: …", above the line
    return 0
  }
  function moveInTalk(dx, dy) {
    var n = listLength()
    // below the two rows of the talk comes the question line
    if (step === "talk" && dy > 0 && pick >= n - 1) { askField.forceActiveFocus(); return }
    if (n > 0) pick = Math.max(0, Math.min(n - 1, pick + (dy !== 0 ? dy : dx)))
  }
  function activateInTalk() {
    if (!service) return
    if (step === "who") takeRow(whoList[pick])
    else if (step === "models") tryPicked(modelList.shown[pick])
    else if (step === "failed") {
      if (pick === 1 && pickedRow && pickedRow.sign_in) {
        service.openSignIn(pickedRow.sign_in)
        close()
      }
      else toStep("who")
    } else if (step === "talk") {
      if (pick === 0) toStep("who")
      else service.setAdvise(Model.adviseNext(advisorNow))
    }
  }
  function takeRow(row) {
    var next = Model.whoNext(row)
    if (next === "setup") { service.openAgentSetup(); close(); return }
    if (next === "address") { toStep("address"); return }
    pickedRow = row
    if (next === "trying") { tryPicked(row.model); return }
    if (!row.models) service.listModels(row.id)   // an agent: ask it which models it has
    toStep("models")
  }
  function tryPicked(model) {
    if (!pickedRow || service.tryConnection(pickedRow.id, model || "") !== "ok") return
    toStep("trying")
  }
  function submitAddress() {
    var where = addressField.text.trim()
    if (where === "") return
    failure = ""
    pickedRow = { id: "http:" + where, name: where }
    service.listModels(where)
  }
  function submitKey() {
    if (keyField.text === "") return
    service.keepKey(keyField.text)
    keyField.text = ""
  }
  // Enter sends the question and keeps the line.
  function submitQuestion() {
    var asked = askField.text.trim()
    if (asked === "" || !service || service.ask(asked) !== "ok") return
    pending = { q: asked, at: seconds() }
    askField.text = ""
  }

  Connections {
    target: root.service
    ignoreUnknownSignals: true
    // The probe came back: someone answers now, or the reason stands there.
    function onTriedChanged() {
      if (root.step !== "trying" || !root.service.tried) return
      if (root.service.tried.ok) root.toStep("talk")
      else { root.failure = root.service.tried.why || "no answer"; root.toStep("failed") }
    }
    // An address said what it offers: its models, or that it wants a key, or nothing.
    function onModelsChanged() {
      var m = root.service.models
      if (!m || m.pending || (root.step !== "address" && root.step !== "key")) return
      if (m.ok) root.toStep("models")
      else if (m.needs_key) root.toStep("key")
      else root.failure = m.why || "no answer"
    }
    function onAdvisorsChanged() {
      if (root.talking && root.step === "talk" && !root.advisorPicked) root.toStep("who")
    }
  }


  implicitWidth: eyes.width + Style.space(8)
  implicitHeight: barSize

  // watchChanges cannot watch a file that does not exist yet (the brain writes it
  // only after waking), so the files are also re-read every few seconds — only while it is
  // awake. Journal, talk and lessons are shown only in the open panel, so they are read only
  // then: this runs in the shared shell, and the journal is up to 256 KiB (cos20 #492, H7).
  // Their path is set only while the panel is open: a FileView loads its file as soon as it
  // has a path (`preload` is on by default), and an empty path lets go of it.
  Timer {
    interval: 5000
    running: root.awake
    repeat: true
    onTriggered: {
      statusFile.reload()
      if (root.popupOpen) root.readPanelFiles()
    }
  }
  function readPanelFiles() { journalFile.reload(); chatFile.reload(); lessonsFile.reload() }

  // While a question is on its way, look for its answer every second; a question nobody
  // answered within a minute and a half is dropped (the brain waits three timeouts).
  Timer {
    interval: 1000
    running: root.pending !== null
    repeat: true
    onTriggered: {
      chatFile.reload()
      if (Model.answered(root.chat, root.pending) || root.seconds() - root.pending.at > 90)
        root.pending = null
    }
  }

  FileView {
    id: chatFile
    path: root.popupOpen || root.pending !== null ? root.stateDir + "/chat.jsonl" : ""
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      var lines = text().trim().split("\n").slice(-3)
      var parsed = []
      for (var i = 0; i < lines.length; i++) {
        try { parsed.push(JSON.parse(lines[i])) } catch (e) {}
      }
      root.chat = parsed
    }
  }

  FileView {
    id: statusFile
    path: root.stateDir + "/status.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try { root.status = JSON.parse(text()) } catch (e) { root.status = { mood: "asleep" } }
    }
    onLoadFailed: root.status = { mood: "asleep" }
  }

  // What it learned (cos20 #451): the brain keeps the last three lessons, newest last.
  FileView {
    id: lessonsFile
    path: root.popupOpen ? root.stateDir + "/lessons.json" : ""
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try { root.lessons = JSON.parse(text()) } catch (e) { root.lessons = [] }
    }
    onLoadFailed: root.lessons = []
  }

  FileView {
    id: journalFile
    path: root.popupOpen ? root.stateDir + "/journal.jsonl" : ""
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      var lines = text().trim().split("\n").slice(-12).reverse()
      var parsed = []
      for (var i = 0; i < lines.length; i++) {
        try { parsed.push(JSON.parse(lines[i])) } catch (e) {}
      }
      root.journal = parsed
    }
  }

  // ---------- Eyes ----------
  // The same eyes as the Cosmergon pet on its OLED (cosmergon-pet, robo_eyes.py): one
  // rounded shape, the mood is a lid laid over it, the gaze wanders and follows smoothly,
  // a blink is a height that falls and rises. Drawn only while something moves.
  //   asleep → closed · content → open · thriving → happy (lid from below) ·
  //   struggling → tired (lid hangs outside) · a failed move → a short shake.
  readonly property string eyeMood: Model.eyeMood(mood)
  property real openness: 1.0          // 1 open … 0 closed
  property real gazeX: 0               // where it looks, -1 … 1
  property real gazeY: 0
  property real targetX: 0
  property real targetY: 0
  property real blinkUntil: 0          // seconds
  property real shakeUntil: 0
  property string shakeKind: ""        // "confused" (sideways) | "laugh" (up and down)
  property real lastFrame: 0
  property real lastFailureAt: 0

  function seconds() { return Date.now() / 1000 }
  function animate() { if (!frame.running) { lastFrame = seconds(); frame.start() } }
  function blink() { blinkUntil = seconds() + 0.18; animate() }
  function shake(kind, duration) { shakeKind = kind; shakeUntil = seconds() + duration; animate() }

  // One frame of the eyes (named so it does not collide with the talk's `step`).
  function frameStep() {
    var now = seconds()
    var dt = Math.max(0, now - lastFrame)
    lastFrame = now
    // A sine, not a line: a lid that closes linearly reads as a flap, not a blink.
    openness = now < blinkUntil ? Math.abs(Math.cos((blinkUntil - now) / 0.18 * Math.PI)) : 1.0
    // The gaze follows its target; 0.12 s reads as a lively jump of the eye.
    var share = 1 - Math.exp(-dt / 0.12)
    gazeX += (targetX - gazeX) * share
    gazeY += (targetY - gazeY) * share
    if (now >= blinkUntil && now >= shakeUntil
        && Math.abs(targetX - gazeX) < 0.02 && Math.abs(targetY - gazeY) < 0.02) {
      gazeX = targetX
      gazeY = targetY
      frame.stop()
    }
    eyes.requestPaint()
  }

  Timer { id: frame; interval: 33; repeat: true; onTriggered: root.frameStep() }

  // It blinks now and then, and looks around, unless it sleeps.
  Timer {
    interval: 4000
    running: root.eyeMood !== "asleep"
    repeat: true
    onTriggered: { interval = 4000 + Math.floor(Math.random() * 3000); root.blink() }
  }
  Timer {
    interval: 3000
    running: root.eyeMood !== "asleep"
    repeat: true
    onTriggered: {
      interval = 3000 + Math.floor(Math.random() * 2000)
      var looks = [[0, 0], [0, 0], [0, -1], [1, -1], [1, 0],
                   [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1]]
      var look = looks[Math.floor(Math.random() * looks.length)]
      root.targetX = look[0]
      root.targetY = look[1]
      root.animate()
    }
  }

  onEyeMoodChanged: {
    if (eyeMood === "asleep") { targetX = 0; targetY = 0; gazeX = 0; gazeY = 0 }
    else if (eyeMood === "happy") shake("laugh", 0.8)
    eyes.requestPaint()
  }
  onStatusChanged: {
    var last = status ? status.last : null
    var at = status && status.updated_at ? status.updated_at : 0
    if (last && last.ok === false && at > lastFailureAt) {
      lastFailureAt = at
      shake("confused", 0.6)
    }
  }
  onEyeInkChanged: eyes.requestPaint()

  Canvas {
    id: eyes
    anchors.centerIn: parent
    // Measures follow the bar's height, in the pet's proportions (36 high, 36 wide,
    // corner 8, gap 10 on a 64-row display). Size from the model (0.64 of the inner height).
    readonly property int eye: Model.eyeSize(root.barSize - Style.space(8))
    readonly property int gap: Math.max(3, Math.round(eye * 0.28))
    readonly property int playX: Math.max(2, Math.round(eye * 0.25))
    readonly property int playY: Math.max(1, Math.round(eye * 0.12))
    width: 2 * eye + gap + 2 * playX
    height: eye + 2 * playY + 2

    onPaint: {
      var ctx = getContext("2d")
      ctx.reset()
      var now = root.seconds()
      var asleep = root.eyeMood === "asleep"
      var dx = Math.round(root.gazeX * playX)
      var dy = Math.round(root.gazeY * playY)
      if (now < root.shakeUntil) {
        var swing = Math.round(Math.sin(now * 18 * Math.PI) * Math.max(1, eye * 0.11))
        if (root.shakeKind === "confused") dx += swing
        else dy += swing
      }
      var left = (width - (2 * eye + gap)) / 2 + dx
      for (var side = -1; side <= 1; side += 2) {
        var x = side < 0 ? left : left + eye + gap
        var h = asleep ? 2 : Math.max(2, Math.round(eye * root.openness))
        // Curiosity: the eye on the side it looks to grows a little.
        var toward = root.gazeX * side
        if (!asleep && toward > 0) h += Math.round(eye * 0.17 * Math.min(1, toward))
        var y = (height - h) / 2 + dy
        var radius = Math.min(Math.round(eye * 0.22), h / 2)
        ctx.globalCompositeOperation = "source-over"
        ctx.fillStyle = root.eyeInk
        ctx.beginPath()
        ctx.roundedRect(x, y, eye, h, radius, radius)
        ctx.fill()
        if (asleep || h < 6 || root.eyeMood === "default") continue
        // The lid cuts into the eye — erased, so the bar shows through whatever its color.
        var depth = Math.round(h * 0.45)
        ctx.globalCompositeOperation = "destination-out"
        ctx.fillStyle = "black"
        ctx.beginPath()
        if (root.eyeMood === "happy") {
          // From below, straight: the eye becomes a dome — the smile sits in the eye.
          ctx.rect(x - 1, y + h - depth, eye + 2, depth + 2)
        } else {
          // Tired: the lid hangs on the outside.
          var outer = side < 0 ? x : x + eye
          var inner = side < 0 ? x + eye : x
          ctx.moveTo(outer, y - 1)
          ctx.lineTo(inner, y - 1)
          ctx.lineTo(outer, y + depth)
          ctx.closePath()
        }
        ctx.fill()
      }
    }
  }

  MouseArea {
    anchors.fill: parent
    acceptedButtons: Qt.LeftButton
    onClicked: root.toggle()
  }

  // ---------- Panel ----------
  KeyboardPanel {
    id: panel
    anchorItem: root
    owner: root
    bar: root.bar
    open: root.popupOpen
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(340))
    contentHeight: panel.fittedContentHeight(root.wantedHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: root.talking && root.typing   // in a line of the talk the keys write
      onMoveRequested: function(dx, dy) { root.moveCursor(dx, dy) }
      onActivateRequested: root.activate(root.cursor)
      onCloseRequested: root.talking ? root.backInTalk() : root.close()
      onTabRequested: function(direction) {
        if (root.bar && typeof root.bar.switchPanelFrom === "function")
          root.bar.switchPanelFrom(root, direction)
      }
      // PgUp / PgDn scroll the middle (the arrows belong to the cursor).
      Keys.onPressed: function(event) {
        if (event.key !== Qt.Key_PageUp && event.key !== Qt.Key_PageDown) return
        scroll.page(event.key === Qt.Key_PageDown ? 1 : -1)
        event.accepted = true
      }

      // ---------- Head: stays ----------
      Column {
        id: head
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: root.zoneGap

        // Who it is and how it feels · its energy
        Item {
          width: parent.width
          implicitHeight: Math.max(heroLabels.implicitHeight, heroEnergy.implicitHeight)

          Column {
            id: heroLabels
            anchors.left: parent.left
            anchors.right: heroEnergy.left
            anchors.rightMargin: Style.space(10)
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(2)

            Text {
              textFormat: Text.PlainText
              width: parent.width
              elide: Text.ElideRight
              color: root.ink
              font.family: root.font
              font.pixelSize: Style.font.title
              font.bold: true
              text: root.status.name || "Shikigon"
            }
            Caption {
              visible: root.view.full
              width: parent.width
              elide: Text.ElideRight
              text: (root.status.persona ? root.status.persona + " · " : "") + root.mood
            }
          }

          Text {
            id: heroEnergy
            textFormat: Text.PlainText
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            color: root.ink
            font.family: root.font
            font.pixelSize: Style.font.display
            font.bold: true
            text: root.view.full && root.status.energy !== undefined
              ? Model.grouped(root.status.energy) + " E" : ""
          }
        }

        // What is going on, in one sentence (awake: the server's own word on what is next),
        // and the few lines that explain a state that needs explaining.
        Column {
          visible: root.view.sentence !== "" || root.view.lines.length > 0
          width: parent.width
          spacing: Style.space(6)

          Line { visible: text !== ""; text: root.view.sentence }
          Repeater {
            model: root.view.lines
            Line { required property var modelData; color: root.dim; text: String(modelData) }
          }
        }

        Row {
          id: stats
          visible: root.view.full && root.status.fields !== undefined && !root.inTournament
            && !root.talking
          width: parent.width
          readonly property real cell: width / 3
          Stat { width: stats.cell; label: "fields"; value: String(root.status.fields) }
          Stat {
            width: stats.cell; label: "rank"
            value: root.status.rank ? "#" + root.status.rank : "–"
          }
          // The brain says "autonomous" when no compass is set; the button for it says "free".
          Stat {
            width: stats.cell; label: "compass"
            value: root.compass === "autonomous" ? "free" : (root.compass || "–")
          }
        }

        // The talk with its advisor (the model named in [brain.advisor]): who answers here,
        // the last two exchanges in the middle, the question line in the foot. Without a
        // model the answer says what is missing. The answers are the model's, not the
        // game's: its name stands in front of each.
        Column {
          visible: root.talking
          width: parent.width
          spacing: Style.space(8)

          PanelSeparator { foreground: root.ink }
          PanelSectionHeader {
            text: root.step === "talk" ? "ASK ITS ADVISOR" : "WHO ANSWERS?"
            foreground: root.ink
            fontFamily: root.font
          }

          // --- who: what was found, then the two ways to get one ---
          Column {
            visible: root.step === "who"
            width: parent.width
            spacing: Style.space(6)
            Repeater {
              model: root.step === "who" ? root.whoList : []
              PickButton { required property var modelData; required property int index
                           at: index; text: String(modelData.text) }
            }
            Line { color: root.dim; text: Model.WHO_DATA }
          }

          // --- an address of its own: type it, pick a model; a key only if it asks ---
          // QuietLine: never editable, so the shell never fetches the clipboard (#11665).
          QuietLine {
            id: addressField
            visible: root.step === "address"
            width: parent.width
            foreground: root.ink
            font.family: root.font
            placeholderText: "http://localhost:11434/v1"
            onAccepted: root.submitAddress()
            Keys.onEscapePressed: root.backInTalk()
          }
          QuietLine {
            id: keyField
            visible: root.step === "key"
            width: parent.width
            foreground: root.ink
            font.family: root.font
            password: true
            placeholderText: "it asks for a key"
            onAccepted: root.submitKey()
            Keys.onEscapePressed: root.backInTalk()
          }
          Line {
            visible: root.step === "address" || root.step === "key"
            color: root.failure !== "" ? Commons.Color.urgent : root.dim
            text: root.failure !== "" ? root.failure
              : (root.step === "key" ? "Kept on this machine, for you only. Enter · Esc goes back"
                                     : "Any OpenAI-compatible address. Enter · Esc goes back")
          }

          // --- models: of an agent, of a server on this machine, or of the address ---
          Column {
            visible: root.step === "models"
            width: parent.width
            spacing: Style.space(6)
            Line { color: root.dim
                   text: (root.pickedRow ? (root.pickedRow.name || "") : "") + " — which model?" }
            Repeater {
              model: root.step === "models" ? root.modelList.shown : []
              PickButton { required property var modelData; required property int index
                           at: index; text: String(modelData) }
            }
            Line { visible: root.modelList.more > 0; color: root.dim
                   text: "and " + root.modelList.more + " more: name one in the config file" }
            Line { visible: root.modelList.shown.length === 0; color: root.dim; text: "asking…" }
          }

          // --- one real question before a connection is taken ---
          Line {
            visible: root.step === "trying"
            text: Model.tryingText(root.pickedRow ? root.pickedRow.name : "")
          }
          Column {
            visible: root.step === "failed"
            width: parent.width
            spacing: Style.space(6)
            Line { color: Commons.Color.urgent; text: root.failure }
            Row {
              width: parent.width
              spacing: Style.space(6)
              PickButton { at: 0; width: (parent.width - parent.spacing) / 2; text: "back" }
              PickButton {
                visible: !!(root.pickedRow && root.pickedRow.sign_in)
                at: 1
                width: (parent.width - parent.spacing) / 2
                text: "open " + (root.pickedRow ? (root.pickedRow.name || "it") : "it")
                      + " to sign in"
              }
            }
          }

          // --- the talk: who answers and how often it advises, the exchanges, the line ---
          Column {
            visible: root.step === "talk"
            width: parent.width
            spacing: Style.space(8)
            // Two rows, one under the other: side by side their texts ran into each other
            // (seen on the device, 1440x900).
            PickButton { at: 0; text: "answers: " + (root.advisorNow ? root.advisorNow.label : "") }
            PickButton { at: 1; text: Model.adviseText(root.advisorNow) }
            Line { visible: text !== ""; color: root.dim; text: Model.callsText(root.advisorNow) }
          }
        }
      }

      // ---------- Middle: scrolls when it has to ----------
      // In the talk it holds the exchanges and stays at the newest one.
      Flickable {
        id: scroll
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: head.bottom
        anchors.topMargin: scrolled.implicitHeight > 0 ? root.zoneGap : 0
        anchors.bottom: foot.top
        anchors.bottomMargin: foot.implicitHeight > 0 ? root.zoneGap : 0
        contentWidth: width
        contentHeight: scrolled.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        Controls.ScrollBar.vertical: Controls.ScrollBar { policy: Controls.ScrollBar.AsNeeded }

        function toEnd() { contentY = Math.max(0, contentHeight - height) }
        function page(direction) {
          contentY = Math.max(0, Math.min(contentHeight - height,
                                          contentY + direction * height * 0.8))
        }
        onContentHeightChanged: if (root.talking) toEnd()
        onHeightChanged: if (root.talking) toEnd()

        Column {
          id: scrolled
          width: scroll.width
          spacing: root.zoneGap

          // The tournament: score against the best, its compass, its Marauders. The compass is
          // shown, not changed here — that is the terminal's [T] (founder 10.10.2026, E-T1).
          Column {
            visible: root.view.full && root.inTournament && !root.talking
            width: parent.width
            spacing: Style.space(4)

            PanelSeparator { foreground: root.ink }
            PanelSectionHeader {
              text: root.inTournament ? "TOURNAMENT #" + root.tournament.number
                + " · ENDS " + Model.endsText(root.tournament.ends_at) : ""
              foreground: root.ink
              fontFamily: root.font
            }
            Repeater {
              model: root.inTournament ? Model.tournamentRows(root.tournament) : []
              ScoreRow { required property var modelData; width: parent.width; row: modelData }
            }
            Line {
              text: root.inTournament ? (root.tournament.compass
                ? "compass " + root.tournament.compass : "no compass yet")
                + " · change it in the terminal: [T]" : ""
            }
            Repeater {
              model: root.inTournament ? Model.marauderLines(root.tournament.bodies) : []
              Line { required property var modelData; color: root.dim; text: String(modelData) }
            }
          }

          // Why it does what it does (brain: situation()), and its last move. Both are the
          // main world's; in a tournament the world steps back (and the journal's top line is
          // the last move) — without this the panel ran off the screen (S366, 900 px display).
          Column {
            visible: root.view.full && !root.inTournament && !root.talking
              && (nowLines.count > 0 || !!root.status.last)
            width: parent.width
            spacing: Style.space(4)

            PanelSeparator { foreground: root.ink }
            PanelSectionHeader { text: "NOW"; foreground: root.ink; fontFamily: root.font }
            Repeater {
              id: nowLines
              model: root.view.full && root.status.situation ? root.status.situation : []
              Line { required property var modelData; text: String(modelData) }
            }
            Line {
              visible: !!root.status.last
              color: root.status.last && root.status.last.ok === false
                ? Commons.Color.urgent : root.dim
              text: root.status.last ? "last move: " + Model.moveWords(root.status.last)
                + (root.status.last.ok ? "" : " ✗ " + (root.status.last.why || "")) : ""
            }
            // Why: what its advisor said on that move (only when a model advises it).
            Line {
              visible: text !== ""
              color: root.dim
              text: Model.adviceLine(root.status.last)
            }
          }

          // What it learned last, always in view once there is a lesson (founder 14.10.: in the
          // panel, not in the bar; panel I): what it does more, what less, and what that rests on.
          Column {
            readonly property var lesson: Model.lessonView(root.lessons, root.seconds())
            visible: root.view.full && !root.talking && lesson !== null
            width: parent.width
            spacing: Style.space(4)

            PanelSeparator { foreground: root.ink }
            // What it rests on rides in the header: one line less, and it stays in view.
            PanelSectionHeader {
              text: "LEARNED" + (parent.lesson ? " · " + parent.lesson.basis : "")
              foreground: root.ink; fontFamily: root.font
            }
            // Two lines each: in a tournament the panel is full, and the whole block has to stay
            // in view (device 14.10.: a 200-character part took five lines and hid "less").
            Line { maximumLineCount: 2; elide: Text.ElideRight
                   text: parent.lesson ? "more: " + parent.lesson.more : "" }
            Line { maximumLineCount: 2; elide: Text.ElideRight
                   text: parent.lesson ? "less: " + parent.lesson.less : "" }
          }

          Column {
            visible: root.journal.length > 0 && !root.talking
            width: parent.width
            spacing: Style.space(4)

            PanelSeparator { foreground: root.ink }
            PanelSectionHeader { text: "JOURNAL"; foreground: root.ink; fontFamily: root.font }
            Repeater {
              model: Model.journalLines(root.journal)
              Line {
                required property var modelData
                wrapMode: Text.NoWrap
                elide: Text.ElideRight
                color: modelData.ok === false ? Commons.Color.urgent : root.ink
                text: modelData.text + (modelData.count > 1 ? "  ×" + modelData.count : "")
              }
            }
          }

          // The talk: the last exchanges, oldest first.
          Column {
            visible: root.talking && root.step === "talk" && root.talk.length > 0
            width: parent.width
            spacing: Style.space(8)
            Repeater {
              model: root.talk
              Line {
                required property var modelData
                required property int index
                // The newest exchange in full (seen on the device: eight lines cut a
                // three-sentence answer), the one before it in short.
                maximumLineCount: index >= root.talk.length - 2 ? 11 : 3
                elide: Text.ElideRight
                color: modelData.ok === false ? Commons.Color.urgent
                  : (modelData.who === "you" ? root.dim : root.ink)
                text: modelData.who + ": " + modelData.text
              }
            }
          }
        }
      }

      // ---------- Foot: stays, so the buttons and the question line are always there ----------
      Column {
        id: foot
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        spacing: root.zoneGap

        Column {
          visible: root.view.full && !root.talking
          width: parent.width
          spacing: Style.space(8)

          PanelSeparator { foreground: root.ink }
          // In a tournament these buttons set the world's compass, not the tournament's: say so.
          PanelSectionHeader {
            text: root.inTournament ? "COMPASS (WORLD)" : "COMPASS"
            foreground: root.ink
            fontFamily: root.font
          }
          Line { color: root.dim; text: "Tell it what matters. It stays itself." }
          Grid {
            id: presetGrid
            width: parent.width
            columns: root.presetColumns
            spacing: Style.space(6)
            readonly property real cell: (width - spacing * (columns - 1)) / columns
            Repeater {
              model: root.presets
              PanelButton {
                required property var modelData
                required property int index
                width: presetGrid.cell
                slot: index
                text: String(modelData)
                active: root.isCurrentPreset(String(modelData))
              }
            }
          }
        }

        Row {
          id: actionRow
          visible: root.actions.length > 0 && !root.talking
          width: parent.width
          spacing: Style.space(6)
          readonly property int count: Math.max(1, root.actions.length)
          readonly property real cell: (width - spacing * (count - 1)) / count
          Repeater {
            model: root.actions
            PanelButton {
              required property var modelData
              required property int index
              width: actionRow.cell
              slot: root.presetCount + index
              text: String(modelData)
            }
          }
        }

        Column {
          visible: root.talking && root.step === "talk"
          width: parent.width
          spacing: Style.space(8)
          QuietLine {
            id: askField
            width: parent.width
            foreground: root.ink
            font.family: root.font
            placeholderText: "what should I do now?"
            onAccepted: root.submitQuestion()
            Keys.onEscapePressed: root.stopTalking()
            Keys.onUpPressed: { root.pick = 1; keyCatcher.forceActiveFocus() }
          }
          Line { color: root.dim; text: "Enter sends · Up: who answers · Esc back" }
        }
      }
    }
  }

  // An input line that is never editable (marketplace review #11665). Qt fetches the
  // clipboard's text into an editable line on every clipboard change and when it turns
  // editable, with no limit and no deadline, inside the shared shell. So the line stays
  // read-only and takes its keys here; a paste goes through `paster`, outside the shell.
  // Input methods (compose, dead keys) do not reach a read-only line; keys that type a
  // character themselves (ä, ß) do.
  component QuietLine: TextField {
    id: line
    readOnly: true
    cursorVisible: activeFocus

    // The line as Model.lineKey reads it, and back: what a key or a paste left.
    function snapshot() {
      const c = cursorPosition
      return { text: text, cursor: c, anchor: c === selectionStart ? selectionEnd : selectionStart }
    }
    function apply(next) {
      text = next.text
      if (next.anchor === next.cursor) { deselect(); cursorPosition = next.cursor }
      else select(next.anchor, next.cursor)
    }
    function put(s) { apply(Model.lineInsert(snapshot(), s)) }

    readonly property var keyNames: ({
      [Qt.Key_Return]: "enter", [Qt.Key_Enter]: "enter", [Qt.Key_Backspace]: "backspace",
      [Qt.Key_Delete]: "delete", [Qt.Key_Left]: "left", [Qt.Key_Right]: "right",
      [Qt.Key_Home]: "home", [Qt.Key_End]: "end", [Qt.Key_Insert]: "insert",
      [Qt.Key_V]: "v", [Qt.Key_A]: "a"
    })
    Keys.onPressed: function (event) {
      const next = Model.lineKey(line.snapshot(), line.keyNames[event.key] || "", event.text, {
        ctrl: (event.modifiers & Qt.ControlModifier) !== 0,
        shift: (event.modifiers & Qt.ShiftModifier) !== 0,
        other: (event.modifiers & (Qt.AltModifier | Qt.MetaModifier)) !== 0
      })
      event.accepted = next !== null
      if (!next) return
      if (next.action === "accept") line.accepted()
      else if (next.action === "paste") root.pasteInto(line)
      else line.apply(next)
    }
  }

  // Ctrl+V or Shift+Insert in a QuietLine: the clipboard is read by its own process, only on
  // that key, for 2 s at most and 4 KiB at most; the first line, without control characters.
  property var pasteTarget: null
  function pasteInto(line) {
    if (paster.running) return
    pasteTarget = line
    paster.running = true
  }
  Process {
    id: paster
    command: ["sh", "-c", "timeout 2 wl-paste -n -t text 2>/dev/null | head -c 4096"]
    stdout: StdioCollector {
      onStreamFinished: {
        const s = Model.pastedLine(text)
        if (root.pasteTarget && s !== "") root.pasteTarget.put(s)
        root.pasteTarget = null
      }
    }
  }

  // Small, dimmed, upper-case label — the shell's caption style.
  component Caption: Text {
    textFormat: Text.PlainText
    color: root.dim
    font.family: root.font
    font.pixelSize: Style.font.caption
    font.bold: true
    font.letterSpacing: 1.2
    font.capitalization: Font.AllUppercase
  }

  component Line: Text {
    textFormat: Text.PlainText
    width: parent ? parent.width : implicitWidth
    wrapMode: Text.Wrap
    color: root.ink
    font.family: root.font
    font.pixelSize: Style.font.body
  }

  component Stat: Column {
    property string label: ""
    property string value: ""
    spacing: Style.space(2)
    Caption { text: parent.label }
    Text {
      textFormat: Text.PlainText
      color: root.ink
      font.family: root.font
      font.pixelSize: Style.font.subtitle
      font.bold: true
      text: parent.value
    }
  }

  // One tournament score: label, a short bar of you against the best, the numbers — like
  // the meters of the shell's own agents panel, in the bar's ink, no colour of its own.
  component ScoreRow: Item {
    id: scoreRow
    property var row: ({ label: "", ratio: 0, text: "" })
    implicitHeight: scoreText.implicitHeight

    Caption {
      id: scoreLabel
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: parent.width * 0.34
      text: scoreRow.row.label
    }
    Rectangle {
      id: track
      anchors.left: scoreLabel.right
      anchors.right: scoreText.left
      anchors.rightMargin: Style.space(10)
      anchors.verticalCenter: parent.verticalCenter
      height: Style.space(4)
      color: Qt.rgba(root.ink.r, root.ink.g, root.ink.b, 0.18)
      Rectangle {
        width: track.width * Math.max(0, Math.min(1, Number(scoreRow.row.ratio) || 0))
        height: track.height
        color: root.ink
      }
    }
    Text {
      id: scoreText
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(84)  // fixed, so the three bars have one length and compare
      horizontalAlignment: Text.AlignRight
      textFormat: Text.PlainText
      color: root.ink
      font.family: root.font
      font.pixelSize: Style.font.body
      text: scoreRow.row.text
    }
  }

  // The shell's button, wired to this panel's cursor (keys and mouse share it).
  component PanelButton: Button {
    property int slot: -1
    fontSize: Style.font.bodySmall
    foreground: root.ink
    fontFamily: root.font
    bordered: true
    hasCursor: root.cursor === slot
    onClicked: root.activate(slot)
    onHovered: function(isHovered) { if (isHovered) root.cursor = slot }
  }

  // A row of a list in the talk view: the same button, on the talk's own cursor (`pick`).
  component PickButton: Button {
    property int at: 0
    width: parent ? parent.width : implicitWidth
    fontSize: Style.font.bodySmall
    foreground: root.ink
    fontFamily: root.font
    bordered: true
    hasCursor: root.talking && !root.typing && root.pick === at
    onClicked: { root.pick = at; root.activateInTalk() }
    onHovered: function(isHovered) { if (isHovered) root.pick = at }
  }
}
