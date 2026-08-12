# Vela Product Requirements Document

**Document status:** Baseline inventory and overhaul foundation

**Last updated:** 2026-08-12

**Product owner:** Vela

**Primary implementation:** Wails/Go desktop shell, Svelte/TypeScript UI, Python download engine

## 1. Purpose

Vela is a cross-platform desktop music-library builder. It accepts links and connected-library selections from supported music services, resolves matching audio through a ranked multi-source engine, writes normalized metadata and artwork, and organizes the result into a local library suitable for players and media servers.

This PRD has two jobs:

1. Record the capabilities that exist in the repository today.
2. Establish the approved direction for a paid-only overhaul in which all previously supporter-only application features are standard.

`README.md`, `FEATURES.md`, the Wails bindings, frontend, and Python engine were used as evidence. Where documentation and implementation disagree, the implementation is treated as the current-state source of truth and the discrepancy is called out.

## 2. Product direction

### 2.1 Approved commercial model

The owner has confirmed control of Antra's copyright and licensing rights and authorized removal of supporter-key restrictions.

The next product version will be a paid-only distribution. A customer who legitimately obtains the application receives the complete feature set. There must be no in-app supporter tier, supporter-key field, supporter validation request, supporter quota display, or supporter-only concurrency branch.

This requirement does not define how customers purchase, download, update, or activate a paid build. Those commercial-distribution decisions remain open and must be specified before a replacement licensing or account system is introduced.

### 2.2 Approved product simplification and redesign

The owner has approved the following overhaul scope:

- Rename the user-facing product and desktop executable from Antra to Vela. Preserve legacy internal package and configuration identifiers until a migration is defined.
- Remove the former logo from the application, documentation headers, and desktop window chrome; use a text-only Vela wordmark.
- Replace the existing multi-theme interface with one Vela visual system offering light and dark appearance modes.
- Rebuild the application shell around an Apple Music-inspired desktop sidebar, artwork-led library pages, spacious content hierarchy, and a compact expandable queue anchored in the lower-right corner.
- Make Apple Music the only connected-library and scheduled-sync service. Spotify links may remain valid downloader inputs, but Spotify account/library synchronization and Spotify-tracked playlists are removed.
- Keep Apple credentials on the user's device. They may be sent to Apple's own authenticated endpoints, but never uploaded to an Antra mirror, shared refresh service, analytics endpoint, or other third party.
- Remove Soulseek/slskd and all other P2P functionality, configuration, runtime management, dependencies, UI, and provider registration.
- Remove the Audio Analyzer and Album Availability Studio from the frontend, Wails bindings, and Python command surface.
- Preserve the non-P2P downloader and resolver behavior for now except where required by the credential and UI changes above.

### 2.3 Product goals

- Turn supported service links into a clean, tagged local audio library with minimal setup.
- Prefer exact recordings and the best permitted quality while avoiding incorrect matches.
- Make large album, playlist, and library operations understandable and recoverable.
- Provide one coherent desktop experience for discovery, acquisition, organization, playback, and scheduled synchronization.
- Ship self-contained builds for Windows, macOS, and Linux.
- Make the upcoming overhaul safer by separating UI, entitlement, provider, and engine concerns.

### 2.4 Non-goals

- Hosting or distributing copyrighted audio as part of the Antra repository.
- Guaranteeing availability from third-party services or community endpoints.
- Circumventing third-party subscriptions, access controls, DRM, regional rights, or terms.
- Replacing a full media server or full-featured music player.
- Defining the future storefront, payment processor, or updater in this baseline document.

## 3. Target users

### 3.1 Library builder

Wants albums, playlists, and individual tracks stored locally with consistent metadata, artwork, filenames, and folder structure.

### 3.2 Collection curator

Needs exact-release matching, explicit-version preference, high-resolution selection, duplicate control, multi-disc handling, and configurable naming.

### 3.3 Media-server user

Maintains a Plex, Jellyfin, Navidrome, or similar library and needs predictable folders, tags, cover art, and disc/track ordering.

### 3.4 Audio-quality reviewer

Uses spectrograms and loudness/quality measurements to identify suspicious or transcoded files.

### 3.5 Playlist follower

Wants selected playlists checked on a schedule with only newly added tracks downloaded.

## 4. Current product surfaces

The desktop application currently exposes four primary navigation modes:

- **Library:** the connected Apple Music library and locally downloaded collection.
- **Add Link:** URL input, tracklist preview, queue, progress, logs, failures, and retry.
- **Artist:** artist search and grouped discography selection.
- **Discover:** country- and genre-filtered Apple Music discovery content.

Supporting overlays and panels include Settings, Folder Structure, Downloaded Music, Library Build History, Source Health, artist results, discography selection, download logs, and first-run setup.

## 5. Feature inventory and requirements

Status vocabulary:

