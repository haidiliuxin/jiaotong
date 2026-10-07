"""Close a reviewed study and select its successor in the same model call.

This is a model choice over a frozen programme, never an executor's default.
Historical stop-then-route traces retain their original semantics and budgets.
"""
VERSION = 'study-handoff-v1'


def actions(context):
    handoff = context.get('study_handoff', {})
    return handoff.get('actions', []) if handoff.get('version') == VERSION else []


def closes_study(action, context):
    return action == 'stop' or context.get('require_hypothesis_review', False) or action in actions(context)


def route_menu(studies, remaining_requests, budget_version):
    from .planning_budget import route_guard
    guard = route_guard(studies, remaining_requests, budget_version)
    options = {s['id']: dict(title=s['title'], road=s['task'].get('road_name'),
                            scenario=s['task']['scenario'], runs=s['runs'])
               for s in studies if guard['choices'][s['id']]['feasible']}
    return options, guard


def contract(options):
    return dict(version=VERSION, actions=list(options),
                meaning='选下一组同时完成本组复核；必须确认全部本组假设和实测代价。当前组不再新增候选，后续组仍须各自建立新基准。')
