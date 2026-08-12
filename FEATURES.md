<h1 align="center">Vela Features</h1>

<p align="center">
  <a href="README.md">← Back to README</a> &nbsp;·&nbsp;
  <a href="README.md">Build and test</a>
</p>

<br/>

---

## Multi-Source Audio Engine

Vela resolves links from Spotify, Apple Music, Amazon Music, Tidal, Qobuz, and Deezer. In lossless mode it queries all active lossless-capable sources in parallel and picks the result with the highest bit depth and sample rate — not just the first match found. Lossy formats (AAC, MP3) use dedicated lossy sources first; lossless adapters are only tried as a last resort.

```
Source chain (per track):

  Authenticated services → Tidal · Qobuz · Amazon Music · Deezer · Apple Music
  Built-in fallbacks    →  lossy and source-specific fallback adapters
  Local resolver        →  ranked non-P2P sources selected by requested format and quality
```

Vela also remembers which sources have been reliable across app restarts (a persistent provider-reliability store) and reorders same-priority sources accordingly — without ever letting a lower-quality source jump ahead of a higher-quality one.

Paid builds include the complete feature set and use the standard two-worker download engine. There is no supporter key or feature-gated tier.

---

## Download Source Selector

Constrain downloads to the services you choose instead of letting the resolver use everything. **Auto** (default) runs the full resolver chain; or select any combination of **Tidal**, **Qobuz**, **Apple Music**, **Amazon Music**, and **Deezer** to limit resolution to only those services — unselected services are excluded entirely. Pick a single service to force it, or several (e.g. Tidal + Qobuz) to allow fallback only between your chosen sources.

Accessible from the pill-style selector on the main screen — no need to open Settings.

---

## ISRC-Based Exact Matching

Most tools match by title and artist and often grab the wrong version: a remaster, a radio edit, a regional pressing. Vela uses **ISRC codes** (the unique identifier of every recording) to identify the requested recording accurately.

When ISRCs are available, Vela uses them to match against source APIs directly. When they are not, it falls back to a scored similarity search with title-artist weighting.

---

## Explicit Version Preference

If the track you requested is the explicit (unedited) version, Vela will prefer it. Radio edits and censored versions are penalised in the match scoring and skipped when a clean result is the only option, keeping the rest of the queue searching until an explicit source is found.

Configurable in Settings: **Prefer explicit versions** (on by default).

---

## Strict Matching Mode

Opt-in safety mode for niche music. When enabled, Vela requires stronger confidence for non-ISRC matches and applies tighter post-download duration validation, preferring a clean failure over a risky wrong-audio save. Default is off so current behaviour is unchanged unless you opt in.

---

## Hi-Res Awareness

Tidal and Qobuz expose per-track bit depth and sample rate in their search results. When a track has a hi-res master available (e.g. 24-bit/96kHz from Tidal, up to 24-bit/192kHz from Qobuz), Vela keeps searching all lossless-capable sources and selects the highest-resolution result — ranked by bit depth first, then sample rate. CD quality (16-bit/44.1kHz) is only used if no hi-res source can be located.

Qobuz URLs also support a **strict 24-bit mode**: if no Qobuz account can produce a genuine 24-bit stream, the request fails cleanly instead of silently saving 16-bit audio under a 24-bit request.

---

## Auto-Tagging

Every downloaded file is tagged automatically. No manual editing, no missing artwork, no "Track 01".

| Tag | Source |
|---|---|
| Title, Artist, Album, Track # | Spotify / Apple Music / Amazon metadata |
| Album artwork | Full-resolution cover from the streaming catalog |
| Release date | Full ISO date where available; year as fallback |
| Genre | Deezer album-level genre via ISRC, with MusicBrainz fallback |
| Composer | Sourced from Qobuz, Tidal, or Deezer metadata |
| Disc number | Correct disc tagging for multi-disc albums |
| Lyrics | LRCLIB synced lyrics, Genius / Musixmatch fallback |
| ISRC | Embedded for future matching |

Tags are written in the correct format for every container: ID3v2 for MP3, Vorbis comments for FLAC, MP4 atoms for M4A — fully readable by Windows Media Player, VLC, foobar2000, and all major media servers.

---

## Smart Library Organisation

Output is structured the way every media server expects:

```
~/Music/
  Artist Name/
    Album Name (Year)/
      101 - Track Title.flac
      102 - Track Title.flac
      cover.jpg
```

