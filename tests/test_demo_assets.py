from pathlib import Path

from waterbag_inspection.demo_assets import seed_demo_images


def test_seed_demo_images_generates_expected_cases(tmp_path):
    generated = seed_demo_images(str(tmp_path), clean=True)

    assert len(generated) == 8
    assert (tmp_path / "camera1" / "bag_0002_cam1_defect_primary.jpg").exists()
    assert (tmp_path / "camera2" / "bag_0004_cam2_micro_patch.jpg").exists()
    assert all(Path(path).exists() for path in generated)
