"""The page rotation, as cards you can pick up.

A row used to be the word "GAME". A word is a poor way to choose between
eighteen screens, so a row is now a picture OF that screen - the board's own
frame, collected by walking the pages and photographing each one. A page you
recognise is a page you can order.

The pictures are real. A mock-up would be a second drawing of every page, and
the moment the firmware changed one it would start lying.

One list, not two. The pages USER walks are at the top, the ones you have put
aside sit below the line, and a card crosses that line without moving house.
Two columns cost twice the width, and a card is 256 pixels of picture before
anything else.
"""

import tkinter as tk
from tkinter import ttk

SHOT_SCALE = 2
SHOT_W, SHOT_H = 128 * SHOT_SCALE, 32 * SHOT_SCALE

# How long to let the board settle on a page before photographing it. A frame
# takes about 18 ms, so this is a few frames - enough that what we catch is the
# new page and not the tail of the old one.
SETTLE_MS = 130

import theme

# The cards' own colours, which are now the whole app's - see theme.py.
BG = theme.PAL["panel"]
CARD = theme.PAL["card"]
DIM = theme.PAL["dim"]


def frame_photo(w, h, data, scale=SHOT_SCALE):
    """An SSD1306 frame buffer as a Tk image.

    The buffer is in pages: one byte holds eight pixels stacked vertically, so
    pixel (x, y) is bit y%8 of byte x + (y//8)*width.
    """
    need = w * h // 8
    if len(data) < need:
        return None
    rows = []
    for y in range(h):
        base, bit = (y >> 3) * w, 1 << (y & 7)
        rows.append("{" + " ".join(
            "#d8f4ff" if data[base + x] & bit else theme.PAL["screen"]
            for x in range(w)) + "}")
    img = tk.PhotoImage(width=w, height=h)
    img.put(" ".join(rows))
    return img.zoom(scale)


