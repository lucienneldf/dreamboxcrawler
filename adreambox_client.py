import time
import requests


class AdreamboxClient:
    BASE_URL = "https://www.adreambox.cn"
    HEADERS = {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }

    def __init__(self, request_interval=0.5):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self.request_interval = request_interval
        self._last_request_time = 0

    def _throttle(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < self.request_interval:
            time.sleep(self.request_interval - elapsed)
        self._last_request_time = time.time()

    def get_all_schools(self):
        schools = []
        start = 0
        while True:
            self._throttle()
            resp = self.session.get(
                f"{self.BASE_URL}/api/v2/adreambox-dreamcenter/getSchoolInfo",
                params={"province": "全国", "start": start},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("schoolInfo", [])
            if not batch:
                break
            for s in batch:
                schools.append({
                    "sid": s["id"],
                    "name": s["name"],
                    "uid": s.get("user_id"),
                })
            start += len(batch)
            if start >= data.get("total", 0):
                break
        return schools

    def get_school_feeds(self, sid, feed_type="feed", max_items=0):
        offset = 0
        limit = 10
        all_html_parts = []
        while True:
            self._throttle()
            resp = self.session.get(
                f"{self.BASE_URL}/dreamcenter/getSchoolFeeds",
                params={
                    "type": feed_type,
                    "sid": sid,
                    "offset": offset,
                    "limit": limit,
                    "loadcount": offset // limit + 1,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("status"):
                break
            html = data.get("data", "")
            if not html or "noDataDiv" in html:
                break
            count = data.get("count", 0)
            if count == 0:
                break
            all_html_parts.append(html)
            offset += count
            if max_items > 0 and offset >= max_items:
                break
        return "\n".join(all_html_parts)

    def get_school_name(self, sid: int) -> str:
        """通过学校列表 API 获取学校名称"""
        start = 0
        while True:
            self._throttle()
            resp = self.session.get(
                f"{self.BASE_URL}/api/v2/adreambox-dreamcenter/getSchoolInfo",
                params={"province": "全国", "start": start},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("schoolInfo", [])
            if not batch:
                break
            for s in batch:
                if s["id"] == sid:
                    return s["name"]
            start += len(batch)
            if start >= data.get("total", 0):
                break
        return f"学校{sid}"

    def get_feed_detail(self, detail_url):
        """获取动态详情页的完整内容"""
        if not detail_url:
            return ""
        
        self._throttle()
        try:
            resp = self.session.get(detail_url, timeout=30)
            resp.raise_for_status()
            
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "lxml")
            
            # 尝试提取详情页的完整文本
            # 梦想盒子详情页的内容容器
            content_el = soup.select_one("div.detail_body") or soup.select_one("div.feed_content") or soup.select_one("div.content") or soup.select_one("div.detail")
            if content_el:
                return content_el.get_text(strip=True)
            
            # 如果找不到特定容器，尝试提取 body 中的主要文本
            # 但这可能会包含太多无关内容
            return ""
        except Exception as e:
            print(f"[详情获取失败] {detail_url}: {e}")
            return ""