- **Implemented:** a user-facing path and implementation are present.
- **Implemented, verify:** implementation exists, but the overhaul must validate behavior and coverage before promising it commercially.
- **Approved change:** explicitly authorized for the paid-only conversion.
- **Future decision:** intentionally unresolved.

### 5.1 First run and application shell

| ID | Requirement | Status |
|---|---|---|
| APP-001 | On first run, prompt the user to choose a local music-library directory. | Implemented |
| APP-002 | Persist application configuration in the platform-appropriate user-data directory. | Implemented |
| APP-003 | Restore the selected theme and configuration on launch. | Implemented |
| APP-004 | Present initialization, empty, loading, success, failure, and offline states without blocking unrelated local functionality. | Implemented, verify |
| APP-005 | Package the desktop application as a self-contained build with its required backend/runtime assets. | Implemented, verify on every target platform |

### 5.2 Link intake and metadata collection

| ID | Requirement | Status |
|---|---|---|
| IN-001 | Accept individual track, album, and playlist URLs where the source supports those resource types. | Implemented |
| IN-002 | Accept multiple URLs in one download session and keep each release visually separated. | Implemented |
| IN-003 | Resolve Spotify, Apple Music, Amazon Music, TIDAL, Qobuz, Deezer, and YouTube Music metadata/link paths. | Implemented, verify per resource type |
| IN-004 | Support Spotify episode and show URLs through the podcast workflow. | Implemented |
| IN-005 | Maintain code paths for additional metadata/fallback services including SoundCloud and SpotFetch where configured. | Implemented, verify user-facing support |
| IN-006 | Load large tracklists progressively and show title, artist, duration, artwork, release type, dates, and counts when available. | Implemented |
| IN-007 | Allow pasting from the system clipboard and starting a download from the populated URL input. | Implemented, verify |

### 5.3 Multi-source resolution

| ID | Requirement | Status |
|---|---|---|
| RES-001 | Resolve candidate audio through enabled adapters rather than assuming the metadata source supplies the audio. | Implemented |
| RES-002 | Rank lossless candidates by quality tier, bit depth, and sample rate. | Implemented |
| RES-003 | Prefer format-appropriate lossy sources for AAC/MP3 and use lossless transcoding only as fallback. | Implemented, verify |
| RES-004 | Use ISRC matching where available and scored title/artist/duration matching otherwise. | Implemented |
| RES-005 | In strict matching mode, apply stronger match and post-download duration thresholds and prefer failure to a risky match. | Implemented |
| RES-006 | Prefer explicit versions when requested and continue searching when a clean/radio-edit candidate conflicts with the request. | Implemented |
| RES-007 | Allow Auto or a selected combination of TIDAL, Qobuz, Apple Music, Amazon Music, and Deezer as the permitted resolver set. | Implemented |
| RES-008 | Remember provider successes/failures and reorder providers only within equivalent quality tiers. | Implemented |
| RES-009 | Back off rate-limited or repeatedly failing providers and continue through viable fallbacks. | Implemented |
| RES-010 | Soulseek/slskd and all P2P adapters must not be registered, packaged, configured, or exposed. | Implemented |

Current non-P2P adapter code includes TIDAL, Qobuz, Apple Music, Amazon Music, Deezer, HiFi/community endpoints, JioSaavn, NetEase, and YouTube-related paths. Availability and legality depend on configuration, user authorization, region, source terms, and endpoint health; presence in code is not a service guarantee.

### 5.4 Quality and output formats

| ID | Requirement | Status |
|---|---|---|
| QLT-001 | Offer Auto/best available, FLAC 24-bit, FLAC 16-bit, ALAC, AAC, and MP3 output choices. | Implemented |
| QLT-002 | Preserve native lossless audio where possible and transcode only when the requested container/codec requires it. | Implemented, verify |
| QLT-003 | For strict Qobuz 24-bit requests, fail clearly instead of labeling 16-bit audio as 24-bit. | Implemented, verify |
| QLT-004 | Surface per-track progress and final source information during a session. | Implemented |

### 5.5 Metadata, tagging, and lyrics

| ID | Requirement | Status |
|---|---|---|
| META-001 | Write title, artist, album artist, album, track/disc numbers, dates, artwork, genre, composer, ISRC, and explicitness when available. | Implemented |
| META-002 | Use container-appropriate tags for FLAC, MP3, and MP4/M4A outputs. | Implemented |
| META-003 | Fetch synced or plain lyrics when enabled, using configured and fallback providers. | Implemented |
| META-004 | Optionally save cover artwork as a sidecar file. | Implemented |
| META-005 | Preserve enough playlist/release context to organize downloads and record useful history. | Implemented |

### 5.6 Library organization and deduplication

