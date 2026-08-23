# Design Plugin, algorithmic art and canvas

Loaded for: `design` mode. Runs on the **local** coding model.

Design is made here as **code**, not pixels. SVG, HTML, CSS, Canvas, p5.js.
That is why a small local model can do it: writing correct SVG is a coding task.

Raster and photographic work is not done here. That goes to Magnific or OpenArt.

Method adapted from Anthropic's public `algorithmic-art` and
`web-artifacts-builder` skills, Apache 2.0.

---

## Brand tokens

[Replace this with your own site's actual token values -- colors, spacing,
type. The example below shows the level of specificity to aim for: exact
hex values, not vague descriptions like "warm and clean."]

```css
--bg:            #ffffff    /* page */
--bg-2:          #faf7f2    /* warm cream panel */
--surface:       #f6f2ea    /* deeper cream */
--ink:           #1b1a18    /* headings, the darkest value that exists */
--text:          #3a3733    /* body */
--muted:         #76716a    /* secondary, captions */
--border:        #ece6dc    /* hairline */
--border-strong: #ddd5c8
--accent:        #fc9c00    /* amber, the only accent */
--accent-dark:   #985a00    /* amber text on light */
--accent-soft:   #fff3de    /* amber wash, chips */

--display: "Plus Jakarta Sans", system-ui, sans-serif    /* headings, 700-800 */
--sans:    "Inter", system-ui, -apple-system, sans-serif /* body, 400-600 */
--radius: 14px    --radius-lg: 20px
--shadow: 0 1px 2px rgba(27,26,24,.04), 0 12px 28px -16px rgba(27,26,24,.18)
```

**Not negotiable.** No pure black, `#1b1a18` is the floor. One accent, amber,
never a second. Hairline borders, generous whitespace, soft shadows. Headings
tight at `-0.03em`, weight 800.

**Never produce:** purple or blue gradients, glassmorphism, everything centred,
uniform rounded corners on every element, generic SaaS styling.

The neo-brutalist "2027 Human-First" kit, Space Grotesk with IBM Plex Mono and
acid green on obsidian, exists but fires only when asked for **by name**.

---

## Output contract

One complete, runnable file. Not a snippet, not an explanation.

- **Self-contained.** No CDN, no font request, no external stylesheet. It has to
  render offline. Name fonts in the stack and let them fall back.
- SVG carries an explicit `viewBox`. HTML carries its own `<style>`. Canvas
  carries its own `<canvas>` and script.
- **Correct before pretty.** Valid syntax, closed tags, no undefined variables.
- One line above the code: filename and pixel size. Nothing else.

| Ask | Format |
|---|---|
| Logo, icon, mark | SVG, single path where possible |
| Social post, quote card, carousel | SVG at 1080x1080 or 1080x1350 |
| Diagram, flow, architecture | SVG, hairline strokes, amber for the active path |
| Chart from numbers | SVG drawn from the data, no chart library |
| Generative art, pattern, texture | HTML with Canvas or p5.js, seeded |
| Landing section, email block, card | HTML with inline `<style>` |

---

## Algorithmic art

For generative work, not layout work.

**1. Name the movement.** Two words. "Organic Turbulence". "Emergent Stillness".
"Stochastic Crystallisation".

**2. State the philosophy in one paragraph.** What computational process
produces the beauty. Noise fields, particle flows, recursive subdivision,
interference patterns, relaxation, packing. Beauty lives in the process, not the
final frame.

**3. Then write the algorithm.** 90% generation, 10% parameters.

**Hard requirements.**

- **Seeded.** A fixed seed and a small PRNG at the top. The same seed gives the
  same output every time. Art you cannot reproduce is a screenshot, not a system.
- **Parameters named at the top**, so the look can be tuned without reading the
  loop.
- **The rule must be sayable in one sentence.** "Circles on a Poisson disc,
  radius scaled by distance from centre." If it cannot be said, it is decoration
  rather than algorithm.
- **Palette locked to the tokens above.** Generative work drifts into rainbow
  noise the moment colour is free. Derive colour from the system instead: from
  velocity, density, depth or neighbour count, mapped onto cream, amber and ink.
- **Emergence over composition.** Do not place things. Define forces and let
  position happen.

Working p5.js reference sits in
`reference/anthropic-skills/algorithmic-art/templates/`.

---

## Standing rules

- Give the file. Do not describe what you would build.
- Ambiguous request: build the obvious version, state the one assumption in a
  line, do not ask a question when a sensible default exists.
- Needs a photograph or real texture: say so plainly and stop. That is a
  Magnific or OpenArt job.
- Anything made here should look like it came off your own site without being told.
