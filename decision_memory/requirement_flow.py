"""Requirement-specific operations composed into the existing Battle controller."""
import copy

from .comparison import aggregate_metrics, validate_comparison
from .evaluation import digest, make_worksheet
from .requirement_protocol import reference_policy, requirement_sample, validate_requirement_references
from .requirements import assemble_requirement, read_requirement_manifest
from .review_v2 import apply_review_v2, score_worksheet_v2
from .storage import read_json


def summarize_comparisons(records, total):
    scores = [row['score'] for row in records if isinstance(row.get('score'), dict)
              and row['score'].get('kind') == 'requirement-comparison']
    summary = {'expected_cases': total, 'fully_scored_cases': sum(score['complete'] for score in scores),
               'metric_scope': 'local_agent_review_not_official_score', 'methods': {}}
    batch_complete = len(scores) == total and all(score['complete'] for score in scores)
    for method in ('whole', 'per_node'):
        values, costs = [], []
        for score in scores:
            for label, assigned in score['methods'].items():
                if assigned == method:
                    values.append(score['variants'][label])
                    costs.append(score['metrics'][method])
        complete = batch_complete and len(values) == total and all(value['complete'] for value in values)
        counts = {name: sum(value['decisions'][name] for value in values)
                  for name in ('correct', 'reviewed', 'citation_supported', 'citation_reviewed')}
        def ratio(top, bottom):
            return top / bottom if complete and bottom else None
        coverage = {}
        for kind in ('decision', 'uncertainty'):
            count = sum(value['reference_coverage'][kind]['total'] for value in values)
            full = sum(value['reference_coverage'][kind]['full'] for value in values)
            coverage[kind] = {'full': full, 'total': count, 'coverage': ratio(full, count)}
        summary['methods'][method] = {'complete': complete, 'scored_cases': len(values),
            'precision': ratio(counts['correct'], counts['reviewed']),
            'citation_support': ratio(counts['citation_supported'], counts['citation_reviewed']),
            'decision_coverage': coverage['decision']['coverage'],
            'uncertainty_coverage': coverage['uncertainty']['coverage'], 'reference_counts': coverage,
            'metrics': aggregate_metrics(costs) if costs else None, 'metrics_scope': 'scored_cases_only'}
    return summary


