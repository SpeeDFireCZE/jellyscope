"""Translating the interface.

The source language is English: the templates and the code hold English
sentences directly, not artificial keys like `dashboard.title`.

```jinja
{{ _("Overview") }}
{{ _n("{n} records", total) }}
```

When another language is set, `translate()` finds the sentence in that
language's dictionary; in English it shows the template's own text.

Why sentences and not artificial keys:

* **A template is readable without the dictionary.** You see what gets
  printed, not a code.
* **A missing translation breaks nothing** - it just shows in English. With
  keys, `dashboard.title` would appear on the page, which is worse than the
  right sentence in another language. A translation may therefore be only
  half done, the usual state of a translation by volunteers.
* The drawback: when you fix a typo in the English, the translations stop
  matching. Tests guard it: every key of a dictionary exists as a literal in
  the code, and every literal the code translates is in the template.

Three shapes of a key
---------------------

* a plain sentence - `"Overview"`;
* a sentence with a note after '#' where one English text serves more
  meanings in another language - `"Name#person"`, `"Name#title"`. The note
  is never shown; the template (en.json) holds the text without it;
* a text with a count - one key per plural form of the language, `"{n}
  records_one"`, `"{n} records_other"` (Czech `_one`, `_few`, `_other`,
  Spanish `_one`, `_many`, `_other`), the i18next v4 shape Weblate reads.
  `_n()` in a template, `formatting.plural()` or `translate_plural()` in
  Python pick the form; `translate_with()` does it for a message kept as a
  key and its values (the error classes, `_flash`).

Where the sentences lie
-----------------------

In `jellyscope/translations/`, one file per language:

    translations/en.json        the template (the source): key -> English text
    translations/cs.json        Czech, a translation like the others
    translations/es.json        Spanish
    translations/log/en.json    messages for the log (the source)
    translations/log/cs.json    messages for the log in Czech

**A new language is one more file** - it appears in the list by itself,
nothing changes in the code because of it (only its plural forms belong
into PLURAL_TAGS, a test asks for them).

The log has its own files on purpose: they are technical sentences for whoever
deals with a failure, and mixing them in among the labels of buttons would
only get in the translator's way.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from pathlib import Path
from typing import Any

log = logging.getLogger("jellyscope.i18n")

TRANSLATIONS_DIR = Path(__file__).resolve().parent / "translations"

# The language the keys in the code are written in: `_("...")` holds a sentence of
# this language, and its file is the template a translation tool starts from.
SOURCE_LANGUAGE = "en"
# The language of a new installation - the choice of an account and of the server
# both fall back to it.
DEFAULT_LANGUAGE = "en"

# The plural forms of a language - the CLDR categories Weblate asks a translator
# for in the i18next v4 format (checked with translate-toolkit 3.20.6 and
# Weblate's source, 21. 11. 2026). A text with a count is one key per form:
# `{n} records_one`, `{n} records_few`, `{n} records_other`. Czech has no "many"
# here: CLDR keeps it for fractions, and translate-toolkit drops it.
#
# The rule of a language (PLURAL_RULES) picks the form for a whole number. A language
# missing here gets "one" and "other" - right for many languages, wrong for the Slavic
# ones - so a test asks for a rule for every translation file and checks that each rule
# picks exactly its language's forms. Rules for whole numbers only: counts are whole.


def _one_other(count: int) -> str:
    return "one" if count == 1 else "other"


def _czech(count: int) -> str:
    return "one" if count == 1 else "few" if 2 <= count <= 4 else "other"


def _spanish(count: int) -> str:
    if count == 1:
        return "one"
    return "many" if count and count % 1_000_000 == 0 else "other"


PLURAL_RULES: dict[str, Any] = {"cs": _czech, "en": _one_other, "de": _one_other, "es": _spanish}
PLURAL_TAGS: dict[str, tuple[str, ...]] = {
    "cs": ("one", "few", "other"),
    "en": ("one", "other"),
    "de": ("one", "other"),
    "es": ("one", "many", "other"),
}

# What a language is called in itself. Whoever is choosing a language does not
# understand the current one yet - "Deutsch" is recognised even by someone who
# cannot read Czech.
#
# The list is here so that a new translation needs no change of the code: a
# code that was forgotten shows as it is (say "PT-BR"). Adding the name is a
# welcome change, but not a necessary one.
LANGUAGE_NAMES = {
    "cs": "Čeština",
    "en": "English",
    "sk": "Slovenčina",
    "de": "Deutsch",
    "pl": "Polski",
    "es": "Español",
    "fr": "Français",
    "it": "Italiano",
    "nl": "Nederlands",
    "pt": "Português",
    "pt-br": "Português (Brasil)",
    "ru": "Русский",
    "uk": "Українська",
    "sv": "Svenska",
    "da": "Dansk",
    "nb": "Norsk bokmål",
    "fi": "Suomi",
    "hu": "Magyar",
    "ro": "Română",
    "el": "Ελληνικά",
    "tr": "Türkçe",
    "he": "עברית",
    "ar": "العربية",
    "zh": "中文",
    "zh-hans": "简体中文",
    "zh-hant": "繁體中文",
    "ja": "日本語",
    "ko": "한국어",
}


def _load(path: Path) -> dict[str, str]:
    """One file with a translation. A broken file must not bring the application down.

    Translations come from outside - from translators, from a translation
    service, from a file edited by hand. So a mistake in one language means
    that language is not used; not that the whole application does not start
    because somebody forgot a comma.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        log.error("translation %s cannot be read: %s", path.name, error)
        return {}
    if not isinstance(data, dict):
        log.error("translation %s is not a dictionary", path.name)
        return {}
    # Only text for text belongs in the dictionary. Anything else (a number, a
    # list, a nested object) would sooner or later get into a template.
    #
    # An empty text is not a translation but a **missing translation**: Weblate
    # writes untranslated keys as `"sentence": ""`. Were the emptiness taken as
    # a valid value, the page would show nothing instead of the fallback
    # English - and an empty line would stay in the log.
    return {key: value for key, value in data.items()
            if isinstance(key, str) and isinstance(value, str)
            and value.strip()}