Downloads land directly inside the library root — no intermediate `Albums/` or `Playlists/` subfolders. All tracks use disc-prefixed numbering (`101`, `102`, ..., `201`, `202`, ...) so Plex, Navidrome, and Jellyfin always know which disc a track belongs to.

### Template-Based Filenames and Folders

Set your own naming scheme using tokens. Three template fields in Folder Settings:

| Template | Default | Example output |
|---|---|---|
| Single track filename | `{artist} - {title}` | `The Beatles - Come Together.flac` |
| Album track filename | `{track} - {title}` | `07 - Come Together.flac` |
| Folder structure | `{album_artist}/{year} - {album}` | `The Beatles/1969 - Abbey Road/` |

Available tokens: `{title}` `{artist}` `{album_artist}` `{album}` `{year}` `{track}` `{disc}` `{genre}` `{composer}` `{isrc}` `{codec}` `{bitrate}` `{quality}`

Each template field shows a live preview as you type. Click any token chip to insert it at the cursor position.

### Multi-Disc Handling

| Mode | Example |
|---|---|
| **Disc prefix** (default) | `2-05 - Track.flac` |
| **Offset 101/201** | `205 - Track.flac` |
| **Track only** | `05 - Track.flac` |

---

## Smart Deduplication

Vela builds an identity index of your library using ISRCs, track IDs, and normalised title+artist keys. Before downloading, it checks if a track already exists, even if it was saved under a different artist folder name or album edition.

### Library Mode Options

| Mode | Behaviour |
|---|---|
| **Smart Dedup** (default) | Skip a track if the same ISRC exists anywhere in your library. Saves storage. |
| **Full Albums** | Skip only if the file already exists in the same destination folder. Lets you own the same track across multiple album contexts. |

---

## Appearance and Navigation

Vela uses one coherent visual system with System, Light, and Dark appearance choices. The desktop shell has a persistent sidebar for Library, Add Music, Artists, Discover, Downloaded, History, and Settings. Active downloads stay visible in a compact lower-right queue that expands into full per-track progress.

---

## Apple Music Library

Apple Music is the only connected-library service. Connect in the browser or enter the Apple authorization and Music User Token manually, then browse Saved Songs and library playlists directly in Vela.

Apple credentials remain in the local application configuration. They are used only with Apple authenticated endpoints and are not uploaded to a Vela server, mirror, shared refresh service, or analytics endpoint. Spotify links remain accepted by the downloader, but there is no Spotify account-library or playlist-sync surface.

Library is the default page on launch.

---

## Auto-Sync / Scheduled Downloads

Keep selected Apple Music playlists mirrored to your library automatically. Toggle tracking on a playlist card, then set a schedule in Settings. While Vela is running, it checks tracked Apple playlists, diffs against the previous state, and downloads **only new tracks**. A "Sync Now" button runs it on demand.

---

## YouTube Music URLs

Paste a `music.youtube.com` link — single track, playlist, or album — and Vela resolves it through the same lossless source chain as a Spotify or Apple Music link. yt-dlp extracts the metadata (and ISRC where available); the actual audio comes from Tidal / Qobuz / Amazon / Deezer, so every quality tier (FLAC, ALAC, AAC, MP3) is supported.

---

## Artist Discography Download

Search for any artist by Spotify or Apple Music URL. Vela fetches their full discography and presents it grouped by release type.

- Browse **Albums**, **Singles**, **EPs and Compilations** separately
- Bulk-select or deselect entire groups with one click
- Queue individual albums or the full catalogue in one batch

---

## Parallel Download Engine

Vela downloads 2 tracks concurrently by default. Playlists and albums that would take minutes sequentially complete in a fraction of the time.

```
Sequential:   track 1 → track 2 → track 3 → ...
Parallel:     track 1 ↘
              track 2 → done
              track 3 ↗
```

---

## Rich Tracklist UI

When a URL is pasted, the full tracklist appears immediately before any download starts. For playlists with 1000+ tracks, rows appear progressively as pages load — you are not waiting for the full fetch to complete.

Each row shows the track title, artist, duration, and a real-time progress bar as the file downloads. The playlist header displays the cover art, type (ALBUM / PLAYLIST / SINGLE), artist, track count, total duration, and release date in the same layout as a streaming app.

When multiple URLs are queued in one session, a divider with the album cover and title separates each batch so you always know which tracks belong where.

