"""The page editor: take a widget off the shelf, drag it onto the screen.

The preview is not a drawing of what the panel will show - it IS what the panel
will show. The same `widgets.render` produces the image here and the 512 bytes
that go down the wire, so the two cannot disagree.

Frames are sent only while the board is on that page. It says so itself with
`!PAGE n` on every change; guessing from the status line would not work, because
that only goes out when reporting is switched on.
"""

import base64
import io
import tkinter as tk
from tkinter import ttk

import widgets as WG

ZOOM = 4
CW, CH = WG.W * ZOOM, WG.H * ZOOM

# Fast enough that a needle looks alive, slow enough to leave the link alone:
# a frame is 684 characters of base64, so this is about 10 kB a second.
SEND_HZ = 15


def _photo(img, size=None):
    """A PIL image as a Tk PhotoImage, without needing PIL's Tk bridge.

    Tk reads PPM out of a base64 blob, and PIL writes PPM. That is the whole
    trick, and it saves a dependency that is missing often enough to matter.
    """
    buf = io.BytesIO()
    img.convert("RGB").resize(size or (CW, CH), 0).save(buf, format="PPM")
    return tk.PhotoImage(data=base64.b64encode(buf.getvalue()))


# Made-up numbers for the shelf, so a bar is half full and a needle points
# somewhere rather than every icon sitting at zero and looking alike.
ICON_DATA = {"speed_kmh": 88, "rpm": 4200, "rpm_max": 7000, "fuel_pct": 62,
             "brake": 1, "throttle": 0.6, "src": "ABC", "gear": 3}


def icon(kind, defaults, zoom=2):
    """A picture of the widget itself, drawn by the widget's own code.

    Not a hand-drawn glyph: an icon drawn separately can come to mean something
    the widget no longer does, and this way a new widget gets one for free.
    """
    w = WG.Widget(kind=kind, x=1, y=1, **defaults)
    img = WG.Image.new("1", (w.w + 2, w.h + 2), 0)
    d = WG.ImageDraw.Draw(img)
    d.fontmode = "1"
    try:
        WG.DRAW[kind](d, w, ICON_DATA)
    except Exception:
        pass
    return _photo(img, (img.width * zoom, img.height * zoom))