def _load_folder(folder: Path) -> dict[str, dict[str, str]]:
    """Every language of the folder except the source (SOURCE_LANGUAGE).

    The source's file is the template: it is loaded on its own as SOURCE,
    the last step of every lookup.
    """
    if not folder.is_dir():
        return {}
    languages: dict[str, dict[str, str]] = {}
    for path in sorted(folder.glob("*.json")):
        code = path.stem.lower()
        if code == SOURCE_LANGUAGE:
            continue
        sentences = _load(path)
        if sentences:
            languages[code] = sentences
    return languages


TRANSLATIONS: dict[str, dict[str, str]] = _load_folder(TRANSLATIONS_DIR)
LOG_TRANSLATIONS: dict[str, dict[str, str]] = _load_folder(TRANSLATIONS_DIR / "log")

# The template of the source language: key -> the text shown. Mostly the key
# itself; it differs for a key with a note after '#' ("Name#person" -> "Name")
# and holds the source forms of a text with a count. The last step of every
# lookup, so a key never shows with its note or its form suffix.
SOURCE: dict[str, str] = _load(TRANSLATIONS_DIR / f"{SOURCE_LANGUAGE}.json") \
    if (TRANSLATIONS_DIR / f"{SOURCE_LANGUAGE}.json").is_file() else {}

# The log's source dictionary (applog translates the log through LOG_SOURCE and
# LOG_TRANSLATIONS), and named shortcuts for English that only the tests read; the
# other languages are taken through TRANSLATIONS.
LOG_SOURCE: dict[str, str] = _load(TRANSLATIONS_DIR / "log" / f"{SOURCE_LANGUAGE}.json") \
    if (TRANSLATIONS_DIR / "log" / f"{SOURCE_LANGUAGE}.json").is_file() else {}
EN: dict[str, str] = SOURCE if SOURCE_LANGUAGE == "en" else TRANSLATIONS.get("en", {})
LOG_EN: dict[str, str] = LOG_SOURCE if SOURCE_LANGUAGE == "en" else LOG_TRANSLATIONS.get("en", {})


