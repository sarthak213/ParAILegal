import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SHOW = ("import json; from app.core import config as c; from app.judgments import store, law_at_time as l; "
        "from app.search import dense; print(json.dumps({'pack': str(c.settings.PACK_DIR), "
        "'llm': str(c.settings.LLM_MODEL_PATH), 'case': str(c.settings.CASE_MODEL_PATH), "
        "'index': str(store.INDEX_DIR), 'amend': str(l.AMENDMENTS_DIR), 'models': str(dense.MODELS_DIR)}))")


def paths(env: dict) -> dict:
    out = subprocess.run([sys.executable, "-c", SHOW], cwd=BACKEND, capture_output=True, text=True,
                         env={**os.environ, **env}, check=True).stdout
    return {k: Path(v) for k, v in json.loads(out.strip().splitlines()[-1]).items()}


def test_development_keeps_everything_in_backend():
    p = paths({"PARAILEGAL_RESOURCES": "", "PARAILEGAL_HOME": ""})
    assert p["pack"] == BACKEND / "data" / "pack" and p["index"] == BACKEND / "data" / "judgments" / "index"
    assert p["llm"].parent == BACKEND / "models" / "llm"


def test_an_install_reads_the_law_from_resources_and_downloads_from_home(tmp_path):
    res, home = tmp_path / "app", tmp_path / "home"
    p = paths({"PARAILEGAL_RESOURCES": str(res), "PARAILEGAL_HOME": str(home)})
    assert p["pack"] == res / "data" / "pack" and p["amend"] == res / "data" / "amendments"
    assert p["models"] == res / "models"                      # search models ship with the app
    assert p["llm"] == home / "models" / "llm" / "Qwen3.5-4B-Q4_K_M.gguf"
    assert p["case"].parent == home / "models" / "llm"
    assert p["index"] == home / "data" / "judgments" / "index"  # downloaded on first run
