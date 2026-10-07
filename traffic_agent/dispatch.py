"""Frozen preview tickets; at-most-once launch within each service lifetime."""
import copy
import hashlib
import json
import threading
import time
import uuid
from pathlib import Path

from .agent import Agent
from .data import input_fingerprints, save_json
from .llm_client import client_for
from .runner import runtime_lock, now
from .scenario_match import match_scenario
from .task import Task
from .storage import ensure_space
from .natural_plan import compose, VERSION
from .feedback_planner import PlanningSession,LEGACY_REQUEST_LIMIT


def evidence_review(state):
    """Small, factual feedback summary. Never ask the model to invent conclusions."""
    runs = state.get("runs", [])
    compared = [r for r in runs if r.get("comparison")]
    chosen = next((r for r in runs if r["name"] == state.get("recommendation")), None)
    rejected = [r for r in compared if not r["comparison"].get("eligible")]
    diagnosis = state.get("task", {}).get("scenario") == "diagnosis"
    result = dict(kind="diagnosis" if diagnosis else "evaluation", status=state["status"],
                  completed_runs=sum(r.get("execution", {}).get("status") == "completed" for r in runs),
                  rejected_count=len(rejected), recommendation=state.get("recommendation"),
                  outcome=("仅完成现状诊断，未执行治理" if diagnosis else
                           "没有满足筛选条件的候选" if state["status"] == "no_candidate" else
                           "保留基准；未形成可推荐的治理措施" if not chosen or not chosen.get("changes") else
                           "形成已测条件下的治理建议，仍需查看不利影响与适用范围"),
                  rejection_reasons=list(dict.fromkeys(v for r in rejected for v in r["comparison"].get("violations", [])))[:8],
                  observations=state.get("diagnostic_observations", []),
                  robust_across_tested_demand=state.get("robust_across_tested_demand"))
    if chosen and chosen.get("comparison"):
        result["delta"] = chosen["comparison"]["delta"]
    from .planning_review import coverage
    result['goal_review'] = coverage(state)
    if state.get('research_review'):result['research_review']=state['research_review']
    from .conclusion_review import assess_state
    result['conclusion_review']=assess_state(state)
    return result