A dedicated log panel (accessible via the 📋 button) shows verbose download output without disrupting the tracklist view.

---

## Failed Downloads Viewer

When a playlist or album finishes, any tracks that failed are collected into a dedicated **Failed (N)** panel below the tracklist — no scrolling to hunt for red rows. Retry a single track, **Retry All** (runs them one by one with live progress), or dismiss entries you don't care about. The panel collapses and auto-hides once everything is retried or dismissed.

---

## Synced Lyrics in the Player

The built-in player for downloaded tracks reads embedded LRC lyrics and shows a scrolling panel that auto-highlights the current line in sync with playback. Toggle it with the ♪ button; it appears automatically when a track has synced lyrics. Plain (unsynced) lyrics are shown too.

---

## Source Health Check

Chips below the URL bar show the live status of each source from a public status endpoint. Green means online and active; darker red means currently unavailable. Vela checks the status on startup and continues working normally if the endpoint is unreachable.

---

## Library History

Every completed download session is saved to history with its cover art thumbnail, album/playlist title, URL, track count, and timestamp, so you can quickly identify what you have downloaded without opening the folder.

---

## Spotify Podcast Downloads

Vela can download any Spotify podcast episode or entire show directly using your own Spotify account cookie — no external server, no third-party proxy.

### Account requirement

A **free Spotify account is sufficient**. Podcast audio is not gated behind Spotify Premium. The 320 kbps OGG Vorbis format is available to all logged-in users.

> The only exception is **subscriber-only episodes** — episodes paywalled by the podcast creator (separate from Spotify Premium). Those will fail with "no audio files available."

### Supported URLs

| URL type | Example |
|---|---|
| Single episode | `https://open.spotify.com/episode/4rOoJ6Egrf8K2IrywzwOMk` |
| Full show (all episodes) | `https://open.spotify.com/show/0ofXAdFIQQRsCYj9754UFx` |

### Setup: getting your sp_dc cookie

1. Open **[open.spotify.com](https://open.spotify.com)** in any browser while logged in
2. Open **DevTools** (F12 on Chrome/Edge, Cmd+Option+I on macOS)
3. Go to **Application** tab → **Cookies** → `https://open.spotify.com`
4. Find the cookie named **`sp_dc`** and copy its value (starts with `AQ...`)
5. In Vela, open **Settings → Spotify Account** and paste it into the **sp_dc cookie** field

> **Easiest path:** use the **Connect Spotify Account** button in Settings instead — it opens your browser, you log in, and Vela captures the cookie automatically. The manual steps above are only needed if you prefer pasting it yourself.

The cookie is valid for approximately one year. The same connection also powers the **My Library** tab.

### Output and tagging

Episodes are saved inside your configured Music folder:

```
~/Music/
  Podcasts/
    Show Name/
      2024-03-15 - Episode Title.ogg
      2024-03-22 - Another Episode.ogg
```

### Rate limiting

To protect your Spotify account from being flagged, Vela applies automatic rate limiting: a 3-7 second random delay between each episode and a 50 episodes/hour hard cap.

---

## Audio Format Options

| Mode | Output |
|---|---|
| **FLAC 24-bit** | Highest available lossless source, prioritising hi-res where available. |
| **FLAC 16-bit** | CD-quality lossless, with 16-bit sources preferred over 24-bit where possible. |
| **ALAC** | Apple-compatible lossless output. |
| **AAC** | Native AAC sources first, lossless only as fallback when needed. |
| **MP3** | Native MP3/lossy sources first, lossless only as fallback when needed. |
| **Auto** | Best available source with lossless preferred. |

---

## Platform Support

All builds ship as **single self-contained binaries** with no Python, no runtime, and no dependencies to install.

| Platform | Minimum | File |
|---|---|---|
| Windows | 10+ | `Vela.exe` |
| macOS | 12+ (Apple Silicon) | `Vela-macOS.dmg` |
| macOS | 12+ (Intel) | `Vela-macOS-Intel.dmg` |
| Linux | Any | `Vela-Linux.AppImage` |

---

## Tech Stack

```
Desktop shell   →  Go 1.23 · Wails v2
Frontend UI     →  Svelte · TypeScript · Vite
Download engine →  Python 3.11
IPC             →  newline-delimited JSON over stdout
Packaging       →  PyInstaller · wails build · AppImage · create-dmg
CI/CD           →  GitHub Actions, 4-platform matrix build on tag push
```

---

[Back to README](README.md)
