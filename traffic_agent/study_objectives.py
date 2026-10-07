"""Freeze separately requested measurements before any native study starts.

Only an explicit separate-check clause is frozen. Historical v1 requests retain
their original interpretation when a sealed planning record is replayed.
"""
import re

from .planning_scope import PrerequisiteError

LEGACY_VERSION = 'explicit-measurements-v1'
VERSION = 'explicit-measurements-v2'
LEGACY_INTRO = re.compile(r'分别(?:检验|判断|核对|评估)([^。；;\n]{1,180})')
INTRO = re.compile(r'分别(?:检验|判断|核对|评估|检查)([^。；;\n]{1,180})')
LEGACY_MARKERS = (
    ('control_response', r'(?:原生|禁行|禁转|控制).{0,12}(?:控制|响应|生效)|(?:禁行|禁转)(?:是否)?生效'),
    ('core_time_decrease', r'(?:冻结)?核心.{0,12}(?:耗时|时间).{0,12}(?:下降|降低|改善)'),
    ('neighbor_limits', r'(?:邻区|周边).{0,16}(?:代价|耗时|过饱和).{0,18}(?:门槛|阈值|约束|可接受|以内)'),
)
MARKERS = (
    LEGACY_MARKERS[0],
    ('core_time_decrease', r'(?:冻结)?核心.{0,12}(?:耗时|时间).{0,12}(?:下降|降低|改善)'),
    ('region_no_worsening', r'(?:各|每个|全部|所有)(?:候选)?(?:区域|区).{0,8}(?:恶化|变差|耗时增加|耗时上升|耗时增长|不恶化|不增加)'),
    ('neighbor_limits', r'(?:邻区|周边).{0,18}(?:(?:代价|耗时|过饱和).{0,18}(?:门槛|阈值|约束|可接受|以内)|(?:满足|通过).{0,12}(?:原定|预定)?(?:门槛|阈值|约束))'),
)


def required(text, version=VERSION):
    if version not in (LEGACY_VERSION, VERSION):
        raise ValueError('未知的分别检验解析协议')
    match = (LEGACY_INTRO if version==LEGACY_VERSION else INTRO).search(text or '')
    if not match:
        return dict(version=version, source_quote='', tests=[], policy_scopes={})
    clause = match.group(1)
    markers=LEGACY_MARKERS if version==LEGACY_VERSION else MARKERS
    tests = [name for name, pattern in markers if re.search(pattern, clause)]
    preface = re.split(r'[。；;\n]',text[:match.start()])[-2:]
    preface = '。'.join(preface)
    full_set=all(word in preface for word in ('停车','单向','组合'))
    policy_scopes=({test:('control_and_combined' if test=='control_response' else 'all')
                    for test in tests} if full_set else {})
    return dict(version=version, source_quote=match.group(), tests=tests, policy_scopes=policy_scopes)


def verify(requirements, assessment):
    actual = {item.get('criterion', {}).get('test') for item in assessment}
    missing = [test for test in requirements['tests'] if test not in actual]
    if missing:
        names = {'control_response': '原生控制响应', 'core_time_decrease': '冻结核心耗时',
                 'region_no_worsening': '各区域耗时是否恶化', 'neighbor_limits': '邻区原定门槛'}
        raise PrerequisiteError('原问题明确要求分别检验：'+ '、'.join(names[x] for x in missing) +
                                '；初始评估未设置独立判据。保持原问题与研究范围，修正后才可执行仿真')
    for test, policies in requirements['policy_scopes'].items():
        candidates=[item['criterion'] for item in assessment if item['criterion']['test']==test]
        allowed=('all','control_and_combined') if policies=='control_and_combined' else ('all',)
        if not any(c['policies'] in allowed for c in candidates):
            raise PrerequisiteError('原问题明确要求停车、单向及组合的分别检验；'+test+
                                    '判据缩小了被检验措施，保持原研究范围后才可执行仿真')
