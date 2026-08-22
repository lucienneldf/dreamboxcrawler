def tag_feed(feed):
    """对动态进行分类打标"""
    has_text = len(feed.get("text_content", "").strip()) > 0
    has_images = feed.get("image_count", 0) > 0
    text_length = len(feed.get("text_content", "").strip())

    # 判断素材类型
    if has_text and has_images:
        material_type = "图文混合"
        value_level = "高价值"
    elif has_images and not has_text:
        material_type = "纯图片"
        value_level = "普通"
    elif has_text and not has_images:
        material_type = "纯文字"
        value_level = "普通"
    else:
        material_type = "纯文字"
        value_level = "普通"

    # 判断字数档位（仅对图文混合类型有效）
    word_tier = ""
    if material_type == "图文混合":
        if text_length < 30:
            word_tier = "短"
        elif text_length < 50:  # 30-49字
            word_tier = "中短"
        elif text_length < 100:  # 50-99字
            word_tier = "中长"
        else:  # >= 100字
            word_tier = "长"

    feed["material_type"] = material_type
    feed["value_level"] = value_level
    feed["word_tier"] = word_tier
    return feed


def get_word_tier(text_content):
    """根据文本内容返回字数档位"""
    text_length = len(text_content.strip()) if text_content else 0
    if text_length < 30:
        return "短"
    elif text_length < 50:
        return "中短"
    elif text_length < 100:
        return "中长"
    else:
        return "长"
