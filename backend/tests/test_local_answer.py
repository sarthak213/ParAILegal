from app.answer import prompts
from app.answer.gate import ANSWER, CAVEAT
from app.answer.pipeline import prepare
from app.llm.server import NOT_INSTALLED, STOPPED, LlamaServer


# ── Prompts ───────────────────────────────────────────────────────────

def test_mode_names():
    assert prompts.mode_of(None) == prompts.RESEARCH
    assert prompts.mode_of("ADVOCATE") == prompts.ADVOCATE
    assert prompts.mode_of("Summarize") == prompts.SUMMARISE
    assert prompts.mode_of("nonsense") == prompts.RESEARCH


def test_messages_carry_sources_question_and_caveat():
    msgs = prompts.messages("Is X legal?", "[1] Section 1", prompts.RESEARCH, CAVEAT)
    assert msgs[0]["role"] == "system" and "only the numbered sources" in msgs[0]["content"]
    user = msgs[1]["content"]
    assert user.index("[1] Section 1") < user.index("Question: Is X legal?")
    assert prompts.CAVEAT_INSTRUCTION in user
    assert prompts.CAVEAT_INSTRUCTION not in prompts.messages("q", "c", prompts.RESEARCH, ANSWER)[1]["content"]


def test_code_written_parts():
    assert prompts.lead(CAVEAT) and not prompts.lead(ANSWER)
    assert "15100" in prompts.tail(prompts.SUMMARISE) and prompts.tail(prompts.RESEARCH) == ""


# ── Server start modes ────────────────────────────────────────────────

def server(tmp_path, device="auto", builds=("cpu", "vulkan"), model=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    for b in builds:
        (tmp_path / "engine" / b).mkdir(parents=True)
        (tmp_path / "engine" / b / "llama-server.exe").write_bytes(b"")
    if model:
        (tmp_path / "m.gguf").write_bytes(b"")
    return LlamaServer(tmp_path / "engine", tmp_path / "m.gguf", 6, 8, 8192, 600, device)


def test_gpu_first_then_cpu(tmp_path):
    s = server(tmp_path)
    assert s.modes() == ["vulkan", "vulkan-nocoopmat", "cpu"]
    s.mode = "cpu"  # the mode that worked last time goes first
    assert s.modes()[0] == "cpu"


def test_device_setting_and_missing_builds(tmp_path):
    assert server(tmp_path / "a", "cpu").modes() == ["cpu"]
    assert server(tmp_path / "b", "gpu").modes() == ["vulkan", "vulkan-nocoopmat"]
    assert server(tmp_path / "c", builds=("cpu",)).modes() == ["cpu"]  # no Vulkan build shipped


def test_command(tmp_path):
    s = server(tmp_path)
    args, env = s.command("vulkan-nocoopmat", 1234)
    assert args[0].endswith("vulkan\\llama-server.exe") or args[0].endswith("vulkan/llama-server.exe")
    assert {"--reasoning", "off"} <= set(args) and "--n-gpu-layers" in args
    assert args[args.index("--parallel") + 1] == "1" and args[args.index("--threads") + 1] == "6"
    assert env == {"GGML_VK_DISABLE_COOPMAT": "1"}
    cpu_args, cpu_env = s.command("cpu", 1234)
    assert "--n-gpu-layers" not in cpu_args and cpu_env == {}


def test_states(tmp_path):
    assert server(tmp_path / "a").state == STOPPED
    assert server(tmp_path / "b", model=False).state == NOT_INSTALLED
    assert server(tmp_path / "c", builds=()).state == NOT_INSTALLED


# ── Mode through the pipeline ─────────────────────────────────────────

class Engine:
    def search(self, query, k):
        return [{"_ref": "BNS 103", "_exact": True, "_ce": None, "_cos": 0.9, "chunk_id": "bns::103::1",
                 "citation": "Section 103 — Punishment for murder.", "text": "Whoever commits murder ..."}]

    def unknown_citations(self, query):
        return []

    def provision(self, ref):
        return []


def test_mode_from_prefix_or_field():
    p = prepare("SUMMARISE: Section 103 BNS", Engine(), 15)
    assert p.mode == prompts.SUMMARISE and p.question == "Section 103 BNS"
    p = prepare("Section 103 BNS", Engine(), 15, mode="advocate")
    assert p.mode == prompts.ADVOCATE and p.question == "Section 103 BNS"
