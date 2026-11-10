"""Text normalization shared by the dictionary build and the search. Change NORM_VERSION when the rules change."""
import re
import unicodedata

NORM_VERSION = 1

_FOLD = str.maketrans({"ø": "o", "ł": "l", "đ": "d", "æ": "ae", "œ": "oe", "ı": "i", "ð": "d", "þ": "th"})
_NONWORD = re.compile(r"[\W_]+")
CZECH_CHARS = set("áčďéěíňóřšťúůýž")

REFLEXIVE = {"se", "si"}
# Valency placeholders in dictionary headwords ("take care of sth", "dát někomu vědět").
PLACEHOLDERS = {
    "sth", "smth", "sb", "smb", "ones", "oneself", "someone", "somebody", "something",
    "neco", "neceho", "necemu", "necim", "nekdo", "nekoho", "nekomu", "nekym", "nekom",
}

# Czech and English auxiliaries (past tense 'zapomněl jsem', 'is', 'did').
AUXILIARY = {"jsem", "jsi", "jsme", "jste", "budu", "budes", "bude", "budeme", "budete", "budou", "bych", "bys",
             "by", "bychom", "byste", "am", "is", "are", "was", "were", "will", "would", "have", "has", "had", "do",
             "does", "did"}
# Unstressed object pronouns that sit between a verb and the rest of a phrase ('mám tě rád' ~ 'mít rád').
CLITICS = {"te", "me", "mne", "ti", "mi", "ho", "mu", "ji", "nas", "vas", "jim", "nam", "vam"}
FUNCTION_WORDS = AUXILIARY | CLITICS | {
    "na", "v", "ve", "do", "z", "ze", "s", "se", "si", "k", "ke", "o", "u", "po", "za", "pro", "pri", "od", "to", "ten",
    "ta", "ty", "tom", "tu", "a", "i", "ze", "mi", "mu", "ho", "ji", "me", "te", "nas", "vas", "jim", "je", "jsou",
    "you", "he", "she", "it", "we", "they", "him", "her", "them", "my", "your", "his", "its", "our", "their", "on",
    "in", "at", "of", "for", "with", "the", "an", "be", "been", "this", "that"}
STOPWORDS = {"the", "and", "for", "you", "your", "are", "was", "with", "from", "that", "this", "not",
             "jsem", "jsi", "jsou", "bude", "byl", "ale", "nebo", "jak", "pro", "jeho", "její", "jejich", "který"}


def norm(text: str) -> str:
    """Lowercase, fold diacritics and punctuation: 'Dát si POZOR!' -> 'dat si pozor'."""
    s = text.casefold()
    s = s.replace("one's", "ones").replace("one’s", "ones")
    s = s.translate(_FOLD)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("'", "").replace("’", "")
    return _NONWORD.sub(" ", s).strip()


def has_czech_chars(text: str) -> bool:
    return any(c in CZECH_CHARS for c in text.lower())


def has_non_ascii_letter(text: str) -> bool:
    return any(c.isalpha() and ord(c) > 127 for c in text)


def core_tokens(tokens: list[str]) -> list[str]:
    """Tokens without reflexive se/si and placeholders; falls back to all tokens if nothing would remain."""
    core = [t for t in tokens if t not in REFLEXIVE and t not in PLACEHOLDERS]
    return core or list(tokens)


def deletions(word: str) -> set[str]:
    """All strings with exactly one character removed (for the typo index)."""
    return {word[:i] + word[i + 1:] for i in range(len(word))}


def osa_distance(a: str, b: str, limit: int = 2) -> int:
    """Optimal string alignment distance (Damerau-Levenshtein without repeated edits), capped at limit+1."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2 = None
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[-1]