def _name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code.upper())


# The languages one can switch between: the source always (it exists even
# without a translation) and one for every file, in alphabetical order.
LANGUAGES: dict[str, str] = {code: _name(code) for code in sorted({SOURCE_LANGUAGE, *TRANSLATIONS})}


# The language of the signed-in account for the request being handled (My account).
#
# Set by the middleware in web/__init__.py from the session's account. A ContextVar,
# not a global variable: requests run concurrently, and the language of one would
# otherwise overwrite the language of another. Where there is no account - the
# sign-in page, background tasks, the log, notifications - it stays empty and
# the server's language applies.
_ACCOUNT_LANGUAGE: ContextVar[str | None] = ContextVar("account_language", default=None)


def set_account_language(code: str | None) -> Any:
    """The account's language for this request. Returns a token for `reset_account_language`."""
    return _ACCOUNT_LANGUAGE.set(code or None)


def reset_account_language(token: Any) -> None:
    _ACCOUNT_LANGUAGE.reset(token)


async def without_account_language(coro: Any) -> Any:
    """Runs a background task in the server's language, not in the account's.

    `asyncio.create_task` inside a request copies the context, the language of
    whoever started the task included. A message in the tasks' log and the
    notifications would then come into being in their personal language - and
    everybody sees them. The task has its own copy of the context, so clearing
    it here affects nobody else.

    Use: `web.base.spawn(task(), name)`, which wraps the task in this, holds it and logs its failure.
    """
    _ACCOUNT_LANGUAGE.set(None)
    return await coro


def server_language() -> str:
    """The language from Settings → General - the default for everybody without a choice of their own."""
    # Imported only here, so the module can be loaded in tests without a database too.
    from . import db

    # A message about a database that cannot be reached (a transfer's target, a failing start) asks for its
    # language too: then it is the default, the message must not fall over on reading the setting.
    try:
        value = db.get_setting("ui_language", DEFAULT_LANGUAGE)
    except Exception:  # noqa: BLE001 - any failure of the database means "no setting"
        return DEFAULT_LANGUAGE
    return value if value in LANGUAGES else DEFAULT_LANGUAGE


def current_language() -> str:
    """The interface language: the account's own choice, otherwise the server's language.

    An account's choice whose file has disappeared meanwhile (a deleted
    translation) is quietly ignored - otherwise one would get stuck in a
    language that no longer exists.
    """
    own = _ACCOUNT_LANGUAGE.get()
    if own and own in LANGUAGES:
        return own
    return server_language()


# The language fallen back on when a translation is missing: English, the source.
# Whoever switches German on while the translation is two thirds done reads the
# rest in English - the language nearly everybody who got as far as a media server
# can read.
FALLBACK_LANGUAGE = "en"


def translate(text: str, language: str | None = None) -> str:
    """Translates a sentence.

    When the translation in the target language is missing, the English
    template's text is shown (a key with a note without its note), and a text
    that is no key comes back as it is. So an unfinished translation is half
    English, never empty - see FALLBACK_LANGUAGE.
    """
    language = language or current_language()
    if language != SOURCE_LANGUAGE:
        table = TRANSLATIONS.get(language, {})
        if text in table:
            return table[text]
    # The template of the source: the text of a key with a note, otherwise the
    # key itself. A text that is no key (a stored error, a value from data)
    # comes back whole, '#' and all.
    return SOURCE.get(text, text)


def plural_tag(language: str, n: Any) -> str:
    """The plural form of a count in a language: "one", "few", "many" or "other".

    Counts are whole numbers; None (missing data) counts as 0, 2.0 from SQL as 2
    and a negative difference takes the form of its absolute value.
    """
    try:
        count = abs(int(n or 0))
    except (TypeError, ValueError):
        count = 0
    return PLURAL_RULES.get(language, _one_other)(count)


