"""Video Downloader - a small Tkinter front-end for yt-dlp.

Features:
    * URL input
    * Target folder picker (with Browse button)
    * Video / Audio (MP3) output radio buttons
    * "Download" button that saves with a live progress bar
    * Single videos and playlists (a playlist URL saves every video
      into the chosen folder)
    * "Completed" / error dialogs
    * "Open Folder" button once a download has finished

Run with:  python downloader_ui.py
"""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from downloader import ProgressState, download_media

APP_TITLE = "Video Downloader"
REFRESH_MS = 60  # throttle UI updates while the download is running


def _human(n: Optional[float]) -> str:
    """Format a byte count (or seconds) sensibly."""
    if n is None:
        return "?"
    n = float(n)
    if n < 0:
        return "?"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} TiB"


def _human_eta(seconds: Optional[float]) -> str:
    if seconds is None:
        return "?"
    seconds = int(seconds)
    m, s = divmod(max(seconds, 0), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


class MovieDownloaderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title(APP_TITLE)
        root.minsize(560, 370)
        root.resizable(False, False)

        self.url_var = tk.StringVar()
        # Sensible default target folder: ~/Downloads/Videos
        self.folder_var = tk.StringVar(
            value=os.path.join(os.path.expanduser("~"), "Downloads", "Videos")
        )

        self.status_var = tk.StringVar(value="Ready.")
        self.status_var.set(
            "Ready. Paste a video or playlist URL and press Download."
        )

        # Internal download state (only touched from the worker thread).
        self._latest: ProgressState = ProgressState()
        self._ui_update_queued = False
        self._thread: threading.Thread | None = None
        self._downloading = False
        # Folder containing the last finished download (single file or playlist).
        self._last_folder: str | None = None

        self._build_widgets()

        # Fit the window to its contents exactly (fonts/DPI dependent) so no
        # widget ever gets clipped, instead of relying on a hard-coded size.
        root.update_idletasks()
        root.geometry(f"{root.winfo_reqwidth()}x{root.winfo_reqheight()}")

    # ------------------------------------------------------------------ UI
    def _build_widgets(self) -> None:
        pad = {"padx": 10, "pady": 6}

        main = ttk.Frame(self.root, padding=16)
        main.pack(fill="both", expand=True)

        # URL row
        ttk.Label(main, text="Download URL").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(main, textvariable=self.url_var, width=60).grid(
            row=0, column=1, columnspan=2, sticky="ew", **pad
        )

        # Target folder row
        ttk.Label(main, text="Target folder").grid(
            row=1, column=0, sticky="w", **pad
        )
        ttk.Entry(main, textvariable=self.folder_var, width=48).grid(
            row=1, column=1, sticky="ew", **pad
        )
        ttk.Button(main, text="Browse...", command=self._browse).grid(
            row=1, column=2, **pad
        )

        # Output type radios (video by default, audio asks for an MP3).
        ttk.Label(main, text="Output type").grid(
            row=2, column=0, sticky="w", **pad
        )
        self.mode_var = tk.StringVar(value="video")
        ttk.Radiobutton(
            main, text="Video", variable=self.mode_var, value="video"
        ).grid(row=2, column=1, sticky="w", **pad)
        ttk.Radiobutton(
            main, text="Audio (MP3)", variable=self.mode_var, value="audio"
        ).grid(row=2, column=2, sticky="w", **pad)

        main.rowconfigure(5, weight=1)

        # Fetch button row
        self.fetch_btn = ttk.Button(
            main, text="Download", command=self._start_download
        )
        self.fetch_btn.grid(row=3, column=1, sticky="ew", **pad)

        self.open_btn = ttk.Button(
            main, text="Open Folder", command=self._open_folder, state="disabled"
        )
        self.open_btn.grid(row=3, column=2, sticky="ew", **pad)

        # Progress bar + status
        self.progress = ttk.Progressbar(main, mode="determinate", maximum=100.0)
        self.progress.grid(row=4, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(main, textvariable=self.status_var, anchor="w").grid(
            row=5, column=0, columnspan=3, sticky="w", **pad
        )

        self.root.bind("<Return>", lambda _e: self._start_download())

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(
            title="Choose a target folder",
            initialdir=self.folder_var.get() or os.path.expanduser("~"),
        )
        if chosen:
            self.folder_var.set(chosen)

    def _open_folder(self) -> None:
        folder = self._last_folder or self.folder_var.get()
        if folder and os.path.isdir(folder):
            try:
                os.startfile(folder)  # Windows
            except AttributeError:
                import subprocess
                import sys

                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.Popen([opener, folder])
        else:
            messagebox.showwarning(
                APP_TITLE, f"Folder does not exist:\n{folder}"
            )

    # ------------------------------------------------------------- actions
    def _start_download(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning(APP_TITLE, "Please enter a video URL first.")
            return
        if self._downloading:
            return

        self._downloading = True
        self._latest = ProgressState(status="starting")
        self.fetch_btn.config(state="disabled")
        self.open_btn.config(state="disabled")
        self.status_var.set("Starting...")
        self.progress.config(mode="determinate", value=0.0)

        self._thread = threading.Thread(
            target=self._worker,
            args=(url, self.folder_var.get().strip(), self.mode_var.get()),
            daemon=True,
        )
        self._thread.start()

    def _worker(self, url: str, folder: str, media_type: str) -> None:
        try:
            paths = download_media(
                url, folder, on_progress=self._on_progress, media_type=media_type
            )
            self.root.after(0, self._on_done, paths, folder)
        except Exception as exc:  # noqa: BLE001 - surface any failure
            self.root.after(0, self._on_error, str(exc) or type(exc).__name__)

    # ------------------------------------------------------- progress glue
    def _on_progress(self, state: ProgressState) -> None:
        """Called from the worker thread; coalesce UI refreshes."""
        self._latest = state
        if not self._ui_update_queued:
            self._ui_update_queued = True
            self.root.after(REFRESH_MS, self._apply_progress)

    def _apply_progress(self) -> None:
        """Run on the main thread; refresh widgets from self._latest."""
        self._ui_update_queued = False
        st = self._latest

        if st.status == "downloading":
            if st.total_bytes:
                self.progress.config(mode="determinate", value=st.percent)
                size_txt = (
                    f"{_human(st.downloaded_bytes)} of {_human(st.total_bytes)}"
                )
            else:
                self.progress.config(mode="indeterminate")
                self.progress.start(12)
                size_txt = _human(st.downloaded_bytes)
            label = st.title or ""
            if st.playlist_index:
                if st.playlist_count:
                    label = f"[{st.playlist_index}/{st.playlist_count}] {label}"
                else:
                    label = f"[{st.playlist_index}] {label}"
            self.status_var.set(
                f"Downloading {label} \u2014 {st.percent:.1f}% "
                f"({size_txt})  {_human(st.speed)}/s  ETA {_human_eta(st.eta)}"
            )
        elif st.status == "converting":
            # yt-dlp is post-processing the best audio into MP3 (audio mode):
            # the download finished, so animate the bar until it completes.
            self.progress.config(mode="indeterminate")
            self.progress.start(12)
            label = "Converting to MP3\u2026"
            if st.playlist_index:
                if st.playlist_count:
                    label = f"[{st.playlist_index}/{st.playlist_count}] {label}"
                else:
                    label = f"[{st.playlist_index}] {label}"
            self.status_var.set(label)
        elif st.status == "finished":
            self.progress.stop()
            self.progress.config(mode="determinate", value=100.0)
            if self.mode_var.get() == "audio":
                self.status_var.set("Converting to MP3\u2026")
            else:
                self.status_var.set("Finalizing / merging streams\u2026")
        else:  # starting / unknown
            self.status_var.set("Fetching video info\u2026")

    def _on_done(self, paths, folder: str) -> None:
        self._downloading = False
        self._last_folder = folder or self.folder_var.get()
        self.progress.stop()
        self.progress.config(mode="determinate", value=100.0)
        self.fetch_btn.config(state="normal")
        self.open_btn.config(state="normal")

        if isinstance(paths, str):  # defensive: tolerate a single path
            paths = [paths]
        if not paths:
            self._on_error("No files were written.")
            return
        item_word = "MP3 file" if self.mode_var.get() == "audio" else "video"
        if len(paths) == 1:
            self.status_var.set(f"Completed \u2713  {os.path.basename(paths[0])}")
            messagebox.showinfo(
                APP_TITLE,
                f"Download completed successfully.\n\n{item_word} saved to:\n"
                + paths[0],
            )
        else:
            item_word += "s"
            self.status_var.set(
                f"Completed \u2713  {len(paths)} {item_word} downloaded"
            )
            messagebox.showinfo(
                APP_TITLE,
                f"Downloaded {len(paths)} {item_word} successfully.\n\n"
                f"Saved to:\n{self._last_folder}",
            )

    def _on_error(self, message: str) -> None:
        self._downloading = False
        self.progress.stop()
        self.progress.config(mode="determinate", value=0.0)
        self.status_var.set(f"Failed \u2717  {message}")
        self.fetch_btn.config(state="normal")
        messagebox.showerror(APP_TITLE, f"Download failed:\n\n{message}")


def main() -> None:
    root = tk.Tk()
    MovieDownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()