"""
person_name.py -- one key for a person's name, in Python and in SQL.

    personKey("Peña, José")        -> "jose pena"
    personKey("Smith Jr., John")   -> "john smith"
    personKey("Mary-Kate O'Brien") -> "mary kate obrien"
    personKeySql("a.first_name", "a.last_name")  the same, in Postgres
    personKeyTextSql("r.athlete_name")           one name column, "Last, First" swapped

★ WHY (2026-10-24, the name-key survey). Four name keys disagreed on real
  names, and the linkers' one DELETED accented letters instead of folding
  them: "José Peña" on one feed and "Jose Pena" on another were two people
  ("jos pea" against "jose pena"), "Lê" became "l", and "Đỗ" vanished. "Jr"
  was never a suffix, so John Smith and John Smith Jr never met. This key
  folds accents to plain letters, turns hyphens into spaces, swaps
  "Last, First", and drops a trailing Jr / Sr / II / III / IV.

! THE PYTHON AND THE SQL ARE ONE RULE. The fold is a fixed character map
  (FOLD_FROM -> FOLD_TO) both sides use through translate(), so they cannot
  drift; tests/test_person_name.py runs the SQL against the Python on hard
  cases. Nothing reads this yet but scripts/diag_name_key.py (a dry run):
  the linkers move to it after the owner has read that list.
"""
import re
import unicodedata


def _foldMap():
    """Every Latin letter with a mark, upper and lower, to its plain letter;
    plus the letters Unicode does not decompose (ø, đ, ł, æ, œ, ß, þ, ð, ı)."""
    src, dst = [], []
    for cp in list(range(0x00C0, 0x0250)) + list(range(0x1E00, 0x1F00)):
        ch = chr(cp)
        base = unicodedata.normalize("NFKD", ch)[:1]
        if base.isascii() and base.isalpha() and base != ch:
            src.append(ch)
            dst.append(base.lower())
    for a, b in (("Ø", "o"), ("ø", "o"), ("Đ", "d"), ("đ", "d"), ("Ł", "l"), ("ł", "l"),
                 ("Æ", "a"), ("æ", "a"), ("Œ", "o"), ("œ", "o"), ("ß", "s"),
                 ("Þ", "t"), ("þ", "t"), ("Ð", "d"), ("ð", "d"), ("ı", "i")):
        if a not in src:
            src.append(a)
            dst.append(b)
    return "".join(src), "".join(dst)


FOLD_FROM, FOLD_TO = _foldMap()
_FOLD = str.maketrans(FOLD_FROM, FOLD_TO)
SUFFIXES = ("jr", "sr", "ii", "iii", "iv")
_SUFFIX_RX = re.compile(r" (?:%s)$" % "|".join(SUFFIXES))


def personKey(name, min_tokens=2, keep_suffix=False):
    """The key of one name string, or None when it is not `min_tokens`
    words after cleaning. keep_suffix: Jr / Sr / II / III / IV stay (the
    linkers' key: a suffix difference can be a father and a son, owner
    2026-10-26, so it is never linked on the name alone)."""
    n = str(name or "").strip()
    if "," in n:
        last, _, first = n.partition(",")
        n = f"{first} {last}"
    n = n.translate(_FOLD).lower().replace("-", " ")
    n = re.sub(r"[^a-z ]+", "", n)
    n = " ".join(n.split())
    if not keep_suffix:
        n = _SUFFIX_RX.sub("", n)
    return n if len(n.split()) >= min_tokens else None


def personKeyParts(first, last, keep_suffix=False):
    """personKey of a first and a last name held apart."""
    return personKey(" ".join(p for p in (str(first or "").strip(), str(last or "").strip()) if p),
                     keep_suffix=keep_suffix)


def _keySql(expr, keep_suffix=False):
    folded = f"lower(translate({expr}, '{FOLD_FROM}', '{FOLD_TO}'))"
    cleaned = (f"btrim(regexp_replace(regexp_replace(replace({folded}, '-', ' '), "
               f"'[^a-z ]+', '', 'g'), '\\s+', ' ', 'g'))")
    if keep_suffix:
        return cleaned
    return f"regexp_replace({cleaned}, ' ({'|'.join(SUFFIXES)})$', '')"


def personKeySql(first, last, keep_suffix=False):
    """personKeyParts in SQL (without the two-word floor: a caller tests
    position(' ' in key) > 0, as the linkers already do)."""
    return _keySql(f"concat_ws(' ', NULLIF(btrim({first}), ''), NULLIF(btrim({last}), ''))",
                   keep_suffix)


def personKeyTextSql(col, keep_suffix=False):
    """personKey of one name column in SQL, "Last, First" swapped."""
    swapped = (f"CASE WHEN position(',' in {col}) > 0 "
               f"THEN substr({col}, position(',' in {col}) + 1) || ' ' || split_part({col}, ',', 1) "
               f"ELSE {col} END")
    return _keySql(swapped, keep_suffix)
