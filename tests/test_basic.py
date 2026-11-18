import json, os, subprocess, sys

HERE = os.path.dirname(__file__)


def run(*args):
    env = dict(os.environ, PYTHONPATH=os.path.join(HERE, "..", "src"))
    p = subprocess.run([sys.executable, "-m", "clippings2json", os.path.join(HERE, "sample.txt"), *args],
                       capture_output=True, text=True, env=env, check=True)
    return json.loads(p.stdout)


def test_grouped():
    d = run()
    lib = {b["title"]: b for b in d["library"]}
    lh = lib["The Left Hand of Darkness"]
    assert lh["author"] == "Le Guin, Ursula K."
    assert len(lh["highlights"]) == 1                      # edited highlight replaced the old one
    h = lh["highlights"][0]
    assert h["text"].endswith("right hand of light.")
    assert h["notes"][0]["text"] == "epigraph for the zine"  # note attached
    assert h["added"] == "2026-11-10T15:05:00"
    assert lib["some-scanned-manual"]["stats"]["highlights_without_text"] == 1
    assert len(d["unparsed"]) == 1                           # Spanish entry needs --llm


def test_flat_and_stable_ids():
    a, b = run("--flat"), run("--flat")
    assert [c["id"] for c in a["clippings"]] == [c["id"] for c in b["clippings"]]
    note = [c for c in a["clippings"] if c["type"] == "note"][0]
    assert note["highlight_id"]


if __name__ == "__main__":
    test_grouped(); test_flat_and_stable_ids(); print("ok")
