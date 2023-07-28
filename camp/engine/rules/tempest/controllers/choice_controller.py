from __future__ import annotations

from abc import abstractmethod
from functools import cached_property
from typing import Literal
from typing import cast

from camp.engine.rules import base_engine
from camp.engine.rules.base_models import Discount
from camp.engine.rules.base_models import PropExpression

from ...decision import Decision
from .. import defs

# TODO: When too many choices have been taken (usually because the character has lost points in the feature),
# the player should be prompted to remove choices.


class ChoiceController(base_engine.ChoiceController):
    _feature: base_engine.BaseFeatureController
    _choice: str

    def __init__(self, feature: base_engine.BaseFeatureController, choice_id: str):
        self._feature = feature
        self._choice = choice_id

    @cached_property
    def definition(self) -> defs.ChoiceDef:
        return self._feature.definition.choices[self._choice]

    @cached_property
    def controller_data(self) -> dict:
        return self.definition.controller_data or {}

    @property
    def id(self) -> str:
        return self._choice

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def description(self) -> str:
        return self.definition.description

    @property
    def limit(self) -> int | Literal["unlimited"]:
        if isinstance(self.definition.limit, int) and self.definition.limit_is_per_rank:
            return self.definition.limit * self._feature.value
        return self.definition.limit

    @property
    def multi(self) -> bool:
        return self.definition.multi

    @property
    def choices_remaining(self) -> int:
        if self.limit == "unlimited":
            return 999
        return self.limit - sum(self.choice_ranks().values())

    def taken_choices(self) -> dict[str, str]:
        taken = {}
        if self._feature.model.choices:
            if choices := self._feature.model.choices.get(self._choice):
                for choice in choices:
                    expr = PropExpression.parse(choice)
                    taken[expr.full_id] = self.describe_choice(choice)
        return taken

    def choose(self, choice: str) -> Decision:
        choice_ranks = self.choice_ranks()
        if not self.multi and choice in choice_ranks:
            return Decision(success=False, reason="Choice already taken.")

        if self.limit != "unlimited" and sum(choice_ranks.values()) >= self.limit:
            return Decision(
                success=False,
                reason=f"Choice {self._choice} of {self._feature.full_id} only accepts {self.limit} choices.",
            )

        self._record_choice(choice)
        return Decision(success=True, mutation_applied=True, reason="Choice applied.")

    def unchoose(self, choice: str) -> Decision:
        choice_ranks = self.choice_ranks()
        if choice not in choice_ranks:
            return Decision(success=False, reason="Choice not taken.")

        choices = self._feature.model.choices.get(self._choice) or []
        if choice_ranks[choice] <= 1:
            choices.remove(choice)
        else:
            expr = PropExpression.parse(choice)
            expr.value = choice_ranks[choice]
            choices.remove(repr(expr))
            expr.value -= 1
            choices.append(repr(expr))

        self._feature.model.choices[self._choice] = choices
        self._feature.reconcile()
        return Decision(success=True, mutation_applied=True, reason="Choice removed.")

    def _record_choice(self, choice: str) -> None:
        choice_ranks = self.choice_ranks()
        if model_choices := self._feature.model.choices:
            choices = model_choices.get(self._choice) or []
        else:
            choices = []

        if choice in choice_ranks:
            # Already taken. If this is a multi-choice, we need to increment the value.
            if self.definition.multi:
                expr = PropExpression.parse(choice)
                expr.value = choice_ranks[choice]
                choices.remove(repr(expr))
                expr.value += 1
                choices.append(repr(expr))
            else:
                # Can't take a choice more than once!
                return Decision(success=False, reason="Choice already taken.")
        else:
            # Not yet taken. Add it to the list.
            choices.append(choice)

        if self._feature.model.choices is None:
            self._feature.model.choices = {}
        self._feature.model.choices[self._choice] = choices
        self._feature.reconcile()

    def choice_ranks(self) -> dict[str, int]:
        ranks = {}
        if self._feature.model.choices:
            if choices := self._feature.model.choices.get(self._choice):
                for choice in choices:
                    expr = PropExpression.parse(choice)
                    ranks[expr.full_id] = expr.value or 1
        return ranks

    def describe_choice(self, choice: str) -> str:
        expr = PropExpression.parse(choice)
        character = self._feature.character
        if expr.prefixes:
            display_name = " ".join(
                character.display_name(p) for p in expr.prefixes
            ) + character.display_name(expr.prop)
        else:
            display_name = self._feature.character.display_name(expr.prop)
        if expr.option:
            display_name += f" [{expr.option}]"
        if expr.value and expr.value > 1:
            display_name += f" x{expr.value}"
        return display_name

    @abstractmethod
    def update_propagation(
        self, grants: dict[str, int], discounts: dict[str, list[Discount]]
    ) -> None:
        """Apply the consequences of the provided choices."""


