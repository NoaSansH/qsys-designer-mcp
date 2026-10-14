# Showcase prompt: divisible ballroom

A prompt to paste into a new Claude conversation that has this MCP server connected. It shows what the tools can do on a real-sized job.

The scenario is a hotel ballroom that divides into three sections with two operable walls:

- **Hardware:** one Core 110f.
- **Audio:** 16 mics (Dante wireless and analog), per-section automixers and AEC, room combining, house paging and BGM over AES67, a split to a visiting console, and a USB conferencing PC.
- **Control:** device drivers for projectors, lighting, cameras and a video switcher, six modes, and a touch panel plus a technician UCI.

It's sized against a real ~220-component event-space design, but most AV integrators will recognise the room.

The prompt asks for honest reporting throughout. Anything the tools can't do gets logged as a gap, not faked. Static checks, runtime checks and things that can't be tested without audio or hardware are reported separately.

## The prompt

Copy everything inside the fence below into a new conversation.

````text
I want to see how far the qsys-designer MCP server can go on a real-sized Q-SYS job. Please design, build, save, verify and test a complete Q-SYS design for the room described below, using only the qsys-designer MCP tools. Treat it like a job you'd hand to a senior Q-SYS programmer: I care about correct signal flow, sensible defaults, clean naming and control logic that holds up, not just a file that opens.

Ground rules

- Start by listing the qsys-designer tools you have and what each one does. If something in this brief can't be done with them, say so plainly, build the closest honest equivalent, and log it as a gap. Don't fake a result or describe something as built when it isn't.
- Save to a new file only. Use Documents\Q-SYS MCP Test\Showcase_Divisible_Ballroom.qsys. If that file already exists, add _v2, _v3 and so on. Never overwrite, rename, delete or save over any existing .qsys file, and don't open or change any other design.
- Use Designer's real component class names, properties and control names. If you aren't sure of a class name or a control name, look it up with the tools rather than guessing.
- Store conservative levels in the file (details below). Nothing should come up loud or live when the design first starts.
- No real IP addresses or passwords anywhere. Use addresses from 192.0.2.0/24 and keep credentials out of the Lua code (empty or placeholder fields the integrator fills in on site).
- Emulation has no audio. Don't claim that any audio path, AEC, gate or limiter was proven by emulation. Say which checks were static, which were runtime (QRC), and which weren't tested.

The room

A hotel ballroom of about 36 × 18 m that divides into three sections, A, B and C (12 × 18 m each), with two operable walls: wall AB and wall BC. The stage is at the end of section A. The conferencing PC lives in section A. Each section has its own projector and screen, ceiling speakers, a delay ring, and an AV floor box. The house has a building-wide paging and background music system, which sends audio in over AES67.

Combine states. There are four valid states, set by two wall sensors (each one either closed or open):
1. Both walls closed: A | B | C (three separate rooms)
2. Wall AB open, wall BC closed: AB | C
3. Wall AB closed, wall BC open: A | BC
4. Both walls open: ABC (one room)
A room group is the set of sections that are joined. The leader of a group is its lowest letter (A in AB and ABC, B in BC).

Hardware and I/O

One Core 110f. No other DSP. The touch panels are UCIs; you don't need to add TSC hardware.

Inputs:
- Core Mic/Line In 1–8: wired mics. 1 = Lectern A, 2 = Lectern B, 3 = Lectern C, 4 = Floor mic A, 5 = Floor mic B, 6 = Floor mic C, 7 = Stage mic 1, 8 = Stage mic 2 (stage mics belong to section A). Phantom power off by default.
- Software Dante RX "wireless", 8 ch: wireless mics W1–W8 (four two-channel receivers).
- Software Dante RX "sources", 8 ch: floor box A L/R, floor box B L/R, floor box C L/R, media player L/R.
- Software Dante RX "console", 8 ch: returns from a visiting mixing console: Main L, Main R, Sub, Front fill, Foldback, Record L, Record R, spare.
- Core Flex 1–4 as inputs: 1/2 = stage guest laptop L/R, 3/4 = video switcher program audio L/R (this needs a lip-sync delay).
- AES67 RX "house", 4 ch: house BGM L/R, house page, spare.
- USB: one 2×2 sound card to the conferencing PC, plus USB HID call control (mute and hook state).

