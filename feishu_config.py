import time
import requests
from datetime import datetime


class FeishuConfigManager:
    """飞书配置管理器 - 用飞书多维表格存储应用配置"""

    BASE_URL = "https://open.feishu.cn/open-apis"

    # 配置表字段定义
    CONFIG_FIELDS = [
        {"field_name": "配置项", "type": 1},  # 文本字段
        {"field_name": "配置值", "type": 1},  # 文本字段
        {"field_name": "更新时间", "type": 5},  # 日期时间字段
        {"field_name": "备注", "type": 1},  # 文本字段
    ]

    # 默认配置项
    DEFAULT_CONFIGS = {
        "auth_enabled": {"value": "false", "note": "是否启用登录认证"},
        "auth_password": {"value": "", "note": "登录密码"},
        "feishu_app_id": {"value": "", "note": "飞书应用 App ID"},
        "feishu_app_secret": {"value": "", "note": "飞书应用 App Secret"},
        "feishu_app_token": {"value": "", "note": "飞书多维表格 App Token"},
        "feishu_table_id": {"value": "", "note": "飞书数据表 Table ID"},
        "request_interval": {"value": "0.5", "note": "请求间隔（秒）"},
        "max_per_school": {"value": "0", "note": "每校最大条数（0=不限）"},
    }

    def __init__(self, app_id, app_secret, app_token, config_table_id):
        """
        初始化配置管理器

        Args:
            app_id: 飞书应用 ID
            app_secret: 飞书应用密钥
            app_token: 飞书多维表格 App Token
            config_table_id: 配置表 Table ID（不是数据表）
        """
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_token = app_token
        self.config_table_id = config_table_id
        self._token = None
        self._token_expires = 0
        self._config_cache = {}
        self._cache_expires = 0

    def _get_token(self):
        """获取飞书访问令牌"""
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
        """获取请求头"""
        return {"Authorization": f"Bearer {self._get_token()}", "Content-Type": "application/json"}

    def ensure_config_table(self):
        """确保配置表存在并初始化默认配置"""
        # 确保字段存在
        self._ensure_fields()

        # 读取现有配置
        existing_configs = self._read_all_configs()

        # 添加缺失的默认配置
        for key, default in self.DEFAULT_CONFIGS.items():
            if key not in existing_configs:
                self._write_config(key, default["value"], default["note"])

    def _ensure_fields(self):
        """确保配置表字段存在"""
        resp = requests.get(
            f"{self.BASE_URL}/bitable/v1/apps/{self.app_token}/tables/{self.config_table_id}/fields",
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取字段失败：{data}")

        existing_fields = {f["field_name"] for f in data["data"]["items"]}

        for field_def in self.CONFIG_FIELDS:
            if field_def["field_name"] not in existing_fields:
                resp = requests.post(
                    f"{self.BASE_URL}/bitable/v1/apps/{self.app_token}/tables/{self.config_table_id}/fields",
                    headers=self._headers(),
                    json=field_def,
                    timeout=10,
                )
                resp.raise_for_status()
                result = resp.json()
                if result.get("code") != 0:
                    raise RuntimeError(f"创建字段失败：{result}")

    def _read_all_configs(self):
        """读取所有配置项"""
        resp = requests.get(
            f"{self.BASE_URL}/bitable/v1/apps/{self.app_token}/tables/{self.config_table_id}/records",
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"读取配置失败：{data}")

        configs = {}
        for record in data["data"]["items"]:
            fields = record["fields"]
            key = fields.get("配置项", "")
            value = fields.get("配置值", "")
            if key:
                configs[key] = value

        return configs

    def _write_config(self, key, value, note=""):
        """写入单个配置项"""
        now_ms = int(datetime.now().timestamp() * 1000)

        # 先检查是否已存在
        existing = self._read_all_configs()

        if key in existing:
            # 更新现有记录
            record_id = self._get_record_id(key)
            if record_id:
                resp = requests.put(
                    f"{self.BASE_URL}/bitable/v1/apps/{self.app_token}/tables/{self.config_table_id}/records/{record_id}",
                    headers=self._headers(),
                    json={
                        "fields": {
                            "配置项": key,
                            "配置值": str(value),
                            "更新时间": now_ms,
                            "备注": note,
                        }
                    },
                    timeout=10,
                )
        else:
            # 创建新记录
            resp = requests.post(
                f"{self.BASE_URL}/bitable/v1/apps/{self.app_token}/tables/{self.config_table_id}/records",
                headers=self._headers(),
                json={
                    "fields": {
                        "配置项": key,
                        "配置值": str(value),
                        "更新时间": now_ms,
                        "备注": note,
                    }
                },
                timeout=10,
            )

        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 0:
            raise RuntimeError(f"写入配置失败：{result}")

        # 更新缓存
        self._config_cache[key] = str(value)
        self._cache_expires = time.time() + 300  # 5 分钟缓存

        return result

    def _get_record_id(self, key):
        """获取配置项的记录 ID"""
        resp = requests.get(
            f"{self.BASE_URL}/bitable/v1/apps/{self.app_token}/tables/{self.config_table_id}/records",
            headers=self._headers(),
            params={"filter": f'CurrentValue.[配置项] = "{key}"'},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            return None
        items = data["data"]["items"]
        if items:
            return items[0]["record_id"]
        return None

    def get_config(self, key, default=None):
        """获取配置值"""
        # 检查缓存
        if time.time() < self._cache_expires and key in self._config_cache:
            return self._config_cache[key]

        # 从飞书读取
        configs = self._read_all_configs()
        value = configs.get(key, default)

        # 更新缓存
        self._config_cache[key] = value
        self._cache_expires = time.time() + 300

        return value

    def set_config(self, key, value, note=""):
        """设置配置值"""
        if key in self.DEFAULT_CONFIGS:
            note = self.DEFAULT_CONFIGS[key]["note"]
        return self._write_config(key, value, note)

    def get_all_configs(self):
        """获取所有配置"""
        return self._read_all_configs()

    def load_app_config(self):
        """加载应用配置（飞书凭证等）"""
        return {
            "feishu": {
                "app_id": self.get_config("feishu_app_id", ""),
                "app_secret": self.get_config("feishu_app_secret", ""),
                "app_token": self.get_config("feishu_app_token", ""),
                "table_id": self.get_config("feishu_table_id", ""),
            },
            "auth": {
                "enabled": self.get_config("auth_enabled", "false").lower() == "true",
                "password": self.get_config("auth_password", ""),
            },
            "crawl": {
                "request_interval": float(self.get_config("request_interval", "0.5")),
                "max_per_school": int(self.get_config("max_per_school", "0")),
            },
        }

    def save_app_config(self, config):
        """保存应用配置"""
        # 保存飞书配置
        feishu = config.get("feishu", {})
        self.set_config("feishu_app_id", feishu.get("app_id", ""))
        self.set_config("feishu_app_secret", feishu.get("app_secret", ""))
        self.set_config("feishu_app_token", feishu.get("app_token", ""))
        self.set_config("feishu_table_id", feishu.get("table_id", ""))

        # 保存认证配置
        auth = config.get("auth", {})
        self.set_config("auth_enabled", str(auth.get("enabled", False)).lower())
        self.set_config("auth_password", auth.get("password", ""))

        # 保存采集配置
        crawl = config.get("crawl", {})
        self.set_config("request_interval", str(crawl.get("request_interval", 0.5)))
        self.set_config("max_per_school", str(crawl.get("max_per_school", 0)))

    def clear_cache(self):
        """清除配置缓存"""
        self._config_cache = {}
        self._cache_expires = 0
