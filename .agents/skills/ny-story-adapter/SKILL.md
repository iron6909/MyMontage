---
name: ny-story-adapter
description: New Yorker adult English micro-drama adapter. Rewrites public-domain stories into CEFR A2 (Oxford 3000) 10-scene / 25-sentence scripts with three independent reversals, per-sentence speaker routing, and full watercolor town backgrounds. Writes episode JSON draft.
---

# NY Story Adapter (OpenMontage custom skill)

Port of `01-oxford-story-adapter` from ny-english-shorts. Non-destructive:
lives only under `.agents/skills/ny-story-adapter/`, never touches core
scheduling code or built-in templates.

## Job

Rewrite `raw_stories/{epId}.md` into a shootable episode JSON:

- Language locked to **CEFR A2**, core vocabulary inside Oxford 3000, adult
  satire and dialogue games kept.
- **10 scenes / 25 sentences**, 2–3 sentences per scene. The three reversals
  each own separate scenes; the second half never rewinds or repeats logic.
- Per-sentence `speaker`: `narrator`, `leo`, `carpenter`, `cook`. Quoted
  role lines use the role voice, unquoted narration uses narrator.
- All English dialogue uses standard double quotes `"..."`.
- Per-scene `visualAction` states left-right staging plus costumes, and
  `characterAnchor`. Every frame carries a full 19th-century town market
  watercolor background (never isolated on pure white).
- 5 idiomatic adult chunks in `glossaryCard` for the end screenshot card.

## The Chameleon structure (ep01 reference)

| Scenes | Plot | Reversal |
|--------|------|----------|
| 1–2 | Quiet square; carpenter shows bleeding finger | — |
| 3–4 | Officer Leo shows off, sheds coat, orders the dog punished | — |
| 5–6 | Crowd: "the General's dog" -> pale, coat back on, blames carpenter | 1st |
| 7–8 | Cook: "the General has no such dog" -> coat off, "bad animal" | 2nd |
| 9–10 | Cook: "the visiting brother's dog" -> fawns, warns, leaves | 3rd |

## Contract

- Input: `projects/<project>/raw_stories/{epId}.md` (or ny-english-shorts
  `workspace/raw_stories/{epId}.md` when porting).
- Output: episode JSON with `status: "draft"` (same shape as
  ny-english-shorts `workspace/episodes/{epId}.json`).
- Validate with `scripts/validate_script.py` before marking draft.

```bash
python .agents/skills/ny-story-adapter/scripts/validate_script.py --episode projects/ny-ep01/episode.json
```

## Handoff

Next: `openrouter_multispeaker_tts` reads `draft` + `sentences[].text` +
`sentences[].speaker` (quoted role lines go to the role voice).
