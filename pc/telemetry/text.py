"""Turning any text into something the panel's font can actually draw.

The OLED draws the GFX built-in font, which is ASCII. Everything else - Cyrillic,
accents, smart quotes, emoji - used to be deleted character by character, which
is fine for a stray typographic dash and a disaster for a title written entirely
in another alphabet: it came out empty.

So letters that HAVE a Latin form get one. Accents come off by decomposition;
Cyrillic goes through a table. What is left after that (Japanese, Chinese,
emoji) has no Latin form and is dropped, and the caller is expected to cope with
an empty string rather than treat it as "nothing there".
"""

import re
import unicodedata

# ';' and '=' are the wire protocol's separators and can never be let through.
UNSAFE = re.compile(r"[^A-Za-z0-9 .,_/+()\[\]:!?&'-]")

_CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d",
    "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
    # Ukrainian, Belarusian, Serbian, Macedonian
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g", "ў": "u",
    "ђ": "dj", "ј": "j", "љ": "lj", "њ": "nj",
    "ћ": "c", "џ": "dz", "ѓ": "g", "ќ": "k",
}

# Letters decomposition leaves alone, because the mark is part of the letter
# rather than something sitting on top of it.
_SPECIAL = {"ß": "ss", "æ": "ae", "ø": "o", "å": "a", "œ": "oe",
            "đ": "d", "ł": "l", "þ": "th", "ð": "d", "ı": "i"}


def translit(s):
    """Latin letters for anything that has them, unchanged for anything else."""
    out = []
    for ch in unicodedata.normalize("NFKD", str(s or "")):
        if unicodedata.combining(ch):
            continue                        # the accent off an already-split e
        low = ch.lower()
        rep = _CYRILLIC.get(low)
        if rep is None:
            rep = _SPECIAL.get(low)
        if rep is None:
            out.append(ch)
        elif not ch.isupper() or not rep:
            out.append(rep)
        elif len(rep) == 1:
            out.append(rep.upper())
        else:
            out.append(rep.capitalize())    # Zh mid-title, not ZH
    return "".join(out)


def clean(s, limit):
    """Ready for the wire: transliterated, stripped of the rest, and cut."""
    return UNSAFE.sub("", translit(s)).strip()[:limit]
