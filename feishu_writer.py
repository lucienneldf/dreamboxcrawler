import time
import requests
from datetime import datetime


class FeishuWriter:
    BASE_URL = "https://open.feishu.cn/open-apis"

    FIELD_DEFINITIONS = [
        {"field_name": "动态 ID", "type": 1},
        {"field_name": "来源学校", "type": 1},
        {"field_name": "学校 SID", "type": 1},
        {"field_name": "发布者", "type": 1},
        {"field_name": "发布时间", "type": 1},
        {"field_name": "正文内容", "type": 1},
        {"field_name": "图片链接", "type": 1},
        {"field_name": "图片数量", "type": 2},
        {"field_name": "素材类型", "type": 3, "property": {
            "options": [
                {"name": "图文混合"},
                {"name": "纯文字"},
                {"name": "纯图片"},
            ]
        }},
        {"field_name": "图文字数档位", "type": 3, "property": {
            "options": [
                {"name": "短"},
                {"name": "中短"},
                {"name": "中长"},
                {"name": "长"},
            ]
        }},
        {"field_name": "价值标记", "type": 3, "property": {
            "options": [
                {"name": "高价值"},
                {"name": "普通"},
            ]
        }},
        {"field_name": "原始链接", "type": 15},
        {"field_name": "采集时间", "type": 5},
    ]

    def __init__(self, app_id, app_secret, app_token, table_id):
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_token = app_token
        self.table_id = table_id
        self._token = None
        self._token_expires = 0

    def _get_token(self):
        if self._token and time.time() < self._token_expires:
            return self._token
        resp = requests.post(
            f"{self.BASE_URL}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取飞书 token 失败：{data}")
        self._token = data["tenant_access_token"]
        self._token_expires = time.time() + data.get("expire", 7200) - 60
        return self._token

    def _headers(self):
        return {"Authorization": f"Bearer {self._get_token()}", "Content-Type": "application/json"}

    def _get_existing_fields(self):
        resp = requests.get(
            f"{self.BASE_URL}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/fields",
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取字段失败：{data}")
        return {f["field_name"] for f in data["data"]["items"]}

    def ensure_fields(self):
        existing = self._get_existing_fields()
        created = []
        for field_def in self.FIELD_DEFINITIONS:
            if field_def["field_name"] in existing:
                continue
            resp = requests.post(
                f"{self.BASE_URL}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/fields",
                headers=self._headers(),
                json=field_def,
                timeout=10,
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("code") == 0:
                created.append(field_def["field_name"])
        return created

    def _build_record(self, feed):
        now_ms = int(datetime.now().timestamp() * 1000)
        image_links = "\n".join(feed.get("images", []))

        pub_time_ms = now_ms
        if feed.get("pub_time"):
            try:
                pub_time_ms = int(datetime.strptime(feed["pub_time"], "%Y-%m-%d %H:%M").timestamp() * 1000)
            except (ValueError, TypeError):
                pub_time_ms = now_ms

        fields = {
            "文本": feed.get("text_content", "") or f"动态#{feed['feed_id']}",
            "动态 ID": str(feed["feed_id"]),
            "来源学校": feed.get("school_name", ""),
            "学校 SID": str(feed.get("school_sid", "")),
            "发布者": feed.get("author", ""),
            "发布时间": pub_time_ms,
            "正文内容": feed.get("text_content", ""),
            "图片链接": image_links,
            "图片数量": feed.get("image_count", 0),
            "素材类型": feed.get("material_type", "纯文字"),
            "图文字数档位": feed.get("word_tier", ""),
            "价值标记": feed.get("value_level", "普通"),
            "原始链接": {"link": feed.get("detail_url", ""), "text": "查看原文"},
            "采集时间": now_ms,
        }
        return {"fields": fields}

    def batch_write(self, feeds, batch_size=100):
        total_written = 0
        for i in range(0, len(feeds), batch_size):
            batch = feeds[i:i + batch_size]
            records = [self._build_record(f) for f in batch]
            resp = requests.post(
                f"{self.BASE_URL}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records/batch_create",
                headers=self._headers(),
                json={"records": records},
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("code") != 0:
                raise RuntimeError(f"批量写入失败：{result}")
            total_written += len(records)
        return total_written

    def write_feed(self, feed):
        """写入单条动态记录"""
        record = self._build_record(feed)
        resp = requests.post(
            f"{self.BASE_URL}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records",
            headers=self._headers(),
            json={"fields": record["fields"]},
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 0:
            raise RuntimeError(f"写入记录失败：{result}")
        return result

    def read_records(self, page_size=100, page_token=None):
        """读取表格记录（支持分页）"""
        params = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(
            f"{self.BASE_URL}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records",
            headers=self._headers(),
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"读取记录失败：{data}")
        return data.get("data") or {}

    def read_all_records(self):
        """读取表格所有记录"""
        all_records = []
        page_token = None
        while True:
            data = self.read_records(page_size=500, page_token=page_token)
            if data is None:
                break
            all_records.extend(data.get("items") or [])
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
        return all_records

    def delete_records(self, record_ids):
        """批量删除记录"""
        resp = requests.delete(
            f"{self.BASE_URL}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records/batch",
            headers=self._headers(),
            json={"records": record_ids},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"删除记录失败：{data}")
        return data.get("data", {})

    def delete_bitable_app(self, app_token):
        """删除多维表格应用"""
        resp = requests.delete(
            f"{self.BASE_URL}/bitable/v1/apps/{app_token}",
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"删除表格失败：{data}")
        return True

    def get_member_by_email(self, app_token, email):
        """通过邮箱查找成员 ID"""
        resp = requests.get(
            f"{self.BASE_URL}/contact/v3/users/batch_get_id",
            headers=self._headers(),
            params={"emails": email},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"查找成员失败：{data}")
        user_list = data.get("data", {}).get("user_list", [])
        if user_list:
            return user_list[0].get("user_id")
        return None

    def sync_records_to_table(self, source_app_token, source_table_id, dest_app_token, dest_table_id):
        """将源表格的记录同步到目标表格"""
        # 读取源表格所有记录
        source_writer = FeishuWriter(self.app_id, self.app_secret, source_app_token, source_table_id)
        records = source_writer.read_all_records()

        # 写入目标表格
        dest_writer = FeishuWriter(self.app_id, self.app_secret, dest_app_token, dest_table_id)
        dest_writer.ensure_fields()

        feeds = []
        for record in records:
            fields = record.get("fields", {})
            image_count = fields.get("图片数量", 0)
            if isinstance(image_count, str):
                try:
                    image_count = int(image_count)
                except (ValueError, TypeError):
                    image_count = 0
            feed = {
                "feed_id": fields.get("动态 ID", ""),
                "school_name": fields.get("来源学校", ""),
                "school_sid": fields.get("学校 SID", ""),
                "author": fields.get("发布者", ""),
                "pub_time": fields.get("发布时间", ""),
                "text_content": fields.get("正文内容", ""),
                "images": fields.get("图片链接", "").split("\n") if fields.get("图片链接") else [],
                "image_count": image_count,
                "material_type": fields.get("素材类型", "纯文字"),
                "value_level": fields.get("价值标记", "普通"),
                "detail_url": fields.get("原始链接", {}).get("link", "") if isinstance(fields.get("原始链接"), dict) else "",
            }
            feeds.append(feed)

        if feeds:
            dest_writer.batch_write(feeds)
        return len(feeds)

    def create_bitable_app(self, name="梦想盒子素材采集"):
        """创建新的多维表格应用"""
        resp = requests.post(
            f"{self.BASE_URL}/bitable/v1/apps",
            headers=self._headers(),
            json={"name": name},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"创建多维表格失败：{data}")
        app_token = data["data"]["app"]["app_token"]
        return app_token

    def create_table(self, app_token, table_name="素材数据"):
        """在多维表格中创建新的数据表"""
        resp = requests.post(
            f"{self.BASE_URL}/bitable/v1/apps/{app_token}/tables",
            headers=self._headers(),
            json={"table": {"name": table_name}},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"创建数据表失败：{data}")
        table_id = data["data"]["table_id"]
        return table_id

    def create_complete_bitable(self, name="梦想盒子素材采集"):
        """创建完整的多维表格（应用 + 数据表 + 字段）"""
        # 1. 创建应用
        app_token = self.create_bitable_app(name)
        
        # 2. 获取默认数据表 ID（创建应用时会自动创建一个默认表）
        resp = requests.get(
            f"{self.BASE_URL}/bitable/v1/apps/{app_token}/tables",
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取数据表列表失败：{data}")
        
        # 使用第一个默认表
        table_id = data["data"]["items"][0]["table_id"]
        
        # 3. 创建字段
        self.app_token = app_token
        self.table_id = table_id
        self.ensure_fields()
        
        return {
            "app_token": app_token,
            "table_id": table_id,
            "url": f"https://your-org.feishu.cn/base/{app_token}?table={table_id}"
        }

    def set_share_permission(self, app_token):
        """
        设置表格分享权限为互联网可访问
        
        Args:
            app_token: 表格 App Token
        
        Returns:
            share_url: 分享链接
        """
        # 设置分享权限
        resp = requests.put(
            f"{self.BASE_URL}/bitable/v1/apps/{app_token}/share",
            headers=self._headers(),
            json={
                "share_setting": {
                    "link_share_setting": {
                        "access_entity_type": "anyone",
                        "access_level": "read"
                    }
                }
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"设置分享权限失败：{data.get('msg', data)}")
        
        # 获取分享链接
        share_url = data.get("data", {}).get("share_url", "")
        return share_url

    def add_member(self, token, member_type, member_id, permission="full_access"):
        """
        添加成员到多维表格
        
        Args:
            token: app_token 或 table_id
            member_type: 成员类型 (email, userid, openid, unionid, departmentid, chatid)
            member_id: 成员 ID（邮箱、用户 ID 等）
            permission: 权限级别 (view, edit, full_access)
        
        Returns:
            member_id: 添加的成员 ID
        """
        # 权限映射
        permission_map = {
            "view": "view",
            "edit": "edit",
            "full_access": "full_access",
            "owner": "full_access",
            "read": "view",
            "comment": "edit",
        }
        
        payload = {
            "member_type": member_type,
            "member_id": member_id,
            "permission": permission_map.get(permission, "full_access"),
        }
        
        resp = requests.post(
            f"{self.BASE_URL}/drive/v1/permissions/{token}/members",
            headers=self._headers(),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            return False, f"添加成员失败：{data.get('msg', data)}"
        
        member_id = data["data"]["member"]["member_id"]
        return True, f"成功添加协作者（{member_type}: {member_id}）"
    
    def get_user_id_by_phone(self, phone):
        """
        通过手机号获取用户 ID
        
        Args:
            phone: 手机号
        
        Returns:
            user_id: 用户 ID
        """
        # 确保 token 已获取
        token = self._get_token()
        
        # 添加国家代码前缀（如果没有）
        if not phone.startswith("+"):
            phone = f"+86{phone}"
        
        # 使用飞书通讯录 API 通过手机号查找用户
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        resp = requests.post(
            f"{self.BASE_URL}/contact/v3/users/batch_get_id",
            headers=headers,
            params={"user_id_type": "open_id"},
            json={"mobiles": [phone]},
            timeout=30,
        )
        
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("code") != 0:
            error_msg = data.get('msg', '未知错误')
            # 如果是权限问题，给出更友好的提示
            if 'permission' in error_msg.lower() or 'scope' in error_msg.lower():
                raise RuntimeError(f"查找用户失败：应用缺少通讯录权限。请在飞书开放平台为应用添加 contact:user.base:readonly 权限并重新发布版本。")
            raise RuntimeError(f"查找用户失败：{error_msg}")
        
        # 返回匹配的用户 ID
        user_list = data.get("data", {}).get("user_list", [])
        if user_list:
            return user_list[0].get("user_id")
        raise RuntimeError(f"未找到手机号为 {phone} 的用户。请确认手机号是否正确，或该用户是否在飞书组织架构中。")

    def transfer_ownership(self, token, member_id):
        """
        转让所有权给指定成员
        
        Args:
            token: app_token 或 table_id
            member_id: 成员 ID
        
        Returns:
            bool: 是否成功
        """
        # 先获取成员列表，找到成员的 member_id
        resp = requests.get(
            f"{self.BASE_URL}/drive/v1/permissions/{token}/members",
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取成员列表失败：{data}")
        
        # 找到目标成员
        target_member = None
        for member in data["data"]["members"]:
            if member["member_id"] == member_id:
                target_member = member
                break
        
        if not target_member:
            raise RuntimeError(f"成员 {member_id} 不存在")
        
        # 转让所有权
        payload = {
            "permission": "owner",
        }
        
        resp = requests.put(
            f"{self.BASE_URL}/drive/v1/permissions/{token}/members/{member_id}",
            headers=self._headers(),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"转让所有权失败：{data}")
        
        return True
