"""架构边界守卫：分层方向、核心层 Qt 依赖范围、循环依赖。"""
import ast
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"

# 核心层里允许依赖 Qt 的适配文件（worker/日志/持久化/下载器），新增文件默认不允许。
CORE_QT_ALLOWED = {
    "applog.py",
    "downloader.py",
    "manga.py",
    "video_library.py",
    "workers.py",
}

# 页面模块之间不允许互相 import；共享件走 design / workspace / perf / 对话框与播放器。
PAGE_MODULES = {
    "home_interface.py", "batch_interface.py", "task_interface.py", "manga_interface.py",
    "video_interface.py", "converter_interface.py", "gif_interface.py",
    "frames_interface.py", "setting_interface.py",
}


def module_name(path):
    rel = path.relative_to(APP.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def resolve_import(path, node):
    """把一条 import 解析成 app.* 模块名；外部依赖返回 None。"""
    current = module_name(path)
    if node.level:
        base = current.split(".")
        if path.name != "__init__.py":
            base = base[:-1]
        base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
        if node.module:
            base = base + node.module.split(".")
        return ".".join(base)
    if node.module and node.module.split(".")[0] == "app":
        return node.module
    return None


def imports_of(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            target = resolve_import(path, node)
            if target and target.startswith("app"):
                found.add(target)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app"):
                    found.add(alias.name)
    return found


def app_modules():
    return {module_name(p): p for p in sorted(APP.rglob("*.py"))}


class LayeringTest(unittest.TestCase):
    def test_core_never_imports_view(self):
        offenders = []
        for path in sorted((APP / "core").rglob("*.py")):
            for target in imports_of(path):
                if target.startswith("app.view"):
                    offenders.append(f"{path.name} -> {target}")
        self.assertEqual(offenders, [], "核心层不得依赖界面层")

    def test_pages_do_not_import_each_other(self):
        offenders = []
        for name in PAGE_MODULES:
            path = APP / "view" / name
            if not path.exists():
                continue
            for target in imports_of(path):
                if target.startswith("app.view.") and target.split(".")[-1] + ".py" in PAGE_MODULES:
                    offenders.append(f"{name} -> {target}")
        self.assertEqual(offenders, [], "页面之间不得互相 import，共享件下沉到通用模块")

    def test_core_qt_dependency_stays_in_allowlist(self):
        offenders = []
        for path in sorted((APP / "core").rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if "PySide6" in source and path.name not in CORE_QT_ALLOWED:
                offenders.append(path.name)
        self.assertEqual(offenders, [], "核心层新增 Qt 依赖需先把该文件移入适配层并更新白名单")

    def test_no_import_cycles(self):
        graph = {name: {t for t in imports_of(path) if t in app_modules()} for name, path in app_modules().items()}
        state = {}
        stack = []

        def visit(node):
            state[node] = "visiting"
            stack.append(node)
            for target in graph.get(node, ()):
                if state.get(target) == "visiting":
                    cycle = stack[stack.index(target):] + [target]
                    self.fail("存在循环依赖：" + " -> ".join(cycle))
                if target not in state:
                    visit(target)
            stack.pop()
            state[node] = "done"

        for name in graph:
            if name not in state:
                visit(name)

    def test_only_http_headers_defines_default_media_headers(self):
        offenders = []
        for path in sorted(APP.rglob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.startswith("HEADERS = {") or line.startswith("def headers_for("):
                    if path.name != "http_headers.py":
                        offenders.append(f"{path.name}:{number}")
        self.assertEqual(offenders, [], "默认请求头只允许在 core/http_headers.py 定义")


if __name__ == "__main__":
    unittest.main()
