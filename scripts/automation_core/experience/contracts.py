"""Trusted caller context and strict, JSON-only observation contracts."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import hashlib
import json
import re


class ExperienceError(Exception):
    def __init__(self, code: str, message: str = '') -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


def require(ok, message='invalid payload', code='PAYLOAD_INVALID'):
    if not ok:
        raise ExperienceError(code, message)


def text(value, *, optional=False, limit=32000):
    require((optional and value is None) or (isinstance(value, str) and bool(value.strip()) and len(value)<=limit))


@dataclass(frozen=True)
class AccessContext:
    subject_id: str
    allowed_collections: tuple[str, ...]
    role: str
    artifact_roots: tuple[str, ...] = ()

    def __post_init__(self):
        text(self.subject_id, limit=200)
        require(self.role in ('owner', 'agent'), 'invalid role')
        require(isinstance(self.allowed_collections, (list, tuple)))
        require(isinstance(self.artifact_roots, (list, tuple)))
        for c in self.allowed_collections:
            text(c, limit=200)
        for root in self.artifact_roots:
            require(isinstance(root, str) and Path(root).is_absolute(), 'absolute media root required')
        object.__setattr__(self, 'allowed_collections', tuple(dict.fromkeys(self.allowed_collections)))
        object.__setattr__(self, 'artifact_roots', tuple(self.artifact_roots))

    def may_access(self, collection_id):
        return collection_id in self.allowed_collections

    def may_write_source_kind(self, source_kind):
        return source_kind in ALLOWED_SOURCE_KINDS and (self.role=='owner' or source_kind not in ('user_explicit', 'validator_observed'))

    def narrow_collections(self, collections):
        return replace(self, allowed_collections=tuple(c for c in self.allowed_collections if c in collections))


ALLOWED_SOURCE_KINDS = frozenset(('user_explicit','agent_report','validator_observed','external_document','synthetic_fixture'))
REQUIRED = {'schema_version','collection_id','event_kind','goal','narrative','source_kind','conditions','outcome','source_refs'}
OPTIONAL = {'synthetic','explicit_reason','artifacts','counterexample_ids','interpretation','preconditions'}


def validate_payload(payload):
    require(type(payload) is dict)
    require(REQUIRED <= payload.keys() and not payload.keys()-REQUIRED-OPTIONAL, 'unknown or missing fields')
    require(type(payload['schema_version']) is int and payload['schema_version']==1)
    require(payload['event_kind']=='episode')
    for name in ('collection_id','goal','narrative','source_kind'):
        text(payload[name])
    require(payload['source_kind'] in ALLOWED_SOURCE_KINDS)
    require(type(payload.get('synthetic',False)) is bool)
    if payload['source_kind']=='synthetic_fixture':
        require(payload.get('synthetic') is True, 'fixture must be labelled synthetic')
    text(payload.get('explicit_reason'), optional=True)
    conditions=payload['conditions']
    require(type(conditions) is dict and len(conditions)<=30)
    for k,v in conditions.items():
        text(k,limit=100)
        require(v is None or type(v) in (str,int,bool,float), 'conditions must be scalar metadata')
    if 'preconditions' in payload:
        pre=payload['preconditions']
        require(type(pre) is dict and 'target' in pre, 'preconditions must contain target')
        require(set(pre)<={'target','require','exclude'},
                'preconditions supports target plus require/exclude condition maps')
        text(pre['target'],limit=2000)
        for name in ('require','exclude'):
            if name in pre:
                cond=pre[name]
                require(type(cond) is dict and len(cond)<=30, f'{name} must be a condition map')
                for k,v in cond.items():
                    text(k,limit=100)
                    require(type(v) in (str,int,bool,float),'condition values must be scalar')
    outcome=payload['outcome']
    require(type(outcome) is dict and not outcome.keys()-{'status','verification','audience_feedback','business_result','evidence_refs'})
    for k,v in outcome.items():
        if k=='evidence_refs':
            require(type(v) is list and all(type(x) is str for x in v))
        else:
            text(v,optional=True)
    refs=payload['source_refs']
    require(type(refs) is list and 0<len(refs)<=30)
    for ref in refs:
        require(type(ref) is dict and {'kind','ref'}<=ref.keys() and not ref.keys()-{'kind','ref','revision','quote','timestamp'})
        for k,v in ref.items():
            require(type(v) in (str,int) and type(v) is not bool)
        text(ref['kind']);text(ref['ref'])
    artifacts=payload.get('artifacts',[])
    require(type(artifacts) is list and len(artifacts)<=20)
    seen=set()
    for a in artifacts:
        require(type(a) is dict and {'artifact_id','media_type','location','checksum','license'}<=a.keys() and not a.keys()-{'artifact_id','media_type','location','checksum','license','segment'})
        for key in ('artifact_id','media_type','location','checksum','license'):
            text(a[key])
        require(a['artifact_id'] not in seen);seen.add(a['artifact_id'])
        require(a['media_type'] in ('text','image','video','other'))
        require(a['license'] in ('read','reference'))
        require(bool(re.fullmatch('[0-9a-f]{64}',a['checksum'])))
        if a['media_type']=='video':
            text(a.get('segment'))
    ids=payload.get('counterexample_ids',[])
    require(type(ids) is list and len(ids)<=10)
    for i in ids:text(i,limit=200)
    if 'interpretation' in payload:
        i=payload['interpretation']
        require(type(i) is dict and set(i)<={'text','basis'} and 'text' in i)
        text(i['text'])
        if 'basis' in i:require(i['basis']=='inference')
    try:
        require(len(json.dumps(payload,ensure_ascii=False,allow_nan=False))<=100000,'payload too large')
    except (TypeError,ValueError):
        raise ExperienceError('PAYLOAD_INVALID','payload must be finite JSON') from None


def check_artifacts(payload,ctx):
    """Resolve only previously authorized local references, never fetch URLs."""
    for a in payload.get('artifacts',[]):
        path=Path(a['location'])
        require(path.is_absolute(),'absolute media path required','ACCESS_DENIED')
        resolved=path.resolve()
        require(any(resolved.is_relative_to(Path(root).resolve()) for root in ctx.artifact_roots),'media outside authorized roots','ACCESS_DENIED')
        require(resolved.is_file(),'referenced media missing','SOURCE_MISSING')
        # No implicit media copying; a changed file cannot masquerade as a version.
        with resolved.open('rb') as f:
            actual=hashlib.file_digest(f,'sha256').hexdigest()
        require(actual==a['checksum'],'referenced media changed','SOURCE_CHANGED')
