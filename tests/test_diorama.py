"""Code tests use tiny synthetic fixtures; they are NOT visual acceptance tests."""
from __future__ import annotations
import base64
from copy import deepcopy
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("diorama", ROOT / "scripts/diorama.py")
assert spec and spec.loader
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jXZkAAAAASUVORK5CYII=")

class DioramaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.image = self.base / "photo.png"
        self.image.write_bytes(PNG)
        self.scene_file = self.base / "scene.json"
        self.scene = json.loads((ROOT / "tests/fixtures/scene.json").read_text(encoding="utf-8"))
        self.scene["source"] = {"path": "photo.png", "sha256": d.fingerprint(self.image)}
        self.save_scene()

    def save_scene(self):
        self.scene_file.write_text(d.json_string(self.scene), encoding="utf-8")

    def validate(self):
        return d.validate_scene(self.scene, self.scene_file)

    def full_review(self):
        # Mock record completion, not an actual visual conclusion.
        result = self.base / "result.png"
        result.write_bytes(PNG + b"result fixture")
        review = d.review_template(self.scene)
        review["result_image"] = {"path": "result.png", "sha256": d.fingerprint(result)}
        review["reviewer"] = {"kind": "human", "viewed_source_and_result": True, "note": "模拟的单元测试记录，不代表视觉判断。"}
        for group in ("checks", "anchor_checks"):
            for item in review[group].values():
                item.update(status="pass", evidence="模拟填写的测试依据，不代表真实图像验收。")
        return review

    def run_cli(self, args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return d.main(args)

    def test_valid_scene(self):
        self.assertEqual(self.validate(), self.image.resolve())

    def test_missing_photo_rejected(self):
        self.image.unlink()
        with self.assertRaises(d.InputError): self.validate()

    def test_text_file_disguised_as_image_rejected(self):
        self.image.write_text("not a photo")
        with self.assertRaises(d.InputError): d.fingerprint(self.image)

    def test_source_change_rejected(self):
        self.image.write_bytes(PNG + b"another source")
        with self.assertRaisesRegex(d.InputError, "SHA-256"): self.validate()

    def test_uninspected_record_rejected(self):
        self.scene["observation"]["inspected"] = False
        with self.assertRaisesRegex(d.InputError, "先识图"): self.validate()

    def test_numeric_true_rejected(self):
        self.scene["observation"]["inspected"] = 1
        with self.assertRaises(d.InputError): self.validate()

    def test_blank_summary_rejected(self):
        self.scene["observation"]["summary"] = " "
        with self.assertRaises(d.InputError): self.validate()

    def test_placeholder_scene_rejected(self):
        self.scene["observation"]["scene_type"] = "TODO"
        with self.assertRaises(d.InputError): self.validate()

    def test_no_anchors_rejected(self):
        self.scene["anchors"] = []
        with self.assertRaises(d.InputError): self.validate()

    def test_duplicate_anchor_rejected(self):
        self.scene["anchors"].append(deepcopy(self.scene["anchors"][0]))
        with self.assertRaises(d.InputError): self.validate()

    def test_required_anchor_needed(self):
        for anchor in self.scene["anchors"]: anchor["required"] = False
        with self.assertRaises(d.InputError): self.validate()

    def test_missing_relations_rejected(self):
        self.scene["relations"] = []
        with self.assertRaises(d.InputError): self.validate()

    def test_unknown_relation_anchor_rejected(self):
        self.scene["relations"][0]["b"] = "ocean"
        with self.assertRaises(d.InputError): self.validate()

    def test_single_anchor_scene_supported(self):
        self.scene["anchors"] = self.scene["anchors"][:1]
        self.scene["relations"] = []
        self.validate()

    def test_absent_people_cannot_have_count(self):
        self.scene["people"]["count"] = 2
        with self.assertRaises(d.InputError): self.validate()

    def test_absent_people_cannot_have_invented_descriptions(self):
        self.scene["people"]["descriptions"] = ["新增游客"]
        with self.assertRaises(d.InputError): self.validate()

    def test_present_people_keeps_cartoon_instruction(self):
        self.scene["people"] = {"status":"present", "count":2, "descriptions":["左前方两人，分别穿蓝色和白色上衣。"]}
        self.validate()
        prompt = d.build_prompt(self.scene, "bottle")
        self.assertIn("2 人", prompt)
        self.assertIn("卡通微缩", prompt)

    def test_uncountable_people_supported(self):
        self.scene["people"] = {"status":"present", "count":None, "descriptions":["远处人群密集，人数无法数清。"]}
        self.validate()
        self.assertIn("不编造人数", d.people_instruction(self.scene["people"]))

    def test_uncertain_people_requires_reason(self):
        self.scene["people"] = {"status":"uncertain", "count":None, "descriptions":[]}
        with self.assertRaises(d.InputError): self.validate()

    def test_uncertain_people_can_be_recorded(self):
        self.scene["people"] = {"status":"uncertain", "count":None, "descriptions":["远处影子无法确认。"]}
        self.validate()

    def test_bad_container_type_is_handled(self):
        self.scene["render"]["container"] = []
        with self.assertRaises(d.InputError): self.validate()

    def test_bad_ratio_rejected(self):
        self.scene["render"]["aspect_ratio"] = "0:4"
        with self.assertRaises(d.InputError): self.validate()

    def test_prompt_requires_source_image(self):
        prompt = d.build_prompt(self.scene, "bottle")
        self.assertIn("实际附加本次原照片", prompt)
        self.assertIn("不镜像照片", prompt)
        self.assertIn("不要新增游客", prompt)

    def test_tank_not_automatically_underwater(self):
        self.assertIn("不自动灌水", d.build_prompt(self.scene, "tank"))

    def test_container_switch_preserves_original_record(self):
        before = deepcopy(self.scene)
        d.build_prompt(self.scene, "suitcase")
        self.assertEqual(before, self.scene)

    def test_new_template_does_not_inherit_scene(self):
        template = d.scene_template()
        self.assertFalse(template["observation"]["inspected"])
        self.assertEqual(template["anchors"], [])
        self.assertEqual(template["observation"]["scene_type"], "")

    def test_relocation_requires_identical_bytes(self):
        relocated = self.base / "moved.png"
        relocated.write_bytes(PNG)
        self.image.unlink()
        self.assertEqual(d.validate_scene(self.scene, self.scene_file, relocated), relocated)
        relocated.write_bytes(PNG + b"changed")
        with self.assertRaises(d.InputError): d.validate_scene(self.scene, self.scene_file, relocated)

    def test_bundle_is_not_image_generation(self):
        output = self.base / "out"
        paths = d.build_bundle(self.scene, self.scene_file, self.image, "bottle", output)
        self.assertEqual(len(paths), 4)
        self.assertFalse(any(p.suffix in (".png", ".jpg") for p in paths))
        job = d.read_json(output / "job.json")
        self.assertEqual(job["status"], "awaiting_image_tool")
        self.assertTrue(job["source_image_required"])
        self.assertEqual((output / job["source_image"]["path"]).resolve(), self.image.resolve())
        self.assertFalse(d.read_json(output / "review.json")["reviewer"]["viewed_source_and_result"])

    def test_output_not_silently_overwritten(self):
        output = self.base / "out"
        d.build_bundle(self.scene, self.scene_file, self.image, "bottle", output)
        with self.assertRaises(d.InputError):
            d.build_bundle(self.scene, self.scene_file, self.image, "tank", output)
        d.build_bundle(self.scene, self.scene_file, self.image, "tank", output, force=True)
        self.assertEqual(d.read_json(output / "job.json")["container"], "tank")

    def test_bundle_does_not_overwrite_scene_input(self):
        protected = self.base / "prompt.md"
        protected.write_text("input", encoding="utf-8")
        with self.assertRaises(d.InputError):
            d.build_bundle(self.scene, protected, self.image, "bottle", self.base, force=True)
        self.assertEqual(protected.read_text(), "input")

    def test_init_cannot_overwrite_source(self):
        code = self.run_cli(["init", "--image", str(self.image), "--out", str(self.image), "--force"])
        self.assertEqual(code, 2)
        self.assertEqual(self.image.read_bytes(), PNG)

    def test_init_and_validate_cli(self):
        path = self.base / "fresh.json"
        self.assertEqual(self.run_cli(["init","--image",str(self.image),"--out",str(path)]), 0)
        self.assertEqual(self.run_cli(["validate","--scene",str(path)]), 2)

    def test_empty_review_cannot_pass(self):
        with self.assertRaises(d.PendingReview):
            d.evaluate_review(self.scene, d.review_template(self.scene), self.base / "review.json")

    def test_not_viewed_review_cannot_pass(self):
        review = self.full_review()
        review["reviewer"]["viewed_source_and_result"] = False
        with self.assertRaises(d.PendingReview): d.evaluate_review(self.scene, review, self.base / "review.json")

    def test_review_requires_all_gates(self):
        review = self.full_review()
        del review["checks"]["people"]
        with self.assertRaises(d.InputError): d.evaluate_review(self.scene, review, self.base / "review.json")

    def test_review_requires_anchor_checks(self):
        review = self.full_review()
        del review["anchor_checks"]["road"]
        with self.assertRaises(d.InputError): d.evaluate_review(self.scene, review, self.base / "review.json")

    def test_review_pass_requires_evidence(self):
        review = self.full_review()
        review["checks"]["source_match"]["evidence"] = ""
        with self.assertRaises(d.InputError): d.evaluate_review(self.scene, review, self.base / "review.json")

    def test_modified_result_invalidates_review(self):
        review = self.full_review()
        (self.base / "result.png").write_bytes(PNG + b"different result")
        with self.assertRaises(d.InputError): d.evaluate_review(self.scene, review, self.base / "review.json")

    def test_wrong_source_review_rejected(self):
        review = self.full_review()
        review["source_sha256"] = "0" * 64
        with self.assertRaises(d.InputError): d.evaluate_review(self.scene, review, self.base / "review.json")

    def test_complete_mock_record_can_be_pass(self):
        self.assertEqual(d.evaluate_review(self.scene, self.full_review(), self.base / "review.json"), "pass")

    def test_failed_mock_record_is_fail(self):
        review = self.full_review()
        review["checks"]["source_match"]["status"] = "fail"
        self.assertEqual(d.evaluate_review(self.scene, review, self.base / "review.json"), "fail")

    def test_pending_mock_record_is_unchecked(self):
        review = self.full_review()
        review["checks"]["source_match"]["status"] = "unchecked"
        self.assertEqual(d.evaluate_review(self.scene, review, self.base / "review.json"), "unchecked")

    def test_invalid_json_reported(self):
        self.scene_file.write_text("{broken")
        self.assertEqual(self.run_cli(["validate","--scene",str(self.scene_file)]), 2)

    def test_file_mapping_artifacts_real_source(self):
        path = d.validate_scene(self.scene, self.scene_file)
        self.assertEqual(path.name, "photo.png")

if __name__ == "__main__":
    unittest.main()
