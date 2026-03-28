"""Dark theme styling for CSI Tool GUI."""

# Catppuccin Mocha-inspired palette
BG_PRIMARY = "#1e1e2e"
BG_SECONDARY = "#313244"
BG_SURFACE = "#45475a"
BG_OVERLAY = "#585b70"
FG_PRIMARY = "#cdd6f4"
FG_SECONDARY = "#a6adc8"
FG_DIM = "#6c7086"
ACCENT = "#89b4fa"
ACCENT_HOVER = "#74c7ec"
SUCCESS = "#a6e3a1"
ERROR = "#f38ba8"
WARNING = "#fab387"
BORDER = "#585b70"

# Fonts
FONT_HEADING = ("Segoe UI", 13, "bold")
FONT_BODY = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_MONO = ("Consolas", 9)

# Dimensions
WINDOW_MIN_WIDTH = 850
WINDOW_MIN_HEIGHT = 580
PAD = 8


def apply_dark_theme(root):
    """Apply dark theme to the root tkinter window and ttk styles."""
    import tkinter.ttk as ttk

    root.configure(bg=BG_PRIMARY)

    style = ttk.Style()
    style.theme_use("clam")

    # General
    style.configure(".", background=BG_PRIMARY, foreground=FG_PRIMARY,
                     fieldbackground=BG_SECONDARY, font=FONT_BODY,
                     borderwidth=0, relief="flat")

    # Frame
    style.configure("TFrame", background=BG_PRIMARY)
    style.configure("Surface.TFrame", background=BG_SECONDARY)

    # Label
    style.configure("TLabel", background=BG_PRIMARY, foreground=FG_PRIMARY,
                     font=FONT_BODY)
    style.configure("Heading.TLabel", font=FONT_HEADING, foreground=FG_PRIMARY)
    style.configure("Dim.TLabel", foreground=FG_DIM)
    style.configure("Success.TLabel", foreground=SUCCESS)
    style.configure("Error.TLabel", foreground=ERROR)
    style.configure("Accent.TLabel", foreground=ACCENT)

    # Button
    style.configure("TButton", background=BG_SURFACE, foreground=FG_PRIMARY,
                     padding=(12, 6), font=FONT_BODY)
    style.map("TButton",
              background=[("active", BG_OVERLAY), ("disabled", BG_SECONDARY)],
              foreground=[("disabled", FG_DIM)])

    style.configure("Accent.TButton", background=ACCENT, foreground=BG_PRIMARY,
                     font=("Segoe UI", 10, "bold"))
    style.map("Accent.TButton",
              background=[("active", ACCENT_HOVER), ("disabled", BG_SURFACE)])

    # Treeview
    style.configure("Treeview", background=BG_SECONDARY, foreground=FG_PRIMARY,
                     fieldbackground=BG_SECONDARY, rowheight=28, font=FONT_BODY,
                     borderwidth=0)
    style.configure("Treeview.Heading", background=BG_SURFACE,
                     foreground=FG_SECONDARY, font=("Segoe UI", 9, "bold"))
    style.map("Treeview",
              background=[("selected", BG_SURFACE)],
              foreground=[("selected", ACCENT)])

    # Progressbar
    style.configure("TProgressbar", background=ACCENT, troughcolor=BG_SECONDARY,
                     borderwidth=0, thickness=8)

    # Entry
    style.configure("TEntry", fieldbackground=BG_SECONDARY, foreground=FG_PRIMARY,
                     insertcolor=FG_PRIMARY, padding=4)

    # Separator
    style.configure("TSeparator", background=BORDER)

    # Scrollbar
    style.configure("Vertical.TScrollbar", background=BG_SURFACE,
                     troughcolor=BG_SECONDARY, borderwidth=0, arrowsize=0)
    style.map("Vertical.TScrollbar",
              background=[("active", BG_OVERLAY)])

    # LabelFrame
    style.configure("TLabelframe", background=BG_PRIMARY, foreground=FG_PRIMARY)
    style.configure("TLabelframe.Label", background=BG_PRIMARY,
                     foreground=FG_SECONDARY, font=FONT_SMALL)
