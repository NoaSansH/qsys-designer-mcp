# Q-SYS design guidance and review checklist

## Signal flow conventions
- Lay out schematics left→right: inputs → processing (AEC, gating/AGC, EQ) → mixing → output processing (EQ, delay, limiting) → outputs.
- One schematic page per functional area (Audio, Control, Video, Paging), with pages named after the function.
- Use **Signal Names** (or Signal Name blocks) for long cross-page connections instead of wiring across the page. Keep names consistent, e.g. `MIC_01`, `PGM_L`.
- Group related components with schematic boxes or text labels. Name every component descriptively ("Room A Mic Mixer", not "Matrix Mixer 1").

## Gain structure
- Set the preamp gain so normal speech peaks around −20 to −12 dBFS, with headroom for shouting.
- Keep mixer crosspoints at 0 dB where possible and control levels with dedicated Gain components (UCI-facing) so the mixer stays unity.
- Put a limiter or soft-clip before amplifier outputs, and set amp gain so a full-scale digital signal doesn't clip the amp.

## Conferencing / AEC
- Each mic channel gets its own AEC processor. The AEC **reference** is the far-end signal sent to the room's loudspeakers, taken as close to the speaker output as possible but **before** any non-linear processing.
- Order: Mic → AEC → (NC/NLP inside AEC) → automixer (gating/gain-sharing) → to far end (USB/softphone/VoIP).
- Don't send the far-end audio back to itself (mix-minus): the far-end receive must not feed the far-end transmit.
- Use Acoustic FBC only in voice-lift or reinforcement scenarios, not for conferencing to the far end.

## Control and scripting
- Expose controls to UCIs, third-party control (QRC/ECP), and scripts through component **Script Access** (Script, External, All) or Named Controls. Set it deliberately, and keep the default (None) where access isn't needed.
- Prefer Block Controller or Text Controller with clearly named controls. Keep device drivers in plugins when they'll be reused.
- Every external device driver should expose Connected/Status controls, reconnect automatically, and avoid busy loops.
- Use Snapshots for room presets and combine/divide states. Use Room Combiner for divisible rooms.

## Network / Dante / AES67
- Use Q-LAN on a properly configured network (QoS, IGMP snooping with a querier, and no EEE on switch ports for audio).
- Dante: check the sample rate (48 kHz) and latency settings. Name Dante channels in the design to match the physical labels.

## Review checklist
1. All components named descriptively. No default "Gain 1", "Mixer 2".
2. No unconnected inputs that should carry signal. No stray outputs feeding nothing (check the Inspector).
3. AEC references correct; mix-minus correct on every conferencing path.
4. Output protection (limiters) before amps and loudspeakers.
5. Script Access is set only where needed. Named Controls are documented.
6. Scripts: no implicit globals, no blocking loops, and status/connection indicators for every external device.
7. Inventory matches the hardware (models, names, IPs). Status/Health components are wired to monitoring.
8. UCIs: controls use ranges and steps that make sense, and a mute/volume is present for every user-facing zone.
