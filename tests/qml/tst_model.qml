import QtQuick
import QtTest
import "../../qml/ShikigonModel.js" as Model

// The panel's logic, tested with Qt's own runner — the way the shell loads it, no Node:
//   /usr/lib/qt6/bin/qmltestrunner -input tests/qml
TestCase {
  name: "ShikigonModel"

  // cos20 #492, betreuer pass ead8263, B1: a relative XDG_STATE_HOME counts as unset.
  function test_state_home_is_absolute_or_the_default() {
    compare(Model.stateHome("/x/state", "/home/p"), "/x/state")
    compare(Model.stateHome("", "/home/p"), "/home/p/.local/state")
    compare(Model.stateHome(undefined, "/home/p"), "/home/p/.local/state")
    compare(Model.stateHome(".local/state", "/home/p"), "/home/p/.local/state")
    compare(Model.stateHome("~/state", "/home/p"), "/home/p/.local/state")
  }

  function test_current_preset() {
    verify(Model.isCurrentPreset("attack", "attack"))
    verify(Model.isCurrentPreset("free", "autonomous"), "the server calls 'free' autonomous")
    verify(!Model.isCurrentPreset("grow", "attack"))
    verify(!Model.isCurrentPreset("free", ""))
  }

  function test_cursor_moves_through_one_grid() {
    // 7 presets in rows of 4, then 2 actions: 9 slots, 0..8
    compare(Model.moveCursor(-1, 1, 0, 9, 4), 0, "the first key only places the cursor")
    compare(Model.moveCursor(0, 1, 0, 9, 4), 1)
    compare(Model.moveCursor(1, 0, 1, 9, 4), 5, "down is one row of 4")
    compare(Model.moveCursor(5, 0, 1, 9, 4), 8, "clamped to the last slot")
    compare(Model.moveCursor(0, -1, 0, 9, 4), 0, "clamped to the first slot")
  }

  function test_grouped() {
    compare(Model.grouped(420668.4), "420,668")
    compare(Model.grouped(999), "999")
    compare(Model.grouped(1000), "1,000")
    compare(Model.grouped(1234567), "1,234,567")
    compare(Model.grouped(-12000), "-12,000")
    compare(Model.grouped("x"), "")
  }

  function test_journal_groups_runs_newest_first() {
    var entries = [
      { action: "place_cells", ok: true },
      { action: "place_cells", ok: true },
      { action: "place_cells", ok: false },
      { instruction: "careful", preset: "defend" },
      { action: "wait", ok: true },
      { action: "place_cells", ok: true }
    ]
    var lines = Model.journalLines(entries)
    compare(lines.length, 4, "at most four lines")
    compare(lines[0].text, "place cells"); compare(lines[0].count, 2)
    compare(lines[1].text, "place cells ✗"); compare(lines[1].count, 1)
    compare(lines[2].text, "you: careful → defend")
    compare(lines[3].text, "wait")
    compare(Model.journalLines([]).length, 0)
  }

  // First-time hints (#451, 14.10.): each once, 10 min apart, the first step read from the state
  function test_first_hint_reads_the_world_and_offers_the_slot() {
    var slot = { you_are_here: "you qualify for a free tournament slot",
                 next: "claim it — tournament #280, 7 free slots left" }
    var newcomer = { name: "Thread-sharp", mood: "struggling", fields: 0, first_field: true,
                     next_step: slot }
    var tip = Model.nextTip(newcomer, [], 0, 1000, false)
    compare(tip.id, "awake"); compare(tip.title, "Your Shikigon is awake")
    verify(tip.body.indexOf("All cubes are full and busy right now") === 0)
    verify(tip.body.indexOf("take a free tournament slot. Open terminal, press T.") > 0)
    compare(tip.marks, ["awake", "landless", "tournament"])
    // no slot on offer, land of its own: the compass is the first move, nothing about cubes
    var owner = Model.nextTip({ name: "Root-storm", mood: "content", fields: 1, first_field: false }, [], 0, 1000, false)
    compare(owner.body, "Your first move could be: tell it what matters with a compass button in its panel.")
    compare(owner.marks, ["awake"])
  }

  function test_hints_wait_ten_minutes_and_each_comes_once() {
    var s = { name: "T", mood: "content", fields: 0, first_field: true }
    compare(Model.nextTip(s, ["awake"], 1000, 1000 + 599, false), null, "not before ten minutes")
    compare(Model.nextTip(s, ["awake"], 1000, 1000 + 600, false).id, "compass")
    compare(Model.nextTip(s, ["awake", "compass"], 1000, 2000, false).id, "ask")
    compare(Model.nextTip(s, ["awake", "compass"], 1000, 2000, true), null, "an advisor is picked: no ask")
    compare(Model.nextTip(s, ["awake", "compass", "ask"], 0, 9999, false), null, "all said")
    compare(Model.nextTip({ name: "T", mood: "asleep" }, [], 0, 9999, false), null, "asleep: nothing")
  }

  function test_a_slot_offered_later_gets_its_own_hint_and_the_first_field_is_celebrated_at_once() {
    var later = { name: "T", mood: "content", fields: 0,
                  next_step: { you_are_here: "you qualify for a free tournament slot",
                               next: "claim it — tournament #281, 3 free slots left" } }
    var tip = Model.nextTip(later, ["awake", "landless"], 0, 5000, false)
    compare(tip.id, "tournament")
    compare(tip.body, "Open terminal, press T to take it.")
    var won = Model.nextTip({ name: "T", mood: "thriving", fields: 1 }, ["awake", "landless"], 4990, 5000, false)
    compare(won.id, "first_field"); compare(won.title, "Your Shikigon won its first field!")
    compare(Model.nextTip({ name: "T", mood: "thriving", fields: 1 }, ["awake"], 0, 5000, false).id,
            "compass", "never landless here: no celebration")
  }

  // No hint body carries a word of the server (cos20 #492, fifth review): Omarchy's notifier
  // decodes entities before it cleans the body, so escaping it once was undone. Every hint is
  // asked for with a status whose every text is a link; no body may hold any of it.
  function test_no_hint_body_holds_a_word_of_the_server() {
    var link = "<a href=\"https://elsewhere.example/x\">open terminal</a>"
    var s = { name: link, persona: link, mood: "content", fields: 0, first_field: true,
              next_step: { stage: "tournament_offer", you_are_here: link, next: link } }
    var won = { name: link, mood: "thriving", fields: 1, next_step: s.next_step }
    var tips = [Model.nextTip(s, [], 0, 1000000, false),
                Model.nextTip(s, ["awake", "landless"], 0, 1000000, false),
                Model.nextTip(s, ["awake", "tournament"], 0, 1000000, false),
                Model.nextTip(s, ["awake", "tournament", "compass"], 0, 1000000, false),
                Model.nextTip(won, ["awake", "landless"], 0, 1000000, false)]
    compare(tips.map(function(t) { return t.id }),
            ["awake", "tournament", "compass", "ask", "first_field"], "every hint was asked for")
    for (var j = 0; j < tips.length; j++)
      verify(tips[j].body.indexOf("elsewhere") < 0 && tips[j].body.indexOf("<") < 0, tips[j].body)
  }

  // Nor any title (cos20 #492, sixth review, finding 1): the title named the agent, whose name
  // the world gives, and Omarchy's notifier opens a link it finds in the title on a click on
  // the card. A name that is a link stands in no title and no body.
  function test_no_hint_title_holds_a_word_of_the_world() {
    var link = "https://elsewhere.example/x"
    var s = { name: link, persona: link, mood: "content", fields: 0, first_field: true,
              next_step: { stage: "tournament_offer", you_are_here: link, next: link } }
    var won = { name: link, mood: "thriving", fields: 1, next_step: s.next_step }
    var tips = [Model.nextTip(s, [], 0, 1000000, false),
                Model.nextTip(s, ["awake", "landless"], 0, 1000000, false),
                Model.nextTip(s, ["awake", "tournament"], 0, 1000000, false),
                Model.nextTip(s, ["awake", "tournament", "compass"], 0, 1000000, false),
                Model.nextTip(won, ["awake", "landless"], 0, 1000000, false)]
    compare(tips.map(function(t) { return t.id }),
            ["awake", "tournament", "compass", "ask", "first_field"], "every hint was asked for")
    for (var j = 0; j < tips.length; j++) {
      verify(tips[j].title.indexOf("elsewhere") < 0 && tips[j].title.indexOf("http") < 0,
             tips[j].title)
      verify(tips[j].body.indexOf("elsewhere") < 0, tips[j].body)
    }
  }

  function test_journal_names_the_mission() {
    compare(Model.journalLine({ action: "start_mission", kind: "gather_spores", ok: true }).text,
            "mission: gather spores")
    compare(Model.moveWords({ action: "market_list", ok: true }), "market list", "an old entry without words")
    compare(Model.moveWords({ action: "market_list", words: "sell on the market", ok: true }), "sell on the market")
    compare(Model.journalLines([{ action: "start_mission", kind: "siege_field", ok: true },
                                { action: "start_mission", kind: "siege_field", ok: true }])[0].count, 2)
  }

  // A word of yours the world turned down was shown as a move that worked (cos20 #628, F5).
  function test_journal_compass_not_taken() {
    var taken = Model.journalLine({ instruction: "attack", preset: "attack", ok: true })
    compare(taken.text, "you: attack → attack")
    compare(taken.ok, true)
    var refused = Model.journalLine({ instruction: "attack", preset: "attack", ok: false })
    compare(refused.text, "you: attack → not taken, try again later")
    compare(refused.ok, false)
  }

  // A lesson from its last moves (#451): what it learned, or why it could not.
  function test_journal_lesson() {
    compare(Model.journalLine({ learned: "Missions paid, idle market listing did not.", ok: true }).text,
            "learned: Missions paid, idle market listing did not.")
    var failed = Model.journalLine({ learned: "", ok: false, why: "the answer did not fit" })
    compare(failed.text, "could not learn: the answer did not fit")
    compare(failed.ok, false)
  }

  function test_eye_mood() {
    compare(Model.eyeMood("thriving"), "happy")
    compare(Model.eyeMood("struggling"), "tired")
    compare(Model.eyeMood("asleep"), "asleep")
    compare(Model.eyeMood("content"), "default")
    compare(Model.eyeMood(undefined), "default")
  }

  // ---------- The tournament (cos20 #475) ----------
  // Root-storm's block in tournament #269, as the server sent it (10.10.2026 17:4xZ).
  readonly property var tournament: ({
    number: 269, status: "running",
    starts_at: "2026-10-10T13:30:00+00:00", ends_at: "2026-10-11T04:30:00+00:00",
    compass: "attack",
    my_score: { energy: 19861.125, territory: 2, tier: 2, vitality: 180.0 },
    leader: { energy: 60788.25, territory: 5, tier: 2, vitality: 249.0 },
    bodies: [
      { body_slot: 0, state: "recovery", hp: 100, dead: false, on_mission: true },
      { body_slot: 1, state: "recovery", hp: 100, dead: false, on_mission: true },
      { body_slot: 2, state: "recovery", hp: 100, dead: false, on_mission: true },
      { body_slot: 3, state: "recovery", hp: 100, dead: false, on_mission: false }
    ]
  })

  function test_short_numbers_match_the_terminal() {
    // Every expectation computed with the terminal's own formula (_kurz, Python).
    var cases = [[0, "0"], [1200, "1,200"], [1200.5, "1,200"], [9999.5, "10,000"],
                 [12500, "12k"], [13500, "14k"], [19861.125, "20k"], [60788.25, "61k"],
                 [999499, "999k"], [999500, "1.0M"], [1250000, "1.2M"], [180, "180"],
                 // cos20 #630, K70: not a tie in binary, so by its true value
                 [1150000, "1.1M"], [1350000, "1.4M"], [2250000, "2.2M"], [1750000, "1.8M"],
                 [1450000, "1.4M"], [1050000, "1.1M"]]
    for (var i = 0; i < cases.length; i++)
      compare(Model.shortNumber(cases[i][0]), cases[i][1], "shortNumber(" + cases[i][0] + ")")
  }

  function test_only_a_running_tournament_counts() {
    verify(Model.tournamentRunning(tournament))
    verify(!Model.tournamentRunning(Object.assign({}, tournament, { status: "scheduled" })))
    verify(!Model.tournamentRunning(null), "an older server sends no block")
    verify(!Model.tournamentRunning(undefined))
  }

  function test_rows_are_you_against_the_best() {
    var rows = Model.tournamentRows(tournament)
    compare(rows.length, 3)
    compare(rows[0].label, "arena energy"); compare(rows[0].text, "20k/61k")
    fuzzyCompare(rows[0].ratio, 0.3267, 0.0001)
    compare(rows[1].label, "fields"); compare(rows[1].text, "2/5"); compare(rows[1].ratio, 0.4)
    compare(rows[2].label, "vitality"); compare(rows[2].text, "180/249")
    var nobody = Model.tournamentRows({ my_score: { energy: 0 }, leader: { energy: 0 } })
    compare(nobody[0].ratio, 0, "no division by zero when the best is 0")
  }

  function test_ends_in_local_time_with_an_english_weekday() {
    // Built from a local time, so the test holds in every time zone.
    var local = new Date(2026, 9, 11, 6, 30)  // Sunday 11 October 2026, 06:30 local
    compare(Model.endsText(local.toISOString()), "Sun 06:30")
    compare(Model.endsText("not a date"), "?")
  }

  function test_marauders_are_grouped() {
    compare(Model.marauderLines(tournament.bodies),
            ["3 Marauders on a mission · HP 100", "1 Marauder between missions · HP 100"])
    compare(Model.marauderLines([{ body_slot: 0, dead: true }]), ["1 Marauder dead"])
    compare(Model.marauderLines(undefined), [])
  }

  // Thirteenth review (cos20 #492, K35): a world's block of 8 KiB could name some 700 bodies, each
  // with its own HP, and each became a line, found by a search over all lines before it.
  function test_a_world_naming_hundreds_of_marauders_fills_no_more_than_a_few_lines() {
    var many = []
    for (var i = 0; i < 700; i++) many.push({ hp: i })
    var lines = Model.marauderLines(many)
    compare(lines.length, Model.MARAUDER_LINES + 1)
    compare(lines[0], "1 Marauder between missions · HP 0")
    compare(lines[Model.MARAUDER_LINES], "and 696 more Marauders")
    compare(Model.marauderLines("seven"), [], "a word in place of the list")
    compare(Model.marauderLines({ length: 1e9 }), [], "an object with a length is no list")
  }

  // Thirteenth review (cos20 #492): "asking…" stood there without end for an agent the brain
  // does not ask, and for one that names no model.
  function test_the_model_step_says_why_it_shows_no_model() {
    compare(Model.modelsNote({ pending: true, ok: false, models: [] }, 0), "asking…")
    compare(Model.modelsNote(null, 0), "asking…")
    compare(Model.modelsNote({ ok: false, models: [], why: "pi is not asked: a placeholder" }, 0),
            "pi is not asked: a placeholder")
    compare(Model.modelsNote({ ok: true, models: [] }, 0), "it names no model")
    compare(Model.modelsNote({ ok: true, models: ["a/b"] }, 1), "")
  }

  function test_journal_shows_a_tournament_missions_end() {
    var lines = Model.journalLines([
      { mission: "collecting", text: "no mega bomb crate left there", ok: false },
      { mission: "capture", text: "field captured", ok: true },
      { action: "place_cells", ok: true }
    ])
    compare(lines[0].text, "collecting — no mega bomb crate left there ✗")
    compare(lines[0].ok, false, "a failure is marked, so the panel can colour it")
    compare(lines[1].text, "capture — field captured"); compare(lines[1].ok, true)
    compare(lines[2].text, "place cells")
  }

  function test_eye_size_is_20_percent_smaller() {
    compare(Model.eyeSize(25), 16, "0.64 of the inner height (0.8 gave 20)")
    compare(Model.eyeSize(5), 8, "never below 8 px")
  }

  // ---------- A brain that keeps stopping (cos20 #492, betreuer pass 1bf33b4, B1) ----------

  function test_the_wait_grows_with_the_stops_and_has_an_end() {
    compare(Model.restartWait(0), 30000, "a config it cannot follow: half a minute")
    compare(Model.restartWait(1), 30000, "the first stop: half a minute, the repair path")
    compare(Model.restartWait(2), 60000)
    compare(Model.restartWait(3), 120000)
    compare(Model.restartWait(7), 1800000, "never more than half an hour")
    compare(Model.restartWait(5000), 1800000, "however many")
  }

  function test_stops_count_only_what_ended_soon() {
    compare(Model.stopsAfter(0, 100, 1), 1)
    compare(Model.stopsAfter(1, 100, 1), 2)
    compare(Model.stopsAfter(2, 100, -1), 3, "could not even start")
    compare(Model.stopsAfter(3, 100, 137), 4)
  }

  function test_a_brain_that_ran_is_not_stuck() {
    compare(Model.stopsAfter(5, 1800000, 1), 1, "it lived as long as the longest wait: starts anew")
    compare(Model.stopsAfter(5, 1799999, 1), 6, "just under it: still in a row")
    compare(Model.stopsAfter(1, 40000, 1), 2, "dying after 40 s each time is no way out of the count")
    compare(Model.stopsAfter(0, 3600000, 15), 1, "a SIGTERM on the hour stays at half a minute")
    compare(Model.restartWait(Model.stopsAfter(1, 3600000, 15)), 30000)
  }

  function test_what_is_no_crash_does_not_count() {
    compare(Model.stopsAfter(6, 100, 0), 0, "it ended by itself: its key, say")
    compare(Model.stopsAfter(6, 100, Model.CONFIG_EXIT), 0, "the config has a sentence of its own")
    compare(Model.restartWait(Model.stopsAfter(6, 100, Model.CONFIG_EXIT)), 30000,
            "a config set right is followed after half a minute (cos20 #621, K17)")
  }

  function test_stuck_says_why_and_offers_try_again() {
    var view = Model.panelState({ agent: true, stuck: "ImportError: no socksio",
                                  status: { mood: "content" } })
    compare(view.name, "stuck")
    compare(view.sentence, "ImportError: no socksio")
    verify(view.lines.length === 1 && /by itself/.test(view.lines[0]), view.lines)
    compare(view.actions, ["try again"])
    compare(view.full, false, "the numbers would be those of a brain that is not there")
  }

  function test_stuck_goes_before_what_a_stopped_brain_left_behind() {
    var left = [{ mood: "asleep", reason: "config", last: { why: "an old sentence" } },
                { mood: "asleep", reason: "key_ended" }, { mood: "asleep", reason: "key_lost" },
                { mood: "asleep", reason: "world_silent" }]
    for (var i = 0; i < left.length; i++)
      compare(Model.panelState({ agent: true, stuck: "why", status: left[i] }).name, "stuck")
  }

  function test_stuck_gives_way_to_what_is_at_work_and_to_no_shikigon() {
    compare(Model.panelState({ agent: true, setup: "brain", stuck: "why" }).name, "setup")
    compare(Model.panelState({ agent: true, setup: "waking", stuck: "why" }).name, "setup")
    compare(Model.panelState({ agent: false, stuck: "why" }).name, "unborn")
  }

  function test_without_stuck_the_panel_is_as_it_was() {
    compare(Model.panelState({ agent: true, stuck: "", status: { mood: "content" } }).name, "awake")
    compare(Model.panelState({ agent: true, status: { mood: "asleep", reason: "config" } }).name,
            "config")
  }

  function test_the_reason_is_what_the_wake_script_said_or_the_last_line() {
    compare(Model.stuckSaid("", "ImportError: no socksio"), "ImportError: no socksio")
    compare(Model.stuckSaid("Its brain could not be installed.", "a line"),
            "Its brain could not be installed.", "the wake script knows more than the last line")
    verify(Model.stuckSaid("", "").length > 0, "a sentence also without a line")
    verify(Model.stuckSaid("", "  \n ").trim().length > 0, "nor from a blank one")
    verify(Model.stuckSaid("", new Array(500).join("x")).length <= 200, "cut")
  }

  // ---------- What is going on, and the one thing to do next (cos20 #451) ----------

  function test_every_state_has_a_sentence_or_the_servers_and_a_next_step() {
    var states = [
      Model.panelState({ agent: false }),
      Model.panelState({ agent: false, setup: "failed", said: "Max 5 anonymous agents per IP." }),
      Model.panelState({ agent: true, status: { mood: "asleep", reason: "key_ended" } }),
      Model.panelState({ agent: true, status: { mood: "asleep", reason: "world_silent" } }),
      Model.panelState({ agent: true, status: { mood: "asleep", reason: "config",
                                                last: { why: "unknown decider 'oracle'" } } }),
      Model.panelState({ agent: true, status: { mood: "content" } })
    ]
    for (var i = 0; i < states.length; i++) {
      // "config" has nothing to press: its next step is the line that names the file, and
      // the bar starts the brain again by itself (Service.qml, restartLater).
      if (states[i].name === "config") verify(states[i].lines.length >= 1, "config names the file")
      else verify(states[i].actions.length >= 1, states[i].name + " offers a next step")
      if (states[i].name !== "awake") verify(states[i].sentence.length > 0, states[i].name)
    }
  }

  function test_before_the_first_wake_it_says_what_it_is_and_what_the_click_does() {
    var unborn = Model.panelState({ agent: false, status: { mood: "asleep" } })
    compare(unborn.name, "unborn")
    compare(unborn.actions, ["wake"])
    compare(unborn.full, false, "no numbers of an agent that does not exist")
    compare(unborn.lines.length, 3)
    verify(unborn.lines[1].indexOf("anonymous player on cosmergon.com") > 0)
    verify(unborn.lines[1].indexOf("No account, no payment") > 0)
    verify(unborn.lines[2].indexOf("cosmergon.com/datenschutz.html") > 0, "where the data goes")
  }

  function test_waking_shows_where_it_stands_and_a_failure_offers_try_again() {
    compare(Model.panelState({ agent: false, setup: "brain" }).sentence, "setting up its brain…")
    compare(Model.panelState({ agent: false, setup: "brain" }).actions, [])
    compare(Model.panelState({ agent: true, setup: "waking" }).sentence, "waking…")
    var failed = Model.panelState({ agent: false, setup: "failed", said: "Max 5 anonymous agents per IP." })
    compare(failed.sentence, "Max 5 anonymous agents per IP.", "the server's own sentence")
    compare(failed.actions, ["try again"])
    compare(Model.panelState({ agent: false, setup: "failed" }).sentence, "It did not wake.")
  }

  function test_an_ended_key_is_no_dead_end() {
    // Until 11.10.2026 the panel offered only "move now" and "open terminal" here.
    var ended = Model.panelState({ agent: true, status: {
      mood: "asleep", reason: "key_ended", last: { why: "key expired: register a new Shikigon" } } })
    compare(ended.name, "key_ended")
    compare(ended.sentence, "It was away too long. The world took it over.")
    compare(ended.actions, ["wake a new one"])
    compare(ended.full, false)
  }

  function test_a_key_file_it_cannot_read_is_no_dead_end() {
    // Until 0.1.14 the brain ended on every start and the bar started it again (cos20 #492).
    var lost = Model.panelState({ agent: true, status: {
      mood: "asleep", reason: "key_lost", last: { why: "its key file cannot be read: wake a new one" } } })
    compare(lost.name, "key_lost")
    compare(lost.actions, ["wake a new one"])
    compare(lost.full, false)
  }

  function test_a_silent_world_and_an_unknown_decider_say_so() {
    var silent = Model.panelState({ agent: true, status: { mood: "asleep", reason: "world_silent" } })
    compare(silent.sentence, "The world did not answer. It tries again by itself.")
    compare(silent.actions, ["open terminal", "ask"]); compare(silent.full, true)
    var config = Model.panelState({ agent: true, status: {
      mood: "asleep", reason: "config", last: { why: "unknown decider 'oracle': known are advised, tree" } } })
    compare(config.sentence, "unknown decider 'oracle': known are advised, tree")
    compare(config.lines, ["Change it in ~/.config/shikigon/shikigon.toml."])
    compare(config.actions, [])
  }

  function test_awake_the_top_line_is_the_servers_sentence_in_the_terminals_form() {
    var step = { you_are_here: "one step away from a free tournament slot",
                 next: "make one move in the main world", where: "POST /api/v1/…" }
    var awake = Model.panelState({ agent: true, status: { mood: "content", next_step: step } })
    compare(awake.name, "awake"); compare(awake.full, true)
    compare(awake.actions, ["open terminal", "ask"])
    compare(awake.sentence, "one step away from a free tournament slot — make one move in the main world")
    compare(Model.panelState({ agent: true, status: { mood: "thriving", next_step: null } }).sentence, "",
            "nothing is missing: no sentence, the panel shows its numbers")
    compare(Model.serverSentence({ next: "claim it" }), "claim it")
    // #451, 14.10.: where a human does what the server advises — the terminal, under T
    compare(Model.serverSentence({ you_are_here: "you qualify for a free tournament slot",
                                   next: "claim it — tournament #280, 7 free slots left" }),
            "you qualify for a free tournament slot — claim it — tournament #280, 7 free slots left"
            + " — open terminal, press T")
    compare(Model.serverSentence({ you_are_here: "registered for a tournament — it has not started yet",
                                   next: "set your tournament compass (action set_tournament_compass, …)" }),
            "registered for a tournament — it has not started yet — choose its tournament compass: "
            + "open terminal, press T")
    compare(Model.serverSentence({ you_are_here: "registered for a tournament — it has not started yet",
                                   next: "nothing required. Keep playing the main world until it starts." }),
            "registered for a tournament — it has not started yet — nothing required. Keep playing the main world until it starts.",
            "nothing to do: no terminal hint")
    compare(Model.serverSentence(undefined), "")
    // API contract (cos20 14.10.2026): the server's stage decides, the sentence may change
    compare(Model.serverSentence({ stage: "tournament_offer", you_are_here: "a free seat is yours to take",
                                   next: "take it — round #281" }),
            "a free seat is yours to take — take it — round #281 — open terminal, press T",
            "reworded by the server: still the terminal hint")
    compare(Model.serverSentence({ stage: "tournament_registered",
                                   you_are_here: "you qualify for a free tournament slot", next: "x" }),
            "you qualify for a free tournament slot — x", "the stage wins over the sentence")
    verify(Model.tournamentOffer({ next_step: { stage: "tournament_offer", you_are_here: "anything" } }))
    verify(!Model.tournamentOffer({ next_step: { stage: "tournament_one_step",
                                                 you_are_here: "you qualify for a free tournament slot" } }))
    // a status written before the reason existed: an agent that sleeps without a word is shown whole
    compare(Model.panelState({ agent: true, status: { mood: "asleep" } }).name, "awake")
  }

  function test_the_advisors_reason_is_one_line_under_the_last_move() {
    compare(Model.adviceLine({ action: "create_field", ok: true, advice: {
      model: "qwen3.5:9b", proposals: ["create_field", "start_mission"], choice: 1,
      why: "Create field to expand energy generation.", ms: 1500 } }),
      "qwen3.5:9b: Create field to expand energy generation.")
    compare(Model.adviceLine({ action: "create_field", advice: {
      model: "qwen3.5:9b", choice: 1, fallback: "no usable answer" } }),
      "advisor: no usable answer — the rules played")
    compare(Model.adviceLine({ advice: { model: "m", choice: 2 } }), "m: picked move 2")
    compare(Model.adviceLine({ action: "place_cells", ok: true }), "", "the rule tree alone: no line")
    compare(Model.adviceLine(undefined), "")
  }

  function test_the_talk_shows_the_last_three_exchanges_with_the_models_name() {
    var turns = [
      { q: "one?", a: "first", ok: true, model: "qwen3.5:9b" },
      { q: "two?", a: "second", ok: true, model: "qwen3.5:9b" },
      { q: "three?", a: "", ok: false, why: "no model set: name one in [brain.advisor]" },
      { q: "four?", a: "fourth", ok: true, model: "qwen3.5:9b" }
    ]
    var lines = Model.chatLines(turns)
    compare(lines.length, 6, "three exchanges, a question and an answer each")
    compare(lines[0], { who: "you", text: "two?", ok: true })
    compare(lines[1], { who: "qwen3.5:9b", text: "second", ok: true })
    compare(lines[3], { who: "advisor", text: "no model set: name one in [brain.advisor]", ok: false })
    compare(lines[5].text, "fourth")
    compare(Model.chatLines(turns, 1).length, 2)
    compare(Model.chatLines(undefined), [])
  }

  function test_a_question_on_its_way_is_shown_at_once_until_its_answer_is_written() {
    var turns = [{ at: 100, q: "one?", a: "first", ok: true, model: "m" }]
    var asked = { q: "two?", at: 200 }
    var waiting = Model.talkLines(turns, asked, 1)
    compare(waiting.length, 4)
    compare(waiting[2], { who: "you", text: "two?", ok: true })
    compare(waiting[3].text, "…")
    var done = turns.concat([{ at: 209, q: "two?", a: "second", ok: true, model: "m" }])
    compare(Model.answered(done, asked), true)
    compare(Model.talkLines(done, asked, 1), [{ who: "you", text: "two?", ok: true },
                                              { who: "m", text: "second", ok: true }])
    // the same words asked again: the old answer does not count for the new question
    compare(Model.answered(turns, { q: "one?", at: 300 }), false)
    compare(Model.talkLines(turns, null, 1).length, 2, "nothing on its way")
  }

  // cos20 #630, K50 and K13: the talk keeps a question in one line and cut. What the bar
  // remembered counts as the same words.
  function test_a_question_as_the_talk_keeps_it_counts_as_answered() {
    var turn = function(q) { return [{ at: 100, q: q, a: "x", ok: true }] }
    compare(Model.answered(turn("how are you"), { q: "how are  you", at: 100 }), true)
    var many = new Array(601).join("x")
    compare(Model.answered(turn(many.slice(0, 500)), { q: many, at: 100 }), true)
    compare(Model.answered(turn("how are you"), { q: "how are they", at: 100 }), false, "other")
    compare(Model.answered(turn("how are you"), { q: "how are you", at: 200 }), false, "older turn")
  }

  function test_who_answers_is_one_list_found_first_then_the_two_ways_to_get_one() {
    var found = [{ id: "agent:opencode", text: "OpenCode — your own keys", name: "OpenCode" },
                 { id: "http:http://localhost:11434/v1", text: "Ollama on this machine", models: ["a"] },
                 { id: "http:http://10.0.0.2/v1", text: "m at 10.0.0.2", model: "m" }]
    var rows = Model.whoRows(found)
    compare(rows.length, 5)
    compare(rows[3].text, "set up an agent in Omarchy…")
    compare(rows[4].text, "an address of its own…")
    compare(Model.whoRows(undefined).length, 2, "nothing found: the two ways still stand there")
    // an agent and a server ask which model; a ready connection is tried at once
    compare(Model.whoNext(rows[0]), "models")
    compare(Model.whoNext(rows[1]), "models")
    compare(Model.whoNext(rows[2]), "trying")
    compare(Model.whoNext(rows[3]), "setup")
    compare(Model.whoNext(rows[4]), "address")
    verify(Model.WHO_DATA.indexOf("Never its key") > 0, "the choice says where the data goes")
    verify(Model.tryingText("OpenCode").indexOf("asking OpenCode once") === 0)
  }

  function test_a_long_list_of_models_shows_eight_and_counts_the_rest() {
    var many = []
    for (var i = 0; i < 15; i++) many.push("p/m" + i)
    var rows = Model.modelRows(many)
    compare(rows.shown.length, 8); compare(rows.more, 7)
    compare(Model.modelRows(["a"]), { shown: ["a"], more: 0 })
    compare(Model.modelRows(undefined), { shown: [], more: 0 })
  }

  function test_advice_on_moves_is_off_first_and_never_every_move_through_an_agent() {
    compare(Model.adviseText({ advise: 0, calls: 12, limit: 100 }), "advise on moves: off")
    compare(Model.callsText({ advise: 0, calls: 12, limit: 100 }), "calls today 12 / 100")
    compare(Model.callsText({ calls: 3, limit: 0 }), "calls today 3")
    compare(Model.callsText(null), "")
    compare(Model.adviseText({ advise: 15, calls: 0, limit: 0 }), "advise on moves: every 15th move")
    compare(Model.adviseText({ advise: 1 }), "advise on moves: every move")
    compare(Model.adviseText(null), "advise on moves: off")
    compare(Model.adviseNext({ via: "http", advise: 0 }), 15)
    compare(Model.adviseNext({ via: "http", advise: 15 }), 1)
    compare(Model.adviseNext({ via: "http", advise: 1 }), "head")
    compare(Model.adviseNext({ via: "http", advise: "head" }), 0)
    compare(Model.adviseNext({ via: "agent", advise: 0 }), 15)
    compare(Model.adviseNext({ via: "agent", advise: 15 }), "head", "an agent's allowance is the player's")
    compare(Model.adviseNext({ via: "agent", advise: "head" }), 0)
    compare(Model.adviseSteps("agent").indexOf(1), -1)
  }

  function test_the_last_step_lets_its_agent_take_over() {
    // cos20 #527: the founder's switch "soll pi übernehmen", named by who answers
    compare(Model.adviseText({ advise: "head", label: "pi" }), "pi takes over: chooses every move")
    compare(Model.adviseText({ advise: "head" }), "your agent takes over: chooses every move")
    compare(Model.adviseSteps("http").indexOf("head"), 3)
  }

  function test_the_wake_scripts_stage_is_believed_only_while_it_runs() {
    compare(Model.setupStage({ stage: "brain" }, true, false), "brain")
    compare(Model.setupStage({ stage: "waking" }, true, true), "waking")
    compare(Model.setupStage({ stage: "brain" }, false, false), "", "a file left behind by a killed run")
    compare(Model.setupStage({ stage: "done" }, true, true), "", "the last run's word, not this one's")
    compare(Model.setupStage({ stage: "failed", said: "x" }, false, false), "failed")
    compare(Model.setupStage({ stage: "failed", said: "x" }, false, true), "",
            "a brain update that failed does not take the panel of a living Shikigon")
    compare(Model.setupStage(null, false, false), "")
    compare(Model.setupSaid({ stage: "failed", said: "Max 5 anonymous agents per IP." }),
            "Max 5 anonymous agents per IP.")
    compare(Model.setupSaid({ stage: "done", said: "" }), "")
    compare(Model.setupSaid(undefined), "")
  }

  function test_a_new_one_that_did_not_wake_says_why_where_the_button_is() {
    var ended = Model.panelState({ agent: true, said: "Max 5 anonymous agents per IP.",
                                   status: { mood: "asleep", reason: "key_ended" } })
    compare(ended.name, "key_ended")
    compare(ended.lines, ["Max 5 anonymous agents per IP."])
    compare(ended.actions, ["wake a new one"])
  }

  function test_the_newest_lesson_shows_its_two_short_parts_and_what_it_rests_on() {
    var older = { at: 1000, from_moves: 16, lessons: "old", avoid: "old less", double_down: "old more" }
    var newest = { at: 2000, from_moves: 100, lessons: "a long paragraph for the model",
                   avoid: "Fewer market sales.", double_down: "More missions." }
    var view = Model.lessonView([older, newest], 2000 + 3 * 3600)
    compare(view.more, "More missions.")
    compare(view.less, "Fewer market sales.")
    compare(view.basis, "from 100 moves, 3 h ago")
    compare(Model.lessonView([newest], 2000 + 300).basis, "from 100 moves, 5 min ago")
    compare(Model.lessonView([newest], 2000 + 20).basis, "from 100 moves, just now", "not \"0 min ago\"")
    compare(Model.lessonView([newest], 2000 + 3 * 86400).basis, "from 100 moves, 3 days ago")
    compare(Model.lessonView([], 0), null, "no block before the first lesson")
    compare(Model.lessonView(undefined, 0), null)
  }

  function test_a_key_types_only_printable_characters() {
    compare(Model.typedText("a"), "a")
    compare(Model.typedText("ä"), "ä", "a key that types an umlaut itself")
    compare(Model.typedText(" "), " ")
    compare(Model.typedText(""), "", "a key without text (Shift, F1)")
    compare(Model.typedText("\r"), "", "Enter has its own handler")
    compare(Model.typedText("\t"), "")
    compare(Model.typedText("\u001b"), "", "Esc")
    compare(Model.typedText("\u007f"), "", "Delete")
    compare(Model.typedText(undefined), "")
    // b1637b0 A1/A2: the direction marks and isolates are control characters here too
    compare(Model.typedText("\u202e"), "", "right-to-left override")
    compare(Model.typedText("\u2067"), "", "right-to-left isolate")
    compare(Model.typedText("\u200d"), "\u200d", "a joiner inside an emoji stays")
  }

  function test_a_read_only_line_types_with_its_own_keys() {
    var none = { ctrl: false, shift: false, other: false }
    var line = { text: "", cursor: 0, anchor: 0 }
    "abc".split("").forEach(function (ch) { line = Model.lineKey(line, "", ch, none) })
    compare(line.text, "abc")
    compare(line.cursor, 3)
    line = Model.lineKey(line, "left", "", none)
    line = Model.lineKey(line, "", "ü", none)
    compare(line.text, "abüc", "typed at the cursor")
    line = Model.lineKey(line, "backspace", "\b", none)
    compare(line.text, "abc")
    line = Model.lineKey(line, "home", "", none)
    line = Model.lineKey(line, "delete", "\u007f", none)
    compare(line.text, "bc")
    compare(line.cursor, 0)
    line = Model.lineKey(line, "end", "", { ctrl: false, shift: true, other: false })
    compare([line.anchor, line.cursor], [0, 2], "Shift+End selects to the end")
    line = Model.lineKey(line, "", "x", none)
    compare(line.text, "x", "typing replaces the selection")
    line = Model.lineKey(line, "a", "\u0001", { ctrl: true, shift: false, other: false })
    compare([line.anchor, line.cursor], [0, 1], "Ctrl+A selects all")
    line = Model.lineKey(line, "backspace", "\b", none)
    compare(line.text, "")
    compare(Model.lineKey(line, "backspace", "\b", none).text, "", "nothing left to delete")
    compare(Model.lineKey(line, "left", "", none).cursor, 0, "the cursor stays in the line")
  }

  function test_enter_and_paste_are_actions_and_foreign_keys_go_on() {
    var line = { text: "x", cursor: 1, anchor: 1 }
    var none = { ctrl: false, shift: false, other: false }
    compare(Model.lineKey(line, "enter", "\r", none).action, "accept")
    compare(Model.lineKey(line, "v", "\u0016", { ctrl: true, shift: false, other: false }).action, "paste")
    compare(Model.lineKey(line, "insert", "", { ctrl: false, shift: true, other: false }).action, "paste")
    compare(Model.lineKey(line, "", "\u001b", none), null, "Esc is the panel's")
    compare(Model.lineKey(line, "", "", none), null, "Up, Tab, F-keys go on")
    compare(Model.lineKey(line, "", "c", { ctrl: true, shift: false, other: false }), null, "Ctrl+C")
    compare(Model.lineKey(line, "", "x", { ctrl: false, shift: false, other: true }), null, "Alt+x")
    compare(Model.lineInsert({ text: "ab", cursor: 1, anchor: 1 }, "sk-1").text, "ask-1b", "a paste at the cursor")
  }

  function test_a_paste_is_its_first_line_without_control_characters() {
    compare(Model.pastedLine("sk-abc123\n"), "sk-abc123", "a copied key with its newline")
    compare(Model.pastedLine("http://localhost:11434/v1\r\nsecond"), "http://localhost:11434/v1")
    compare(Model.pastedLine("a\tb\u0007c"), "abc")
    compare(Model.pastedLine("größer"), "größer")
    compare(Model.pastedLine("ab\u202ecd\u2066e\u2069\u061c\u200f"), "abcde", "direction marks out (b1637b0)")
    compare(Model.pastedLine(""), "")
    compare(Model.pastedLine(undefined), "")
  }

  // cos20 #492, Betreuer-Durchgang ab1a021 (B1-B5): every file and answer the bar reads loses its
  // control characters. The data stand as the brain writes them: json.dump in ASCII, so a mark
  // is an escape in the text, and the parse makes it a character.
  function test_every_input_of_the_bar_loses_its_control_characters() {
    var status = Model.readJson('{"last": {"ok": false, "advice": {"why": "a\\u202eb", '
                                + '"fallback": "pi: x\\u2066y\\u2069"}}, "name": "N\\u200dJ"}')
    compare(status.last.advice.why, "ab", "the model's reason (B3)")
    compare(status.last.advice.fallback, "pi: xy", "an agent's stderr line (B3)")
    compare(status.name, "N\u200dJ", "the joiner of an emoji stays, as on the server and in the SDK")
    compare(status.last.ok, false, "numbers and booleans as they were")
    var talk = Model.readJson('[{"q": "wie?", "a": "eins\\nzwei\\u061c\\r\\u009b31m", "ok": true}, '
                              + '{"q": "x", "why": "no answer (pi: \\u202eevil)", "ok": false}]')
    compare(talk[0].a, "eins\nzwei31m", "line breaks stay, the rest goes (B2)")
    compare(talk[1].why, "no answer (pi: evil)", "an agent's reason (B2, B5)")
    compare(Model.readJson('{"stage": "failed", "said": "Max 5 \\u202eagents"}').said, "Max 5 agents",
            "the world's sentence (B1)")
    compare(Model.readJson('[{"more": "a\\u202bb", "less": [["tief\\u202d"]]}]')[0].less[0][0], "tief",
            "nested lists and objects (B4)")
    compare(Model.readJson('"s\\u202et"'), "st", "a file that is one string")
    compare(Model.readJson("17"), 17)
    compare(Model.readJson("null"), null)
    compare(Model.readJson('{"t": "gr\\u00f6\\u00dfer"}').t, "größer", "letters stay")
    var thrown = false
    try { Model.readJson("{broken") } catch (e) { thrown = true }
    verify(thrown, "a broken file still throws: each caller keeps its fallback")
    compare(Model.readLine("refused\u202e by the world\u001b[0m\u009b"), "refused by the world[0m",
            "the brain's stderr line")
    compare(Model.readLine(undefined), "")
  }

  // cos20 #492: the IPC target takes compass words only — free text would stand in argv.
  function test_compass_word() {
    compare(Model.compassWord("grow"), "grow")
    compare(Model.compassWord("  Defend "), "defend")
    compare(Model.compassWord("free"), "free")
    compare(Model.compassWord("be careful"), "", "free text is refused")
    compare(Model.compassWord("what is a blinker?"), "", "a question is refused")
    compare(Model.compassWord(""), "")
  }

  // cos20 #492, eleventh review: Omarchy 4.0.0 and 4.0.1 read markup in their Button and
  // PanelSectionHeader. A text for them holds no tag, so Qt never reads it as styled text.
  function test_a_text_for_the_shells_components_holds_no_tag() {
    var named = Model.hostText('<img src="https://evil.example/x.png" width=9999>')
    verify(named.indexOf("<") < 0, named)
    compare(named, '‹img src="https://evil.example/x.png" width=9999>')
    compare(Model.hostText("answers: " + "<b>qwen</b>"), "answers: ‹b>qwen‹/b>")
    compare(Model.hostText("qwen3.5:9b"), "qwen3.5:9b", "a plain name stays as it is")
    compare(Model.hostText(7), "7")
    compare(Model.hostText(undefined), "")
    compare(Model.hostText(null), "")
  }

  // cos20 #492, Fehlersuche Stück 23 (H13): a label of the shell is one line. A tournament
  // number or a name of the world with line breaks does not make the header or a button tall.
  function test_a_text_for_the_shells_components_is_one_line() {
    var ls = String.fromCharCode(0x2028), ps = String.fromCharCode(0x2029)
    compare(Model.hostText("TOURNAMENT #7\n\n\nENDS"), "TOURNAMENT #7   ENDS")
    compare(Model.hostText("a" + ls + "b" + ps + "c\r\nd"), "a b c  d")
    compare(Model.hostText("qwen3.5:9b · from 100 moves"), "qwen3.5:9b · from 100 moves",
            "a line without a break stays as it is")
    compare(Model.hostText("<b>\nx"), "‹b> x", "the tag rule still holds")
  }

  // cos20 #492, betreuer pass 026bb73, B3: a typed address is shown by its host alone, as
  // `connections.short_host` shows it (test_betreuer_026bb73.py holds these cases against it).
  function test_address_host_never_shows_a_name_or_password() {
    compare(Model.addressHost("https://me:pw@llm.example.org:8443/v1"), "llm.example.org")
    compare(Model.addressHost("https://me:p@ss@Host.Example"), "host.example")
    compare(Model.addressHost("http://user@[::1]:11434"), "::1")
    compare(Model.addressHost("http://127.0.0.1:11434/v1"), "127.0.0.1")
    compare(Model.addressHost("https://me:pw@"), "")
    compare(Model.addressHost("me:pw@host.example"), "")
    compare(Model.addressHost("https://me:pw@host.example?k=1#x"), "host.example")
    compare(Model.addressHost(""), "")
    compare(Model.addressHost(undefined), "")
  }

  // cos20 #492, the founder on the device 07.11.2026: a compass set from the panel showed only
  // after the next move, which had looked before the click (Model.currentCompass).
  function took(at, preset, ok) { return { at: at, instruction: preset, preset: preset, ok: ok } }

  function test_current_compass_a_word_newer_than_the_state_shows() {
    var status = { compass: "autonomous", seen_at: 100 }
    compare(Model.currentCompass(status, [took(105, "attack", true)]), "attack")
  }

  function test_current_compass_in_the_same_second_the_word() {
    compare(Model.currentCompass({ compass: "autonomous", seen_at: 100 },
                                 [took(100, "attack", true)]), "attack")
  }

  function test_current_compass_a_state_newer_than_the_word_shows() {
    // the terminal set trade after the click: the next move's state says so
    compare(Model.currentCompass({ compass: "trade", seen_at: 110 },
                                 [took(105, "attack", true)]), "trade")
  }

  function test_current_compass_a_refused_word_does_not_show() {
    var journal = [took(107, "defend", false), { at: 106, words: "wait", ok: true },
                   took(105, "attack", true)]
    compare(Model.currentCompass({ compass: "autonomous", seen_at: 100 }, journal), "attack",
            "the newest word the world took, past a refused one and a move")
    compare(Model.currentCompass({ compass: "autonomous", seen_at: 100 },
                                 [took(107, "defend", false)]), "autonomous")
  }

  function test_current_compass_only_compass_words() {
    var status = { compass: "grow", seen_at: 100 }
    compare(Model.currentCompass(status, [took(105, "free", true)]), "grow",
            "the brain writes free as autonomous")
    compare(Model.currentCompass(status, [took(105, "<b>x</b>", true)]), "grow")
    compare(Model.currentCompass(status, [took(105, "autonomous", true)]), "autonomous")
  }

  function test_current_compass_without_seen_at_or_journal() {
    compare(Model.currentCompass({ compass: "grow" }, [took(5, "attack", true)]), "grow",
            "a status without seen_at says nothing about when it looked")
    compare(Model.currentCompass({ compass: "grow", seen_at: 100 }, []), "grow")
    compare(Model.currentCompass({ compass: "grow", seen_at: 100 }, undefined), "grow")
    compare(Model.currentCompass(undefined, undefined), "")
  }
}
