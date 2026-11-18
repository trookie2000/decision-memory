"""Describe stage, directory and manifest inputs without changing source IDs.

Nodes express known order. Supplementary documents have no inferred node or date;
speaker markers never promote them to confirmed stage dialogue.
"""
from .requirements import MAX_NODES, MAX_REQUIREMENT_BYTES, RequirementSample
from .sources import DOCUMENT_TYPES, REQUIREMENT

POLICY = 'typed-input-v1'


def describe_input(sample):
    """Return JSON metadata shared by model packets, results and CLI inspection."""
    whole = sample.stage == REQUIREMENT
    if isinstance(sample, RequirementSample):
        nodes = [dict(node) for node in sample.nodes]
        locations = {key: {'node_id': nodes[sample.node_order(key)]['id'] if sample.node_order(key) is not None else None,
                           'node_order': sample.node_order(key)} for key in sample.catalog}
    elif whole:
        nodes = [{'id': str(number), 'stage': sample.node_stages[number], 'order': order,
                  'files': list(dict.fromkeys(source.file for source in sample.catalog.values()
                                              if source.node == number))}
                 for order, number in enumerate(sorted(sample.node_stages))]
        by_number = {number: node for number, node in zip(sorted(sample.node_stages), nodes)}
        locations = {}
        for key, source in sample.catalog.items():
            if source.document_type == 'supplement':
                locations[key] = {'node_id': None, 'node_order': None}
            elif source.node not in by_number:
                raise ValueError('阶段材料缺少明确节点，不能根据来源编号推断顺序：' + key)
            else:
                node = by_number[source.node]
                locations[key] = {'node_id': node['id'], 'node_order': node['order']}
    else:
        nodes = []
        locations = {key: {'node_id': sample.stage, 'node_order': 0} for key in sample.catalog}
    if whole and (len(nodes) > MAX_NODES or sample.input_bytes > MAX_REQUIREMENT_BYTES):
        raise ValueError('整个需求最多 64 个节点、8 MB；不截断材料')
    stages = {node['id']: node['stage'] for node in nodes}
    documents = {}
    for key, source in sample.catalog.items():
        if source.document_type not in DOCUMENT_TYPES:
            raise ValueError('未知材料类型：' + str(source.document_type))
        location = locations[key]
        identity = source.file, location['node_id']
        document = {'file': source.file, 'document_type': source.document_type, **location,
                    'stage': stages.get(location['node_id']) if whole else sample.stage}
        if identity in documents and documents[identity] != document:
            raise ValueError('同一文件的材料类型或节点不一致：' + source.file)
        documents[identity] = document
    source_format = 'manifest' if isinstance(sample, RequirementSample) else 'directory' if whole else 'stage'
    return {'policy': POLICY, 'source_format': source_format, 'input_scope': REQUIREMENT if whole else 'stage',
            'nodes': nodes, 'documents': list(documents.values()), 'source_nodes': locations}
