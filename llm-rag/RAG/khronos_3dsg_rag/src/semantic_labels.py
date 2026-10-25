"""Shared exact-first ontology contract; no fuzzy matching or scene IDs."""
LABEL_CONTRACT_VERSION = 'ontology-surface-safe-synonyms-v1'


def label_forms(label: str) -> set[str]:
    compact = ''.join(label.lower().replace('_', ' ').split())
    forms = {compact}
    if compact.endswith('ies') and len(compact) > 3:
        forms.add(compact[:-3]+'y')
    elif compact.endswith(('ches','shes','xes','zes','sses')):
        forms.add(compact[:-2])
    elif compact.endswith('s') and not compact.endswith(('ss','us','is')):
        forms.add(compact[:-1])
    return forms


def normalize_label(value: str | None, ontology, node_type: str | None = None) -> str | None:
    if not value:
        return None
    labels = list(ontology)
    exact = [label for label in labels if label.lower() == value.lower().strip()]
    if len(exact) == 1:
        return exact[0]
    forms = label_forms(value)
    if node_type:
        kind = node_type.lower().strip()
        singular = kind[:-1] if kind.endswith('s') else kind
        if value.lower().strip() in (singular, singular+'s'):
            return singular
    matches = [label for label in labels if forms & label_forms(label)]
    if matches:
        return matches[0] if len(matches) == 1 else None
    # Exact phrase groups only; never substring class invention. All present
    # category alternatives participate, so seat cannot hide a sofa collision.
    groups = [
        {'nightstand','bedsidetable'},
        {'healthmonitor','patientmonitor'},
    ]
    for group in groups:
        if forms & group:
            candidates = [label for label in labels if label_forms(label) & group]
            return candidates[0] if len(candidates) == 1 else None
    if forms & {'seat'}:
        seating = {'seat','chair','sofa','bench','stool','armchair'}
        candidates = [label for label in labels if label_forms(label) & seating]
        return candidates[0] if len(candidates) == 1 else None
    return None


def normalize_reference_label(value, ontology, node_type, text):
    """Do not repair a noncanonical label unsupported by its original text.

    Exact model labels retain the pre-existing semantic-parser contract; this
    guard prevents surface repair from rescuing a contradictory class guess.
    """
    import re
    mapped = normalize_label(value, ontology, node_type)
    # Relation target text is usually the exact object-category phrase. Prefer
    # that complete ontology phrase over a model label that retained only its
    # head noun (e.g. "signs" for "exit sign"). Do not mine arbitrary longer
    # descriptions, where several object categories may be mentioned.
    if (node_type or '').lower().rstrip('s') == 'object' and text and mapped is not None:
        phrase = re.sub(r'^\s*(?:a|an|the)\s+', '', text.strip(), flags=re.I)
        phrase_label = normalize_label(phrase, ontology, node_type)
        text_tokens = re.findall(r'[a-z0-9]+', phrase.lower().replace('_', ' '))
        head_is_supported = any(normalize_label(token, ontology, node_type) == mapped
                                for token in text_tokens)
        if phrase_label is not None and head_is_supported:
            mapped = phrase_label
    exact = value and any(value.lower() == label.lower() for label in ontology)
    kind = (node_type or '').lower().rstrip('s')
    generic = value and kind and value.lower().strip() in (kind, kind+'s')
    explicit = re.fullmatch(r'\s*(?:(?:place|room)\s+(?:id\s+)?)?\d{1,20}\s*', text or '', re.I)
    if mapped is None or exact or generic or explicit or not text:
        return mapped
    tokens = re.findall(r'[a-z0-9]+', text.lower().replace('_',' '))
    supported = any(normalize_label(' '.join(tokens[start:start+length]), ontology) == mapped
                    for start in range(len(tokens)) for length in range(1,5))
    return mapped if supported else None
