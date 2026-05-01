from pathlib import Path

import cv2
import numpy as np

from waterbag_inspection.artifacts import ArtifactWriter


def test_artifact_writer_flushes_async_copy_and_image(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("trace artifact", encoding="utf-8")
    copy_target = tmp_path / "out" / "source.txt"
    image_target = tmp_path / "out" / "result.jpg"
    image = np.full((32, 48, 3), 220, dtype=np.uint8)

    writer = ArtifactWriter(enabled=True, max_queue_size=8)
    writer.copy_file(source, copy_target)
    writer.write_image(image_target, image)

    assert writer.flush(timeout=2.0) is True
    assert copy_target.read_text(encoding="utf-8") == "trace artifact"
    assert Path(image_target).exists()
    assert cv2.imread(str(image_target)) is not None
    assert writer.close(timeout=2.0) is True
