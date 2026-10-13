# Video Downloader

A small Tkinter desktop app that downloads videos (MX Player, YouTube, and
anything else [yt-dlp](https://github.com/yt-dlp/yt-dlp) supports) straight
from a URL, showing a live progress bar and a "completed" dialog.

## Setup

```bash
pip install -r requirements.txt
```

You also need **ffmpeg** on your PATH for best-video + best-audio merging:
<https://www.gyan.dev/ffmpeg/builds/> (add the `bin` folder to PATH).

## Run

```bash
python downloader_ui.py
```

## Usage

1. **Download URL** – paste a video page URL **or a playlist URL** (e.g. an
   MX Player episode link or a YouTube `/playlist?list=...` link).
2. **Target folder** – choose where the file(s) should be saved (defaults to
   `~/Downloads/Videos`).
3. **Output type** – choose **Video** (merged MKV, up to 1080p) or
   **Audio (MP3)** (best audio track converted to a 192 kbps MP3).
4. Click **Download** – the progress bar fills while downloading; the status
   line shows percent / speed / ETA (for playlists it also shows
   `[current/total]`).
5. A **"Download completed successfully"** dialog pops up when done, and the
   **Open Folder** button becomes available.

### Single video vs playlist

* **Single video URL** (e.g. `watch?v=ABC123`, even with a `list=` parameter)
  → that one video is saved to the target folder.
* **Playlist URL** (e.g. `/playlist?list=PL...`) → every video in the
  playlist is saved to the target folder; the progress bar shows which item
  is downloading and the completion dialog reports how many videos were saved.

## Files

| File            | Purpose                                          |
| --------------- | ------------------------------------------------ |
| `downloader_ui.py`        | Tkinter UI (URL field, folder picker, radio buttons, progress) |
| `downloader.py` | yt-dlp wrapper with progress callbacks (single videos + playlists) |

## Notes

* Downloads are capped at **1080p** (`downloader.MAX_HEIGHT`) and saved as
  **MKV** with the best video+audio merged (dual audio tracks are preserved).
* In **Audio (MP3)** mode each item's best audio track is extracted and
  converted with ffmpeg to a **192 kbps MP3**.
* A single video inside a playlist link (`watch?v=..&list=..`) still downloads
  only that one video; an explicit `/playlist?list=..` URL downloads the whole
  playlist. Unavailable/private entries in a playlist are skipped so one dead
  link doesn't stop the rest.
* As with any streaming site, check the platform's Terms of Service before
  saving content — MX Player does not offer an official download feature.