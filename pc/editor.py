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

    PNG, not PPM. Tk reads a PPM from a FILE but not out of a base64 blob:
    8.6.15 answers that with "couldn't recognize image data", which is how
    every icon on the shelf and the whole preview came up blank while the
    picture going to the panel was perfectly fine. PNG it reads either way.
    """
    buf = io.BytesIO()
    img.convert("RGB").resize(size or (CW, CH), 0).save(buf, format="PNG")
    return tk.PhotoImage(data=base64.b64encode(buf.getvalue()).decode("ascii"))


# Made-up numbers for the shelf, so a bar is half full and a needle points
# somewhere rather than every icon sitting at zero and looking alike.
ICON_DATA = {"speed_kmh": 88, "rpm": 4200, "rpm_max": 7000, "fuel_pct": 62,
             "brake": 1, "throttle": 0.6, "src": "ABC", "gear": 3,
             "joy_x": 0.5, "joy_y": -0.35}


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
        self._from = None           # where the pointer was when it was picked up
        self._moaned = False
        self._photo_ref = None
        self._snap = [False, False]
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
        # What it IS, in one line, when the pointer is over it. "Lamp" told
        # nobody anything, and a name you have to guess at is a name you do
        # not try.
        self.about = tk.StringVar(value="")
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
                wdg.bind("<Enter>", lambda _e, k=kind: self.about.set(
                    WG.ABOUT.get(k, "")))
                wdg.bind("<Leave>", lambda _e: self.about.set(""))
                wdg.bind("<ButtonPress-1>",
                         lambda e, k=kind, d=defaults: self._pick(k, d, e))
                wdg.bind("<B1-Motion>", self._haul)
                wdg.bind("<ButtonRelease-1>", self._drop)

        ttk.Label(shelf, textvariable=self.about, foreground="#8a7",
                  wraplength=150, justify="left"
                  ).pack(anchor="w", padx=6, pady=(8, 4))

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, pady=8)

        # ---- the page itself: what it is called, and whether it wears the
        # board's header. The name is drawn into the picture we send, so it is
        # ours to keep - the board calls this slot MINE 3 and always will.
        top = ttk.Frame(right)
        top.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Label(top, text="Page name").pack(side="left")
        self.name_var = tk.StringVar(
            value=self.app.page_name(self.app.page_of(self.slot)))
        ent = ttk.Entry(top, textvariable=self.name_var, width=16)
        ent.pack(side="left", padx=6)
        ent.bind("<KeyRelease>", lambda _e: self._rename())
        self.hdr_var = tk.BooleanVar(value=self.app.header_of(self.slot))
        ttk.Checkbutton(top, text="Header bar", variable=self.hdr_var,
                        command=self._toggle_header).pack(side="left", padx=8)
        ttk.Label(top, foreground="#666",
                  text="the name and the page counter, in the board's own nine "
                       "rows").pack(side="left")
        self.grid_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Grid", variable=self.grid_var
                        ).pack(side="right")

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
        # The values are filled in per widget - a Lamp showing the clock is not
        # a feature nobody thought of, it is a list that could not say no.
        self.f_box = ttk.Combobox(row, textvariable=self.f_var, width=15,
                                  state="readonly", values=[])
        self.f_box.pack(side="left", padx=4)
        self.f_box.bind("<<ComboboxSelected>>", lambda _e: self._edit())

        # Grouped so each can be taken away whole for the kinds that ignore it.
        self.lab_grp = ttk.Frame(row)
        ttk.Label(self.lab_grp, text="Label").pack(side="left", padx=(8, 0))
        self.l_var = tk.StringVar()
        e = ttk.Entry(self.lab_grp, textvariable=self.l_var, width=8)
        e.pack(side="left", padx=4)
        e.bind("<KeyRelease>", lambda _e: self._edit())

        # Which button, which switch. The kinds that pick one of a list do
        # not have a field to point at - "A3" is not telemetry, it is a choice.
        self.which_grp = ttk.Frame(row)
        ttk.Label(self.which_grp, text="Which").pack(side="left", padx=(8, 0))
        self.which_var = tk.StringVar()
        self.which_box = ttk.Combobox(self.which_grp, textvariable=self.which_var,
                                      width=18, state="readonly", values=[])
        self.which_box.pack(side="left", padx=4)
        self.which_box.bind("<<ComboboxSelected>>", lambda _e: self._edit())

        # The clock is a field, so it needs a shape: 24h, 12h, the date.
        self.fmt_grp = ttk.Frame(row)
        ttk.Label(self.fmt_grp, text="Format").pack(side="left", padx=(8, 0))
        self.fmt_var = tk.StringVar()
        self.fmt_box = ttk.Combobox(self.fmt_grp, textvariable=self.fmt_var,
                                    width=18, state="readonly",
                                    values=[lbl for _k, lbl in WG.CLOCK_FMTS])
        self.fmt_box.pack(side="left", padx=4)
        self.fmt_box.bind("<<ComboboxSelected>>", lambda _e: self._edit())

        # How a bar fills: solid, or one of the board's two fades - the
        # song bar's faded track, or the rev counter's ramp.
        self.fill_grp = ttk.Frame(row)
        ttk.Label(self.fill_grp, text="Fill").pack(side="left", padx=(8, 0))
        self.fill_var = tk.StringVar()
        self.fill_box = ttk.Combobox(self.fill_grp, textvariable=self.fill_var,
                                     width=12, state="readonly",
                                     values=[lbl for _k, lbl in WG.FILLS])
        self.fill_box.pack(side="left", padx=4)
        self.fill_box.bind("<<ComboboxSelected>>", lambda _e: self._edit())

        # A second field, for the kinds that read two. Hidden the rest of the
        # time: one more combobox on every widget, greyed out on seven of the
        # nine, is worse than a row that comes and goes.
        self.row2 = ttk.Frame(props)
        ttk.Label(self.row2, text="up / down shows").pack(side="left")
        self.fy_var = tk.StringVar()
        self.fy_box = ttk.Combobox(self.row2, textvariable=self.fy_var, width=15,
                                   state="readonly", values=[])
        self.fy_box.pack(side="left", padx=4)
        self.fy_box.bind("<<ComboboxSelected>>", lambda _e: self._edit())
        ttk.Label(self.row2, foreground="#666",
                  text="the axis line shows when that one is dead centre, to "
                       "the pixel").pack(side="left", padx=10)

        self.spins = {}

        def spin(parent, name, lo, hi):
            ttk.Label(parent, text=name).pack(side="left", padx=(8, 0))
            v = tk.IntVar()
            sp = ttk.Spinbox(parent, from_=lo, to=hi, width=4, textvariable=v,
                             command=self._edit)
            sp.pack(side="left", padx=2)
            sp.bind("<KeyRelease>", lambda _e: self._edit())
            self.spins[name] = v

        # A knob's detents per turn: a property of the encoder on your panel,
        # not of this program. Twenty here, so eighteen degrees each.
        self.turn_grp = ttk.Frame(row)
        spin(self.turn_grp, "per turn", 2, 200)

        self.size_grp = ttk.Frame(row)
        spin(self.size_grp, "size", 6, 30)
        self.wh_grp = ttk.Frame(row)      # the anchor everything packs before
        self.wh_grp.pack(side="left")
        self.w_grp = ttk.Frame(self.wh_grp)
        spin(self.w_grp, "w", 1, WG.W)
        self.h_grp = ttk.Frame(self.wh_grp)
        spin(self.h_grp, "h", 1, WG.H)
        ttk.Button(row, text="Delete", command=self._delete).pack(side="right")

    def _rename(self):
        self.app.set_page_name(self.slot, self.name_var.get())
        win = self.winfo_toplevel()
        if isinstance(win, EditorWindow):
            win.title("PicoPanel - %s"
                      % self.app.page_name(self.app.page_of(self.slot)))

    def _toggle_header(self):
        self.app.set_header(self.slot, self.hdr_var.get())

    def _head(self):
        return self.app.header_for(self.slot)

    # ------------------------------------------------ off the shelf and on
    def _pick(self, kind, defaults, ev):
        """Lift a widget off the shelf. The thing that follows the pointer is a
        borderless window with the icon in it - Tk has no drag and drop of its
        own, and a ghost you can see is the difference between dragging and
        clicking and hoping."""
        self._dnd = (kind, defaults)
        self._from = (ev.x_root, ev.y_root)
        self._kill_ghost()
        try:
            img = icon(kind, defaults)     # before the window: if this throws,
            win = tk.Toplevel(self)        # there is no window to leave behind
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            tk.Label(win, image=img, bd=0, bg="#0a0c0e").pack()
            self._ghost_img = img
            self._ghost = win
            self._haul(ev)
        except Exception:
            self._kill_ghost()

    def _kill_ghost(self):
        """One place that gets rid of it. It used to be destroyed only on a
        clean drop, so the one exception that could happen while picking it up
        left a blank white window sitting in the corner of the screen."""
        if self._ghost is not None:
            try:
                self._ghost.destroy()
            except Exception:
                pass
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
        start, self._from = self._from, None
        self._kill_ghost()
        if not kind_def:
            return
        kind, defaults = kind_def
        # Where did it land? Screen coordinates into canvas coordinates - the
        # pointer is what the user aimed with, not the widget it started on.
        cx = self.canvas.winfo_rootx()
        cy = self.canvas.winfo_rooty()
        x = (ev.x_root - cx) // ZOOM
        y = (ev.y_root - cy) // ZOOM
        if start and abs(ev.x_root - start[0]) < 5 and abs(ev.y_root - start[1]) < 5:
            x, y = WG.W // 2, WG.H // 2   # a plain click puts it in the middle,
        if not (0 <= x < WG.W and 0 <= y < WG.H):   # so there is a way that
            return                      # cannot miss; a real drag that lands
                                        # off the screen still adds nothing
        w = WG.Widget(kind=kind, **defaults)
        ww, hh = self._size_of(w)
        w.x = max(0, min(WG.W - 1, int(x - ww // 2)))
        w.y = max(0, min(WG.H - 1, int(y - hh // 2)))
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

    def _box_of(self, w):
        """(x, y, width, height) of what that widget actually draws, in panel
        pixels - the ink, not the place it was asked to start from."""
        try:
            dx, dy, ww, hh = WG.bounds(w, dict(self.app.widget_data(),
                                               _editing=1))
        except Exception:
            dx, dy, ww, hh = 0, 0, w.w, w.h
        return w.x + dx, w.y + dy, ww, hh

    def _size_of(self, w):
        b = self._box_of(w)
        return b[2], b[3]

    def _hit(self, x, y):
        for w in reversed(self.items):
            bx, by, ww, hh = self._box_of(w)
            # A one-pixel line is a thing you have to be able to grab, so
            # nothing is ever narrower than four pixels to the pointer.
            ww, hh = max(ww, 4), max(hh, 4)
            if bx - 1 <= x < bx + ww + 1 and by - 1 <= y < by + hh + 1:
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
        w = self.sel
        # No snapping. It pulled the widget about within two pixels of the
        # middle, which is a deadzone by another name: you could not put a
        # thing one pixel off centre if you wanted to. The line only tells you.
        w.x = max(0, min(WG.W - 1, ev.x // ZOOM - dx))
        w.y = max(0, min(WG.H - 1, ev.y // ZOOM - dy))

    def _up(self, _ev):
        if self._drag is not None:
            self._save()
        self._drag = None

    def _show_props(self):
        w = self.sel
        if not w:
            return
        # Only what this kind can actually do. Everything else is taken away
        # rather than greyed out: a control that does nothing invites you to
        # turn it and then wonder what you broke.
        allowed = WG.fields_for(w.kind)
        keys = [k for k, _l in allowed]
        if allowed and w.field not in keys:
            w.field = keys[0]          # a layout from before the lists existed
            self._save()
        self.f_box["values"] = [lbl for _k, lbl in allowed]
        self.fy_box["values"] = [lbl for _k, lbl in allowed]
        if w.kind in WG.TWO_FIELD:
            self.row2.pack(fill="x", padx=6, pady=(0, 4))
            self.fy_var.set(WG.FIELD_LABEL.get(w.field_y, w.field_y))
        else:
            self.row2.pack_forget()
        if w.kind in WG.USES_LABEL:
            self.lab_grp.pack(side="left", before=self.wh_grp)
        else:
            self.lab_grp.pack_forget()
        if w.kind in WG.HAS_FILL:
            self.fill_grp.pack(side="left", before=self.wh_grp)
            self.fill_var.set(WG.FILL_LABEL.get(w.opts.get("fill", "solid"),
                                                "Solid"))
        else:
            self.fill_grp.pack_forget()
        choices = WG.CHOICES.get(w.kind)
        if choices:
            self.which_box["values"] = [lbl for _k, lbl in choices]
            cur = w.opts.get("which", choices[0][0])
            self.which_var.set(dict(choices).get(cur, choices[0][1]))
            self.which_grp.pack(side="left", before=self.wh_grp)
        else:
            self.which_grp.pack_forget()
        if w.field == "clock":
            self.fmt_var.set(WG.CLOCK_LABEL.get(w.opts.get("fmt", "%H:%M"),
                                                WG.CLOCK_FMTS[0][1]))
            self.fmt_grp.pack(side="left", before=self.wh_grp)
        else:
            self.fmt_grp.pack_forget()
        # w and h only where they do something. On text they did not: the box
        # is the box the letters come out as.
        (self.w_grp.pack(side="left") if w.kind in WG.USES_W
         else self.w_grp.pack_forget())
        (self.h_grp.pack(side="left") if w.kind in WG.USES_H
         else self.h_grp.pack_forget())
        if w.kind in WG.USES_SIZE:
            self.size_grp.pack(side="left", before=self.wh_grp)
        else:
            self.size_grp.pack_forget()
        if w.kind == "knob":
            self.spins["per turn"].set(int(w.opts.get("per_turn",
                                                      WG.DEFAULT_PER_TURN)))
            self.turn_grp.pack(side="left", before=self.wh_grp)
        else:
            self.turn_grp.pack_forget()
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
        want_y = self.fy_var.get()
        for key, lbl in WG.FIELDS:
            if lbl == want_y:
                w.field_y = key
                break
        if w.kind in WG.HAS_FILL:
            for key, lbl in WG.FILLS:
                if lbl == self.fill_var.get():
                    w.opts["fill"] = key
                    break
        for key, lbl in WG.CHOICES.get(w.kind, []):
            if lbl == self.which_var.get():
                w.opts["which"] = key
                break
        if w.field == "clock":
            for key, lbl in WG.CLOCK_FMTS:
                if lbl == self.fmt_var.get():
                    w.opts["fmt"] = key
                    break
        w.label = self.l_var.get()
        if w.kind == "knob":
            try:
                w.opts["per_turn"] = max(2, int(self.spins["per turn"].get()))
            except Exception:
                pass
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
            # "_editing" is how a widget that hides itself when it has
            # nothing to say - the alarm countdown - still shows up here,
            # where you have to be able to see it to place it.
            img = WG.render(self.items, dict(self.app.widget_data(), _editing=1),
                            header=self._head())
            if img is not None:
                self._photo_ref = _photo(img)
                self.canvas.delete("all")
                self.canvas.create_image(0, 0, anchor="nw", image=self._photo_ref)
                self._centred()
                self._grid()
                if self.sel:
                    w = self.sel
                    bx, by, ww, hh = self._box_of(w)
                    if w.kind in WG.AUTO_SIZE:
                        w.w, w.h = ww, hh      # keep the stored box honest
                    self.canvas.create_rectangle(
                        bx * ZOOM, by * ZOOM,
                        (bx + ww) * ZOOM - 1, (by + hh) * ZOOM - 1,
                        outline="#e8744f", dash=(3, 2))
        except Exception as e:
            # Once, not sixty times a second - but once, because a preview that
            # goes black and says nothing is how a broken _photo() lived here
            # for a whole release.
            if not self._moaned:
                self._moaned = True
                try:
                    self.app._log("the preview is not drawing: %s" % e, "err")
                except Exception:
                    pass
        self.after(100, self._tick)

    def _centred(self):
        """Is the selected widget dead centre, on each axis?

        To the pixel, and only to say so: the middle line lights when the
        widget's own middle lands on the screen's, and stays lit while it is
        selected - it is a readout, not a magnet.
        """
        self._snap = [False, False]
        w = self.sel
        if not w:
            return
        bx, by, ww, hh = self._box_of(w)
        self._snap = [round(bx + ww / 2.0) == round(WG.W / 2.0),
                      round(by + hh / 2.0) == round(WG.H / 2.0)]

    def _grid(self):
        """The screen's own grid, over the picture but never in it.

        Eight pixels, because that is the band the panel's memory is organised
        in and what every drawn row lines up with anyway. The middle of the
        screen is marked brighter, and brighter still while a widget is
        snapped to it - which is the question this answers: where IS the
        middle.
        """
        if not self.grid_var.get():
            return
        for gx in range(8, WG.W, 8):
            self.canvas.create_line(gx * ZOOM, 0, gx * ZOOM, CH, fill="#1c2a36")
        for gy in range(8, WG.H, 8):
            self.canvas.create_line(0, gy * ZOOM, CW, gy * ZOOM, fill="#1c2a36")
        mx, my = WG.W // 2 * ZOOM, WG.H // 2 * ZOOM
        self.canvas.create_line(mx, 0, mx, CH, dash=(5, 4),
                                fill="#e8744f" if self._snap[0] else "#3f6c8c")
        self.canvas.create_line(0, my, CW, my, dash=(5, 4),
                                fill="#e8744f" if self._snap[1] else "#3f6c8c")

    # -------------------------------------------------------------- output
    def frame_bytes(self):
        img = WG.render(self.items, self.app.widget_data(), header=self._head())
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