| ID | Requirement | Status |
|---|---|---|
| LIB-001 | Organize audio beneath a user-selected library root using configurable folder and filename templates. | Implemented |
| LIB-002 | Support tokens for title, artist, album artist, album, year, track, disc, genre, composer, ISRC, codec, bitrate, and quality. | Implemented |
| LIB-003 | Show a live example for naming templates and provide clickable token insertion. | Implemented |
| LIB-004 | Configure track-number padding, multi-disc numbering, illegal-character replacement, whitespace handling, and filename conflicts. | Implemented |
| LIB-005 | In Smart Dedup mode, identify existing tracks using ISRC, service IDs, and normalized title/artist identity. | Implemented |
| LIB-006 | In Full Albums mode, permit the same recording in separate album contexts and skip only destination duplicates. | Implemented |
| LIB-007 | Scan the local library and expose downloaded albums and playlists through a browsable view. | Implemented |
| LIB-008 | Save session history with timestamp, source URL, artwork, totals, downloaded/failed/skipped counts, and source breakdown. | Implemented |
| LIB-009 | Allow history to be cleared explicitly. | Implemented |
| LIB-010 | Store new desktop downloads in an explicit library root, defaulting to the user's `Music/Vela` folder, with album and playlist context folders. Offer `Music/Vela`, `Downloads/Vela`, and custom-root choices. Preserve an existing legacy `Apple Music` root without moving user files. Materialize a recording in every requested context by hard-linking when possible and copying otherwise. | Implemented |
| LIB-011 | Treat Apple Music as the authoritative connected library and describe local-file state as Downloaded, not Added to library. | Implemented |

### 5.7 Download session controls

| ID | Requirement | Status |
|---|---|---|
| DL-001 | Show queued tracks and real-time per-track states. Show determinate transfer progress only from measured bytes/duration; use labeled indeterminate resolving and processing states otherwise. | Implemented |
| DL-002 | Permit cancellation of the active download operation. | Implemented |
| DL-003 | Collect failed tracks into a dedicated panel. | Implemented |
| DL-004 | Automatically requeue transient and no-match track failures with jittered backoff for up to five minutes without occupying a worker while waiting. Stop immediately on authentication, unsupported-content, cancellation, or storage errors; expose a final failure only after retry exhaustion. | Implemented |
| DL-005 | Provide a verbose log panel without replacing the primary tracklist UI. | Implemented |
| DL-006 | Run two concurrent workers for all legitimate paid builds, without checking a supporter key. | Implemented |
| DL-007 | Expose a validated concurrent song-worker setting defaulting to two, with a hardware-derived ceiling of 8, 12, or 16. Retain provider-specific sub-limits and warn that higher concurrency may increase throttling, CPU, and disk contention rather than improve speed. | Implemented |
| DL-008 | Represent a requested song, album, or playlist as one job in Downloads; integrate completed history there and show excess jobs as waiting. | Partially implemented; active and completed surfaces shipped, multi-job scheduler pending |
| DL-009 | Show completed/total progress in both queue states, use semantic per-song state icons, remove completed-row progress bars, and pin overall progress to the queue's bottom edge. | Implemented |

### 5.8 Connected libraries and authentication

| ID | Requirement | Status |
|---|---|---|
| AUTH-001 | Do not provide Spotify account/library synchronization. Spotify authentication needed by retained downloader or podcast paths must not create a connected-library surface. | Implemented |
| AUTH-002 | Remove Spotify Liked Songs, mixes, playlists, saved albums, followed artists, tracked playlists, and sync controls from the application. | Implemented |
| AUTH-003 | Connect Apple Music through browser capture or manual authorization and Music-User-Token fields. | Implemented |
| AUTH-004 | Show Apple Music saved-song count, library albums with Apple-provided track counts, and library playlists. | Implemented |
| AUTH-005 | Support TIDAL OAuth/session validation and display validation status. | Implemented |
| AUTH-006 | Support Amazon browser login/capture and direct credential configuration where the user has authorized access. | Implemented, verify |
| AUTH-007 | Support optional Qobuz and Deezer credentials; do not accept or store Soulseek credentials. | Implemented |
| AUTH-008 | Store secrets locally with appropriately masked UI fields and never write secret values to ordinary logs. | Implemented, security review required |
| AUTH-009 | Clearly explain expiry, account requirements, and failure recovery for every credential type. | Partially implemented |
| AUTH-010 | Persist an account-scoped local Apple Music index and per-release track cache. Use cache-first reads, explicit refresh, 100-item API pages, and bounded parallel page retrieval for large collections. | Implemented |

### 5.9 Discovery and artist workflows

| ID | Requirement | Status |
|---|---|---|
| DISC-001 | Search for artists and display candidate results. | Implemented |
| DISC-002 | Load a selected artist's releases and group them into albums, singles, EPs, and compilations. | Implemented |
| DISC-003 | Support individual, group, and full-discography selection before queueing. | Implemented |
| DISC-004 | Browse top albums and recommended playlists by storefront/country and genre. | Implemented |
| DISC-005 | Allow a discovery item to enter the normal download workflow. | Implemented |

