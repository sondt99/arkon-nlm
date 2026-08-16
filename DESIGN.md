# Arkon — Lab Terminal

## North Star: "Secure Knowledge Grid"
A calm, editorial knowledge base with the quiet confidence of a security console.
Emerald-green product accent, serif headings for authority, and a faint technical
grid that reads as "on-premise, internal, trustworthy" — never noisy or neon.

The single source of truth for all values is `frontend/src/app/globals.css`
(`:root` for light, `.dark` for dark). This file is the intent; the CSS wins.

## Colors
Emerald green is the product accent; the surfaces are cool, warm-neutral greens
(never pure white or pure black).

- **Primary — emerald** (`#087f5b` light / `#21e68a` dark): CTAs, active nav, focus, links.
- **Background** (`#f2f7f4` light / `#050c0a` dark): cool mint in light, near-black green in dark.
- **Card / surface** (`#fbfdfc` / `#0a1511`): slightly lifted from the background.
- **Ring** (`#0aa574` / `#21e68a`): focus outline; every interactive element must show it.
- **Destructive** (`#d13b4a` / `#ff6375`): errors only.

### Data-classification tones
Scope/type badges use a **separate**, opaque, same-hue palette (blue, violet, amber,
teal, pink, cyan, slate) defined as `--tone-*` tokens — deliberately distinct from the
green product accent so classification never reads as a call to action. Charts use a
green/teal-anchored categorical set (`--chart-1..5`) that harmonizes with the primary.

## Typography
- **Headings (h1–h4):** EB Garamond — editorial serif, large with tight leading.
- **Body/labels:** Manrope — geometric sans-serif.
- **Mono accents:** eyebrow labels, badges, the corner watermark — uppercase,
  wide letter-spacing (`0.18–0.2em`), tiny (9–11px), in primary green.
- The serif/sans/mono mix is the signature: authority + clarity + a technical undertone.

## Surface & elevation
- Cards: `bg-card` at ~92% opacity with `backdrop-blur`, `ring-1 ring-foreground/10`,
  rounded-xl. Hover lifts a soft shadow + a faint primary ring.
- `.shadow-sahara` is the standard elevated-panel shadow (kept for name compatibility).
- The `body` carries a fixed 32px technical grid plus a soft primary glow top-right;
  the `app-content` shows a `ARKON // SECURE KNOWLEDGE GRID` mono watermark bottom-right.

## Components
- **Buttons:** primary = solid emerald; outline/secondary/ghost per `ui/button.tsx`;
  `link` = underline on hover. Focus shows a 3px ring; hover adds a subtle emerald glow.
- **Cards:** use the `ui/card.tsx` primitive; don't hand-roll card styling.
- **Inputs:** translucent background, token border, emerald focus ring.
- **Loading:** the `Skeleton` primitive (pulse + shimmer sweep). Never spin raw
  Material Symbols ligature text — the global CSS converts `.animate-spin` icons
  into a font-independent spinner.
- **Badges:** `scope-badge` / `wiki-type-badge` / `tone-badge` map to `--tone-*`.

## Rules
- **Tokens only.** Never hardcode a hex in a component — reference a CSS variable so
  light/dark and future retheming stay coherent. (Charts, scrollbars, skeletons all
  derive from tokens.)
- **Both themes always.** Every change must be checked in light and dark; anything
  defined only for one theme is a bug.
- **Accessibility is not optional:** visible focus on every interactive element,
  `aria-label` on icon-only buttons (mark the icon `aria-hidden`), body text at
  WCAG AA contrast (≥4.5:1), and honor `prefers-reduced-motion` (already global).
- **Restraint.** The grid, glow, and watermark are deliberately faint. Keep the accent
  for what's actionable; let content and whitespace carry the page.
