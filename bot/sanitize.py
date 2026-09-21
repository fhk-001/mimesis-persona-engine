"""输入文本清洗。

Windows 控制台和管道的编码有时和 Python 的解码方式对不上，字符串里会混进
`\\udcXX` 这种"代理字符"。这类字符不能存进 SQLite、也不能发给大模型
（两者都会直接抛 UnicodeEncodeError 让程序崩掉），所以在入口处统一清掉。
正常汉字、emoji 都不受影响。
"""


def clean(text):
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""
    try:
        return text.encode("utf-8", "ignore").decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001 - 兜底：逐个字符过滤代理区
        return "".join(
            char for char in text if not (0xD800 <= ord(char) <= 0xDFFF)
        )
