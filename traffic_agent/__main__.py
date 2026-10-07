"""Run with python -m traffic_agent. All paths are resolved from the repository."""
import argparse
import json
import sys
from pathlib import Path

from .agent import Agent, plan
from .runner import DEFAULT_SOURCE, EXPERIMENTS
from .task import CAPABILITIES, Task, parse_request


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="TranStar交通仿真规则智能体")
    parser.add_argument("command", choices=["plan", "run", "check", "capabilities", "serve", "analyze", "adapt", "circulation-trial", "oneway-trial"])
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--task", type=Path, help="JSON任务卡")
    inputs.add_argument("--request", help="中文受限任务模板")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--root", type=Path, default=EXPERIMENTS)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--job", type=Path, help="任务目录，用于稳健分析或追加敏感性反馈")
    args = parser.parse_args()
    try:
        if args.command == "capabilities":
            result = CAPABILITIES
        elif args.command == "check":
            from .preflight import check
            task = Task.from_dict(json.loads(args.task.read_text(encoding="utf-8-sig"))) if args.task else parse_request(args.request) if args.request else Task.from_dict({})
            result = check(task, args.source.resolve())
            if not result['preflight_passed']:
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 2
        elif args.command == "analyze":
            from .decision_analysis import decision_analysis
            if args.job is None:
                raise ValueError("analyze需要--job指定任务目录")
            result = decision_analysis(json.loads((args.job / 'state.json').read_text(encoding='utf-8')))
        elif args.command == "adapt":
            if args.job is None:
                raise ValueError("adapt需要--job指定已完成的任务")
            result = {"job": str(Agent.adapt_completed(args.job, args.source))}
        elif args.command == "serve":
            from .web import serve
            serve(args.source, args.root, args.port)
            return 0
        else:
            task = Task.from_dict(json.loads(args.task.read_text(encoding="utf-8-sig"))) if args.task else parse_request(args.request) if args.request else Task.from_dict({})
            if args.command == "circulation-trial" and task.scenario != "circulation":
                raise ValueError("circulation-trial需要circulation任务卡")
            if args.command == "oneway-trial" and task.scenario != "oneway":
                raise ValueError("oneway-trial需要oneway任务卡")
            if args.command == "plan":
                result = plan(task, args.source)
            else:
                agent = Agent(task, args.source, args.root)
                print(f"JOB {agent.job}", flush=True)
                result = {"job": str(agent.run(allow_experimental=args.command in ("circulation-trial", "oneway-trial"))), "status": agent.state["status"]}
                if result["status"] == "needs_validation":
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                    return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
