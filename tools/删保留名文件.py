r"""删掉目录里的 Windows 保留名文件（这次是根目录那个 0 字节的 `nul`）。

它是怎么来的：命令里写 `2> nul`（Linux 的习惯）在 Windows 上不会重定向到空设备，
而是在当前目录建出一个**真文件**，名字是保留名 nul。留着它的代价不是难看——
带这个条目的文件夹在资源管理器里删不掉，`git clean` 和打包脚本都会在它上面报错。

删它只能走 Win32 的扩展路径前缀 `\\?\`：普通路径解析会把 "nul" 当设备名，
DeleteFile 直接拒（WinError 5 access denied）；加上前缀后按字面名字解析，才删得动。
顺一个坑：`os.path.abspath()` 会把含 "nul" 的路径解析成设备路径 `\\.\nul`，
所以这里一律用 os.path.join 拼，绝不 normalize。
"""
import os

反斜杠 = chr(92)
前缀 = 反斜杠 * 2 + "?" + 反斜杠
保留名 = {"nul", "con", "prn", "aux", "com1", "com2", "com3", "lpt1", "lpt2"}


def 找(root: str) -> list:
    """列出目录里名字撞 Windows 保留名的文件（只查一层，不递归）。"""
    中 = []
    for 项 in os.scandir(root):
        主名 = 项.name.lower().split(".")[0]
        if 主名 not in 保留名:
            continue
        try:
            if 项.is_file():
                中.append(os.path.join(root, 项.name))
        except OSError:
            中.append(os.path.join(root, 项.name))   # stat 都读不动的，正是这一族
    return 中


def 删(root: str) -> list:
    删掉的 = []
    for 路径 in 找(root):
        try:
            os.remove(前缀 + 路径)
            删掉的.append(路径)
        except OSError as e:
            print("删不动 %s：%s: %s" % (路径, type(e).__name__, e))
    return 删掉的


if __name__ == "__main__":
    根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print("根目录里的保留名文件：%s" % ([os.path.basename(x) for x in 找(根)] or "没有"))
    for 路径 in 删(根):
        print("已删：%s" % 路径)
    print("剩下的：%s" % ([os.path.basename(x) for x in 找(根)] or "没有了"))
