"""Exact material-name aliases and file-content identity for scan placement."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


def standard_material_key(item):
    """Storage category, distinct from the identity of a physical certificate.

    Categories may contain several companies, people or reporting periods;
    ownership and exact document selection remain downstream requirements.
    Unknown requests stay in memory and use a shared container only on upload.
    """
    from api.services.tenders.placeholders import DEFAULT_SLOTS
    from api.services.tenders.match import _kind_hits

    known = {slot.key for slot in DEFAULT_SLOTS} | {'qualification', 'safety', 'other_materials'}
    if item.key in known:
        return item.key
    if item.key == 'bank_guarantee':
        return 'bond'
    direct = canonical_material_key(item)
    if direct:
        return direct
    from api.services.tenders.match import normalize_material_title

    name = normalize_material_title(item.title)
    if not (_kind_hits(name) & known):
        name += ' ' + str(item.hint or '')
    if re.search(r'基本(?:存款)?账户|开户许可', name):
        return 'bank_permit'
    if re.search(r'投标保证金|(?:电子|银行)保函|保证金.*(?:回单|凭证|缴存)', name):
        return 'bond'
    if re.search(r'资质证书|企业资质', name) and not re.search(r'iso|体系', name, re.I):
        return 'qualification'
    hits = _kind_hits(name) & known
    return next(iter(hits)) if len(hits) == 1 else ''


def canonical_material_key(item):
    """Use evidence in the material name, not opaque generated key prefixes.

    Exact default names and narrow generic variants identify the same material.
    Named people/companies, reporting periods and distinct certificates remain
    separate; sharing a broad material category alone is not equivalence.
    """
    from api.services.tenders.placeholders import DEFAULT_SLOTS

    def normalized(title):
        from api.services.tenders.match import normalize_material_title

        title = re.sub(r'^(?:附[：:]|此处粘贴|在此粘贴)\s*', '', (title or '').strip())
        title = re.sub(r'(?:的)?(?:复印件或扫描件|复印件|扫描件)', '', title)
        title = re.sub(r'[（(](正本|副本)[）)]', r'\1', title)
        title = re.sub(r'[（(]加盖(?:单位)?公章[）)]$', '', title)
        title = re.sub(r'[（(]\s*[）)]', '', title)
        return normalize_material_title(title)

    title = normalized(item.title)
    defaults = {slot.key: slot for slot in DEFAULT_SLOTS}
    if item.key in defaults:
        return item.key
    for key, slot in defaults.items():
        if title and title == normalized(slot.title):
            return key
    variants = {
        'license': r'(?:本企业|本公司|本单位|企业|企业法人)?营业执照(?:正本|副本)?',
        'id_legal': r'(?:法定代表人|法人代表|法人)身份证(?:正反面|正面及反面)?',
        'id_agent': r'(?:授权代理人|委托代理人|授权委托人|被授权人|代理人)身份证(?:正反面|正面及反面)?',
        'bank_permit': r'(?:(?:基本账户)?开户许可证(?:正本|副本)?|基本存款账户(?:信息|证明)?|基本账户(?:信息|开户证明))',
        'bond': r'(?:(?:投标)?保证金(?:缴存回单|缴纳凭证|缴纳证明|电子保函凭证|回单|汇款凭证|凭证)?|银行保函|电子保函)(?:[（(][^）)]*[）)])?',
        'credit': r'(?:企业)?信用(?:查询|报告|截图)(?:截图|打印件)?',
    }
    return next((key for key, pattern in variants.items() if re.fullmatch(pattern, title)), '')


def material_key_aliases(items, *, preferred_keys=()):
    from api.services.tenders.match import normalize_material_title, material_sources, _kind_hits

    preferred = set(preferred_keys)
    groups = {}
    by_key = {item.key.strip(): item for item in items if item.key.strip()}
    for key, item in by_key.items():
        standard = canonical_material_key(item)
        title = normalize_material_title(item.title)
        # Empty labels provide no evidence that two material keys are aliases.
        group = ('standard', standard) if standard else (("title", title) if title else ("key", key))
        sources = material_sources(str(item.title or '') + ' ' + str(item.hint or ''))
        kinds = frozenset(_kind_hits(title) or _kind_hits(item.hint))
        groups.setdefault((group, sources, kinds), []).append(key)
    aliases = {}
    for (group, sources, kinds), keys in groups.items():
        canonical = group[1] if group[0] == 'standard' and not sources else next((key for key in keys if key in preferred), keys[0])
        aliases.update((key, canonical) for key in keys)
    return aliases


def file_identity(path: Path, cache: dict) -> str:
    resolved = path.resolve()
    if resolved not in cache:
        try:
            with path.open("rb") as stream:
                cache[resolved] = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError:
            # Leave unreadable files for the existing renderer's error handling.
            cache[resolved] = "path:" + str(resolved)
    return cache[resolved]


def unique_material_files(files, *, cache=None):
    cache = {} if cache is None else cache
    result = []
    seen = set()
    for value in files:
        path = Path(value)
        identity = file_identity(path, cache)
        if identity not in seen:
            seen.add(identity)
            result.append(path)
    return result


_TITLE_NOISE = (
    '复印件', '扫描件', '原件', '加盖公章', '加盖单位公章', '证明材料', '相关资料',
    '及所附', '及其', '以及', '有关', '相关', '情况', '资料', '材料', '文件',
    '说明', '列表', '清单', '表',
)
_CORE = (
    r'营业执照', r'开户许可|基本(?:存款)?账户', r'身份证', r'建造师',
    r'注册电气工程师', r'注册执业资格', r'安全生产考核', r'安全生产许可',
    r'社保|社会保险', r'劳动合同', r'职称', r'资质证书', r'质量(?:管理)?体系|iso',
    r'财务审计|审计报告', r'财务报表', r'纳税|完税', r'信用中国', r'企业信用',
    r'业绩|类似项目', r'合同', r'保证金', r'保函', r'简历', r'承诺书', r'联合体',
    r'(?:正在实施|正在供货|新承接).*项目',
)
# Chinese has no word separators. Keep document subjects and scope words as
# whole tokens; retain unknown text rather than silently discarding qualifiers.
_TITLE_TERMS = (
    '营业执照', '开户许可证', '基本账户', '身份证', '建造师', '注册电气工程师',
    '注册执业资格', '安全生产考核', '安全生产许可', '社会保险', '社保', '劳动合同',
    '职称', '资质证书', '质量管理体系', '质量体系', 'iso', '财务审计', '审计报告',
    '纳税', '完税', '信用中国', '企业信用', '类似项目', '业绩', '合同协议书',
    '合同', '中标通知书', '保证金', '保函', '简历', '承诺书', '联合体',
    '正在履行', '新承接', '近年', '已完成', '完成', '项目', '法定代表人',
    '法人代表', '授权代理人', '委托代理人', '代理人', '正反面',
)


def material_title_fingerprint(title: str) -> tuple[str, ...]:
    """Normalize a title into conservative subject/scope tokens, without changing keys."""
    from api.services.tenders.match import normalize_material_title

    value = normalize_material_title(title)
    value = re.sub(r'^(?:(?:附[：:]|此处粘贴|在此粘贴)\s*)+', '', value)
    value = re.sub(r'[（(][^（）()]*[）)]', '', value)
    for noise in sorted(_TITLE_NOISE, key=len, reverse=True):
        value = value.replace(noise, '')
    value = re.sub(r'^(?:[（(]?[一二三四五六七八九十]+[）)]?[、.．]?|\d+[.．、]|第\d+)', '', value)
    value = re.sub(r'正在(?:实施|供货)', '正在履行', value)
    terms = '|'.join(re.escape(term) for term in sorted(_TITLE_TERMS, key=len, reverse=True))
    value = re.sub(f'({terms})', r' \1 ', value)
    value = re.sub(r'[及和的与]', ' ', value)
    value = re.sub(r'[^\w、]+', ' ', value)
    return tuple(token for token in re.split(r'[、\s]+', value.strip()) if len(token) >= 2)


def material_title_similarity(a: str, b: str) -> float:
    """Jaccard similarity of title tokens; missing subjects never count as a match."""
    left, right = set(material_title_fingerprint(a)), set(material_title_fingerprint(b))
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def same_material_title(a: str, b: str) -> bool:
    """Align synonyms only within the same evidence subject, person role and project phase."""
    from api.services.tenders.match import normalize_material_title

    left, right = normalize_material_title(a), normalize_material_title(b)
    if not any(re.search(core, left, re.I) and re.search(core, right, re.I) for core in _CORE):
        return False
    negative = r'不适用|无需|免于'
    if bool(re.search(negative, a)) != bool(re.search(negative, b)):
        return False
    # Shared ID/contract words alone must not erase who or which period is requested.
    for scopes in (
        (r'法定代表人|法人代表|法人', r'授权代理人|委托代理人|被授权人|代理人'),
        (r'近年完成|已完成|已完工|已竣工', r'正在实施|正在供货|正在履行|在建|新承接'),
    ):
        left_scope = {i for i, pattern in enumerate(scopes) if re.search(pattern, left)}
        right_scope = {i for i, pattern in enumerate(scopes) if re.search(pattern, right)}
        if left_scope != right_scope:
            return False
    left_tokens = set(material_title_fingerprint(a))
    right_tokens = set(material_title_fingerprint(b))
    common = left_tokens & right_tokens
    return material_title_similarity(a, b) >= 0.7 or (
        len(common) >= 2 and (left_tokens <= right_tokens or right_tokens <= left_tokens)
    )
