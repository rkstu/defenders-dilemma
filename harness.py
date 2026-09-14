"""
Evaluation harness for The Defender's Dilemma.
Thin loop: load tasks → call API → log full responses → detect refusals.
Uses the standard openai library for both OpenRouter and Nebius.
"""

import json
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

from config import (
    MODELS,
    PLATFORMS,
    FRAMINGS,
    OPENROUTER_API_KEY,
    NEBIUS_API_KEY,
    TASKS_DIR,
    LOGS_DIR,
    get_generation_params,
    regex_refusal_check,
)


def get_client(model_key: str) -> OpenAI:
    model_cfg = MODELS[model_key]
    platform = PLATFORMS[model_cfg["platform"]]
    api_key = (
        OPENROUTER_API_KEY
        if model_cfg["platform"] == "openrouter"
        else NEBIUS_API_KEY
    )
    if not api_key:
        raise ValueError(
            f"Missing API key for {model_cfg['platform']}. "
            f"Set {platform['api_key_env']} in your environment."
        )
    return OpenAI(base_url=platform["base_url"], api_key=api_key)


def build_messages(task: dict, framing: str) -> list[dict]:
    prompt = FRAMINGS[framing].format(artifact_type=task["artifact_type"])
    return [{"role": "user", "content": f"{prompt}\n\n{task['input_artifact']}"}]


def build_request_kwargs(model_key: str, messages: list[dict]) -> dict:
    model_cfg = MODELS[model_key]
    gen_params = get_generation_params(model_key)
    kwargs = {"model": model_cfg["model_id"], "messages": messages, **gen_params}
    if model_cfg["provider_pin"] is not None:
        kwargs["extra_body"] = {
            "provider": {
                "order": model_cfg["provider_pin"],
                "allow_fallbacks": False,
            }
        }
    return kwargs


def call_model(client: OpenAI, request_kwargs: dict) -> dict:
    try:
        response = client.chat.completions.create(**request_kwargs)
        return {"success": True, "response": response.model_dump(), "error": None}
    except Exception as e:
        return {
            "success": False,
            "response": None,
            "error": {"type": type(e).__name__, "message": str(e)},
        }


def extract_response_text(result: dict) -> str:
    if not result["success"]:
        return ""
    try:
        return result["response"]["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def detect_api_refusal(result: dict) -> dict | None:
    """Detect API-level content filter blocks.
    Returns dict if blocked, None otherwise.
    """
    if not result["success"]:
        return None
    try:
        choice = result["response"]["choices"][0]
        finish_reason = choice.get("finish_reason", "")
        message = choice.get("message", {})
        refusal_text = message.get("refusal")
        content = message.get("content")
        if finish_reason == "content_filter" or (refusal_text and not content):
            return {
                "is_api_refusal": True,
                "finish_reason": finish_reason,
                "refusal_message": refusal_text,
            }
    except (KeyError, IndexError, TypeError):
        pass
    return None


def run_single(task: dict, model_key: str, framing: str, epoch: int) -> dict:
    client = get_client(model_key)
    messages = build_messages(task, framing)
    request_kwargs = build_request_kwargs(model_key, messages)

    ts_start = datetime.now(timezone.utc).isoformat()
    result = call_model(client, request_kwargs)
    ts_end = datetime.now(timezone.utc).isoformat()

    response_text = extract_response_text(result)
    api_refusal = detect_api_refusal(result)
    regex_result = regex_refusal_check(response_text) if response_text else None

    return {
        "task_id": task["id"],
        "task_title": task["title"],
        "category": task["category"],
        "model_key": model_key,
        "model_id": MODELS[model_key]["model_id"],
        "platform": MODELS[model_key]["platform"],
        "provider_pin": MODELS[model_key]["provider_pin"],
        "framing": framing,
        "epoch": epoch,
        "timestamp_start": ts_start,
        "timestamp_end": ts_end,
        "generation_params": get_generation_params(model_key),
        "prompt_sent": messages[0]["content"],
        "api_result": result,
        "response_text": response_text,
        "api_level_refusal": api_refusal,
        "regex_refusal": regex_result,
    }


def load_tasks(task_file: str = "all_tasks.json") -> list[dict]:
    path = TASKS_DIR / task_file
    with open(path) as f:
        return json.load(f)


def save_log_entry(entry: dict, run_id: str):
    log_file = LOGS_DIR / f"{run_id}.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def run_phase(
    model_keys: list[str],
    framings: list[str],
    epochs: int = 1,
    task_file: str = "all_tasks.json",
    task_ids: list[str] | None = None,
    run_id: str | None = None,
    delay: float = 2.0,
):
    if run_id is None:
        run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    tasks = load_tasks(task_file)
    if task_ids:
        tasks = [t for t in tasks if t["id"] in task_ids]
        if not tasks:
            print(f"No tasks matched IDs: {task_ids}")
            return run_id
    total = len(tasks) * len(model_keys) * len(framings) * epochs
    print(f"Run {run_id}: {total} calls "
          f"({len(tasks)} tasks × {len(model_keys)} models × "
          f"{len(framings)} framings × {epochs} epochs)")

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    meta = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "task_file": task_file,
        "model_keys": model_keys,
        "framings": framings,
        "epochs": epochs,
        "total_calls": total,
    }
    with open(LOGS_DIR / f"{run_id}_meta.json", "w") as f:
        json.dump(meta, f, indent=2)

    call_num = 0
    for epoch in range(1, epochs + 1):
        for task in tasks:
            for model_key in model_keys:
                for framing in framings:
                    call_num += 1
                    print(f"  [{call_num}/{total}] "
                          f"{task['id']} | {model_key} | {framing} | epoch {epoch}",
                          end="", flush=True)

                    entry = run_single(task, model_key, framing, epoch)
                    save_log_entry(entry, run_id)

                    if not entry["api_result"]["success"]:
                        print(f" -> ERROR: {entry['api_result']['error']}")
                    elif entry.get("api_level_refusal"):
                        print(f" -> API-BLOCK ({entry['api_level_refusal']['finish_reason']})")
                    else:
                        is_ref = (entry["regex_refusal"]["is_refusal"]
                                  if entry["regex_refusal"] else "?")
                        preview = entry["response_text"][:80].replace("\n", " ")
                        print(f" -> OK (regex_ref={is_ref}) {preview}...")

                    if delay > 0:
                        time.sleep(delay)

    print(f"\nDone. Logs: {LOGS_DIR / run_id}.jsonl")
    return run_id


