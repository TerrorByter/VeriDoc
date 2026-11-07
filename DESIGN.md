---
name: VeriDoc
description: Soft paper and ink case-management UI for trade-finance document review; what code established stays apart from what an agent said.
colors:
  paper: "hsl(40 13% 92%)"
  surface: "hsl(42 20% 95.5%)"
  sunken: "hsl(40 12% 88.5%)"
  hairline: "hsl(38 9% 80%)"
  rule-strong: "hsl(38 8% 50%)"
  ink: "hsl(222 28% 13%)"
  ink-muted: "hsl(222 9% 33%)"
  ink-faint: "hsl(222 7% 41%)"
  ink-blue: "hsl(224 44% 30%)"
  ink-blue-soft: "hsl(224 26% 88%)"
  on-ink-blue: "hsl(42 30% 96%)"
  aws-amber: "hsl(28 70% 26%)"
  aws-amber-soft: "hsl(34 50% 86%)"
  sage: "hsl(150 38% 24%)"
  sage-soft: "hsl(145 20% 86%)"
  ochre: "hsl(34 72% 24%)"
  ochre-soft: "hsl(40 52% 84%)"
  rust: "hsl(16 62% 31%)"
  rust-soft: "hsl(18 46% 87%)"
  wine: "hsl(350 52% 31%)"
  wine-soft: "hsl(350 32% 89%)"
  highlighter: "hsl(44 90% 55% / 0.32)"
  highlighter-edge: "hsl(38 75% 38%)"
  dark-paper: "hsl(222 16% 11%)"
  dark-surface: "hsl(222 14% 14.5%)"
  dark-text: "hsl(40 14% 89%)"
  dark-ink-blue: "hsl(224 55% 78%)"
typography:
  display:
    fontFamily: "Hanken Grotesk, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "24px"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "-0.015em"
  headline:
    fontFamily: "Hanken Grotesk, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "18px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  title:
    fontFamily: "Hanken Grotesk, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1.5
  body:
    fontFamily: "Hanken Grotesk, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Hanken Grotesk, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1.5
  data:
    fontFamily: "Geist Mono, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "max(12px, 0.92em)"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "tabular-nums"
rounded:
  sm: "2px"
  md: "4px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  stage-gap: "32px"
components:
  button:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    height: "32px"
    padding: "0 12px"
  button-hover:
    backgroundColor: "{colors.sunken}"
  button-primary:
    backgroundColor: "{colors.ink-blue}"
    textColor: "{colors.on-ink-blue}"
    rounded: "{rounded.md}"
    height: "32px"
    padding: "0 12px"
  button-small:
    height: "28px"
    padding: "0 10px"
  pill-sage:
    backgroundColor: "{colors.sage-soft}"
    textColor: "{colors.sage}"
    rounded: "{rounded.md}"
    height: "22px"
    padding: "0 8px"
  pill-ochre:
    backgroundColor: "{colors.ochre-soft}"
    textColor: "{colors.ochre}"
    rounded: "{rounded.md}"
    height: "22px"
    padding: "0 8px"
  pill-rust:
    backgroundColor: "{colors.rust-soft}"
    textColor: "{colors.rust}"
    rounded: "{rounded.md}"
    height: "22px"
    padding: "0 8px"
  pill-wine:
    backgroundColor: "{colors.wine-soft}"
    textColor: "{colors.wine}"
    rounded: "{rounded.md}"
    height: "22px"
    padding: "0 8px"
  pill-neutral:
    backgroundColor: "{colors.sunken}"
    textColor: "{colors.ink-muted}"
    rounded: "{rounded.md}"
    height: "22px"
    padding: "0 8px"
  pill-ink-blue:
    backgroundColor: "{colors.ink-blue-soft}"
    textColor: "{colors.ink-blue}"
    rounded: "{rounded.md}"
    height: "22px"
    padding: "0 8px"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "14px 16px"
  lead-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "14px 16px"
  lead-card-withheld:
    backgroundColor: "{colors.sunken}"
    textColor: "{colors.ink-muted}"
    rounded: "{rounded.md}"
  segmented:
    backgroundColor: "{colors.sunken}"
    textColor: "{colors.ink-muted}"
    rounded: "{rounded.md}"
    padding: "3px"
  segmented-selected:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    height: "28px"
  calm-line:
    backgroundColor: "{colors.sage-soft}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "12px 14px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    height: "32px"
    padding: "0 10px"
---

# Design System: VeriDoc

## Overview

**Creative North Star: "The Annotated Ledger"**

VeriDoc is a case file on soft paper. An analyst or MLRO reads for long stretches, often projected for a panel, so the ground is a warm grey-cream (never white, nothing above 97% lightness), text is ink-blue near-black, and one muted ink blue carries action, selection and AI proposals. Status is desaturated sage, ochre, rust and wine and always arrives with a word or an icon. The world is flat: hairline borders, 4px corners, no gradients, no glass, almost no shadow.

