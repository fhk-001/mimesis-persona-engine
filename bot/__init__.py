"""微信性格克隆聊天机器人。

模块划分：
    chatlog    聊天记录解析（微信导出 txt / csv / json -> 统一记录）
    style      本地风格统计画像（不需要大模型）
    persona    性格画像（本地统计 + 大模型分析）与提示词渲染
    retriever  真实语料检索（BM25，给模型当"语气参考"）
    llm        大模型调用（任何 OpenAI 兼容接口，只用标准库）
    memory     会话记忆（SQLite）
    engine     回复编排：人设 + 记忆 + 语料检索 -> 分条回复 + 打字延迟
    adapters   接入层：终端 / 个人微信 / 企业微信
"""

__version__ = "0.1.0"
