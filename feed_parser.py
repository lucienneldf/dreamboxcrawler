import re
from bs4 import BeautifulSoup
from datetime import datetime


def normalize_pub_time(time_str: str) -> str:
    """
    将发布时间标准化为带年份的格式。
    输入可能是：
    - "05-26 09:37" （今年，无年份）
    - "2022-11-30 11:53" （带年份）
    - "昨天 14:20"
    - "3 天前"
    输出统一为："YYYY-MM-DD HH:MM" 格式
    """
    if not time_str:
        return ""

    now = datetime.now()
    current_year = now.year

    # 情况 1：已经是完整格式 "2022-11-30 11:53"
    if re.match(r'^\d{4}-\d{2}-\d{2}', time_str):
        return time_str

    # 情况 2："MM-DD HH:MM" 格式（今年）
    match = re.match(r'^(\d{2})-(\d{2})\s+(\d{2}:\d{2})$', time_str)
    if match:
        month, day, time = match.groups()
        return f"{current_year}-{month}-{day} {time}"

    # 情况 3："MM-DD" 格式（今年，无时间）
    match = re.match(r'^(\d{2})-(\d{2})$', time_str)
    if match:
        month, day = match.groups()
        return f"{current_year}-{month}-{day} 00:00"

    # 情况 4："昨天 HH:MM"
    match = re.match(r'^昨天\s+(\d{2}:\d{2})$', time_str)
    if match:
        from datetime import timedelta
        yesterday = now - timedelta(days=1)
        return f"{yesterday.strftime('%Y-%m-%d')} {match.group(1)}"

    # 情况 5："X 天前"
    match = re.match(r'^(\d+) 天前$', time_str)
    if match:
        from datetime import timedelta
        days_ago = int(match.group(1))
        target_date = now - timedelta(days=days_ago)
        return target_date.strftime('%Y-%m-%d 00:00')

    # 其他情况，原样返回
    return time_str


def parse_feeds_html(html_text, school_sid, school_name):
    soup = BeautifulSoup(html_text, "lxml")
    feed_items = soup.select("div.feed_item")
    results = []
    for item in feed_items:
        feed_id_match = re.search(r"id=\"feed_(\d+)\"", str(item))
        if not feed_id_match:
            continue
        feed_id = feed_id_match.group(1)

        author_el = item.select_one("span.feed_uname")
        author = author_el.get_text(strip=True) if author_el else ""

        time_el = item.select_one("span.feed_time")
        pub_time_raw = time_el.get_text(strip=True) if time_el else ""
        pub_time = normalize_pub_time(pub_time_raw)

        text_el = item.select_one("a.feed_text")
        text_content = text_el.get_text(strip=True) if text_el else ""

        detail_link = ""
        date_link = item.select_one("a.date")
        if date_link and date_link.get("href"):
            detail_link = date_link["href"]
            if not detail_link.startswith("http"):
                detail_link = f"https://www.adreambox.cn{detail_link}"

        images = []
        for img in item.select("img.lazy[data-original]"):
            img_url = img.get("data-original", "")
            if img_url:
                img_url = img_url.replace("&amp;", "&")
                if not img_url.startswith("http"):
                    img_url = f"https://www.adreambox.cn{img_url}"
                # 去掉尺寸参数（w= 和 h=），获取原图
                if "?" in img_url:
                    base_url, params = img_url.split("?", 1)
                    # 保留 token 参数，去掉 w 和 h 参数
                    kept_params = []
                    for param in params.split("&"):
                        if param and not param.startswith("w=") and not param.startswith("h="):
                            kept_params.append(param)
                    img_url = base_url + ("?" + "&".join(kept_params) if kept_params else "")
                images.append(img_url)

        results.append({
            "feed_id": feed_id,
            "school_sid": str(school_sid),
            "school_name": school_name,
            "author": author,
            "pub_time": pub_time,
            "text_content": text_content,
            "images": images,
            "image_count": len(images),
            "detail_url": detail_link,
        })
    return results
