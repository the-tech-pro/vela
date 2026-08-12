# Vela Design System

**Document status:** Current-state baseline and overhaul rules

**Last updated:** 2026-08-11

**Implementation reference:** `antra-wails/frontend/src/style.css` and `antra-wails/frontend/src/App.svelte`

## 1. Design intent

Vela is a focused desktop music-library application inspired by Apple Music's hierarchy and Apple platform conventions without copying Apple assets or presenting itself as an Apple product. It should feel calm, native, artwork-led, and precise under heavy workloads.

The approved interface uses one visual identity with light and dark appearances:

- Dense information presented without looking like a terminal log.
- Album artwork as the dominant content imagery.
- One restrained music-red accent used for selection, progress, focus, and primary actions.
- Explicit visibility of source, quality, progress, and failure state.
- Neutral layered surfaces that adapt coherently between light and dark appearance.

## 2. Design principles

1. **Outcome first.** The current release, queue status, or next required action should be the strongest element.
2. **Progress must be trustworthy.** Distinguish queued, resolving, downloading, processing, complete, skipped, cancelled, and failed states.
3. **Artwork supports orientation.** Use covers to identify releases; never let decorative imagery reduce legibility.
4. **Advanced without intimidation.** Keep common actions direct and place credentials, provider tuning, and naming rules in progressive disclosure.
5. **Theme-safe semantics.** Components consume semantic tokens; themes define those tokens.
6. **Local-app confidence.** Make file destinations, saved state, account connections, and destructive actions explicit.
7. **Accessible by construction.** Keyboard behavior, focus, contrast, labels, and reduced motion are component requirements.

## 3. Foundations

### 3.1 Typography

Default UI typography follows the operating-system sans-serif stack; monospaced text is reserved for paths, codecs, bitrates, tokens, logs, and time readouts:

```css
--font-ui: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', sans-serif;
--font-mono: 'SFMono-Regular', 'Fira Code', 'JetBrains Mono', Consolas, monospace;
font-size: 14px;
line-height: 1.5;
```

The repository bundles a Fira Code font asset. Preserve monospaced text for technical values, paths, codecs, bitrates, tokens, logs, and time readouts.

For the overhaul, introduce a UI sans-serif token while retaining mono for technical content:

```css
--font-ui: Inter, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif;
--font-mono: 'Fira Code', 'JetBrains Mono', Consolas, monospace;
```

Recommended scale:

| Token | Size/line height | Use |
|---|---|---|
| `--text-xs` | 11px/16px | Metadata, helper copy, badges |
| `--text-sm` | 12px/18px | Compact controls and table rows |
| `--text-md` | 14px/21px | Default body and form controls |
| `--text-lg` | 16px/24px | Section headings and release titles |
| `--text-xl` | 20px/28px | Modal/page titles |
| `--text-2xl` | 28px/34px | Primary content title when space permits |

Do not rely on font size below 11px. Use 400 for body, 500–600 for labels/actions, and 650–700 for headings.

### 3.2 Color tokens

Every theme currently defines this semantic set:

| Token | Purpose |
|---|---|
| `--bg-color` | Window background |
| `--bg-elevated` | Elevated panel/card background |
| `--bg-overlay` | Modal and popover backdrop/surface support |
| `--fg-color` | General foreground text |
| `--text-primary` | Highest-emphasis text |
| `--text-secondary` | Supporting text |
| `--text-muted` | Low-emphasis metadata |
| `--text-faint` | Disabled/de-emphasized details |
| `--accent-color` | Primary actions, selection, progress, focus |
| `--accent-strong` | Hover/strong accent |
| `--accent-soft` | Subtle accent surface |
| `--accent-border` | Accent outline |
| `--surface-color` | Default translucent surface |
| `--surface-light` | Hover or elevated surface |
| `--surface-strong` | Selected/strong surface |
| `--surface-accent` | Accent-tinted surface |
| `--border-color` | Quiet divider/border |
| `--border-strong` | Form and emphasized border |
| `--success-color` | Completed, connected, healthy |
| `--warning-color` | Degraded, partial, attention |
| `--error-color` | Failed, unavailable, destructive |

