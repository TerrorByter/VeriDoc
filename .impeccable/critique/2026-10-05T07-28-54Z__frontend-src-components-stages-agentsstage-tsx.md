---
target: Agents stage
total_score: 20
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 3
target_identity: "file:/Users/sean/Documents/Coding Projects/AIF-Group-1/frontend/src/components/stages/AgentsStage.tsx"
target_fingerprint: "sha256:d58d3e8c2b85407b1e683010a2cbf2666d90e108fe86f395ddee445338034c31"
target_path: /Users/sean/Documents/Coding Projects/AIF-Group-1/frontend/src/components/stages/AgentsStage.tsx
timestamp: 2026-10-05T07-28-54Z
slug: frontend-src-components-stages-agentsstage-tsx
---
# Critique: Agents stage (LIVE-RUN-7, LIVE-RUN-9)

Method: dual-agent (A: design review sub-agent, B: detector sub-agent).

Brief pinned by the user: light mode, not blinding, professional Hermes / Nous Research look. The light theme was not rendered by either assessment.

## Design health score: 20/40 (Acceptable, low end)

1 Visibility of system status 2: header says Fast track / 0/100 / Complete beside a verified lead; "Needs attention" fires though code explained the only finding.
2 Match with real world 3: banking terms verbatim; internal jargon leaks.
3 User control 2: no action on the AI lead.
4 Consistency 2: code-explained finding looks open; refused call looks like a failure.
5 Error prevention 2: one-click false "Confirm flag" on E02.
6 Recognition vs recall 2: L04 names T16 but the document pane stays on February; memo ids not links.
7 Flexibility 2: no jump to leads; tab groups lack arrow keys.
8 Minimalist design 2: briefs and a 63-call trace above the result.
9 Error recovery 1: 17 of 63 calls show red Error (refusals the agent corrected).
10 Help 2: nothing explains verified / corroborated / unverified.

## Detector
Components (tsx): clean. styles.css: side-tab at :525 (mild true positive), side-tab at :565 (false positive, evidence quote), layout-transition at :369 (false positive).

## Priority issues
- [P1] A code-explained finding renders as an open fraud signal (clarify).
- [P1] The answer (the verified lead) is buried ~2,900px down (distill).
- [P1] Evidence is not beside the claim: no document link on leads, memo ids plain text (harden).
- [P2] Refused-and-corrected calls show the same red Error as failures (clarify).
- [P2] Accessibility, mostly light mode: pill.high 4.19, pill.med 4.31, .svc 4.34, .count.hot 4.19, --faint fails both themes; 59px overflow at 390px; text under 12px; targets 17-20px; weak input focus ring (harden).

## Persona red flags
Alex: no jump-to-leads, no undo on Confirm. Sam: tabs lack arrow keys; faint text fails AA; small citation targets. MLRO: false Confirm pollutes the record; no accept/park on L04; header contradicts the lead.

## Minor
Empty grey cell in the facts grid; model ids break mid-word; "Pattern found" uses severity orange; "Version 36 checks" mislabel; Supervisor trace rows with "-" results.

## Questions
Why is the lead the seventh section? Is a code-resolved finding a receipt, not a finding? Should an AI lead be a decision surface?
