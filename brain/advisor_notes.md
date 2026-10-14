About the Shikigon (what its player sees)
- A Shikigon is one Cosmergon agent of the player's own. It shows as a pair of eyes in the bar of their desktop and plays by itself: one move per tick of the world (about a minute), chosen by a rule tree from the moves the world offers at that moment.
- Its face says how its world is going: thriving (energy rising), content, struggling (energy falling or a catastrophe), asleep (not woken yet, the world did not answer, or its key has ended).
- The key: when a Shikigon is woken, the Cosmergon server gives it an API key. The key is what makes this agent the player's: whoever holds it plays it. It is kept in a private file on the player's machine and is never shown. A free, anonymous key ends 24 hours after its last use; every move moves that time out again. When it ends, the agent stays in the world as a Vagant played by the server and cannot be brought back; the player wakes a new Shikigon.
- The compass: a word from the player (attack, defend, grow, trade, cooperate, explore, or free) tilts its choices. It does not override its character (farmer, trader, diplomat, scientist, warrior, expansionist).
- The panel (click on the eyes) shows energy, fields, rank, compass, the last move, what it learned last and the journal. Its buttons set the compass, open the talk with its advisor, or open the terminal, where the player can play by hand. It moves by itself once per tick; there is no button for a move.
- Tournaments: the server sometimes offers a free tournament slot. Joining it, and choosing or changing the tournament compass (attack, defend, grow), happens in the terminal: open it from the panel and press T. The panel shows the tournament but has no button to join; it is meant to invite the player into the terminal.
- A model can advise it on a move. The rules still decide which moves are possible.
- Its first field: the world is full, so a new Shikigon owns no land and cannot buy a place. It has to win one: its marauder gathers spores, builds bombs, then besieges a weak field, which becomes its own. That takes hours; meanwhile it has no income of its own. A free tournament slot lets it play at once (terminal, T).
- It learns from its own moves: every 100 moves the advisor model looks at its energy before and after each move and writes a short lesson (what worked, what to avoid, what to do more of). The newest lesson is given to the advisor with every advice and answer, and the panel shows it under LEARNED (what to do more, what less). The lessons stay on the player's machine; a newly woken Shikigon starts without them.

Words of Conway's Game of Life used in this world
- Field: a board of cells the agent owns. A cell lives or dies by its eight neighbours: a dead cell with exactly three living neighbours is born, a living cell with two or three stays alive.
- Preset: a known pattern placed on a field in one move.
- Blinker: three cells in a row that flip between horizontal and vertical every tick. The smallest oscillator; left alone it lives forever.
- Block: four cells in a square that never change (a still life).
- Glider: five cells that travel diagonally across the field.
- Toad, pulsar, pentadecathlon: larger oscillators. R-pentomino: five cells that grow chaotically for a long time.
- Tick: one step of the world. One move per tick.
