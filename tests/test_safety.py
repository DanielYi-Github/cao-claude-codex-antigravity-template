import pytest

from lyria_auto.errors import SafetyBlockedError
from lyria_auto.safety import PromptSafety


def test_blocks_named_reference():
    safety = PromptSafety(["in the style of", "nujabes"])
    with pytest.raises(SafetyBlockedError):
        safety.require_safe("Make music in the style of Nujabes")


def test_allows_descriptive_music():
    safety = PromptSafety(["in the style of", "nujabes"])
    safety.require_safe("Warm instrumental jazz with brushed drums and upright bass")
