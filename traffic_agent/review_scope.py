"""One programme hypothesis review, after every independent study is measured.

Intermediate reviews still confirm the study's evidence and measured costs.
They must not judge global hypotheses using only local finding identifiers.
Legacy records retain their original review contract.
"""
VERSION = 'hypothesis-review-scope-v1'


def contract(study_count):
    return dict(version=VERSION, mode='programme_final_only' if study_count > 1 else 'single_study',
                rule='多组任务的初始假设只在最终总复核中逐项判断，并引用带study编号的证据；中途完整复核本组实测和代价。')


def programme_only(value):
    return value.get('review_scope', {}).get('mode') == 'programme_final_only'


def local_hypotheses(state):
    return [] if programme_only(state) else state.get('research_hypotheses', [])


def verify_context(state, context):
    if state.get('review_scope') != context.get('review_scope'):
        raise ValueError('假设复核范围与冻结研究任务不符')
    if programme_only(context) and context.get('initial_hypotheses'):
        raise ValueError('跨组假设不能放入仅有本组引用的复核')
