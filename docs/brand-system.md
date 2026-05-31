# Brand System

Forager uses the shared `98.Harness` brand reference for KISTI institutional
colors and logo usage. In the local workspace, the shared source is
`../BRAND.md` from this repository root. This page is the public, repository
tracked copy for Forager documentation.

## Message Architecture

Forager's public brand should lead with the north star from
[`project-direction.md`](project-direction.md):

> Forager lets people entrust meaningful work to agents and return to evidence,
> choices, and continuity instead of mystery.

Use that sentence for high-level positioning. Use the practical language below
for docs, UI labels, and implementation plans.

| Layer | Preferred language |
| --- | --- |
| Product category | Local meta-harness |
| Worker model | Harness-backed agents |
| Main promise | Evidence, choices, and continuity |
| Operating loop | Ondesk to Offdesk to Ondesk |
| Trust model | Completed execution is not accepted truth |
| Durable state | Local approval, evidence, recovery, review, and knowledge promotion |
| Knowledge model | Candidates first, reviewed promotion later |

Avoid leading with generic phrases such as "AI agent manager" or "terminal
session manager" when the context is product positioning. Those phrases are
still accurate for narrow feature descriptions, but the top-level identity is
Forager as the supervising local control plane around other harnesses.

## Source

- KISTI institution symbol page: <https://www.kisti.re.kr/intro/pageView/18>
- Shared workspace logo asset: `../assets/kisti-logo-en.png`
- Forager repository logo asset: `assets/kisti-logo-en.png`

## Official KISTI Colors

| Token | Hex | RGB | CMYK | Meaning |
| --- | --- | --- | --- | --- |
| `kisti.blue` | `#0075ba` | `rgb(0, 117, 186)` | `C100 M40 Y0 K0` | Science and technology |
| `kisti.red` | `#da2128` | `rgb(218, 33, 40)` | `C10 M100 Y100 K0` | Industrial technology |

## Forager UI Tokens

| Token | Hex | Where it is used |
| --- | --- | --- |
| `brand.500` / `kisti.blue` | `#0075ba` | Primary web, docs, TUI, and tmux accent |
| `kisti.red` | `#da2128` | Waiting, attention, and endpoint states |
| `kisti.cyan` | `#38bdf8` | Secondary accent, route starts, hover emphasis |
| `surface.navy` | `#081625` | Website background start |
| `surface.ink` | `#020617` | Website background end and deep surfaces |
| `brand.900` | `#0a324f` | Dark brand shade |

## Implementation Map

| Surface | File |
| --- | --- |
| Astro and Tailwind colors | `website/tailwind.config.mjs` |
| Website global CSS | `website/public/styles.css` |
| mdBook theme | `theme/css/custom.css` |
| TUI theme | `src/tui/styles.rs` |
| tmux status bar | `src/tmux/status_bar.rs` |
| Logo and social preview | `assets/logo.svg`, `assets/logo-lockup.svg`, `assets/social-preview.svg` |

## Usage Rules

- Use `kisti.blue` as the primary institutional accent.
- Use `kisti.red` sparingly for attention, waiting, decision, or endpoint states.
- Avoid green, lime, emerald, gold, or copied template palettes on user-facing
  brand surfaces unless a project has a separate documented reason.
- Keep official KISTI logo usage on a white or near-white badge with adequate
  padding.
- Put the north star ahead of feature inventory on first-contact surfaces.
- Distinguish hosted harness responsibility from Forager responsibility in
  product, docs, and UI copy.
- Preserve the logo metaphor as a bounded evidence trail: frame for local
  boundary, trail for agent work, checkpoints for approval/evidence/review, and
  KISTI red for the human decision point.
- When updating this page, also update the shared workspace reference at
  `../BRAND.md` if the change applies beyond Forager.