### 5.10 Auto-sync

| ID | Requirement | Status |
|---|---|---|
| SYNC-001 | Allow Apple Music playlists to be tracked or untracked from their library cards. | Implemented |
| SYNC-002 | Store the last known track IDs and download only newly detected tracks. | Implemented |
| SYNC-003 | Configure local time and days of week using a seven-bit Monday-to-Sunday mask. | Implemented |
| SYNC-004 | Run auto-sync manually with Sync Now. | Implemented |
| SYNC-005 | Run Apple-only scheduled synchronization while the application process is available. | Approved change; verify lifecycle behavior |
| SYNC-006 | Clearly distinguish scheduled-in-app behavior from an OS background service. | Required documentation improvement |

### 5.11 Downloaded-music browser and player

| ID | Requirement | Status |
|---|---|---|
| PLAY-001 | Browse locally downloaded albums and playlists. | Implemented |
| PLAY-002 | Open a release, inspect its track list, and play local audio. | Implemented |
| PLAY-003 | Support previous/next, seek, elapsed/duration, volume, queue position, and playback error states. | Implemented |
| PLAY-004 | Read embedded synced lyrics and highlight/scroll the active line during playback. | Implemented |
| PLAY-005 | Display plain lyrics when synchronized timing is unavailable. | Implemented |
| PLAY-006 | Save or refresh release cover art in the configured library. | Implemented |
| PLAY-007 | Do not expose an online streaming, embedded playback, or external playback-launcher surface. | Implemented |

### 5.12 Removed audio analyzer

| ID | Requirement | Status |
|---|---|---|
| ANA-001 | Do not expose audio-analysis navigation, file pickers, spectrogram generation, quality scoring, or export controls. | Implemented |

### 5.13 Source health

| ID | Requirement | Status |
|---|---|---|
| OPS-001 | Show source-health chips and a detail popover from the configured public status endpoint. | Implemented |
| OPS-002 | Continue local and alternative-source functionality when status or support endpoints are unavailable. | Implemented, verify |
| OPS-003 | Do not expose a regional album-availability checker or its backend command path. | Implemented |

### 5.14 Themes and appearance

| ID | Requirement | Status |
|---|---|---|
| UI-001 | Provide one Vela visual system with light and dark appearance modes. | Implemented |
| UI-002 | Follow system appearance by default and permit an explicit light/dark override. | Implemented |
| UI-003 | Use a persistent desktop sidebar for Library, Artists, Discover, Downloaded, Downloads, dynamic connected-iPod entries, and Settings. | Implemented |
| UI-004 | Use an expandable lower-right queue that preserves visibility of active work without taking over the main page. | Implemented |
| UI-005 | Use artwork-led cards, neutral layered surfaces, SF-style system typography, restrained motion, and a single music-red accent inspired by Apple Music without copying Apple trademarks or assets. | Implemented |
| UI-006 | Use Lucide as the general UI icon system; a connected iPod uses the closest neutral portable-device glyph when no dedicated iPod glyph exists. | Implemented |
| UI-007 | Settings use category pages, auto-save valid changes on change/blur, and display validation errors without a Save Changes button. | Implemented |
| UI-008 | Persist appearance/general preferences in a versioned migrated config: System/Light/Dark, 85–125% scale, three densities, bounded sidebar/artwork sizing, motion policy, player volume, startup/page behavior, desktop notifications, and bounded history retention. | Implemented |

### 5.15 Podcasts

| ID | Requirement | Status |
|---|---|---|
| POD-001 | Download individual Spotify podcast episodes and full shows using the user's authorized Spotify session. | Implemented |
| POD-002 | Save episodes under `Podcasts/<Show>/` with date-based filenames. | Implemented |
| POD-003 | Preserve podcast metadata and use the available OGG stream. | Implemented |
| POD-004 | Apply randomized request delay and a 50-episodes-per-hour safety cap. | Implemented |
| POD-005 | Explain that subscriber-only episodes may remain unavailable. | Implemented in documentation |

### 5.16 Supporter-system removal

| ID | Requirement | Status |
|---|---|---|
| PAY-001 | Remove the Supporter Key field, empty-key promotion, and false `✓ Supporter` UI state. | Implemented |
| PAY-002 | Remove `GetKeyInfo`, `KeyInfoResult`, `isSupporterKey`, and remote `/api/keys/validate` calls when no longer used for another authorized purpose. | Implemented |
| PAY-003 | Set the standard paid-build worker count to two without entitlement branching. | Implemented |
| PAY-004 | Remove supporter-only wording, quota claims, 30-day key claims, and free-tier claims from in-app copy and maintained documentation. | Implemented |
| PAY-005 | Decide separately whether general donation/support-status messaging remains. It must not imply feature gating. | Future decision |
| PAY-006 | Do not conflate the personal supporter key with private mirror-server credentials; preserve required service authentication until the mirror architecture is redesigned. | Required safeguard |
| PAY-007 | Update or replace the repository license and commercial distribution terms before shipping the paid-only version. | Owner action required |

