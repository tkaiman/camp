from camp.engine.rules.tempest.controllers.character_controller import TempestCharacter


def test_weird_wanderings(character: TempestCharacter):
    assert character.apply("artisan:2")
    assert character.apply("weird-wanderings")
    choices = character.feature_controller("weird-wanderings").choices
    assert choices
    choice_list = list(choices.values())[0].available_choices().keys()
    assert choice_list
    for c in choice_list:
        feature = character.feature_controller(c)
        assert feature.feature_type == "power"
        assert feature.parent != "artisan"
        assert feature.definition.tier == 1
        if refresh := feature.definition.refresh:
            assert "Spell" not in refresh
        if effect := feature.definition.effect:
            assert "Refresh" not in effect