Outputs:
- AES67 TX "amps 1" and "amps 2", 8 ch each, to the amplifiers. Per section: Main L, Main R, Ceiling, Delay ring. That's 12 channels used and 4 spare.
- Core Line Out 1–3: subwoofers A, B, C. 4/5: stage foldback wedges 1/2. 6: foyer overflow. 7/8: spare.
- Core Flex 5–8 as outputs: 5 = assistive listening, 6 = spare, 7/8 = local recorder L/R.
- Software Dante TX "console split", 16 ch: all 16 mics after input gain only (pre-EQ, pre-AEC), for the visiting console.
- AES67 TX "stream", 2 ch: the stream/record mix to the streaming encoder.
- USB: conference TX to the PC.

DSP requirements per path

Mics (all 16):
- Input gain, then high-pass (100 Hz wired, 120 Hz wireless), then a 4-band parametric EQ (flat), then AEC.
- Only linear processing before the AEC. Dynamics and gating go after it.
- AEC reference: each section's AEC must take the loudspeaker feed of the group that section is in, after the master volume. That feed carries the far end. When sections combine, the reference has to follow, so build the reference selection as routing that the combine logic drives, and show how it changes per state. Wireless mics roam, so their AEC reference follows whichever group is in Conference mode (when no group is conferencing, the reference choice doesn't matter, but it must still be defined).
- After the AEC, one gating automixer per section: section A gets Lectern A, Floor A, Stage 1, Stage 2; B gets Lectern B and Floor B; C gets Lectern C and Floor C. Wireless mics W1–W8 are assigned to sections through an assignment matrix that the combine logic drives (by default W1–W4 to A, W5–W6 to B, W7–W8 to C; when sections join, every wireless mic follows its group).
- Lectern priority: in each section, the lectern mic ducks the floor and wireless mics by a modest amount (about 10 dB).

Speech reinforcement and section mixes:
- Each section has a section mix with these inputs: program L/R, speech reinforcement (the mic mixes of every section in its group), conference far-end, and house BGM. Then a section master volume (max gain 0 dB). The section master feeds that section's outputs.
- House paging must not go through the section master. It goes into every section's outputs at its own fixed level and ducks BGM and program by about 15 dB. It must work when the room is Off, and it must never reach the conference TX, the stream or the recorder. It's muted only in Technician mode, where the visiting console runs the room.

Program sources:
- Each section has a stereo source router with these inputs: its own floor box, stage laptop, media player, delayed video switcher program, local BGM player, house BGM.
- Lip-sync delay on the video switcher audio: start at 0 ms, adjustable up to 250 ms.
- Local BGM: an audio file player with a playlist.
- When sections combine, every router in the group follows the leader's selection.

Outputs:
- An output matrix from the three section feeds (plus page and foldback) to all speaker outputs. Then per output: trim, high-pass (mains and ceiling 60 Hz, delay ring 80 Hz, subs 30 Hz plus a 120 Hz low-pass), 4-band PEQ, delay (the delay rings need about 15 ms to start with), and a peak limiter. Then out to AES67 or the line outs.
- Technician mode: per section, a router chooses between the house section feed and the visiting console returns (Main L/R to mains, Sub to subs, Front fill to ceiling, Foldback to the wedges).
- Foyer overflow takes the mix of whichever group contains A. Assistive listening takes the same speech plus program as section A's group, with no page and no BGM.

Conferencing:
- Conference TX = the mic mixes of the sections in the conferencing group, then a leveler, compressor and mute, then USB TX. No program, page or far-end audio in TX.
- Conference RX = USB RX, then a level control, into the section mixes of the conferencing group only.
- Only a group that contains section A can enter Conference mode, because the PC is there.

Stream and record:
- A stream mix: all mic groups, program and conference far end, but no page. It goes through a limiter to AES67 "stream" and Flex 7/8. It's live only in Stream and Technician modes; in Technician mode, the console's Record L/R replace the house stream mix.

Metering: input meters for all 16 mics, section master meters, output meters for every speaker channel, and TX/RX meters.

Control and state logic

Put the state logic in Text Controllers with Lua. Use one main state machine, not a web of logic gates. Suggested split: a combine controller, a mode controller (it can be the same one), a camera controller, and one driver per external device. Use Component access from Lua to drive the DSP. Every button on the UCI binds to a control on these controllers, and every indicator is driven from the real DSP or device state, so the panel stays truthful if something is changed from somewhere else (another panel, QRC, a snapshot).