Its signature is structural honesty. A lead is set as two columns: the agent's proposal in prose on the left, and beside it, divided by one strong vertical rule, a ruled ledger of what code established with a state mark. Anything settled goes quiet: a dashed, transparent receipt rather than a card. Open sections get a strong underline rule and 18px headings; everything else folds.

Light is the default. Dark is an opt-in (`data-theme="dark"`), retuned to the same hues (ink blue lifted to 78% lightness, paper at 11%) and never the default.

**Key Characteristics:**
- Warm paper ground, ink-blue text, a single ink-blue accent
- Hairline rules do the work that shadows and fills do elsewhere
- Prose for agent claims, ledger (label / value rows) for code facts
- Mono only for ids, amounts, dates and check ids, with tabular figures
- Collapsed folds for audit material; the verdict reads first

## Colors

A restrained paper-and-ink palette: two neutrals for ground, one ink-blue accent, four status hues kept dark and dusty so their soft tints stay legible.

### Primary
- **Ink Blue** (hsl 224 44% 30%): links, primary buttons, selected stage and tab text, focus ring, the one accent. Its soft tint (hsl 224 26% 88%) marks selection, AI-proposal pills and the active rail step.

### Secondary
- **Amber for AWS** (hsl 28 70% 26%, soft hsl 34 50% 86%): only the tag for a call that leaves for AWS (service chips such as Bedrock).

### Neutral
- **Paper** (hsl 40 13% 92%): page ground.
- **Surface** (hsl 42 20% 95.5%): cards, top bar, rail, inputs, buttons.
- **Sunken** (hsl 40 12% 88.5%): table heads, code chips, segmented track, document pane ground, withheld leads.
- **Hairline** (hsl 38 9% 80%) and **Strong Rule** (hsl 38 8% 50%): quiet dividers and the ledger divider/control borders.
- **Ink** (hsl 222 28% 13%), **Muted** (hsl 222 9% 33%), **Faint** (hsl 222 7% 41%): text tiers; faint is the floor for any text.
- **Highlighter** (hsl 44 90% 55% at 32%, edge hsl 38 75% 38%): the box that shows a fact on the document page.

### Status
- **Sage** (hsl 150 38% 24%) verified, ran, calm. **Ochre** (hsl 34 72% 24%) medium, partial. **Rust** (hsl 16 62% 31%) high, flagged. **Wine** (hsl 350 52% 31%) critical, contradicted, errors. Each has a `-soft` tint used as pill and callout background; the dark theme remaps all of them to light tints over deep tinted grounds.

### Named Rules
**The Word Beside The Colour Rule.** Status colour never stands alone: a pill carries a dot and a word, a state mark carries an icon and a label.
**The Not Blinding Rule.** No surface is above 97% lightness; a white PDF is multiplied onto the paper tone (`mix-blend-mode: multiply`; dark theme dims it instead) so the document pane is not a lamp.
**The One Accent Rule.** Ink blue is the only hue for action and selection. Amber means AWS and nothing else.

## Typography

**Reading Font:** Hanken Grotesk (system-ui fallback), weights 400, 500, 600, 700 loaded
**Data Font:** Geist Mono (ui-monospace fallback), weights 400, 500

**Character:** A humanist grotesque for prose and a quiet mono for the things an analyst copies and compares. Headings are tightened by 1 to 1.5% letter-spacing and balanced; there is no display face and no uppercase labelling.

### Hierarchy
- **Display** (600, 24px, 1.25): page and stage titles (`h1`, stage `h2`).
- **Headline** (600, 18px, 1.3): primary section headings ("What the investigation found"), case id.
- **Title** (600, 14px): fold headings, card lead lines, state marks.
- **Body** (400, 14px, 1.5): everything else; stage descriptions are capped at 72ch.
- **Label** (500, 12px, sentence case, no tracking): rail group names, field and ledger labels, pills, counts. Helper text uses 13px.
- **Data** (Geist Mono 400, max(12px, 0.92em), tabular figures): ids, SGD amounts, dates, check ids, document names.

### Named Rules
**The Mono Is For Data Rule.** Mono is used only for ids, amounts, dates and check ids (`.mono`, `code`, `kbd`, counts). Names and sentences stay in the reading face; a fact value is mono only when its key is on the id/amount/date list.
**The Verbatim Terms Rule.** Banking terms and check ids appear exactly as the data gives them; amounts format as `SGD 18,530.00`.

## Layout

The case workspace is three columns under a 52px sticky top bar and a case header: stage rail (232px) | stage detail (fluid) | document pane (minmax 320px, 0.8fr, sticky, full height). Stage detail pads 24px 28px and stacks its sections at a 32px gap (`stage-gap`); sections internally use 12 to 14px, cards 14px 16px, pills and controls on a 4/8/12/16 rhythm. Other pages centre at max 1180px (820px narrow). Folds sit flush at the bottom with hairline tops and tighten into each other.

Responsive: under 1180px the document pane drops below the detail (80vh); under 760px the rail becomes a wrapped grid of steps, the segmented control scrolls horizontally, two-column grids collapse to one, and the lead card is already container-query driven (two columns from 560px of card width, otherwise the ledger stacks under the prose behind a strong top rule). Reduced motion removes all animation.

