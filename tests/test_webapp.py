"""The Web App's files (section 5): served under the per-release path, complete, and translated.

The screens themselves are plain JavaScript in backend/webapp; these tests check what can go wrong
without a browser: a missing file, a path outside the folder, and a text without its Uzbek and Russian
translation (NFR-15), including every refusal the backend can send."""

import re
from pathlib import Path

from backend.config import webapp_version

from .conftest import auth

WEBAPP = Path(__file__).parent.parent / "backend" / "webapp"
BACKEND = Path(__file__).parent.parent / "backend"
SCREENS = ("app.js", "progress.js", "payments.js", "users.js", "settings.js", "useminute.js")


def translations() -> dict[str, tuple[str, str]]:
    """English → (Uzbek, Russian), read from i18n.js rows ["English", "Uzbek", "Russian"]."""
    source = (WEBAPP / "i18n.js").read_text()
    string = r'"((?:[^"\\]|\\.)*)"'
    rows = re.findall(r"\[\s*" + string + r",\s*" + string + r",\s*" + string + r"\s*\]", source)
    return {en: (uz, ru) for en, uz, ru in rows}


def keys_used_by_screens() -> set[str]:
    """Every literal text the screens pass to t(), including both sides of t(cond ? "A" : "B")."""
    keys = set()
    for name in SCREENS:
        source = (WEBAPP / name).read_text()
        for call in re.finditer(r"\bt\(([^()]*(?:\([^()]*\)[^()]*)*)\)", source):
            head = call.group(1).split("{", 1)[0]  # the text, not the values passed with it
            # literals compared with === are values, not texts
            keys.update(re.findall(r'(?<![=!]== )"((?:[^"\\]|\\.)*)"', head))
        # texts kept in tables and looked up later: ["key", "Text"] pairs and { key: "Text" } maps
        keys.update(re.findall(r'\[\s*"[a-z_]*"\s*,\s*"([A-Z][^"]*)"\s*\]', source))
        for table in re.findall(r"const (?:TAB_NAMES|ACTIONS|STATE|STATUS_CHIP|ROLE)\w* = \{(.*?)\};", source, re.S):
            keys.update(re.findall(r'"([A-Z][^"]+)"', table))
    return {k for k in keys if re.search("[A-Za-z]", k) and k not in ("en", "uz", "ru")}


async def test_the_web_app_is_served_under_its_release_path(client, settings):
    base = f"/webapp/v/{webapp_version()}/"
    page = await client.get(base)
    assert page.status_code == 200 and "App.start()" in page.text
    for script in re.findall(r'<script src="([^":]+)"', page.text):  # every file the page loads exists
        r = await client.get(base + script)
        assert r.status_code == 200, script
    assert (await client.get(base + "app.css")).status_code == 200
    assert settings.webapp_url.endswith(base)


async def test_files_outside_the_web_app_folder_are_not_served(client):
    base = f"/webapp/v/{webapp_version()}/"
    for path in ("../config.py", "..%2Fconfig.py", "nothing-here.js"):
        assert (await client.get(base + path)).status_code == 404


def test_a_new_release_of_the_files_gets_a_new_path():
    """Phones that cached the old page load the new one (Settings.webapp_url)."""
    assert re.fullmatch(r"[0-9a-f]{10}", webapp_version())


def test_every_text_on_screen_has_uzbek_and_russian():
    table = translations()
    missing = sorted(keys_used_by_screens() - set(table))
    assert not missing, f"no translation in i18n.js for: {missing}"
    empty = [en for en, (uz, ru) in table.items() if not uz.strip() or not ru.strip()]
    assert not empty


def test_translations_keep_every_placeholder():
    """A {value} in the English text must survive in both translations, or it would show blank."""
    for en, (uz, ru) in translations().items():
        wanted = set(re.findall(r"\{(\w+)\}", en))
        assert set(re.findall(r"\{(\w+)\}", uz)) == wanted, en
        assert set(re.findall(r"\{(\w+)\}", ru)) == wanted, en


def test_every_refusal_from_the_backend_is_translated():
    """The Web App shows the backend's English refusal in the user's language (APP-8)."""
    messages = set()
    for path in BACKEND.rglob("*.py"):
        source = path.read_text()
        messages.update(re.findall(r'(?:AppError\(\s*"[a-z_]+",|forbidden\(|not_found\()\s*"([^"]+)"', source))
        messages.update(re.findall(r'^TAKEN = "([^"]+)"', source, re.M))
        messages.update(re.findall(r'AppError\(\s*"[a-z_]+",\s*\n\s*"([^"]+)"', source))
        messages.update(re.findall(r'InitDataError\("([^"]+)"\)', source))
    messages.add("Check what you entered and try again")  # every invalid request (backend/main.py)
    table = translations()
    missing = sorted(m for m in messages if m not in table)
    assert not missing, f"backend refusals with no translation in i18n.js: {missing}"


async def test_the_web_app_reads_the_language_the_user_chose(client, team):
    """APP-8: the choice made in the Web App is what /api/me hands back on the next load."""
    r = await client.post("/api/me/language", json={"language": "ru"}, headers=auth(team.ali))
    assert r.status_code == 200
    assert (await client.get("/api/me", headers=auth(team.ali))).json()["user"]["language"] == "ru"