class BaseFeatureChoice(ChoiceController):
    _feature: base_engine.BaseFeatureController
    _choice: str

    def __init__(self, feature: base_engine.BaseFeatureController, choice_id: str):
        self._feature = feature
        self._choice = choice_id

    @cached_property
    def definition(self) -> defs.ChoiceDef:
        return cast(defs.BaseFeatureDef, self._feature.definition).choices[self._choice]

    @property
    def id(self) -> str:
        return self._choice

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def description(self) -> str:
        return self.definition.description

    @property
    def limit(self) -> int | Literal["unlimited"]:
        if isinstance(self.definition.limit, int) and self.definition.limit_is_per_rank:
            return self.definition.limit * self._feature.value
        return self.definition.limit

    def choose(self, choice: str) -> Decision:
        if not self._matches(choice):
            return Decision(
                success=False,
                reason=f"`{choice}` does not match choice definition for {self._feature.full_id}/{self._choice}",
            )
        return super().choose(choice)

    def available_choices(self) -> dict[str, str]:
        # Already taken too many?
        if self.choices_remaining <= 0:
            return {}
        character = self._feature.character
        feats = self._matching_features()
        choices = {}
        for expr in sorted(feats):
            choices[expr] = character.display_name(expr)
        return choices

    def _matching_features(self) -> set[str]:
        return {
            choice
            for choice in self._feature.character.ruleset.features
            if self._matches(choice)
        }

    def _matches(self, choice: str) -> bool:
        if feat := self._feature.character.feature_def(choice):
            return self.definition.matcher.matches(feat)
        return False

    def removable_choices(self) -> set[str]:
        # TODO: Prevent choices from being removed when the character is not in "free edit" mode.
        # There may be other circumstances when a choice can or can't be removed.
        # TODO: Allow choices to be removed when the character has more choices than they are allowed.
        return self.taken_choices()


class GrantChoice(BaseFeatureChoice):
    show_description: bool = True

    def available_choices(self) -> dict[str, str]:
        # Already taken too many?
        if self.choices_remaining <= 0:
            return {}

        feats = self._matching_features()
        feats -= set(self.taken_choices().keys())

        choices = {}
        for expr in sorted(feats):
            feat = self._feature.character.feature_controller(expr)
            short = feat.short_description
            descr = getattr(feat, "formal_name", feat.display_name())
            if self.show_description:
                if not feat.possible_ranks:
                    descr = f"{descr} (Already at Max)"
                elif short:
                    descr = f"{descr}: {short}"

            choices[expr] = descr
        return choices

    def update_propagation(
        self, grants: dict[str, int], discounts: dict[str, list[Discount]]
    ) -> None:
        for choice in self.taken_choices():
            if choice not in grants:
                grants[choice] = 0
            grants[choice] += 1


class SameTagChoice(GrantChoice):
    """Used only by the Artisan power Studied Focus.

    Allows the character to choose multiple powers to be granted, but they must all share a tag.
    """

    @property
    def tags(self) -> set[str]:
        selections = list(self.taken_choices().keys())
        if not selections:
            return set()
        ruleset = self._feature.character.ruleset
        tags = ruleset.features.get(selections.pop()).tags
        for selection in selections:
            if feature_def := ruleset.features.get(selection):
                tags = tags & feature_def.tags
        return tags

    def _matching_features(self) -> set[str]:
        ruleset = self._feature.character.ruleset
        features = super()._matching_features()
        if tags := self.tags:
            return {feat for feat in features if tags & ruleset.features[feat].tags}
        return features


class PracticedCraftChoice(GrantChoice):
    def _matching_features(self) -> set[str]:
        character = self._feature.character
        if req := self.definition.controller_data.get("requires"):
            if not character.meets_requirements(req):
                return set()

        choice_reqs: dict[str, str]
        if choice_reqs := self.definition.controller_data.get("choice-requires"):
            features = super()._matching_features()
            available_feats = set()
            for feat in features:
                if (req := choice_reqs.get(feat)) and character.meets_requirements(req):
                    available_feats.add(feat)
            return available_feats

        return set()


class AccessibleClassPowerChoice(GrantChoice):
    def _matches(self, choice: str) -> bool:
        character = self._feature.character

        if not super()._matches(choice):
            return False
        # Powers granted by this controller must be:
        # 1. Of a class and tier that the character normally has access to.
        # 2. Not a power that the character already has.
        feat = self._feature.character.feature_controller(choice)
        if feat.value > 0:
            return False

        if not (parent := feat.parent) or parent.feature_type != "class":
            # Don't grant anything that didn't come from a class, for now.
            return False

        if parent.value == 0:
            # ...and only if the character has taken the class.
            return False

        # TODO: Add a generic "the character could take this power" check to features.
        if feat.feature_type == "power" and (tier := getattr(feat, "tier", None)):
            # If the power has a tier, it must be one that the character has access to in its parent class.
            if character.get(f"{parent.full_id}.powers@{tier}") <= 0:
                return False
        elif feat.feature_type == "utility":
            if character.get(f"{parent.full_id}.utilities") <= 0:
                return False
        else:
            # Don't grant anything that isn't a power or utility.
            return False

        return True


