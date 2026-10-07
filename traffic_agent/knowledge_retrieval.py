"""Bounded retrieval from a curated local catalog, never from model output.

These snippets explain methods and tool limits. Native run observations, not
retrieved documents or past jobs, determine treatment effects and eligibility.
"""
import hashlib
import json
import math
import re
from pathlib import Path

LEGACY_VERSION = 'curated-knowledge-rag-v1'
V2_VERSION = 'curated-knowledge-rag-v2'
VERSION = 'curated-knowledge-rag-v3'
SUPPORTED_VERSIONS = (LEGACY_VERSION, V2_VERSION, VERSION)
CATALOG = Path(__file__).with_name('knowledge.json')
MAX_HITS = 3
ID = re.compile(r'[a-z0-9][a-z0-9_.-]{0,63}\Z')
CITATION = re.compile(r'【K:([^】]+)】')
HAN_OR_WORD = re.compile(r'[\u3400-\u9fff]+|[a-z0-9]+', re.I)
PHASE_TERMS = {
    'parking': '停车 需求 核心 邻区',
    'oneway_target': '单向 禁行 方向 公交',
    'route': '仿真 指标 研究 约束',
    'initial': '',
}
GENERAL_IDS = {'contest.agent', 'assignment.fixed', 'metrics.boundary'}
TOPIC_PATTERNS = {
    'parking': r'停车', 'oneway': r'单向', 'circulation': r'禁转|微循环',
    'construction': r'施工|占道', 'transit': r'公交优先|公交专用',
    'signal': r'信号|配时', 'staggered': r'错峰|分时',
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha256(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def terms(value):
    result = set()
    for part in HAN_OR_WORD.findall(value.casefold()):
        if re.fullmatch(r'[\u3400-\u9fff]+', part):
            result.update(part[i:i+2] for i in range(len(part)-1))
        elif len(part) >= 2:
            result.add(part)
    return result


def read_catalog(path=CATALOG):
    raw = Path(path).read_bytes()
    try:
        entries = json.loads(raw.decode('utf-8'))
    except (UnicodeError, ValueError) as exc:
        raise ValueError('知识库编码或JSON无效') from exc
    if not isinstance(entries, list) or not entries or len(entries) > 100:
        raise ValueError('知识库条目数量无效')
    seen = set()
    for item in entries:
        if (not isinstance(item, dict) or set(item) != {'id', 'topics', 'source', 'text'}
                or not isinstance(item['id'], str) or not ID.fullmatch(item['id'])
                or item['id'] in seen or not isinstance(item['topics'], list)
                or not item['topics'] or len(item['topics']) > 10
                or any(not isinstance(topic, str) or not ID.fullmatch(topic) for topic in item['topics'])
                or not isinstance(item['source'], str) or not 1 <= len(item['source']) <= 500
                or not isinstance(item['text'], str) or not 1 <= len(item['text']) <= 1000
                or re.search(r'sk-[A-Za-z0-9]{16,}', item['source'] + item['text'])):
            raise ValueError('知识库条目无效、重复或包含疑似密钥')
        seen.add(item['id'])
    return entries, hashlib.sha256(raw).hexdigest()


def requested_topics(request,version):
    if version==LEGACY_VERSION:
        return {topic for topic,pattern in TOPIC_PATTERNS.items() if re.search(pattern,request)}
    if version==VERSION:
        return semantic_topics(request)
    selected=set()
    for clause in re.split(r'[。；，,;\n]|但是|但|而是|改为',request):
        for topic,pattern in TOPIC_PATTERNS.items():
            for match in re.finditer(pattern,clause):
                prefix=clause[:match.start()]
                denied=re.search(r'(?:不做|不安排|不研究|不要|不采用|无需|排除)[^。；，,;\n]{0,24}$',prefix)
                if not denied or re.search(r'(?:不是|并非|并不是)不',prefix):selected.add(topic)
    return selected


def semantic_topics(request):
    """Bounded clause-local intent; negated lists never cross a contrast."""
    selected=set()
    negative=r'(?:不需要|不要|不必|暂不|无需|无须|排除|不)(?:再|先|进行|开展)?(?:考虑|涉及|需要|实施|采用|安排|研究|开展|做|执行)?'
    positive=r'(?:需要|需|要|可以|采用|安排|研究|考虑|实施|做|执行)'
    for clause in re.split(r'[。；，,;\n]|但是|然而|而是|改为|转而|但',request):
        mentions=sorted((m.start(),m.end(),topic) for topic,pattern in TOPIC_PATTERNS.items()
                        for m in re.finditer(pattern,clause))
        polarity=True;cursor=0
        for i,(start,end,topic) in enumerate(mentions):
            before=clause[cursor:start]
            # An affirmative predicate starts a new list, while a shared
            # negative predicate applies to "单向和禁转" together.
            predicates=list(re.finditer(negative+'|'+positive,before))
            if predicates:
                last=predicates[-1]
                polarity=not bool(re.fullmatch(negative,last.group()))
                if not polarity and re.search(r'(?:不是|并非|并不是)\s*$',before[:last.start()]):polarity=True
            next_start=mentions[i+1][0] if i+1<len(mentions) else len(clause)
            after=clause[end:next_start]
            suffix=re.search(r'^(?:组织|治理|方案|措施)?\s*(?:也|都|均)?\s*('+negative+r')',after)
            denied=bool(suffix)
            # "单向和禁转都不实施" attaches the shared suffix to the list.
            if not denied and i+1<len(mentions):
                tail=clause[end:]
                denied=bool(re.search(r'(?:都|均)\s*'+negative,tail)) and not re.search(positive,before)
            if polarity and not denied:selected.add(topic)
            cursor=end
    return selected


def query_text(request,version):
    if version==LEGACY_VERSION:return request
    return request.replace('平均车速','速度 汇总').replace('车速','速度').replace('算术平均','汇总')


def retrieve(request, phase, scenario=None, *, catalog_path=CATALOG,version=VERSION):
    if (not isinstance(request, str) or not isinstance(phase, str)
            or scenario is not None and not isinstance(scenario, str)):
        raise ValueError('知识检索输入无效')
    if version not in SUPPORTED_VERSIONS:raise ValueError('未知的知识检索协议')
    entries, catalog_sha256 = read_catalog(catalog_path)
    query = terms(query_text(request,version) + ' ' + PHASE_TERMS.get(phase, ''))
    topics = requested_topics(request,version)
    # The phase is context, not a license to use past experiment outcomes.
    eligible = [e for e in entries if '智能体实验/jobs/' not in e['source'].replace('\\', '/')
                and (scenario in e['topics'] if scenario is not None else
                     e['id'] in GENERAL_IDS or bool(topics.intersection(e['topics'])))]
    frequencies = {term: sum(term in terms(e['text']+' '+e['id']) for e in eligible) for term in query}
    ranked = []
    for entry in eligible:
        matching = query & terms(entry['text']+' '+entry['id'])
        if not matching:
            continue
        score = sum(1 / (1 + frequencies[term]) for term in matching)
        ranked.append((score, entry['id'], entry))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    hits = []
    for score, _, entry in ranked[:MAX_HITS]:
        hit = {key: entry[key] for key in ('id', 'topics', 'source', 'text')}
        hit['score'] = round(score, 6)
        hit['sha256'] = sha256(canonical({key: hit[key] for key in ('id', 'topics', 'source', 'text')}))
        hits.append(hit)
    return dict(version=version, phase=phase, scenario=scenario,
                query_sha256=sha256(canonical([request, phase, scenario])),
                catalog_sha256=catalog_sha256, hits=hits,
                hits_sha256=sha256(canonical(hits)),
                boundary='人工整理的本地方法摘录；只用于提出或解释待检验思路，不能替代本次原生仿真结果。')


def verify(bundle, request, phase, scenario=None, *, catalog_path=CATALOG):
    if (not isinstance(bundle, dict) or set(bundle) != {
            'version', 'phase', 'scenario', 'query_sha256', 'catalog_sha256',
            'hits', 'hits_sha256', 'boundary'} or bundle['version'] not in SUPPORTED_VERSIONS
            or bundle['phase'] != phase or bundle['scenario'] != scenario
            or not isinstance(bundle['catalog_sha256'], str)
            or not re.fullmatch(r'[0-9a-f]{64}', bundle['catalog_sha256'])
            or not isinstance(bundle['boundary'], str) or not 1 <= len(bundle['boundary']) <= 300
            or bundle['query_sha256'] != sha256(canonical([request, phase, scenario]))
            or not isinstance(bundle['hits'], list) or len(bundle['hits']) > MAX_HITS
            or bundle['hits_sha256'] != sha256(canonical(bundle['hits']))):
        raise ValueError('知识检索记录结构或摘要无效')
    ids = set()
    for hit in bundle['hits']:
        if (not isinstance(hit, dict) or set(hit) != {'id', 'topics', 'source', 'text', 'score', 'sha256'}
                or not isinstance(hit['id'], str) or not ID.fullmatch(hit['id']) or hit['id'] in ids
                or not isinstance(hit['topics'], list) or not hit['topics']
                or any(not isinstance(topic,str) or not ID.fullmatch(topic) for topic in hit['topics'])
                or not isinstance(hit['source'], str) or not 1 <= len(hit['source']) <= 500
                or '智能体实验/jobs/' in hit['source'].replace('\\','/')
                or not isinstance(hit['text'], str) or not 1 <= len(hit['text']) <= 1000
                or type(hit['score']) not in (int, float) or not math.isfinite(hit['score'])
                or hit['score'] < 0 or hit['sha256'] != sha256(canonical(
                    {key: hit[key] for key in ('id', 'topics', 'source', 'text')}))):
            raise ValueError('知识检索命中记录无效')
        ids.add(hit['id'])
    _, current_sha256 = read_catalog(catalog_path)
    if current_sha256 != bundle['catalog_sha256']:
        return 'catalog_changed'
    if bundle != retrieve(request, phase, scenario, catalog_path=catalog_path,version=bundle['version']):
        raise ValueError('知识检索结果与当期目录不一致')
    return 'verified'


def validate_citations(value, bundle):
    allowed = {hit['id'] for hit in bundle['hits']}

    def scan(item):
        if isinstance(item, str):
            if any(match not in allowed for match in CITATION.findall(item)):
                raise ValueError('知识引用不在本轮检索结果中')
            if bundle.get('version')==VERSION:
                for clause in re.split(r'[。；;\n]',item):
                    cited=CITATION.findall(clause)
                    if not cited:continue
                    denied=bool(re.search(r'不能|不得|不应|并非|不代表|尚未|未证明|待验证|待.*实测|若|如果',clause))
                    if not denied and re.search(r'(?:证明|证实|保证|确定)[^。；;\n]{0,18}(?:本次|该|此)?(?:方案|治理)?[^。；;\n]{0,12}(?:下降|改善|通过|有效|收益)',clause):
                        raise ValueError('知识引用仅支持方法，不能证明本次治理效果；效果须来自同需求原生实测')
                    if ('metrics.boundary' in cited and not denied
                            and re.search(r'(?:用|采用|按|取)[^。；;\n]{0,6}算术平均',clause)):
                        raise ValueError('知识引用与来源不符：速度按Σ(qL)/Σ(qL/v)汇总，不能按算术平均')
        elif isinstance(item, dict):
            for child in item.values():
                scan(child)
        elif isinstance(item, list):
            for child in item:
                scan(child)

    scan(value)
