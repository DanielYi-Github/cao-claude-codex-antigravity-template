from lyria_auto.metadata import build_metadata
from lyria_auto.models import PromptPlan


def test_scheduled_metadata_is_private():
    settings = {
        "metadata": {
            "language": "zh-TW",
            "title_template": "{scene} Coffee Jazz",
            "description_template": "{scene} {instrumentation} {mood} {duration_minutes}",
            "tags": ["jazz"],
        }
    }
    channel = {
        "privacy_status": "public",
        "category_id": "10",
        "publish": {"mode": "scheduled", "delay_hours": 1, "spacing_hours": 2},
    }
    plan = PromptPlan("x", "sig", "Rainy Cafe", "calm", "piano")
    md = build_metadata(settings, channel, plan, 60)
    assert md.privacy_status == "private"
    assert md.publish_at and md.publish_at.endswith("Z")
