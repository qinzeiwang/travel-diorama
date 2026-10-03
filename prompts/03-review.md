# 03 · 原图对照验收

同时打开本次原照片和实际生成图；再读取对应 scene-lock.json。不要只看提示词就验收。

逐项填写 review.json：

- 七项检查：source_match、anchors_and_relations、people、container_integrity、three_dimensionality、color_and_materials、no_extras。
- 每个必保锚点单独检查，记录是否对应及一条可观察依据。
- 状态只能为 pass、fail、unchecked；看不到或无法判断时用 unchecked，不默认 pass。
- 填写生成图实际文件路径及 SHA-256、原图 SHA-256、看图者类型和简短备注。仅在真实查看两张图后将 viewed_source_and_result 置为 true。

没有同场景、同动线和核心关系，图片再漂亮也失败。人物、穿壁和贴图问题优先于调色问题。

失败时给出针对性的修正，不泛泛写“再精致一点”。修正生成仍附加原照片。没有真正检查实际结果时，不给出通过结论。
