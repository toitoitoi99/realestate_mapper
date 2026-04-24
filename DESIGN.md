---
version: alpha
name: Lisbon Real Estate Explorer
description: >
  A data-forward real estate research tool for the Greater Lisbon area.
  Dense, functional, and trustworthy — built for investors and home buyers
  who want to explore hundreds of listings quickly.
colors:
  primary: "#2563eb"
  primary-hover: "#1d4ed8"
  primary-tint: "#eff6ff"
  primary-border: "#bfdbfe"
  secondary: "#4b5563"
  muted: "#6b7280"
  faint: "#9ca3af"
  surface: "#ffffff"
  surface-secondary: "#f9fafb"
  surface-tertiary: "#f3f4f6"
  border: "#e5e7eb"
  border-strong: "#d1d5db"
  text-primary: "#111827"
  text-secondary: "#374151"
  text-muted: "#6b7280"
  success: "#16a34a"
  success-bg: "#f0fdf4"
  success-border: "#bbf7d0"
  warning: "#b45309"
  warning-bg: "#fef3c7"
  error: "#dc2626"
  error-bg: "#fef2f2"
  error-border: "#fecaca"
  score-a: "#059669"
  score-b: "#16a34a"
  score-c: "#f59e0b"
  score-d: "#ef4444"
typography:
  display:
    fontFamily: system-ui, -apple-system, sans-serif
    fontSize: 24px
    fontWeight: 700
    lineHeight: 1.2
  heading:
    fontFamily: system-ui, -apple-system, sans-serif
    fontSize: 16px
    fontWeight: 600
    lineHeight: 1.4
  subheading:
    fontFamily: system-ui, -apple-system, sans-serif
    fontSize: 14px
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: system-ui, -apple-system, sans-serif
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: system-ui, -apple-system, sans-serif
    fontSize: 12px
    fontWeight: 500
    lineHeight: 1.4
  caption:
    fontFamily: system-ui, -apple-system, sans-serif
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.4
  micro:
    fontFamily: system-ui, -apple-system, sans-serif
    fontSize: 10px
    fontWeight: 500
    lineHeight: 1
    letterSpacing: 0.04em
  price:
    fontFamily: system-ui, -apple-system, sans-serif
    fontSize: 14px
    fontWeight: 700
    lineHeight: 1
rounded:
  sm: 4px
  md: 8px
  full: 9999px
spacing:
  xs: 4px
  sm: 8px
  md: 12px
  lg: 16px
  xl: 24px
components:
  listing-card:
    backgroundColor: "{colors.surface}"
    borderColor: "{colors.border}"
    borderRadius: "{rounded.md}"
    padding: "{spacing.md}"
  listing-card-active:
    backgroundColor: "#eff6ff"
    borderColor: "#60a5fa"
  listing-card-liked:
    backgroundColor: "#f0fdf4"
    borderColor: "{colors.success-border}"
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    borderRadius: "{rounded.md}"
    padding: "8px 16px"
  button-secondary:
    textColor: "{colors.primary}"
    borderRadius: "{rounded.md}"
    padding: "4px 8px"
  badge:
    borderRadius: "{rounded.full}"
    padding: "2px 8px"
    fontSize: "{typography.micro.fontSize}"
  persona-bar:
    backgroundColor: "{colors.primary-tint}"
    borderColor: "#dbeafe"
    padding: "6px 16px"
  sidebar:
    width: "320px"
    backgroundColor: "{colors.surface}"
    borderColor: "{colors.border}"
---

## Overview

The Lisbon Real Estate Explorer is a data-forward research tool for the Greater Lisbon metropolitan area. It serves four personas — rental investors, flippers, home buyers, and home renters — each with different priorities, but all needing to scan and compare large numbers of listings quickly.

The visual identity is **functional minimalism**: clean whites and neutral grays carry the structure; a single blue primary accent drives all interactive affordances; semantic greens, ambers, and reds communicate quality signals without decoration. The result feels more like a professional data terminal than a consumer property portal — authoritative, scannable, and precise.