Combine:
- Inputs: two wall-sensor controls (closed/open). In the real room these would be GPIO; here they're toggle controls you can set over QRC.
- On a state change: rebuild the groups, re-route the AEC references, the wireless assignment, the speech reinforcement, the source routers and the outputs. Followers copy the leader's mode, source, volume and mute.
- When a wall opens and a group splits, the sections that leave keep the group's mode, except that only the group containing A keeps Conference. A group without A that was in Conference drops to Presentation with mics muted.
- A wall change while a call is live must not unmute anything.

Modes (per room group):
- Off: masters muted and reset to -20 dB, all mics muted, conference TX muted, stream muted, BGM stopped, projectors off and screens up. Page still works. In Off, panel requests to unmute are refused, and unmutes from outside (QRC, another panel) are put back.
- Ambient: BGM (house or local) at a low level, mics muted, projectors off, lighting scene "Ambient".
- Presentation: program to the room, lectern and wireless reinforcement available but stored muted when entering from Off, projector on, lighting "Presentation".
- Conference (only with A in the group): conference RX to the room, TX live when entering from Off unless privacy is on, camera auto-tracking on.
- Stream: like Presentation, plus stream mix live, recorder start/stop available, and camera tracking on.
- Technician: house mix replaced by the console returns for the group's sections, page muted, house reinforcement off, lighting zone controls exposed. Behind a PIN.
- Pressing the active mode button again must not leave the group in a "no mode" state. Mode LEDs are mutually exclusive per group.

Cameras: 6 PTZ cameras over HTTP (2 per section). Each mic has a camera preset. When a gate opens, recall that mic's preset, hold for 3 s, and fall back to the wide shot after 10 s of no gates. Only active in Conference and Stream. Read the gates from the automixer; if you can't drive gates in emulation, give the controller a preset-recall test control so the mapping can still be tested.

Device drivers (Text Controllers, generic protocols, addresses in 192.0.2.0/24):
- Lighting processor over TCP: 8 scenes, 5 zone levels, with feedback.
- 3 projectors over TCP (power, input, status), plus screen up/down per section.
- Video switcher over TCP: route a source to each projector.
- Recorder/stream encoder over HTTP: start, stop, and a status poll every 2 s.
Each driver shows connection status, reconnects on its own, doesn't flood the log, and doesn't raise script errors when the device isn't there (it won't be, in emulation).

Other:
- A device online monitor: ping or connection status for every external device, rolled up into one fault indicator.
- Nightly auto-off at a configurable time (default 23:00). Give it a test control so the auto-off can be fired on demand.
- PIN protection (PIN pad) for the Technician, Room Setup and Audio pages. Store the PIN as a configurable value, not as text in the UCI.
- Snapshot banks if the tools can create and store them: output tuning per combine state (4), audio presets (Speech, Music, Dinner), and a Power Off bank. If the tools can't create snapshot banks, do it with preset tables in Lua and log the gap.

UCI pages

UCI "HALL_PANEL" (iPad-sized, 1024 × 768), with a header on every page showing the room group being controlled, the current combine state and mode, and a home button:
- 00_Home: mode buttons with a line of help text under each, room status, and a Power Off button that asks for confirmation.
- 01_Ambient: BGM source, level, mute.
- 02_Presentation: source per group, projector on/off and screen up/down, volume and mute, mic mute, lighting scene.
- 03_Conference: call mute (with a clear MICS LIVE / MICS MUTED state), volume, privacy, camera auto/manual and presets, and a lip-sync delay hidden behind an info button.
- 04_Stream: stream/record start and stop with status, stream level and mute, camera select.
- 05_Technician (PIN): console patch on/off per section, page mute, lighting zone faders.
- 06_Room_Setup (PIN): wall states, a manual combine override, wireless mic assignment.
- 07_Audio (PIN): per-mic gain, mute and meter for all 16 mics, section master meters, output trims and meters.
- 08_System: device online status, script status, nightly auto-off time.

UCI "TECH_PANEL": one page with all I/O meters and the device status, for the AV team.

Use popups and layers for pages that change between divided and combined states, if the tools support them. If not, say so and use static pages.

Naming rules