New components must use these semantics. Do not add literal colors in Svelte markup. If a new semantic need arises, add a token across every theme first.

Add explicit tokens during the overhaul for:

```css
--focus-ring: color-mix(in srgb, var(--accent-color) 70%, white);
--disabled-opacity: 0.45;
--overlay-scrim: rgba(0, 0, 0, 0.62);
--shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.18);
--shadow-md: 0 12px 36px rgba(0, 0, 0, 0.28);
```

### 3.3 Appearance

There is one Vela theme with three user preferences: System, Light, and Dark. System follows `prefers-color-scheme`; Light and Dark override it. The preference persists locally.

Light appearance uses warm-neutral white content, a slightly cooler translucent sidebar, dark primary text, soft grey separators, and `#fa2d55` as the accent. Dark appearance uses near-black content, a lifted charcoal sidebar, off-white primary text, quiet separators, and `#ff375f` as the accent.

Do not introduce provider-branded themes. Provider identity belongs in small logos, labels, and metadata only.

### 3.4 Spacing

Use a 4px base unit:

| Token | Value |
|---|---|
| `--space-1` | 4px |
| `--space-2` | 8px |
| `--space-3` | 12px |
| `--space-4` | 16px |
| `--space-5` | 20px |
| `--space-6` | 24px |
| `--space-8` | 32px |
| `--space-10` | 40px |
| `--space-12` | 48px |

Prefer 8px gaps within a control group, 12–16px within a card, 20–24px between sections, and 24–32px around primary page content.

### 3.5 Radius and borders

The current global radius is 8px, while many basic controls use 4px. Standardize on:

| Token | Value | Use |
|---|---|---|
| `--radius-sm` | 4px | Badges, compact controls |
| `--radius-md` | 8px | Inputs, buttons, cards |
| `--radius-lg` | 12px | Panels and modals |
| `--radius-pill` | 999px | Chips, segmented controls |

Default borders are 1px. Use stronger borders for focus, selection, or invalid state—not decoration.

### 3.6 Iconography and imagery

- Prefer one consistent icon set during the overhaul; the current UI mixes emoji, text glyphs, and image assets.
- Icon-only buttons require an accessible label and tooltip.
- Standard icon sizes: 16px in controls, 20px in navigation, 24px for feature actions.
- Album/release artwork uses 1:1 aspect ratio, `object-fit: cover`, and a neutral placeholder.
- Provider logos may identify a service but must not replace a written label in settings or error states.

## 4. Layout

### 4.1 Window shell

The window is a full-height split-view shell inspired by Apple desktop applications:

- Sidebar: 228–252px, persistent on desktop, containing grouped navigation. Appearance belongs in Settings, not the sidebar.
- Toolbar: contextual title, search or page actions, visually part of the content column.
- Main content: one artwork-led, task-focused page at a time.
- Optional bottom player: persistent only while a local track is loaded.
- Queue: compact floating surface anchored to the lower-right; expands upward/left without replacing the current page.
- Overlays: reserved for short selection, confirmation, and inspector workflows rather than primary navigation.

Avoid stacking multiple modals. Close or replace the current modal before opening another, except for native file/directory dialogs.

### 4.2 Content width

- Primary forms and tracklists: fluid width with a practical max around 1120–1280px.
- Reading/settings column: 680–840px.
- Compact confirmation dialog: 420–520px.
- Large browser overlay: up to 1200px or 92vw.

### 4.3 Responsive behavior

Vela is desktop-first but must tolerate narrow windows:

