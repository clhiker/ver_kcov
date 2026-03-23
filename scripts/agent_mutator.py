import argparse
import json
import os
import random
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT_TEMPLATE_PATH = REPO_ROOT / "prompts" / "agent_mutator_prompt.txt"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.coverage_db import CoverageDatabase
from core.path_fingerprinter import PathFingerprinter
from core.pc_resolver import PCResolver
from scripts.vm_utils import VMConfig, detect_guest_workdir, run_guest_command, to_repo_relative
from utils.config import Config, DEFAULT_CONFIG_PATH, load_project_config


def log(message: str) -> None:
    print(message, flush=True)


@dataclass
class AttemptResult:
    attempt: int
    asm_path: str
    obj_path: str
    pcs_path: str
    compile_ok: bool
    verifier_ok: bool
    compile_error: str = ""
    verifier_log: str = ""
    pc_count: int = 0
    path_hash: str = ""
    path_status: str = "unknown"
    existing_testcase_count: int = 0
    target_line: Optional[int] = None
    target_hit: bool = False
    verifier_lines: list[int] | None = None


@dataclass
class LLMResponse:
    content: str


@dataclass
class MutationContextLine:
    index: int
    source_line: int
    text: str


@dataclass
class GapCandidate:
    line_number: int
    context: str


@dataclass
class AutoSelection:
    seed_path: Path
    target_text: str
    metadata: dict[str, Any]


@dataclass
class PathCluster:
    path_hash: str
    testcase_names: list[str]
    sequence: list[str]
    testcase_count: int


def resolve_openai_compat_settings(config: Config, explicit_model: str) -> tuple[str, str, str]:
    base_url = config.openai_base_url.strip()
    api_key = config.openai_api_key.strip()
    model = explicit_model or config.openai_model.strip() or config.agent_model

    if not base_url:
        raise RuntimeError("config/kcov_config.yaml 中缺少 openai_base_url。")
    if not api_key:
        raise RuntimeError("config/kcov_config.yaml 中缺少 openai_api_key。")

    return base_url, api_key, model


