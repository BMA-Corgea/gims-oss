# Watery — GIMS house style

The GIMS front-end design system. **Watery** = warm light glowing down through cool
teal/green water — painterly, biophilic, calm. The deep-water (dark) reading. This is
GIMS's house style; it is **not** the generic "Nocturne" default.

Source of truth: `static/styles/watery.css` (`:root` tokens + base components + motion).
Recolor a `:root` variable, never 40 hex codes. Icons live in `static/icons.svg`
(SVG `<symbol>` sprite, referenced with `<use>`); never emoji as UI icons.

## Mood
Deep teal-green water base; warm amber/gold "sun-shaft" light filtering from above;
luminous aqua/seafoam as the interactive accent; bioluminescent green for
verified/success. Soft, organic, premium — calm under data density.

## Palette (tokens)
- **Surfaces (deep water):** `--bg #06140f` → `--bg2 #0a1f1a` → `--surface #0e2a23` → `--surface2 #143a30`.
- **Cards (green-tinted):** `--card #11362a` → `--card-2 #173f2f`. Cards read **green** — lighter and
  greener than the near-black base, so panels stand off the floor. Framed with a **thick (2px) tan edge**
  `--card-edge rgba(216,189,138,.55)` (`--card-edge-strong` on hover) — warm sun-shaft framing on cool water.
  `--card-border rgba(99,224,193,.24)` stays the subtle cyan edge for *small* icon chips only.
- **Borders:** brighter than v1 — `--border rgba(140,230,200,.14)`, `--border2 rgba(140,230,200,.30)`
  (hairlines); the visible card frame is the **tan** `--card-edge`, not a border token.
- **Text:** warm off-white `--text #e8f4ee` → `--text-mid #a6cabd` → `--text-soft #6f988b` → `--text-mute #436055`.
- **Accent (aqua/seafoam):** `--accent #2dd4bf` → `--accent-2 #6ee7c7`; `--accent-glow rgba(45,212,191,.32)`.
  Aqua is the **accent** colour — borders, focus rings, icons, hover glows.
- **Action blue (the only blue):** `--blue #4f9dff` → `--blue-2 #74b9ff`, deep `--blue-deep #2f7fe6`,
  `--blue-glow rgba(79,157,255,.36)`, `--blue-text #bcd9ff`. Blue is the **action** colour —
  the primary CTA (`.btn-primary`) and workspace buttons. House rule: **blue buttons on green cards**, cyan edges.
- **Warm light (sun shafts):** `--warm #f4c987`, `--warm-2 #f0a868`, `--warm-glow rgba(244,201,135,.16)`.
- **Ambient blobs (tokenized):** `--glow-aqua`, `--glow-floor` (+ `--warm-glow`) drive the page's radial-gradient light.
- **Verified (bioluminescent green):** `--green #34e0a1`, `--green-text #6ef0bf`, `--green-glow rgba(52,224,161,.40)`.
- **Warn** amber `--amber #f6c560`; **Danger** coral-red `--red #f0726a`.

> **Colour roles (v2):** *green* = surface/card, *tan* = card frame, *cyan/aqua* = icon-chip edge & accent,
> *blue* = action. Keep these distinct — don't tint a card blue or make a button cyan.

## Shape & light
- Glassy rounded panels, radius **18 / 13 / 9** (lg/base/sm) — a touch larger/softer than Nocturne for the organic feel.
- "Light" = layered radial gradients: warm sun shaft top, aqua mid-glow, deep-green floor — they drift slowly (`drift`, ~22s).
- Soft, water-deep shadows tinted with `rgba(2,18,14,…)`.

## Motion — "Gentle Watery" (calm, not busy)
- `ripple` — slow caustic sweep on the primary CTA (hover), softer/slower than Nocturne's shimmer.
- `drift` — very slow position drift on the background light blobs.
- `pulse-dot` — bioluminescent live indicator.
- `rise` (panels), `fade`+`pop` (modals), `slide-in` (toasts). Hover-lift `translateY(-1/-2px)`. Transitions .14–.22s.

## Components (in `watery.css`)

**Formal surface layer — theme once, reuse everywhere.** To avoid hand-colouring each
section, every boxed surface uses these shared primitives (recolour the token, not the page):
- `.panel` — the canonical framed card: green-water body + **tan 2px frame** + soft shadow + `rise`.
  Add `.interactive` for the hover lift (border → `--card-edge-strong`). `.panel-head`/`.panel-body` for structure.
- `.w-pop` — a floating framed surface (dropdown menus / popovers): same frame, `shadow-lg`, `pop` entrance.
- `.icon-chip` — an icon tile: cyan accent by default, `.blue` for action contexts, `.round` for avatars.
  Consumer sets the size (width/height + inner `.icon`); colour/edge live in the class.
- `.count-pill` — a small cyan tally badge.

Plus: fields (`.field`/`.input`/`.select`), buttons (`.btn`, `.btn-primary` **blue**-gradient with ripple,
`.btn.ghost`/`.sm`/`.green`/`.blue`), chips/pills (`.chip` + `.ok/.accent/.warn`, `.pill-live`),
toggle switch, modal/overlay, toasts, themed scrollbars. Type: **Inter** (300–700).

## Scope / rollout
1. ✅ Shared system (`watery.css`) + icon sprite (`icons.svg`).
2. ✅ Flagship: **launcher** (`gui/components/launcher.html` + `static/styles/launcher.css`).
   **v2 (2026-06-27):** app-shell with a sticky **section rail** + **collapsible** green section
   panels (state persisted in `localStorage`), **blue** workspace buttons on green cards with bright
   cyan edges, the **gnome as a header mascot** (no longer floating in the corner), and a
   **first-class login** experience — a Watery sign-in card when logged out, a header **profile chip**
   when logged in (driven by the login node's `/login/inject.js`, which toggles `body.is-anon/.is-authed`
   and dispatches `gims:authapplied`). Preserves the JS contract: `.launcher-button`, `data-tooltip`,
   `data-tag`, `#tooltip`, and `window.GIMS.launcher`.
3. ⏭ Fan out to the other 21 `gui/components/*.html` pages (each imports `watery.css`,
   drops its bespoke palette, keeps page-specific layout).