class Dispatcher:
    def __init__(self, source, root, settings):
        self.source, self.root, self.settings = Path(source), Path(root), settings
        self.lock = threading.RLock()
        self.match_lock = threading.Lock()
        self.tickets = {}
        self.active = None
        self.closed = False

    def preview(self, body):
        if set(body) - {"request", "overrides", "matcher"}:
            raise ValueError("未知情景匹配字段")
        # Serialize paid previews; only a bounded validator-driven correction may continue.
        if not self.match_lock.acquire(blocking=False):
            raise ValueError("情景匹配正在进行，请等待当前结果；未追加模型调用")
        try:
            if body.get("matcher") == "model" and self.active and self.active.is_alive():
                raise ValueError("策略实验仍在执行，请完成后再规划新任务；未请求模型")
            fingerprints = input_fingerprints(self.source)
            if body.get("matcher") == "model" and body.get("overrides"):
                raise ValueError("大模型模式仅接收自然语言；请将预算和目标直接写在描述中")
            key = hashlib.sha256(json.dumps([VERSION, body, fingerprints, self.settings.revision()], sort_keys=True).encode()).hexdigest()
            with self.lock:
                for ticket in self.tickets.values():
                    if ticket["key"] == key and time.monotonic() - ticket["time"] < 1800:
                        return copy.deepcopy(ticket["result"])
            client = client_for(*self.settings.credentials()) if body.get("matcher") == "model" else None
            if client is not None:
                from .initial_planner import reviewed_compose
                result=reviewed_compose(body.get('request'),self.source,client,self.root,compose)
            else:
                result=match_scenario(body,self.source)
            result["ticket"] = uuid.uuid4().hex
            result["matched_at"] = now()
            with self.lock:
                if len(self.tickets) >= 32:
                    del self.tickets[next(iter(self.tickets))]
                self.tickets[result["ticket"]] = dict(key=key, time=time.monotonic(), result=copy.deepcopy(result),
                                                      fingerprints=fingerprints, revision=self.settings.revision(), job=None)
            return result
        finally:
            self.match_lock.release()

    def launch(self, token):
        if not isinstance(token, str):
            raise ValueError("请先匹配生成任务卡")
        with self.lock:
            ticket = self.tickets.get(token)
            if not ticket:
                raise ValueError("任务卡不存在或服务已重启，请重新匹配；历史任务不会自动重跑")
            if ticket["job"]:
                return dict(id=ticket["job"], reused=True)
            if self.closed or time.monotonic() - ticket["time"] >= 1800:
                raise ValueError("任务卡已过期，请重新匹配")
            if ticket["result"]["status"] != "ready":
                raise ValueError("情景未通过执行检查")
            if self.active and self.active.is_alive():
                raise ValueError("TranStar 任务正在执行，请等待完成")
            definitions = ticket["result"].get("experiments") or [dict(task=ticket["result"]["task"], title="情景实验")]
            with runtime_lock(self.root):
                if input_fingerprints(self.source) != ticket["fingerprints"]:
                    raise ValueError("源场景已变化，请重新匹配任务")
                # Check every planned control study before creating any job.
                # The startup's default parking task does not require gcc.
                from .preflight import check
                for definition in definitions:
                    study = Task.from_dict(definition["task"])
                    if study.scenario == "oneway" or (study.scenario == "circulation" and study.turn is not None):
                        errors = check(study, self.source)["errors"]
                        if errors:
                            raise ValueError("运行依赖未通过：" + "；".join(errors))
            ensure_space([x['task'] for x in definitions], self.source, self.root)
            model = ticket['result'].get('matcher') == 'model'
            if model and ticket.get('revision') != self.settings.revision():
                raise ValueError('模型设置已变化，请重新匹配任务')
            planner = PlanningSession(client_for(*self.settings.credentials()), ticket['result']['request'],
                                      ticket['result'].get('model_usage'),hypotheses=ticket['result'].get('assessment'),
                                      initial_usages=ticket['result'].get('initial_model_usages'),
                                      limit=ticket['result'].get('model_budget',{}).get('limit',LEGACY_REQUEST_LIMIT),
                                      choice_basis_protocol=ticket['result'].get('choice_basis_protocol')) if model else None
            agents = [Agent(Task.from_dict(x["task"]), self.source, self.root,
                            expected_inputs=ticket["fingerprints"]) for x in definitions]
            if planner:
                for agent in agents:
                    if agent.task.scenario in ('parking', 'oneway'):
                        agent.planner = planner
            first = agents[0].job_id
            programme=None
            if planner:
                from .programme_review import manifest,VERSION as PROGRAMME_VERSION
                from .research_evidence import VERSION as EVIDENCE_VERSION
                from .feedback_planner import digest
                programme=manifest(first,definitions,agents,planner.hypotheses,ticket['fingerprints'],now(),
                                   model_limit=planner.limit,initial_requests=planner.used,choice_basis_protocol=planner.choice_basis_protocol,
                                   knowledge_retrieval_protocol=ticket['result'].get('knowledge_retrieval_protocol'))
                if ticket['result'].get('initial_review_protocol'):
                    from .initial_planner import evidence
                    programme['initial_planning_digest']=digest(evidence(ticket['result']))
                save_json(self.root/'programmes'/first/'plan.json',programme)
                for agent in agents:
                    agent.programme_plan=programme
                    agent.state.setdefault('task',agent.task.as_dict())
                    agent.state.update(programme_protocol=PROGRAMME_VERSION,programme_plan_digest=digest(programme),
                                       evidence_protocol=EVIDENCE_VERSION,research_hypotheses=planner.hypotheses)
                planner.bind_programme(programme,agents)
            record = dict(ticket["result"], job_id=first, job_ids=[a.job_id for a in agents],
                          status="starting", started=now(), source_inputs=ticket["fingerprints"],
                          progress=[dict(job_id=a.job_id, title=x["title"], status="queued", runs=x["task"]["max_runs"])
                                    for a, x in zip(agents, definitions)])
            if programme:record['programme_protocol']=programme['version']
            path = self.root / "dispatches" / (first + ".json")
            def planning_updated():
                record.update(planning_trace=planner.trace, model_budget=planner.budget())
                save_json(path, record)
            if planner:
                planner.on_update = planning_updated
                planning_updated()
            save_json(path, record)
            ticket["job"] = first

            def work():
                try:
                    pending = list(range(len(agents)))
                    index = 0
                    while pending:
                        pending.remove(index)
                        agent = agents[index]
                        record.update(status="running", active_job=agent.job_id)
                        progress = record["progress"][index]
                        progress["status"] = "running"
                        if planner:
                            planner.final_group=not pending
                            planner.pending_studies=[programme['studies'][i] for i in pending]
                            from .planning_budget import reserved_after_current
                            planner.reserved_requests=reserved_after_current(agent.task.as_dict(),[definitions[i]['task'] for i in pending])
                        save_json(path, record)
                        try:
                            agent.run()
                        except Exception:
                            progress["status"] = "failed"
                            raise
                        progress["status"] = agent.state["status"]
                        if agent.state["status"] not in ("completed", "no_candidate", "incomplete"):
                            raise RuntimeError("原生任务未正常结束")
                        progress.update(recommendation=agent.state.get("recommendation"), run_count=agent.state["run_count"],
                                        review=evidence_review(agent.state),
                                        comparison_scope="仅相对此组的新基准评价；不跨适配器混算")
                        save_json(path, record)
                        if agent.state['status']=='incomplete':
                            for i in pending:record['progress'][i].update(status='skipped',reason='前组研究未完成；保留证据，不宣称后续已执行')
                            break
                        if planner:
                            # A last parking decision may already review every completed run.
                            last = planner.trace[-1] if planner.trace else None
                            from .feedback_planner import run_observations
                            reviewed = (not pending and last and last['job_id'] == agent.job_id and
                                        last.get('action') == 'stop' and last['status'] == 'accepted' and
                                        last['input']['observations'] == run_observations(agent.state))
                            from .study_handoff import actions as handoff_actions
                            transferred=bool(last and last['status']=='accepted' and last['job_id']==agent.job_id
                                             and last.get('action') in handoff_actions(last['input']['context'])
                                             and last['input']['observations']==run_observations(agent.state))
                            if transferred:
                                next_index=next((i for i in pending if programme['studies'][i]['id']==last['action']),None)
                                if next_index is None:raise ValueError('模型选定的后续研究不在待执行集合中')
                            else:
                                next_index = None if reviewed else planner.route(agent, [(i, definitions[i]) for i in pending],
                                                                                 [p for p in record['progress'] if p.get('review')])
                            progress.update(status=agent.state['status'],review=evidence_review(agent.state))
                            if next_index is None:
                                for i in pending:
                                    record['progress'][i].update(status='skipped', reason=planner.trace[-1].get('reason'))
                                break
                            index = next_index
                        elif pending:
                            index = pending[0]
                    record["status"] = ('incomplete' if pending or any(a.state['status']=='incomplete' for a in agents) or (planner and planner.halted) else
                                        'no_candidate' if all(a.state["status"] == "no_candidate" for a in agents) else 'completed')
                    if planner and record['status']=='completed' and planner.trace[-1]['status']!='accepted':
                        record['status']='incomplete'
                except Exception:
                    record["status"] = "failed"
                    record["error"] = "研究执行中断，停止后续调度；详见子任务记录"
                    for p in record["progress"]:
                        if p["status"] == "queued":
                            p["status"] = "skipped"
                finally:
                    record["finished"] = now()
                    record.pop("active_job", None)
                    if programme:
                        from .programme_review import review
                        try:
                            record['programme_review']=review(programme,{a.job_id:a.state for a in agents},planner.trace)
                        except Exception:
                            record['status']='failed'
                            record['error']='研究证据整理失败；原始输出保留，不自动重新调用模型或仿真'
                            record['programme_review']=dict(complete=False,reviewed=False,summary=record['error'],unanswered=[record['error']],
                                hypotheses=planner.hypotheses,updates=[],evidence=dict(groups=[],findings=[],scope='证据整理未完成。'))
                        if record['status']=='completed' and not record['programme_review']['complete']:record['status']='incomplete'
                        save_json(self.root/'programmes'/first/'result.json',record)
                    save_json(path, record)
                    for agent in agents:
                        if agent.job.is_dir():
                            save_json(agent.job / "dispatch.json", record)
                            if planner:
                                agent.state['programme_review']=record['programme_review']
                                agent.persist()
                                from .report import write_report
                                write_report(agent.job, agent.state)

            self.active = threading.Thread(target=work, name="dispatch-" + first, daemon=False)
            self.active.start()
            return dict(id=first, job_ids=record["job_ids"], reused=False)

    def listing(self):
        records = []
        for p in sorted((self.root / "dispatches").glob("*.json"), reverse=True)[:20]:
            try:
                record = json.loads(p.read_text("utf-8"))
                if (not isinstance(record, dict) or not isinstance(record.get('job_id'), str)
                        or not record['job_id'] or not isinstance(record.get('status'), str)):
                    raise ValueError('调度记录不可读')
                for key in ('planning_trace', 'progress'):
                    if key in record and (not isinstance(record[key], list)
                            or any(not isinstance(row, dict) for row in record[key])):
                        raise ValueError('调度记录不可读')
                if record.get('programme_review') is not None and not isinstance(record['programme_review'], dict):
                    raise ValueError('调度记录不可读')
                state_path = self.root / "jobs" / record["job_id"] / "state.json"
                status = record["status"]
                if state_path.exists() and "progress" not in record:
                    status = json.loads(state_path.read_text("utf-8"))["status"]
                live = self.active and self.active.is_alive() and self.active.name == "dispatch-" + record["job_id"]
                if status in ("starting", "created", "running") and not live:
                    status = "interrupted"  # Never resume an old native experiment implicitly.
                shown = {k: record.get(k) for k in ("job_id", "job_ids", "active_job", "progress", "goal", "experiments",
                                "request", "scenario", "reason", "matcher", "started", "model_usage", "budget", "allocated_runs",
                                "version", "assessment", "alternatives", "assumptions", "planning_basis", "unallocated_runs",
                                "knowledge_retrieval", "knowledge_retrieval_protocol")}
                shown.update({k: record.get(k) for k in ('planning_trace', 'model_budget', 'normalization_notes', 'programme_protocol', 'programme_review',
                                                             'execution_scope','decision_permissions','initial_review_protocol','initial_planning_attempts','initial_model_usages','routing_budget','budget_check')})
                from .planning_review import display_trace, coverage
                shown['planning_trace'] = display_trace(record.get('planning_trace', []))
                from .conclusion_review import assess_traces,assess_state
                conclusion=assess_traces(record.get('planning_trace',[]))
                shown['conclusion_review']=conclusion
                # A newer, read-only claim audit can disclose conflicts in
                # sealed prose without changing its original protocol verdict.
                public_claim_review=None;public_claim_owner=None
                latest_final=next((t for t in reversed(record.get('planning_trace',[]))
                                   if t.get('status')=='accepted' and t.get('action')=='stop'
                                   and t.get('input',{}).get('context',{}).get('measured_conclusion',{}).get('version')
                                   in ('measured-conclusion-v5','measured-conclusion-v6','measured-conclusion-v7',
                                       'measured-conclusion-v8','measured-conclusion-v9','measured-conclusion-v10',
                                       'measured-conclusion-v11','measured-conclusion-v12')),None)
                if latest_final:
                    from .conclusion_review import contract
                    from .public_claims import VERSION as CLAIM_VERSION,issues as public_claim_issues,demand_cost_issues
                    frozen=latest_final['input'];facts=contract(frozen['observations'],frozen['context'])
                    latest_input=latest_final.get('review_correction',{}).get('input') or frozen
                    original_protocol=facts['version']
                    found=(public_claim_issues(latest_final,facts,latest_input.get('model_budget'))
                           if original_protocol=='measured-conclusion-v5' else
                           demand_cost_issues(latest_final,facts,frozen['context'])
                           if original_protocol=='measured-conclusion-v6' else [])
                    from .metric_assertions import VERSION as METRIC_VERSION,issues as metric_issues
                    found += metric_issues(latest_final,facts,frozen['context'],frozen['observations'])
                    public_claim_review=dict(version=CLAIM_VERSION,status='needs_review' if found else 'no_detected_conflict',
                                             metric_protocol=METRIC_VERSION,issues=found,read_only=True,original_protocol=original_protocol,
                                             job_id=latest_final.get('job_id'),ordinal=latest_final.get('ordinal'))
                    public_claim_owner=latest_final.get('job_id')
                    shown['public_claim_review']=public_claim_review
                    for shown_trace in shown['planning_trace']:
                        if (shown_trace.get('job_id'),shown_trace.get('ordinal')) == (public_claim_owner,latest_final.get('ordinal')):
                            shown_trace['public_claim_review']=public_claim_review
                prior_review=shown.get('programme_review')
                if prior_review and public_claim_review and public_claim_review['status']=='needs_review':
                    prior_review['recorded_summary']=prior_review.get('summary')
                    prior_review['summary']='公开模型总结与结构化实测存在冲突；以下原生对照数值可单独查看，模型原文保留在逐轮记录中。'
                    prior_review['recorded_complete']=prior_review.get('complete')
                    prior_review['complete']=False
                    prior_review['unanswered']=list(prior_review.get('unanswered') or [])+['公开模型总结有独立复核指出的事实冲突。']
                old_conflict='模型结论与实测代价或已知未测量项存在冲突，结论待核对。'
                if (prior_review and conclusion.get('unreviewed') and not conclusion['issues']
                        and old_conflict in prior_review.get('unanswered',[])):
                    # Earlier versions inferred a conflict from missing rejected
                    # arguments. Correct the read view without rewriting evidence.
                    prior_review['recorded_unanswered']=list(prior_review['unanswered'])
                    prior_review['unanswered']=[n for n in prior_review['unanswered'] if n!=old_conflict]+[
                        '工具回复未通过校验，完整模型复核仍缺失；现有记录不足以推定具体事实冲突。']
                for progress in shown.get('progress') or []:
                    path=self.root/'jobs'/progress['job_id']/'state.json'
                    state=None
                    progress['completed_run_count']=None
                    checked=assess_traces([t for t in record.get('planning_trace',[]) if t.get('job_id')==progress['job_id']])
                    if path.is_file():
                        state=json.loads(path.read_text('utf-8'))
                        from .study_comparison import comparison
                        if state.get('id') and state.get('task',{}).get('demand_factors'):
                            progress['study_comparison']=comparison(state)
                        # Group summaries are saved on exit; expose completed scenes
                        # while the group is still running without changing evidence.
                        if isinstance(state.get('runs'),list):
                            progress['completed_run_count']=sum(r.get('execution',{}).get('status')=='completed' for r in state['runs'])
                        checked=assess_state(state)
                        enhanced={r['ref']:r for r in checked['costs']}
                        conclusion['costs']=[enhanced.get(r['ref'],r) for r in conclusion['costs']]
                    if progress.get('review'):
                        progress['review']['conclusion_review']=checked
                        if state is not None:
                            current_goal=coverage(state)
                            if current_goal is not None:
                                prior_goal=progress['review'].get('goal_review')
                                if prior_goal is not None and prior_goal!=current_goal:
                                    progress['review']['recorded_goal_review']=prior_goal
                                progress['review']['goal_review']=current_goal
                        research=progress['review'].get('research_review')
                        if research and checked['status']=='needs_review':
                            research['recorded_reviewed']=research.get('reviewed')
                            research['reviewed']=False
                    if progress.get('status')=='completed' and checked['status']=='needs_review':
                        progress.update(status='incomplete',recorded_status='completed')
                    if (progress.get('status')=='completed' and progress.get('job_id')==public_claim_owner
                            and public_claim_review and public_claim_review['status']=='needs_review'):
                        progress.update(status='incomplete',recorded_status='completed',public_claim_review=public_claim_review)
                        research=progress.get('review',{}).get('research_review')
                        if research:
                            research['recorded_reviewed']=research.get('reviewed')
                            research['reviewed']=False
                    if progress.get('status')=='completed' and progress.get('review',{}).get('goal_review',{}):
                        if not progress['review']['goal_review']['complete']:
                            progress.update(status='incomplete',recorded_status='completed')
                if status=='completed' and any(p.get('status')=='incomplete' for p in shown.get('progress') or []):
                    shown['recorded_status']=status
                    status='incomplete'  # Read-only presentation; sealed records remain unchanged.
                if status=='completed' and conclusion['status']=='needs_review':
                    shown['recorded_status']=status
                    status='incomplete'
                if status=='completed' and public_claim_review and public_claim_review['status']=='needs_review':
                    shown['recorded_status']=status
                    status='incomplete'
                shown["status"] = status
                json.dumps(shown, allow_nan=False)
                records.append(shown)
            except (ValueError, OSError, KeyError, TypeError, AttributeError, OverflowError):
                records.append(dict(status="unreadable", job_id=p.stem))
        return records

    def close(self):
        self.closed = True  # Existing non-daemon simulation completes with its evidence intact.