class AgileLearnerChoice(BaseFeatureChoice):
    """Used by the Agile Learner skill.

    In conjunction with the specified matcher in the skill def, this presents only
    basic classes that the character has levels in. Additionally, the class must
    currently have at least one tier-1 slot to trade away - you can't take a single
    level in a class and then apply all three ranks of Agile Learner to it.

    This will dock the character one tier-1 slot from the chosen class, then grant
    one tier-2 slot in that same class. If a spell slot was swapped, the character
    should also be able to swap out a known spell.
    """

    # TODO: Once characters are locked down, add a "swap out a known spell" token.

    def _matches(self, choice: str) -> bool:
        if not super()._matches(choice):
            return False

        character = self._feature.character
        feat = character.feature_controller(choice)

        # You have to have levels in it already.
        if feat.value <= 0:
            return False

        # Check for a novice spell or basic power to trade away.
        if feat.caster:
            if character.get(f"{feat.full_id}.spell_slots@1") <= 0:
                return False
        elif character.get(f"{feat.full_id}.powers@1") <= 0:
            return False

        return True

    def update_propagation(
        self, grants: dict[str, int], discounts: dict[str, list[Discount]]
    ) -> None:
        character = self._feature.character
        for choice, ranks in self.choice_ranks().items():
            feat = character.feature_controller(choice)
            if feat.caster:
                grants[f"{choice}.spell_slots@1"] = -ranks
                grants[f"{choice}.spell_slots@2"] = ranks
            else:
                grants[f"{choice}.powers@1"] = -ranks
                grants[f"{choice}.powers@2"] = ranks


class OptionBonusRouter(GrantChoice):
    """Special choice controller used only by option features.

    A few classes grant a bonus to a template option ID. For example, Socialite
    and Mage grant "Lore". Not "Lore [Arcana]", just "Lore", with the intent that
    the player will choose a Lore to receive. We could add a choice controller to
    every feature that grants something like this, but then we'd have to port the
    freeform option logic (Lore [Sailboats] is a perfectly valid lore to choose).

    That's plausible and it might be desirable in the future, but for now, we have this.
    When an option skill has a bonus, it presents this choice controller to allow
    one of its existing option instances to be chosen to receive the bonus. Additionally,
    the option template presents itself as having a free purchase available. The purchase
    logic notes whether the feature has "unspent bonus" left, and if so, redirects the
    purchase to making a choice in this controller. This allows the option template's
    presentation of options and freeform field to be hijacked for the purpose of selecting
    a bonus Lore or whatever.
    """

    show_description: bool = False

    def _matching_features(self) -> set[str]:
        return {c.full_id for c in self._feature.option_controllers.values()}

    def _matches(self, choice: str) -> bool:
        expr = PropExpression.parse(choice)
        return expr.prop == self._feature.id and expr.option

    @property
    def name(self) -> str:
        return f"Bonus {self._feature.display_name()}"

    @property
    def description(self) -> str:
        return f"You have received a bonus rank of {self._feature.display_name()}. Choose an existing option to apply it to."

    @property
    def limit(self) -> int:
        return self._feature.bonus

    @property
    def multi(self) -> bool:
        return self._feature.option_def.multiple

    def update_propagation(
        self, grants: dict[str, int], discounts: dict[str, list[Discount]]
    ) -> None:
        if not self.multi:
            # This skill only gets a single option ever, so all bonuses should be directed
            # to it instead of just the one that the GrantChoice would normally give.
            if choices := self.taken_choices().keys():
                grants[list(choices)[0]] = self._feature.granted_ranks
        else:
            super().update_propagation(grants, discounts)


def make_controller(
    feature: base_engine.BaseFeatureController, choice_id: str
) -> ChoiceController:
    """Factory function for custom choice controllers."""
    choice_def = cast(defs.BaseFeatureDef, feature.definition).choices[choice_id]
    match choice_def.controller:
        case "sphere-grant":
            from . import sphere_choice

            return sphere_choice.SphereGrantChoice(feature, choice_id)
        case "sphere-bonus":
            from . import sphere_choice

            return sphere_choice.SphereBonusChoice(feature, choice_id)
        case "patron":
            from . import patron_choice

            return patron_choice.PatronChoice(feature, choice_id)
        case "same-tag":
            return SameTagChoice(feature, choice_id)
        case "practiced-craft":
            return PracticedCraftChoice(feature, choice_id)
        case "accessible-powers":
            return AccessibleClassPowerChoice(feature, choice_id)
        case "agile-learner":
            return AgileLearnerChoice(feature, choice_id)
        case None:
            return GrantChoice(feature, choice_id)
        case _:
            raise ValueError(f"Unknown choice controller '{choice_def.controller}'")