def translate_plural(key: str, n: Any, language: str | None = None, /, **values: Any) -> str:
    """A text with a count in the right form: "1 záznam", "3 záznamy", "5 záznamů".

    `key` is the text without the form suffix ("{n} records"); `{n}` gets the
    number written in the sentence's language (formatting.number),
    `values` the other placeholders (whole numbers among them written the same way).

    The lookup: the language's form, its "other" (a form a translator left out),
    then the same in English and in the source template, then the key itself.
    A translation whose placeholders do not fit is skipped instead of bringing
    the page down - the next language shows the sentence.
    """
    from .formatting import number

    language = language or current_language()
    chain: list[tuple[dict[str, str], str]] = []
    if language != SOURCE_LANGUAGE:
        chain.append((TRANSLATIONS.get(language, {}), language))
    chain.append((SOURCE, SOURCE_LANGUAGE))     # the source (English) is the fallback
    shown = number(n, language)
    # The other whole numbers of the sentence ("{total} checked") are written like the count.
    values = {k: number(v, language) if isinstance(v, int) and not isinstance(v, bool) else v for k, v in values.items()}
    for table, code in chain:
        for form in (plural_tag(code, n), "other"):
            pattern = table.get(f"{key}_{form}")
            if pattern is None:
                continue
            try:
                return pattern.format(n=shown, **values)
            except (KeyError, IndexError, ValueError):
                break
    try:
        return key.format(n=shown, **values)
    except (KeyError, IndexError, ValueError):
        return key


def is_plural(key: str) -> bool:
    """Whether a key is a text with a count (the source template has its forms)."""
    return f"{key}_other" in SOURCE


def translate_with(key: str, language: str | None = None, /, **values: Any) -> str:
    """A message kept as a key and its values (an error class, _flash, a task's parts), in a language.

    A text with a count gets the form its `n` needs (translate_plural); any other message is
    translated and the values are filled in. Without values the template comes back unfilled.
    A placeholder that does not fit raises, as `.format()` does - the callers have their own rescue.
    The key and the language are positional only, so a placeholder may be called `language` too.
    """
    # A value that is itself a message (an error inside "Nothing is deleted without a backup: {reason}") is
    # read in the same language as the whole.
    values = {k: v.translated(language) if isinstance(v, (Message, TranslatableError)) else v
              for k, v in values.items()}
    if is_plural(key):
        if "n" not in values:
            return _plural_template(key, language)
        count = values.pop("n")
        return translate_plural(key, count, language, **values)
    text = translate(key, language)
    return text.format(**values) if values else text


def _plural_template(key: str, language: str | None) -> str:
    """The "other" form of a count, unfilled - for a message shown without its values."""
    language = language or current_language()
    for table in (TRANSLATIONS.get(language, {}), SOURCE):
        if f"{key}_other" in table:
            return table[f"{key}_other"]
    return key


class Message:
    """A message kept as a key and its values - for something read later, in the reader's language.

    The task log, the collector's error and an item's analysis error are written once and read by people
    with different languages. `message_parts()` gives what to store, `stored_text()` reads it back.
    `str()` is the English sentence (a log line, the tests).
    """

    __slots__ = ("template", "values")

    def __init__(self, template: str, /, **values: Any) -> None:
        self.template = template
        self.values = values

    def translated(self, language: str | None = None) -> str:
        return _rescued(self.template, self.values, language)

    def __str__(self) -> str:
        return self.translated(SOURCE_LANGUAGE)

    def __repr__(self) -> str:
        return f"Message({self.template!r}, **{self.values!r})"


class TranslatableError(RuntimeError):
    """An error a person reads: a key of the English template and its values, apart.

    Only the template can be translated - a finished sentence with a path or a number in it is in no
    dictionary. `str(error)` is the English source with the values filled in (a log line, a traceback,
    the tests); whoever shows the error asks for `error_text(error)`, whoever stores it (the task log)
    for `message_parts(error)` and reads it back with `stored_text`. The errors of Jellyfin's client,
    of ffprobe, of an import and of the other applications' clients (services.ServiceError) are of
    this kind.
    """

    def __init__(self, template: str, /, **values: Any) -> None:
        self.template = template
        self.values = values
        super().__init__(_rescued(template, values, SOURCE_LANGUAGE))

    def translated(self, language: str | None = None) -> str:
        """The text in a language (a count in its form); without a translation English."""
        return _rescued(self.template, self.values, language)