## 6. Important distinctions and constraints

### 6.1 Supporter key versus provider credentials

The current code has multiple key concepts:

- The personal `antra_api_key`, currently used for supporter validation and some quota/proxy requests.
- A manifest-delivered mirror API key used by self-hosted source adapters.
- User-owned service credentials, cookies, OAuth tokens, and device files.
- Optional third-party API keys for metadata and lyrics providers.

Removing supporter entitlement must not blindly remove mirror authentication or user service credentials. Each call site must be classified before deletion.

### 6.2 Third-party dependency behavior

- Features may depend on service availability, account tier, region, API changes, rate limits, and endpoint configuration.
- The UI must report unavailable sources and authorization failures without promising universal coverage.
- Credentials must be supplied and used only for accounts and content the user is authorized to access.
- The product must avoid claims that it bypasses subscriptions, DRM, regional licensing, or third-party controls.
- Online playback is outside the product scope. YouTube Music links remain downloader inputs only where supported.

### 6.3 Current documentation discrepancies

- `FEATURES.md` says free and supporter tiers exist; the approved paid-only direction supersedes this.
- `FEATURES.md` says two concurrent tracks by default; the current implementation gives one worker to non-supporters and two to validated supporters.
- `FEATURES.md` says 11 themes; the frontend defines 12.
- `README.md` says the software is free, which will become incorrect for the paid-only release.
- Some code-level providers and login paths have little or no user documentation and require validation before marketing.

## 7. Non-functional requirements

### 7.1 Reliability

- A single failed provider must not terminate a multi-provider resolution attempt when alternatives remain.
- Interrupted sessions must leave partial output in a predictable, recoverable state.
- Configuration writes must be atomic or recoverable.
- Network calls need explicit timeouts and actionable errors.
- Auto-sync must not redownload unchanged playlist entries.

### 7.2 Performance

- Paid builds use two download workers by default.
- Large tracklists must render progressively and keep the window responsive.
- Source searches that are safe to parallelize should run concurrently with bounded resource use.
- Library scans must not freeze the UI thread.

### 7.3 Security and privacy

- Never include secrets in logs, history, telemetry, screenshots, or error reports.
- Mask stored credentials in the UI and avoid returning them through APIs that do not require them.
- Restrict local media serving and OAuth callbacks to loopback interfaces where applicable.
- Validate remote manifests and endpoints; future hardening should use authenticated/signed configuration.
- Treat browser-captured tokens and device files as sensitive local data.
- Never upload Apple authorization or Music-User-Token values to a mirror or shared refresh endpoint.
- Document what data leaves the device and why.

### 7.4 Accessibility

- All actionable controls must be keyboard reachable and have a visible focus state.
- Icon-only buttons require accessible names/tooltips.
- Text and state colors should meet WCAG 2.2 AA contrast targets.
- Status must not be communicated by color alone.
- Modal focus must be trapped and restored to the invoking control.
- Reduced-motion preferences must be respected for decorative animation.

### 7.5 Compatibility

- Windows 10 or newer.
- macOS 12 or newer on Apple Silicon and Intel where builds remain supported.
- Linux AppImage on supported desktop environments.
- Output metadata must remain readable by common desktop players and media servers.

### 7.6 Maintainability

- Break the monolithic `App.svelte` into feature-oriented components and stores during the overhaul.
- Keep provider adapters behind a consistent interface.
- Keep Go-to-Python messages versionable and schema-validated.
- Centralize design tokens; avoid new hard-coded colors and inline layout styles.
- Add automated tests for matching, organization, config migration, entitlement removal, and critical UI state.

## 8. Architecture baseline

| Layer | Current responsibility |
|---|---|
| Svelte/TypeScript | Navigation, forms, overlays, library browsing, queue/progress UI, player, and settings presentation |
| Wails/Go | Desktop lifecycle, dialogs, config persistence, local file/media access, process management, events, browser-assisted login orchestration |
| Python service/engine | Metadata collection, source resolution, download orchestration, validation, tagging, transcoding, organization, deduplication, podcasts, auto-sync |
| Newline-delimited JSON IPC | Backend commands and event/progress exchange |
| Endpoint manifest | Remote source URLs and some source authentication/configuration |
| Local user-data files | Configuration, caches, provider statistics, history, support-status cache, and service state |

## 9. Data and configuration

Primary persisted settings include:

- Library root and first-run completion.
- Output format, retry count, source selection, matching preferences, and cover-art behavior.
- Folder and filename templates, numbering, whitespace, conflict, and deduplication preferences.
- Theme.
- Apple Music, Amazon Music, TIDAL, Qobuz, and Deezer connection details, plus downloader-only Spotify credentials where still required.
- Auto-sync schedule and tracked playlist state.
- Provider reliability statistics and endpoint-manifest-derived configuration in the Python layer.

