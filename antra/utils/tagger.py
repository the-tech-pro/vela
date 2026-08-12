"""
File tagger using mutagen.
Supports MP3 (ID3) and FLAC (VorbisComment).
Embeds: title, artists, album, year, track number, genre, artwork, lyrics.
"""
import hashlib
import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from io import BytesIO
from typing import Optional

import requests
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (
    ID3,
    TIT2,  # Title
    TPE1,  # Artist
    TPE2,  # Album artist
    TALB,  # Album
    TDRC,  # Year
    TRCK,  # Track number
    TPOS,  # Disc number
    TCON,  # Genre
    TCOM,  # Composer
    TPUB,  # Publisher/Label
    APIC,  # Artwork
    USLT,  # Unsynced lyrics
    SYLT,  # Synced lyrics
    TSRC,  # ISRC
    TXXX,  # Custom text
    Encoding,
)
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.mp3 import MP3
from antra.utils.lyrics import validate_and_strip_lrc, lrc_to_sylt_frames
try:
    from PIL import Image
except ImportError:
    Image = None

from antra.core.models import TrackMetadata

logger = logging.getLogger(__name__)

_Artwork = tuple[bytes, str, int, int, int]


@dataclass
class _ArtworkFlight:
    event: threading.Event = field(default_factory=threading.Event)
    result: Optional[_Artwork] = None


def _sniff_image_mime(data: bytes, response_mime: Optional[str]) -> str:
    mime = (response_mime or "").split(";")[0].strip().lower()
    if mime in {"image/jpeg", "image/png"}:
        return mime

    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"

    return "image/jpeg"