Key design principles:
- **Density over spaciousness** — 12–16px card padding, small type (12–14px body), tight gaps. Every pixel earns its place.
- **Flat with intentional borders** — no drop shadows by default; thin `border-gray-200` borders define hierarchy. `shadow-sm` appears only on hover or as a focus indicator.
- **Blue is the only interactive color** — every clickable element, active tab, and focus ring uses the primary blue family. Other colors are semantic (scores, status) never decorative.
- **Numbers are first-class citizens** — prices, scores, m², and per-m² values are bold and visually prominent. They are the product.

## Colors

The palette is a high-contrast neutral base with a single blue accent and a strict semantic layer.

- **Primary (#2563eb):** The sole interactive color. Used for buttons, active states, links, focus rings, and the PersonaBar tint. Never used decoratively.
- **Surface (#ffffff / #f9fafb):** White for cards and panels; `gray-50` for page backgrounds, filter sections, and grouped containers.
- **Text hierarchy (gray-900 → gray-400):** Four steps of gray carry all text — headings in `gray-900`, labels in `gray-700`, body in `gray-600`, captions and dividers in `gray-400`.
- **Border (gray-200 / gray-300):** `gray-200` is the default separation between elements. `gray-300` appears on inputs and stronger dividers.
- **Success (#16a34a / green family):** Positive price signals, liked listings, score grade B.
- **Warning (#f59e0b / amber family):** Score grade C, moderate renovation signals.
- **Error (#dc2626 / red family):** Disliked/hidden listings, score grade D, price-too-high warnings.
- **Score grades:** A = emerald-600 (#059669), B = green-600 (#16a34a), C = amber-500 (#f59e0b), D = red-500 (#ef4444). Always displayed as filled colored pills with white text.

Do not introduce colors outside this palette without a semantic justification. Avoid decorative gradients or background color fills beyond `gray-50`.

## Typography

One font family — the OS native sans-serif stack (`system-ui, -apple-system, sans-serif`) — applied at 6 size steps. No web fonts are loaded; this keeps the tool fast and the text crisp on any display.

- **Display (24px/700):** Large score numbers in the scorecard panel only.
- **Heading (16px/600):** Page-level section titles (e.g. "My account", "Tune your map").
- **Subheading (14px/600):** Card titles, listing addresses, sidebar section headers.
- **Body (14px/400):** All prose — descriptions, filter labels, detail rows.
- **Label (12px/500):** Badges, tab labels, form hints, neighborhood names.
- **Caption (12px/400):** Timestamps, source attribution, fine print.
- **Micro (10px/500, tracked):** Badge text inside status pills and score ratings. Always uppercase with `letter-spacing: 0.04em`.
- **Price (14px/700):** Listing prices and per-m² values. Bold to make numbers scannable in a dense list.

Line-clamp (`line-clamp-2`) on listing titles prevents overflow without truncation dots. `truncate` on addresses and neighborhood names that may be long. Never wrap numbers.

## Layout

The app uses a fixed three-region layout: a full-width PersonaBar strip at the top, a fixed 320px sidebar on the left, and the Leaflet map filling the remaining viewport.

- **PersonaBar height:** ~32px (`py-1.5`). Compact — it must not push the map below the fold.
- **Sidebar width:** 320px (`w-80`), fixed. On mobile this overlaps the map (known limitation — not currently responsive).
- **Card padding:** 12px (`p-3`) for listing cards in the sidebar list. 16px (`p-4`) for detail panels and filter sections.
- **List gap:** 8px (`gap-2`) between listing cards.
- **Section gap:** 12px (`gap-3`) between grouped controls; 16px (`gap-4`) between major sections.
- **Inline element gap:** 4–6px (`gap-1`, `gap-1.5`) between badges, icons, and inline text.
- **Grid layout:** Detail grids use 2 columns with `gap-x-4 gap-y-2`.

All panels use `flex flex-col h-full` with `overflow-hidden` at the container and `overflow-y-auto` on the scrollable region. Never use fixed pixel heights for scroll areas — always let flex distribute the space.

## Elevation & Depth

Depth is expressed through borders, not shadows. The visual layer stack is:

1. **Page background** — `gray-50`
2. **Panel / sidebar surface** — `white` with `border-r border-gray-200`
3. **Card surface** — `white` with `border border-gray-200 rounded-lg`
4. **Hover state** — `border-blue-300 shadow-sm` (the only default shadow)
5. **Active / selected** — `border-blue-400 border-2` or `bg-blue-50`
6. **Overlay / dropdown** — `shadow-md` (e.g. the sidebar collapse toggle)

Never use `shadow-lg` or above in standard UI. Reserve stronger shadows only for modals or toasts if those are ever introduced.

## Shapes

- **Cards:** `rounded-lg` (8px) — the standard container shape for listing cards, detail panels, and grouped sections.
- **Buttons:** `rounded-lg` (8px) for filled primary buttons; `rounded` (4px) for compact action buttons.
- **Pills / badges:** `rounded-full` (9999px) for status indicators, filter chips, and score grades.
- **Inputs:** `rounded` (4px) or `rounded-md` (6px) for text inputs and selects.
- **Collapse toggle:** `rounded-r-md` (right side only) — the sidebar toggle protruding from the left edge.

Avoid mixing radii within the same component family. Cards are always `rounded-lg`; pills are always `rounded-full`.

## Components

### Listing Card
Default: white background, `border border-gray-200 rounded-lg p-3`. Title in `text-sm font-semibold text-gray-900`, price in `text-sm font-bold text-blue-700`, metadata in `text-xs text-gray-500`. Three states:
- **Default:** `border-gray-100 hover:border-blue-300 hover:shadow-sm`
- **Liked:** `bg-green-50/50 border-green-200`
- **Disliked:** `bg-gray-50 border-gray-200 opacity-70`

Never use colored backgrounds on the default state. Hover shows the blue border, not a background change.

### Buttons
- **Primary (filled):** `bg-blue-600 text-white text-sm font-medium px-4 py-2 rounded-lg hover:bg-blue-700`
- **Secondary (outline):** `border border-gray-300 text-gray-700 text-sm px-2 py-1 rounded hover:bg-gray-50`
- **Ghost (text link):** `text-blue-700 hover:underline text-xs` — used in bars and headers

### Score Grade Badges
Four tiers, always rendered as solid filled pills with white text (`text-[10px] font-medium uppercase px-1.5 py-0.5 rounded`):
- A → `bg-emerald-600`
- B → `bg-green-600`
- C → `bg-amber-500`
- D → `bg-red-500`

### Filter / Match Chips
Active filter: `bg-blue-50 text-blue-700 border border-blue-200 rounded-full px-2 py-0.5 text-xs inline-flex items-center gap-1`. Include an `×` dismiss button. Match badges use `bg-emerald-50 text-emerald-700 border-emerald-200`.

### PersonaBar
Slim strip: `bg-blue-50 border-b border-blue-100 px-4 py-1.5 text-xs flex items-center gap-3`. Persona label is `font-semibold text-blue-700`. Preference chips are white pills: `bg-white border border-blue-200 text-blue-800 text-[11px] rounded-full px-2 py-0.5`. Action links are `text-blue-700 hover:underline`. Pipe dividers are `text-gray-400`.

### Tabs
Active tab: `border-b-2 border-blue-600 text-blue-700 font-medium`. Inactive: `border-b-2 border-transparent text-gray-500 hover:text-gray-800 hover:border-gray-300`. Padding: `px-5 py-2.5`. Tabs sit on a bottom border row — the active border-b-2 overlaps the row border to form a seamless underline.

## Do's and Don'ts

**Do:**
- Use `border-gray-200` to separate regions — avoid full-bleed background blocks for separation.
- Keep all numeric values (prices, scores, m²) bold. They are the product.
- Use `rounded-full` consistently for all badge/pill shapes — never `rounded-md` on a pill.
- Apply semantic colors purposefully: green = good signal, amber = caution, red = bad signal or dismiss action.
- Default new panels and drawers to `bg-white` with a `border` — do not assume gray backgrounds.

**Don't:**
- Don't add new colors outside the established palette for decorative purposes.
- Don't use `shadow-lg` or above in standard UI — it signals a modal context which this app doesn't use yet.
- Don't use font sizes above `text-base` (16px) except for the scorecard display number.
- Don't introduce a web font — system-ui is intentional for performance and native rendering.
- Don't use `bg-blue-600` as a background for anything other than primary CTA buttons.