Before the overhaul ships, configuration needs a schema version and explicit migrations, especially for removing `antra_api_key` and obsolete supporter fields without disturbing mirror or provider credentials.

## 10. Success measures

Initial measures for the overhaul should be collected locally or through an explicitly consented, privacy-preserving system:

- Percentage of sessions completing without an application-level error.
- Track resolution success, incorrect-match reports, and provider fallback frequency.
- Median time from pasted URL to visible tracklist.
- Median and 95th-percentile completion time by collection size and format.
- Retry recovery rate for failed tracks.
- Auto-sync runs that add only genuinely new tracks.
- Crash-free sessions by platform.
- Support requests related to authentication, source availability, naming, and output location.

No telemetry implementation is authorized by this document. Collection requires a separate privacy and product decision.

## 11. Acceptance criteria for the first paid-only conversion

The conversion is complete when:

1. A clean install can complete first-run setup and start a two-worker download without a supporter key.
2. No supporter-key input, supporter badge, quota display, validation request, or supporter-only copy remains in the production UI.
3. The application no longer calls `/api/keys/validate` for feature entitlement.
4. Removing entitlement code does not remove required mirror-server or user-service authentication.
5. Existing user configurations migrate without losing library paths, naming preferences, Apple credentials, or Apple tracked playlists; obsolete Spotify-sync, P2P, and legacy-theme fields are discarded safely.
6. Offline startup is not delayed by an obsolete entitlement check.
7. `README.md`, `FEATURES.md`, in-app copy, generated Wails bindings, and release notes describe the paid-only feature model consistently.
8. Frontend checks, Go tests/build, and relevant Python tests pass.
9. Manual smoke tests cover first run, one URL, an album/playlist, retry/cancel, downloaded music playback, settings persistence, and at least one connected-library flow.
10. Distribution terms and the repository license reflect the owner's intended paid product model.

## 12. Overhaul roadmap

### iPod management requirements

Vela will integrate the MIT-licensed iOpenPod engine behind Vela's own design
system. The distributed product must retain iOpenPod's copyright and MIT
license notice. The PyQt interface is not part of Vela; reusable headless
device, database, artwork, checksum, backup, and sync modules are.

| ID | Requirement | Status |
|---|---|---|
| IPOD-001 | Detect mounted iPod Classic, Mini, and Nano devices and show identity, capacity, firmware, filesystem, database, checksum, and media capabilities. | Implemented |
| IPOD-001A | Run iOpenPod as an embedded headless library only; never launch its GUI or require a separate background application. Show a sidebar entry named for each detected device only while connected. | Implemented |
| IPOD-001B | On Windows, detect attached Mac-formatted HFS+ iPods through bounded raw read-only metadata inspection when the OS cannot mount them. Show a clear compatibility state and block browse, backup, restore, sync, eject, and capacity operations rather than treating the device as absent or writable. | Implemented; file access still requires macOS or a trusted HFS+ filesystem layer |
| IPOD-002 | Read and browse device tracks, albums, artists, podcasts, playlists, ratings, play counts, skip counts, artwork, photos, and video metadata without modifying the device. | Implemented |
| IPOD-003 | Build a reviewable sync plan before any device write, covering additions, removals, metadata, artwork, playlists, transcoding, and storage impact. | Implemented |
| IPOD-004 | Create a recoverable device/database backup before applying a sync plan and expose offline inventory, notes, deep verification, export, retention, same-device restore, and interrupted-operation recovery. | Implemented; supervised hardware verification pending |
| IPOD-005 | Sync Vela's local library to the iPod, preserving iTunesDB/SQLiteDB rules, generation-specific checksums, artwork formats, and safe file paths. | Safety-gated SyncEngine backend implemented; supervised hardware verification pending |
| IPOD-006 | Transcode unsupported FLAC/OGG/video media to device-compatible formats with FFmpeg and an optional conversion cache. | Planned |
| IPOD-007 | Create, edit, delete, and reorder standard and smart playlists; manage podcasts, photos, videos, and drag-and-drop additions. | Planned |
| IPOD-008 | Import play counts, ratings, skips, and voice memos back to the local library where supported; scrobbling remains opt-in. | Planned |
| IPOD-009 | Support safe eject, device diagnostics, Rockbox compatibility options, and explicit warnings for unsupported Shuffle and Touch models. | Safe eject, hardware gating, and guided Rockbox capacity workflow implemented; supervised hardware verification pending |
| IPOD-010 | Restore a verified full regular-file snapshot only to the exact source identity, after a fresh verified host-side safety checkpoint, exact target revalidation, and a clearly marked non-cancellable commit/flush boundary. | Implemented; supervised hardware verification pending |
| IPOD-011 | Migrate compatible media and library metadata to a separately initialized replacement iPod without copying source SysInfo or other identity material; rebuild target-specific databases, checksums, artwork, and playlists through a reviewed addition-only plan. | Implemented; supervised hardware verification pending |
| IPOD-012 | Offer an experimental Windows-only Classic 6G/6.5G capacity-unlock workflow only after FAT32, stable identity, model/firmware, USB, writable-volume, and storage-health evidence pass. Require pinned artifacts, verified filesystem and SysCfg backups, byte-exact candidate/readback checks, manual NOR attestation, and user-controlled iTunes restore. | Implemented behind hardware release gate |
| IPOD-013 | Persist restore, migration, and capacity-unlock operation state across restarts; reject cancellation after commit and prevent application shutdown while protected commit/flush work is active. | Implemented |