def error_text(error: BaseException, language: str | None = None) -> str:
    """What a person reads of an error: a translatable one in their language, any other as it is."""
    return error.translated(language) if isinstance(error, TranslatableError) else str(error)


def _rescued(template: str, values: dict[str, Any], language: str | None) -> str:
    """A key with its values in a language - never an exception.

    A translation whose placeholders do not fit (a typo from Weblate) falls back to the English template
    with the values; when not even that fits, the template itself is shown. A task's result or an error
    message must not fail because of a translator's slip.
    """
    for code in (language, SOURCE_LANGUAGE):
        try:
            return translate_with(template, code, **values)
        except (KeyError, IndexError, ValueError, TypeError, AttributeError):
            continue
    return template


def join_messages(parts: list[Any], separator: str = " ") -> Message:
    """Several messages as one (the results of the *Arr apps, a sentence and its additions).

    Each part is translated on its own when the whole is read; a text part is a key (or a finished text,
    which stays as it is).
    """
    template = separator.join(f"{{p{index}}}" for index in range(len(parts)))
    return Message(template, **{f"p{index}": Message(part) if isinstance(part, str) else part
                                for index, part in enumerate(parts)})


# The longest text a stored value keeps - like the stored template (scan_log cuts it at 500): an error's
# text with a whole answer of a server in it would otherwise fill the task log.
MAX_VALUE_CHARS = 500


def _to_json(value: Any) -> Any:
    """A value as JSON holds it: numbers, a bool and None as they are, a message nested, the rest as text
    of at most MAX_VALUE_CHARS characters."""
    if isinstance(value, (Message, TranslatableError)):
        return {"template": value.template, "values": {k: _to_json(v) for k, v in value.values.items()}}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:MAX_VALUE_CHARS]


def _from_json(value: Any) -> Any:
    if isinstance(value, dict) and isinstance(value.get("template"), str):
        values = value.get("values") if isinstance(value.get("values"), dict) else {}
        return Message(value["template"], **{k: _from_json(v) for k, v in values.items()})
    return value


def message_parts(message: str | Message | BaseException) -> tuple[str, str | None]:
    """What to store of a message: (the template, its values as JSON or None).

    A text is its own template; a message or a translatable error keeps its key and values; any other
    error is its text. Read it back with `stored_text()`.
    """
    if isinstance(message, (Message, TranslatableError)):
        values = {k: _to_json(v) for k, v in message.values.items()}
        return message.template, (json.dumps(values, ensure_ascii=False) if values else None)
    return str(message), None


def stored_text(template: str | None, values: str | None, language: str | None = None) -> str:
    """A stored message (`message_parts()`) in a language - the reader's when None.

    Without values the template is translated: a key becomes the language's sentence, an old row (a
    finished sentence) comes back as it was written. When the values do not fit the translation (a
    translator dropped a placeholder), the English template is filled in; when not even that works, the
    template is shown. Reading a message never fails the page.
    """
    if not template:
        return ""
    try:
        decoded = json.loads(values) if values else None
    except ValueError:
        decoded = None
    if not isinstance(decoded, dict) or not decoded:
        return translate(template, language)
    return _rescued(template, {k: _from_json(v) for k, v in decoded.items()}, language)


class UiName(str):
    """A name from the interface handed to the log as a value ("scheduled task: %s" with a task's
    name): a log in another language translates it. Every other value of a log message is data -
    a token's or a library's name, a status - and stays as it is, even when it equals a key."""


def translate_log(text: str, language: str) -> str:
    """The same for messages in the log - they have a dictionary and a choice of their own.

    The log is read by foreign eyes too (it is passed on with a failure), so
    a message without a translation stays in English, the source.
    """
    if language == SOURCE_LANGUAGE:
        return text
    return LOG_TRANSLATIONS.get(language, {}).get(text, text)


def register(env: Any) -> None:
    """Makes the translation available to templates as the function `_`."""
    env.globals["_"] = translate
    env.globals["_n"] = translate_plural
    env.globals["stored_text"] = stored_text
    env.globals["ui_languages"] = LANGUAGES