class PageList(ttk.Frame):
    """The cards: what USER walks, and what has been left out of it."""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.shots = {}             # (page, sub) -> PhotoImage
        self._keep = []             # the shrunk ones, so Tk keeps them
        self._blank = None
        self._rows = {}
        self._drag = None           # what is in the hand, while it is
        self._build()

    def _build(self):
        head = ttk.Frame(self)
        head.pack(fill="x")
        ttk.Label(head, wraplength=330, justify="left",
                  text="USER walks these, in this order. Drag a card by its "
                       "handle to move it. Double-click one of your own pages "
                       "to lay it out. + makes a new one right below."
                  ).pack(side="left", anchor="w")
        ttk.Button(head, text="Refresh pictures",
                   command=self.collect).pack(side="right", padx=4)
        ttk.Button(head, text="+ New page",
                   command=lambda: self.app.pg_new()).pack(side="right", padx=4)

        # ---- the scrolling list. Eighteen cards are far taller than the
        # window, and a list you cannot reach the bottom of is not a list you
        # can put in order.
        box = ttk.Frame(self)
        box.pack(fill="both", expand=True, pady=6)
        self.canvas = tk.Canvas(box, highlightthickness=0, bg=BG, width=520)
        bar = ttk.Scrollbar(box, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.list = tk.Frame(self.canvas, bg=BG)
        win = self.canvas.create_window((0, 0), window=self.list, anchor="nw")
        self.list.bind("<Configure>", lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(win, width=e.width))
        self._wheel(self.canvas)
        self._wheel(self.list)

    def _wheel(self, wdg):
        """The wheel belongs to the list, not to whatever card happens to be
        under the pointer - Tk hands it to the innermost widget, and a card is
        a dozen of them. Bound on the widget AND everything inside it, since
        the name, the hint and the buttons are all innermost to something.
        """
        wdg.bind("<MouseWheel>",
                 lambda e: self.canvas.yview_scroll(-e.delta // 120, "units"))
        for kid in wdg.winfo_children():
            self._wheel(kid)

    def _blank_shot(self):
        """A dark rectangle the size of the panel, for a page not yet seen.

        A Label with no image at all sizes itself in CHARACTERS and LINES, so
        128x32 meant as pixels came out 256 characters wide and 64 lines tall:
        one card filling the whole window and hiding every other one.
        """
        if self._blank is None:
            img = tk.PhotoImage(width=SHOT_W, height=SHOT_H)
            img.put(theme.PAL["screen"], to=(0, 0, SHOT_W, SHOT_H))
            self._blank = img
        return self._blank

    # ------------------------------------------------------------- filling
    def refill(self):
        for child in self.list.winfo_children():
            child.destroy()
        self._rows = {}
        self._keep = []
        self._faces_of = {}
        self._tail = None
        order, rest = self.app._pg_ids
        for i, pg in enumerate(order):
            self._card(pg, i, True)
            self._faces(pg)
        if rest:
            head = tk.Frame(self.list, bg=BG)
            head.pack(fill="x", pady=(10, 2))
            tk.Label(head, text="Not shown - USER walks past these", bg=BG,
                     fg=DIM, font=("", 9)).pack(side="left", padx=8)
            self._wheel(head)
            self._tail = head        # where the rotation ends, for the drag
        for pg in rest:
            self._card(pg, None, False)
            self._faces(pg)

    def _faces(self, pg):
        """Faces, indented under the page they belong to - USER walks them
        without leaving it.

        They are not draggable: the rotation is by page, and a face travels
        with its page whether you like it or not. GAME's and MUSIC's have no
        buttons either, because how many there are is the board's to know -
        GAME's count depends on what is sending. Your own pages' faces are
        yours, so those get a delete and open their own editor.
        """
        n = self.app.subs_of(pg)
        mine_page = self.app.slot_of(pg) is not None
        mine = self._faces_of.setdefault(pg, [])
        for sub in range(1, n):
            shot = self.shots.get((pg, sub)) or self._blank_shot()
            small = shot.subsample(SHOT_SCALE)   # the panel's own size, 128x32
            self._keep.append(small)             # or Tk drops it and shows air
            row = tk.Frame(self.list, bg=CARD, bd=1, relief="solid")
            row.pack(fill="x", padx=(34, 4), pady=(0, 3))
            tk.Label(row, text="↳", bg=CARD, fg=DIM,
                     font=("", 9)).pack(side="left", padx=(6, 2))
            pic = tk.Label(row, image=small, bg=theme.PAL["screen"], bd=0)
            pic.pack(side="left", padx=4, pady=3)
            tk.Label(row, text="%s - %d of %d" % (self.app.page_name(pg),
                                                  sub + 1, n),
                     bg=CARD, fg=DIM, font=("", 9)).pack(side="left", padx=6)
            if mine_page:
                tk.Label(row, text="double-click", bg=CARD,
                         fg=theme.PAL["faint"], font=("", 8)).pack(side="left")
                b = tk.Button(row, text="✕", width=2, bd=0,
                              bg=theme.PAL["line"], fg=theme.PAL["warn"],
                              activebackground="#39404a",
                              activeforeground=theme.PAL["warn"],
                              command=lambda p=pg, s=sub:
                                  self.app.face_del(p, s))
                b.pack(side="right", padx=4)
                self._wheel(b)
            mine.append(row)         # they travel with the page when it moves
            for wdg in (row, pic):
                wdg.bind("<Button-1>",
                         lambda _e, p=pg, s=sub: self.app.pg_show(p, s))
                if mine_page:
                    wdg.bind("<Double-Button-1>",
                             lambda _e, p=pg, s=sub: self.app.pg_open(p, s))
            self._wheel(row)

    def _card(self, pg, idx, inside):
        name = self.app.page_name(pg)
        card = tk.Frame(self.list, bd=1, relief="solid", bg=CARD)
        card.pack(fill="x", padx=4, pady=3)
        self._rows[pg] = card

        # The handle. Dragging anywhere on the card would fight with the
        # double-click that opens the editor, so the grip is its own column -
        # the same reason a list on a phone has one.
        grip = tk.Label(card, text="⠇\n⠇", bg=CARD,
                        fg=DIM if inside else "#4a5160",
                        cursor="fleur" if inside else "arrow", font=("", 9))
        grip.pack(side="left", padx=(4, 2))

        pic = tk.Label(card, image=self.shots.get((pg, 0)) or self._blank_shot(),
                       bg=theme.PAL["screen"], bd=0)
        pic.pack(side="left", padx=4, pady=4)

        # The buttons take their room BEFORE the text does. Pack hands out
        # space in the order it is asked for and silently drops whatever no
        # longer fits, which is how the delete button vanished from the one
        # card whose caption ran long.
        btn = tk.Frame(card, bg=CARD)
        btn.pack(side="right", padx=6)

        side = tk.Frame(card, bg=CARD)
        side.pack(side="left", fill="both", expand=True, padx=6)
        tk.Label(side, text=name, bg=CARD, fg=theme.PAL["ink"] if inside else DIM,
                 font=("", 10, "bold")).pack(anchor="w")
        if self.app.slot_of(pg) is not None:
            tk.Label(side, text="yours - double-click", bg=CARD,
                     fg=DIM, font=("", 8)).pack(anchor="w")
        if (pg, 0) not in self.shots:
            tk.Label(side, text="no picture yet", bg=CARD,
                     fg=theme.PAL["faint"], font=("", 8)).pack(anchor="w")

        def tool(text, cmd, fg="#e6e8ec", width=2):
            b = tk.Button(btn, text=text, width=width, bd=0,
                          bg=theme.PAL["line"], fg=fg,
                          activebackground="#39404a", activeforeground=fg,
                          command=cmd)
            b.pack(side="right", padx=2)
            self._wheel(b)

        # Keep the screen lit on this page, or let it sleep. A sun, because
        # the panel's own word for it is "dim after twenty seconds".
        lit = self.app.aod_of(pg)
        tool("☀" if lit else "◌",
             lambda p=pg: self.app.aod_toggle(p),
             fg="#e8c45f" if lit else "#6b7280")
        # Holds the USER button while the gamepad is armed: the page will not
        # step past it and you disarm to leave. The GAME page has always done
        # this - the chip says which others do.
        held = self.app.hid_of(pg)
        tool("HID", lambda p=pg: self.app.hid_toggle(p), width=3,
             fg="#7fc7ff" if held else "#6b7280")
        # Out of the rotation, or back into it.
        tool("↓" if inside else "↑", lambda p=pg: self.app.pg_toggle(p))
        # A page of your own, made right here rather than at the end: the place
        # you want it is the place you were looking at.
        tool("+", lambda p=pg: self.app.pg_new(after=p))
        if self.app.slot_of(pg) is not None:
            # Another face on this page - walked with USER without leaving it,
            # the way GAME's are. Distinct from "+", which makes a whole page.
            tool("↳+", lambda p=pg: self.app.face_add(p), width=3)
            tool("✕", lambda p=pg: self.app.pg_delete(p), fg=theme.PAL["warn"])

        for wdg in (card, pic, side):
            wdg.bind("<Button-1>", lambda _e, p=pg: self.app.pg_show(p))
            wdg.bind("<Double-Button-1>", lambda _e, p=pg: self.app.pg_open(p))
        # Last, so the recursion catches the labels and buttons inside.
        self._wheel(card)
        if inside:
            grip.bind("<ButtonPress-1>",
                      lambda e, p=pg, i=idx: self._grab(e, p, i))
            grip.bind("<B1-Motion>", self._haul)
            grip.bind("<ButtonRelease-1>", self._release)

    # -------------------------------------------------------------- moving
    #
    # The card is lifted OUT of the list and follows the pointer pixel by
    # pixel, with a gap left where it will land. It used to jump straight to
    # the new position the moment the pointer crossed a card - correct, and it
    # felt like the list was arguing with you rather than being held.

    def _grab(self, ev, pg, idx):
        card = self._rows.get(pg)
        if card is None or self._drag:
            return
        h, w = card.winfo_height(), card.winfo_width()
        y = card.winfo_rooty() - self.list.winfo_rooty()
        hold = ev.y_root - card.winfo_rooty()

        card.pack_forget()
        for row in self._faces_of.get(pg, []):
            row.pack_forget()          # the faces go with their page
        self.list.update_idletasks()

        # Where the OTHER cards sit with this one out of the flow. Measured
        # once, and the gap is never counted: the drop position is then a
        # fixed function of where the pointer is, and cannot oscillate between
        # two answers as the layout moves under it.
        order = self.app._pg_ids[0]
        rest = [p for p in order if p != pg]
        mids = []
        for p in rest:
            c = self._rows[p]
            mids.append(c.winfo_rooty() - self.list.winfo_rooty()
                        + c.winfo_height() / 2.0)

        gap = tk.Frame(self.list, bg=theme.PAL["bg"], bd=1, relief="sunken", height=h)
        self._drag = {"pg": pg, "card": card, "gap": gap, "rest": rest,
                      "mids": mids, "hold": hold, "index": None}
        self._gap_to(min(idx, len(rest)))
        card.configure(relief="raised", bd=2)
        card.place(in_=self.list, x=4, y=y, width=w, height=h)
        card.lift()

    def _gap_to(self, j):
        d = self._drag
        if d["index"] == j:
            return
        d["index"] = j
        gap = d["gap"]
        gap.pack_forget()
        opts = dict(fill="x", padx=4, pady=3)
        if j < len(d["rest"]):
            gap.pack(before=self._rows[d["rest"][j]], **opts)
        elif self._tail is not None:
            gap.pack(before=self._tail, **opts)
        else:
            gap.pack(**opts)

    def _haul(self, ev):
        d = self._drag
        if not d:
            return
        y = ev.y_root - self.list.winfo_rooty()
        d["card"].place_configure(y=int(y - d["hold"]))

        # Near an edge, the list comes to meet you - a page cannot be dragged
        # somewhere you cannot see.
        top, height = self.canvas.winfo_rooty(), self.canvas.winfo_height()
        if ev.y_root < top + 26:
            self.canvas.yview_scroll(-1, "units")
        elif ev.y_root > top + height - 26:
            self.canvas.yview_scroll(1, "units")

        self._gap_to(sum(1 for m in d["mids"] if m < y))

    def _release(self, _ev):
        d, self._drag = self._drag, None
        if not d:
            return
        d["gap"].destroy()
        d["card"].place_forget()
        order = self.app._pg_ids[0]
        order.remove(d["pg"])
        order.insert(min(d["index"], len(order)), d["pg"])
        self.refill()
        self.app.pg_apply()

    # ---------------------------------------------------------- collecting
    def collect(self):
        """Walk every page and photograph it, then put the panel back.

        About three seconds for the lot. Done on demand rather than at startup:
        it moves the panel about, and doing that behind somebody's back the
        moment the app opens would be rude.
        """
        self.app.collect_shots(self._shot_done)

    def _shot_done(self, key, photo):
        """`key` is (page, sub-page) - GAME and MUSIC have several faces."""
        if photo is not None:
            self.shots[key] = photo
        self.refill()
