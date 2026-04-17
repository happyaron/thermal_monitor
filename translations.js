// Central translation file — edit strings here.
// Consumed by:
//   • Browser  → loaded via <script src="translations.js"> before thermal_monitor.js
//   • Python   → thermal_monitor/alerts.py parses the JSON section with a regex
//
// Parameterised strings use {placeholder} syntax.
// The web "status" map translates OK/WARN/CRIT/ERROR badge labels.
window.TRANSLATIONS = {
  "en": {
    "web": {
      "connecting":  "connecting...",
      "connected":   "connected",
      "fetchFailed": "fetch failed: {msg}",
      "dataStale":   "data stale ({min} min ago)",
      "paused":      "(paused)",
      "nextIn":      "next in {s}s",
      "chipOk":      "OK",
      "chipWarn":    "WARN",
      "chipCrit":    "CRIT",
      "chipErr":     "ERR",
      "chipSources": "sources",
      "chipSensors": "sensors",
      "hosts":       "{n} hosts",
      "locale":      null,
      "status":      { "OK": "OK", "WARN": "WARN", "CRIT": "CRIT", "ERROR": "ERROR" }
    },
    "alerts": {
      "header":     "## {icon} Thermal Alert — {ts}",
      "subtitle":   "Equipment room temperature warning",
      "critLabel":  "**🔥 CRITICAL:**",
      "warnLabel":  "**⚠️ WARNING:**",
      "critSuffix": "(crit: {crit}°C)",
      "warnSuffix": "(warn: {warn}°C)",
      "escalation": "🔥 Critical thermal alert in equipment room! Please check immediately."
    }
  },
  "zh": {
    "web": {
      "connecting":  "连接中...",
      "connected":   "已连接",
      "fetchFailed": "获取失败：{msg}",
      "dataStale":   "数据已过期（{min} 分钟前）",
      "paused":      "（已暂停）",
      "nextIn":      "{s} 秒后刷新",
      "chipOk":      "正常",
      "chipWarn":    "警告",
      "chipCrit":    "严重",
      "chipErr":     "错误",
      "chipSources": "数据源",
      "chipSensors": "传感器",
      "hosts":       "{n} 台主机",
      "locale":      "zh-CN",
      "status":      { "OK": "正常", "WARN": "警告", "CRIT": "严重", "ERROR": "错误" }
    },
    "alerts": {
      "header":     "## {icon} 温度告警 — {ts}",
      "subtitle":   "机房温度异常",
      "critLabel":  "**🔥 严重：**",
      "warnLabel":  "**⚠️ 警告：**",
      "critSuffix": "（严重阈值：{crit}°C）",
      "warnSuffix": "（警告阈值：{warn}°C）",
      "escalation": "🔥 机房温度严重告警，请立即检查！"
    }
  },
  "ja": {
    "web": {
      "connecting":  "接続中...",
      "connected":   "接続済み",
      "fetchFailed": "取得失敗：{msg}",
      "dataStale":   "データが古くなっています（{min} 分前）",
      "paused":      "（一時停止）",
      "nextIn":      "{s} 秒後に更新",
      "chipOk":      "正常",
      "chipWarn":    "警告",
      "chipCrit":    "重大",
      "chipErr":     "エラー",
      "chipSources": "ソース",
      "chipSensors": "センサー",
      "hosts":       "{n} 台",
      "locale":      "ja-JP",
      "status":      { "OK": "正常", "WARN": "警告", "CRIT": "重大", "ERROR": "エラー" }
    },
    "alerts": {
      "header":     "## {icon} 温度アラート — {ts}",
      "subtitle":   "機器室の温度異常",
      "critLabel":  "**🔥 重大：**",
      "warnLabel":  "**⚠️ 警告：**",
      "critSuffix": "（重大閾値：{crit}°C）",
      "warnSuffix": "（警告閾値：{warn}°C）",
      "escalation": "🔥 機器室で重大な温度アラートが発生しました！直ちに確認してください！"
    }
  }
};