def testcase_to_source_path(testcase_name: str) -> Optional[Path]:
    base_idx = testcase_name.replace("re_", "").replace(".o", "")
    candidates = [
        REPO_ROOT / "testcases" / "code" / f"{base_idx}.c",
        REPO_ROOT / "mid-cases" / "code" / f"{base_idx}.c",
        REPO_ROOT / "mini-cases" / "code" / f"{base_idx}.c",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def sequence_jaccard(seq1: list[str], seq2: list[str]) -> float:
    s1 = set(seq1)
    s2 = set(seq2)
    if not s1 or not s2:
        return 0.0
    return len(s1 & s2) / len(s1 | s2)


def sequence_order_similarity(seq1: list[str], seq2: list[str]) -> float:
    import difflib

    if not seq1 or not seq2:
        return 0.0
    return difflib.SequenceMatcher(None, seq1, seq2).ratio()


def combined_path_similarity(seq1: list[str], seq2: list[str]) -> float:
    return 0.6 * sequence_jaccard(seq1, seq2) + 0.4 * sequence_order_similarity(seq1, seq2)


def find_gap_candidates(db_path: str, verifier_src: Path) -> list[GapCandidate]:
    if not verifier_src.exists():
        return []

    with CoverageDatabase(db_path) as db:
        covered = db.get_covered_lines_by_file("kernel/bpf/verifier.c")

    src_lines = verifier_src.read_text(encoding="utf-8").splitlines()
    gaps: list[GapCandidate] = []
    for idx, line in enumerate(src_lines):
        line_num = idx + 1
        if line_num not in covered:
            continue
        if not re.match(r"^\s*if\s*\(", line):
            continue

        inner_hit = any((line_num + offset) in covered for offset in range(1, 4))
        if inner_hit:
            continue

        context = "\n".join(src_lines[idx:idx + 4]).strip()
        gaps.append(GapCandidate(line_number=line_num, context=context))

    return gaps


def get_path_clusters(config: Config) -> list[PathCluster]:
    db = CoverageDatabase(config.db_path)
    try:
        summaries = db.get_execution_paths_summary()
        clusters: list[PathCluster] = []
        for item in summaries:
            sequence_map = db.get_execution_path_sequence(item["path_hash"])
            flat_sequence: list[str] = []
            for file_path, lines in sorted(sequence_map.items()):
                for line in lines:
                    flat_sequence.append(f"{file_path}:{line}")

            clusters.append(PathCluster(
                path_hash=item["path_hash"],
                testcase_names=item["testcases"],
                sequence=flat_sequence,
                testcase_count=len(item["testcases"]),
            ))
        return clusters
    finally:
        db.close()


def choose_sparse_enrichment_selection(config: Config) -> AutoSelection:
    clusters = get_path_clusters(config)
    dense_clusters = [item for item in clusters if item.testcase_count >= 2]
    sparse_clusters = [item for item in clusters if item.testcase_count == 1]

    if not dense_clusters:
        raise RuntimeError("当前 enrichment 模式下没有可用的稠密执行路径。")
    if not sparse_clusters:
        raise RuntimeError("当前 enrichment 模式下没有可用的稀疏执行路径。")

    best_pair = None
    best_score = -1.0
    for sparse in sparse_clusters:
        for dense in dense_clusters:
            score = combined_path_similarity(sparse.sequence, dense.sequence)
            if score > best_score:
                best_score = score
                best_pair = (dense, sparse)

    assert best_pair is not None
    dense, sparse = best_pair

    selected_source = None
    selected_testcase = None
    for testcase_name in dense.testcase_names:
        source = testcase_to_source_path(testcase_name)
        if source is not None:
            selected_source = source
            selected_testcase = testcase_name
            break

    if selected_source is None:
        raise RuntimeError("无法将稠密路径测试用例反查回对应的源码 .c 文件。")

    sparse_preview = ", ".join(sparse.sequence[:12]) if sparse.sequence else "<no-sequence>"
    dense_preview = ", ".join(dense.sequence[:12]) if dense.sequence else "<no-sequence>"
    target_text = (
        "Objective: path enrichment from dense to sparse.\n"
        f"Target sparse path hash: {sparse.path_hash}\n"
        f"Target sparse testcase count: {sparse.testcase_count}\n"
        f"Source dense path hash: {dense.path_hash}\n"
        f"Source dense testcase count: {dense.testcase_count}\n"
        f"Dense-to-sparse similarity score: {best_score:.4f}\n"
        f"Target sparse path preview: {sparse_preview}\n"
        f"Current dense path preview: {dense_preview}\n"
        "Goal: mutate the dense-path seed so it migrates toward the sparse target path "
        "or produces additional nearby variants that deepen testing around that sparse behavior."
    )

    return AutoSelection(
        seed_path=selected_source,
        target_text=target_text,
        metadata={
            "objective": "enrich_sparse",
            "selected_dense_testcase": selected_testcase,
            "selected_dense_path_hash": dense.path_hash,
            "selected_sparse_path_hash": sparse.path_hash,
            "similarity_score": best_score,
            "dense_testcase_count": dense.testcase_count,
            "sparse_testcase_count": sparse.testcase_count,
        },
    )


def choose_new_path_generation_selection(config: Config) -> AutoSelection:
    clusters = get_path_clusters(config)
    dense_clusters = [item for item in clusters if item.testcase_count >= 2]
    candidate_clusters = dense_clusters or clusters
    if not candidate_clusters:
        raise RuntimeError("当前数据库中没有可用于新路径生成模式的执行路径摘要。")

    candidate_clusters.sort(key=lambda item: item.testcase_count, reverse=True)

    selected_source = None
    selected_testcase = None
    selected_cluster = None
    for cluster in candidate_clusters:
        for testcase_name in cluster.testcase_names:
            source = testcase_to_source_path(testcase_name)
            if source is not None:
                selected_source = source
                selected_testcase = testcase_name
                selected_cluster = cluster
                break
        if selected_source is not None:
            break

    if selected_source is None or selected_cluster is None:
        raise RuntimeError("在新路径生成模式下，无法将测试用例反查回对应的源码 .c 文件。")

    gaps = find_gap_candidates(config.db_path, REPO_ROOT / "verifier.c")
    if gaps:
        gap = gaps[0]
        target_text = (
            "Objective: path generation and diversity expansion.\n"
            f"Target verifier line {gap.line_number}\n{gap.context}\n"
            f"Source dense path hash: {selected_cluster.path_hash}\n"
            f"Source dense testcase count: {selected_cluster.testcase_count}\n"
            "Goal: discover genuinely new execution paths around this uncovered verifier branch, "
            "and prefer structurally diverse variants rather than repeatedly reproducing the same dense path."
        )
        gap_meta = {"line_number": gap.line_number, "context": gap.context}
    else:
        target_text = (
            "Objective: path generation and diversity expansion.\n"
            f"Source dense path hash: {selected_cluster.path_hash}\n"
            f"Source dense testcase count: {selected_cluster.testcase_count}\n"
            "Goal: discover genuinely new execution paths and produce diverse verifier behaviors."
        )
        gap_meta = {"line_number": None, "context": ""}

    return AutoSelection(
        seed_path=selected_source,
        target_text=target_text,
        metadata={
            "objective": "generate_new",
            "selected_dense_testcase": selected_testcase,
            "selected_dense_path_hash": selected_cluster.path_hash,
            "dense_testcase_count": selected_cluster.testcase_count,
            "gap": gap_meta,
        },
    )


def choose_auto_selection(config: Config, objective: str) -> AutoSelection:
    if objective == "generate_new":
        return choose_new_path_generation_selection(config)
    return choose_sparse_enrichment_selection(config)


class MutationEvaluator:
    def __init__(self, config: Config, vm_config: VMConfig, out_dir: Path, target_line: Optional[int] = None):
        self.config = config
        self.vm_config = vm_config
        self.out_dir = out_dir
        self.target_line = target_line
        self.run_prefix = out_dir.name
        self.fingerprinter = PathFingerprinter(config)
        self.resolver = PCResolver(config)
        self.db = CoverageDatabase(config.db_path)
        self.guest_workdir = detect_guest_workdir(vm_config)

    def close(self):
        self.db.close()

    def evaluate(self, asm_code: str, attempt: int) -> AttemptResult:
        basename = f"{self.run_prefix}_attempt_{attempt:02d}"
        asm_path = self.out_dir / f"{basename}.s"
        obj_path = self.out_dir / f"{basename}.o"
        pcs_path = self.out_dir / f"{basename}.pcs"
        log_path = self.out_dir / f"{basename}.verifier.log"

        asm_path.write_text(asm_code, encoding="utf-8")

        compile_cmd = [
            "clang",
            "-O2",
            "-g",
            "-target",
            "bpf",
            "-c",
            str(asm_path),
            "-o",
            str(obj_path),
        ]
        compile_res = subprocess.run(compile_cmd, capture_output=True, text=True)
        if compile_res.returncode != 0:
            return AttemptResult(
                attempt=attempt,
                asm_path=str(asm_path),
                obj_path=str(obj_path),
                pcs_path=str(pcs_path),
                compile_ok=False,
                verifier_ok=False,
                compile_error=compile_res.stderr.strip(),
            )

        rel_obj = to_repo_relative(obj_path)
        rel_pcs = to_repo_relative(pcs_path)
        rel_log = to_repo_relative(log_path)
        guest_cmd = (
            f"./kcov_runner ./{rel_obj} -o ./{rel_pcs} > /dev/null 2> ./{rel_log}; "
            "rc=$?; "
            f"printf 'RET=%s\\n' \"$rc\"; "
            f"tail -n 120 ./{rel_log} 2>/dev/null || true; "
            "exit 0"
        )
        guest_res = run_guest_command(self.vm_config, guest_cmd, workdir=self.guest_workdir)
        verifier_log = guest_res.stdout.strip()
        verifier_ok = self._is_verifier_success(verifier_log)

        pcs = self._read_pcs(pcs_path)
        path_hash = ""
        path_status = "no-pcs"
        existing_count = 0
        target_hit = False
        verifier_lines: list[int] = []

        if pcs:
            fingerprint = self.fingerprinter.generate(pcs)
            path_hash = fingerprint.path_id
            existing_count = self._get_existing_testcase_count(path_hash)
            if existing_count == 0:
                path_status = "new"
            elif existing_count == 1:
                path_status = "sparse"
            else:
                path_status = "dense"

            verifier_lines = self._resolve_verifier_lines(pcs)
            if self.target_line is not None:
                target_hit = self.target_line in verifier_lines

        return AttemptResult(
            attempt=attempt,
            asm_path=str(asm_path),
            obj_path=str(obj_path),
            pcs_path=str(pcs_path),
            compile_ok=True,
            verifier_ok=verifier_ok,
            verifier_log=verifier_log,
            pc_count=len(pcs),
            path_hash=path_hash,
            path_status=path_status,
            existing_testcase_count=existing_count,
            target_line=self.target_line,
            target_hit=target_hit,
            verifier_lines=verifier_lines[-30:],
        )

    def _read_pcs(self, pcs_path: Path) -> list[str]:
        if not pcs_path.exists():
            return []
        return [line.strip() for line in pcs_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _get_existing_testcase_count(self, path_hash: str) -> int:
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM test_cases WHERE path_hash = ?", (path_hash,))
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def _resolve_verifier_lines(self, pcs: list[str]) -> list[int]:
        if not pcs:
            return []
        missing = set(pcs) - set(self.resolver._lookup_table.keys())
        if missing:
            self.resolver._lookup_table.update(self.resolver._run_batch_llvm_symbolizer(sorted(missing)))

        lines: list[int] = []
        seen: set[int] = set()
        for pc in pcs:
            for loc in self.resolver._lookup_table.get(pc, []):
                if not loc.file.endswith("kernel/bpf/verifier.c"):
                    continue
                if loc.line > 0 and loc.line not in seen:
                    lines.append(loc.line)
                    seen.add(loc.line)
        return lines

    def _is_verifier_success(self, verifier_log: str) -> bool:
        if "RET=0" not in verifier_log:
            return False

        rejection_markers = [
            "failed to load",
            "load failed",
            "verifier rejected",
            "infinite loop detected",
            "invalid argument",
            "[error]",
        ]
        lower_log = verifier_log.lower()
        return not any(marker in lower_log for marker in rejection_markers)


def create_llm(config: Config, provider: str, model: str, temperature: float):
    if provider == "auto":
        if config.ollama_host:
            provider = "ollama"
        elif config.openai_base_url or config.openai_api_key or config.openai_model:
            provider = "openai_compat"
        else:
            provider = "google"

    if provider == "google":
        if not config.google_api_key.strip():
            raise RuntimeError("config/kcov_config.yaml 中缺少 google_api_key。")
        os.environ["GOOGLE_API_KEY"] = config.google_api_key.strip()
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=model, temperature=temperature)

    if provider == "openai_compat":
        base_url, api_key, model = resolve_openai_compat_settings(config, model)
        return OpenAICompatLLM(base_url=base_url, api_key=api_key, model=model, temperature=temperature)

    if provider == "ollama":
        return OllamaLLM(
            host=config.ollama_host,
            model=model,
            temperature=temperature,
        )

    raise RuntimeError(f"不支持的 provider：{provider}")


class OpenAICompatLLM:
    def __init__(self, base_url: str, api_key: str, model: str, temperature: float):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature

    def invoke(self, prompt: str) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI 兼容接口请求失败：HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI 兼容接口请求失败：{exc}") from exc

        data = json.loads(raw)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"OpenAI 兼容接口未返回有效 choices：{raw[:500]}")

        message = choices[0].get("message") or {}
        content = message.get("content", "")
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                else:
                    text_parts.append(str(item))
            content = "\n".join(text_parts)

        return LLMResponse(content=str(content))


