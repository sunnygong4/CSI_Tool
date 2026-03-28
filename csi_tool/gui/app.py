"""Main application window for CSI Tool."""

import logging
import os
import subprocess
import tkinter as tk
import tkinter.ttk as ttk
from pathlib import Path
from tkinter import filedialog, messagebox

from . import styles
from .styles import apply_dark_theme
from .widgets import FileListWidget, ProgressWidget, StatusBar
from ..core.cr3_parser import CR3Parser
from ..core.extractor import Extractor
from ..core.models import ExtractionJob
from ..utils.config import load_config, save_config
from ..utils.dnglab_finder import find_dnglab, get_dnglab_version

logger = logging.getLogger(__name__)

FORMAT_LABELS = {
    "dng": "Adobe DNG",
    "cr3": "Canon CR3",
}
FORMAT_CODES = {label: code for code, label in FORMAT_LABELS.items()}


class CSIToolApp:
    """Main GUI application."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("CSI Tool - Canon CR3 Burst Extractor")
        self.root.minsize(styles.WINDOW_MIN_WIDTH, styles.WINDOW_MIN_HEIGHT)
        self.root.geometry("980x670")

        self._set_dark_titlebar()
        apply_dark_theme(self.root)

        self.config = load_config()
        self.parser = CR3Parser()
        self.extractor = Extractor(self.config, self.parser)
        self._output_dir: Path | None = None
        self.format_var = tk.StringVar(value=FORMAT_LABELS.get(self.config.output_format, FORMAT_LABELS["dng"]))

        self._build_ui()
        self._update_engine_status()

    def _set_dark_titlebar(self):
        if os.name != "nt":
            return
        try:
            import ctypes

            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                20,
                ctypes.byref(ctypes.c_int(1)),
                ctypes.sizeof(ctypes.c_int),
            )
        except Exception:
            pass

    def _build_ui(self):
        pad = styles.PAD

        menubar = tk.Menu(
            self.root,
            bg=styles.BG_SURFACE,
            fg=styles.FG_PRIMARY,
            activebackground=styles.ACCENT,
            activeforeground=styles.BG_PRIMARY,
            borderwidth=0,
        )

        file_menu = tk.Menu(
            menubar,
            tearoff=0,
            bg=styles.BG_SURFACE,
            fg=styles.FG_PRIMARY,
            activebackground=styles.ACCENT,
            activeforeground=styles.BG_PRIMARY,
        )
        file_menu.add_command(label="Add Files...", command=self._on_add_files, accelerator="Ctrl+O")
        file_menu.add_command(label="Add Folder...", command=self._on_add_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)

        tools_menu = tk.Menu(
            menubar,
            tearoff=0,
            bg=styles.BG_SURFACE,
            fg=styles.FG_PRIMARY,
            activebackground=styles.ACCENT,
            activeforeground=styles.BG_PRIMARY,
        )
        tools_menu.add_command(label="Settings...", command=self._on_settings)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        help_menu = tk.Menu(
            menubar,
            tearoff=0,
            bg=styles.BG_SURFACE,
            fg=styles.FG_PRIMARY,
            activebackground=styles.ACCENT,
            activeforeground=styles.BG_PRIMARY,
        )
        help_menu.add_command(label="About", command=self._on_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)
        self.root.bind("<Control-o>", lambda event: self._on_add_files())

        toolbar = ttk.Frame(self.root)
        toolbar.pack(fill="x", padx=pad, pady=(pad, 4))

        ttk.Button(toolbar, text="Add Files", command=self._on_add_files).pack(side="left", padx=(0, 4))
        ttk.Button(toolbar, text="Add Folder", command=self._on_add_folder).pack(side="left", padx=(0, 4))
        ttk.Button(toolbar, text="Remove", command=self._on_remove).pack(side="left", padx=(0, 4))
        ttk.Button(toolbar, text="Clear All", command=self._on_clear).pack(side="left", padx=(0, 16))

        ttk.Button(toolbar, text="Output Folder...", command=self._on_set_output).pack(side="right", padx=(4, 0))
        self.output_label = ttk.Label(toolbar, text="Output: auto", style="Dim.TLabel")
        self.output_label.pack(side="right", padx=(0, 12))

        self.format_combo = ttk.Combobox(
            toolbar,
            textvariable=self.format_var,
            values=[FORMAT_LABELS["dng"], FORMAT_LABELS["cr3"]],
            width=20,
            state="readonly",
        )
        self.format_combo.pack(side="right", padx=(0, 6))
        self.format_combo.bind("<<ComboboxSelected>>", self._on_format_changed)
        ttk.Label(toolbar, text="Format:", style="Dim.TLabel").pack(side="right", padx=(0, 6))

        list_frame = ttk.LabelFrame(self.root, text="Burst CR3 Files")
        list_frame.pack(fill="both", expand=True, padx=pad, pady=(0, 4))

        self.file_list = FileListWidget(list_frame, on_select=self._on_file_selected)
        self.file_list.pack(fill="both", expand=True, padx=4, pady=4)

        action_frame = ttk.Frame(self.root)
        action_frame.pack(fill="x", padx=pad, pady=(0, 4))

        self.extract_all_btn = ttk.Button(
            action_frame,
            text="Extract All",
            style="Accent.TButton",
            command=self._on_extract_all,
        )
        self.extract_all_btn.pack(side="left", padx=(0, 8))

        self.extract_sel_btn = ttk.Button(
            action_frame,
            text="Extract Selected",
            command=self._on_extract_selected,
        )
        self.extract_sel_btn.pack(side="left", padx=(0, 8))

        self.cancel_btn = ttk.Button(
            action_frame,
            text="Cancel",
            command=self._on_cancel,
            state="disabled",
        )
        self.cancel_btn.pack(side="left")

        self.open_output_btn = ttk.Button(
            action_frame,
            text="Open Output Folder",
            command=self._on_open_output,
        )
        self.open_output_btn.pack(side="right")

        progress_frame = ttk.LabelFrame(self.root, text="Progress")
        progress_frame.pack(fill="both", padx=pad, pady=(0, 4))

        self.progress = ProgressWidget(progress_frame)
        self.progress.pack(fill="both", expand=True, padx=4, pady=4)

        self.status_bar = StatusBar(self.root)
        self.status_bar.pack(fill="x", side="bottom")

    def _current_output_format(self) -> str:
        return FORMAT_CODES.get(self.format_var.get(), self.config.output_format or "dng")

    def _update_engine_status(self):
        if self.extractor.has_dnglab and self.config.dnglab_path:
            version = get_dnglab_version(Path(self.config.dnglab_path))
            self.status_bar.set_engine_status(True, f"Adobe DNG ready ({version}) | Canon CR3 available")
        else:
            self.status_bar.set_engine_status(True, "Canon CR3 available | Adobe DNG unavailable")

    def _on_format_changed(self, _event=None):
        # Keep the toolbar choice session-scoped; Settings controls the saved default.
        return

    def _on_add_files(self):
        initial_dir = self.config.last_input_dir or str(Path.home())
        files = filedialog.askopenfilenames(
            title="Select CR3 Burst Files",
            initialdir=initial_dir,
            filetypes=[("Canon RAW", "*.CR3 *.cr3"), ("All files", "*.*")],
        )
        if not files:
            return

        self.config.last_input_dir = str(Path(files[0]).parent)
        save_config(self.config)

        added = 0
        for file_name in files:
            path = Path(file_name)
            burst = self.parser.parse(path)
            if burst.is_valid and burst.frame_count > 1:
                self.file_list.add_file(burst)
                self.progress.append_log(f"Added: {burst.filename} ({burst.frame_count} frames)", "info")
                added += 1
            elif burst.is_valid:
                self.progress.append_log(
                    f"Skipped: {path.name} (not a burst file - {burst.frame_count} frame)",
                    "warning",
                )
            else:
                self.progress.append_log(f"Error: {path.name} - {burst.error_message}", "error")

        self._update_file_count()
        if added > 0:
            self.progress.append_log(f"Added {added} burst file(s)", "success")

    def _on_add_folder(self):
        initial_dir = self.config.last_input_dir or str(Path.home())
        directory = filedialog.askdirectory(
            title="Select Folder with CR3 Files",
            initialdir=initial_dir,
        )
        if not directory:
            return

        self.config.last_input_dir = directory
        save_config(self.config)

        from ..utils.file_helpers import find_cr3_files

        cr3_files = find_cr3_files(Path(directory))
        if not cr3_files:
            self.progress.append_log(f"No .CR3 files found in {directory}", "warning")
            return

        added = 0
        for path in cr3_files:
            burst = self.parser.parse(path)
            if burst.is_valid and burst.frame_count > 1:
                self.file_list.add_file(burst)
                added += 1

        self._update_file_count()
        self.progress.append_log(f"Scanned {len(cr3_files)} files, added {added} burst file(s)", "info")

    def _on_remove(self):
        self.file_list.remove_selected()
        self._update_file_count()

    def _on_clear(self):
        self.file_list.clear()
        self._update_file_count()

    def _on_set_output(self):
        initial = self.config.last_output_dir or str(Path.home())
        directory = filedialog.askdirectory(title="Select Output Directory", initialdir=initial)
        if not directory:
            return

        self._output_dir = Path(directory)
        self.config.last_output_dir = directory
        save_config(self.config)

        display = str(self._output_dir)
        if len(display) > 40:
            display = "..." + display[-37:]
        self.output_label.configure(text=f"Output: {display}")

    def _on_file_selected(self, burst_file):
        self.progress.append_log(
            f"Selected: {burst_file.filename} - "
            f"{burst_file.frame_count} frames, {burst_file.camera_model}, {burst_file.capture_date}",
            "info",
        )

    def _on_extract_all(self):
        burst_files = self.file_list.get_all()
        if not burst_files:
            messagebox.showinfo("No Files", "Add some CR3 burst files first.")
            return
        self._run_extraction(burst_files)

    def _on_extract_selected(self):
        burst_files = self.file_list.get_selected()
        if not burst_files:
            messagebox.showinfo("No Selection", "Select files from the list first.")
            return
        self._run_extraction(burst_files)

    def _run_extraction(self, burst_files: list):
        output_format = self._current_output_format()
        if output_format == "dng" and not self.extractor.has_dnglab:
            messagebox.showerror(
                "DNGLab Required",
                "Adobe DNG output needs dnglab. Open Settings and set a valid dnglab.exe path.",
            )
            self._on_settings()
            return

        jobs = []
        for burst_file in burst_files:
            if self._output_dir:
                output_dir = self._output_dir
                if self.config.output_subfolder_per_burst:
                    output_dir = output_dir / burst_file.path.stem
            else:
                output_dir = burst_file.path.parent / f"{burst_file.path.stem}_extracted"

            jobs.append(
                ExtractionJob(
                    burst_file=burst_file,
                    output_dir=output_dir,
                    output_format=output_format,
                )
            )

        self._set_extracting(True)
        self.progress.reset()
        self.progress.append_log(
            f"Starting {FORMAT_LABELS.get(output_format, output_format.upper())} extraction for {len(jobs)} file(s)...",
            "info",
        )

        for burst_file in burst_files:
            self.file_list.set_status(
                burst_file,
                f"Extracting {FORMAT_LABELS.get(output_format, output_format.upper())}...",
            )

        def on_progress(current, total, message):
            self.root.after(0, self.progress.set_progress, current, total, message)
            self.root.after(0, self.progress.append_log, message, "info")

        def on_complete(completed_jobs):
            self.root.after(0, self._on_extraction_complete, completed_jobs)

        self.extractor.batch_extract(jobs, on_progress, on_complete)

    def _on_extraction_complete(self, jobs):
        self._set_extracting(False)

        total_extracted = 0
        failures = 0
        last_output = None
        output_format = jobs[0].output_format if jobs else self._current_output_format()

        for job in jobs:
            if job.status == "completed":
                count = len(job.extracted_files)
                total_extracted += count
                self.file_list.set_status(job.burst_file, f"Done ({count})")
                self.progress.append_log(
                    f"{job.burst_file.filename}: {count} {FORMAT_LABELS.get(output_format, output_format.upper())} file(s) extracted",
                    "success",
                )
                last_output = job.output_dir
            else:
                failures += 1
                self.file_list.set_status(job.burst_file, "Failed")
                self.progress.append_log(
                    f"{job.burst_file.filename}: FAILED - {job.error_message}",
                    "error",
                )

        self.progress.set_progress(
            1,
            1,
            f"Complete: {total_extracted} frames from {len(jobs) - failures} file(s)",
        )

        if last_output:
            self._last_output_dir = last_output

        if failures:
            messagebox.showwarning(
                "Extraction Complete",
                f"Extracted {total_extracted} {FORMAT_LABELS.get(output_format, output_format.upper())} files.\n"
                f"{failures} file(s) failed - check the log for details.",
            )
        else:
            messagebox.showinfo(
                "Extraction Complete",
                f"Successfully extracted {total_extracted} {FORMAT_LABELS.get(output_format, output_format.upper())} files!",
            )

    def _on_cancel(self):
        self.extractor.cancel()
        self.progress.append_log("Cancelling...", "warning")

    def _on_open_output(self):
        target = getattr(self, "_last_output_dir", None) or self._output_dir
        if target and target.exists():
            if os.name == "nt":
                os.startfile(str(target))
            else:
                subprocess.Popen(["xdg-open", str(target)])
        else:
            messagebox.showinfo("No Output", "No output folder to open yet. Extract some files first.")

    def _set_extracting(self, running: bool):
        state = "disabled" if running else "normal"
        self.extract_all_btn.configure(state=state)
        self.extract_sel_btn.configure(state=state)
        self.cancel_btn.configure(state="normal" if running else "disabled")
        self.format_combo.configure(state="disabled" if running else "readonly")

    def _on_settings(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Settings")
        dialog.geometry("540x300")
        dialog.configure(bg=styles.BG_PRIMARY)
        dialog.transient(self.root)
        dialog.grab_set()

        pad = 12

        ttk.Label(dialog, text="Default Output Format", style="Heading.TLabel").pack(
            anchor="w",
            padx=pad,
            pady=(pad, 4),
        )

        output_var = tk.StringVar(value=FORMAT_LABELS.get(self.config.output_format, FORMAT_LABELS["dng"]))
        format_combo = ttk.Combobox(
            dialog,
            textvariable=output_var,
            values=[FORMAT_LABELS["dng"], FORMAT_LABELS["cr3"]],
            width=22,
            state="readonly",
        )
        format_combo.pack(anchor="w", padx=pad, pady=(0, 12))

        ttk.Label(dialog, text="DNGLab Path", style="Heading.TLabel").pack(
            anchor="w",
            padx=pad,
            pady=(0, 4),
        )

        path_frame = ttk.Frame(dialog)
        path_frame.pack(fill="x", padx=pad, pady=(0, 4))

        path_var = tk.StringVar(value=self.config.dnglab_path or "")
        path_entry = ttk.Entry(path_frame, textvariable=path_var)
        path_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))

        def browse_dnglab():
            path = filedialog.askopenfilename(
                title="Select dnglab executable",
                filetypes=[("Executable", "*.exe"), ("All files", "*.*")],
            )
            if path:
                path_var.set(path)

        def auto_detect():
            found = find_dnglab(path_var.get().strip() or None)
            if found:
                path_var.set(str(found))
                status_label.configure(text=f"Found: {found}", style="Success.TLabel")
            else:
                status_label.configure(text="dnglab not found", style="Error.TLabel")

        ttk.Button(path_frame, text="Browse", command=browse_dnglab).pack(side="left")
        ttk.Button(path_frame, text="Auto-Detect", command=auto_detect).pack(side="left", padx=(4, 0))

        status_label = ttk.Label(dialog, text="", style="Dim.TLabel")
        status_label.pack(anchor="w", padx=pad, pady=(0, 8))

        subfolder_var = tk.BooleanVar(value=self.config.output_subfolder_per_burst)
        ttk.Checkbutton(
            dialog,
            text="Create subfolder per burst file",
            variable=subfolder_var,
        ).pack(anchor="w", padx=pad, pady=(8, 4))

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill="x", padx=pad, pady=pad, side="bottom")

        def on_save():
            self.config.output_format = FORMAT_CODES.get(output_var.get(), "dng")
            dnglab_value = path_var.get().strip()
            if dnglab_value:
                if not self.extractor.set_dnglab_path(dnglab_value):
                    messagebox.showerror(
                        "Invalid Path",
                        "That path does not point to a working dnglab executable.",
                        parent=dialog,
                    )
                    return
                self.config.dnglab_path = dnglab_value
            else:
                self.config.dnglab_path = None
                self.extractor._init_dnglab()

            self.config.output_subfolder_per_burst = subfolder_var.get()
            save_config(self.config)
            self.format_var.set(FORMAT_LABELS[self.config.output_format])
            self._update_engine_status()
            dialog.destroy()

        ttk.Button(btn_frame, text="Save", style="Accent.TButton", command=on_save).pack(
            side="right",
            padx=(4, 0),
        )
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="right")

    def _on_about(self):
        messagebox.showinfo(
            "About CSI Tool",
            "CSI Tool v1.1.0\n\n"
            "Canon CR3 Burst File Extractor\n\n"
            "Default output: Adobe DNG\n"
            "Alternate output: Canon CR3",
        )

    def _update_file_count(self):
        count = len(self.file_list.get_all())
        self.status_bar.set_file_count(count)

    def run(self):
        self.root.mainloop()