- At widths below 900px, move secondary header actions into an overflow menu.
- At widths below 720px, convert side-by-side fields/cards to one column.
- Track rows may hide tertiary metadata but must preserve title, artist, state, and primary action.
- Modals must stay within `92vw × 88vh` and keep their header/actions visible while the body scrolls.
- Do not rely on hover for essential information.

## 5. Core components

### 5.1 Buttons

Variants:

- **Primary:** accent-filled; one principal action per surface.
- **Secondary:** accent-soft background and accent border.
- **Ghost:** transparent/quiet for low-priority toolbar actions.
- **Destructive:** error color, used only for clear/remove/cancel actions with material impact.
- **Icon:** square target with tooltip and accessible name.

Minimum target size is 36×36px; prefer 40×40px for icon-only actions. Disabled buttons must remain legible, use `aria-disabled`/`disabled`, and not respond to hover.

### 5.2 Inputs and selects

- Labels sit above fields; placeholders never replace labels.
- Helper and error text sit below the field.
- Password/token values remain masked with an explicit reveal action when needed.
- Focus uses both a border change and visible focus ring.
- Invalid state uses error border, icon, and text—not color alone.
- Long paths and tokens use the mono font and support copying.

### 5.3 Tabs and segmented controls

Primary navigation includes Library, Artists, Discover, Downloaded, Downloads, dynamic connected-iPod destinations, and Settings. Add Music is a short overlay launched from Downloads; History is integrated into Downloads. Service/source selectors use compact segmented controls or chips.

- Selected state uses accent surface, stronger text, and `aria-current` or `aria-selected`.
- Keyboard arrow navigation is required for true tablists.
- Avoid encoding selected state only through opacity.

### 5.4 Cards

Release, playlist, artist, and theme cards share:

- Artwork/preview region.
- Primary title of at most two visible lines.
- Secondary metadata.
- Explicit action or whole-card behavior, not both ambiguously.
- Hover and keyboard-focus parity.

### 5.5 Chips and badges

Use chips for filters and selectable sources. Use badges for passive state such as codec, quality, explicit, source, healthy, or failed.

State mapping:

| State | Semantic color |
|---|---|
| Complete, connected, healthy | Success |
| Partial, retrying, rate-limited | Warning |
| Failed, invalid, offline | Error |
| Active selection or progress | Accent |
| Queued, skipped, unavailable option | Muted/neutral |

### 5.6 Track rows and progress

A track row should reserve stable areas for:

1. Track/disc position.
2. Title and artist.
3. Duration and quality/source metadata.
4. Status/progress.
5. Contextual action.

Do not move row content as status changes. Determinate progress uses a bar and percentage when credible; indeterminate resolving/tagging states use a labeled activity indicator. “Processing” must not show fabricated percentages.

### 5.7 Modals and overlays

- Use a clear title and optional one-line purpose.
- Keep close action in a consistent top-right location.
- Trap focus, close on Escape when safe, and restore focus to the invoker.
- Clicking the scrim may close informational overlays, but not flows with unsaved credentials or destructive consequences.
- Persistent actions belong in a footer; long bodies scroll independently.

### 5.8 Toasts and banners

Use toasts for transient results that require no decision. Use inline banners for actionable errors and persistent degraded states.

- Success toast: 3–5 seconds.
- Error toast: remains long enough to read and offers a log/details action when useful.
- Never place credentials or raw server responses in a toast.
- Donation/support messages must not masquerade as errors or imply gated functionality in the paid build.

### 5.9 Empty, loading, and error states

Every asynchronous surface needs:

- Skeleton or labeled loading state.
- Empty-state explanation and next action.
- Error summary in plain language.
- Retry when the operation is safe to repeat.
- Details/log path for technical diagnostics.

## 6. Feature patterns

### 6.1 Link download

Downloads is the activity surface. Its Add custom action opens a compact link-and-destination overlay. Once metadata arrives, each requested song, album, or playlist is represented as a job. Source and format choices remain secondary settings. Cancellation remains available throughout active work.

