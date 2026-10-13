"""Core download logic for the Video Downloader GUI.

Wraps yt-dlp's Python API so the Tkinter UI can receive fine-grained
progress callbacks (bytes, speed, ETA, percent) while downloading
services such as YouTube, MX Player, etc.

The same function handles single videos AND playlists. yt-dlp's URL
routing decides which kind of link was pasted:

    * https://.../watch?v=ABC123             -> that single video
    * https://.../watch?v=ABC123&list=PL...  -> just that single video
    * https://.../playlist?list=PL...        -> every video in the playlist

Usage:
    from downloader import download_media, ProgressState, DownloadError

    def on_progress(state: ProgressState) -> None:
        print(state.percent, state.status)

    paths = download_media("https://...", r"D:\\Videos", on_progress)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Literal, Optional


class DownloadError(Exception):
    """Raised when the URL is invalid or the download fails for any reason."""


@dataclass
class ProgressState:
    """Immutable snapshot describing an in-flight yt-dlp download."""

    status: str = ""  # one of: starting | downloading | finished | error
    downloaded_bytes: float = 0.0
    total_bytes: Optional[float] = None
    speed: Optional[float] = None  # bytes / second
    eta: Optional[float] = None  # seconds
    percent: float = 0.0  # 0.0 - 100.0
    title: Optional[str] = None
    filename: Optional[str] = None
    # Only populated while downloading a playlist:
    playlist: Optional[str] = None  # playlist title (or id)
    playlist_index: Optional[int] = None  # 1-based position of the entry
    playlist_count: Optional[int] = None  # total entries in the playlist


# A callback receiving a fresh ProgressState after every yt-dlp hook event.
ProgressCallback = Callable[[ProgressState], None]


# Maximum height (pixels) we allow so we do not download 4K by accident
# on services that offer it. 1080p is plenty for normal viewing.
MAX_HEIGHT = 1080


class _ErrorLog:
    """Minimal yt-dlp logger that collects error messages instead of printing.

    yt-dlp calls ``logger.error(...)`` for every failed video/entry. We buffer
    those so that, if nothing could be downloaded at all, the UI can show why
    (e.g. sign-in required, video unavailable, etc.).
    """

    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        self.messages.append(msg)

    def last_error(self) -> Optional[str]:
        return self.messages[-1] if self.messages else None


def _make_hook(on_progress: ProgressCallback):
    """Translate yt-dlp progress-hook dicts into ProgressState callbacks."""

    def hook(d: dict) -> None:
        status = d.get("status", "")
        state = ProgressState(status=status)
        info = d.get("info_dict", {}) or {}
        state.title = info.get("title")
        state.filename = d.get("filename")
        state.playlist = info.get("playlist") or info.get("playlist_title")
        state.playlist_index = info.get("playlist_index")
        state.playlist_count = info.get("playlist_count")

        if status == "downloading":
            downloaded = d.get("downloaded_bytes") or 0.0
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            state.downloaded_bytes = float(downloaded)
            state.total_bytes = float(total) if total else None
            state.speed = d.get("speed")
            state.eta = d.get("eta")
            if state.total_bytes:
                state.percent = min(100.0, downloaded / state.total_bytes * 100.0)
        elif status == "finished":
            state.percent = 100.0

        on_progress(state)

    return hook


def _make_postprocess_hook(on_progress: ProgressCallback):
    """Translate yt-dlp postprocessor-hook dicts into ProgressState callbacks.

    Used in audio mode so the UI can animate the progress bar while the
    best-audio track is being converted to MP3 by ffmpeg. yt-dlp guarantees
    exactly one ``started`` and one ``finished`` call per post-processor.
    """

    def hook(d: dict) -> None:
        status = d.get("status", "")
        if status not in ("started", "finished"):
            return  # ignore any unknown/lower-level events
        state = ProgressState(status="converting")
        info = d.get("info_dict", {}) or {}
        state.title = info.get("title")
        state.playlist = info.get("playlist") or info.get("playlist_title")
        state.playlist_index = info.get("playlist_index")
        state.playlist_count = info.get("playlist_count")
        on_progress(state)

    return hook


def download_media(
    url: str,
    target_dir: str,
    on_progress: Optional[ProgressCallback] = None,
    media_type: Literal["video", "audio"] = "video",
) -> list[str]:
    """Download every video that ``url`` refers to into ``target_dir``.

    A single-video URL writes one file; a playlist URL writes one file for
    every video in the playlist. See the module docstring for the exact
    autodetection rules.

    When ``media_type`` is ``"video"`` (default) each item is saved as a
    merged MKV; when it is ``"audio"`` the best audio track of each item is
    extracted and converted to a standalone MP3 file.

    Returns:
        The absolute paths of all written files.

    Raises:
        DownloadError: if the URL is empty, nothing could be written, or
            yt-dlp cannot complete the job.
    """
    import yt_dlp  # imported lazily so the GUI imports fast

    if not url or not url.strip():
        raise DownloadError("Please enter a URL first.")

    audio_only = media_type == "audio"

    try:
        os.makedirs(target_dir, exist_ok=True)
    except OSError as exc:
        raise DownloadError(f"Could not create target folder:\n{exc}") from exc

    error_log = _ErrorLog()
    ydl_opts = {
        "outtmpl": os.path.join(target_dir, "%(title)s [%(id)s].%(ext)s"),
        # Video mode: best video (up to 1080p) + best audio, merged with
        # ffmpeg. Audio mode: just the best audio track (it is converted to
        # an MP3 by the postprocessor below).
        "format": (
            f"bestvideo[height<={MAX_HEIGHT}]+bestaudio/best"
            if not audio_only
            else "bestaudio/best"
        ),
        # Auto-detection: with `noplaylist` on, a URL that references a video
        # *and* a playlist (watch?v=..&list=..) downloads only that single
        # video, while an explicit playlist URL (/playlist?list=..) still
        # downloads every entry. See the module docstring for examples.
        "noplaylist": True,
        # One unavailable/private video should not abort an entire playlist;
        # yt-dlp skips the entry and continues. Its messages go to `error_log`
        # so we can still explain the failure if nothing gets written at all.
        "ignoreerrors": True,
        "quiet": True,
        "no_warnings": True,
        "windowsfilenames": True,
        "logger": error_log,
        "progress_hooks": [_make_hook(on_progress or (lambda _st: None))],
    }
    if audio_only:
        # Convert the downloaded best audio to a single MP3 file at 192 kbps.
        ydl_opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
        # Keep the UI appraised while ffmpeg performs the conversion so it can
        # animate the progress bar (the file is fully downloaded at this point).
        ydl_opts["postprocessor_hooks"] = [
            _make_postprocess_hook(on_progress or (lambda _st: None))
        ]
    else:
        # Containers that may be used when merging formats.
        ydl_opts["merge_output_format"] = "mkv"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise DownloadError(_first_line(str(exc))) from exc
    except Exception as exc:  # noqa: BLE001 - surface all failures to the user
        raise DownloadError(str(exc) or type(exc).__name__) from exc

    written = _collect_written_files(info)
    if not written:
        raise DownloadError(_nothing_written_message(error_log))
    return [os.path.abspath(path) for path in written]


def _collect_written_files(info: Optional[dict]) -> list[str]:
    """Return the file paths yt-dlp reported as written for ``info``.

    ``extract_info`` returns a single info dict for a video but a playlist
    dict (with an ``entries`` list) for a playlist. With ``ignoreerrors`` a
    failed entry is ``None`` and a completely failed request returns ``None``.

    For each entry we return at most one path: the final file after all
    post-processing. ``entry['filepath']`` is updated by every post-processor
    (``.mkv`` after the video merge, ``.mp3`` after audio extraction), while
    the per-format ``requested_downloads`` paths point at the *intermediate*
    files that get deleted afterwards, so they are only used as a fallback.
    """
    written: list[str] = []
    for entry in info.get("entries") or [info] if info else []:  # single video == one entry
        if not entry:
            continue
        # Priority: final post-processed path, legacy '_filename', then any
        # still-existing per-format file (in case no post-processing ran).
        candidates: list[str] = []
        for key in ("filepath", "_filename"):
            if entry.get(key):
                candidates.append(entry[key])
        for item in entry.get("requested_downloads") or []:
            fp = item.get("filepath")
            if fp and fp not in candidates:
                candidates.append(fp)
        for fp in candidates:
            if os.path.exists(fp):
                written.append(fp)
                break
    return written


def _nothing_written_message(error_log: _ErrorLog) -> str:
    """Build a readable error when yt-dlp ended with zero output files."""
    message = (
        "No videos were downloaded. The URL may be invalid, the video(s) may "
        "be private or region-locked, or the service may be blocking requests."
    )
    last = error_log.last_error()
    if last:
        return message + "\n\n" + last
    return message


def download_video(
    url: str,
    target_dir: str,
    on_progress: Optional[ProgressCallback] = None,
    media_type: Literal["video", "audio"] = "video",
) -> str:
    """Backward-compatible wrapper around :func:`download_media`.

    Returns the path of the first written file. Prefer ``download_media``
    when the URL may point to a playlist.
    """
    return download_media(url, target_dir, on_progress, media_type)[0]


def _first_line(text: str) -> str:
    """Make multi-line yt-dlp error messages readable in a dialog box."""
    return text.strip().splitlines()[0] if text.strip() else "Download failed."