"""Feature capabilities of the PFAFF Creative machines this app supports.

The models differ in which memories and media they expose, and in how P-Memory
writes are framed.  Recording that in one registry keeps the differences out of
the UI and protocol code, where they used to be re-derived from model-name
string comparisons.
"""

from dataclasses import dataclass

# P-Memory write framing.  75xx machines take the header in its own frame and
# then the stitch data in chunks; the 1475 CD takes a single frame that carries
# both the header and the stitch data.
PMEMORY_VARIANT_75XX = "75xx"
PMEMORY_VARIANT_1475CD = "1475cd"


@dataclass(frozen=True)
class MachineModel:
    """One machine model and the features it supports.

    Args:
        name (str): Canonical name; stored in the config and shown in the UI.
        ident_markers (tuple[str, ...]): Substrings of the response to
            ``MachineComm.query_machine()`` that identify this model.
        p_memory (bool): P-Memory slots can be read and written.
        m_memory (bool): M-Memory slots are available.
        memory_cards (bool): Memory cards can be read and written.
        card_embroidery (bool): Embroidery (hoop) designs can be stored on a card.
        card_p_muster (bool): P-Muster (9 mm / MAXI) patterns can be stored on a card.
        high_speed (bool): The 10472 baud rate is supported.
        pmemory_variant (str): One of the ``PMEMORY_VARIANT_*`` constants.
    """

    name: str
    ident_markers: tuple
    p_memory: bool
    m_memory: bool
    memory_cards: bool
    card_embroidery: bool
    card_p_muster: bool
    high_speed: bool = True
    pmemory_variant: str = PMEMORY_VARIANT_75XX


# Registry.  The order matters twice: the first entry is the default model, and
# the first model whose ident marker matches wins during detection.  Keep the
# more specific markers (e.g. "7550 CD") ahead of generic ones when two models
# could share a substring.
MODELS = (
    MachineModel(
        name="PFAFF Creative 7570",
        ident_markers=("7570",),
        p_memory=True,
        m_memory=True,
        memory_cards=True,
        card_embroidery=True,
        card_p_muster=True,
    ),
    MachineModel(
        name="PFAFF Creative 7560",
        ident_markers=("7560",),
        p_memory=False,
        m_memory=True,
        memory_cards=True,
        card_embroidery=True,
        card_p_muster=False,
    ),
    MachineModel(
        name="PFAFF Creative 7550",
        # "7550 CD" is what the ident string and the original software use;
        # bare "7550" covers units that omit the CD suffix.
        ident_markers=("7550 CD", "7550"),
        p_memory=True,
        m_memory=True,
        memory_cards=False,
        card_embroidery=False,
        card_p_muster=False,
    ),
    MachineModel(
        name="PFAFF Creative 1475 CD",
        ident_markers=("1475 CD", "1475A"),
        p_memory=True,
        m_memory=True,
        memory_cards=False,
        card_embroidery=False,
        card_p_muster=False,
        high_speed=False,
        pmemory_variant=PMEMORY_VARIANT_1475CD,
    ),
)

MODEL_NAMES = tuple(model.name for model in MODELS)
DEFAULT_MODEL_NAME = MODELS[0].name


def find_model(name):
    """Return the model called *name*, or ``None`` when it is not registered."""
    for model in MODELS:
        if model.name == name:
            return model
    return None


def find_model_by_ident(text):
    """Return the first model whose ident marker appears in *text*, or ``None``."""
    for model in MODELS:
        for marker in model.ident_markers:
            if marker in text:
                return model
    return None
