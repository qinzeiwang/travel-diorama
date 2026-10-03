# 记录格式

路径相对于所在 JSON 文件；绝对路径也可使用。原图移动后可通过 --image 指定同字节文件的新位置。不要将宿主的临时文件 ID 当作本地路径。

## 场景记录

source.path 和 source.sha256 由 init 创建。observation.inspected 默认 false；实际看图后填写 scene_type、summary、light_weather、palette、uncertainties，再置为 true。空数组不等于已完成识图。

每个 anchors 项的结构：

```json
{
  "id": "main_path",
  "label": "根据原照片填写",
  "position": "相对原照片画面的方位",
  "appearance": "可见轮廓、颜色与特征",
  "required": true
}
```

每个 relations 项含 a、relation、b；a 和 b 必须是不同的现有锚点 id。多个锚点至少记录一条空间关系；只有一个主景的极简照片可以没有关系项。

people.status 可为 none_visible、present、uncertain。none_visible 配 count: 0 和空 descriptions；present 配正整数人数或 null，以及可见位置、动作和服饰描述；uncertain 配 count: null，并描述为何无法确认。

render.container 为 bottle、suitcase、tank 之一；render.color 固定为 bright；aspect_ratio 例如 3:4。CLI 的 --container 可以仅切换容器，不改写源记录。

## 验收记录

build 会按必保锚点生成 review.json。result_image.path 相对 review.json，sha256 由 fingerprint 子命令获取。reviewer.kind 为 human 或 vision_model。真实对照看过原图与成图后才能设置 viewed_source_and_result: true。

checks 含七项规则，anchor_checks 按锚点 id 填写。每项含 status 和 evidence；状态为 pass、fail、unchecked，通过和失败都要有一句可观察依据。程序不能验证“实际看过图”的陈述真伪，所以它只核验记录，不声称独立完成视觉验收。
