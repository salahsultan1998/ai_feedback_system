"""Validate three-level classifications and format their two-column display."""


def validate_labels(labels):
    if not isinstance(labels, dict) or not labels:
        raise ValueError("游戏分类配置无效。")

    def check_nodes(nodes, allow_children):
        if not isinstance(nodes, list):
            raise ValueError("分类 themes/children 必须为列表。")
        names = []
        for node in nodes:
            if not isinstance(node, dict) or not isinstance(node.get("name"), str) or not node["name"].strip():
                raise ValueError("分类名称必须为非空字符串。")
            if node["name"] != node["name"].strip():
                raise ValueError("分类名称不可包含首尾空格。")
            names.append(node["name"])
            if "children" in node:
                if not allow_children:
                    raise ValueError("分类最多支持三层。")
                check_nodes(node["children"], False)
        if len(names) != len(set(names)):
            raise ValueError("同一层级下不可有重复分类。")

    for label, group in labels.items():
        if not isinstance(label, str) or not label.strip() or label != label.strip() or not isinstance(group, dict):
            raise ValueError("I 类别配置无效。")
        check_nodes(group.get("themes"), True)


def validate_path(label, theme, subtheme, schema):
    labels = schema["labels"]
    if label not in labels or label == "辱骂":
        raise ValueError("I 类别不属于当前分类表。")
    if not theme:
        if subtheme:
            raise ValueError("III 类别必须有对应的 II 类别。")
        return
    node = next((item for item in labels[label]["themes"] if item["name"] == theme), None)
    if node is None:
        raise ValueError("II 类别不属于所选 I 类别。")
    if subtheme and subtheme not in [item["name"] for item in node.get("children", [])]:
        raise ValueError("III 类别不属于所选 II 类别。")


def display_theme(theme, subtheme):
    return f"{theme}（{subtheme}）" if subtheme else theme