### 6.2 Failed downloads

Failures live in a dedicated, collapsible section after the active tracklist. Each entry contains the failure reason, Retry, and Dismiss. Retry All is prominent only when more than one retryable item exists.

### 6.3 Connected accounts

Account cards use four states: Not connected, Connecting, Connected, and Needs attention. Show the account/service name and expiry/reconnect guidance without exposing secrets.

### 6.4 Settings

Use a centered overlay with a persistent category rail and one visible page at a time. Valid changes apply automatically on change or field blur; invalid values show an inline error and are not persisted. Do not show a Save Changes button. Categories are:

- General and library location.
- Output quality and matching.
- Folder and filename rules.
- Sources and accounts.
- Auto-sync.
- Appearance.
- Advanced/diagnostics.

The paid-only build must not contain a Supporter Key section or supporter status. Apple Music is the only connected-library account. Provider credentials required by the retained downloader remain under an Advanced Sources section and must not be confused with library sync.

### 6.5 Player and lyrics

The player preserves title, artist, release context, playback controls, seek, time, volume, and queue position. Synced lyrics highlight the active line but avoid continuous animation when reduced motion is requested.

## 7. Content style

- Use sentence case: “Add to library,” not “Add To Library.”
- Prefer direct verbs: Add, Retry, Connect, Choose folder, Save, Cancel.
- Say what happened and what the user can do next.
- Name retained services consistently: Spotify, Apple Music, Amazon Music, TIDAL, Qobuz, and Deezer. Do not mention Soulseek or P2P in production UI.
- Avoid “unlimited” unless no technical, account, provider, or fair-use limit exists.
- Avoid “2× faster”; concurrency does not guarantee doubled speed.
- Distinguish “source unavailable,” “account authorization required,” “no confident match,” and “download failed.”
- Never describe a non-empty credential field as validated.

## 8. Accessibility requirements

- Meet WCAG 2.2 AA contrast for text and meaningful component boundaries.
- Provide `:focus-visible` styling with at least a 2px apparent ring.
- Maintain logical DOM and tab order.
- Ensure every form field has an associated label.
- Use semantic buttons, headings, dialogs, tablists, progress bars, and status regions.
- Announce important async changes through restrained `aria-live` regions.
- Do not auto-focus changing track rows during downloads.
- Respect `prefers-reduced-motion` and avoid flashing/pulsing effects.
- Provide non-color labels/icons for success, warning, and error.

## 9. Motion

- Interaction transitions: 120–180ms.
- Modal/toast entry: 180–240ms.
- Use opacity and small transforms; avoid large travel.
- Progress animation may repeat only while work is active.
- Disable decorative motion under `prefers-reduced-motion: reduce`.

## 10. Implementation rules for the overhaul

### Settings and device surfaces

- Settings use a centered, paged overlay above the current page; opening a
  contextual cog selects the relevant settings page. Only the active page may scroll.
- Downloads owns the Add custom link overlay. Audio format and resolver source
  controls live on their settings page.
- Discover uses search as its primary top control. Storefront and genre are
  contextual settings, with United Kingdom as the default storefront.
- iPod device cards use neutral hardware silhouettes and system colors. They
  show identity and storage before exposing any management operation.
- iOpenPod is embedded headlessly. Never expose its GUI, a permanent generic
  iPod tab, or a separate background-process concept. A named device item appears
  in the sidebar only while a device is detected.
- Device writes must be staged in a review sheet that clearly separates
  additions, removals, conversions, and metadata-only changes.

