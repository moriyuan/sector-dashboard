"""Separate vendor namespaces; legacy Eastmoney archives remain immutable."""

def sector_kind(kind, concept_source='ths'):
    return 'concept_ths' if kind == 'concept' and concept_source == 'ths' else kind


def vendor_name(kind, concept_source='ths'):
    return '同花顺' if kind == 'concept' and concept_source == 'ths' else '东方财富'
