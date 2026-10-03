#!/usr/bin/env python3
"""Offline photo-lock helpers. No model calls, uploads, or image generation.

Python 3.10+, standard library only. Paths in JSON files are relative to the
containing JSON file unless absolute. A hash verifies bytes, not visual truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

VERSION = "3.0.0"
CONTAINERS = {
    "bottle": (
        "透明玻璃瓶",
        "采用宽而通透的瓶身，瓶口、瓶塞和厚实瓶底可辨。地形落在瓶底，可见薄底座前缘或侧缘，"
        "树木和建筑有独立体积与遮挡，与玻璃留可见空隙，远端收束在有限模型内。正面略俯视或轻微侧视，"
        "不镜像照片。实体不能穿过瓶壁、瓶肩或瓶塞。不是把照片贴在瓶内；"
        "避免厚重反光遮挡主景，瓶内也不应像无边界的真实街道。",
    ),
    "suitcase": (
        "打开的行李箱",
        "下半部承载前中景，上半部以立体浅浮雕衔接远景，浮雕厚度或层间阴影可辨。两半都有内容并保持主次，"
        "保留箱沿、铰链、提手和支承。允许少量天空背衬，不允许整盖只贴一张照片。"
        "不让道路无缘无故沿盖子垂直爬升，不因箱体限制改变主景左右关系。",
    ),
    "tank": (
        "玻璃鱼缸或展示缸",
        "横向组织前中远景，薄地形支承在缸底，前缘或侧缘可见，与玻璃留空隙；山体有侧面、顶缘和上方留空，玻璃边角与厚度清楚。"
        "原图是陆景就做干景，不自动灌水、不加鱼、气泡、珊瑚；"
        "只有原照片本来存在的水体才保留在对应地形范围。",
    ),
}
CHECKS = {
    "source_match": "场景类型、主景和主要动线对应原图",
    "anchors_and_relations": "关键景物及左右、相邻、遮挡关系对应",
    "people": "原图人物保留并卡通化，无人物则不新增",
    "container_integrity": "支承与边界成立，无穿壁、漂浮、错误积水",
    "three_dimensionality": "存在立体地形与体块，不是照片贴纸",
    "color_and_materials": "色彩鲜活通透、白灰中性、材质分明",
    "no_extras": "无新增无依据物件、旅游装饰和额外文案",
}


class InputError(ValueError):
    """Invalid source, scene record, or review record."""


class PendingReview(InputError):
    """A review is intentionally incomplete, not a passed visual inspection."""


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError(f"无法读取 JSON：{path}；{exc}") from exc
    if not isinstance(value, dict):
        raise InputError(f"JSON 顶层必须为对象：{path}")
    return value


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InputError(f"{label} 必须是对象。")
    return value


def text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{label} 必须填写非空文字。")
    result = value.strip()
    if result.casefold() in {"todo", "tbd", "待填写", "待识别", "填写", "placeholder"}:
        raise InputError(f"{label} 仍是占位内容。")
    return result


def strings(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise InputError(f"{label} 必须是{'可为空的' if allow_empty else '非空的'}文字数组。")
    return [text(item, f"{label}[{i}]") for i, item in enumerate(value)]


def relative_path(path: Path, base: Path) -> str:
    try:
        return Path(os.path.relpath(path.resolve(), base.resolve())).as_posix()
    except ValueError:  # Windows paths on different drives.
        return str(path.resolve())


def locate(raw: Any, base: Path, label: str) -> Path:
    p = Path(text(raw, label)).expanduser()
    return p.resolve() if p.is_absolute() else (base / p).resolve()


def fingerprint(path: Path) -> str:
    """Check a common file signature and hash bytes; this is not full decoding."""
    if not path.is_file():
        raise InputError(f"没有可读取的图像文件：{path}")
    with path.open("rb") as handle:
        head = handle.read(16)
        supported = (
            head.startswith(b"\x89PNG\r\n\x1a\n")
            or head.startswith(b"\xff\xd8\xff")
            or (head.startswith(b"RIFF") and head[8:12] == b"WEBP")
        )
        if not supported:
            raise InputError("仅接受带常见 JPEG、PNG 或 WebP 文件头的图像；不能用文字文件冒充照片。")
        digest = hashlib.sha256()
        digest.update(head)
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def hash_text(value: Any, label: str) -> str:
    v = text(value, label).lower()
    if re.fullmatch(r"[0-9a-f]{64}", v) is None:
        raise InputError(f"{label} 不是有效的 SHA-256。")
    return v


def scene_template() -> dict[str, Any]:
    """A new image never inherits the example scene or a completed inspection."""
    return {
        "schema_version": "1.0",
        "source": {"path": "", "sha256": ""},
        "observation": {
            "inspected": False,
            "scene_type": "",
            "summary": "",
            "light_weather": "",
            "palette": [],
            "uncertainties": [],
        },
        "anchors": [],
        "relations": [],
        "people": {"status": "uncertain", "count": None, "descriptions": []},
        "do_not_add": [],
        "render": {"container": "bottle", "color": "bright", "aspect_ratio": "3:4"},
    }


def validate_scene(scene: dict[str, Any], scene_file: Path,
                   image_override: Path | None = None) -> Path:
    if scene.get("schema_version") != "1.0":
        raise InputError("schema_version 必须是 1.0。")
    src = require_object(scene.get("source"), "source")
    image = image_override.resolve() if image_override else locate(src.get("path"), scene_file.parent, "source.path")
    expected = hash_text(src.get("sha256"), "source.sha256")
    if fingerprint(image) != expected:
        raise InputError("原图 SHA-256 不一致。新照片必须重新识图，不能仅替换路径复用旧记录。")
    obs = require_object(scene.get("observation"), "observation")
    if obs.get("inspected") is not True:
        raise InputError("尚未确认实际查看原图；先识图并填写记录，不允许直接生成。")
    for key in ("scene_type", "summary", "light_weather"):
        text(obs.get(key), f"observation.{key}")
    strings(obs.get("palette"), "observation.palette")
    strings(obs.get("uncertainties"), "observation.uncertainties", allow_empty=True)
    anchors = scene.get("anchors")
    if not isinstance(anchors, list) or not anchors:
        raise InputError("至少记录一个真实识别锚点；不要为凑数虚构物件。")
    ids: set[str] = set()
    required_count = 0
    for i, item in enumerate(anchors):
        item = require_object(item, f"anchors[{i}]")
        aid = text(item.get("id"), f"anchors[{i}].id")
        if re.fullmatch(r"[a-z][a-z0-9_-]*", aid) is None or aid in ids:
            raise InputError("锚点 id 必须是唯一的小写英文标识，可含数字、下划线和连字符。")
        ids.add(aid)
        for key in ("label", "position", "appearance"):
            text(item.get(key), f"anchors[{i}].{key}")
        if type(item.get("required")) is not bool:
            raise InputError("每个锚点都需明确 required: true 或 false。")
        required_count += int(item["required"])
    if not required_count:
        raise InputError("至少一个锚点必须标记为必保。")
    relations = scene.get("relations")
    if not isinstance(relations, list) or (len(ids) > 1 and not relations):
        raise InputError("多锚点场景至少记录一条空间关系。")
    for i, item in enumerate(relations):
        item = require_object(item, f"relations[{i}]")
        a, b = item.get("a"), item.get("b")
        if not isinstance(a, str) or not isinstance(b, str) or a not in ids or b not in ids or a == b:
            raise InputError("空间关系需引用两个不同的有效锚点 id。")
        text(item.get("relation"), f"relations[{i}].relation")
    people = require_object(scene.get("people"), "people")
    status = people.get("status")
    if status not in ("none_visible", "present", "uncertain"):
        raise InputError("people.status 只能为 none_visible、present 或 uncertain。")
    count = people.get("count")
    descriptions = strings(people.get("descriptions"), "people.descriptions", allow_empty=True)
    if status == "none_visible" and (type(count) is not int or count != 0 or descriptions):
        raise InputError("未见清晰人物时 count 应为 0、descriptions 应为空，不新增人设。")
    if status == "present":
        if count is not None and (type(count) is not int or count < 1):
            raise InputError("有人物时 count 为正整数；无法数清时填写 null。")
        if not descriptions:
            raise InputError("有人物时需记录位置、动作、服饰等可见特征。")
    if status == "uncertain" and (count is not None or not descriptions):
        raise InputError("人物无法确认时 count 为 null，并记录不确定依据，不编造人数。")
    strings(scene.get("do_not_add"), "do_not_add")
    render = require_object(scene.get("render"), "render")
    if not isinstance(render.get("container"), str) or render["container"] not in CONTAINERS:
        raise InputError("container 只能为 bottle、suitcase 或 tank。")
    if render.get("color") != "bright":
        raise InputError("本版默认采用 bright 亮彩规则；其他视觉路线请修改规则后重新记录。")
    ratio = text(render.get("aspect_ratio"), "render.aspect_ratio")
    if re.fullmatch(r"[1-9][0-9]?:[1-9][0-9]?", ratio) is None:
        raise InputError("画幅格式应为正整数比例，例如 3:4 或 16:9。")
    return image


def people_instruction(people: dict[str, Any]) -> str:
    if people["status"] == "none_visible":
        return "原照片未见清晰人物。不要新增游客、情侣、儿童、卡通小人或其他人物。"
    if people["status"] == "uncertain":
        return ("人物存在与否无法确认：" + "；".join(people["descriptions"]) +
                "。不把模糊影子擅自扩写为人，不新增人物；不编造精确人数。")
    count = (f"可辨人物为 {people['count']} 人，保留人数" if people["count"] is not None
             else "原图人群无法精确数清，保持可见密度与分布，不编造人数")
    return (count + "。保留位置、动作、朝向、发型轮廓和服饰主色；统一转为简化五官、适度大头、哑光质感的卡通微缩手办，避免过分幼态，"
            "不做写实面部，不识别身份，不把远景小人放大。可见特征：" + "；".join(people["descriptions"]) + "。")


def build_prompt(scene: dict[str, Any], container: str) -> str:
    title, composition = CONTAINERS[container]
    obs = scene["observation"]
    labels = {a["id"]: a["label"] for a in scene["anchors"]}
    anchor_lines = [
        f"- {'必保' if a['required'] else '低权重可简化'}｜{a['label']}：{a['position']}；{a['appearance']}。"
        for a in scene["anchors"]
    ]
    relation_lines = [f"- {labels[r['a']]} → {labels[r['b']]}：{r['relation']}。" for r in scene["relations"]]
    return "\n".join([
        f"# 原照片重构 · {title}", "", "## 必须附加原照片",
        "执行时必须实际附加本次原照片，作为唯一内容参考。只传此文字或路径不够。",
        f"原图 SHA-256：{scene['source']['sha256']}",
        "样图如有附加，仅参考容器、材质和亮彩风格，不复制样图景物。",
        "优先级：原图内容与空间关系 > 关键景物 > 人物规则 > 容器适配 > 视觉美化。", "",
        "## 原图场景锁定", f"场景类型：{obs['scene_type']}。", f"主景：{obs['summary']}",
        f"光线与天气：{obs['light_weather']}", f"原有色彩：{'；'.join(obs['palette'])}。",
        f"不确定信息：{'；'.join(obs['uncertainties']) if obs['uncertainties'] else '未另作不确定推断'}。", "",
        "## 锚点", *anchor_lines, "", "## 空间关系", *(relation_lines or ["单一主景，无需虚构额外相邻关系。"]), "",
        "## 人物", people_instruction(scene["people"]), "", "## 容器与体积", composition,
        "允许整体缩放、适度压缩纵深和最少量背侧补全；保持主要方位和动线。",
        "不是风景照片贴在内壁，不新增不可见的新景区。地形、建筑、植物有体积、支承和接触阴影。", "",
        "## 亮彩与材质",
        "雾中远山保留低对比、软边缘和山脊轮廓，不去雾锐化或拔高山峰；云雾不做悬浮棉团装饰。",
        "依据原图已有色相选择性提纯和提高饱和度，提亮暗部并增强层次；鲜活、通透、明亮而不荧光。",
        "灰色与白色保持中性，高光不过曝。不要擅自改天气时间，不增加落日、光束、花墙或灯串。",
        "玻璃中央清透、边缘有少量反射折射；沥青、植被、建筑和箱体材质各不相同，不统一树脂亮面。",
        "简洁低干扰背景、合理台面接触阴影，适度景深但关键景物可辨，雾中远景保持原有可见程度，不镜像画面。", "",
        "## 禁止增加或改变", *[f"- {x}。" for x in scene["do_not_add"]],
        "不添加额外文字、标语、地名牌、贴纸、标签或水印。原有文字不清楚时不编造内容。", "",
        f"输出一张 {scene['render']['aspect_ratio']} 的完整容器微缩景观。内容与结构先于漂亮。", "",
    ])


def review_template(scene: dict[str, Any] | None = None) -> dict[str, Any]:
    anchors = scene.get("anchors", []) if scene else []
    return {
        "schema_version": "1.0",
        "source_sha256": scene["source"]["sha256"] if scene else "",
        "result_image": {"path": "", "sha256": ""},
        "reviewer": {"kind": "human", "viewed_source_and_result": False, "note": ""},
        "checks": {key: {"status": "unchecked", "evidence": ""} for key in CHECKS},
        "anchor_checks": {a["id"]: {"status": "unchecked", "evidence": ""} for a in anchors if a["required"]},
    }


def evaluate_review(scene: dict[str, Any], review: dict[str, Any], review_file: Path) -> str:
    if review.get("schema_version") != "1.0":
        raise InputError("验收 schema_version 必须是 1.0。")
    if hash_text(review.get("source_sha256"), "review.source_sha256") != scene["source"]["sha256"].lower():
        raise InputError("验收记录绑定的原图与场景锁定记录不一致。")
    result = require_object(review.get("result_image"), "result_image")
    if not result.get("path") or not result.get("sha256"):
        raise PendingReview("尚未绑定实际成图，不能记录验收通过。")
    result_path = locate(result.get("path"), review_file.parent, "result_image.path")
    if fingerprint(result_path) != hash_text(result.get("sha256"), "result_image.sha256"):
        raise InputError("成图 SHA-256 不一致；请重新查看实际要交付的成图。")
    reviewer = require_object(review.get("reviewer"), "reviewer")
    if reviewer.get("kind") not in ("human", "vision_model"):
        raise InputError("reviewer.kind 应为 human 或 vision_model。")
    if reviewer.get("viewed_source_and_result") is not True:
        raise PendingReview("尚未确认实际对照查看原图与成图。")
    text(reviewer.get("note"), "reviewer.note")
    checks = require_object(review.get("checks"), "checks")
    if set(checks) != set(CHECKS):
        raise InputError("验收必须包含且仅包含约定的七项检查。")
    anchor_checks = require_object(review.get("anchor_checks"), "anchor_checks")
    required_ids = {a["id"] for a in scene["anchors"] if a["required"]}
    all_ids = {a["id"] for a in scene["anchors"]}
    if not required_ids.issubset(anchor_checks) or not set(anchor_checks).issubset(all_ids):
        raise InputError("必保锚点未全部验收，或验收记录引用了不存在的锚点。")
    states: list[str] = []
    for key, value in list(checks.items()) + list(anchor_checks.items()):
        value = require_object(value, f"review.{key}")
        status = value.get("status")
        if status not in ("pass", "fail", "unchecked"):
            raise InputError(f"{key} 的状态无效。")
        if status != "unchecked":
            text(value.get("evidence"), f"{key}.evidence")
        states.append(status)
    if "fail" in states:
        return "fail"
    return "unchecked" if "unchecked" in states else "pass"


def json_string(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def write_files(files: dict[Path, str], *, force: bool = False) -> None:
    """Refuse accidental overwrites; replace each generated text file atomically."""
    for path in files:
        if path.exists() and (not force or not path.is_file()):
            raise InputError(f"目标已存在：{path}；需要覆盖生成文件时明确传 --force。")
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                             dir=path.parent, delete=False) as tmp:
                temp_path = Path(tmp.name)
                tmp.write(content)
            os.replace(temp_path, path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()


def build_bundle(scene: dict[str, Any], scene_file: Path, image: Path,
                 container: str, output: Path, *, force: bool = False) -> list[Path]:
    # Self-contained handoff metadata, not a service-specific API payload.
    job = {
        "skill": "travel-diorama", "version": VERSION,
        "status": "awaiting_image_tool", "container": container,
        "source_image_required": True,
        "source_image": {"path": relative_path(image, output), "sha256": scene["source"]["sha256"], "role": "content_reference"},
        "scene_record": relative_path(scene_file, output),
        "style_reference_images": [], "prompt_file": "prompt.md",
        "must_attach_actual_source_image": True,
        "note": "交接清单不是 API 请求。接收方需实际附加原照片；辅助代码不会生图或上传。",
    }
    review_guide = ("# 验收记录填写\n\n实际查看原图和成图后填写 review.json。\n\n" +
                    "\n".join(f"- {key}：{label}" for key, label in CHECKS.items()) +
                    "\n\n每个必保锚点也需填写状态与可观察依据；不确定用 unchecked。"
                    "路径相对 review.json，成图 SHA-256 可用 fingerprint 子命令获得。"
                    "程序只核验记录，不自动进行视觉验收。\n")
    targets = {
        output / "prompt.md": build_prompt(scene, container),
        output / "job.json": json_string(job),
        output / "review.json": json_string(review_template(scene)),
        output / "review-guide.md": review_guide,
    }
    protected = {image.resolve(), scene_file.resolve()}
    if any(p.resolve() in protected for p in targets):
        raise InputError("输出目标与原图或场景记录重名，已阻止覆盖输入。")
    write_files(targets, force=force)
    return list(targets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="照片锁定微缩景观：离线记录、提示词与验收辅助，不调用模型。")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("fingerprint", help="检查常见图像文件头并输出 SHA-256")
    p.add_argument("--image", type=Path, required=True)
    p = sub.add_parser("init", help="为新照片建立未识图的空记录")
    p.add_argument("--image", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--force", action="store_true")
    for name in ("validate", "build", "check-review"):
        p = sub.add_parser(name)
        p.add_argument("--scene", type=Path, required=True)
        p.add_argument("--image", type=Path, help="照片迁移后的路径；字节哈希必须不变")
        if name == "build":
            p.add_argument("--container", choices=tuple(CONTAINERS))
            p.add_argument("--out", type=Path, required=True)
            p.add_argument("--force", action="store_true")
        if name == "check-review":
            p.add_argument("--review", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "fingerprint":
            print(json_string({"path": str(args.image.resolve()), "sha256": fingerprint(args.image)}), end="")
            return 0
        if args.command == "init":
            if args.out.resolve() == args.image.resolve():
                raise InputError("识图记录输出不能覆盖原照片。")
            scene = scene_template()
            scene["source"] = {"path": relative_path(args.image, args.out.parent), "sha256": fingerprint(args.image)}
            write_files({args.out: json_string(scene)}, force=args.force)
            print(f"已建立未识图记录：{args.out}。请实际看图并填写内容；没有生成图片。")
            return 0
        scene = read_json(args.scene)
        image = validate_scene(scene, args.scene, args.image)
        if args.command == "validate":
            print("原图绑定与记录格式通过；这不是自动识图或视觉正确性证明。")
            return 0
        if args.command == "build":
            container = args.container or scene["render"]["container"]
            paths = build_bundle(scene, args.scene, image, container, args.out, force=args.force)
            print("已创建交接资料（未调用生图工具）：\n" + "\n".join(str(p) for p in paths))
            return 0
        state = evaluate_review(scene, read_json(args.review), args.review)
        if state == "pass":
            print("验收记录为通过；程序已检查绑定与记录完整性，没有独立进行视觉验收。")
            return 0
        print("验收记录存在失败项。" if state == "fail" else "仍有 unchecked 项，尚未验收完成。")
        return 1 if state == "fail" else 2
    except (InputError, OSError, UnicodeError) as exc:
        print(f"未完成：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
