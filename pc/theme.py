"""One look for the whole app - the one the page cards already had.

The list of pages was dark, with cards and a warm accent; everything around it
was whatever ttk does by default on Windows, which is grey. Two designs in one
window is worse than either, so this is the card list's palette applied to the
lot, in one place, so there is nowhere for a second look to start.

Tk's own widgets (Text, Listbox, Canvas) do not take a theme, so they are
handed the same colours by name - hence the PAL dict rather than a pile of
literals scattered about.
"""

from tkinter import ttk

PAL = {
    "bg":     "#151920",
    "panel":  "#1b1e24",
    "card":   "#20242c",
    "field":  "#11151b",
    "line":   "#2b3038",
    "ink":    "#e6e8ec",
    "dim":    "#8b93a1",
    "faint":  "#6b7280",
    "accent": "#e8744f",
    "ok":     "#5fd08a",
    "warn":   "#e0806a",
    "screen": "#0a0c0e",
}


def apply(root):
    """Dress the window and every ttk widget in it."""
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except Exception:
        pass
    root.configure(background=PAL["bg"])

    st.configure(".",
                 background=PAL["bg"], foreground=PAL["ink"],
                 fieldbackground=PAL["field"], bordercolor=PAL["line"],
                 lightcolor=PAL["line"], darkcolor=PAL["line"],
                 troughcolor=PAL["panel"], focuscolor=PAL["accent"],
                 insertcolor=PAL["ink"], selectbackground=PAL["accent"],
                 selectforeground="#10131a")

    st.configure("TFrame", background=PAL["bg"])
    st.configure("TLabel", background=PAL["bg"], foreground=PAL["ink"])
    st.configure("TLabelframe", background=PAL["bg"], bordercolor=PAL["line"])
    st.configure("TLabelframe.Label", background=PAL["bg"],
                 foreground=PAL["dim"])

    st.configure("TNotebook", background=PAL["bg"], bordercolor=PAL["line"],
                 tabmargins=(6, 4, 0, 0))
    st.configure("TNotebook.Tab", background=PAL["panel"],
                 foreground=PAL["dim"], bordercolor=PAL["line"],
                 padding=(14, 6))
    st.map("TNotebook.Tab",
           background=[("selected", PAL["card"])],
           foreground=[("selected", PAL["ink"])],
           expand=[("selected", (0, 0, 0, 0))])

    st.configure("TButton", background=PAL["card"], foreground=PAL["ink"],
                 bordercolor=PAL["line"], focusthickness=1, padding=(10, 4))
    st.map("TButton",
           background=[("pressed", PAL["line"]), ("active", "#39404a")],
           foreground=[("disabled", PAL["faint"])])

    for kind in ("TEntry", "TSpinbox", "TCombobox"):
        st.configure(kind, fieldbackground=PAL["field"], foreground=PAL["ink"],
                     bordercolor=PAL["line"], arrowcolor=PAL["dim"],
                     insertcolor=PAL["ink"], padding=(4, 3))
        st.map(kind,
               fieldbackground=[("readonly", PAL["card"]),
                                ("disabled", PAL["panel"])],
               foreground=[("disabled", PAL["faint"])],
               arrowcolor=[("active", PAL["accent"])])

    for kind in ("TCheckbutton", "TRadiobutton"):
        st.configure(kind, background=PAL["bg"], foreground=PAL["ink"],
                     indicatorbackground=PAL["field"],
                     indicatorforeground=PAL["accent"],
                     upperbordercolor=PAL["line"],
                     lowerbordercolor=PAL["line"],
                     focusthickness=0)
        st.map(kind,
               background=[("active", PAL["bg"])],
               indicatorbackground=[("selected", PAL["field"]),
                                    ("active", PAL["card"]),
                                    ("disabled", PAL["panel"])],
               indicatorforeground=[("selected", PAL["accent"]),
                                    ("disabled", PAL["faint"])],
               upperbordercolor=[("selected", PAL["accent"])],
               lowerbordercolor=[("selected", PAL["accent"])],
               foreground=[("disabled", PAL["faint"])])

    st.configure("TProgressbar", background=PAL["accent"],
                 troughcolor=PAL["panel"], bordercolor=PAL["line"],
                 lightcolor=PAL["accent"], darkcolor=PAL["accent"])
    st.configure("Vertical.TScrollbar", background=PAL["card"],
                 troughcolor=PAL["panel"], bordercolor=PAL["line"],
                 arrowcolor=PAL["dim"])
    st.map("Vertical.TScrollbar", background=[("active", "#39404a")])
    st.configure("Horizontal.TScrollbar", background=PAL["card"],
                 troughcolor=PAL["panel"], bordercolor=PAL["line"],
                 arrowcolor=PAL["dim"])
    st.configure("TSeparator", background=PAL["line"])

    st.configure("Hint.TLabel", foreground=PAL["faint"], background=PAL["bg"])
    st.configure("Head.TLabel", foreground=PAL["ink"], background=PAL["bg"])
    return st


def text_opts(caret=True):
    """What a tk.Text or tk.Listbox needs to match. They take no theme.

    A Listbox has no caret and refuses to be told about one, so `caret=False`
    leaves that out rather than making every caller remember.
    """
    opts = dict(background=PAL["field"], foreground=PAL["ink"],
                selectbackground=PAL["accent"], selectforeground="#10131a",
                highlightthickness=1, highlightbackground=PAL["line"],
                highlightcolor=PAL["line"], borderwidth=0)
    if caret:
        opts["insertbackground"] = PAL["ink"]
    return opts
