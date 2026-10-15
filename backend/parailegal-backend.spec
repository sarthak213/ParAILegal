# PyInstaller recipe for the desktop backend (docs/PACKAGING.md, step 4).
#
#     .venv/Scripts/pyinstaller parailegal-backend.spec --noconfirm
#
# A one-folder build in dist/parailegal-backend/: the Tauri shell runs parailegal-backend.exe as
# its sidecar (backend/desktop_server.py). The law, the search models and the llama.cpp engine are
# not in it: the installer puts them in the resources folder, and the models and judgments are
# downloaded to the data folder (app/setup/download.py).
#
# Left out: the v1 cloud clients (Qdrant, Cohere, FAISS), imported only when SEARCH_ENGINE=v1, and
# the build-time libraries (PyArrow, PDFium, PyInstaller itself), which the app never imports.

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

V1_ONLY = ("app.services.rag_services", "app.services.retrieval", "app.infrastructure.llm",
           "app.infrastructure.qdrant", "app.infrastructure.faiss")
hidden = (
    collect_submodules("app", filter=lambda name: not name.startswith(V1_ONLY))  # routes, lazy imports
    + collect_submodules("uvicorn")       # loops and protocols chosen at run time
    + ["fastembed", "onnxruntime", "tokenizers"]
)
datas = collect_data_files("fastembed") + [("app/setup/manifest.json", "app/setup")]

a = Analysis(
    ["desktop_server.py"],
    pathex=["."],
    hiddenimports=hidden,
    datas=datas,
    excludes=["qdrant_client", "cohere", "faiss", "groq", "pyarrow", "pypdfium2", "pandas", "matplotlib",
              "tkinter", "pytest", "IPython", "mcp", "PyInstaller", "huggingface_hub.cli", *V1_ONLY],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="parailegal-backend", console=False,
          upx=False, icon=None)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="parailegal-backend")