class OllamaLLM:
    def __init__(self, host: str, model: str, temperature: float):
        self.host = host.rstrip("/")
        self.model = model
        self.temperature = temperature

    def invoke(self, prompt: str) -> LLMResponse:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
            },
        }
        request = urllib.request.Request(
            f"{self.host}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama API 请求失败：HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama API 请求失败：{exc}") from exc

        data = json.loads(raw)
        content = data.get("response", "")
        return LLMResponse(content=str(content))


def parse_target_line(target_text: str) -> Optional[int]:
    patterns = [
        r"(?:line|Line|line_number|line no\.?)\s*[:#]?\s*(\d+)",
        r"行(?:号)?\s*[:#]?\s*(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, target_text)
        if match:
            return int(match.group(1))
    return None


def is_mutation_candidate(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith((".", "#", "//")):
        return False
    if stripped.endswith(":"):
        return False
    if re.match(r"^[A-Za-z_.$][\w.$]*:\s*$", stripped):
        return False
    if re.match(r"^[A-Za-z_.$][\w.$]*:\s+#", stripped):
        return False
    if stripped.startswith(("r", "w", "if ", "goto ", "call ", "exit", "lock ")):
        return True
    return False


def build_mutation_context(asm_text: str) -> list[MutationContextLine]:
    context: list[MutationContextLine] = []
    for source_line, line in enumerate(asm_text.splitlines(), start=1):
        if not is_mutation_candidate(line):
            continue
        context.append(MutationContextLine(
            index=len(context) + 1,
            source_line=source_line,
            text=line.rstrip(),
        ))
    return context


def render_mutation_context(context: list[MutationContextLine]) -> str:
    rendered = []
    for item in context:
        rendered.append(f"{item.index:03d} | src:{item.source_line:04d} | {item.text}")
    return "\n".join(rendered)


def apply_mutation_plan(original_asm: str, context: list[MutationContextLine], proposal: dict[str, Any]) -> str:
    assembly = proposal.get("assembly")
    if assembly:
        return str(assembly).strip() + "\n"

    edits = proposal.get("edits") or []
    if not isinstance(edits, list):
        raise ValueError("proposal 中的 'edits' 字段必须是列表。")

    lines = original_asm.splitlines()
    index_to_source = {item.index: item.source_line for item in context}
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        idx = edit.get("index")
        new_line = edit.get("line")
        if idx not in index_to_source or not isinstance(new_line, str):
            continue
        lines[index_to_source[idx] - 1] = new_line

    return "\n".join(lines) + "\n"


def extract_json_payload(text: str) -> dict[str, Any]:
    candidates: list[str] = []
    fenced_blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)
    candidates.extend(fenced_blocks)

    stack = 0
    start_idx = None
    in_string = False
    escape = False
    for idx, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if stack == 0:
                start_idx = idx
            stack += 1
        elif ch == "}":
            if stack == 0:
                continue
            stack -= 1
            if stack == 0 and start_idx is not None:
                candidates.append(text[start_idx:idx + 1])
                start_idx = None

    raw = text.strip()
    for candidate in reversed(candidates):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            if "assembly" not in data and "edits" not in data:
                data["assembly"] = extract_assembly(candidate)
            return data

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        assembly = extract_assembly(raw)
        return {
            "analysis": raw[:500],
            "hypothesis": "Fallback parser used",
            "assembly": assembly,
        }
    if "assembly" not in data and "edits" not in data:
        data["assembly"] = extract_assembly(raw)
    return data


def extract_assembly(text: str) -> str:
    fenced = re.search(r"```(?:assembly|asm)?\s*(.*?)```", text, re.S)
    if fenced:
        return fenced.group(1).strip() + "\n"
    return text.strip() + "\n"


def maybe_read_text_argument(value: str) -> str:
    try:
        path = Path(value)
        if path.exists():
            return path.read_text(encoding="utf-8")
    except OSError:
        pass
    return value


def build_prompt(
    mutation_context_text: str,
    target_gap_info: str,
    previous_attempts: list[dict[str, Any]],
    target_line: Optional[int],
    objective: str,
    discovered_hashes: list[str],
) -> str:
    history = json.dumps(previous_attempts[-4:], ensure_ascii=False, indent=2)
    target_line_hint = f"Target verifier.c line: {target_line}" if target_line is not None else "Target line was not parsed from the description."
    objective_hint = (
        "Primary objective: enrich a sparse path by mutating a seed currently on a similar dense path."
        if objective == "enrich_sparse"
        else "Primary objective: generate genuinely new paths and produce diverse variants around an uncovered verifier branch."
    )
    diversity_hint = ", ".join(discovered_hashes[-8:]) if discovered_hashes else "<none yet>"
    template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.format(
        objective_hint=objective_hint,
        target_line_hint=target_line_hint,
        diversity_hint=diversity_hint,
        target_gap_info=target_gap_info,
        mutation_context_text=mutation_context_text,
        history=history,
    ).strip()


def summarize_attempt(result: AttemptResult) -> dict[str, Any]:
    return {
        "attempt": result.attempt,
        "compile_ok": result.compile_ok,
        "verifier_ok": result.verifier_ok,
        "pc_count": result.pc_count,
        "path_hash": result.path_hash,
        "path_status": result.path_status,
        "existing_testcase_count": result.existing_testcase_count,
        "target_line": result.target_line,
        "target_hit": result.target_hit,
        "verifier_lines_tail": result.verifier_lines or [],
        "compile_error_tail": result.compile_error[-600:],
        "verifier_log_tail": result.verifier_log[-1200:],
    }


def evaluate_attempt_score(
    result: AttemptResult,
    objective: str,
    target_sparse_hash: Optional[str] = None,
) -> tuple[int, int, int, int, int]:
    matched_sparse = 1 if (objective == "enrich_sparse" and target_sparse_hash and result.path_hash == target_sparse_hash) else 0
    return (
        matched_sparse,
        1 if result.target_hit else 0,
        {"new": 3, "sparse": 2, "dense": 1}.get(result.path_status, 0),
        1 if result.verifier_ok else 0,
        result.pc_count,
    )


def update_diversity_pool(pool: dict[str, dict[str, Any]], result: AttemptResult, objective: str) -> None:
    if not result.path_hash:
        return

    entry = {
        "attempt": result.attempt,
        "score": evaluate_attempt_score(result, objective),
        "result": summarize_attempt(result),
    }
    previous = pool.get(result.path_hash)
    if previous is None or entry["score"] > previous["score"]:
        pool[result.path_hash] = entry


def collect_result_buckets(
    diversity_pool: dict[str, dict[str, Any]],
    objective: str,
    target_sparse_hash: Optional[str],
) -> dict[str, list[dict[str, Any]]]:
    entries = sorted(diversity_pool.values(), key=lambda entry: entry["score"], reverse=True)

    matched_sparse: list[dict[str, Any]] = []
    new_paths: list[dict[str, Any]] = []
    sparse_paths: list[dict[str, Any]] = []
    diverse_paths: list[dict[str, Any]] = []

    for entry in entries:
        result = entry["result"]
        result_with_score = {
            **result,
            "score": list(entry["score"]),
        }
        if objective == "enrich_sparse" and target_sparse_hash and result.get("path_hash") == target_sparse_hash:
            matched_sparse.append(result_with_score)
        if result.get("path_status") == "new":
            new_paths.append(result_with_score)
        if result.get("path_status") == "sparse":
            sparse_paths.append(result_with_score)
        diverse_paths.append(result_with_score)

    return {
        "matched_sparse": matched_sparse,
        "new_paths": new_paths,
        "sparse_paths": sparse_paths,
        "diverse_paths": diverse_paths,
    }


def prepare_seed_assembly(seed_path: Path, out_dir: Path) -> Path:
    if seed_path.suffix == ".s":
        return seed_path
    if seed_path.suffix != ".c":
        raise ValueError("seed 必须是 .s 或 .c 文件。")

    generated = out_dir / f"{seed_path.stem}_seed.s"
    compile_cmd = [
        "clang",
        "-O2",
        "-g",
        "-target",
        "bpf",
        "-c",
        str(seed_path),
        "-S",
        "-o",
        str(generated),
    ]
    subprocess.run(compile_cmd, capture_output=True, text=True, check=True)
    return generated


def run_agent_mutation(args) -> int:
    config = load_project_config(args.config)
    vm_config = VMConfig(
        ssh_key=args.ssh_key or config.vm_ssh_key,
        ssh_port=args.ssh_port or config.vm_ssh_port,
        ssh_host=args.ssh_host or config.vm_ssh_host,
        guest_mount_point=args.guest_mount_point or config.vm_guest_mount_point,
        guest_workdir=args.guest_workdir,
    )

    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    auto_selection = None
    objective = args.objective or config.agent_objective
    log("[*] 正在加载 seed 和 target 选择结果...")
    if not args.seed or not args.target:
        auto_selection = choose_auto_selection(config, objective)
        selection_meta = auto_selection.metadata
        log(
            f"[*] 自动选择完成：目标={selection_meta.get('objective', objective)} "
            f"seed={auto_selection.seed_path}"
        )
        if selection_meta.get("selected_dense_path_hash") and selection_meta.get("selected_sparse_path_hash"):
            log(
                f"[*] 稠密路径 -> 稀疏路径目标：稠密路径={selection_meta['selected_dense_path_hash']} "
                f"稀疏路径={selection_meta['selected_sparse_path_hash']} "
                f"相似度={selection_meta.get('similarity_score', 0.0):.4f}"
            )
        elif selection_meta.get("gap", {}).get("line_number"):
            log(f"[*] 新路径目标缺口：verifier.c:{selection_meta['gap']['line_number']}")

    seed_input = args.seed or str(auto_selection.seed_path)
    target_input = args.target or auto_selection.target_text

    log(f"[*] 正在准备 seed 汇编：{seed_input}")
    seed_path = prepare_seed_assembly(Path(seed_input).resolve(), out_dir)
    seed_asm = seed_path.read_text(encoding="utf-8")
    mutation_context = build_mutation_context(seed_asm)
    mutation_context_text = render_mutation_context(mutation_context)
    target_gap_info = maybe_read_text_argument(target_input)
    target_line = parse_target_line(target_gap_info)
    if auto_selection is not None:
        objective = auto_selection.metadata.get("objective", objective)

    log(f"[*] 正在初始化 LLM：provider={args.provider or config.agent_provider} model={args.model or config.agent_model}")
    llm = create_llm(config, args.provider or config.agent_provider, args.model or config.agent_model, args.temperature if args.temperature is not None else config.agent_temperature)
    effective_model = getattr(llm, "model", args.model or config.agent_model)
    evaluator = MutationEvaluator(config, vm_config, out_dir, target_line=target_line)
    log(f"[*] 用于执行 verifier 的 guest 工作目录：{evaluator.guest_workdir}")
    history: list[dict[str, Any]] = []
    best_result: Optional[AttemptResult] = None
    discovered_hashes: list[str] = []
    diversity_pool: dict[str, dict[str, Any]] = {}
    target_sparse_hash = auto_selection.metadata.get("selected_sparse_path_hash") if auto_selection else None
    matched_target_sparse = False

    metadata = {
        "seed": str(seed_path),
        "target": target_gap_info,
        "target_line": target_line,
        "objective": objective,
        "provider": args.provider,
        "model": effective_model,
        "guest_workdir": evaluator.guest_workdir,
        "mutable_instruction_count": len(mutation_context),
        "auto_selection": auto_selection.metadata if auto_selection else None,
    }
    (out_dir / "run_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        max_iterations = args.max_iterations or config.agent_max_iterations
        for attempt in range(1, max_iterations + 1):
            prompt = build_prompt(
                mutation_context_text,
                target_gap_info,
                history,
                target_line,
                objective,
                discovered_hashes,
            )
            log(f"[*] 第 {attempt} 次尝试：正在为 {len(mutation_context)} 条可变异指令请求变异方案...")
            response = llm.invoke(prompt)
            content = getattr(response, "content", response)
            if isinstance(content, list):
                content = "\n".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in content
                )
            proposal = extract_json_payload(str(content))
            asm_code = apply_mutation_plan(seed_asm, mutation_context, proposal)
            log(f"[*] 第 {attempt} 次尝试：正在应用编辑、编译，并在 guest 中运行 verifier...")
            result = evaluator.evaluate(asm_code, attempt)

            proposal_record = {
                "attempt": attempt,
                "analysis": proposal.get("analysis", ""),
                "hypothesis": proposal.get("hypothesis", ""),
                "raw_response": str(content),
            }
            (out_dir / f"{evaluator.run_prefix}_attempt_{attempt:02d}.proposal.json").write_text(
                json.dumps(proposal_record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (out_dir / f"{evaluator.run_prefix}_attempt_{attempt:02d}.result.json").write_text(
                json.dumps(summarize_attempt(result), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            log(
                f"[*] 第 {attempt} 次尝试结果：编译成功={result.compile_ok} "
                f"verifier 通过={result.verifier_ok} PC 数={result.pc_count} "
                f"路径={result.path_hash or '-'} 状态={result.path_status} "
                f"命中目标={result.target_hit}"
            )

            history.append({
                "analysis": proposal.get("analysis", ""),
                "hypothesis": proposal.get("hypothesis", ""),
                "result": summarize_attempt(result),
            })
            if result.path_hash and result.path_hash not in discovered_hashes:
                discovered_hashes.append(result.path_hash)
            update_diversity_pool(diversity_pool, result, objective)

            if best_result is None or evaluate_attempt_score(result, objective, target_sparse_hash) > evaluate_attempt_score(best_result, objective, target_sparse_hash):
                best_result = result

            if objective == "enrich_sparse":
                if result.path_hash and target_sparse_hash and result.path_hash == target_sparse_hash:
                    matched_target_sparse = True
                    log(f"[+] 第 {attempt} 次尝试命中了目标稀疏路径。")
                    if len(discovered_hashes) >= (args.nearby_budget or config.agent_nearby_budget):
                        break

            if objective == "generate_new" and (result.target_hit or result.path_status in {"new", "sparse"}):
                log(f"[+] 由于第 {attempt} 次尝试发现了较有希望的路径，提前结束本轮。")
                break

        if best_result is None:
            log("[!] 没有任何一次尝试成功完成。")
            return 1

        buckets = collect_result_buckets(diversity_pool, objective, target_sparse_hash)
        final_summary = {
            "objective": objective,
            "matched_target_sparse": matched_target_sparse,
            "best_attempt": best_result.attempt,
            "best_result": summarize_attempt(best_result),
            "discovered_hashes": discovered_hashes,
            "diversity_pool": buckets["diverse_paths"][:(args.top_k or config.agent_top_k)],
            "matched_sparse_pool": buckets["matched_sparse"][:(args.top_k or config.agent_top_k)],
            "new_path_pool": buckets["new_paths"][:(args.top_k or config.agent_top_k)],
            "sparse_path_pool": buckets["sparse_paths"][:(args.top_k or config.agent_top_k)],
        }
        (out_dir / "summary.json").write_text(json.dumps(final_summary, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"[*] 最佳尝试：第 {best_result.attempt} 次")
        log(f"[*] 输出目录：{out_dir}")
        return 0
    finally:
        evaluator.close()
def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI 辅助的 eBPF 汇编变异器")
    parser.add_argument("-s", "--seed", help="seed .s 或 .c 文件路径；省略时自动选择")
    parser.add_argument("-t", "--target", help="目标缺口描述，或包含描述内容的文本文件；省略时自动选择")
    parser.add_argument("--objective", default=None, choices=["enrich_sparse", "generate_new"], help="路径引导目标")
    parser.add_argument("-o", "--output-dir", default="mutated-cases/agent_runs/latest", help="生成结果的输出目录")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="项目配置 YAML 路径")
    parser.add_argument("--provider", default=None, help="可选，覆盖 config.agent_provider")
    parser.add_argument("--model", default=None, help="可选，覆盖 config.agent_model")
    parser.add_argument("--temperature", type=float, default=None, help="可选，覆盖 config.agent_temperature")
    parser.add_argument("--max-iterations", type=int, default=None, help="可选，覆盖 config.agent_max_iterations")
    parser.add_argument("--top-k", type=int, default=None, help="可选，覆盖 config.agent_top_k")
    parser.add_argument("--nearby-budget", type=int, default=None, help="可选，覆盖 config.agent_nearby_budget")
    parser.add_argument("--ssh-key", default=None, help="可选，覆盖 config.vm_ssh_key")
    parser.add_argument("--ssh-port", type=int, default=None, help="可选，覆盖 config.vm_ssh_port")
    parser.add_argument("--ssh-host", default=None, help="可选，覆盖 config.vm_ssh_host")
    parser.add_argument("--guest-mount-point", default=None, help="可选，覆盖 config.vm_guest_mount_point")
    parser.add_argument("--guest-workdir", default=None, help="可选，覆盖自动探测到的 guest 工作目录")
    return parser


if __name__ == "__main__":
    parser = build_arg_parser()
    cli_args = parser.parse_args()
    try:
        raise SystemExit(run_agent_mutation(cli_args))
    except Exception as exc:
        print(f"[!] {exc}", file=sys.stderr)
        raise SystemExit(1)