def print_summary(run_id: str):
    log_file = LOGS_DIR / f"{run_id}.jsonl"
    entries = []
    with open(log_file) as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))

    print(f"\n{'='*60}")
    print(f"Summary: {run_id} ({len(entries)} responses)")
    print(f"{'='*60}")

    groups = {}
    for e in entries:
        key = (e["model_key"], e["framing"])
        groups.setdefault(key, []).append(e)

    for (model, framing), group in sorted(groups.items()):
        total = len(group)
        api_blocks = sum(1 for e in group if e.get("api_level_refusal"))
        successes = sum(1 for e in group if e["api_result"]["success"])
        responded = successes - api_blocks

        print(f"\n  {model} | {framing}: {total} calls")
        if api_blocks:
            print(f"    API blocks: {api_blocks}/{successes}")
        if responded > 0:
            regex_ref = sum(1 for e in group
                          if e.get("regex_refusal") and e["regex_refusal"]["is_refusal"])
            print(f"    Regex refusals: {regex_ref}/{responded}")

        for e in group:
            if not e["api_result"]["success"]:
                print(f"    {e['task_id']}: ERROR")
            elif e.get("api_level_refusal"):
                print(f"    {e['task_id']}: API-BLOCK")
            else:
                preview = e["response_text"][:60].replace("\n", " ")
                print(f"    {e['task_id']}: OK | {preview}...")


def main():
    parser = argparse.ArgumentParser(description="Defender's Dilemma evaluation harness")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run")
    run_p.add_argument("--models", nargs="+", required=True, choices=list(MODELS.keys()))
    run_p.add_argument("--framings", nargs="+", default=["neutral", "ir_authorized"],
                       choices=list(FRAMINGS.keys()))
    run_p.add_argument("--epochs", type=int, default=1)
    run_p.add_argument("--tasks", default="all_tasks.json")
    run_p.add_argument("--task-ids", nargs="+", default=None,
                       help="Filter to specific task IDs (e.g., --task-ids A1 C5)")
    run_p.add_argument("--run-id", default=None)
    run_p.add_argument("--delay", type=float, default=2.0)

    sum_p = sub.add_parser("summary")
    sum_p.add_argument("run_id")

    args = parser.parse_args()
    if args.command == "run":
        rid = run_phase(args.models, args.framings, args.epochs,
                        args.tasks, args.task_ids, args.run_id, args.delay)
        print_summary(rid)
    elif args.command == "summary":
        print_summary(args.run_id)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