For this contract, **full file restore** means the complete regular-file
snapshot supported by iOpenPod. It does not contain a partition table, NOR,
SysCfg, firmware, or a factory image. **Compatible-device migration** restores
content while preserving the replacement iPod's hardware identity and
rebuilding device-specific databases/checksums; it never raw-copies source
identity files. **Capacity unlock** is a separate destructive maintenance
workflow that changes a supported Classic 6G/6.5G SysCfg and then wipes the
device during a manual restore of Apple's unmodified firmware 2.0.2.

All device mutations require explicit user confirmation. Device discovery and
browsing are read-only. Restore requires a verified host-side safety checkpoint
and exact device revalidation. Vela must never silently initialize, erase,
format, rewrite NOR, or launch/control a firmware restore. Hard NOR corruption
can still require external hardware recovery; Vela does not claim factory
support or a guaranteed unbrick path.

### Phase 1 — Baseline and entitlement simplification

- Remove supporter feature gating while preserving unrelated credentials.
- Make two workers standard.
- Remove or rewrite supporter/free-tier copy.
- Add config migration and tests.
- Align public documentation and licensing.

### Phase 2 — Structural UI refactor

- Split `App.svelte` into navigation, downloads, library, discovery, player, settings, and modal components.
- Introduce typed stores for config, session state, account state, and playback.
- Replace inline styles with design-system components and tokens.
- Improve keyboard, focus, responsive, and empty/error states.

### Phase 3 — Engine and provider hardening

- Formalize provider capability declarations and health.
- Version IPC messages and config.
- Add deterministic resolver/matching tests and fixture-based provider tests.
- Harden manifest authenticity, secret handling, and local media serving.

### Phase 4 — Commercial distribution

- Decide storefront, delivery, updates, refunds, device policy, and support model.
- If activation is required, specify it as a new system with offline and recovery requirements; do not resurrect ambiguous supporter logic.
- Publish privacy, third-party-service, and acceptable-use documentation.

## 13. Open product decisions

- What is the paid product name, price, purchase channel, and update entitlement?
- Is the paid build DRM-free, account-based, license-key-based, or storefront-managed?
- Should the donation/support progress UI remain after paid conversion?
- Should concurrency remain fixed at two or become a bounded advanced setting?
- Which provider integrations are officially supported versus experimental?
- Which account flows should remain visible in the simplified settings experience?
- Is scheduled sync expected to work only while Antra is open, or through an OS background agent?
- What telemetry, crash reporting, and update checking—if any—will be offered and under what consent model?
- Which themes remain first-class after the visual overhaul?
- What migration and support policy applies to users of earlier free/supporter builds?

## 14. Library indexing and download queue requirements