class RequirementFlow:
    def __init__(self, battle):
        self.battle = battle

    def inputs(self):
        requirement_id, nodes = read_requirement_manifest(self.battle.input_path('manifest.json'))
        return assemble_requirement(requirement_id, nodes), nodes

    def freeze(self):
        b = self.battle
        author = b.author_result()
        value = author['requirement']
        sample = requirement_sample(value, b.state['sample_id'])
        refs = validate_requirement_references(author['references'], sample)
        manifest = {'schema_version': 1, 'requirement_id': sample.sample_id, 'nodes': []}
        for node in value['nodes']:
            row = {'id': node['id'], 'stage': node['stage']}
            for field in ('dialogue', 'artifact'):
                name = f'nodes/{node["id"]}/{field}.md'
                b.put_text(f'inputs/{sample.sample_id}/' + name, node[field])
                row[field] = name
            manifest['nodes'].append(row)
        b.put_json(f'inputs/{sample.sample_id}/manifest.json', manifest)
        b.put_json('scoring/references.json', refs)
        policy = reference_policy(refs)
        b.put_json('scoring/reference-policy.json', policy)
        saved, _ = self.inputs()
        if saved.fingerprint != sample.fingerprint:
            raise ValueError('员工正文与保存后的需求输入不一致')
        b.put_json('frozen.json', {'task_id': b.state['task_id'], 'sample_id': sample.sample_id,
                                  'stage': 'requirement', 'round': b.state['round'],
                                  'input_fingerprint': sample.fingerprint, 'file_hashes': sample.file_hashes,
                                  'references_digest': digest(refs), 'reference_policy_digest': digest(policy),
                                  'review_kind': 'agent', 'human_review': 'pending'})
        b.state['frozen'] = True
        b.save()

    def payload(self, role):
        b = self.battle
        if role == 'reviewer':
            author = b.author_result()
            return {'requirement': author['requirement'], 'references': author['references'],
                    'brief': b.state['brief'], 'review_clarification': b.state['clarification']}
        bundle = read_json(b.path(b.state['prediction']))
        sample, nodes = self.inputs()
        validate_comparison(bundle, sample, nodes, b.path(b.state['prediction']).parent)
        variants = {label: {**copy.deepcopy(view), 'view_digest': bundle['view_digests'][label]}
                    for label, view in bundle['prediction']['variants'].items()}
        return {'requirement': b.author_result()['requirement'],
                'references': read_json(b.path('scoring/references.json')),
                'reference_policy': read_json(b.path('scoring/reference-policy.json')),
                'prediction': {'sample_id': sample.sample_id, 'variants': variants},
                'prediction_digest': digest(bundle)}

    def worksheets(self, bundle):
        b = self.battle
        refs = read_json(b.path('scoring/references.json'))
        policy = read_json(b.path('scoring/reference-policy.json'))
        return {'kind': 'comparison-worksheets', 'schema_version': 1, 'bundle_digest': digest(bundle),
                'view_digests': bundle['view_digests'], 'worksheets': {
                    label: make_worksheet(view, refs, 'agent', schema_version=2, reference_policy=policy)
                    for label, view in bundle['prediction']['variants'].items()}}

    def score(self, judgments):
        b = self.battle
        bundle = read_json(b.path(b.state['prediction']))
        original = read_json(b.path('scoring/worksheet.json'))
        if original != self.worksheets(bundle) or not isinstance(judgments, dict) or set(judgments) != {'A', 'B'}:
            raise ValueError('比较评分视图或匿名标签不匹配')
        reviewed = {}
        for label, worksheet in original['worksheets'].items():
            value = judgments[label]
            if not isinstance(value, dict) or set(value) != {'judgments', 'open_question_judgments', 'reference_judgments'}:
                raise ValueError('每个匿名方法需要三组完整 v2 判定')
            reviewed[label] = apply_review_v2(worksheet, **value)
        value = {'bundle_digest': digest(bundle), 'worksheets': reviewed}
        score = self.scores(bundle, value)
        b.put_json('scoring/review.json', value)
        b.put_json('scoring/score.json', score)
        return score

    @staticmethod
    def scores(bundle, reviewed):
        if reviewed.get('bundle_digest') != digest(bundle) or set(reviewed.get('worksheets', {})) != {'A', 'B'}:
            raise ValueError('评分与原始比较包未绑定')
        values = {label: score_worksheet_v2(worksheet) for label, worksheet in reviewed['worksheets'].items()}
        return {'schema_version': 2, 'kind': 'requirement-comparison', 'methods': bundle['labels'],
                'variants': values, 'metrics': bundle['metrics'], 'complete': all(v['complete'] for v in values.values()),
                'human_review': 'pending', 'official_score': None}

    def audit(self, frozen):
        b = self.battle
        if b.state.get('score') and not b.state.get('prediction'):
            raise ValueError('没有真实预测不能存在整需求评分')
        sample, nodes = self.inputs()
        refs = read_json(b.path('scoring/references.json'))
        validate_requirement_references(refs, sample)
        policy = read_json(b.path('scoring/reference-policy.json'))
        if (sample.fingerprint != frozen['input_fingerprint'] or sample.file_hashes != frozen['file_hashes']
                or digest(refs) != frozen['references_digest'] or policy != reference_policy(refs)
                or digest(policy) != frozen['reference_policy_digest'] or frozen['round'] != b.state['round']):
            raise ValueError('冻结需求、参考或分类策略不匹配')
        review = read_json(b.path(f'operations/r{b.state["round"]:02d}-reviewer/result.json'))
        if review['metadata']['status'] != 'passed':
            raise ValueError('整需求未通过审题')
        if b.state.get('prediction'):
            bundle = read_json(b.path(b.state['prediction']))
            validate_comparison(bundle, sample, nodes, b.path(b.state['prediction']).parent)
            expected = self.worksheets(bundle)
            for op in b.state['operations'].values():
                if op['identity']['role'] == 'scorer':
                    payload = read_json(b.path(f'operations/{op["key"]}/payload.json'))
                    if (op['identity']['prediction_digest'] != digest(bundle)
                            or payload != self.payload('scorer')):
                        raise ValueError('员工评分任务与原始比较包不匹配')
            if read_json(b.path('scoring/worksheet.json')) != expected:
                raise ValueError('原始比较表与预测不一致')
            if b.state.get('score'):
                reviewed = read_json(b.path('scoring/review.json'))
                for label, worksheet in reviewed['worksheets'].items():
                    # Restore only judgment fields to compare all immutable worksheet data.
                    fixed = {key: value for key, value in worksheet.items() if key not in
                             ('judgments', 'open_question_judgments', 'reference_judgments')}
                    original = {key: value for key, value in expected['worksheets'][label].items() if key not in
                                ('judgments', 'open_question_judgments', 'reference_judgments')}
                    if fixed != original:
                        raise ValueError('已评分的原始视图或参考被改动')
                if self.scores(bundle, reviewed) != b.state['score']:
                    raise ValueError('整需求评分无法重算')