- Upper case with underscores. A prefix by area: MIC_, WL_ (wireless), SEC_A_ / SEC_B_ / SEC_C_, SRC_, PGM_, CONF_, PAGE_, BGM_, STREAM_, OUT_, AMP_, CTL_ (state logic), DEV_ (device drivers), SNAP_, UCI pages numbered 00_ to 08_.
- Examples: MIC_L01_HPF, WL_05_AEC, SEC_B_AUTOMIX, SEC_C_MASTER, OUT_A_DELAY_EQ, CONF_TX_MUTE, PAGE_DUCKER, CTL_COMBINE, DEV_LIGHTING.
- Names are unique. Don't leave a Designer default name (Mixer_8x8_3, Selector_14 and so on) on anything you created. Core I/O blocks can keep the names Designer gives them.
- Script Access: External on the CTL_ and DEV_ components and on any DSP block the QRC tests need to read. None everywhere else. If the tests need more, build a separate _TEST copy with All and say which file was tested.

Conservative stored levels

- Section masters -20 dB, muted, max gain 0 dB.
- All mic channels muted at the AEC output or TX, conference TX muted, stream muted.
- Reinforcement crosspoints at -10 dB, page level -10 dB, BGM -20 dB, conference RX -6 dB.
- Limiter thresholds -10 dBFS.
- Preamp gains left at a safe default, phantom off.
- Every stored value must take effect at runtime. Check it over QRC after emulation starts, not just in the file. Some tools store a normalised position as well as a value, and getting that wrong makes a value read back fine but load as minimum.

Validation I want to see

1. Static read-back of the saved file, done separately from the build. Check:
   - every component, with class, name and key properties
   - every wire, and every stored value
   - names unique and matching the naming rules
   - no unconnected audio pins on DSP blocks except listed spares
   - every UCI control bound to a real component control
   - a graph search for each path below, with the AEC reference input treated as not passing audio.
2. Static path checks, positive:
   - each of the 16 mics reaches its section automixer through HPF, EQ and AEC (entering on the Microphone pin)
   - section mics reach conference TX
   - conference RX reaches every section's speaker outputs
   - every program source reaches every section's outputs
   - house page reaches every speaker output without passing through a section master
   - console returns reach the outputs through the technician routers
   - all 16 mics reach the Dante console split.
3. Static path checks, negative:
   - page and program never reach conference TX
   - page never reaches the stream, the recorder or assistive listening
   - conference RX never loops back to TX
   - BGM never reaches TX
   - wireless mics don't bypass the AEC.
4. Runtime tests in emulation over QRC. For each of the 4 combine states, check:
   - the groups and leader
   - the AEC reference routing
   - wireless assignment
   - source router follow-the-leader
   - speech reinforcement crosspoints
   - output and technician routing
   - mode LEDs.
5. Runtime mode tests per group:
   - Off refuses and reverts unmutes
   - each mode's mute, route and level state is correct
   - Conference is refused for a group without A
   - a wall opening during a call doesn't unmute anything, and the group without A drops to Presentation
   - pressing the active mode again keeps the mode
   - Technician needs the PIN, and a wrong PIN is refused
   - page still works in Off and is muted in Technician
   - nightly auto-off fires from its test control
   - the camera preset mapping can be checked with the test control
   - every Lua script reports OK with 0 errors
   - device drivers show offline cleanly without script errors.
6. Stored levels checked at runtime against what you stored.
7. State plainly what can't be tested without audio or hardware: AEC convergence, gating, ducking depth, limiter action, real levels and EQ, Dante/AES67 subscriptions, USB enumeration on the PC, device protocols against real devices, and how the UCI looks on an iPad. Don't mark those as passed.

Report

Write the report in this structure. Use tables where it helps, and give pass/fail counts.

A. MCP capabilities discovered: what each tool could and couldn't do on this job.
B. Implementation summary: signal flow (a text diagram is fine), design decisions and any deviations from this brief, with reasons.
C. Component inventory read back from the saved file: name, class, key properties, stored values.
D. Signal-flow verification: each path, with static and runtime results.
E. Control logic verification: each runtime test and its result, per combine state and per mode.
F. Validation results: save, reload, compile/emulate, script status, invalid components or properties.
G. Artifact information: file path, size, hash, any test copy, and confirmation that no existing file was touched.
H. Limitations and next steps: missing tool features, things that need a real Core or real audio, and on-site tuning.
I. Overall assessment: PASS, PARTIAL PASS or FAIL, and what would be needed for a full pass.

Save the evidence (read-back dump, runtime test log) next to the design file and link it from the report.
````