| ID | Requirement | Status |
|---|---|---|
| LIB-INDEX-001 | After Apple Music connects, checkpoint every library album, playlist, favourite song collection, and release track list into the account-scoped local SQLite index. | Implemented |
| LIB-INDEX-002 | Show resumable indexing progress above Settings. Closing the app pauses work by stopping the index process; reopening resumes from cached release checkpoints. | Implemented |
| LIB-INDEX-003 | Library summaries and already-indexed release details load from local storage first. Apple endpoints are used to refresh the index, not as a prerequisite for every view. | Implemented |
| LIB-INDEX-004 | Maintain a persistent local index for Downloaded music so summaries and previously opened release details do not require a full filesystem/probe scan on every view. | Implemented |
| LIB-INDEX-005 | Pre-index every Downloaded release detail, metadata probe result, and artwork reference. Show the last complete index immediately while a background reconciliation runs. | Implemented |
| LIB-INDEX-006 | Checkpoint completed Apple release details in the local backend index without streaming bulk track payloads through the UI event channel; preload summary artwork separately. | Implemented |
| LIB-INDEX-007 | Run Apple and Downloaded indexing asynchronously and stage their startup work so navigation remains interactive. Both indexes expose bounded, determinate progress events and return cached content immediately. | Implemented |
| LIB-INDEX-008 | Treat an Apple index as complete only when every current album and playlist detail is checkpointed successfully. Keep incomplete progress visible, retry transient failures, skip the progress surface on later launches after a matching complete index, and provide a confirmed Reset index action that preserves credentials and downloaded files. | Implemented |
| LIB-INDEX-009 | Calculate full-library progress from real Apple `meta.total` counts for saved songs and every album/playlist, not summary estimates or merely the number of release requests. Reserve 100% exclusively for explicit successful validation. | Implemented |
| LIB-INDEX-010 | Count every release checkpoint and every song occurrence as index work, use whole percentages, and advance as track pages finish. Count the derived artist-index pass and each artist-detail cache write explicitly, then include final cache validation so progress cannot hide a long save behind a synthetic 99%. | Implemented |
| LIB-INDEX-011 | After a complete index, return release details and derived artist lookups with the cache-first library snapshot so album, playlist, Favourites, and artist pages open without a per-page backend fetch. Keep the last complete snapshot visible during later background summary reconciliation. | Implemented |
| QUEUE-001 | Treat every song, album, playlist, or custom link request as a persistent job. Adding a request must never replace or cancel the active job. | Implemented |
| QUEUE-002 | Restore waiting work after restart. Completed files are validated/reused and incomplete work resumes without duplicating valid output. | Implemented |
| QUEUE-003 | Pause stops the active backend process tree promptly, preserves completed songs, checkpoints the job as paused, and retries or resumes a provider-owned partial where supported. Queue cancellation requires themed confirmation. | Implemented |
| QUEUE-004 | Apply worker-limit changes live from 1 to the device ceiling. Increasing starts additional work when available; decreasing lets in-flight tracks finish and gates subsequent starts. Show authoritative active/configured/ceiling and phase counts. | Implemented |
| QUEUE-005 | The floating download controller remains compact, shows current artwork and overall progress, offers pause/resume/cancel, opens Downloads when clicked, and disappears when no active/waiting job remains. | Implemented |
| QUEUE-006 | Downloads contains the current song list (bounded-height scrolling), waiting job queue, clean expandable log, and completed job history with functioning action menus. | Implemented |
| QUEUE-007 | Waiting and paused jobs can be reordered, promoted to run immediately (checkpointing the former active job), or removed individually with confirmation. Queue state changes render before backend preparation begins. | Implemented |
| LIB-INDEX-012 | Downloaded indexing weights metadata work by every detected song rather than every folder, emits explicit warning/error events, and never reports completion when the index cache could not be written. | Implemented |
| LIB-INDEX-013 | Apple indexing yields resource priority to explicit downloads and resumes from its local checkpoints afterwards so background work cannot starve the download backend. | Implemented |
| ART-001 | Artwork extraction cache identities include the complete normalized path plus file size and modification time; releases in the same parent folder must never collide. Pre-warm embedded artwork during background indexing, request display-sized remote artwork, preserve decoded/cache reuse across navigation, and keep cover sidecars enabled by default. | Implemented |
| LIB-INDEX-014 | Ignore playlist-folder resources returned alongside playlists. Load playlist tracks from Apple's library or catalog playlist resource and its included relationship, following API-provided catalog and pagination links instead of constructing a direct catalog `/tracks` route. Preserve known zero counts so valid empty playlists checkpoint successfully without a track request. | Implemented |
| NAMING-001 | Default album track names use the track number only (`12 - Title`). Disc prefixes are opt-in so normal metadata cannot produce confusing names such as `1-12 - Title`. | Implemented |

## 15. Library information architecture

The sidebar Library section is expanded by default and orders its destinations
as Recently Added, Albums, Playlists, Favourites, Artists, and Downloaded.
Artists and Discover are not separate top-level product areas. Attached iPods
remain dynamic named entries. Right-clicking Library opens a context menu with
Refresh library; it does not refresh immediately.

- Recently Added presents locally indexed albums and playlists.
- Albums and Playlists use consistent selectable grid cards and shared search,
  sort, filter, selection, download, and context-menu behaviour.
- The standalone Playlists grid keeps the normal workspace title bar. The
  dedicated Favourites destination omits it because its artwork-led hero is the
  page title; opened playlists retain normal detail back navigation.
- Favourites opens Apple Music's actual automatic Favourite Songs playlist,
  using its Apple-provided URL, artwork, track count, and a red star identity.
- Artists lists library artists alphabetically with artwork when available and
  leads to the artist's locally indexed library music.
- Downloaded remains at the bottom of the Library group and represents files on
  this device.
- Library is a fixed section label, styled like Activity. It has no icon,
  collapse affordance, or selected state; only its nested destination is active.
- Albums, playlists, favourites, and artists open as full main-panel track
  pages. Release pages have adaptive artwork-derived backgrounds, local search
  and ordering, Escape/back navigation, and never use a modal sheet.
- Downloaded albums and playlists use the same full-panel release pattern;
  cached local artwork URLs are renewed for the active media-server session.
- Library search, sorting, and order reversal apply synchronously from the
  in-memory index as the user types or chooses an option.
