"""Whole-requirement employee returns; references stay outside extraction."""
from .evaluation import digest, identifiers
from .requirements import assemble_requirement
from .sources import MAX_FILE_BYTES, index_materials


def requirement_sample(value, sample_id=None):
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'requirement_id', 'nodes'}
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or (sample_id is not None and value.get('requirement_id') != sample_id)
            or not isinstance(value.get('nodes'), list) or not 1 <= len(value['nodes']) <= 64):
        raise ValueError('requirement.json 需要匹配的身份和 1～64 个有序节点')
    nodes, total = [], 0
    for node in value['nodes']:
        if (not isinstance(node, dict) or set(node) != {'id', 'stage', 'dialogue', 'artifact'}
                or any(not isinstance(node[key], str) for key in ('id', 'stage', 'dialogue', 'artifact'))):
            raise ValueError('节点需要 id、stage 和原始 dialogue/artifact 正文')
        sizes = [len(node[key].encode('utf-8')) for key in ('dialogue', 'artifact')]
        total += sum(sizes)
        if any(size > MAX_FILE_BYTES for size in sizes) or total > 8_000_000:
            raise ValueError('单份材料最多 2 MB，整个需求最多 8 MB')
        sample = index_materials(node['dialogue'], node['artifact'], node['stage'], node['id'],
                                 f'nodes/{node["id"]}/dialogue.md', f'nodes/{node["id"]}/artifact.md')
        nodes.append((node['id'], node['stage'], sample))
    return assemble_requirement(value['requirement_id'], nodes)


def validate_requirement_references(references, sample):
    if not isinstance(references, list) or not references:
        raise ValueError('整需求参考必须是非空 JSON 数组')
    identifiers(references, 'id')
    node_ids = {node['id'] for node in sample.nodes}
    for ref in references:
        if (set(ref) != {'id', 'text', 'kind', 'evidence'} or ref.get('kind') not in ('decision', 'uncertainty')
                or not isinstance(ref.get('text'), str) or not ref['text'].strip()
                or not isinstance(ref.get('evidence'), list) or not ref['evidence']):
            raise ValueError('整需求参考需要 id/text/kind/evidence，kind 为 decision 或 uncertainty')
        for evidence in ref['evidence']:
            if (not isinstance(evidence, dict) or set(evidence) != {'node_id', 'source', 'quote'}
                    or not isinstance(evidence['node_id'], str) or evidence['node_id'] not in node_ids
                    or evidence['source'] not in ('dialogue', 'artifact')
                    or not isinstance(evidence['quote'], str) or not evidence['quote'].strip()):
                raise ValueError('参考引文需要有效 node_id、source 和逐字 quote')
            prefix = ('D' if evidence['source'] == 'dialogue' else 'A') + '.' + evidence['node_id'] + '.'
            if not any(key.startswith(prefix) and evidence['quote'] in source.text
                       for key, source in sample.catalog.items()):
                raise ValueError('参考引文不在指定节点和来源的同一原文行内')
    return references


def parse_requirement(value, references, sample_id):
    sample = requirement_sample(value, sample_id)
    return {'requirement': value, 'references': validate_requirement_references(references, sample)}


def reference_policy(references):
    return {'schema_version': 1, 'references_digest': digest(references), 'assignments': [
        {'reference_id': ref['id'], 'kind': ref['kind'],
         'rationale': '使用出题人给出且通过本轮审题的参考分类；按需求结束时的有效状态评分。'}
        for ref in references]}


def worker_instructions(role):
    if role == 'author':
        return '''你是真实出题员工。按 brief 编写完全虚构的已完成需求，包含 2～4 个有序节点。
节点可以是 analysis、design、coding 或 testing；每节点给自然的用户/AGENT 讨论和对应产物，
使用 **用户：** 和 **AGENT：** 标记，保留真实的提议、纠偏、确认与未决事项。
这是编题阶段，还没有调用被测 CLI；不要把答案、评分口吻或文件协议要求写进原始材料。
必须完整输出 ## requirement.json 与 ## gold.json 两个 JSON 代码块，另有公共协议要求的 result.json。
requirement.json 格式：{"schema_version":1,"requirement_id":"本任务 sample_id","nodes":[
{"id":"analysis","stage":"analysis","dialogue":"完整原始对话正文，JSON 内换行转义",
"artifact":"该节点最终产物正文"}, ...]}。id 使用字母数字、下划线或连字符且唯一。
gold.json 为数组，每条严格包含 id、text、kind、evidence。kind 为 decision 或 uncertainty。
decision 是整个需求结束后仍有效的原子知识；uncertainty 是仍未说明的字段或未决事项。
早期已被替换的决定、后来已解决的未知项不能作为最终参考。不同范围可以共存，不为凑数拆分。
evidence 每项严格为 {"node_id":"对应节点 id","source":"dialogue 或 artifact",
"quote":"指定节点、指定来源中同一行内连续的逐字原文"}；不要删改空格或标点。
通常 3～6 条 decision，另保留实际存在的 uncertainty；未知理由和边界不能自行补齐。
result.json 增加 status=completed。返修时依据 previous 与真实 review_feedback，返回完整新版材料和参考。
'''
    if role == 'reviewer':
        return '''你是真实评审员工。现在审查整个需求的题目和候选参考，不评分 CLI。
检查全部有序节点、跨节点的最终有效性、业务范围、理由/边界、重复、误合并、未决项和逐字来源。
独立检查每个 gold 的 decision/uncertainty 分类：只计需求结束时仍有效或仍未知的内容。
若有 review_clarification，核对源材料是否已经体现；未写入原文的条件不能当作已有事实。
不能替出题人改材料或 gold；需要修正则 status=needs_revision，说明真实依据和具体修改要求。
通过用 passed，有争议用 human_review；所有状态都必须有非空 feedback。正文列出逐项核查。
'''
    return '''你是真实评审员工。现在只评两个匿名原始预测视图，不替任何方法改答案。
原文、参考及 reference_policy 已在预测前固定，A/B 标签不表示优劣。先分别判断，再汇总。
prediction.variants 中每个标签含 items、open_questions 和不可修改的视图摘要。
## judgments.json 必须为对象，键恰为输入中的 A/B 标签，每个值严格包含三个数组：
judgments：每个 item_id 一项，包含 correct、citation_support、matches、notes；
open_question_judgments：按 open_questions 顺序给 Q001、Q002 等 question_id，字段同上；
reference_judgments：每个 reference_id 一项，包含 coverage、supported_by、notes。
correct/citation_support 取 true/false/null；未审保留 null。matches 只能填给定参考 ID。
coverage 取 full/partial/absent/null。full/partial 必须有正确且引用支持的原始输出，不预设通过。
supported_by 每项：{"target":"item","id":"K001","field":"conclusion/reason/boundary"}
或 {"target":"open_question","id":"Q001"}，field 对 item 要选实际字段，不能写斜杠合并值。
decision 参考只能由 item 支持；uncertainty 可由未说明 reason/boundary 或 open_question 支持，
不能把未知事项作为已确定 conclusion 计分。只在 matches 中提名参考不等于完整覆盖。
注意后续节点推翻旧决定、旧问题后来解决、重复条目和范围误合并。每个已审判定必须有依据说明。
只写 result.json、judgments.json 和简短解释，不回显原文、gold 或 prediction，不新增或修改答案。
result.json 增加 status=completed，保留公共协议全部身份字段和 prediction_digest。写后回读核对。
'''
