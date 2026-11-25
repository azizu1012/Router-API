"""The dashboard's three language tables must carry the same keys, non-empty.

`t(key, lang)` in frontend-src/src/utils/i18n.js falls back:

    TR[lang]?.[key] || TR['vi']?.[key] || key

Both failure modes are silent in the browser. A key missing from `en` renders
Vietnamese, and so does a key whose `en` value is the empty string, because `""`
is falsy and the `||` walks straight to the Vietnamese table. That second form is
how `stg_unit_times` shipped: `en: ""` printed "lần" on the Settings sliders
under English while every key was nominally present.

So this test checks presence AND non-empty-ness, in both directions, by running
the real module through node — regex over the source file is what missed the
empty value the first time. It skips when node is unavailable rather than
pretending the check passed.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
I18N = REPO / "frontend-src" / "src" / "utils" / "i18n.js"
LANGS = ("vi", "en", "ja")


needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed; cannot import the ESM table"
)


@pytest.fixture(scope="module")
def tables():
    """Import the real TR object so the parser cannot drift from the source."""
    assert I18N.exists(), f"missing {I18N}"
    script = (
        "import(process.argv[1]).then(m => "
        "process.stdout.write(JSON.stringify(m.TR)))"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script, I18N.as_uri()],
        capture_output=True, text=True, timeout=60, cwd=str(REPO),
    )
    if out.returncode != 0:
        pytest.fail(f"node could not import i18n.js:\n{out.stderr[-2000:]}")
    return json.loads(out.stdout)


@needs_node
def test_all_three_tables_are_present(tables):
    assert set(tables) == set(LANGS), sorted(tables)


@needs_node
def test_every_key_exists_in_every_language(tables):
    keys = set().union(*(tables[lang] for lang in LANGS))
    missing = [
        f"{lang}:{key}"
        for lang in LANGS for key in sorted(keys)
        if key not in tables[lang]
    ]
    assert not missing, f"{len(missing)} translations missing: {missing[:40]}"


@needs_node
def test_no_translation_is_an_empty_string(tables):
    """An empty string is the dangerous one: `||` turns it into Vietnamese."""
    empty = [
        f"{lang}:{key}"
        for lang in LANGS
        for key, value in tables[lang].items()
        if isinstance(value, str) and not value.strip()
    ]
    assert not empty, f"empty translations render as Vietnamese: {empty}"


VIETNAMESE = re.compile(
    "[àáảãạăâđêôơưÀÁẢÃẠĂÂĐÊÔƠƯẠ-ỹ]"
)


@needs_node
def test_no_english_or_japanese_value_is_vietnamese_prose(tables):
    """A copied-over string leaks Vietnamese text, which is exactly what users reported.

    Identical values are fine when they are proper nouns or English loanwords
    ("Router API", "Token", "RPM" are the same in all three tables). What is never
    fine is Vietnamese *prose* sitting in the en/ja table — diacritics are the tell.
    """
    leaked = [
        f"{lang}:{key} = {value[:50]!r}"
        for lang in ("en", "ja")
        for key, value in tables[lang].items()
        if isinstance(value, str) and VIETNAMESE.search(value)
    ]
    assert not leaked, f"{len(leaked)} Vietnamese strings in a non-vi table: {leaked[:40]}"


@needs_node
def test_the_fallback_cannot_be_triggered_by_a_missing_key():
    """Guard the exact shape of t(): if the fallback changes, this test should be revisited."""
    source = I18N.read_text(encoding="utf-8")
    assert "TR[lang]?.[key] || TR['vi']?.[key] || key" in source, (
        "t()'s fallback chain changed — re-check whether empty values still fall "
        "through to Vietnamese"
    )