class Editor(ttk.Frame):
    """One custom page. `slot` says which of the board's four it is."""

    def __init__(self, master, app, slot=0):
        super().__init__(master)
        self.app = app
        self.slot = slot
        self.items = [WG.Widget.from_dict(d) for d in app.layout_of(slot)]
        self.sel = None
        self._drag = None
        self._dnd = None            # (kind, defaults) while dragging off the shelf
        self._ghost = None
        self._photo_ref = None
        self._build()
        self._tick()

    # ------------------------------------------------------------ building
    def _build(self):
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)

        # ---- the shelf, down the side. Vertical because each item is a
        # picture of a widget and pictures want to be side by side with their
        # names, not stacked under them in a row that runs off the window.
        shelf = ttk.LabelFrame(body, text="Widgets")
        shelf.pack(side="left", fill="y", padx=8, pady=8)
        ttk.Label(shelf, foreground="#666", wraplength=150, justify="left",
                  text="Pick one up and drop it on the screen."
                  ).pack(anchor="w", padx=6, pady=(4, 6))
        self._icons = []
        for kind, label, defaults in WG.PALETTE:
            row = ttk.Frame(shelf)
            row.pack(fill="x", padx=6, pady=3)
            try:
                img = icon(kind, defaults)
                self._icons.append(img)       # a PhotoImage nothing holds is
            except Exception:                 # collected, and the row goes blank
                img = None
            lab = tk.Label(row, image=img, bd=1, relief="solid", bg="#0a0c0e",
                           cursor="hand2")
            lab.pack(side="left")
            ttk.Label(row, text=label).pack(side="left", padx=6)
            for wdg in (lab, row):
                wdg.bind("<ButtonPress-1>",
                         lambda e, k=kind, d=defaults: self._pick(k, d, e))
                wdg.bind("<B1-Motion>", self._haul)
                wdg.bind("<ButtonRelease-1>", self._drop)

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, pady=8)

        self.canvas = tk.Canvas(right, width=CW, height=CH, bg="#0a0c0e",
                                highlightthickness=1, highlightbackground="#555")
        self.canvas.pack(padx=8)
        self.canvas.bind("<Button-1>", self._down)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._up)

        ttk.Label(right, foreground="#555", wraplength=CW, justify="left",
                  text="This preview is the picture the panel gets, drawn by "
                       "the same code - it cannot drift. The page is sent only "
                       "while the board is showing it."
                  ).pack(anchor="w", padx=8, pady=(4, 0))

        props = ttk.LabelFrame(right, text="Selected widget")
        props.pack(fill="x", padx=8, pady=6)
        row = ttk.Frame(props)
        row.pack(fill="x", padx=6, pady=4)

        ttk.Label(row, text="Shows").pack(side="left")
        self.f_var = tk.StringVar()
        self.f_box = ttk.Combobox(row, textvariable=self.f_var, width=15,
                                  state="readonly",
                                  values=[lbl for _k, lbl in WG.FIELDS])
        self.f_box.pack(side="left", padx=4)
        self.f_box.bind("<<ComboboxSelected>>", lambda _e: self._edit())

        ttk.Label(row, text="Label").pack(side="left", padx=(8, 0))
        self.l_var = tk.StringVar()
        e = ttk.Entry(row, textvariable=self.l_var, width=8)
        e.pack(side="left", padx=4)
        e.bind("<KeyRelease>", lambda _e: self._edit())

        self.spins = {}
        for name, lo, hi in (("size", 6, 30), ("w", 1, WG.W), ("h", 1, WG.H)):
            ttk.Label(row, text=name).pack(side="left", padx=(8, 0))
            v = tk.IntVar()
            sp = ttk.Spinbox(row, from_=lo, to=hi, width=4, textvariable=v,
                             command=self._edit)
            sp.pack(side="left", padx=2)
            sp.bind("<KeyRelease>", lambda _e: self._edit())
            self.spins[name] = v
        ttk.Button(row, text="Delete", command=self._delete).pack(side="right")

    # ------------------------------------------------ off the shelf and on
    def _pick(self, kind, defaults, ev):
        """Lift a widget off the shelf. The thing that follows the pointer is a
        borderless window with the icon in it - Tk has no drag and drop of its
        own, and a ghost you can see is the difference between dragging and
        clicking and hoping."""
        self._dnd = (kind, defaults)
        try:
            self._ghost = tk.Toplevel(self)
            self._ghost.overrideredirect(True)
            self._ghost.attributes("-topmost", True)
            img = icon(kind, defaults)
            self._ghost_img = img
            tk.Label(self._ghost, image=img, bd=0, bg="#0a0c0e").pack()
            self._haul(ev)
        except Exception:
            self._ghost = None

    def _haul(self, ev):
        if self._ghost is None:
            return
        try:
            self._ghost.geometry("+%d+%d" % (ev.x_root + 8, ev.y_root + 8))
        except Exception:
            pass

    def _drop(self, ev):
        kind_def, self._dnd = self._dnd, None
        if self._ghost is not None:
            self._ghost.destroy()
            self._ghost = None
        if not kind_def:
            return
        kind, defaults = kind_def
        # Where did it land? Screen coordinates into canvas coordinates - the
        # pointer is what the user aimed with, not the widget it started on.
        cx = self.canvas.winfo_rootx()
        cy = self.canvas.winfo_rooty()
        x = (ev.x_root - cx) // ZOOM
        y = (ev.y_root - cy) // ZOOM
        if not (0 <= x < WG.W and 0 <= y < WG.H):
            return                      # dropped off the screen: nothing added
        w = WG.Widget(kind=kind, **defaults)
        w.x = max(0, min(WG.W - 1, int(x - w.w // 2)))
        w.y = max(0, min(WG.H - 1, int(y - w.h // 2)))
        self.items.append(w)
        self.sel = w
        self._show_props()
        self._save()

    # ------------------------------------------------------------- editing
    def _delete(self):
        if self.sel in self.items:
            self.items.remove(self.sel)
            self.sel = None
            self._save()

    def _hit(self, x, y):
        for w in reversed(self.items):
            if w.x <= x < w.x + max(w.w, 4) and w.y <= y < w.y + max(w.h, 4):
                return w
        return None

    def _down(self, ev):
        x, y = ev.x // ZOOM, ev.y // ZOOM
        w = self._hit(x, y)
        self.sel = w
        self._drag = (x - w.x, y - w.y) if w else None
        self._show_props()

    def _move(self, ev):
        if not self.sel or self._drag is None:
            return
        dx, dy = self._drag
        self.sel.x = max(0, min(WG.W - 1, ev.x // ZOOM - dx))
        self.sel.y = max(0, min(WG.H - 1, ev.y // ZOOM - dy))

    def _up(self, _ev):
        if self._drag is not None:
            self._save()
        self._drag = None

    def _show_props(self):
        w = self.sel
        if not w:
            return
        self.f_var.set(WG.FIELD_LABEL.get(w.field, w.field))
        self.l_var.set(w.label)
        self.spins["size"].set(w.size)
        self.spins["w"].set(w.w)
        self.spins["h"].set(w.h)

    def _edit(self):
        w = self.sel
        if not w:
            return
        want = self.f_var.get()
        for key, lbl in WG.FIELDS:
            if lbl == want:
                w.field = key
                break
        w.label = self.l_var.get()
        try:
            w.size = int(self.spins["size"].get())
            w.w = int(self.spins["w"].get())
            w.h = int(self.spins["h"].get())
        except Exception:
            pass                      # half-typed number: leave it until it's whole
        self._save()

    def _save(self):
        self.app.set_layout(self.slot, [w.to_dict() for w in self.items])

    # ------------------------------------------------------------ painting
    def _tick(self):
        try:
            img = WG.render(self.items, self.app.widget_data())
            if img is not None:
                self._photo_ref = _photo(img)
                self.canvas.delete("all")
                self.canvas.create_image(0, 0, anchor="nw", image=self._photo_ref)
                if self.sel:
                    w = self.sel
                    self.canvas.create_rectangle(
                        w.x * ZOOM, w.y * ZOOM,
                        (w.x + w.w) * ZOOM - 1, (w.y + w.h) * ZOOM - 1,
                        outline="#e8744f", dash=(3, 2))
        except Exception:
            pass                      # the editor must never take the app down
        self.after(100, self._tick)

    # -------------------------------------------------------------- output
    def frame_bytes(self):
        img = WG.render(self.items, self.app.widget_data())
        return None if img is None else WG.to_frame(img)


class EditorWindow(tk.Toplevel):
    """The editor on its own, opened by double-clicking a page."""

    def __init__(self, app, slot, title):
        super().__init__(app)
        self.title("PicoPanel - %s" % title)
        self.transient(app)
        self.editor = Editor(self, app, slot)
        self.editor.pack(fill="both", expand=True)
        try:
            self.iconbitmap(app.icon_path)
        except Exception:
            pass
