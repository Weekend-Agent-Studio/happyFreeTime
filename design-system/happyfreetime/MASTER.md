# HappyFreeTime Design System

**Product type:** local outing planning workspace

**Audience:** users repeatedly comparing and adjusting practical plans

**Visual direction:** 日光晴蓝（约 60% 色彩强度），现代、轻盈、可信、路线感知

## Principles

- The first screen is the working product, never a marketing landing page.
- Optimize for scanning, comparison, and repeated edits.
- Show assumptions, tradeoffs, route source, and degradation state near results.
- 核心方案卡保持高不透明度；磨砂玻璃只服务于应用外壳、悬浮检查器和控制层。
- Prefer familiar Lucide icons for tools and commands.
- Every interactive control is keyboard reachable and at least 44 px on touch screens.
- 任何缺少的图片、评分、营业时间或地址都使用明确占位或缺口说明，前端不补写事实。

## Palette

| Role | Value | Usage |
|---|---|---|
| Primary | `#1979E6` | selected plan, main command, route action |
| Primary strong | `#075FBF` | hover and emphasis |
| Primary soft | `#E8F3FF` | selected row, route and provenance tint |
| Sun | `#F2C54B` | recommendation badge and sparse warm focus |
| Sun strong | `#DFAA22` | warm emphasis |
| Sun soft | `#FFF6D8` | recommendation rationale and forecast context |
| Mint | `#168978` | verified state and timeline marker |
| Background | `#EDF7FF` | app canvas base beneath ambient gradients |
| Surface | `#FFFEFA` | candidate cards and readable content |
| Surface cool | `#F6F9FD` | quiet controls and unavailable data notes |
| Text | `#172236` | primary copy |
| Text muted | `#66728A` | metadata |
| Border | `rgba(111, 135, 173, 0.20)` | dividers and card outlines |
| Danger | `#A43D38` | blocking error only |

The canvas mixes cool sky blue and warm daylight yellow. Avoid red-orange washes, purple gradients, saturated status text, and single-hue screens. Glass blur is deliberately stronger on rails and drawers (`blur(20–28px)`), while data cards remain readable and mostly opaque.

## Typography

- Stack: `Inter`, `Avenir Next`, `Segoe UI`, `PingFang SC`, `Microsoft YaHei`, sans-serif.
- Body: 13-14 px / 1.6; compact metadata: 9-12 px.
- Panel heading: 16-22 px. Product name: 16 px.
- Use weight and spacing for hierarchy; headings may use slight negative tracking, while Chinese body copy remains naturally spaced.
- Preserve practical Chinese system-font fallbacks; do not install a display font merely to match a concept image.

## Geometry

- Radius: 6-9 px for tags, 10-16 px for controls, cards and glass surfaces.
- Desktop shell: 260 px session rail, flexible conversation workspace, 372 px contextual inspector, with 12 px gutters.
- The contextual inspector starts below the account header and reads as a floating surface; it can collapse without changing conversation state.
- Candidate cards use equal information positions. A blue outline, elevated shadow and action fill distinguish the selected plan.
- Fixed-format controls use stable dimensions; icon buttons are 44 x 44 px.

## Core Components

- **Session row:** title, outcome summary and date; one active tint inside a vertically scrollable rail.
- **Chat turn:** the session restores the complete user/butler exchange. A Plan is a rich assistant reply, not a replacement for conversation history.
- **Shared context:** weather, explicit return constraint and budget appear once above all candidates.
- **Plan card:** photo or honest fallback, strategy title, time/cost/distance, plan-specific warning, compact stops/legs and selection action. Do not repeat a card-level “strongest reason”.
- **Recommendation summary:** only the selected plan expands into detailed recommendation reasons and accepted tradeoffs.
- **Timeline:** stable time column, numbered marker, expandable POI detail and RouteLeg action. All views reference the same plan object and route-leg index.
- **Route overview:** a truthful stop-order track plus total distance and commute duration; geography is shown by Amap, never by a fabricated diagram.
- **Composer:** one-line input, persistent quick adjustments and icon-only send button with accessible label.
- **Inspector tabs:** trip, map, orders, evidence. Unsupported orders show an explicit empty state rather than sample data.
- **Conflict panel:** reason and selectable relaxation options.

## Responsive Behavior

- `>1340`: full `260 / flexible / 372` three-column workspace.
- `1061-1340`: narrower session rail and inspector while preserving simultaneous comparison.
- `901-1060`: compact icon rail, conversation and inspector.
- `<=900`: one-column conversation. History and inspector become accessible full-screen dialogs; the selected plan is expanded first and other plans collapse progressively.
- Mobile keeps the composer sticky in the thumb zone, uses 44 px targets and respects safe-area insets.

## Motion And Accessibility

- 160-220 ms color, elevation and selection transitions; assistant result reveal may use a single 600 ms entrance.
- Loading dots and sheet transitions may animate; disable or collapse them under `prefers-reduced-motion`.
- Use visible `:focus-visible` outlines with 2 px offset.
- Maintain WCAG AA contrast and never encode status by color alone.
