"""Custom tkinter widgets for CSI Tool."""

import tkinter as tk
import tkinter.ttk as ttk
from pathlib import Path

from . import styles
from ..core.models import BurstFile
from ..utils.file_helpers import human_readable_size


class FileListWidget(ttk.Frame):
    """Treeview listing loaded burst CR3 files."""

    def __init__(self, parent, on_select=None, **kwargs):
        super().__init__(parent, **kwargs)
        self._on_select = on_select
        self._burst_files: dict[str, BurstFile] = {}  # iid -> BurstFile

        # Treeview
        columns = ("frames", "size", "camera", "status")
        self.tree = ttk.Treeview(self, columns=columns, show="headings",
                                 selectmode="extended")
        self.tree.heading("frames", text="Frames")
        self.tree.heading("size", text="Size")
        self.tree.heading("camera", text="Camera")
        self.tree.heading("status", text="Status")

        self.tree.column("frames", width=70, anchor="center")
        self.tree.column("size", width=90, anchor="e")
        self.tree.column("camera", width=140)
        self.tree.column("status", width=90, anchor="center")

        # Scrollbar
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Bind selection
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

    def add_file(self, burst_file: BurstFile) -> None:
        """Add a burst file to the list."""
        status = "Ready" if burst_file.is_valid else "Error"
        iid = self.tree.insert("", "end", values=(
            burst_file.frame_count,
            human_readable_size(burst_file.file_size),
            burst_file.camera_model,
            status,
        ), text=burst_file.filename)
        self._burst_files[iid] = burst_file

    def remove_selected(self) -> None:
        """Remove selected items from the list."""
        for iid in self.tree.selection():
            self._burst_files.pop(iid, None)
            self.tree.delete(iid)

    def get_selected(self) -> list[BurstFile]:
        """Return list of currently selected burst files."""
        return [self._burst_files[iid] for iid in self.tree.selection()
                if iid in self._burst_files]

    def get_all(self) -> list[BurstFile]:
        """Return all burst files in the list."""
        return list(self._burst_files.values())

    def clear(self) -> None:
        """Remove all items."""
        self.tree.delete(*self.tree.get_children())
        self._burst_files.clear()

    def set_status(self, burst_file: BurstFile, status: str) -> None:
        """Update the status column for a burst file."""
        for iid, bf in self._burst_files.items():
            if bf is burst_file:
                self.tree.set(iid, "status", status)
                break

    def _on_tree_select(self, event) -> None:
        if self._on_select:
            selected = self.get_selected()
            if selected:
                self._on_select(selected[0])


class ProgressWidget(ttk.Frame):
    """Progress bar and scrollable log area."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)

        # Progress bar row
        progress_frame = ttk.Frame(self)
        progress_frame.pack(fill="x", pady=(0, 4))

        self.progress_label = ttk.Label(progress_frame, text="Ready",
                                         style="Dim.TLabel")
        self.progress_label.pack(side="left")

        self.progress_bar = ttk.Progressbar(progress_frame, mode="determinate",
                                             length=200)
        self.progress_bar.pack(side="right", fill="x", expand=True, padx=(8, 0))

        # Log area
        self.log_text = tk.Text(
            self, height=8, wrap="word",
            bg=styles.BG_SECONDARY, fg=styles.FG_SECONDARY,
            font=styles.FONT_MONO, borderwidth=0, highlightthickness=0,
            insertbackground=styles.FG_PRIMARY, state="disabled",
            padx=8, pady=4,
        )
        self.log_text.pack(fill="both", expand=True)

        # Log text tags
        self.log_text.tag_configure("info", foreground=styles.FG_SECONDARY)
        self.log_text.tag_configure("success", foreground=styles.SUCCESS)
        self.log_text.tag_configure("error", foreground=styles.ERROR)
        self.log_text.tag_configure("warning", foreground=styles.WARNING)

    def set_progress(self, current: int, total: int, message: str = "") -> None:
        """Update progress bar and label."""
        if total > 0:
            pct = (current / total) * 100
            self.progress_bar["value"] = pct
            self.progress_label.configure(
                text=message or f"{current}/{total} ({pct:.0f}%)")
        else:
            self.progress_bar["value"] = 0
            self.progress_label.configure(text=message or "Ready")

    def append_log(self, message: str, level: str = "info") -> None:
        """Append a message to the log area."""
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n", level)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def reset(self) -> None:
        """Clear progress and log."""
        self.progress_bar["value"] = 0
        self.progress_label.configure(text="Ready")
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")


class StatusBar(ttk.Frame):
    """Bottom status bar showing extractor status and file count."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, style="Surface.TFrame", **kwargs)

        self.engine_label = ttk.Label(
            self,
            text="CR3 Engine: Built in",
            style="Success.TLabel",
            font=styles.FONT_SMALL,
        )
        self.engine_label.pack(side="left", padx=8, pady=4)

        ttk.Separator(self, orient="vertical").pack(side="left", fill="y", pady=4)

        self.file_count_label = ttk.Label(self, text="0 files",
                                           style="Dim.TLabel",
                                           font=styles.FONT_SMALL)
        self.file_count_label.pack(side="left", padx=8, pady=4)

    def set_engine_status(self, ready: bool, detail: str = "") -> None:
        if ready:
            text = f"CR3 Engine: {detail}" if detail else "CR3 Engine: Built in"
            self.engine_label.configure(text=text, style="Success.TLabel")
        else:
            text = f"CR3 Engine: {detail}" if detail else "CR3 Engine: Unavailable"
            self.engine_label.configure(text=text, style="Error.TLabel")

    def set_file_count(self, count: int) -> None:
        self.file_count_label.configure(
            text=f"{count} file{'s' if count != 1 else ''}")
