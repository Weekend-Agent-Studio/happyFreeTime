# HappyFreeTime Design System

**Product type:** local outing planning workspace
**Audience:** users repeatedly comparing and adjusting practical plans
**Visual direction:** quiet, clear, trustworthy, route-aware

## Principles

- The first screen is the working product, never a marketing landing page.
- Optimize for scanning, comparison, and repeated edits.
- Show assumptions, tradeoffs, route source, and degradation state near results.
- Keep sections unframed; use cards only for individual plan candidates.
- Prefer familiar Lucide icons for tools and commands.
- Every interactive control is keyboard reachable and at least 44 px on touch screens.

## Palette

| Role | Value | Usage |
|---|---|---|
| Primary | `#176B52` | selected states, main command, timeline |
| Primary strong | `#0E503C` | hover and emphasis |
| Primary soft | `#E7F2ED` | selected row and provenance tint |
| Accent | `#D95D45` | secondary map stop and sparse emphasis |
| Route | `#3178C6` | distance, duration, and route source |
| Background | `#F4F6F4` | app canvas |
| Surface | `#FFFFFF` | tools, candidates, composer |
| Surface muted | `#F7F9F7` | quiet controls and empty states |
| Text | `#17211D` | primary copy |
| Text muted | `#66736D` | metadata |
| Border | `#DCE3DF` | dividers and card outlines |
| Danger | `#B42318` | blocking error only |

Avoid purple gradients, glass effects, decorative blobs, and single-hue screens.

## Typography

- Stack: `Inter`, `Segoe UI`, `PingFang SC`, `Microsoft YaHei`, sans-serif.
- Body: 14 px / 1.6; compact metadata: 11-12 px.
- Panel heading: 16-20 px. Product name: 16 px.
- Use weight and spacing for hierarchy; do not scale text with viewport width.
- Letter spacing is `0`.

## Geometry

- Radius: 4 px for tags, 6-8 px for controls and cards.
- Desktop shell: 232 px navigation, flexible workspace, 336 px detail panel.
- Border and a restrained shadow distinguish selected candidates.
- Fixed-format controls use stable dimensions; icon buttons are 44 x 44 px.

## Core Components

- **Session row:** title, state, optional date; one active tint.
- **Assumption chip:** field label plus editable resolved value.
- **Plan card:** strategy, total cost/time, stops, highlights, selection action.
- **Timeline:** stable time column, numbered marker, stop detail, route leg.
- **Composer:** one-line input and icon-only send button with accessible label.
- **Detail tabs:** trip, map, orders; no nested cards.
- **Conflict panel:** reason and selectable relaxation options.

## Responsive Behavior

- `>=1120`: three-column workspace.
- `820-1119`: hide session rail; workspace plus detail panel.
- `<820`: one column; details flow below results, composer is in normal layout.
- `<520`: plan cards and suggestions become one column; labels may wrap.

## Motion And Accessibility

- 150-200 ms color and border transitions only.
- Loading dots may animate; disable them under `prefers-reduced-motion`.
- Use visible `:focus-visible` outlines with 2 px offset.
- Maintain WCAG AA contrast and never encode status by color alone.
