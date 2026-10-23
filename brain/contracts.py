"""What a contract move costs this Shikigon: one answer for its head and for its rules.

The world books what a contract says, and not only through its escrow: a tribute takes its
``amount_per_tick`` from its payer every tick, an alliance its ``cost_per_tick`` from both sides,
and a mercenary takes up to five mega bombs from the agent that hires him once he accepts (cos20
``core/contract_manager.py``: ``_process_tribute``, ``_process_alliance_cost``,
``_equip_mercenary_bombs``). Until 0.1.13 the learning budget read only the escrow and a payer
written exactly like the agent's own id, and the rule tree accepted an offer by its kind alone
(``cosmergon-decider-tree`` 2.3.10: a warrior, trader, diplomat or expansionist any tribute, the
one it would pay too). Another player could offer a Shikigon a tribute it pays, and it paid him
every tick (cos20 #492, ninth review). The server leaves that decision to the agent (S246, F6),
so the Shikigon makes it here: a contract costs nothing only where its kind and terms say so,
anything else costs.
"""

from __future__ import annotations

import uuid
from typing import Any

import files

CONTRACT_MOVES = frozenset({"propose_contract", "accept_contract", "propose_counter",
                            "propose_from_template"})  # fmt: skip
# Kinds the world books nothing for while no escrow is pledged (cos20 ``models/contract.py``;
# ``custom`` is kept on honor).
FREE_KINDS = frozenset({"non_aggression", "trade_agreement", "alliance_public", "custom"})
# Kinds whose reward goes to the one who accepts: free to accept, a cost to offer.
PAID_TO_ACCEPTER = frozenset({"mercenary", "conditional"})
# The rule tree's templates (2.3.10, ``_resolve_propose_from_template``) by the kind they render to.
TEMPLATES = {"T07_TRADE_AGREEMENT": "trade_agreement", "T08_NON_AGGRESSION": "non_aggression"}
# The names the world reads a contract move's escrow under (cos20 ``core/actions/formen.py``):
# ``escrow_amount`` for propose_contract and accept_contract, ``escrow`` for propose_from_template
# and propose_counter. Until 0.1.13 only the first was read, and a template the head wrote with an
# escrow passed as costing nothing (cos20 #492, tenth review).
ESCROW_FIELDS = ("escrow_amount", "escrow")


def same_id(one: Any, other: Any) -> bool:
    """Two ids as the server reads them: a UUID in any spelling it takes (upper case, without
    hyphens, in braces, ``urn:uuid:``) is one id. Until 0.1.13 they were compared as text."""

    def plain(value: Any) -> str:
        try:
            return str(uuid.UUID(str(value)))
        except ValueError:
            return str(value)

    return plain(one) == plain(other)


def _view(params: dict[str, Any]) -> dict[str, Any]:
    """The fields of a move as it goes out: those at the top, the ones under ``params`` filling
    in what the top does not name (the rule tree sends a template there)."""
    inner = params.get("params")
    top = {key: value for key, value in params.items() if key != "params"}
    return {**(inner if isinstance(inner, dict) else {}), **top}


def _offer(state: Any, contract_id: Any) -> dict[str, Any] | None:
    """The contract offered to this agent with that id, as the world lists it; ``None`` if none."""
    for offer in tuple(getattr(state, "pending_contracts", None) or ()):
        if isinstance(offer, dict) and same_id(offer.get("contract_id"), contract_id):
            return offer
    return None


def _kind_costs(kind: str, terms: dict[str, Any], accepting: bool, me: str) -> bool:
    if kind in FREE_KINDS:
        return False
    if kind == "alliance":
        cost = files.finite(terms.get("cost_per_tick") or 0)
        return cost is None or cost != 0
    if kind == "tribute":
        payer = terms.get("payer_id")
        return not me or payer is None or same_id(payer, me)
    if kind in PAID_TO_ACCEPTER:
        return not accepting
    return True


def costs_me(action: str, params: dict[str, Any], state: Any) -> bool:
    """True if this contract move costs this agent anything, now or later: its escrow under any
    name the world reads it by (``ESCROW_FIELDS``), a tribute it pays, the costs of an alliance,
    the reward and bombs of a mercenary it hires, a kind the brain does not know. ``False`` for any
    move that is no contract move."""
    if action not in CONTRACT_MOVES:
        return False
    view = _view(params)
    for name in ESCROW_FIELDS:
        escrow = files.finite(view.get(name) or 0)
        if escrow is None or escrow != 0:
            return True
    me = str(getattr(state, "agent_id", "") or "")
    if action == "accept_contract":
        offer = _offer(state, view.get("contract_id"))
        if offer is None:
            return True
        kind, terms, accepting = offer.get("contract_type"), offer.get("terms"), True
    elif action == "propose_contract":
        kind, terms, accepting = view.get("contract_type"), view.get("terms"), False
    elif action == "propose_from_template":
        kind, terms, accepting = TEMPLATES.get(str(view.get("template_id"))), {}, False
    else:  # a counter offer: terms of its own, read like none
        return True
    return _kind_costs(str(kind or ""), terms if isinstance(terms, dict) else {}, accepting, me)


class Careful:
    """The rule tree, saying no where a contract would cost this Shikigon (``costs_me``).

    An offer that costs it is rejected instead of accepted; a proposal that costs it is left out
    and the tree asked again. The built-in deciders get their tree only through here
    (``make_decider``, ``right_now``)."""

    def __init__(self, tree: Any) -> None:
        self.tree = tree
        self.name = str(getattr(tree, "name", "tree"))
        self.version = str(getattr(tree, "version", ""))

    async def decide(
        self, state: Any, blocked: frozenset[str] = frozenset()
    ) -> tuple[str, dict[str, Any]]:
        choice: tuple[str, dict[str, Any]] = await self.tree.decide(state, blocked=blocked)
        action, params = choice
        if not costs_me(action, params, state):
            return choice
        if action == "accept_contract" and "reject_contract" not in blocked:
            return "reject_contract", {"contract_id": str(_view(params).get("contract_id"))}
        if action in blocked:  # a tree that does not keep to the list: nothing that costs goes out
            return "wait", {}
        return await self.decide(state, blocked | {action})  # each contract move once at most

    async def healthcheck(self) -> bool:
        return bool(await self.tree.healthcheck())
