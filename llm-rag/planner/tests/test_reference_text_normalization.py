"""Label-free surface spelling remains ontology bounded and preserves raw input."""
import pytest
from test_grounding_semantic_contracts import make_api, FIRST
from planner_schemas.intent import EntityReference
from semantic_labels import normalize_reference_label


def test_specific_compound_ontology_label_wins_over_supported_head_noun():
    assert normalize_reference_label(
        "signs", ["sign", "exitsign"], "object", "exit sign"
    ) == "exitsign"


def test_generic_object_label_is_not_overridden_without_specific_compound_match():
    assert normalize_reference_label(
        "signs", ["sign", "exitsign"], "object", "room with a sign"
    ) == "sign"


def test_phrase_specificity_does_not_override_a_contradictory_object_class():
    assert normalize_reference_label(
        "patient monitor", ["bed", "patientmonitor"], "object", "bed"
    ) is None


@pytest.mark.parametrize('text', ['the bed', 'a bed', 'an bed', '  THE bed  '])
@pytest.mark.parametrize('quantifier', ['one', 'any', 'all', 'nearest'])
def test_label_free_articles_do_not_hide_known_class(text, quantifier):
    reference = EntityReference(text=text, node_type='object', quantifier=quantifier, position=[0, 0, 0])
    original = reference.model_dump()
    result = make_api().resolve_entity(reference)
    assert result.status == ('resolved_set' if quantifier == 'all' else 'resolved')
    selected = result.selected_many[0] if quantifier == 'all' else result.selected
    assert selected.node_id == FIRST
    assert reference.model_dump() == original


@pytest.mark.parametrize('text', ['the television', 'a screen', 'the bed beside television'])
def test_article_stripping_does_not_enable_unknown_or_partial_class(text):
    assert make_api().resolve_entity(EntityReference(text=text, node_type='object')).status == 'not_found'
