---
version: 1
slug: "frontend-src-components-stages-agentsstage-tsx"
primary_target: "frontend/src/components/stages/AgentsStage.tsx"
related_targets: ["frontend/src/styles.css","frontend/src/components/screens/Workspace.tsx"]
---

# Surface brief: Agents stage (and the case workspace shell)

Mode: Operate. Scope: the Agents stage of a case, plus the shared tokens, type and components every stage uses (the visual world is app-wide; this stage proves it).

Audience, job, task: a bank analyst or MLRO deciding what a flagged trade-finance case needs; the same screen is projected for an assessment panel. The task is to see in seconds what the investigation found, what code established versus what an agent said, and where the evidence is. Constraints (PRODUCT.md): AI never looks like a decision; evidence beside every claim; banking terms and check ids verbatim; WCAG AA; light mode, not blinding.

Chosen direction: pinned by the user, no roll. Nous Research: soft paper and ink, flat and understated, low glare. Memorable moment: a lead is set as two columns, the agent's proposal in prose beside a ruled "established by code" ledger of facts with a state mark; an explained rule finding becomes a quiet receipt.

Unresolved: whether the dark theme is kept as a secondary option (kept, retuned to the same palette, never the default).

## Direction contract

THESIS: Separate what an agent said from what code established, structurally, not by a pill; and let what is explained go quiet. Refuses the dark SaaS dashboard of stacked same-weight sections with the answer 2,900px down.

OWN-WORLD: Soft warm paper ground (never pure white, nothing above 97% lightness), ink-blue near-black text, one muted ink-blue accent for action and selection, desaturated sage / ochre / rust / wine for status that always carries a word or icon. Flat: hairline ink borders, 4px corners, almost no shadow, no gradients, no glass. Grotesque sans for reading, mono only for ids, amounts, dates and check ids, with tabular figures. A document page renders multiplied onto the paper tone so a white PDF is not a lamp.

STORY: The analyst opens Agents and reads the verdict first (open items, or none), sees the lead beside the code's ledger and a link to the exact statement row, understands that explained findings need no action, and can open the plan and trace only when they want to audit how the team got there. They believe it because every claim sits next to its evidence.

FIRST VIEWPORT: Left rail unchanged in structure. Stage header compact. Directly under it, "What the investigation found": each flagged lead as a two-column card (agent proposal left, ledger of code-established facts right, state mark "Verified by code", Show in document buttons), then the memo points with linked ids; with no flags, one calm line saying nothing is open. Rule findings follow; explained ones are muted receipts ("Explained by code: T16 + T17 = 18530.00") with Review anyway folded. The Supervisor plan, trace and unchecked observations sit below as collapsed sections. Primary action stays the existing Record decision in the header; nothing on this stage looks like a decision.

FORM: a ruled ledger beside prose on paper, for an Operate surface; pinned by the user (position n/a; seed key: none, user-pinned, no roll).

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
