"""Shared look and small widgets for the control panel."""
import tkinter as tk
from tkinter import ttk

GREEN = "#4CAF50"
GREEN_DARK = "#388E3C"
GREEN_LIGHT = "#E8F5E9"
BLUE = "#0099CC"
RED = "#E53935"
ORANGE = "#EF6C00"
GREY = "#757575"
BACKGROUND = "#FAFAFA"
CARD = "#FFFFFF"
BORDER = "#E0E0E0"
TEXT = "#212121"
MUTED = "#757575"
FONT = "Segoe UI"
SYMBOLS = "Segoe UI Symbol"


def style(root: tk.Tk) -> None:
    s = ttk.Style(root)
    s.configure("Treeview", rowheight=26, font=(FONT, 10))
    s.configure("Treeview.Heading", font=(FONT, 9, "bold"))
    s.configure("TButton", font=(FONT, 9), padding=(10, 4))
    s.configure("Big.TButton", font=(FONT, 10, "bold"), padding=(14, 6))
    s.configure("TCheckbutton", background=CARD, font=(FONT, 9))
    s.configure("Page.TCheckbutton", background=BACKGROUND, font=(FONT, 9))


class Tooltip:
    """Shows `text` in a small box while the mouse is over `widget`."""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _=None) -> None:
        if self.window or not self.text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{x}+{y}")
        tk.Label(self.window, text=self.text, bg="#424242", fg="white", font=(FONT, 9), justify="left",
                 wraplength=320, padx=8, pady=6).pack()

    def _hide(self, _=None) -> None:
        if self.window:
            self.window.destroy()
            self.window = None


def info(parent: tk.Misc, text: str, bg: str = CARD) -> tk.Label:
    label = tk.Label(parent, text="ⓘ", bg=bg, fg=GREEN_DARK, font=(SYMBOLS, 11), cursor="question_arrow")
    Tooltip(label, text)
    return label


def badge(parent: tk.Misc, number: int, bg: str = BACKGROUND) -> tk.Canvas:
    """A green numbered circle, matching the numbers in the help text above."""
    canvas = tk.Canvas(parent, width=22, height=22, bg=bg, highlightthickness=0)
    canvas.create_oval(1, 1, 21, 21, fill=GREEN, outline="")
    canvas.create_text(11, 11, text=str(number), fill="white", font=(FONT, 9, "bold"))
    return canvas


def icon_button(parent: tk.Misc, symbol: str, tip: str, command) -> tk.Button:
    button = tk.Button(parent, text=symbol, command=command, width=3, height=1, font=(SYMBOLS, 15), bg=CARD,
                       fg=GREEN_DARK, activebackground=GREEN_LIGHT, relief="flat", bd=0, cursor="hand2",
                       highlightthickness=1, highlightbackground=BORDER)
    Tooltip(button, tip)
    return button


class Card(tk.Frame):
    def __init__(self, parent: tk.Misc, title: str) -> None:
        super().__init__(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        tk.Label(self, text=title, bg=CARD, fg=GREEN_DARK, font=(FONT, 10, "bold")).pack(anchor="w", padx=12,
                                                                                        pady=(10, 4))
        self.body = tk.Frame(self, bg=CARD)
        self.body.pack(fill="both", expand=True, padx=12, pady=(0, 10))

    def row(self, label: str, value: str = "", copy: bool = False, right=None) -> tk.StringVar:
        return row(self.body, label, value, copy, right=right)


def row(parent: tk.Misc, label: str, value: str = "", copy: bool = False, bold: bool = False,
        right=None) -> tk.StringVar:
    """A label and a value that wraps to the width it gets, optionally with a Copy button or `right(frame)`."""
    frame = tk.Frame(parent, bg=CARD)
    frame.pack(fill="x", pady=1)
    tk.Label(frame, text=label, bg=CARD, fg=TEXT if bold else MUTED, width=12, anchor="nw",
             font=(FONT, 9, "bold" if bold else "normal")).pack(side="left", anchor="n")
    var = tk.StringVar(value=value)
    if copy:
        def to_clipboard() -> None:
            frame.clipboard_clear()
            frame.clipboard_append(var.get())

        ttk.Button(frame, text="Copy", width=5, command=to_clipboard).pack(side="right")
    if right:
        right(frame).pack(side="right")
    shown = tk.Label(frame, textvariable=var, bg=CARD, fg=TEXT, anchor="w", justify="left", font=(FONT, 9))
    shown.pack(side="left", fill="x", expand=True)
    shown.bind("<Configure>", lambda e: shown.configure(wraplength=max(80, e.width - 4)))
    return var


class ScrollFrame(tk.Frame):
    """A frame whose `inner` part scrolls vertically when the window is too small for it."""

    def __init__(self, parent: tk.Misc, bg: str = BACKGROUND) -> None:
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=bar.set)
        self.inner.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(window, width=e.width))
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.bind_all("<MouseWheel>", self._wheel, add="+")

    def _wheel(self, event) -> None:
        if self.winfo_ismapped() and self.inner.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")


class Page(tk.Frame):
    """One screen of the panel. The green band shows `title` and `help` while the page is open."""
    title = ""
    help = ""

    def __init__(self, app) -> None:
        super().__init__(app.body, bg=BACKGROUND)
        self.app = app

    def shown(self) -> None:
        pass

    def commit(self) -> bool:
        """Called by Next in the tutorial. False keeps the user on this page."""
        return True