class FileTagger:
    _MAX_EMBEDDED_ART_EDGE = 1000
    _DEFAULT_ARTWORK_CACHE_ENTRIES = 64

    def __init__(self, artwork_cache_entries: int = _DEFAULT_ARTWORK_CACHE_ENTRIES):
        self._artwork_cache_entries = max(1, int(artwork_cache_entries))
        self._artwork_cache: OrderedDict[str, Optional[_Artwork]] = OrderedDict()
        self._artwork_content_cache: OrderedDict[tuple[str, str], Optional[_Artwork]] = OrderedDict()
        self._artwork_flights: dict[str, _ArtworkFlight] = {}
        self._artwork_content_flights: dict[tuple[str, str], _ArtworkFlight] = {}
        self._artwork_lock = threading.Lock()
        self._sidecar_lock = threading.Lock()

    def tag(
        self,
        file_path: str,
        track: TrackMetadata,
    ) -> bool:
        """Tag file at file_path with all available metadata. Returns True on success."""
        # MusicBrainz fallback enrichment
        from antra.core.musicbrainz_fetcher import enrich_metadata
        if not track.genres and track.isrc:
            logger.debug(f"[Tagger] Genres missing for {track.title}, querying MusicBrainz...")
            enrich_metadata(track)

        ext = os.path.splitext(file_path)[1].lower()
        try:
            if ext == ".flac":
                self._tag_flac(file_path, track)
            elif ext == ".mp3":
                self._tag_mp3(file_path, track)
            elif ext in {".m4a", ".mp4"}:
                self._tag_mp4(file_path, track)
            else:
                logger.warning(f"Unsupported format for tagging: {ext}")
                self._write_lyrics_sidecars(file_path, track)
                return False
            
            self.embed_lyrics(
                file_path,
                track.lyrics or "",
                track.synced_lyrics or "",
                track.duration_ms or 0
            )
            self._write_lyrics_sidecars(file_path, track)
            logger.debug(f"Tagged: {file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to tag {file_path}: {e}")
            return False

    def save_cover_art_sidecar(self, file_path: str, track: TrackMetadata) -> Optional[str]:
        if not track.artwork_url:
            return None

        folder = os.path.dirname(file_path)
        if not folder:
            return None

        with self._sidecar_lock:
            existing = self._existing_cover_sidecar(folder)
            if existing:
                return existing

        artwork = self._fetch_raw_artwork(track.artwork_url)
        if not artwork:
            return None

        data, mime = artwork
        with self._sidecar_lock:
            existing = self._existing_cover_sidecar(folder)
            if existing:
                return existing
            ext = ".png" if mime == "image/png" else ".jpg"
            out_path = os.path.join(folder, f"cover{ext}")
            try:
                with open(out_path, "wb") as fh:
                    fh.write(data)
                logger.debug("Saved cover art sidecar: %s", out_path)
                return out_path
            except Exception as e:
                logger.warning("Failed to save cover art sidecar for %s: %s", file_path, e)
                return None

    # ── FLAC ──────────────────────────────────────────────────────────────

    def _tag_flac(self, path: str, track: TrackMetadata):
        audio = FLAC(path)

        audio["title"] = track.title
        audio["artist"] = track.artists
        album_artist_str = ", ".join(track.album_artists) if track.album_artists else track.primary_artist
        audio["albumartist"] = [album_artist_str]
        audio["album"] = track.album
        date_str = track.release_date or (str(track.release_year) if track.release_year else None)
        if date_str:
            audio["date"] = date_str
            # Also write YEAR (4-digit) — Windows Media Player reads YEAR, not DATE
            year_str = date_str[:4] if len(date_str) >= 4 and date_str[:4].isdigit() else None
            if year_str:
                audio["year"] = year_str
        if track.track_number:
            tn = str(track.track_number)
            if track.total_tracks:
                tn += f"/{track.total_tracks}"
            audio["tracknumber"] = tn
        if track.disc_number:
            audio["discnumber"] = str(track.disc_number)
        if track.genres:
            audio["genre"] = track.genres
        if track.isrc:
            audio["isrc"] = track.isrc
        if track.upc:
            audio["barcode"] = track.upc
        if track.iswc:
            audio["iswc"] = track.iswc
        if track.audio_traits:
            audio["audio_traits"] = track.audio_traits
        if track.spotify_id:
            audio["spotify_id"] = track.spotify_id
        if track.composer:
            audio["composer"] = [track.composer]
        lbl = getattr(track, "label", None)
        if lbl:
            audio["organization"] = [lbl]

        artwork = self._fetch_artwork(track.artwork_url)
        if artwork:
            artwork_data, mime, width, height, depth = artwork
            audio.clear_pictures()
            pic = Picture()
            pic.type = 3  # Cover (front)
            pic.mime = mime
            pic.desc = "Cover"
            pic.data = artwork_data
            pic.width = width
            pic.height = height
            pic.depth = depth
            pic.colors = 0
            audio.add_picture(pic)

        audio.save()

    # ── MP3 ───────────────────────────────────────────────────────────────

    def _tag_mp3(self, path: str, track: TrackMetadata):
        try:
            audio = ID3(path)
        except Exception:
            audio = ID3()

        audio.add(TIT2(encoding=3, text=track.title))
        audio.add(TPE1(encoding=3, text=track.artist_string))
        album_artist_str = ", ".join(track.album_artists) if track.album_artists else track.primary_artist
        audio.add(TPE2(encoding=3, text=album_artist_str))
        audio.add(TALB(encoding=3, text=track.album))

        date_str = track.release_date or (str(track.release_year) if track.release_year else None)
        if date_str:
            audio.add(TDRC(encoding=3, text=date_str))
        if track.track_number:
            tn = str(track.track_number)
            if track.total_tracks:
                tn += f"/{track.total_tracks}"
            audio.add(TRCK(encoding=3, text=tn))
        if track.disc_number:
            audio.add(TPOS(encoding=3, text=str(track.disc_number)))
        if track.genres:
            audio.add(TCON(encoding=3, text=", ".join(track.genres)))
        if track.isrc:
            audio.add(TSRC(encoding=3, text=track.isrc))
        if track.upc:
            audio.add(TXXX(encoding=3, desc="BARCODE", text=track.upc))
        if track.iswc:
            audio.add(TXXX(encoding=3, desc="ISWC", text=track.iswc))
        if track.audio_traits:
            audio.add(TXXX(encoding=3, desc="AUDIO_TRAITS", text=", ".join(track.audio_traits)))
        if track.spotify_id:
            audio.add(TXXX(encoding=3, desc="SPOTIFYID", text=track.spotify_id))
        if track.composer:
            audio.add(TCOM(encoding=3, text=track.composer))
        lbl = getattr(track, "label", None)
        if lbl:
            audio.add(TPUB(encoding=3, text=lbl))

        # Artwork
        artwork = self._fetch_artwork(track.artwork_url)
        if artwork:
            artwork_data, mime, _width, _height, _depth = artwork
            audio.delall("APIC")
            audio.add(APIC(
                encoding=3,
                mime=mime,
                type=3,
                desc="Cover",
                data=artwork_data,
            ))

        # Save as ID3v2.3 instead of mutagen's native v2.4 so Windows Explorer
        # and standard mobile players can reliably read the Cover Art APIC frame.
        audio.save(path, v1=2, v2_version=3)

    # ── MP4 / M4A ────────────────────────────────────────────────────────────

    def _tag_mp4(self, path: str, track: TrackMetadata):
        audio = MP4(path)

        audio["\xa9nam"] = [track.title]
        audio["\xa9ART"] = track.artists
        album_artist_str = ", ".join(track.album_artists) if track.album_artists else track.primary_artist
        audio["aART"] = [album_artist_str]
        audio["\xa9alb"] = [track.album]

        date_str = track.release_date or (str(track.release_year) if track.release_year else None)
        if date_str:
            audio["\xa9day"] = [date_str]
        if track.track_number:
            audio["trkn"] = [(track.track_number, track.total_tracks or 0)]
        if track.disc_number:
            audio["disk"] = [(track.disc_number, 0)]
        if track.genres:
            audio["\xa9gen"] = track.genres
        if track.spotify_id:
            audio["----:com.apple.iTunes:SPOTIFYID"] = [MP4FreeForm(track.spotify_id.encode("utf-8"))]
        if track.isrc:
            audio["----:com.apple.iTunes:ISRC"] = [MP4FreeForm(track.isrc.encode("utf-8"))]
        if track.composer:
            audio["\xa9wrt"] = [track.composer]
        lbl = getattr(track, "label", None)
        if lbl:
            audio["----:com.apple.iTunes:LABEL"] = [MP4FreeForm(lbl.encode("utf-8"))]

        artwork = self._fetch_artwork(track.artwork_url)
        if artwork:
            artwork_data, mime, _width, _height, _depth = artwork
            image_format = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
            audio["covr"] = [MP4Cover(artwork_data, imageformat=image_format)]

        audio.save()

    # ── Helpers ───────────────────────────────────────────────────────────

    def _fetch_artwork(self, url: Optional[str]) -> Optional[_Artwork]:
        if not url:
            return None
        with self._artwork_lock:
            if url in self._artwork_cache:
                result = self._artwork_cache.pop(url)
                self._artwork_cache[url] = result
                return result
            flight = self._artwork_flights.get(url)
            if flight is None:
                flight = _ArtworkFlight()
                self._artwork_flights[url] = flight
                owner = True
            else:
                owner = False

        if not owner:
            flight.event.wait()
            return flight.result

        result: Optional[_Artwork] = None
        try:
            for attempt in range(3):
                try:
                    resp = requests.get(url, timeout=10)
                    resp.raise_for_status()
                    result = self._prepare_artwork_content_once(
                        bytes(resp.content),
                        resp.headers.get("Content-Type"),
                    )
                    if result is None:
                        raise ValueError("artwork could not be decoded")
                    break
                except Exception as exc:
                    if attempt < 2:
                        time.sleep(1.5 ** attempt)
                    else:
                        logger.warning("Failed to download artwork from %s: %s", url, exc)
        finally:
            with self._artwork_lock:
                self._remember_artwork(self._artwork_cache, url, result)
                self._artwork_flights.pop(url, None)
                flight.result = result
                flight.event.set()
        return result

    def _prepare_artwork_content_once(
        self,
        data: bytes,
        response_mime: Optional[str],
    ) -> Optional[_Artwork]:
        content_key = (hashlib.sha256(data).hexdigest(), _sniff_image_mime(data, response_mime))
        with self._artwork_lock:
            if content_key in self._artwork_content_cache:
                result = self._artwork_content_cache.pop(content_key)
                self._artwork_content_cache[content_key] = result
                return result
            flight = self._artwork_content_flights.get(content_key)
            if flight is None:
                flight = _ArtworkFlight()
                self._artwork_content_flights[content_key] = flight
                owner = True
            else:
                owner = False

        if not owner:
            flight.event.wait()
            return flight.result

        result: Optional[_Artwork] = None
        try:
            result = self._normalize_artwork(data, response_mime)
        except Exception as exc:
            logger.debug("Failed to decode artwork content: %s", exc)
        finally:
            with self._artwork_lock:
                self._remember_artwork(self._artwork_content_cache, content_key, result)
                self._artwork_content_flights.pop(content_key, None)
                flight.result = result
                flight.event.set()
        return result

    def _remember_artwork(self, cache: OrderedDict, key, value) -> None:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > self._artwork_cache_entries:
            cache.popitem(last=False)

    @staticmethod
    def _existing_cover_sidecar(folder: str) -> Optional[str]:
        for name in ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.png"):
            path = os.path.join(folder, name)
            if os.path.exists(path):
                return path
        return None

    def _fetch_raw_artwork(self, url: Optional[str]) -> Optional[tuple[bytes, str]]:
        artwork = self._fetch_artwork(url)
        if not artwork:
            return None
        data, mime, _width, _height, _depth = artwork
        return data, mime

    @staticmethod
    def _prepare_sidecar_artwork(data: bytes, response_mime: Optional[str]) -> tuple[bytes, str]:
        mime = _sniff_image_mime(data, response_mime)
        if Image is None:
            return data, mime

        image = Image.open(BytesIO(data))
        image.load()
        if mime == "image/png" and image.mode in ("RGBA", "LA"):
            out = BytesIO()
            image.save(out, format="PNG", optimize=True)
            return out.getvalue(), "image/png"

        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
            mime = "image/jpeg"

        if mime == "image/png":
            out = BytesIO()
            image.save(out, format="PNG", optimize=True)
            return out.getvalue(), "image/png"

        out = BytesIO()
        image = image.convert("RGB")
        image.save(out, format="JPEG", quality=95, optimize=True)
        return out.getvalue(), "image/jpeg"

    @staticmethod
    def _normalize_artwork(data: bytes, response_mime: Optional[str]) -> tuple[bytes, str, int, int, int]:
        mime = _sniff_image_mime(data, response_mime)
        if Image is None:
            return data, mime, 0, 0, 0

        image = Image.open(BytesIO(data))
        image.load()
        width, height = image.size
        if max(width, height) > FileTagger._MAX_EMBEDDED_ART_EDGE:
            resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
            image.thumbnail(
                (FileTagger._MAX_EMBEDDED_ART_EDGE, FileTagger._MAX_EMBEDDED_ART_EDGE),
                resampling,
            )
            width, height = image.size

        if mime == "image/png" and image.mode in ("RGBA", "LA"):
            image = image.convert("RGB")
            mime = "image/jpeg"
        elif image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
            mime = "image/jpeg"

        if mime == "image/png":
            out = BytesIO()
            image.save(out, format="PNG", optimize=True)
            depth = 32 if image.mode == "RGBA" else 8
            return out.getvalue(), "image/png", width, height, depth

        out = BytesIO()
        image = image.convert("RGB")
        image.save(out, format="JPEG", quality=90, optimize=True)
        return out.getvalue(), "image/jpeg", width, height, 24

    @staticmethod
    def _write_lyrics_sidecars(file_path: str, track: TrackMetadata):
        """
        Writes .lrc and .txt sidecar files for external players.

        Skipped for formats that already carry lyrics in their tags:
          - FLAC  → SYNCEDLYRICS / LYRICS VorbisComment tags
          - MP3   → SYLT (synced) / USLT (plain) ID3 frames
          - M4A   → ©lyr atom
        For any other extension (e.g. .ogg, .opus) sidecars are still written.
        """
        ext = os.path.splitext(file_path)[1].lower()

        # These formats have embedded lyrics written by embed_lyrics(); no sidecar needed.
        _EMBEDDED_FORMATS = {".flac", ".mp3", ".m4a", ".mp4", ".aac"}

        if ext in _EMBEDDED_FORMATS:
            return

        base, _ = os.path.splitext(file_path)

        if track.synced_lyrics:
            try:
                with open(base + ".lrc", "w", encoding="utf-8") as handle:
                    handle.write(track.synced_lyrics)
            except Exception as e:
                logger.warning(f"Failed to write synced lyrics sidecar for {file_path}: {e}")

        if track.lyrics:
            try:
                with open(base + ".txt", "w", encoding="utf-8") as handle:
                    handle.write(track.lyrics)
            except Exception as e:
                logger.warning(f"Failed to write plain lyrics sidecar for {file_path}: {e}")

    def embed_lyrics(
        self,
        path: str,
        lyrics: str = "",
        synced_lyrics: str = "",
        duration_ms: int = 0,
    ) -> None:
        """
        Embed lyrics into audio file metadata.
        Prioritizes synced (LRC) lyrics over plain text.
        Validates LRC timestamps against duration before embedding.
        """
        # Validate and clean LRC if we have duration info
        if synced_lyrics and duration_ms:
            synced_lyrics = validate_and_strip_lrc(synced_lyrics, duration_ms)

        # Best available lyric string for formats that don't support synced
        best_plain = synced_lyrics or lyrics  # LRC is readable as plain text too

        ext = os.path.splitext(path)[1].lower()

        try:
            if ext == ".flac":
                audio = FLAC(path)
                if synced_lyrics:
                    audio["SYNCEDLYRICS"] = [synced_lyrics]
                if lyrics:
                    audio["LYRICS"] = [lyrics]
                elif synced_lyrics:
                    audio["LYRICS"] = [synced_lyrics]
                audio.save()

            elif ext == ".mp3":
                try:
                    audio = ID3(path)
                except Exception:
                    audio = ID3()
                # Plain/unsynced lyrics
                best = lyrics or synced_lyrics
                if best:
                    audio.add(USLT(
                        encoding=Encoding.UTF8,
                        lang="eng",
                        desc="",
                        text=best,
                    ))
                # Synced lyrics via SYLT
                if synced_lyrics:
                    frames = lrc_to_sylt_frames(synced_lyrics)
                    if frames:
                        audio.add(SYLT(
                            encoding=Encoding.UTF8,
                            lang="eng",
                            format=2,   # milliseconds
                            type=1,     # lyrics
                            desc="",
                            text=frames,
                        ))
                audio.save(path, v1=2, v2_version=3)

            elif ext in (".m4a", ".mp4", ".aac"):
                audio = MP4(path)
                # M4A only supports plain text lyrics via \xa9lyr atom
                # Use LRC as plain text if no plain lyrics available
                if best_plain:
                    audio["\xa9lyr"] = [best_plain]
                audio.save()

            else:
                from logging import getLogger
                getLogger(__name__).debug(f"[Tagger] Unsupported format for lyric embedding: {ext}")

        except Exception as e:
            from logging import getLogger
            getLogger(__name__).warning(f"[Tagger] Failed to embed lyrics into {path}: {e}")
