# Brand System

Forager uses the shared `98.Harness` brand reference for KISTI institutional
colors and logo usage. In the local workspace, the shared source is
`../BRAND.md` from this repository root. This page is the public, repository
tracked copy for Forager documentation.

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
| Logo and social preview | `assets/logo.svg`, `assets/social-preview.svg` |

## Usage Rules

- Use `kisti.blue` as the primary institutional accent.
- Use `kisti.red` sparingly for attention, waiting, decision, or endpoint states.
- Avoid green, lime, emerald, gold, or copied template palettes on user-facing
  brand surfaces unless a project has a separate documented reason.
- Keep official KISTI logo usage on a white or near-white badge with adequate
  padding.
- When updating this page, also update the shared workspace reference at
  `../BRAND.md` if the change applies beyond Forager.
