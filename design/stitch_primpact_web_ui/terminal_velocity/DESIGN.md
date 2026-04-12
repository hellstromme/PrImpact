# Design System Strategy: The Kinetic Terminal

## 1. Overview & Creative North Star
The Creative North Star for this system is **"The Kinetic Terminal."** 

This design system moves beyond the "standard dashboard" by treating the browser as a high-performance IDE. It bridges the gap between raw command-line efficiency and high-end editorial clarity. We reject the "flat" web; instead, we embrace a layered, immersive environment where data feels "etched" into the interface. By utilizing intentional asymmetry—such as right-aligned technical metadata contrasting with left-aligned prose—we create a rhythmic flow that guides a developer's eye through complex impact analysis without cognitive overload.

## 2. Colors & Tonal Architecture
The palette is rooted in a "Deep Dark" philosophy, prioritizing ocular comfort during long debugging sessions while using high-chroma accents to signal urgency.

*   **The "No-Line" Rule:** Sectioning must never rely on 1px solid borders. To separate a code diff from a report summary, use a background shift from `surface` (#10141a) to `surface_container_low` (#181c22). Boundaries are felt through value shifts, not drawn with lines.
*   **Surface Hierarchy & Nesting:** Depth is a functional tool. 
    *   **Level 0 (Base):** `surface` for the primary application background.
    *   **Level 1 (Sections):** `surface_container` for the main content areas.
    *   **Level 2 (Active States/Cards):** `surface_container_high` for hovering or focused report modules.
*   **The "Glass & Gradient" Rule:** Floating overlays (like impact tooltips) should utilize `surface_container_highest` with a `backdrop-blur` of 12px and 80% opacity. For primary actions, use a subtle linear gradient from `primary` (#6fdd78) to `primary_container` (#34a547) to give the button a "machined" metallic finish.
*   **Signature Textures:** Apply a 2% opacity noise texture over `surface_container_lowest` to simulate the phosphor grain of a high-end terminal, adding "soul" to the dark space.

## 3. Typography: The Editorial Monospace
The system utilizes a dual-axis typographic approach to separate "Human Intent" (Inter) from "Machine Output" (JetBrains Mono/Fira Code).

*   **Display & Headline (Space Grotesk):** Used for high-level impact scores. The wide aperture of Space Grotesk provides a technical, futuristic feel that demands attention without being decorative.
*   **Titles & Body (Inter):** The workhorse for UI labels and explanatory text. Inter’s tall x-height ensures readability at small scales (`label-sm`: 0.6875rem) when viewing dense dependency trees.
*   **The Technical Monospace:** All SHAs, file paths, and code snippets must use monospace. This isn't just for code; it's a semantic signal that the information is "System-Generated Data."

## 4. Elevation & Depth: Tonal Layering
We do not use shadows to mimic "paper." We use light and tone to mimic "hardware."

*   **The Layering Principle:** To lift a "Critical Impact" card, do not add a drop shadow. Instead, transition the card background to `surface_container_highest`. This "glow from within" feels more native to a backlit screen.
*   **Ambient Shadows:** For modal dialogues only, use a highly diffused shadow: `box-shadow: 0 20px 40px rgba(10, 14, 20, 0.5)`. The shadow color is derived from `surface_container_lowest` to ensure it looks like a natural occlusion of light.
*   **The "Ghost Border" Fallback:** If high-density data requires a container (e.g., a multi-column diff), use a "Ghost Border": `outline-variant` (#3e4a3d) at 15% opacity. It should be felt, not seen.
*   **Glassmorphism:** Use semi-transparent `secondary_container` tones for "Warning" states to allow the deep background to bleed through, creating a "Warning Light" effect rather than a flat amber box.

## 5. Components

### Buttons
*   **Primary:** Gradient of `primary` to `primary_container`. Text in `on_primary_fixed` (Deep Green). 0.25rem (DEFAULT) radius.
*   **Tertiary:** No background. Text in `primary`. On hover, a subtle `surface_container_high` background appears.

### Status Chips
*   **Clean:** `primary_container` background with `on_primary_fixed` text.
*   **Blocker:** `tertiary_container` (Red-tinted) with a 1px "Ghost Border" of `tertiary`.
*   **Styling:** Chips are strictly rectangular (`sm` radius) to maintain the "Hacker Sleek" aesthetic.

### Cards & Impact Lists
*   **The Rule of Zero Dividers:** Never use horizontal rules. Separate report line items using 12px of vertical space and a alternating subtle shift between `surface` and `surface_container_low`.
*   **Data Density:** Use `label-md` for metadata (last commit, author) to keep the footprint small, allowing more "Code Impact" data to fit above the fold.

### Input Fields
*   **Style:** Minimalist. Underline only or a `surface_container_highest` fill.
*   **Focus:** A 1px `primary` glow. No offset.

### Signature Component: The "Impact Sparkline"
A custom micro-chart component placed within list items. It uses a `secondary` (Amber) to `tertiary` (Red) gradient to visualize the "blast radius" of a code change across the codebase.

## 6. Do's and Don'ts

### Do:
*   **Do** use `monospace` for any string of text that is non-prose (versions, sizes, timestamps).
*   **Do** leverage `surface_bright` for hover states on dark backgrounds to create a "backlit" effect.
*   **Do** use asymmetrical layouts—e.g., a wide left column for code and a narrow right column for "Impact Metadata."

### Don't:
*   **Don't** use pure white (#FFFFFF). All "white" text should be `on_surface` (#dfe2eb) to prevent eye strain.
*   **Don't** use rounded corners larger than `md` (0.375rem). This system is precision-engineered; overly rounded corners feel too "consumer-soft."
*   **Don't** use standard "Drop Shadows." If an element needs to pop, use a tonal shift or a "Ghost Border."