## Elevation & Depth

Flat. Depth is conveyed by tone (surface over paper over sunken) and hairline borders. Shadows exist only as: an overlay shadow on the decision drawer (`0 8px 24px` ink at 14%), a 1px page-sheet shadow under the document page, an inset press on buttons, and a 1px selected-tab lift. Hover and selection are shown by tint and ring (3px soft ring on selectable packs), not by lifting.

### Named Rules
**The Flat At Rest Rule.** Cards, leads and findings carry no shadow. Only overlays and the document sheet may.
**The Quiet Receipt Rule.** What is resolved goes dashed, transparent and muted (refuted and withheld leads, off notes); what needs attention stays solid on surface.

## Shapes

One corner: 4px on buttons, inputs, pills, cards, lead cards, code chips and tabs. Pills are rectangular with a 6px dot, not capsules. Borders are 1px hairline; the ledger divider and control borders use the strong rule. Dashed borders mean absent, withheld or off. The one capsule is the filter chip (999px).

## Components

### Buttons
- **Shape:** 4px, 32px high (28px small), 1px strong-rule border, weight 500.
- **Default:** surface fill, ink text; hover sinks to sunken; active gets a faint inset press.
- **Primary:** ink-blue fill, cream text (Record decision, Score, Continue); hover brightens 8%. One per view.
- **Ghost / danger:** transparent border; danger is wine text with wine-tinted border and soft-wine hover.
- **Focus:** 2px ink-blue outline at 2px offset on every focusable element.

### Pills and counts
A 22px rectangle with a 6px dot and a word, tone-coded sage, ochre, rust, wine, neutral (sunken with inset hairline) or ink blue. Counts are mono 12px on sunken; `hot` turns rust.

### Lead card (signature)
A surface card with a header line (lead id in mono, tags: "AI lead", kind, "Agent: ..." status, pattern), then two columns. Left: the agent's hypothesis in bold, "Proposal of the ...", Expected / Observed rows ruled by hairlines, "Rests on" mono links. Right: the ledger aside, one strong rule away (1px left border from 560px card width, top border below), holding the state mark, the check's reason, a label/value ledger with hairline rows, and "Show X in document" small buttons. Withheld leads are dashed on sunken.

### State mark
Icon plus words, weight 600: sage for Verified / Corroborated / Code agrees, muted for Unverified, wine for Contradicted. Where the agent itself refuted a pattern and code agrees, it reads as agreement in sage, not an alarm.

### Rule-check finding card
Surface card, 14px 16px, severity shown by border tint (ochre, rust, wine at 45 to 60% mix; high and critical add a faint soft-tint fill) together with a severity pill. Compare rows are a dt/dd grid; quotes are mono on sunken; actions sit under a hairline.

### Section, fold and disclosure
A primary section has an 18px heading over a strong-rule underline with a muted note right-aligned. A fold is a `details` with hairline top, a 6px chevron rotating on open, title left and meta right; hover turns the title ink blue. A disclosure is an ink-blue "Show N ..." summary. Nothing audit-only is open by default.

### Segmented tabs
A sunken track with 3px padding; tabs are 13px muted, the selected tab is surface-filled with ink text and a 1px lift. `role="tablist"` with Left and Right arrow keys moving focus and selection (`tabKeys`); mono counts sit inside tabs.

### Calm line
When nothing is open: one sage-tinted bordered line with a tick, bold sentence and a reason. It replaces an empty list.

### Navigation and rail
Top bar 52px on surface with a 2px ink-blue underline on the current page. The rail groups stages (Read, Check, Decide, Explain, Record) with numbered steps; the current step is ink-blue soft with a 600 ink-blue label; a flagged step carries a rust icon and an optional count. Left/Right arrows step through stages.

### Inputs
32px, strong-rule 1px border, surface fill, 4px; focus shifts the border to ink blue; checkboxes use ink-blue accent.

## Do's and Don'ts

### Do:
- **Do** set agent claims as prose and code-established facts as a ruled ledger in a separate column; separate them structurally, not by a pill alone.
- **Do** pair every status colour with a word or icon.
- **Do** keep every surface within the paper range (lightness at or below 97%) and use hairline borders instead of shadows.
- **Do** use 4px corners everywhere except filter chips.
- **Do** set ids, amounts, dates and check ids in Geist Mono with tabular figures, and leave everything else in Hanken Grotesk.
- **Do** make resolved items quiet (dashed, transparent) and collapse audit material behind folds.
- **Do** retune dark mode to the same hues; light stays the default.

### Don't:
- **Don't** put a decision-looking surface on an AI output; leads are proposals and the one primary action remains Record decision.
- **Don't** use pure white, gradients, glass or hard offset shadows.
- **Don't** use colour as the only carrier of meaning, or amber for anything but AWS.
- **Don't** use mono for names or prose, or uppercase tracked labels.
- **Don't** stack same-weight sections with the answer far down; the verdict leads.
