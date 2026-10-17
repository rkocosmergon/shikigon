import QtQuick
import QtTest
import "../../qml/ShikigonModel.js" as Model

// The panel's logic, tested with Qt's own runner — the way the shell loads it, no Node:
//   /usr/lib/qt6/bin/qmltestrunner -input tests/qml
TestCase {
  name: "ShikigonModel"

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
    compare(tip.id, "awake"); compare(tip.title, "Thread-sharp is awake")
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
    compare(tip.body, "tournament #281, 3 free slots left. Open terminal, press T to take it.")
    var won = Model.nextTip({ name: "T", mood: "thriving", fields: 1 }, ["awake", "landless"], 4990, 5000, false)
    compare(won.id, "first_field"); compare(won.title, "T won its first field!")
    compare(Model.nextTip({ name: "T", mood: "thriving", fields: 1 }, ["awake"], 0, 5000, false).id,
            "compass", "never landless here: no celebration")
  }

  // A mission by its kind, not by the action's name (#451, 14.10.)
  function test_a_hints_body_is_never_read_as_markup() {
    compare(Model.notifyBody("Slot <b>open</b> & free"), "Slot &lt;b&gt;open&lt;/b&gt; &amp; free")
    compare(Model.notifyBody("&lt;"), "&amp;lt;", "what looks escaped already is shown as written")
    compare(Model.notifyBody(undefined), "")
  }

  function test_journal_names_the_mission() {
    compare(Model.journalLine({ action: "start_mission", kind: "gather_spores", ok: true }).text,
            "mission: gather spores")
    compare(Model.moveWords({ action: "market_list", ok: true }), "market list", "an old entry without words")
    compare(Model.moveWords({ action: "market_list", words: "sell on the market", ok: true }), "sell on the market")
    compare(Model.journalLines([{ action: "start_mission", kind: "siege_field", ok: true },
                                { action: "start_mission", kind: "siege_field", ok: true }])[0].count, 2)
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
                 [999499, "999k"], [999500, "1.0M"], [1250000, "1.2M"], [180, "180"]]
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
    compare(Model.adviseNext({ via: "http", advise: 1 }), 0)
    compare(Model.adviseNext({ via: "agent", advise: 0 }), 15)
    compare(Model.adviseNext({ via: "agent", advise: 15 }), 0, "an agent's allowance is the player's")
    compare(Model.adviseSteps("agent").indexOf(1), -1)
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
    compare(Model.pastedLine(""), "")
    compare(Model.pastedLine(undefined), "")
  }
}
