# Audio Plugin

Reactive LED visualization driven by system audio (PipeWire/PulseAudio). Captures the default audio sink output and maps frequency/volume data to keyboard LEDs in real time.

## Requirements

- `pw-cat` (PipeWire) — used for audio capture
- `pactl` — used to find the default audio sink

## Modes

### `volume`

All LEDs change to the same color based on overall volume level. Uses a 3-color gradient (low/mid/high) — any color can be set to `off` (black).

**Use case**: simple reactive glow that pulses with the music.

```yaml
params:
  mode: volume
  color_low: off              # color at silence (off = black)
  color_mid: [255, 200, 0]    # color at mid volume
  color_high: [255, 0, 0]     # color at max volume
```

### `spectrum`

FFT frequency bands mapped to the keyboard X axis (left = low freq, right = high freq). Amplitude is represented on the Y axis.

Two display styles:

- **`bars`** (default) — LEDs fill from bottom to top like a classic equalizer. A column at 50% amplitude lights the bottom 3 of 6 rows.
- **`brightness`** — All LEDs in each column are lit. Brightness scales with the column's amplitude. No on/off threshold.

**Use case**: classic equalizer visualization across the keyboard.

```yaml
params:
  mode: spectrum
  spectrum_color_low: [0, 0, 255]    # color at low amplitude
  spectrum_color_high: [255, 0, 0]   # color at high amplitude
  freq_scale: log                    # log | linear
  spectrum_style: bars               # bars | brightness
```

### `bands`

All LEDs light up with a single color determined by the dominant frequency content. The audio is split into configurable frequency bands, each with its own color. A spectral centroid algorithm determines which color to show at each moment.

Four blending modes:

- **`centroid`** (default) — color from the amplitude-weighted spectral centroid. Brightness from peak band amplitude. Clean single-color output that shifts with the music.
- **`energy`** — same centroid color, but brightness reflects total energy across all bands. More active music = brighter.
- **`saturate`** — each band contributes its color proportionally to its amplitude (weighted average). Shows mixed colors when multiple bands are active.
- **`dominant`** — color of the single loudest band, with smooth blending toward the second loudest.

Per-band smoothing adapts to frequency: bass colors persist (slow decay), treble colors flash briefly (fast decay), matching the perceptual characteristics of each frequency range.

**Use case**: whole-keyboard color that reacts to the tonal character of the music.

```yaml
params:
  mode: bands
  bands_blend: centroid              # centroid | energy | saturate | dominant
  bands:
    - {freq_min: 20, freq_max: 120, color: [255, 0, 0]}        # Sub/Kick — red
    - {freq_min: 120, freq_max: 500, color: [255, 160, 0]}     # Bass/Snare — orange
    - {freq_min: 500, freq_max: 2000, color: [0, 255, 50]}     # Voice/Guitar — green
    - {freq_min: 2000, freq_max: 5000, color: [0, 100, 255]}   # Presence — blue
    - {freq_min: 5000, freq_max: 20000, color: [160, 0, 255]}  # Brilliance — purple
```

### `spectrum_bands`

Combines the spectrum layout (X = frequency, like `spectrum`) with per-band colors (like `bands`). Each frequency column is colored according to which band it belongs to, with smooth color gradients between adjacent bands. All LEDs in each column are lit with brightness proportional to amplitude.

**Use case**: colorful equalizer where each frequency range has its own color identity.

```yaml
params:
  mode: spectrum_bands
  # Uses the same `bands` config for colors and frequency ranges
```

## Shared Parameters

These apply to all modes:

```yaml
params:
  capture_volume: 10.0       # pw-cat capture gain (amplification)
  normalization: peak         # peak | window | hybrid
  peak_decay: 0.999           # peak mode: decay rate per frame
  window_seconds: 3           # window mode: rolling window size
  hybrid_mix: 0.5             # hybrid mode: 0=full window, 1=full peak
```

### Normalization Modes

Controls how amplitude is normalized to the 0-1 range:

- **`peak`** (default) — tracks the all-time peak with a slow exponential decay. Preserves dynamics across song sections: quiet intros appear dim, loud choruses appear bright. The `peak_decay` parameter controls how fast the peak decays (0.999 = ~23s to halve at 30fps, 0.995 = ~7s).
- **`window`** — rolling window normalization. Always maps recent activity to full 0-100% range. Good for consistent brightness regardless of volume. `window_seconds` controls the window size.
- **`hybrid`** — blends peak and window results. `hybrid_mix` controls the balance (0.0 = pure window, 1.0 = pure peak, 0.5 = equal mix).

## Default Band Colors

The default 5 bands are tuned for perceptual distinctness on RGB LEDs:

| Band | Frequency | Musical Content | Color |
|------|-----------|-----------------|-------|
| Sub/Kick | 20–120 Hz | Kick drum, sub-bass | Red `[255, 0, 0]` |
| Bass/Snare | 120–500 Hz | Bass guitar, snare body | Orange `[255, 160, 0]` |
| Voice/Guitar | 500–2000 Hz | Vocals, rhythm guitar | Green `[0, 255, 50]` |
| Presence | 2000–5000 Hz | Snare crack, vocal clarity | Blue `[0, 100, 255]` |
| Brilliance | 5000–20000 Hz | Hi-hat, cymbals, air | Purple `[160, 0, 255]` |

Colors were chosen to be perceptually distinct on keyboard LEDs (no yellow, which appears white on LEDs).

## Full Example Configuration

```yaml
plugins:
  audio:
    enabled: true
    priority: 40
    opacity: 0.8
    params:
      mode: spectrum_bands
      capture_volume: 10.0
      normalization: peak
      peak_decay: 0.999
      # Spectrum mode
      spectrum_color_low: [0, 0, 255]
      spectrum_color_high: [255, 0, 0]
      freq_scale: log
      spectrum_style: bars
      # Bands / Spectrum Bands
      bands_blend: centroid
      bands:
        - {freq_min: 20, freq_max: 120, color: [255, 0, 0]}
        - {freq_min: 120, freq_max: 500, color: [255, 160, 0]}
        - {freq_min: 500, freq_max: 2000, color: [0, 255, 50]}
        - {freq_min: 2000, freq_max: 5000, color: [0, 100, 255]}
        - {freq_min: 5000, freq_max: 20000, color: [160, 0, 255]}
```
