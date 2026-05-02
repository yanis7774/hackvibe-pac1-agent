"""
Агент-раннер для BitGN PAC1. Claude в чате = мозг, скрипт = руки.

Команды:
  recon <task_id>                                    — старт trial + полная разведка
  exec <harness_url> <json_array>                    — выполнить цепочку команд
  submit <harness_url> <trial_id> <answer> <refs> [outcome]  — отправить ответ + score
  all                                                — показать список всех задач
  rules                                              — показать правила
  learn <text>                                       — добавить правило

Env:
  BENCHMARK_HOST  — API URL (default: api.bitgn.com)
  BENCHMARK_ID    — бенчмарк (default: bitgn/pac1-dev)

Tools в exec (JSON):
  {"tool":"tree","root":"/","level":2}
  {"tool":"context"}
  {"tool":"find","name":"*.md","root":"/","kind":"files","limit":10}
  {"tool":"search","pattern":"Status: done","root":"/","limit":10}
  {"tool":"list","path":"/"}
  {"tool":"read","path":"file.md"}
  {"tool":"read","path":"file.md","start_line":1,"end_line":10,"number":true}
  {"tool":"write","path":"file.md","content":"..."}
  {"tool":"write","path":"file.md","content":"new line","start_line":5,"end_line":5}
  {"tool":"delete","path":"file.md"}
  {"tool":"mkdir","path":"/new_dir"}
  {"tool":"move","from":"old.md","to":"new.md"}
  {"tool":"answer","message":"done","refs":["AGENTS.md"],"outcome":"OUTCOME_OK"}

Outcomes: OUTCOME_OK, OUTCOME_DENIED_SECURITY, OUTCOME_NONE_CLARIFICATION,
          OUTCOME_NONE_UNSUPPORTED, OUTCOME_ERR_INTERNAL
"""

import json
import os
import sys
from datetime import datetime

from google.protobuf.json_format import MessageToDict

from bitgn.harness_connect import HarnessServiceClientSync
from bitgn.harness_pb2 import (
    EndTrialRequest,
    GetBenchmarkRequest,
    StartPlaygroundRequest,
    StatusRequest,
)
from bitgn.vm.pcm_connect import PcmRuntimeClientSync
from bitgn.vm.pcm_pb2 import (
    AnswerRequest,
    ContextRequest,
    DeleteRequest,
    FindRequest,
    ListRequest,
    MkDirRequest,
    MoveRequest,
    Outcome,
    ReadRequest,
    SearchRequest,
    TreeRequest,
    WriteRequest,
)
from connectrpc.errors import ConnectError

BITGN_URL = os.getenv("BITGN_HOST") or os.getenv("BENCHMARK_HOST") or "https://api.bitgn.com"
BENCHMARK_ID = os.getenv("BENCH_ID") or os.getenv("BENCHMARK_ID") or "bitgn/pac1-dev"
BITGN_API_KEY = os.getenv("BITGN_API_KEY") or ""
RULES_FILE = os.path.join(os.path.dirname(__file__), "RULES.md")
PROGRESS_FILE = os.getenv("PROGRESS_FILE") or "/home/bitgn/progress.log"


