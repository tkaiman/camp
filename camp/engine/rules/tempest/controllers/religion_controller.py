from __future__ import annotations

from camp.engine.rules.decision import Decision

from .. import defs
from . import character_controller
from . import feature_controller


class ReligionController(feature_controller.FeatureController):
    definition: defs.Religion
    supports_child_purchases: bool = True

    def __init__(self, full_id: str, character: character_controller.TempestCharacter):
        super().__init__(full_id, character)
        if not isinstance(self.definition, defs.Religion):
            raise ValueError(
                f"Expected {full_id} to be a religion but was {type(self.definition)}"
            )

    def can_afford(self, value: int = 1) -> Decision:
        if self.character.religion:
            return Decision.NO
        return Decision.OK