1. New UI code must not add inline `style` attributes except for truly data-driven values such as progress width or artwork URL.
2. Extract reusable Button, IconButton, Field, Modal, Toast, Badge, Tabs, Card, TrackRow, EmptyState, and Progress components.
3. Store design tokens in one stylesheet and theme overrides in a dedicated theme file.
4. Use CSS classes or component variants instead of copying style blocks.
5. Test every component in light and dark appearance before considering it complete.
6. Keep provider logos and third-party-inspired colors optional; core usability must survive without them.
7. Remove supporter-only UI and copy during the paid-only conversion. Do not replace it with a misleading “paid” badge.
8. Validate layouts at 1280×720, 1440×900, 1920×1080, and a narrow 720px-wide window.

## 11. Known current-state design debt

- `App.svelte` is a very large component containing most product surfaces and state.
- Many components are expressed as inline styles, making theme and accessibility changes difficult.
- The legacy interface mixes emoji, text glyphs, provider images, and custom visual marks.
- Some selected states depend heavily on opacity or color.
- Focus trapping/restoration and narrow-window behavior need a systematic audit.
- The settings UI currently shows “Supporter” whenever the key field is non-empty, even without validation.
- The legacy multi-theme system must be removed rather than migrated.
- Several technical controls and credentials compete visually with common settings.

These are baseline observations, not authorization for an unbounded redesign. Each overhaul slice should cite this document and the relevant PRD requirements.

## 12. Library and downloads interaction patterns

- Library is a hierarchical sidebar group. Nested destinations use one icon
  family, 36px rows, a 16px indent, and no competing top-level Artists or
  Discover destination.
- Search is 38px high. Sort and filter are separate 38×38 icon buttons with the
  same radius, surface, border, and vertical alignment. Their popovers always
  use the active theme surface and text tokens.
- The multi-selection toolbar is centered within the main content panel rather
  than the window or sidebar grid. Its boundary is neutral; accent color is
  reserved for the selected card and controls.
- Selection circles are 24×24 with the icon optically centered. Context menus
  use the stateful verbs Select and Deselect.
- The compact download controller is the only floating queue surface. Its
  artwork is 42×42; its pause/resume and cancel controls are circular; its
  overall progress line forms the bottom edge.
- The current-track list belongs to Downloads, is capped at roughly 42vh, and
  scrolls independently. Completed progress bars disappear. Active rows use a
  rotating circular progress icon; waiting rows use a clock; completed rows use
  a tick.
- Clean logs are collapsed by default behind Show more and use a subdued mono
  surface. Raw terminal framing and noisy provider bootstrap output are not
  shown.

## 13. Full-panel library details and perceived performance

- Library and Activity are quiet uppercase section labels. Section labels are
  never accent-filled and Library is not collapsible.
- Album, playlist, favourites, and artist detail use the complete content panel,
  with a 230px artwork hero, a restrained blurred artwork wash, compact local
  controls, and dense 58px track rows. Only navigated releases show a back
  arrow; the dedicated Favourites destination does not.
- Close controls always use the Lucide X glyph centered in a circular button.
  Text-plus-icon buttons use inline-flex alignment and explicit line height.
- Previously indexed content remains visible during refresh. Loading surfaces
  are reserved for a true first index; background reconciliation must not blank
  or block a populated page.
- Startup indexing is background work and must never capture pointer input or
  delay navigation. Stage competing indexers, keep event payloads bounded, and
  show a determinate percentage whenever the backend knows total work.
- A full-index progress indicator disappears only after every current release
  has a successful local checkpoint. Matching completed indexes stay quiet on
  later launches; incomplete indexes keep an explicit remaining-work state.
- Index percentage represents completed release checkpoints plus completed
  songs and may use one decimal place near completion. It must advance as
  large playlist pages finish rather than parking at a synthetic 99%.
- The standalone Playlists grid omits the redundant workspace title bar. A
  playlist detail page retains its title/back navigation header.
- Downloaded releases use the same full-panel hero and track-list hierarchy as
  connected-library releases. Do not split the page into simultaneous grid and
  detail columns.
- Long track lists avoid per-row backdrop filters and use off-screen rendering
  containment. Popovers close when pointer input moves outside their menu or
  trigger.
