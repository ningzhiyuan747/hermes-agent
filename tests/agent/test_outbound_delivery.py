from agent.outbound_delivery import DeliveryTarget


def test_delivery_target_formats_threaded_target():
    target = DeliveryTarget(platform="feishu", chat_id="oc_chat_1", thread_id="omt-2")
    assert target.is_valid() is True
    assert target.to_target_ref() == "feishu:oc_chat_1:omt-2"


def test_delivery_target_from_origin_normalizes_platform():
    target = DeliveryTarget.from_origin({"platform": "WeiXin", "chat_id": "filehelper"})
    assert target.to_target_ref() == "weixin:filehelper"