def _log_progress(msg: str):
    """Записать прогресс в лог-файл (видно в реальном времени)"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {msg}\n"
    try:
        with open(PROGRESS_FILE, "a") as f:
            f.write(line)
            f.flush()
    except Exception:
        pass


def _make_client():
    """Создать клиент"""
    return HarnessServiceClientSync(BITGN_URL)

OUTCOME_MAP = {
    "OUTCOME_OK": Outcome.OUTCOME_OK,
    "OUTCOME_DENIED_SECURITY": Outcome.OUTCOME_DENIED_SECURITY,
    "OUTCOME_NONE_CLARIFICATION": Outcome.OUTCOME_NONE_CLARIFICATION,
    "OUTCOME_NONE_UNSUPPORTED": Outcome.OUTCOME_NONE_UNSUPPORTED,
    "OUTCOME_ERR_INTERNAL": Outcome.OUTCOME_ERR_INTERNAL,
}


def to_dict(proto_msg):
    return MessageToDict(proto_msg)


def format_tree(node, prefix="", is_last=True):
    """Форматирование дерева в shell-like вид"""
    branch = "└── " if is_last else "├── "
    lines = [f"{prefix}{branch}{node.name}"]
    child_prefix = f"{prefix}{'    ' if is_last else '│   '}"
    children = list(node.children)
    for idx, child in enumerate(children):
        lines.extend(format_tree(child, child_prefix, idx == len(children) - 1))
    return lines


def dispatch(vm, cmd):
    """Выполнить одну команду в PCM VM"""
    tool = cmd.get("tool")
    try:
        if tool == "context":
            result = vm.context(ContextRequest())
            return {"ok": True, "tool": tool, "result": to_dict(result)}

        elif tool == "tree":
            result = vm.tree(TreeRequest(root=cmd.get("root", ""), level=cmd.get("level", 2)))
            # Форматируем дерево в читаемый вид
            root = result.root
            if root.name:
                lines = [root.name]
                children = list(root.children)
                for idx, child in enumerate(children):
                    lines.extend(format_tree(child, is_last=idx == len(children) - 1))
                return {"ok": True, "tool": tool, "result": "\n".join(lines)}
            return {"ok": True, "tool": tool, "result": "(empty)"}

        elif tool == "find":
            kind_map = {"all": 0, "files": 1, "dirs": 2}
            result = vm.find(FindRequest(
                root=cmd.get("root", "/"), name=cmd.get("name", ""),
                type=kind_map.get(cmd.get("kind", "all"), 0), limit=cmd.get("limit", 10)))
            return {"ok": True, "tool": tool, "result": to_dict(result)}

        elif tool == "search":
            result = vm.search(SearchRequest(
                root=cmd.get("root", "/"), pattern=cmd.get("pattern", ""), limit=cmd.get("limit", 10)))
            # Форматируем как rg
            matches = [f"{m.path}:{m.line}:{m.line_text}" for m in result.matches]
            return {"ok": True, "tool": tool, "result": "\n".join(matches) if matches else "(no matches)"}

        elif tool == "list":
            result = vm.list(ListRequest(name=cmd.get("path", "/")))
            entries = [f"{e.name}/" if e.is_dir else e.name for e in result.entries]
            return {"ok": True, "tool": tool, "result": "\n".join(entries) if entries else "(empty)"}

        elif tool == "read":
            result = vm.read(ReadRequest(
                path=cmd.get("path"), number=cmd.get("number", False),
                start_line=cmd.get("start_line", 0), end_line=cmd.get("end_line", 0)))
            return {"ok": True, "tool": tool, "path": cmd.get("path"), "result": result.content}

        elif tool == "write":
            result = vm.write(WriteRequest(
                path=cmd.get("path"), content=cmd.get("content", ""),
                start_line=cmd.get("start_line", 0), end_line=cmd.get("end_line", 0)))
            return {"ok": True, "tool": tool}

        elif tool == "delete":
            vm.delete(DeleteRequest(path=cmd.get("path")))
            return {"ok": True, "tool": tool}

        elif tool == "mkdir":
            vm.mk_dir(MkDirRequest(path=cmd.get("path")))
            return {"ok": True, "tool": tool}

        elif tool == "move":
            vm.move(MoveRequest(from_name=cmd.get("from"), to_name=cmd.get("to")))
            return {"ok": True, "tool": tool}

        elif tool == "answer":
            outcome_str = cmd.get("outcome", "OUTCOME_OK")
            vm.answer(AnswerRequest(
                message=cmd.get("message", ""),
                outcome=OUTCOME_MAP.get(outcome_str, Outcome.OUTCOME_OK),
                refs=cmd.get("refs", [])))
            return {"ok": True, "tool": tool}

        else:
            return {"ok": False, "error": f"Unknown tool: {tool}"}

    except ConnectError as e:
        return {"ok": False, "tool": tool, "error": str(e.message), "code": str(e.code)}


def deep_recon_pcm(vm, root="", level=3):
    """Полная разведка через tree + read всех файлов"""
    results = []

    # Дерево
    try:
        tree_result = vm.tree(TreeRequest(root=root, level=level))
        root_node = tree_result.root
        if root_node.name:
            lines = [root_node.name]
            children = list(root_node.children)
            for idx, child in enumerate(children):
                lines.extend(format_tree(child, is_last=idx == len(children) - 1))
            results.append({"type": "tree", "result": "\n".join(lines)})
        else:
            results.append({"type": "tree", "result": "(empty)"})
    except ConnectError as e:
        results.append({"type": "tree", "error": str(e.message)})

    # Context
    try:
        ctx = to_dict(vm.context(ContextRequest()))
        if ctx:
            results.append({"type": "context", "result": ctx})
    except ConnectError:
        pass

    # Ищем все файлы и читаем их
    try:
        found = vm.find(FindRequest(root="/", name="*", type=1, limit=20))
        for fpath in found.items:
            try:
                content = vm.read(ReadRequest(path=fpath))
                results.append({"type": "file", "path": fpath, "content": content.content})
            except ConnectError as e:
                results.append({"type": "file", "path": fpath, "error": str(e.message)})
    except ConnectError as e:
        results.append({"type": "find_error", "error": str(e.message)})

    return results


def cmd_all():
    """Показать все задачи бенчмарка"""
    client = _make_client()
    print(f"Подключение к {BITGN_URL}...")
    status = client.status(StatusRequest())
    print(f"Статус: {MessageToDict(status)}")
    res = client.get_benchmark(GetBenchmarkRequest(benchmark_id=BENCHMARK_ID))
    out = {
        "benchmark_id": res.benchmark_id,
        "description": res.description,
        "tasks": [{"task_id": t.task_id} for t in res.tasks],
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


def cmd_recon(task_id: str):
    """Старт trial + полная разведка"""
    client = _make_client()
    trial = client.start_playground(
        StartPlaygroundRequest(benchmark_id=BENCHMARK_ID, task_id=task_id))

    vm = PcmRuntimeClientSync(trial.harness_url)
    recon = deep_recon_pcm(vm)

    # Правила
    rules = ""
    if os.path.exists(RULES_FILE):
        with open(RULES_FILE) as f:
            rules = f.read()

    out = {
        "trial_id": trial.trial_id,
        "harness_url": trial.harness_url,
        "instruction": trial.instruction,
        "recon": recon,
        "rules": rules,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


def cmd_exec(harness_url: str, commands_json: str):
    """Выполнить цепочку команд"""
    vm = PcmRuntimeClientSync(harness_url)
    commands = json.loads(commands_json)
    results = []
    for cmd in commands:
        results.append(dispatch(vm, cmd))
    print(json.dumps(results, indent=2, ensure_ascii=False))


def cmd_submit(harness_url: str, trial_id: str, answer: str, refs_str: str, outcome: str = "OUTCOME_OK"):
    """Отправить ответ и получить score"""
    vm = PcmRuntimeClientSync(harness_url)
    refs = [r.strip() for r in refs_str.split(",") if r.strip()]

    try:
        vm.answer(AnswerRequest(
            message=answer,
            outcome=OUTCOME_MAP.get(outcome, Outcome.OUTCOME_OK),
            refs=refs))
    except ConnectError as e:
        print(json.dumps({"error": str(e.message), "score": -1}))
        return

    client = _make_client()
    result = client.end_trial(EndTrialRequest(trial_id=trial_id))
    out = {
        "score": result.score,
        "detail": list(result.score_detail),
        "answer": answer,
        "refs": refs,
        "outcome": outcome,
    }
    _log_progress(f"APPROACH {trial_id} outcome={outcome} answer={answer[:80]}")
    _log_progress(f"SUBMIT {trial_id} score={result.score} outcome={outcome}")
    print(json.dumps(out, indent=2, ensure_ascii=False))


def cmd_rules():
    if os.path.exists(RULES_FILE):
        with open(RULES_FILE) as f:
            print(f.read())
    else:
        print("Правил пока нет.")


def cmd_learn(text: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(RULES_FILE, "a") as f:
        if not os.path.exists(RULES_FILE) or os.path.getsize(RULES_FILE) == 0:
            f.write("# Правила PAC1 агента (накоплены из опыта)\n")
        f.write(f"\n- [{timestamp}] {text}\n")
    _log_progress(f"LEARN {text[:100]}")
    print(f"Записано: {text}")


def cmd_retry(task_id: str):
    """Retry задачу через playground (для обучения, не для run score)"""
    _log_progress(f"RETRY_START {task_id}")
    client = _make_client()
    trial = client.start_playground(
        StartPlaygroundRequest(benchmark_id=BENCHMARK_ID, task_id=task_id))

    vm = PcmRuntimeClientSync(trial.harness_url)
    recon = deep_recon_pcm(vm)

    rules = ""
    if os.path.exists(RULES_FILE):
        with open(RULES_FILE) as f:
            rules = f.read()

    out = {
        "trial_id": trial.trial_id,
        "task_id": task_id,
        "harness_url": trial.harness_url,
        "instruction": trial.instruction,
        "recon": recon,
        "rules": rules,
        "mode": "playground_retry",
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


# === RUN MODE (для соревнования) ===

def cmd_run_start(name: str = "Claude Agent"):
    """Начать официальный run — получить все trial_ids"""
    if not BITGN_API_KEY:
        print(json.dumps({"error": "BITGN_API_KEY не задан. Получи на bitgn.com в профиле."}))
        return
    client = _make_client()
    from bitgn.harness_pb2 import StartRunRequest
    res = client.start_run(StartRunRequest(
        benchmark_id=BENCHMARK_ID,
        name=name,
        api_key=BITGN_API_KEY,
    ))
    out = {
        "run_id": res.run_id,
        "benchmark_id": res.benchmark_id,
        "trial_ids": list(res.trial_ids),
        "total_tasks": len(res.trial_ids),
    }
    _log_progress(f"RUN_START {res.run_id} tasks={len(res.trial_ids)}")
    print(json.dumps(out, indent=2, ensure_ascii=False))


def cmd_run_trial(trial_id: str):
    """Начать конкретный trial из run — получить задачу и harness_url"""
    client = _make_client()
    from bitgn.harness_pb2 import StartTrialRequest as RunStartTrialRequest
    trial = client.start_trial(RunStartTrialRequest(trial_id=trial_id))

    vm = PcmRuntimeClientSync(trial.harness_url)
    recon = deep_recon_pcm(vm)

    rules = ""
    if os.path.exists(RULES_FILE):
        with open(RULES_FILE) as f:
            rules = f.read()

    out = {
        "trial_id": trial.trial_id,
        "task_id": trial.task_id,
        "run_id": trial.run_id,
        "harness_url": trial.harness_url,
        "instruction": trial.instruction,
        "recon": recon,
        "rules": rules,
    }
    _log_progress(f"RUN_TRIAL {trial.task_id} ({trial.trial_id}) instruction={trial.instruction[:80]}")
    print(json.dumps(out, indent=2, ensure_ascii=False))


def cmd_run_status(run_id: str):
    """Проверить статус run — score, состояние"""
    client = _make_client()
    from bitgn.harness_pb2 import GetRunRequest
    res = client.get_run(GetRunRequest(run_id=run_id))
    out = {
        "run_id": res.run_id,
        "benchmark_id": res.benchmark_id,
        "name": res.name,
        "score": res.score,
        "state": res.state,
        "trials": [{"trial_id": t.trial_id, "score": t.score} for t in res.trials] if res.trials else [],
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


def cmd_run_submit(run_id: str):
    """Отправить run в Hall of Fame + верифицировать через run-status"""
    client = _make_client()
    from bitgn.harness_pb2 import SubmitRunRequest, GetRunRequest
    res = client.submit_run(SubmitRunRequest(run_id=run_id, force=True))
    _log_progress(f"RUN_SUBMIT {res.run_id} state={res.state}")

    # Верификация через run-status
    state_names = {0: "UNSPECIFIED", 1: "RUNNING", 2: "PENDING_EVAL", 3: "EVALUATED"}
    try:
        verify = client.get_run(GetRunRequest(run_id=run_id))
        verify_state_name = state_names.get(verify.state, f"unknown({verify.state})")
        trials_count = len(verify.trials) if verify.trials else 0
        out = {
            "run_id": res.run_id,
            "submit_state": res.state,
            "submit_state_name": state_names.get(res.state, "unknown"),
            "verify_state": verify.state,
            "verify_state_name": verify_state_name,
            "verify_trials": trials_count,
            "verify_score": verify.score if hasattr(verify, 'score') else None,
            "submitted_ok": verify.state in (2, 3),  # PENDING_EVAL или EVALUATED
        }
        _log_progress(f"RUN_VERIFY {run_id} state={verify_state_name} trials={trials_count}")
    except Exception as e:
        out = {
            "run_id": res.run_id,
            "submit_state": res.state,
            "verify_error": str(e),
            "submitted_ok": False,
        }
        _log_progress(f"RUN_VERIFY_ERROR {run_id} {e}")

    print(json.dumps(out, indent=2, ensure_ascii=False))
    if out.get("submitted_ok"):
        print("=== SUBMITTED TO HALL OF FAME (verified) ===")
    else:
        print("=== WARN: submit not verified ===")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    action = sys.argv[1]

    if action == "all":
        cmd_all()
    elif action == "recon" and len(sys.argv) >= 3:
        cmd_recon(sys.argv[2])
    elif action == "exec" and len(sys.argv) >= 4:
        cmd_exec(sys.argv[2], sys.argv[3])
    elif action == "submit" and len(sys.argv) >= 6:
        outcome = sys.argv[6] if len(sys.argv) >= 7 else "OUTCOME_OK"
        cmd_submit(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], outcome)
    elif action == "rules":
        cmd_rules()
    elif action == "learn" and len(sys.argv) >= 3:
        cmd_learn(" ".join(sys.argv[2:]))
    elif action == "retry" and len(sys.argv) >= 3:
        cmd_retry(sys.argv[2])
    # Run mode (соревнование)
    elif action == "run-start":
        name = sys.argv[2] if len(sys.argv) >= 3 else "Claude Agent"
        cmd_run_start(name)
    elif action == "run-trial" and len(sys.argv) >= 3:
        cmd_run_trial(sys.argv[2])
    elif action == "run-status" and len(sys.argv) >= 3:
        cmd_run_status(sys.argv[2])
    elif action == "run-submit" and len(sys.argv) >= 3:
        cmd_run_submit(sys.argv[2])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
